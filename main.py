from __future__ import annotations

import os
import secrets
from pathlib import Path

import bcrypt
from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

import db
import generate
import linkedin

load_dotenv()

ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "frontend"
templates = Jinja2Templates(directory=str(FRONTEND))

app = FastAPI(title="Postroom")
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET", "").strip() or secrets.token_urlsafe(32),
    same_site="lax",
    https_only=False,
)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Accept", "Content-Type"],
)


@app.on_event("startup")
def _startup() -> None:
    db.init_db()


def _flash(request: Request, message: str, kind: str = "error") -> None:
    request.session["flash"] = {"message": message, "kind": kind}


def _wants_json(request: Request) -> bool:
    return "application/json" in request.headers.get("accept", "")


def _page_result(
    request: Request,
    message: str,
    *,
    ok: bool,
    error_to: str,
    success_to: str,
    status_code: int = 400,
):
    if _wants_json(request):
        return JSONResponse({"ok": ok, "message": message}, status_code=200 if ok else status_code)
    if not ok and message:
        _flash(request, message)
    return RedirectResponse(success_to if ok else error_to, status_code=303)


def _publish_result(
    request: Request,
    message: str,
    *,
    ok: bool,
    status_code: int = 400,
    post_id: str = "",
):
    if _wants_json(request):
        body: dict[str, str | bool] = {"ok": ok, "message": message}
        if post_id:
            body["post_id"] = post_id
        if not ok and status_code == 401:
            body["login_url"] = "/login"
        return JSONResponse(body, status_code=200 if ok else status_code)
    _flash(request, message, "ok" if ok else "error")
    return RedirectResponse("/studio", status_code=303)


def _pop_flash(request: Request) -> dict[str, str] | None:
    flash = request.session.pop("flash", None)
    return flash if isinstance(flash, dict) else None


def _company_id(request: Request) -> int | None:
    value = request.session.get("company_id")
    return int(value) if value else None


def _require_company(request: Request) -> dict | RedirectResponse:
    company_id = _company_id(request)
    if not company_id:
        return RedirectResponse("/login", status_code=303)
    company = db.get_company(company_id)
    if company is None:
        request.session.clear()
        return RedirectResponse("/login", status_code=303)
    return company


def _profile_values(
    company_name: str,
    what_they_do: str,
    audience: str,
    offer: str,
    proof: str,
    voice: str,
    brand_colors: str,
    website: str,
) -> dict[str, str] | str:
    fields = {
        "company_name": company_name.strip(),
        "what_they_do": what_they_do.strip(),
        "audience": audience.strip(),
        "offer": offer.strip(),
        "proof": proof.strip(),
        "voice": voice.strip(),
        "brand_colors": brand_colors.strip(),
        "website": website.strip(),
    }
    missing = [name for name, value in fields.items() if name != "website" and not value]
    if missing:
        return "Fill in every brand field except the website."
    return fields


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _check_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:
        return False


def _studio_context(request: Request, company: dict) -> dict:
    draft = db.latest_draft(company["id"])
    connection = db.get_linkedin(company["id"])
    return {
        "request": request,
        "title": "Studio",
        "company": company,
        "draft": draft,
        "linkedin": connection,
        "linkedin_ready": linkedin.configured(),
        "coral_models": generate.CORAL_MODELS,
        "openai_models": generate.OPENAI_MODELS,
        "flash": _pop_flash(request),
    }


@app.get("/styles.css")
def styles() -> FileResponse:
    return FileResponse(FRONTEND / "styles.css", media_type="text/css")


@app.get("/demo")
def demo_page() -> FileResponse:
    return FileResponse(FRONTEND / "socialpilot_ai_client_demo.html", media_type="text/html")


@app.get("/api/linkedin")
def linkedin_status(request: Request):
    company_id = _company_id(request)
    company_count = db.count_companies()
    if not company_id:
        return {
            "authenticated": False,
            "company_count": company_count,
            "login_url": "/login",
            "studio_url": "/studio",
        }
    company = db.get_company(company_id)
    if company is None:
        return {
            "authenticated": False,
            "company_count": company_count,
            "login_url": "/login",
            "studio_url": "/studio",
        }
    connection = db.get_linkedin(company_id)
    draft = db.latest_draft(company_id)
    draft_payload = None
    if draft:
        has_image = bool(draft.get("image_path") and Path(draft["image_path"]).is_file())
        draft_payload = {
            "id": draft["id"],
            "caption": draft.get("caption") or "",
            "alt_text": draft.get("alt_text") or "",
            "approved": bool(draft.get("approved")),
            "has_image": has_image,
            "image_url": f"/media/{draft['id']}" if has_image else None,
            "linkedin_post_id": draft.get("linkedin_post_id") or "",
        }
    connected = bool(connection and connection.get("access_token") and connection.get("member_sub"))
    return {
        "authenticated": True,
        "company_count": company_count,
        "company_name": company["company_name"],
        "login_url": "/login",
        "connect_url": "/auth/linkedin",
        "studio_url": "/studio",
        "linkedin_ready": linkedin.configured(),
        "linkedin": {
            "connected": connected,
            "display_name": (connection or {}).get("display_name") or "",
        },
        "draft": draft_payload,
    }


