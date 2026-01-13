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
        Initialize Gemini scorer with optional API key.

        API key validation and model initialization are deferred until first use
        to prevent import-time crashes when environment variables aren't set.

        Args:
            api_key: Gemini API key. Do NOT fall back to Google Maps key.
            model: Gemini model name.
            min_delay_s: Minimum spacing between Gemini calls (global, per process).
            max_retries: Retries on rate limit / transient errors.
            backoff_base_s: Base for exponential backoff.
            backoff_cap_s: Max sleep between retries.
        """
        self._api_key = api_key
        self.model_name = model
        self._model = None  # Lazy initialization
        self._configured = False

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

    @property
    def api_key(self) -> str:
        """Lazy-load API key from environment on first access."""
        if self._api_key is None:
            self._api_key = os.getenv("GEMINI_API_KEY")
        if not self._api_key:
            raise ValueError(
                "Gemini API key not configured. "
                "Set GEMINI_API_KEY environment variable."
            )
        return self._api_key

    @property
    def model(self):
        """Lazy-initialize Gemini model on first access."""
        if self._model is None:
            if not self._configured:
                genai.configure(api_key=self.api_key)
                self._configured = True
            self._model = genai.GenerativeModel(self.model_name)
        return self._model

    def score(self, street_view: StreetViewImage) -> Optional[PropertyScore]:
        """Score a property based on Street View imagery."""
        if not street_view.image_available or not street_view.image_data:
            logger.warning("No image data available for scoring")
            return None

        try:
            image_part = {"mime_type": "image/jpeg", "data": street_view.image_data}

            logger.info("Sending image to Gemini for scoring...")
            response_text = self._generate_with_backoff([self.scoring_prompt, image_part])

            score_data = self._parse_response(response_text)
            if not score_data:
                logger.error("Failed to parse scoring response")
                return None

            return self._create_property_score(score_data)

        except Exception as e:
            logger.error(f"Gemini scoring error: {e}", exc_info=True)
            return None

    def score_multiple(self, street_view: StreetViewImage, image_urls: List[str]) -> List[Optional[PropertyScore]]:
        """
        Score multiple angles of the same property.

        Note: This makes N separate Gemini calls. With 3 images per address
        (front-facing angles), keep concurrency low elsewhere or you will hit rate limits.
        """
        import requests

        scores: List[Optional[PropertyScore]] = []
        # Updated for front-facing angles (3 images instead of 4 cardinal)
        angle_names = ["Front", "Front-Left", "Front-Right", "Angle4", "Angle5"]

        for idx, url in enumerate(image_urls):
            try:
                r = requests.get(url, timeout=20)
                r.raise_for_status()

                temp_sv = StreetViewImage(
                    image_url=url,
                    image_data=r.content,
                    image_available=True,
                )

                logger.info(f"Scoring {angle_names[idx] if idx < len(angle_names) else f'Angle{idx}'} angle...")
                score = self.score(temp_sv)

                if score:
                    angle = angle_names[idx] if idx < len(angle_names) else f"Angle{idx}"
                    # If your PropertyScore model has these fields, annotate them; otherwise remove
                    if hasattr(score, "brief_reasoning") and score.brief_reasoning:
                        score.brief_reasoning = f"[{angle} View] {score.brief_reasoning}"
                    if hasattr(score, "scoring_model"):
                        score.scoring_model = f"{self.model_name} ({angle})"

                scores.append(score)

            except Exception as e:
                logger.error(f"Error scoring angle {idx}: {e}", exc_info=True)
                scores.append(None)

        return scores

    def _generate_with_backoff(self, parts: list) -> str:
        """
        Enforces global spacing + exponential backoff on rate limit/transient errors.
        Returns response.text.
        """
        attempt = 0
        while True:
            attempt += 1

            # Global spacing between calls
            self._sleep_for_min_delay()

            try:
                resp = self.model.generate_content(parts)
                # Some SDK responses can be empty/None if blocked; handle defensively
                text = getattr(resp, "text", None)
                if not text:
                    raise RuntimeError("Empty Gemini response.text")
                return text

            except Exception as e:
                if attempt > self.max_retries or not self._is_retryable(e):
                    raise

                sleep_s = min(self.backoff_cap_s, self.backoff_base_s * (2 ** (attempt - 1)))
                # jitter
                sleep_s = sleep_s * (0.7 + 0.6 * random.random())
                logger.warning(f"Gemini retryable error (attempt {attempt}/{self.max_retries}): {e}. Sleeping {sleep_s:.2f}s")
                time.sleep(sleep_s)

    def _sleep_for_min_delay(self) -> None:
        if self.min_delay_s <= 0:
            return

        with GeminiPropertyScorer._lock:
            now = time.monotonic()
            elapsed = now - GeminiPropertyScorer._last_call_ts
            if elapsed < self.min_delay_s:
                time.sleep(self.min_delay_s - elapsed)
            GeminiPropertyScorer._last_call_ts = time.monotonic()

    def _is_retryable(self, e: Exception) -> bool:
        """
        Best-effort detection for rate limit/transient errors across SDK exceptions.
        """
        msg = str(e).lower()
        retry_markers = [
            "429",
            "resource_exhausted",
            "rate limit",
            "quota",
            "temporarily",
            "timeout",
            "unavailable",
            "internal",
            "deadline",
        ]
        return any(m in msg for m in retry_markers)

    def _parse_response(self, response_text: str) -> Optional[dict]:
        """Parse Gemini's JSON response."""
        try:
            return json.loads(response_text)
        except json.JSONDecodeError:
            # Extract JSON from ```json blocks
            if "```json" in response_text:
                try:
                    json_str = response_text.split("```json", 1)[1].split("```", 1)[0].strip()
                    return json.loads(json_str)
                except (IndexError, json.JSONDecodeError):
                    pass

            # Extract first {...} object
            try:
                start = response_text.index("{")
                end = response_text.rindex("}") + 1
                return json.loads(response_text[start:end])
            except (ValueError, json.JSONDecodeError):
                logger.error(f"Could not parse JSON from response: {response_text[:400]}")
                return None

    def _create_property_score(self, score_data: dict) -> PropertyScore:
        """
        Create PropertyScore model from parsed response.

        Supports BOTH:
        - New schema: property_score/confidence_level/recommendation/brief_reasoning/...
        - Old-ish schema: overall_score/confidence/reasoning (maps to new fields)
        """
        from .models import ConfidenceLevel, RecommendationLevel

        # Normalize schema
        if "property_score" not in score_data and "overall_score" in score_data:
            # Map 1-10 overall_score to 0-100 property_score (10 = severe distress)
            overall = score_data.get("overall_score")
            try:
                overall_f = float(overall)
                score_data["property_score"] = max(0, min(100, int(round((overall_f / 10.0) * 100))))
            except Exception:
                score_data["property_score"] = 50

            score_data["brief_reasoning"] = score_data.get("reasoning", "No reasoning provided")
            conf = (score_data.get("confidence") or "medium").lower()
            score_data["confidence_level"] = conf
            # If not provided, set a neutral default
            score_data["recommendation"] = score_data.get("recommendation", "review").lower()

        # Required fields with defaults
        property_score = score_data.get("property_score")
        if property_score is None:
            raise ValueError(f"Missing property_score in Gemini response: keys={list(score_data.keys())}")

        confidence_raw = (score_data.get("confidence_level") or "medium").lower()
        recommendation_raw = (score_data.get("recommendation") or "review").lower()

        # Defensive enum parsing
        try:
            confidence = ConfidenceLevel(confidence_raw)
        except Exception:
            confidence = ConfidenceLevel("medium")

        try:
            recommendation = RecommendationLevel(recommendation_raw)
        except Exception:
            recommendation = RecommendationLevel("review")

        return PropertyScore(
            property_score=property_score,
            confidence_level=confidence,
            primary_indicators_observed=score_data.get("primary_indicators_observed", []),
            recommendation=recommendation,
            brief_reasoning=score_data.get("brief_reasoning", "No reasoning provided"),
            image_quality_issues=score_data.get("image_quality_issues"),
            scoring_model=self.model_name,
        )
