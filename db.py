from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

DATA_DIR = Path(__file__).resolve().parent / ".data"
DB_PATH = DATA_DIR / "app.db"
IMAGE_DIR = DATA_DIR / "images"
_ready: set[str] = set()

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    company_name TEXT NOT NULL,
    what_they_do TEXT NOT NULL,
    audience TEXT NOT NULL,
    offer TEXT NOT NULL,
    proof TEXT NOT NULL,
    voice TEXT NOT NULL,
    brand_colors TEXT NOT NULL,
    website TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS linkedin_connections (
    company_id INTEGER PRIMARY KEY,
    access_token TEXT NOT NULL,
    member_sub TEXT,
    display_name TEXT,
    email TEXT,
    FOREIGN KEY (company_id) REFERENCES companies(id)
);

CREATE TABLE IF NOT EXISTS oauth_states (
    state TEXT PRIMARY KEY,
    company_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (company_id) REFERENCES companies(id)
);

CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL,
    angle TEXT,
    provider TEXT,
    model TEXT,
    captions_json TEXT,
    caption TEXT,
    alt_text TEXT,
    image_path TEXT,
    linkedin_post_id TEXT,
    approved INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (company_id) REFERENCES companies(id)
);

CREATE INDEX IF NOT EXISTS idx_drafts_company ON drafts(company_id, id DESC);
"""

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    key = str(DB_PATH.resolve())
    if key not in _ready:
        conn.executescript(SCHEMA)
        _migrate(conn)
        _ready.add(key)
    return conn


@contextmanager
def session() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with session():
        return None


def _migrate(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(drafts)")}
    if "approved" not in columns:
        conn.execute("ALTER TABLE drafts ADD COLUMN approved INTEGER NOT NULL DEFAULT 0")


def _company_from_row(row: sqlite3.Row | None, *, include_secret: bool = False) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    if not include_secret:
        data.pop("password_hash", None)
    return data


def create_company(
    *,
    email: str,
    password_hash: str,
    company_name: str,
    what_they_do: str,
    audience: str,
    offer: str,
    proof: str,
    voice: str,
    brand_colors: str,
    website: str,
) -> dict[str, Any]:
    with session() as conn:
        cur = conn.execute(
            """
            INSERT INTO companies (
                email, password_hash, company_name, what_they_do, audience,
                offer, proof, voice, brand_colors, website, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                email,
                password_hash,
                company_name,
                what_they_do,
                audience,
                offer,
                proof,
                voice,
                brand_colors,
                website,
                _now(),
            ),
        )
        company_id = int(cur.lastrowid)
    company = get_company(company_id)
    if company is None:
        raise RuntimeError("Company was not saved")
    return company


