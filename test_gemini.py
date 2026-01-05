#!/usr/bin/env python3
"""Test script to debug Gemini API issues"""

import os
import sys
from dotenv import load_dotenv
import google.generativeai as genai
import requests

load_dotenv()

def test_api_key():
    """Test if API key is valid"""
    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GOOGLE_MAPS_API_KEY")
    print(f"API Key found: {api_key[:20]}..." if api_key else "❌ No API key found")
    return api_key

def test_list_models(api_key):
    """Test listing available models"""
    try:
        genai.configure(api_key=api_key)
        print("\n📋 Available Gemini models:")
        models = genai.list_models()
        for m in models:
            if 'generateContent' in m.supported_generation_methods:
                print(f"  ✓ {m.name}")
        return True
    except Exception as e:
        print(f"❌ Error listing models: {e}")
        return False

def test_simple_text_generation(api_key):
    """Test basic text generation"""
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')

        print("\n🧪 Testing text generation...")
        response = model.generate_content("Say 'Hello World'")
        print(f"✓ Response: {response.text}")
        return True
    except Exception as e:
        print(f"❌ Error in text generation: {e}")
        return False

def test_vision_with_url(api_key):
    """Test vision API with a real image URL"""
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')

        # Use a test image URL
        test_url = "https://maps.googleapis.com/maps/api/streetview?size=800x600&location=39.3643,-74.4229&fov=90&heading=135&pitch=0&key=" + os.getenv("GOOGLE_MAPS_API_KEY")

        print("\n🖼️  Testing vision API with Street View image...")
        print(f"Image URL: {test_url[:80]}...")

        # Fetch image
        response = requests.get(test_url, timeout=15)
        response.raise_for_status()
        image_data = response.content

        print(f"✓ Image fetched ({len(image_data)} bytes)")

        # Create image part
        image_part = {
            "mime_type": "image/jpeg",
            "data": image_data
        }

        # Test with simple prompt
        print("Sending to Gemini for analysis...")
        gemini_response = model.generate_content([
            image_part,
            "Describe this image in one sentence."
        ])

        print(f"✓ Gemini response: {gemini_response.text}")
        return True

    except Exception as e:
        print(f"❌ Error in vision test: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_scoring_prompt(api_key):
    """Test with actual scoring prompt"""
    try:
        from pathlib import Path
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')

        # Load actual prompt
        prompt_path = Path(__file__).parent / "prompts" / "scoring_v1.txt"
        if prompt_path.exists():
            with open(prompt_path, "r") as f:
                scoring_prompt = f.read()
            print(f"\n✓ Loaded scoring prompt ({len(scoring_prompt)} chars)")
        else:
            print("\n⚠️  Scoring prompt file not found, using fallback")
            scoring_prompt = """Analyze this property and return JSON:
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
}"""

        # Use test image
        test_url = "https://maps.googleapis.com/maps/api/streetview?size=800x600&location=39.3643,-74.4229&fov=90&heading=135&pitch=0&key=" + os.getenv("GOOGLE_MAPS_API_KEY")

        print("\n🏠 Testing with scoring prompt...")

        # Fetch image
        response = requests.get(test_url, timeout=15)
        response.raise_for_status()
        image_data = response.content

        # Create image part
        image_part = {
            "mime_type": "image/jpeg",
            "data": image_data
        }

        # Test scoring
        print("Sending to Gemini for scoring...")
        gemini_response = model.generate_content([
            image_part,
            scoring_prompt
        ])

        print(f"✓ Raw response:\n{gemini_response.text}\n")

        # Try parsing
        import json
        try:
            # Try direct parse
            parsed = json.loads(gemini_response.text)
            print("✓ Successfully parsed as JSON")
            print(f"  Overall score: {parsed.get('overall_score')}")
            print(f"  Confidence: {parsed.get('confidence')}")
        except json.JSONDecodeError:
            # Try extracting from markdown
            text = gemini_response.text
            if "```json" in text:
                json_str = text.split("```json")[1].split("```")[0].strip()
                parsed = json.loads(json_str)
                print("✓ Successfully parsed JSON from markdown block")
                print(f"  Overall score: {parsed.get('overall_score')}")
                print(f"  Confidence: {parsed.get('confidence')}")
            else:
                print("⚠️  Response is not valid JSON")

        return True

    except Exception as e:
        print(f"❌ Error in scoring test: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("=" * 60)
    print("Gemini API Diagnostic Test")
    print("=" * 60)

    # Test 1: API Key
    api_key = test_api_key()
    if not api_key:
        print("\n❌ FATAL: No API key found. Set GOOGLE_API_KEY in .env")
        sys.exit(1)

    # Test 2: List models
    if not test_list_models(api_key):
        print("\n⚠️  Could not list models - API key may not have Gemini API enabled")
        print("Enable it at: https://console.cloud.google.com/apis/library/generativelanguage.googleapis.com")

    # Test 3: Simple text
    test_simple_text_generation(api_key)

    # Test 4: Vision with URL
    test_vision_with_url(api_key)

    # Test 5: Full scoring
    test_scoring_prompt(api_key)

    print("\n" + "=" * 60)
    print("Test complete!")
    print("=" * 60)
