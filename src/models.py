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


class ComponentScores(BaseModel):
    """Individual component scores for property condition."""
    roof: int = Field(ge=1, le=10)
    siding: int = Field(ge=1, le=10)
    landscaping: int = Field(ge=1, le=10)
    vacancy_signals: int = Field(ge=1, le=10)


class PropertyScore(BaseModel):
    """VLM scoring results."""
    overall_score: int = Field(ge=1, le=10)
    reasoning: str = Field(max_length=2000)
    component_scores: ComponentScores
    confidence: ConfidenceLevel
    image_quality_issues: Optional[str] = None
    scoring_model: str = "claude-sonnet-4-20250514"
    scored_at: datetime = Field(default_factory=datetime.now)


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

    # Scoring (single angle - for backward compatibility and standard tier)
    prospect_score: Optional[int] = Field(None, ge=1, le=10)
    score_reasoning: Optional[str] = None
    score_roof: Optional[int] = Field(None, ge=1, le=10)
    score_siding: Optional[int] = Field(None, ge=1, le=10)
    score_landscaping: Optional[int] = Field(None, ge=1, le=10)
    score_vacancy: Optional[int] = Field(None, ge=1, le=10)
    scoring_model: Optional[str] = None
    confidence: Optional[ConfidenceLevel] = None

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
        self.prospect_score = score.overall_score
        self.score_reasoning = score.reasoning
        self.score_roof = score.component_scores.roof
        self.score_siding = score.component_scores.siding
        self.score_landscaping = score.component_scores.landscaping
        self.score_vacancy = score.component_scores.vacancy_signals
        self.scoring_model = score.scoring_model
        self.confidence = score.confidence
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
            self.prospect_score = first_score.overall_score
            self.score_reasoning = first_score.reasoning
            self.score_roof = first_score.component_scores.roof
            self.score_siding = first_score.component_scores.siding
            self.score_landscaping = first_score.component_scores.landscaping
            self.score_vacancy = first_score.component_scores.vacancy_signals
            self.scoring_model = first_score.scoring_model
            self.confidence = first_score.confidence

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
