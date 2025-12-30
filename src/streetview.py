"""Google Street View image fetching module."""

import os
import logging
from typing import Optional, Tuple
import requests
from dotenv import load_dotenv

from .models import GeocodedProperty, StreetViewImage

load_dotenv()
logger = logging.getLogger(__name__)


class StreetViewFetcher:
    """Handles Street View image retrieval via Google Maps API."""

    METADATA_URL = "https://maps.googleapis.com/maps/api/streetview/metadata"
    IMAGE_URL = "https://maps.googleapis.com/maps/api/streetview"

    def __init__(self, api_key: Optional[str] = None):
        """Initialize Street View fetcher with API key."""
        self.api_key = api_key or os.getenv("GOOGLE_MAPS_API_KEY")
        if not self.api_key:
            raise ValueError("Google Maps API key not found in environment")

    def fetch(
        self,
        property: GeocodedProperty,
        size: str = "640x640",
        fov: int = 90,
        pitch: int = 10,
        multi_angle: bool = False
    ) -> Optional[StreetViewImage]:
        """
        Fetch Street View image(s) for a property.

        Args:
            property: Geocoded property with coordinates
            size: Image size (default: 640x640)
            fov: Field of view in degrees (default: 90)
            pitch: Camera pitch (default: 10)
            multi_angle: If True, fetch 4 images (N, S, E, W). If False, fetch 1 smart image.

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

        image_available, image_date, pano_id = metadata

        if multi_angle:
            # Fetch 4 images from different angles
            image_urls = self._fetch_multi_angle_urls(location, size, fov, pitch)
            # Use first image as primary
            image_url = image_urls[0] if image_urls else ""
            image_data = None  # Don't fetch data for multiple images

            return StreetViewImage(
                image_url=image_url,
                image_urls_multi_angle=image_urls,
                image_data=image_data,
                image_date=image_date,
                pano_id=pano_id,
                image_available=image_available
            )
        else:
            # Fetch single image with smart heading (default to Southeast - 135°)
            image_url = self._construct_image_url(location, size, fov, pitch, heading=135)
            image_data = self._fetch_image_data(image_url)

            return StreetViewImage(
                image_url=image_url,
                image_data=image_data,
                image_date=image_date,
                pano_id=pano_id,
                image_available=image_available
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

    def _check_metadata(self, location: str) -> Optional[Tuple[bool, Optional[str], Optional[str]]]:
        """
        Check if Street View imagery is available for location.

        Returns:
            Tuple of (available, date, pano_id) or None if not available
        """
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
                return (True, image_date, pano_id)
            else:
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
