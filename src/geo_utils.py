"""Geospatial utility functions for ProspectGrid."""

import math
from typing import Tuple


def calculate_bearing(from_lat: float, from_lng: float,
                      to_lat: float, to_lng: float) -> float:
    """
    Calculate bearing from point A to point B using the forward azimuth formula.

    This is the heading (in degrees, 0-360) that points from the camera location
    toward the property. Use this as the 'heading' parameter in Street View
    Static API to get front-facing property images.

    Args:
        from_lat: Camera/panorama latitude (from metadata API's location.lat)
        from_lng: Camera/panorama longitude (from metadata API's location.lng)
        to_lat: Property latitude (from geocoding)
        to_lng: Property longitude (from geocoding)

    Returns:
        Bearing in degrees (0-360), where:
        - 0° = North
        - 90° = East
        - 180° = South
        - 270° = West

    Example:
        >>> # Camera is south of property → should point north
        >>> calculate_bearing(39.0, -74.0, 40.0, -74.0)
        0.0

        >>> # Camera is west of property → should point east
        >>> calculate_bearing(39.0, -75.0, 39.0, -74.0)
        89.99...
    """
    # Convert to radians
    lat1 = math.radians(from_lat)
    lat2 = math.radians(to_lat)
    delta_lng = math.radians(to_lng - from_lng)

    # Forward azimuth formula
    x = math.sin(delta_lng) * math.cos(lat2)
    y = (math.cos(lat1) * math.sin(lat2) -
         math.sin(lat1) * math.cos(lat2) * math.cos(delta_lng))

    bearing = math.degrees(math.atan2(x, y))

    # Normalize to 0-360
    return (bearing + 360) % 360


def haversine_distance(lat1: float, lng1: float,
                       lat2: float, lng2: float) -> float:
    """
    Calculate the great-circle distance between two points on Earth.

    Args:
        lat1, lng1: First point coordinates (degrees)
        lat2, lng2: Second point coordinates (degrees)

    Returns:
        Distance in meters
    """
    R = 6371000  # Earth's radius in meters

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lng2 - lng1)

    a = (math.sin(delta_phi / 2) ** 2 +
         math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R * c


def generate_candidate_headings(primary_heading: float,
                                 spread_degrees: float = 25.0,
                                 num_candidates: int = 3) -> list[float]:
    """
    Generate candidate headings around a primary heading.

    Useful for premium tier where multiple angles are desired.
    Instead of cardinal directions (N/E/S/W), this generates headings
    that are all roughly front-facing.

    Args:
        primary_heading: The computed front-facing heading (0-360)
        spread_degrees: Angular spread between candidates
        num_candidates: Number of headings to generate (1, 3, or 5 recommended)

    Returns:
        List of headings in degrees (0-360)

    Example:
        >>> generate_candidate_headings(90.0, spread_degrees=25, num_candidates=3)
        [90.0, 65.0, 115.0]  # Front, front-left, front-right
    """
    if num_candidates == 1:
        return [primary_heading]

    candidates = [primary_heading]

    # Add symmetric offsets
    for i in range(1, (num_candidates + 1) // 2 + 1):
        offset = spread_degrees * i
        candidates.append((primary_heading - offset) % 360)
        candidates.append((primary_heading + offset) % 360)
        if len(candidates) >= num_candidates:
            break

    return candidates[:num_candidates]


def round_coords(lat: float, lng: float, decimals: int = 5) -> Tuple[float, float]:
    """
    Round coordinates to specified precision.

    5 decimal places ≈ 1.1 meter precision, suitable for cache keys.

    Args:
        lat: Latitude
        lng: Longitude
        decimals: Number of decimal places (default: 5)

    Returns:
        Tuple of (rounded_lat, rounded_lng)
    """
    return (round(lat, decimals), round(lng, decimals))
