from __future__ import annotations

import base64
import json
import os
import re
from typing import Any

import httpx
from openai import OpenAI

CORAL_MODELS = [
    "deepseek-v4.1-flash",
    "deepseek-v4.1-flash-fast",
    "deepseek-v4.1-flash-value",
]
OPENAI_MODELS = [
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4.1",
    "gpt-4.1-mini",
]
DEFAULTS = {
    "coral": "deepseek-v4.1-flash",
    "openai": "gpt-4o",
}
# gpt-image-1 was shut down on 2026-10-23.
IMAGE_MODEL = "gpt-image-2.5-sunburst"

BANNED = (
    "excited to announce",
    "in today's world",
    "game-changer",
    "delve",
    "unlock",
    "elevate",
    "proud to",
    "thrilled",
)


class GenerationError(Exception):
    pass


def provider_for_model(model: str) -> str:
    if model in OPENAI_MODELS or model.startswith("gpt-"):
        return "openai"
    return "coral"


def resolve_model(provider: str, model: str | None) -> str:
    allowed = CORAL_MODELS if provider == "coral" else OPENAI_MODELS
    if model in allowed:
        return model
    return DEFAULTS[provider]


def chat_client(provider: str) -> OpenAI:
    if provider == "coral":
        key = os.getenv("CORAL_API_KEY", "").strip()
        if not key:
            raise GenerationError("CORAL_API_KEY is not set in .env")
        base = os.getenv("CORAL_BASE_URL", "https://inference.coralbricks.ai/v1").strip()
        return OpenAI(api_key=key, base_url=base.rstrip("/"), timeout=120)
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        raise GenerationError("OPENAI_API_KEY is not set in .env")
    return OpenAI(api_key=key, timeout=120)


def _profile_block(company: dict[str, Any]) -> str:
    return (
        f"Company: {company['company_name']}\n"
        f"What they do: {company['what_they_do']}\n"
        f"Audience: {company['audience']}\n"
        f"Offer: {company['offer']}\n"
        f"Proof: {company['proof']}\n"
        f"Voice: {company['voice']}\n"
        f"Brand colors: {company['brand_colors']}\n"
        f"Website: {company.get('website') or 'n/a'}"
    )


def _parse_json(text: str) -> dict[str, Any]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise GenerationError("The model did not return JSON") from None
        data = json.loads(raw[start : end + 1])
    if not isinstance(data, dict):
        raise GenerationError("The model did not return a JSON object")
    return data


def _complete(provider: str, model: str, system: str, user: str, temperature: float) -> dict[str, Any]:
    client = chat_client(provider)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    try:
        try:
            completion = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                response_format={"type": "json_object"},
            )
        except Exception:
            completion = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
            )
    except GenerationError:
        raise
    except Exception as exc:
        raise GenerationError(f"{provider} error: {exc}") from exc
    content = (completion.choices[0].message.content or "").strip()
    if not content:
        raise GenerationError(f"{provider} returned an empty caption")
    return _parse_json(content)


def strategize(
    company: dict[str, Any],
    provider: str,
    model: str,
    avoid_angle: str,
    brief: str = "",
) -> dict[str, Any]:
    brief = (brief or "").strip()[:1200]
    if brief:
        direction = (
            "The user supplied a campaign brief. Follow that brief and the company profile. "
            "Stay in this company's industry, offer, and audience."
        )
        user = f"{_profile_block(company)}\n\nCampaign brief:\n{brief}"
    else:
        direction = "No topic was given. Invent one fresh post from the company profile."
        user = _profile_block(company)
    system = (
        "You are a LinkedIn creative director. "
        f"{direction} Return only JSON with keys angle, captions, recommended, art_direction. "
        "captions is an array of exactly 3 strings. recommended is 0, 1, or 2. "
        "Each caption is 140 to 220 characters. The first sentence must stand alone when the feed cuts the rest. "
        "One concrete idea: a buyer moment, a result, or a before and after. "
        f"Do not reuse this previous angle: {avoid_angle or 'none'}. "
        f"Never use these phrases: {', '.join(BANNED)}. "
        "Match the voice. Use the company name only if the sentence needs it. "
        "At most two specific hashtags at the end. No emoji. "
        "Write to the named audience. "
        "art_direction is one sentence for a square photograph with no text, letters, or logos in the frame."
    )
    data = _complete(provider, model, system, user, 0.9)
    captions = data.get("captions")
    if not isinstance(captions, list) or len(captions) < 1:
        raise GenerationError("The model did not return captions")
    captions = [str(item).strip() for item in captions if str(item).strip()]
    if len(captions) < 1:
        raise GenerationError("The model returned blank captions")
    while len(captions) < 3:
        captions.append(captions[0])
    captions = captions[:3]
    try:
        recommended = int(data.get("recommended", 0))
    except (TypeError, ValueError):
        recommended = 0
    if recommended not in (0, 1, 2):
        recommended = 0
    angle = str(data.get("angle") or "company moment").strip()
    art = str(data.get("art_direction") or "").strip()
    return {
        "angle": angle,
        "captions": captions,
        "recommended": recommended,
        "art_direction": art,
    }


def edit_caption(
    company: dict[str, Any],
    provider: str,
    model: str,
    caption: str,
    art_direction: str,
) -> dict[str, str]:
    system = (
        "You tighten one LinkedIn caption. Return only JSON with keys caption, alt_text, image_brief. "
        "caption stays 140 to 220 characters, keeps the same idea, and makes the first line sharper. "
        f"Never use these phrases: {', '.join(BANNED)}. No emoji. At most two specific hashtags. "
        "alt_text is one sentence for a screen reader and has no hashtags. "
        "image_brief describes a square photograph. No words, letters, logos, or interface in the picture. "
        "Use the brand colors only as a color mood."
    )
    user = (
        f"{_profile_block(company)}\n\n"
        f"Caption to tighten:\n{caption}\n\n"
        f"Starting art direction:\n{art_direction or 'A single concrete scene from the caption.'}"
    )
    data = _complete(provider, model, system, user, 0.4)
    final = str(data.get("caption") or caption).strip()
    if not final:
        final = caption
    return {
        "caption": final,
        "alt_text": str(data.get("alt_text") or f"Image for {company['company_name']}").strip(),
        "image_brief": str(data.get("image_brief") or art_direction or caption).strip(),
    }


def image_prompt(company: dict[str, Any], image_brief: str) -> str:
    return (
        "Editorial square photograph for a LinkedIn feed. "
        "No words, no letters, no numbers, no logos, no watermarks, no screens, no mockups. "
        f"Color mood only, not as painted text: {company['brand_colors']}. "
        "One clear subject, natural light, high contrast, generous negative space. "
        f"Scene: {image_brief}"
    )


def render_image(prompt: str, dest_path: str) -> None:
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        raise GenerationError("OPENAI_API_KEY is not set, so the image cannot be created")
    client = OpenAI(api_key=key, timeout=180)
    try:
        result = client.images.generate(
            model=IMAGE_MODEL,
            prompt=prompt,
            size="1024x1024",
            quality="medium",
            n=1,
        )
    except Exception as exc:
        raise GenerationError(f"Image error: {exc}") from exc
    item = result.data[0]
    raw = getattr(item, "b64_json", None)
    if raw:
        blob = base64.b64decode(raw)
    elif getattr(item, "url", None):
        response = httpx.get(item.url, timeout=60)
        response.raise_for_status()
        blob = response.content
    else:
        raise GenerationError("The image API returned no file")
    with open(dest_path, "wb") as handle:
        handle.write(blob)