@app.get("/")
def home(request: Request) -> RedirectResponse:
    if _company_id(request):
        return RedirectResponse("/studio", status_code=303)
    return RedirectResponse("/login", status_code=303)


@app.get("/login")
def login_page(request: Request):
    if _company_id(request):
        return RedirectResponse("/studio", status_code=303)
    return templates.TemplateResponse(
        request,
        "login.html",
        {"title": "Log in", "flash": _pop_flash(request), "email": ""},
    )


@app.post("/login")
def login(request: Request, email: str = Form(), password: str = Form()):
    record = db.get_company_by_email(email.strip().lower())
    if record is None or not _check_password(password, record["password_hash"]):
        return _page_result(request, "That email and password do not match.", ok=False, error_to="/login", success_to="/studio")
    request.session["company_id"] = record["id"]
    return _page_result(request, "Logged in.", ok=True, error_to="/login", success_to="/studio")


@app.get("/register")
def register_page(request: Request):
    if _company_id(request):
        return RedirectResponse("/studio", status_code=303)
    return templates.TemplateResponse(
        request,
        "register.html",
        {"title": "Register", "flash": _pop_flash(request), "form": {}},
    )


@app.post("/register")
def register(
    request: Request,
    email: str = Form(),
    password: str = Form(),
    company_name: str = Form(),
    what_they_do: str = Form(),
    audience: str = Form(),
    offer: str = Form(),
    proof: str = Form(),
    voice: str = Form(),
    brand_colors: str = Form(),
    website: str = Form(""),
):
    email_clean = email.strip().lower()
    profile = _profile_values(
        company_name, what_they_do, audience, offer, proof, voice, brand_colors, website
    )
    if not email_clean or "@" not in email_clean:
        return _page_result(request, "Enter a valid work email.", ok=False, error_to="/register", success_to="/studio")
    if len(password) < 8:
        return _page_result(request, "Use a password of at least 8 characters.", ok=False, error_to="/register", success_to="/studio")
    if isinstance(profile, str):
        return _page_result(request, profile, ok=False, error_to="/register", success_to="/studio")
    if db.get_company_by_email(email_clean):
        return _page_result(request, "That email is already registered. Log in instead.", ok=False, error_to="/register", success_to="/studio")
    company = db.create_company(email=email_clean, password_hash=_hash_password(password), **profile)
    request.session["company_id"] = company["id"]
    return _page_result(request, "Company registered.", ok=True, error_to="/register", success_to="/studio")


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    if _wants_json(request):
        return {"ok": True, "message": "Logged out."}
    return RedirectResponse("/login", status_code=303)


@app.get("/studio")
def studio(request: Request):
    company = _require_company(request)
    if isinstance(company, RedirectResponse):
        return company
    return templates.TemplateResponse(request, "studio.html", _studio_context(request, company))


@app.post("/studio/generate")
def studio_generate(
    request: Request,
    provider: str = Form("coral"),
    model_coral: str = Form(""),
    model_openai: str = Form(""),
):
    company = _require_company(request)
    if isinstance(company, RedirectResponse):
        if _wants_json(request):
            return JSONResponse(
                {"ok": False, "message": "Register or log in before generating."},
                status_code=401,
            )
        return company
    provider = "openai" if provider == "openai" else "coral"
    model = generate.resolve_model(provider, model_openai if provider == "openai" else model_coral)
    previous = db.latest_draft(company["id"])
    avoid = previous["angle"] if previous else ""
    try:
        plan = generate.strategize(company, provider, model, avoid)
        chosen = plan["captions"][plan["recommended"]]
        edited = generate.edit_caption(company, provider, model, chosen, plan["art_direction"])
        draft = db.create_draft(
            company_id=company["id"],
            angle=plan["angle"],
            provider=provider,
            model=model,
            captions=plan["captions"],
            caption=edited["caption"],
            alt_text=edited["alt_text"],
        )
        image_path = db.IMAGE_DIR / f"draft-{draft['id']}.png"
        generate.render_image(generate.image_prompt(company, edited["image_brief"]), str(image_path))
        db.update_draft(draft["id"], company["id"], image_path=str(image_path))
        message = "A new post is ready. Edit the caption, then approve it."
        ok = True
        status_code = 200
    except generate.GenerationError as exc:
        message = str(exc)
        ok = False
        status_code = 400
    except Exception as exc:  # noqa: BLE001
        message = f"Could not generate the post: {exc}"
        ok = False
        status_code = 400
    return _publish_result(request, message, ok=ok, status_code=status_code)