def get_company(company_id: int) -> dict[str, Any] | None:
    with session() as conn:
        row = conn.execute("SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone()
    return _company_from_row(row)


def get_company_by_email(email: str) -> dict[str, Any] | None:
    with session() as conn:
        row = conn.execute("SELECT * FROM companies WHERE email = ?", (email,)).fetchone()
    return _company_from_row(row, include_secret=True)


def count_companies() -> int:
    with session() as conn:
        row = conn.execute("SELECT COUNT(*) AS total FROM companies").fetchone()
    return int(row["total"]) if row else 0


def update_company(
    company_id: int,
    *,
    company_name: str,
    what_they_do: str,
    audience: str,
    offer: str,
    proof: str,
    voice: str,
    brand_colors: str,
    website: str,
) -> None:
    with session() as conn:
        conn.execute(
            """
            UPDATE companies
            SET company_name = ?, what_they_do = ?, audience = ?, offer = ?,
                proof = ?, voice = ?, brand_colors = ?, website = ?
            WHERE id = ?
            """,
            (
                company_name,
                what_they_do,
                audience,
                offer,
                proof,
                voice,
                brand_colors,
                website,
                company_id,
            ),
        )


def save_oauth_state(state: str, company_id: int) -> None:
    with session() as conn:
        conn.execute(
            "INSERT INTO oauth_states (state, company_id, created_at) VALUES (?, ?, ?)",
            (state, company_id, _now()),
        )


def pop_oauth_state(state: str) -> int | None:
    with session() as conn:
        row = conn.execute(
            "SELECT company_id FROM oauth_states WHERE state = ?",
            (state,),
        ).fetchone()
        conn.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
    if row is None:
        return None
    return int(row["company_id"])


def save_linkedin(
    company_id: int,
    *,
    access_token: str,
    member_sub: str | None,
    display_name: str | None,
    email: str | None,
) -> None:
    with session() as conn:
        conn.execute(
            """
            INSERT INTO linkedin_connections (
                company_id, access_token, member_sub, display_name, email
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(company_id) DO UPDATE SET
                access_token = excluded.access_token,
                member_sub = excluded.member_sub,
                display_name = excluded.display_name,
                email = excluded.email
            """,
            (company_id, access_token, member_sub, display_name, email),
        )


def get_linkedin(company_id: int) -> dict[str, Any] | None:
    with session() as conn:
        row = conn.execute(
            "SELECT * FROM linkedin_connections WHERE company_id = ?",
            (company_id,),
        ).fetchone()
    return dict(row) if row else None


def clear_linkedin(company_id: int) -> None:
    with session() as conn:
        conn.execute("DELETE FROM linkedin_connections WHERE company_id = ?", (company_id,))


def latest_draft(company_id: int) -> dict[str, Any] | None:
    with session() as conn:
        row = conn.execute(
            """
            SELECT * FROM drafts
            WHERE company_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (company_id,),
        ).fetchone()
    return _draft_from_row(row)


def get_draft(draft_id: int, company_id: int) -> dict[str, Any] | None:
    with session() as conn:
        row = conn.execute(
            "SELECT * FROM drafts WHERE id = ? AND company_id = ?",
            (draft_id, company_id),
        ).fetchone()
    return _draft_from_row(row)


def create_draft(
    *,
    company_id: int,
    angle: str,
    provider: str,
    model: str,
    captions: list[str],
    caption: str,
    alt_text: str,
) -> dict[str, Any]:
    with session() as conn:
        cur = conn.execute(
            """
            INSERT INTO drafts (
                company_id, angle, provider, model, captions_json, caption,
                alt_text, image_path, linkedin_post_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)
            """,
            (
                company_id,
                angle,
                provider,
                model,
                json.dumps(captions),
                caption,
                alt_text,
                _now(),
            ),
        )
        draft_id = int(cur.lastrowid)
    draft = get_draft(draft_id, company_id)
    if draft is None:
        raise RuntimeError("Draft was not saved")
    return draft


def update_draft(
    draft_id: int,
    company_id: int,
    *,
    caption: str | None = None,
    alt_text: str | None = None,
    image_path: str | None = None,
    linkedin_post_id: str | None = None,
    approved: int | None = None,
) -> None:
    fields: list[str] = []
    values: list[Any] = []
    if caption is not None:
        fields.append("caption = ?")
        values.append(caption)
    if alt_text is not None:
        fields.append("alt_text = ?")
        values.append(alt_text)
    if image_path is not None:
        fields.append("image_path = ?")
        values.append(image_path)
    if linkedin_post_id is not None:
        fields.append("linkedin_post_id = ?")
        values.append(linkedin_post_id)
    if approved is not None:
        fields.append("approved = ?")
        values.append(1 if approved else 0)
    if not fields:
        return
    values.extend([draft_id, company_id])
    with session() as conn:
        conn.execute(
            f"UPDATE drafts SET {', '.join(fields)} WHERE id = ? AND company_id = ?",
            values,
        )


def _draft_from_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    raw = data.get("captions_json") or "[]"
    try:
        data["captions"] = json.loads(raw)
    except json.JSONDecodeError:
        data["captions"] = []
    return data
