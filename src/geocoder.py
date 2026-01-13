"""Google Maps geocoding module."""

import os
import logging
from typing import Optional
import requests
from dotenv import load_dotenv

from .models import RawAddress, GeocodedProperty, GeocodeStatus

load_dotenv()
logger = logging.getLogger(__name__)


class Geocoder:
    """Handles address geocoding via Google Maps API."""

    BASE_URL = "https://maps.googleapis.com/maps/api/geocode/json"

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize geocoder with optional API key.

        API key validation is deferred until first use to prevent
        import-time crashes when environment variables aren't set.
        """
        self._api_key = api_key

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

        Args:
            address: Raw address to geocode

        Returns:
            GeocodedProperty if successful, None otherwise
        """
        try:
            params = {
                "address": address.full_address,
                "key": self.api_key
            }

            logger.info(f"Geocoding: {address.full_address}")
            response = requests.get(self.BASE_URL, params=params, timeout=10)
            response.raise_for_status()

            data = response.json()

            if data["status"] == "OK" and data["results"]:
                return self._parse_geocode_response(data["results"][0])
            elif data["status"] == "ZERO_RESULTS":
                logger.warning(f"No geocoding results for: {address.full_address}")
                return None
            elif data["status"] == "OVER_QUERY_LIMIT":
                logger.error("Google Maps API rate limit exceeded")
                raise Exception("Rate limit exceeded")
            else:
                logger.warning(f"Geocoding failed with status: {data['status']}")
                return None

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

        # Get formatted address
        formatted_address = result["formatted_address"]

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