@app.post("/studio/caption")
def studio_caption(
    request: Request,
    draft_id: int = Form(),
    caption: str = Form(),
):
    company = _require_company(request)
    if isinstance(company, RedirectResponse):
        return company
    draft = db.get_draft(draft_id, company["id"])
    caption = caption.strip()
    if draft is None or not caption:
        _flash(request, "Choose a caption first.")
        return RedirectResponse("/studio", status_code=303)
    provider = draft["provider"] if draft["provider"] in ("coral", "openai") else "openai"
    model = draft["model"] or generate.DEFAULTS[provider]
    try:
        edited = generate.edit_caption(company, provider, model, caption, "")
        image_path = db.IMAGE_DIR / f"draft-{draft['id']}.png"
        generate.render_image(generate.image_prompt(company, edited["image_brief"]), str(image_path))
        db.update_draft(
            draft["id"],
            company["id"],
            caption=edited["caption"],
            alt_text=edited["alt_text"],
            image_path=str(image_path),
            approved=0,
        )
        _flash(request, "Caption and image updated. Approve it before posting.", "ok")
    except generate.GenerationError as exc:
        _flash(request, str(exc))
    except Exception as exc:  # noqa: BLE001
        _flash(request, f"Could not update the image: {exc}")
    return RedirectResponse("/studio", status_code=303)


@app.post("/studio/approve")
def studio_approve(
    request: Request,
    draft_id: int = Form(),
    caption: str = Form(),
):
    company = _require_company(request)
    if isinstance(company, RedirectResponse):
        if _wants_json(request):
            return JSONResponse(
                {"ok": False, "message": "Register or log in before approving."},
                status_code=401,
            )
        return company
    draft = db.get_draft(draft_id, company["id"])
    caption = caption.strip()
    if draft is None or not caption:
        return _publish_result(request, "Generate a post before approving it.", ok=False)
    if draft.get("linkedin_post_id"):
        return _publish_result(request, "This post is already on LinkedIn.", ok=False)
    db.update_draft(draft["id"], company["id"], caption=caption[:3000], approved=1)
    return _publish_result(request, "Approved. Post it to LinkedIn when you are ready.", ok=True)


@app.post("/studio/revise")
def studio_revise(request: Request, draft_id: int = Form()):
    company = _require_company(request)
    if isinstance(company, RedirectResponse):
        return company
    draft = db.get_draft(draft_id, company["id"])
    if draft is None:
        _flash(request, "Generate a post before editing it.")
        return RedirectResponse("/studio", status_code=303)
    if draft.get("linkedin_post_id"):
        _flash(request, "This post is already on LinkedIn.")
        return RedirectResponse("/studio", status_code=303)
    db.update_draft(draft["id"], company["id"], approved=0)
    _flash(request, "Edit the caption, then approve it again.", "ok")
    return RedirectResponse("/studio", status_code=303)


