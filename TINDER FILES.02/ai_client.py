"""
AI client — OpenAI vision + TTS.

Public functions:
  get_hookup_individual_verdict(image_bytes)       → {"sentiment": str, "text": str, "filler_mp3": bytes, "verdict_mp3": bytes}
  get_hookup_final_verdict(image_bytes_list)        → same
  get_date_verdict(image_bytes_list)                → same
  text_to_speech(text)                              → mp3 bytes
"""
import base64
import json
import random

from openai import OpenAI
import config

_client = None


def init():
    global _client
    _client = OpenAI(api_key=config.OPENAI_API_KEY)


# ── Internal helpers ───────────────────────────────────────────────────────────

def _b64(image_bytes):
    return base64.b64encode(image_bytes).decode("utf-8")


def _image_content(image_bytes_list):
    """Build list of image_url content blocks."""
    blocks = []
    for img in image_bytes_list:
        blocks.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{_b64(img)}"},
        })
    return blocks


def _call_vision(system_prompt, image_bytes_list, max_tokens):
    """Send images to GPT-4o and return raw response string."""
    content = _image_content(image_bytes_list)
    content.append({"type": "text", "text": "What's your take?"})

    response = _client.chat.completions.create(
        model=config.VISION_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": content},
        ],
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content


def _parse(raw):
    """
    Parse structured JSON response.
    Expected: {"sentiment": "hot|meh|yucky", "text": "..."}
    Falls back to {"sentiment": "meh", "text": raw} on any error.
    """
    try:
        data = json.loads(raw)
        sentiment = data.get("sentiment", "meh").lower().strip()
        if sentiment not in ("hot", "meh", "yucky"):
            sentiment = "meh"
        text = data.get("text", "").strip()
        if not text:
            text = raw
        return {"sentiment": sentiment, "text": text}
    except Exception:
        return {"sentiment": "meh", "text": raw}


def _build_verdict_package(parsed):
    """
    Pre-generate TTS for both filler and verdict text.
    Returns the parsed dict augmented with mp3 bytes so long-press plays instantly.
    """
    filler_text  = random.choice(config.FILLER_LINES)
    verdict_text = parsed["text"]

    filler_mp3  = text_to_speech(filler_text)
    verdict_mp3 = text_to_speech(verdict_text)

    return {
        "sentiment":   parsed["sentiment"],
        "text":        verdict_text,
        "filler_mp3":  filler_mp3,
        "verdict_mp3": verdict_mp3,
    }


# ── Public API ─────────────────────────────────────────────────────────────────

def get_hookup_individual_verdict(image_bytes):
    """
    Single-image hookup read.
    image_bytes : raw JPEG bytes
    returns     : {"sentiment", "text", "filler_mp3", "verdict_mp3"}
    """
    raw    = _call_vision(config.HOOKUP_INDIVIDUAL_PROMPT, [image_bytes], max_tokens=80)
    parsed = _parse(raw)
    return _build_verdict_package(parsed)


def get_hookup_final_verdict(image_bytes_list):
    """
    Comparative verdict across all 5 images (standalone, includes filler TTS).
    image_bytes_list : list of raw JPEG bytes (up to 5)
    returns          : {"sentiment", "text", "filler_mp3", "verdict_mp3"}
    """
    raw    = _call_vision(config.HOOKUP_FINAL_PROMPT, image_bytes_list, max_tokens=160)
    parsed = _parse(raw)
    return _build_verdict_package(parsed)


def get_hookup_game_plan(image_bytes_list):
    """
    Comparative game plan for the 5th-image double-verdict flow.
    No filler TTS — the connector line serves as the transition.
    image_bytes_list : list of raw JPEG bytes (all 5)
    returns          : {"sentiment", "text", "verdict_mp3"}
    """
    raw    = _call_vision(config.HOOKUP_FINAL_PROMPT, image_bytes_list, max_tokens=160)
    parsed = _parse(raw)
    return {
        "sentiment":   parsed["sentiment"],
        "text":        parsed["text"],
        "verdict_mp3": text_to_speech(parsed["text"]),
    }


def get_date_verdict(image_bytes_list):
    """
    Progressive date mode read — tier chosen by image count.
    image_bytes_list : list of raw JPEG bytes (1-5)
    returns          : {"sentiment", "text", "filler_mp3", "verdict_mp3"}
    """
    tier   = config.get_date_tier(len(image_bytes_list))
    raw    = _call_vision(tier["prompt"], image_bytes_list, tier["max_tokens"])
    parsed = _parse(raw)
    return _build_verdict_package(parsed)


def text_to_speech(text):
    """Convert text to MP3 bytes using OpenAI TTS."""
    response = _client.audio.speech.create(
        model=config.TTS_MODEL,
        voice=config.TTS_VOICE,
        input=text,
    )
    return response.content
