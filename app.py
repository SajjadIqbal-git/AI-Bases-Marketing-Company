from __future__ import annotations

import os
from datetime import datetime, timezone

import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000").rstrip("/")
BYPASS_OPENAI = os.getenv("BYPASS_OPENAI", "true").lower() in ("1", "true", "yes", "on")

OPENAI_MODELS = [
    "gpt-4o-mini",
    "gpt-4o",
    "gpt-4.1-mini",
    "gpt-4.1",
]

TEST_POST_TEMPLATE = (
    "Quick connection test from my AI Post Generator app "
    f"({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}). "
    "Safe to delete — just verifying the LinkedIn API works."
)

st.set_page_config(
    page_title="AI LinkedIn Poster",
    page_icon="🔗",
    layout="wide",
)

if "draft" not in st.session_state:
    st.session_state.draft = TEST_POST_TEMPLATE if BYPASS_OPENAI else ""


def api_get(path: str):
    url = f"{BACKEND_URL}{path}"
    try:
        return requests.get(url, timeout=30)
    except requests.RequestException as exc:
        st.error(f"Cannot reach backend at {BACKEND_URL}. Start it with: uvicorn main:app --reload --port 8000")
        st.caption(str(exc))
        return None


def api_post(path: str, payload: dict | None = None):
    url = f"{BACKEND_URL}{path}"
    try:
        return requests.post(url, json=payload or {}, timeout=120)
    except requests.RequestException as exc:
        st.error(f"Backend request failed: {exc}")
        return None


def publish_post(text: str) -> None:
    with st.spinner("Publishing..."):
        resp = api_post("/api/post", {"text": text.strip()})
    if resp and resp.status_code == 200:
        result = resp.json()
        st.balloons()
        st.success("Posted to LinkedIn.")
        if result.get("post_id"):
            st.caption(f"Post id: {result['post_id']}")
    elif resp is not None:
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:  # noqa: BLE001
            detail = resp.text
        st.error(detail)


query = st.query_params
if query.get("linkedin_connected") == "1":
    st.success("LinkedIn connected successfully.")
    st.query_params.clear()
if query.get("linkedin_error"):
    st.error(f"LinkedIn connection failed: {query.get('linkedin_error')}")
    st.query_params.clear()

st.title("AI LinkedIn Poster")
if BYPASS_OPENAI:
    st.caption("**Test mode:** OpenAI is bypassed. Connect LinkedIn and post a test message below.")
else:
    st.caption("Generate drafts with OpenAI and publish to your LinkedIn profile.")

health = api_get("/health")
if health is None or health.status_code != 200:
    st.stop()

cfg_resp = api_get("/config/status")
cfg = cfg_resp.json() if cfg_resp and cfg_resp.status_code == 200 else {}

col_side, col_main = st.columns([1, 2])

with col_side:
    st.subheader("LinkedIn")
    status_resp = api_get("/auth/status")
    connected = False
    profile = None
    if status_resp and status_resp.status_code == 200:
        data = status_resp.json()
        connected = data.get("connected", False)
        profile = data.get("profile")

    if not cfg.get("linkedin_configured"):
        st.warning("Set LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET in `.env`.")

    if connected and profile:
        st.success(f"Connected as **{profile.get('name') or 'LinkedIn user'}**")
        if profile.get("email"):
            st.caption(profile["email"])
        if st.button("Disconnect", use_container_width=True):
            resp = api_post("/auth/logout")
            if resp and resp.status_code == 200:
                st.session_state.draft = TEST_POST_TEMPLATE if BYPASS_OPENAI else ""
                st.rerun()
    else:
        st.info("Connect your account to post.")
        st.link_button(
            "Connect LinkedIn",
            f"{BACKEND_URL}/auth/login",
            use_container_width=True,
        )

    if not BYPASS_OPENAI:
        st.divider()
        st.subheader("OpenAI")
        if not cfg.get("openai_configured"):
            st.warning("Set OPENAI_API_KEY in `.env`.")

        model = st.selectbox(
            "Model",
            OPENAI_MODELS,
            index=0,
            help="Uses OPENAI_DEFAULT_MODEL from .env when not overridden here.",
        )
        tone = st.selectbox("Tone", ["professional", "conversational", "inspiring", "educational", "bold"])
        include_hashtags = st.checkbox("Include hashtags", value=True)
        max_chars = st.slider("Max length (chars)", 800, 3000, 2800, 100)
    else:
        model = tone = None
        include_hashtags = True
        max_chars = 2800
        st.divider()
        st.info("Set `BYPASS_OPENAI=false` in `.env` to turn AI drafting back on.")

