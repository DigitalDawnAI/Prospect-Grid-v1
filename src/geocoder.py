"""Google Maps geocoding module."""

import os
import time
import random
import logging
from typing import Optional
import requests
from dotenv import load_dotenv

from .models import RawAddress, GeocodedProperty, GeocodeStatus
from .cache import get_cache, Cache

load_dotenv()
logger = logging.getLogger(__name__)


class RetryableError(Exception):
    """Exception for errors that should trigger a retry."""
    pass


class Geocoder:
    """Handles address geocoding via Google Maps API."""

    BASE_URL = "https://maps.googleapis.com/maps/api/geocode/json"

    # Retry configuration
    MAX_RETRIES = 3
    BACKOFF_BASE = 1.0  # seconds
    BACKOFF_CAP = 16.0  # seconds

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize geocoder with optional API key.

        API key validation is deferred until first use to prevent
        import-time crashes when environment variables aren't set.
        """
        self._api_key = api_key
        self._cache = get_cache()

    @property
    def api_key(self) -> str:
        """Lazy-load API key from environment on first access."""
        if self._api_key is None:
            self._api_key = os.getenv("GOOGLE_MAPS_API_KEY")
        if not self._api_key:
            raise ValueError(
                "Google Maps API key not configured. "
                "Set GOOGLE_MAPS_API_KEY environment variable."
            )
        return self._api_key

    def geocode(self, address: RawAddress) -> Optional[GeocodedProperty]:
        """
        Geocode an address to coordinates and standardized format.

        Results are cached for 30 days to reduce API costs.

        Args:
            address: Raw address to geocode

        Returns:
            GeocodedProperty if successful, None otherwise
        """
        cache_key = Cache.geocode_key(address.full_address)

        # Check cache first
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.info(f"Geocode cache hit for: {address.full_address}")
            return GeocodedProperty(**cached)

        # Cache miss - call API with retry
        result = self._geocode_with_retry(address)

        # Cache successful results
        if result is not None:
            self._cache.set(
                cache_key,
                result.model_dump(),
                ttl_seconds=Cache.TTL_GEOCODE
            )

        return result

    def _geocode_with_retry(self, address: RawAddress) -> Optional[GeocodedProperty]:
        """
        Geocode with exponential backoff retry on transient failures.

        Retries on:
        - OVER_QUERY_LIMIT (rate limiting)
        - Network timeouts
        - 5xx server errors
        """
        last_error = None

        for attempt in range(self.MAX_RETRIES):
            try:
                result = self._geocode_api(address)
                return result  # Success or permanent failure (ZERO_RESULTS, etc.)

            except RetryableError as e:
                last_error = e
                if attempt < self.MAX_RETRIES - 1:
                    # Exponential backoff with jitter
                    sleep_time = min(
                        self.BACKOFF_CAP,
                        self.BACKOFF_BASE * (2 ** attempt)
                    )
                    # Add jitter (±30%)
                    sleep_time *= (0.7 + 0.6 * random.random())
                    logger.warning(
                        f"Geocoding retry {attempt + 1}/{self.MAX_RETRIES} "
                        f"for {address.full_address}, sleeping {sleep_time:.1f}s: {e}"
                    )
                    time.sleep(sleep_time)
                else:
                    logger.error(
                        f"Geocoding failed after {self.MAX_RETRIES} attempts "
                        f"for {address.full_address}: {e}"
                    )

        return None

    def _geocode_api(self, address: RawAddress) -> Optional[GeocodedProperty]:
        """Make actual API call to Google Maps Geocoding API."""
        try:
            params = {
                "address": address.full_address,
                "key": self.api_key
            }

            logger.info(f"Geocoding (API call): {address.full_address}")
            response = requests.get(self.BASE_URL, params=params, timeout=10)

            # Retry on 5xx errors
            if response.status_code >= 500:
                raise RetryableError(f"Server error: {response.status_code}")

            response.raise_for_status()

            data = response.json()

            if data["status"] == "OK" and data["results"]:
                return self._parse_geocode_response(data["results"][0])
            elif data["status"] == "ZERO_RESULTS":
                logger.warning(f"No geocoding results for: {address.full_address}")
                return None
            elif data["status"] == "OVER_QUERY_LIMIT":
                raise RetryableError("Rate limit exceeded (OVER_QUERY_LIMIT)")
            elif data["status"] == "UNKNOWN_ERROR":
                raise RetryableError("Google Maps UNKNOWN_ERROR (transient)")
            else:
                logger.warning(f"Geocoding failed with status: {data['status']}")
                return None

        except requests.exceptions.Timeout as e:
            raise RetryableError(f"Request timeout: {e}")
        except requests.exceptions.ConnectionError as e:
            raise RetryableError(f"Connection error: {e}")
        except RetryableError:
            raise  # Re-raise retryable errors
        except requests.exceptions.RequestException as e:
            logger.error(f"Geocoding request failed: {e}")
            return None
        except Exception as e:
            logger.error(f"Geocoding error: {e}")
            return None

    def _parse_geocode_response(self, result: dict) -> GeocodedProperty:
        """Parse Google Maps geocoding response into GeocodedProperty."""

        # Extract coordinates
        location = result["geometry"]["location"]
        lat = location["lat"]
        lng = location["lng"]

        # Extract address components
        components = {
            comp["types"][0]: comp["long_name"]
            for comp in result["address_components"]
        }

        # Get formatted address (strip country suffix for US addresses)
        formatted_address = result["formatted_address"].removesuffix(", USA")

        # Extract specific fields
        street_number = components.get("street_number", "")
        route = components.get("route", "")
        street = f"{street_number} {route}".strip()

        city = (
            components.get("locality") or
            components.get("sublocality") or
            components.get("administrative_area_level_3") or
            ""
        )

        state = components.get("administrative_area_level_1", "")
        zip_code = components.get("postal_code", "")
        county = components.get("administrative_area_level_2", "")

        # Remove "County" suffix if present
        if county.endswith(" County"):
            county = county[:-7]

        return GeocodedProperty(
            address_full=formatted_address,
            address_street=street,
            city=city,
            state=state,
            zip=zip_code,
            county=county,
            latitude=lat,
            longitude=lng,
            geocode_status=GeocodeStatus.SUCCESS
        )
