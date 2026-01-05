"""Data models for ProspectGrid."""

from datetime import datetime
from typing import Optional, Dict, Any
from enum import Enum
from pydantic import BaseModel, Field, field_validator


class GeocodeStatus(str, Enum):
    """Geocoding result status."""
    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL = "partial"


class ProcessingStatus(str, Enum):
    """Overall processing status."""
    PENDING = "pending"
    COMPLETE = "complete"
    ERROR = "error"
    NO_IMAGERY = "no_imagery"


class LeadStatus(str, Enum):
    """User-managed lead status."""
    NEW = "new"
    CONTACTED = "contacted"
    QUALIFIED = "qualified"
    DEAD = "dead"


class ConfidenceLevel(str, Enum):
    """VLM confidence level."""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class RawAddress(BaseModel):
    """Raw address input from CSV or manual entry."""
    address: str
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None

    @property
    def full_address(self) -> str:
        """Construct full address string."""
        parts = [self.address]
        if self.city:
            parts.append(self.city)
        if self.state:
            parts.append(self.state)
        if self.zip:
            parts.append(self.zip)
        return ", ".join(parts)


class GeocodedProperty(BaseModel):
    """Property with geocoding results."""
    address_full: str
    address_street: str
    city: str
    state: str
    zip: str
    county: Optional[str] = None
    latitude: float
    longitude: float
    geocode_status: GeocodeStatus = GeocodeStatus.SUCCESS

    @field_validator('latitude')
    @classmethod
    def validate_latitude(cls, v: float) -> float:
        """Validate latitude range."""
        if not -90 <= v <= 90:
            raise ValueError(f"Latitude must be between -90 and 90, got {v}")
        return v

    @field_validator('longitude')
    @classmethod
    def validate_longitude(cls, v: float) -> float:
        """Validate longitude range."""
        if not -180 <= v <= 180:
            raise ValueError(f"Longitude must be between -180 and 180, got {v}")
        return v


class StreetViewImage(BaseModel):
    """Street View image metadata and data."""
    image_url: str
    image_urls_multi_angle: Optional[list[str]] = None  # N, E, S, W angles
    image_data: Optional[bytes] = None
    image_date: Optional[str] = None  # Format: "YYYY-MM"
    pano_id: Optional[str] = None
    image_available: bool = True
    imagery_stale: bool = False

    @field_validator('imagery_stale', mode='before')
    @classmethod
    def check_stale(cls, v: bool, info) -> bool:
        """Auto-detect stale imagery (>3 years old)."""
        if 'image_date' in info.data and info.data['image_date']:
            try:
                year = int(info.data['image_date'].split('-')[0])
                current_year = datetime.now().year
                if current_year - year > 3:
                    return True
            except (ValueError, IndexError):
                pass
        return v


class RecommendationLevel(str, Enum):
    """Property acquisition recommendation level."""
    STRONG_CANDIDATE = "strong_candidate"
    MODERATE_CANDIDATE = "moderate_candidate"
    WEAK_CANDIDATE = "weak_candidate"
    NOT_A_CANDIDATE = "not_a_candidate"


class ComponentScores(BaseModel):
    """Individual component scores for property condition (legacy - kept for backward compatibility)."""
    roof: int = Field(ge=1, le=10)
    siding: int = Field(ge=1, le=10)
    landscaping: int = Field(ge=1, le=10)
    vacancy_signals: int = Field(ge=1, le=10)


class PropertyScore(BaseModel):
    """VLM scoring results - new format (0-100 scale for distressed property acquisition)."""
    property_score: int = Field(ge=0, le=100, description="Distress score (0-100, higher = better candidate)")
    confidence_level: ConfidenceLevel
    primary_indicators_observed: list[str] = Field(default_factory=list, description="Key distress indicators")
    recommendation: RecommendationLevel
    brief_reasoning: str = Field(max_length=2000, description="Analysis of property condition")

    # Legacy fields for backward compatibility (auto-converted from 0-100 scale)
    overall_score: Optional[int] = Field(None, ge=1, le=10, description="Legacy 1-10 scale")
    reasoning: Optional[str] = Field(None, max_length=2000)
    component_scores: Optional[ComponentScores] = None

    # Metadata
    image_quality_issues: Optional[str] = None
    scoring_model: str = "gemini-2.5-flash"
    scored_at: datetime = Field(default_factory=datetime.now)

    def __init__(self, **data):
        """Initialize and auto-populate legacy fields for backward compatibility."""
        super().__init__(**data)

        # Auto-convert property_score (0-100) to overall_score (1-10) for legacy support
        if self.property_score is not None and self.overall_score is None:
            self.overall_score = min(10, max(1, round(self.property_score / 10)))

        # Copy brief_reasoning to reasoning
        if self.brief_reasoning and not self.reasoning:
            self.reasoning = self.brief_reasoning

    @property
    def confidence(self) -> ConfidenceLevel:
        """Alias for backward compatibility."""
        return self.confidence_level


