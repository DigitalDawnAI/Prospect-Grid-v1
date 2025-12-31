"""VLM property scoring module using Google Gemini 2.0 Flash."""

import os
import json
import logging
from typing import Optional, List
from pathlib import Path
import google.generativeai as genai
from dotenv import load_dotenv

from .models import PropertyScore, ComponentScores, ConfidenceLevel, StreetViewImage

load_dotenv()
logger = logging.getLogger(__name__)


class GeminiPropertyScorer:
    """Handles property condition scoring using Gemini 2.0 Flash vision model."""

    def __init__(self, api_key: Optional[str] = None, model: str = "gemini-2.0-flash-exp"):
        """
        Initialize scorer with Google API key.

        Args:
            api_key: Google API key (or from environment)
            model: Gemini model to use (default: gemini-2.0-flash-exp)
        """
        self.api_key = api_key or os.getenv("GOOGLE_API_KEY") or os.getenv("GOOGLE_MAPS_API_KEY")
        if not self.api_key:
            raise ValueError("Google API key not found in environment")

        genai.configure(api_key=self.api_key)
        self.model = genai.GenerativeModel(model)

        # Load scoring prompt
        prompt_path = Path(__file__).parent.parent / "prompts" / "scoring_v1.txt"
        if prompt_path.exists():
            with open(prompt_path, "r") as f:
                self.scoring_prompt = f.read()
        else:
            # Fallback prompt if file doesn't exist
            self.scoring_prompt = """Analyze this property image and provide a JSON response with:
{
  "overall_score": 1-10,
  "reasoning": "brief explanation",
  "component_scores": {
    "roof": 1-10,
    "siding": 1-10,
    "landscaping": 1-10,
    "vacancy_signals": 1-10
  },
  "confidence": "high|medium|low"
}

Score 10 = severe distress, 1 = excellent condition"""

    def score(self, street_view: StreetViewImage) -> Optional[PropertyScore]:
        """
        Score a property based on Street View imagery.

        Args:
            street_view: Street View image data

        Returns:
            PropertyScore if successful, None otherwise
        """
        if not street_view.image_available or not street_view.image_data:
            logger.warning("No image data available for scoring")
            return None

        try:
            logger.info("Sending image to Gemini for scoring...")

            # Create image part
            image_part = {
                "mime_type": "image/jpeg",
                "data": street_view.image_data
            }

            # Call Gemini API
            response = self.model.generate_content([
                image_part,
                self.scoring_prompt
            ])

            # Extract text response
            response_text = response.text

            # Parse JSON response
            score_data = self._parse_response(response_text)

            if score_data:
                return self._create_property_score(score_data)
            else:
                logger.error("Failed to parse scoring response")
                return None

        except Exception as e:
            logger.error(f"Gemini scoring error: {e}")
            return None

    def score_multiple(self, street_view: StreetViewImage, image_urls: List[str]) -> List[Optional[PropertyScore]]:
        """
        Score multiple angles of the same property.

        Args:
            street_view: Street View image data (not used, just for compatibility)
            image_urls: List of image URLs to score

        Returns:
            List of PropertyScore objects (one per angle)
        """
        import requests

        scores = []
        angle_names = ["North", "East", "South", "West"]

        for idx, url in enumerate(image_urls):
            try:
                # Fetch image data
                response = requests.get(url, timeout=15)
                response.raise_for_status()
                image_data = response.content

                # Create temporary StreetViewImage
                temp_sv = StreetViewImage(
                    image_url=url,
                    image_data=image_data,
                    image_available=True
                )

                # Score this angle
                logger.info(f"Scoring {angle_names[idx]} angle...")
                score = self.score(temp_sv)

                if score:
                    # Add angle name to reasoning
                    score.reasoning = f"[{angle_names[idx]} View] {score.reasoning}"
                    score.scoring_model = f"{self.model._model_name} ({angle_names[idx]})"

                scores.append(score)

            except Exception as e:
                logger.error(f"Error scoring angle {idx}: {e}")
                scores.append(None)

        return scores

    def _parse_response(self, response_text: str) -> Optional[dict]:
        """
        Parse Gemini's JSON response.

        Args:
            response_text: Raw response text

        Returns:
            Parsed JSON dict or None if parsing fails
        """
        try:
            # Try to parse as JSON directly
            return json.loads(response_text)
        except json.JSONDecodeError:
            # Try to extract JSON from markdown code block
            if "```json" in response_text:
                try:
                    json_str = response_text.split("```json")[1].split("```")[0].strip()
                    return json.loads(json_str)
                except (IndexError, json.JSONDecodeError):
                    pass

            # Try to find JSON object in text
            try:
                start = response_text.index("{")
                end = response_text.rindex("}") + 1
                json_str = response_text[start:end]
                return json.loads(json_str)
            except (ValueError, json.JSONDecodeError):
                pass

            logger.error(f"Could not parse JSON from response: {response_text[:200]}")
            return None

    def _create_property_score(self, score_data: dict) -> PropertyScore:
        """
        Create PropertyScore model from parsed response.

        Args:
            score_data: Parsed JSON response

        Returns:
            PropertyScore instance
        """
        component_scores = ComponentScores(
            roof=score_data["component_scores"]["roof"],
            siding=score_data["component_scores"]["siding"],
            landscaping=score_data["component_scores"]["landscaping"],
            vacancy_signals=score_data["component_scores"]["vacancy_signals"]
        )

        confidence = ConfidenceLevel(score_data["confidence"].lower())

        return PropertyScore(
            overall_score=score_data["overall_score"],
            reasoning=score_data["reasoning"],
            component_scores=component_scores,
            confidence=confidence,
            image_quality_issues=score_data.get("image_quality_issues"),
            scoring_model=self.model._model_name
        )