@app.post("/studio/publish")
async def studio_publish(
    request: Request,
    draft_id: int = Form(),
):
    company = _require_company(request)
    if isinstance(company, RedirectResponse):
        if _wants_json(request):
            return JSONResponse(
                {"ok": False, "message": "Log in before posting.", "login_url": "/login"},
                status_code=401,
            )
        return company
    draft = db.get_draft(draft_id, company["id"])
    caption = (draft.get("caption") or "").strip() if draft else ""
    if draft is None or not caption:
        return _publish_result(request, "Generate a post before publishing.", ok=False)
    if not draft.get("approved"):
        return _publish_result(request, "Approve the caption before posting.", ok=False)
    if not draft.get("image_path") or not Path(draft["image_path"]).is_file():
        return _publish_result(request, "This draft has no image yet.", ok=False)
    connection = db.get_linkedin(company["id"])
    if not connection or not connection.get("access_token") or not connection.get("member_sub"):
        return _publish_result(request, "Connect LinkedIn before posting.", ok=False)
    image_bytes = Path(draft["image_path"]).read_bytes()
    try:
        post_id = await linkedin.publish_image_post(
            access_token=connection["access_token"],
            member_sub=connection["member_sub"],
            caption=caption[:3000],
            image_bytes=image_bytes,
            alt_text=draft.get("alt_text") or "",
        )
    except linkedin.LinkedInError as exc:
        return _publish_result(request, str(exc), ok=False, status_code=502)
    db.update_draft(draft["id"], company["id"], caption=caption[:3000], linkedin_post_id=post_id or "")
    return _publish_result(
        request,
        "Posted to your LinkedIn profile.",
        ok=True,
        post_id=post_id or "",
    )


@app.get("/media/{draft_id}")
def media(request: Request, draft_id: int):
    company_id = _company_id(request)
    if not company_id:
        return RedirectResponse("/login", status_code=303)
    draft = db.get_draft(draft_id, company_id)
    if draft is None or not draft.get("image_path"):
        return RedirectResponse("/studio", status_code=303)
    path = Path(draft["image_path"])
    if not path.is_file():
        return RedirectResponse("/studio", status_code=303)
    return FileResponse(path, media_type="image/png")


@app.get("/profile")
def profile(request: Request):
    company = _require_company(request)
    if isinstance(company, RedirectResponse):
        return company
    connection = db.get_linkedin(company["id"])
    return templates.TemplateResponse(
        request,
        "profile.html",
        {
            "title": "Profile",
            "company": company,
            "linkedin": connection,
            "linkedin_ready": linkedin.configured(),
            "flash": _pop_flash(request),
        },
    )


@app.post("/profile")
def profile_save(
    request: Request,
    company_name: str = Form(),
    what_they_do: str = Form(),
    audience: str = Form(),
    offer: str = Form(),
    proof: str = Form(),
    voice: str = Form(),
    brand_colors: str = Form(),
    website: str = Form(""),
):
    company = _require_company(request)
    if isinstance(company, RedirectResponse):
        return company
    profile_data = _profile_values(
        company_name, what_they_do, audience, offer, proof, voice, brand_colors, website
    )
    if isinstance(profile_data, str):
        _flash(request, profile_data)
        return RedirectResponse("/profile", status_code=303)
    db.update_company(company["id"], **profile_data)
    _flash(request, "Profile saved.", "ok")
    return RedirectResponse("/profile", status_code=303)


@app.get("/auth/linkedin")
def auth_linkedin(request: Request):
    company = _require_company(request)
    if isinstance(company, RedirectResponse):
        return company
    if not linkedin.configured():
        _flash(request, "Add LinkedIn client id and secret to .env first.")
        return RedirectResponse("/studio", status_code=303)
    state = secrets.token_urlsafe(24)
    db.save_oauth_state(state, company["id"])
    return RedirectResponse(linkedin.authorization_url(state), status_code=302)


@app.get("/auth/callback")
async def auth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    if error:
        _flash(request, f"LinkedIn connection failed: {error}")
        return RedirectResponse("/studio", status_code=303)
    if not code or not state:
        _flash(request, "LinkedIn did not return a login code.")
        return RedirectResponse("/studio", status_code=303)
    company_id = db.pop_oauth_state(state)
    if company_id is None:
        _flash(request, "That LinkedIn login expired. Try Connect again.")
        return RedirectResponse("/studio", status_code=303)
    try:
        token = await linkedin.exchange_code(code)
        profile = await linkedin.fetch_userinfo(token["access_token"])
    except linkedin.LinkedInError as exc:
        _flash(request, str(exc))
        return RedirectResponse("/studio", status_code=303)
    db.save_linkedin(
        company_id,
        access_token=token["access_token"],
        member_sub=profile.get("sub"),
        display_name=profile.get("name"),
        email=profile.get("email"),
    )
    request.session["company_id"] = company_id
    _flash(request, "LinkedIn connected.", "ok")
    return RedirectResponse("/studio", status_code=303)


@app.post("/auth/disconnect")
def auth_disconnect(request: Request) -> RedirectResponse:
    company_id = _company_id(request)
    if company_id:
        db.clear_linkedin(company_id)
        _flash(request, "LinkedIn disconnected.", "ok")
    return RedirectResponse("/studio", status_code=303)
