"""Unit tests for geospatial utility functions."""

import pytest
import math
from src.geo_utils import (
    calculate_bearing,
    haversine_distance,
    generate_candidate_headings,
    round_coords,
)


class TestCalculateBearing:
    """Tests for bearing calculation."""

    def test_due_north(self):
        """Camera south of property should point north (~0°)."""
        bearing = calculate_bearing(39.0, -74.0, 40.0, -74.0)
        # Should be close to 0° (North)
        assert bearing < 5 or bearing > 355

    def test_due_south(self):
        """Camera north of property should point south (~180°)."""
        bearing = calculate_bearing(40.0, -74.0, 39.0, -74.0)
        assert 175 <= bearing <= 185

    def test_due_east(self):
        """Camera west of property should point east (~90°)."""
        bearing = calculate_bearing(39.0, -75.0, 39.0, -74.0)
        assert 85 <= bearing <= 95

    def test_due_west(self):
        """Camera east of property should point west (~270°)."""
        bearing = calculate_bearing(39.0, -74.0, 39.0, -75.0)
        assert 265 <= bearing <= 275

    def test_northeast(self):
        """Camera SW of property should point NE (~45°)."""
        bearing = calculate_bearing(39.0, -75.0, 40.0, -74.0)
        assert 30 <= bearing <= 60

    def test_result_is_positive(self):
        """Bearing should always be in range 0-360."""
        # Test many angle combinations
        test_cases = [
            (0, 0, 1, 1),
            (0, 0, -1, -1),
            (45, 90, 46, 91),
            (-33.8688, 151.2093, -33.8600, 151.2100),  # Sydney
        ]
        for lat1, lng1, lat2, lng2 in test_cases:
            bearing = calculate_bearing(lat1, lng1, lat2, lng2)
            assert 0 <= bearing < 360, f"Bearing {bearing} out of range for {lat1},{lng1} -> {lat2},{lng2}"

    def test_same_location_returns_zero(self):
        """Same start and end should return 0 (or NaN, handle gracefully)."""
        bearing = calculate_bearing(39.0, -74.0, 39.0, -74.0)
        # When points are the same, bearing is undefined but atan2 returns 0
        assert 0 <= bearing < 360

    def test_atlantic_city_realistic(self):
        """Test with realistic Street View scenario in Atlantic City."""
        # Property at 123 Main St, Atlantic City (example)
        property_lat, property_lng = 39.3642, -74.4229
        # Typical street panorama is offset ~10-20m
        pano_lat, pano_lng = 39.3641, -74.4231  # Slightly SW of property

        bearing = calculate_bearing(pano_lat, pano_lng, property_lat, property_lng)
        # From SW, pointing NE should be ~30-60°
        assert 20 <= bearing <= 80


class TestHaversineDistance:
    """Tests for distance calculation."""

    def test_same_point_is_zero(self):
        """Distance from point to itself should be 0."""
        dist = haversine_distance(39.0, -74.0, 39.0, -74.0)
        assert dist == 0.0

    def test_known_distance(self):
        """Test with known distance between two points."""
        # NYC to LA is approximately 3,940 km
        nyc = (40.7128, -74.0060)
        la = (34.0522, -118.2437)
        dist = haversine_distance(*nyc, *la)
        # Should be approximately 3.94 million meters
        assert 3_900_000 <= dist <= 4_000_000

    def test_short_distance(self):
        """Test typical Street View offset (~15m)."""
        # Points ~15m apart
        lat1, lng1 = 39.0, -74.0
        # Move ~15m north (1 degree lat ≈ 111km)
        lat2 = lat1 + (15 / 111000)
        lng2 = lng1

        dist = haversine_distance(lat1, lng1, lat2, lng2)
        assert 10 <= dist <= 20  # Should be ~15m


class TestGenerateCandidateHeadings:
    """Tests for candidate heading generation."""

    def test_single_candidate(self):
        """Single candidate should just be the primary heading."""
        headings = generate_candidate_headings(90.0, num_candidates=1)
        assert headings == [90.0]

    def test_three_candidates(self):
        """Three candidates: primary, left, right."""
        headings = generate_candidate_headings(90.0, spread_degrees=25, num_candidates=3)
        assert len(headings) == 3
        assert headings[0] == 90.0  # Primary
        assert headings[1] == 65.0  # Left
        assert headings[2] == 115.0  # Right

    def test_wraparound_at_zero(self):
        """Headings should wrap around at 0/360."""
        headings = generate_candidate_headings(10.0, spread_degrees=25, num_candidates=3)
        assert 10.0 in headings
        assert 345.0 in headings  # 10 - 25 = -15 → 345
        assert 35.0 in headings   # 10 + 25 = 35

    def test_wraparound_at_360(self):
        """Headings should wrap around at 360."""
        headings = generate_candidate_headings(350.0, spread_degrees=25, num_candidates=3)
        assert 350.0 in headings
        assert 325.0 in headings  # 350 - 25 = 325
        assert 15.0 in headings   # 350 + 25 = 375 → 15


class TestRoundCoords:
    """Tests for coordinate rounding."""

    def test_default_precision(self):
        """Default precision is 5 decimal places."""
        lat, lng = round_coords(39.123456789, -74.987654321)
        assert lat == 39.12346
        assert lng == -74.98765

    def test_custom_precision(self):
        """Custom precision works correctly."""
        lat, lng = round_coords(39.123456789, -74.987654321, decimals=3)
        assert lat == 39.123
        assert lng == -74.988

    def test_negative_coords(self):
        """Negative coordinates round correctly."""
        lat, lng = round_coords(-33.868888, 151.209444)
        assert lat == -33.86889
        assert lng == 151.20944


class TestBearingEdgeCases:
    """Edge case tests for bearing calculation."""

    def test_poles(self):
        """Test near poles doesn't break."""
        # Near North Pole
        bearing = calculate_bearing(89.0, 0.0, 89.5, 45.0)
        assert 0 <= bearing < 360

    def test_international_date_line(self):
        """Test crossing international date line."""
        # From Japan to Alaska
        bearing = calculate_bearing(35.6762, 139.6503, 61.2181, -149.9003)
        assert 0 <= bearing < 360

    def test_equator(self):
        """Test along equator."""
        bearing = calculate_bearing(0.0, 0.0, 0.0, 10.0)
        assert 85 <= bearing <= 95  # Should be ~90° (East)
