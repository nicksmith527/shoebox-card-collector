from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.error
import urllib.request


GEMINI_MODEL = os.getenv("GEMINI_CARD_MODEL", "gemini-3.6-flash")
def _gemini_url(model: str) -> str:
    return (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        + model
        + ":generateContent"
    )



def _api_key():
    return os.getenv("GEMINI_API_KEY", "").strip()


def _clean_json_text(text: str) -> str:
    text = str(text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    return text


def identify_card_photo(uploaded_file) -> dict:
    """
    Ask Gemini to extract sports-card identity fields from one front image.
    Returns a suggestion only; Shoebox requires user confirmation before saving.
    """
    key = _api_key()
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not configured.")

    raw = uploaded_file.getvalue()
    if not raw:
        raise RuntimeError("The image file is empty.")

    mime = getattr(uploaded_file, "type", None) or "image/jpeg"

    prompt = """
You are identifying a physical sports trading card from a photograph.

Return ONLY a JSON object with these keys:
sport, year, manufacturer, set_name, player_name, card_number,
variation, rookie, grading_company, grade, confidence, notes.

Rules:
- Read printed text, logos, card number, player name and design clues.
- card_number should NOT include a leading #.
- year must be an integer or null.
- rookie must be true/false/null.
- grading_company is RAW, PSA, SGC, CGC, BGS, or null.
- grade is a number 1-10 or null.
- confidence is a number from 0 to 1.
- If uncertain, use null rather than inventing a fact.
- set_name should be the product/set name, not the player's team.
- variation should describe a visible parallel/variation only when reasonably certain.
- notes should briefly mention any uncertainty.
"""

    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {
                    "inline_data": {
                        "mime_type": mime,
                        "data": base64.b64encode(raw).decode("ascii"),
                    }
                },
            ]
        }],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
        },
    }

    models = [GEMINI_MODEL]
    # Fallback target can be overridden later without another code change.
    fallback = os.getenv("GEMINI_CARD_FALLBACK_MODEL", "gemini-3.7-flash").strip()
    if fallback and fallback not in models:
        models.append(fallback)

    last_error = None
    result = None

    for model in models:
        for attempt in range(3):
            req = urllib.request.Request(
                f"{_gemini_url(model)}?key={key}",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "Shoebox-Card-Collector/1.0",
                },
                method="POST",
            )

            try:
                with urllib.request.urlopen(req, timeout=45) as response:
                    result = json.loads(response.read().decode("utf-8"))
                break

            except urllib.error.HTTPError as error:
                body = error.read().decode("utf-8", errors="replace")
                last_error = RuntimeError(
                    f"{model} returned HTTP {error.code}: {body[:350]}"
                )

                # Retry transient service errors on the same model.
                if error.code in (429, 500, 502, 503, 504) and attempt < 2:
                    time.sleep(1.0 * (2 ** attempt))
                    continue

                # A missing/retired model should immediately try fallback.
                if error.code == 404:
                    break

                # Other hard failures should not hammer the endpoint.
                break

            except Exception as error:
                last_error = error
                if attempt < 2:
                    time.sleep(1.0 * (2 ** attempt))
                    continue
                break

        if result is not None:
            break

    if result is None:
        raise RuntimeError(
            "AI identification is temporarily unavailable. "
            f"Last provider error: {last_error}"
        )

    candidates = result.get("candidates") or []
    if not candidates:
        raise RuntimeError("Gemini returned no identification candidate.")

    parts = (
        candidates[0]
        .get("content", {})
        .get("parts", [])
    )
    text = "".join(str(p.get("text") or "") for p in parts)
    if not text:
        raise RuntimeError("Gemini returned an empty identification response.")

    try:
        card = json.loads(_clean_json_text(text))
    except Exception as error:
        raise RuntimeError(
            f"Could not parse Gemini's card identification response: {text[:500]}"
        ) from error

    # Normalize common values.
    if card.get("year") not in (None, ""):
        try:
            card["year"] = int(card["year"])
        except Exception:
            card["year"] = None

    if card.get("card_number") is not None:
        card["card_number"] = str(card["card_number"]).strip().lstrip("#")

    try:
        card["confidence"] = float(card.get("confidence") or 0)
    except Exception:
        card["confidence"] = 0.0

    return card