class ScoredProperty(BaseModel):
    """Complete property record with all data."""
    # Address & Location
    address_full: str
    address_street: str
    city: str
    state: str
    zip: str
    county: Optional[str] = None
    latitude: float
    longitude: float

    # Scoring (single angle - new format)
    property_score: Optional[int] = Field(None, ge=0, le=100, description="Distress score (0-100)")
    recommendation: Optional[RecommendationLevel] = None
    primary_indicators: Optional[list[str]] = None
    score_reasoning: Optional[str] = None
    confidence_level: Optional[ConfidenceLevel] = None

    # Legacy scoring fields (1-10 scale - kept for backward compatibility)
    prospect_score: Optional[int] = Field(None, ge=1, le=10)
    score_roof: Optional[int] = Field(None, ge=1, le=10)
    score_siding: Optional[int] = Field(None, ge=1, le=10)
    score_landscaping: Optional[int] = Field(None, ge=1, le=10)
    score_vacancy: Optional[int] = Field(None, ge=1, le=10)
    scoring_model: Optional[str] = None
    confidence: Optional[ConfidenceLevel] = None  # Alias for confidence_level

    # Multi-angle scoring (premium tier - stores all 4 angle scores)
    scores_by_angle: Optional[list[PropertyScore]] = None  # N, E, S, W angle scores

    # Imagery
    streetview_url: Optional[str] = None
    streetview_urls_multi_angle: Optional[list[str]] = None  # N, E, S, W angles
    streetview_date: Optional[str] = None
    image_available: bool = True
    imagery_stale: bool = False

    # Status
    geocode_status: GeocodeStatus = GeocodeStatus.SUCCESS
    processing_status: ProcessingStatus = ProcessingStatus.PENDING
    lead_status: LeadStatus = LeadStatus.NEW

    # Metadata
    campaign_id: Optional[str] = None
    notes: Optional[str] = None
    processed_date: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    @classmethod
    def from_geocoded(cls, geocoded: GeocodedProperty, campaign_id: Optional[str] = None) -> "ScoredProperty":
        """Create from geocoded property."""
        return cls(
            address_full=geocoded.address_full,
            address_street=geocoded.address_street,
            city=geocoded.city,
            state=geocoded.state,
            zip=geocoded.zip,
            county=geocoded.county,
            latitude=geocoded.latitude,
            longitude=geocoded.longitude,
            geocode_status=geocoded.geocode_status,
            campaign_id=campaign_id
        )

    def add_street_view(self, street_view: StreetViewImage) -> None:
        """Add Street View data to property."""
        self.streetview_url = street_view.image_url
        self.streetview_urls_multi_angle = street_view.image_urls_multi_angle
        self.streetview_date = street_view.image_date
        self.image_available = street_view.image_available
        self.imagery_stale = street_view.imagery_stale

    def add_score(self, score: PropertyScore) -> None:
        """Add scoring data to property (single angle)."""
        # New format fields
        self.property_score = score.property_score
        self.recommendation = score.recommendation
        self.primary_indicators = score.primary_indicators_observed
        self.score_reasoning = score.brief_reasoning
        self.confidence_level = score.confidence_level
        self.scoring_model = score.scoring_model

        # Legacy fields for backward compatibility
        self.prospect_score = score.overall_score
        self.confidence = score.confidence_level

        # Legacy component scores (if available)
        if score.component_scores:
            self.score_roof = score.component_scores.roof
            self.score_siding = score.component_scores.siding
            self.score_landscaping = score.component_scores.landscaping
            self.score_vacancy = score.component_scores.vacancy_signals

        self.processing_status = ProcessingStatus.COMPLETE
        self.processed_date = datetime.now()
        self.updated_at = datetime.now()

    def add_scores_multi_angle(self, scores: list[PropertyScore]) -> None:
        """Add multi-angle scoring data to property (N, E, S, W angles)."""
        self.scores_by_angle = scores

        # Also populate single-angle fields with the first valid score for backward compatibility
        valid_scores = [s for s in scores if s is not None]
        if valid_scores:
            first_score = valid_scores[0]

            # New format fields
            self.property_score = first_score.property_score
            self.recommendation = first_score.recommendation
            self.primary_indicators = first_score.primary_indicators_observed
            self.score_reasoning = first_score.brief_reasoning
            self.confidence_level = first_score.confidence_level
            self.scoring_model = first_score.scoring_model

            # Legacy fields
            self.prospect_score = first_score.overall_score
            self.confidence = first_score.confidence_level

            if first_score.component_scores:
                self.score_roof = first_score.component_scores.roof
                self.score_siding = first_score.component_scores.siding
                self.score_landscaping = first_score.component_scores.landscaping
                self.score_vacancy = first_score.component_scores.vacancy_signals

        self.processing_status = ProcessingStatus.COMPLETE
        self.processed_date = datetime.now()
        self.updated_at = datetime.now()


class Campaign(BaseModel):
    """Campaign metadata."""
    campaign_id: str
    campaign_name: str
    target_area: Optional[str] = None
    date_created: datetime = Field(default_factory=datetime.now)
    date_completed: Optional[datetime] = None
    properties_submitted: int = 0
    properties_processed: int = 0
    properties_failed: int = 0
    avg_score: Optional[float] = None
    high_priority_count: int = 0  # score >= 7
    total_cost: float = 0.0
    status: str = "draft"  # draft, processing, complete, error


class ProcessingResult(BaseModel):
    """Result of processing a batch."""
    campaign_id: str
    total_submitted: int
    successful: int
    failed: int
    no_imagery: int
    duplicates: int
    total_cost: float
    avg_score: Optional[float] = None
    high_priority_count: int = 0
    processing_time_seconds: float
    errors: list[str] = Field(default_factory=list)
