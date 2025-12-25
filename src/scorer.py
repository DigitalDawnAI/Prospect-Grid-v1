"""VLM property scoring module using Claude."""

import os
import json
import base64
import logging
from typing import Optional
from pathlib import Path
from anthropic import Anthropic
from dotenv import load_dotenv

from .models import PropertyScore, ComponentScores, ConfidenceLevel, StreetViewImage

load_dotenv()
logger = logging.getLogger(__name__)


class PropertyScorer:
    """Handles property condition scoring using Claude vision model."""

    def __init__(self, api_key: Optional[str] = None, model: str = "claude-sonnet-4-20250514"):
        """
        Initialize scorer with Anthropic API key.

        Args:
            api_key: Anthropic API key (or from environment)
            model: Claude model to use
        """
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("Anthropic API key not found in environment")

        self.client = Anthropic(api_key=self.api_key)
        self.model = model

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
            # Encode image to base64
            image_base64 = base64.standard_b64encode(street_view.image_data).decode('utf-8')

            # Call Claude API
            logger.info("Sending image to Claude for scoring...")
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": image_base64
                            }
                        },
                        {
                            "type": "text",
                            "text": self.scoring_prompt
                        }
                    ]
                }]
            )

            # Extract text response
            response_text = response.content[0].text

            # Parse JSON response
            score_data = self._parse_response(response_text)

            if score_data:
                return self._create_property_score(score_data)
            else:
                logger.error("Failed to parse scoring response")
                return None

        except Exception as e:
            logger.error(f"Scoring error: {e}")
            return None

    def _parse_response(self, response_text: str) -> Optional[dict]:
        """
        Parse Claude's JSON response.

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
            scoring_model=self.model
        )
