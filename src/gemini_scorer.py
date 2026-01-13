"""VLM property scoring module using Google Gemini vision model."""

import os
import json
import logging
import time
import random
import threading
from typing import Optional, List
from pathlib import Path

import google.generativeai as genai
from dotenv import load_dotenv

from .models import PropertyScore, StreetViewImage

load_dotenv()
logger = logging.getLogger(__name__)


class GeminiPropertyScorer:
    """Handles property condition scoring using Gemini vision model."""

    # Global throttling across all instances/threads in this process
    _lock = threading.Lock()
    _last_call_ts = 0.0

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gemini-2.5-flash",
        min_delay_s: float = 1.0,
        max_retries: int = 6,
        backoff_base_s: float = 1.0,
        backoff_cap_s: float = 30.0,
    ):
        """
        Args:
            api_key: Gemini API key (prefer GOOGLE_API_KEY). Do NOT fall back to Google Maps key.
            model: Gemini model name.
            min_delay_s: Minimum spacing between Gemini calls (global, per process).
            max_retries: Retries on rate limit / transient errors.
            backoff_base_s: Base for exponential backoff.
            backoff_cap_s: Max sleep between retries.
        """
        # IMPORTANT: do not fall back to GOOGLE_MAPS_API_KEY for Gemini calls
        self.api_key = api_key or os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("Gemini API key not found (set GOOGLE_API_KEY or GEMINI_API_KEY)")

        genai.configure(api_key=self.api_key)
        self.model_name = model
        self.model = genai.GenerativeModel(model)

        self.min_delay_s = float(min_delay_s)
        self.max_retries = int(max_retries)
        self.backoff_base_s = float(backoff_base_s)
        self.backoff_cap_s = float(backoff_cap_s)

        # Load scoring prompt
        prompt_path = Path(__file__).parent.parent / "prompts" / "scoring_v1.txt"
        if prompt_path.exists():
            with open(prompt_path, "r") as f:
                self.scoring_prompt = f.read()
        else:
            # Keep fallback aligned with what _create_property_score can parse
            self.scoring_prompt = """Return ONLY valid JSON with this schema:
{
  "property_score": 0-100,
  "confidence_level": "high|medium|low",
  "recommendation": "skip|review|pursue",
  "brief_reasoning": "short explanation",
  "primary_indicators_observed": ["..."],
  "image_quality_issues": "optional string or null"
}
Scoring: 100 = severe distress, 0 = excellent condition.
"""

    def score(self, street_view: StreetViewImage) -> Optional[PropertyScore]:
        """Score a property based on Street View imagery."""
        if not street_view.image_available or not street_view.image_data:
            logger.warning("No image data available for scoring")
            return None

        try:
            im
