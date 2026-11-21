from __future__ import annotations

import asyncio
import os
import urllib.parse
from typing import Any

import httpx

SCOPES = "openid profile w_member_social"


class LinkedInError(Exception):
    pass


def _version() -> str:
    return os.getenv("LINKEDIN_API_VERSION", "202603").strip() or "202603"


def _client_id() -> str:
    return os.getenv("LINKEDIN_CLIENT_ID", "").strip()


def _client_secret() -> str:
    return os.getenv("LINKEDIN_CLIENT_SECRET", "").strip()


def redirect_uri() -> str:
    return os.getenv("LINKEDIN_REDIRECT_URI", "http://localhost:8000/auth/callback").strip()


def configured() -> bool:
    return bool(_client_id() and _client_secret())


def authorization_url(state: str) -> str:
    if not configured():
        raise LinkedInError("LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET must be set in .env")
    params = {
        "response_type": "code",
        "client_id": _client_id(),
        "redirect_uri": redirect_uri(),
        "state": state,
        "scope": SCOPES,
    }
    return "https://www.linkedin.com/oauth/v2/authorization?" + urllib.parse.urlencode(params)


def _headers(token: str, *, json_body: bool = True) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {token}",
        "LinkedIn-Version": _version(),
        "X-Restli-Protocol-Version": "2.0.0",
    }
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers


async def exchange_code(code: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "https://www.linkedin.com/oauth/v2/accessToken",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri(),
                "client_id": _client_id(),
                "client_secret": _client_secret(),
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if response.status_code != 200:
        raise LinkedInError(response.text[:400])
    payload = response.json()
    if not payload.get("access_token"):
        raise LinkedInError("LinkedIn did not return an access token")
    return payload


async def fetch_userinfo(access_token: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            "https://api.linkedin.com/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if response.status_code != 200:
        raise LinkedInError(f"LinkedIn profile failed: {response.text[:400]}")
    return response.json()


async def _initialize_upload(client: httpx.AsyncClient, token: str, owner: str) -> tuple[str, str]:
    response = await client.post(
        "https://api.linkedin.com/rest/images?action=initializeUpload",
        headers=_headers(token),
        json={"initializeUploadRequest": {"owner": owner}},
    )
    if response.status_code not in (200, 201):
        raise LinkedInError(f"Image upload failed: {response.text[:500]}")
    value = (response.json() or {}).get("value") or {}
    upload_url = value.get("uploadUrl")
    image_urn = value.get("image")
    if not upload_url or not image_urn:
        raise LinkedInError("LinkedIn did not return an upload URL")
    return upload_url, image_urn


async def _put_bytes(client: httpx.AsyncClient, token: str, upload_url: str, image_bytes: bytes) -> None:
    response = await client.put(
        upload_url,
        content=image_bytes,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "image/png",
        },
    )
    if response.status_code not in (200, 201):
        raise LinkedInError(f"Image transfer failed: {response.text[:500]}")


def _post_body(owner: str, caption: str, image_urn: str, alt_text: str) -> dict[str, Any]:
    return {
        "author": owner,
        "commentary": caption,
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "content": {
            "media": {
                "id": image_urn,
                "altText": (alt_text or "Company post image")[:300],
            }
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }


def _not_ready(response: httpx.Response) -> bool:
    text = response.text.lower()
    return any(word in text for word in ("processing", "not ready", "not available", "wait"))


async def publish_image_post(
    *,
    access_token: str,
    member_sub: str,
    caption: str,
    image_bytes: bytes,
    alt_text: str,
) -> str | None:
    owner = f"urn:li:person:{member_sub}"
    async with httpx.AsyncClient(timeout=90.0) as client:
        upload_url, image_urn = await _initialize_upload(client, access_token, owner)
        await _put_bytes(client, access_token, upload_url, image_bytes)
        body = _post_body(owner, caption, image_urn, alt_text)
        response = await client.post(
            "https://api.linkedin.com/rest/posts",
            headers=_headers(access_token),
            json=body,
        )
        if response.status_code not in (200, 201) and _not_ready(response):
            await asyncio.sleep(3)
            response = await client.post(
                "https://api.linkedin.com/rest/posts",
                headers=_headers(access_token),
                json=body,
            )
    if response.status_code not in (200, 201):
        raise LinkedInError(response.text[:800])
    return response.headers.get("x-restli-id") or response.headers.get("X-Restli-Id")