with col_main:
    if BYPASS_OPENAI:
        st.subheader("LinkedIn post test")
        draft = st.text_area(
            "Post text",
            value=st.session_state.draft or TEST_POST_TEMPLATE,
            height=200,
            max_chars=3000,
            key="draft_editor",
        )
        st.session_state.draft = draft
        st.caption(f"{len(draft)} / 3000 characters")

        c1, c2 = st.columns(2)
        with c1:
            if st.button("Reset test message", use_container_width=True):
                st.session_state.draft = TEST_POST_TEMPLATE
                st.session_state.draft_editor = TEST_POST_TEMPLATE
                st.rerun()
        with c2:
            post_disabled = not connected or not draft.strip()
            if st.button(
                "Post to LinkedIn",
                type="primary",
                disabled=post_disabled,
                use_container_width=True,
            ):
                publish_post(draft)
    else:
        st.subheader("1. What should this post be about?")
        prompt = st.text_area(
            "Brief",
            height=140,
            placeholder="e.g. Lessons from shipping our first AI feature to production...",
        )

        gen_col1, _gen_col2 = st.columns([1, 4])
        with gen_col1:
            generate_clicked = st.button("Generate draft", type="primary", use_container_width=True)

        if generate_clicked:
            if not prompt.strip():
                st.warning("Enter a brief first.")
            elif not cfg.get("openai_configured"):
                st.warning("Configure OpenAI in `.env` first.")
            else:
                with st.spinner("Generating with OpenAI..."):
                    resp = api_post(
                        "/api/generate",
                        {
                            "prompt": prompt.strip(),
                            "model": model,
                            "tone": tone,
                            "max_chars": max_chars,
                            "include_hashtags": include_hashtags,
                        },
                    )
                if resp and resp.status_code == 200:
                    text = resp.json().get("text", "")
                    st.session_state.draft = text
                    st.session_state.draft_editor = text
                    st.success("Draft ready — edit below before posting.")
                    st.rerun()
                elif resp is not None:
                    try:
                        detail = resp.json().get("detail", resp.text)
                    except Exception:  # noqa: BLE001
                        detail = resp.text
                    st.error(detail)

        st.subheader("2. Edit your post")
        draft = st.text_area(
            "Post text",
            value=st.session_state.draft,
            height=320,
            max_chars=3000,
            key="draft_editor",
        )
        st.session_state.draft = draft
        st.caption(f"{len(draft)} / 3000 characters")

        post_disabled = not connected or not draft.strip()
        if st.button("Post to LinkedIn", type="primary", disabled=post_disabled, use_container_width=True):
            publish_post(draft)

with st.expander("Setup checklist"):
    st.markdown(
        f"""
1. Copy `.env.example` to `.env` and add keys.
2. In [LinkedIn Developer Portal](https://www.linkedin.com/developers/), add redirect URL: `{cfg.get("redirect_uri", "http://localhost:8000/auth/callback")}`.
3. Request product access for **Share on LinkedIn** (`w_member_social`) and **Sign In with LinkedIn using OpenID Connect**.
4. Run backend: `uvicorn main:app --reload --port 8000`
5. Run UI: `streamlit run app.py`
        """
    )