"""Google Street View image fetching module."""

import os
import logging
from typing import Optional, Tuple, NamedTuple
import requests
from dotenv import load_dotenv

from .models import GeocodedProperty, StreetViewImage
from .geo_utils import calculate_bearing
from .cache import get_cache, Cache

load_dotenv()
logger = logging.getLogger(__name__)

# Sentinel value for "no coverage" cache entries
NO_COVERAGE = "__NO_COVERAGE__"


class PanoMetadata(NamedTuple):
    """Street View panorama metadata."""
    available: bool
    image_date: Optional[str]
    pano_id: Optional[str]
    pano_lat: Optional[float]
    pano_lng: Optional[float]


class StreetViewFetcher:
    """Handles Street View image retrieval via Google Maps API."""

    METADATA_URL = "https://maps.googleapis.com/maps/api/streetview/metadata"
    IMAGE_URL = "https://maps.googleapis.com/maps/api/streetview"

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize Street View fetcher with optional API key.

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

    def fetch(
        self,
        property: GeocodedProperty,
        size: str = "640x640",
        fov: int = 80,
        pitch: int = 5,
        multi_angle: bool = False
    ) -> Optional[StreetViewImage]:
        """
        Fetch Street View image(s) for a property.

        Uses bearing calculation to get front-facing images instead of
        arbitrary heading values.

        Args:
            property: Geocoded property with coordinates
            size: Image size (default: 640x640)
            fov: Field of view in degrees (default: 80 for better framing)
            pitch: Camera pitch (default: 5 to capture roofline)
            multi_angle: If True, fetch 3 front-facing angles. If False, fetch 1 optimal image.

        Returns:
            StreetViewImage if available, None otherwise
        """
        location = f"{property.latitude},{property.longitude}"

        # Check metadata first to see if imagery is available
        metadata = self._check_metadata(location)
        if not metadata:
            logger.warning(f"No Street View imagery available for: {property.address_full}")
            return StreetViewImage(
                image_url="",
                image_available=False
            )

        # Calculate front-facing heading using bearing from camera to property
        if metadata.pano_lat is not None and metadata.pano_lng is not None:
            heading = calculate_bearing(
                from_lat=metadata.pano_lat,
                from_lng=metadata.pano_lng,
                to_lat=property.latitude,
                to_lng=property.longitude
            )
            logger.info(f"Calculated front-facing heading: {heading:.1f}° for {property.address_full}")
        else:
            # Fallback to property-relative heading if pano location unavailable
            heading = 135  # SE as fallback
            logger.warning(f"Pano location unavailable, using fallback heading {heading}°")

        if multi_angle:
            # Fetch 3 front-facing angles instead of cardinal NESW
            image_urls = self._fetch_multi_angle_urls_optimized(
                location, size, fov, pitch, primary_heading=int(heading)
            )
            image_url = image_urls[0] if image_urls else ""
            image_data = None  # Don't fetch data for multiple images

            return StreetViewImage(
                image_url=image_url,
                image_urls_multi_angle=image_urls,
                image_data=image_data,
                image_date=metadata.image_date,
                pano_id=metadata.pano_id,
                image_available=metadata.available
            )
        else:
            # Fetch single image with computed front-facing heading
            image_url = self._construct_image_url(
                location, size, fov, pitch, heading=int(heading)
            )
            image_data = self._fetch_image_data(image_url)

            return StreetViewImage(
                image_url=image_url,
                image_data=image_data,
                image_date=metadata.image_date,
                pano_id=metadata.pano_id,
                image_available=metadata.available
            )

    def _fetch_multi_angle_urls(
        self,
        location: str,
        size: str,
        fov: int,
        pitch: int
    ) -> list[str]:
        """
        Fetch Street View image URLs from 4 cardinal directions.

        DEPRECATED: Use _fetch_multi_angle_urls_optimized instead for
        front-facing images.

        Args:
            location: Lat,lng string
            size: Image size
            fov: Field of view
            pitch: Camera pitch

        Returns:
            List of 4 image URLs (North, East, South, West)
        """
        headings = [0, 90, 180, 270]  # N, E, S, W
        urls = []

        for heading in headings:
            url = self._construct_image_url(location, size, fov, pitch, heading)
            urls.append(url)

        return urls

    def _fetch_multi_angle_urls_optimized(
        self,
        location: str,
        size: str,
        fov: int,
        pitch: int,
        primary_heading: int,
        spread_degrees: int = 25
    ) -> list[str]:
        """
        Fetch Street View image URLs from front-facing angles.

        Instead of cardinal directions (NESW), this generates 3 headings
        around the computed front-facing direction:
        - Primary (direct front)
        - Front-left (primary - 25°)
        - Front-right (primary + 25°)

        Args:
            location: Lat,lng string
            size: Image size
            fov: Field of view
            pitch: Camera pitch
            primary_heading: Computed front-facing heading (0-360)
            spread_degrees: Angular offset for left/right views

        Returns:
            List of 3 image URLs (Front, Front-Left, Front-Right)
        """
        headings = [
            primary_heading,
            (primary_heading - spread_degrees) % 360,
            (primary_heading + spread_degrees) % 360,
        ]
        urls = []

        for heading in headings:
            url = self._construct_image_url(location, size, fov, pitch, int(heading))
            urls.append(url)

        return urls

    def _check_metadata(self, location: str) -> Optional[PanoMetadata]:
        """
        Check if Street View imagery is available for location.

        Results are cached:
        - Positive coverage: 30 days
        - No coverage: 7 days (Street View updates quarterly)

        Returns:
            PanoMetadata with availability, date, pano_id, and camera location.
            Returns None if no imagery available.

        Note:
            The returned pano_lat/pano_lng is the CAMERA position, not the property.
            This is crucial for calculating front-facing heading.
        """
        # Parse location for cache key
        try:
            lat, lng = map(float, location.split(","))
            cache_key = Cache.coverage_key(lat, lng)
        except (ValueError, AttributeError):
            cache_key = None

        # Check cache first
        if cache_key:
            cached = self._cache.get(cache_key)
            if cached == NO_COVERAGE:
                logger.info(f"Coverage cache hit (no coverage): {location}")
                return None
            elif cached is not None:
                logger.info(f"Coverage cache hit: {location}")
                return PanoMetadata(
                    available=True,
                    image_date=cached.get("image_date"),
                    pano_id=cached.get("pano_id"),
                    pano_lat=cached.get("pano_lat"),
                    pano_lng=cached.get("pano_lng")
                )

        # Cache miss - call API
        try:
            params = {
                "location": location,
                "key": self.api_key
            }

            response = requests.get(self.METADATA_URL, params=params, timeout=10)
            response.raise_for_status()

            data = response.json()

            if data["status"] == "OK":
                image_date = data.get("date", None)  # Format: "YYYY-MM"
                pano_id = data.get("pano_id", None)
                # Extract camera location (this is WHERE the panorama was taken)
                pano_location = data.get("location", {})
                pano_lat = pano_location.get("lat")
                pano_lng = pano_location.get("lng")

                # Cache positive result
                if cache_key:
                    self._cache.set(
                        cache_key,
                        {
                            "image_date": image_date,
                            "pano_id": pano_id,
                            "pano_lat": pano_lat,
                            "pano_lng": pano_lng
                        },
                        ttl_seconds=Cache.TTL_COVERAGE
                    )

                return PanoMetadata(
                    available=True,
                    image_date=image_date,
                    pano_id=pano_id,
                    pano_lat=pano_lat,
                    pano_lng=pano_lng
                )
            else:
                logger.info(f"No Street View coverage at {location}: {data['status']}")
                # Cache negative result with shorter TTL
                if cache_key:
                    self._cache.set(
                        cache_key,
                        NO_COVERAGE,
                        ttl_seconds=Cache.TTL_NO_COVERAGE
                    )
                return None

        except Exception as e:
            logger.error(f"Street View metadata check failed: {e}")
            return None

    def _construct_image_url(
        self,
        location: str,
        size: str,
        fov: int,
        pitch: int,
        heading: int = 0
    ) -> str:
        """Construct Street View Static API image URL."""
        return (
            f"{self.IMAGE_URL}?"
            f"location={location}&"
            f"size={size}&"
            f"fov={fov}&"
            f"pitch={pitch}&"
            f"heading={heading}&"
            f"key={self.api_key}"
        )

    def _fetch_image_data(self, image_url: str) -> Optional[bytes]:
        """
        Fetch actual image data from URL.

        Args:
            image_url: Street View image URL

        Returns:
            Image bytes or None if fetch fails
        """
        try:
            response = requests.get(image_url, timeout=15)
            response.raise_for_status()
            return response.content
        except Exception as e:
            logger.error(f"Failed to fetch image data: {e}")
            return None
