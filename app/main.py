from __future__ import annotations

import json
import os
import re
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Literal

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from openai import OpenAI
from pydantic import BaseModel, Field
from supabase import Client, create_client

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR.parent / ".env")

APP_NAME = "Daily X Post Agent"
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.6-luna")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
X_ACCESS_TOKEN = os.getenv("X_ACCESS_TOKEN", "")
STORAGE_MODE = os.getenv("STORAGE_MODE", "supabase").lower()

app = FastAPI(title=APP_NAME, version="0.1.0")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

supabase: Client | None = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

_openai: OpenAI | None = None
if os.getenv("OPENAI_API_KEY"):
    _openai = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Local fallback lets you test the API before connecting Supabase.
LOCAL_UPDATES: dict[str, dict[str, Any]] = {}
LOCAL_POSTS: dict[str, dict[str, Any]] = {}

SYSTEM_PROMPT = """
You are a personal daily-progress social media agent for a software-engineering student.

Your job is to transform a raw daily work log into one authentic X post.

Hard rules:
- Never invent work, achievements, numbers, technologies, or results.
- Preserve the user's actual accomplishments and unfinished work.
- Prefer concrete progress over generic motivation.
- Sound like a real developer sharing a build-in-public update, not a corporate account.
- Keep the post concise and easy to scan.
- Avoid repetitive phrases and fake enthusiasm.
- Use emojis sparingly.
- Use at most 3 relevant hashtags.
- Never claim a project is finished unless the user says it is finished.
- If the raw note is ambiguous, use cautious wording rather than guessing.
- Return valid JSON matching the requested shape.
"""


class DailyUpdateIn(BaseModel):
    work_date: date = Field(default_factory=date.today)
    raw_update: str = Field(min_length=1, max_length=10000)
    user_id: str = Field(default="default", min_length=1, max_length=100)


class StructuredUpdate(BaseModel):
    completed: list[str] = Field(default_factory=list)
    in_progress: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    projects: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    notable_result: str | None = None


class GeneratePostIn(BaseModel):
    update_id: str
    tone: Literal["casual", "professional", "build-in-public"] = "build-in-public"
    user_id: str = "default"


class PostResponse(BaseModel):
    id: str
    update_id: str
    post_text: str
    character_count: int
    status: str


class PublishIn(BaseModel):
    post_id: str
    user_id: str = "default"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_post(text: str) -> str:
    text = text.strip().strip('"')
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text[:280]


def mock_structure(raw_update: str) -> StructuredUpdate:
    parts = [p.strip(" .") for p in re.split(r"[\n;]+", raw_update) if p.strip()]
    completed = parts[:6]
    return StructuredUpdate(completed=completed)


def structure_update(raw_update: str) -> StructuredUpdate:
    if not _openai:
        return mock_structure(raw_update)

    schema = {
        "type": "object",
        "properties": {
            "completed": {"type": "array", "items": {"type": "string"}},
            "in_progress": {"type": "array", "items": {"type": "string"}},
            "blockers": {"type": "array", "items": {"type": "string"}},
            "projects": {"type": "array", "items": {"type": "string"}},
            "technologies": {"type": "array", "items": {"type": "string"}},
            "notable_result": {"type": ["string", "null"]},
        },
        "required": ["completed", "in_progress", "blockers", "projects", "technologies", "notable_result"],
        "additionalProperties": False,
    }
    response = _openai.responses.create(
        model=OPENAI_MODEL,
        instructions=SYSTEM_PROMPT,
        input=f"Extract this daily work log into the schema. Do not infer facts.\n\nRAW LOG:\n{raw_update}",
        text={"format": {"type": "json_schema", "name": "daily_update", "strict": True, "schema": schema}},
    )
    return StructuredUpdate.model_validate_json(response.output_text)


def generate_post(structured: StructuredUpdate, raw_update: str, tone: str, work_date: date) -> str:
    if not _openai:
        lines = [f"Daily build log — {work_date.isoformat()} 🚀"]
        for item in structured.completed[:4]:
            lines.append(f"• {item}")
        if structured.in_progress:
            lines.append(f"Still working on: {structured.in_progress[0]}")
        return clean_post("\n".join(lines) + "\n\n#BuildInPublic #DevJourney")

    response = _openai.responses.create(
        model=OPENAI_MODEL,
        instructions=SYSTEM_PROMPT,
        input=(
            f"Write ONE X post for {work_date.isoformat()} in a {tone} tone.\n"
            f"Raw update:\n{raw_update}\n\n"
            f"Structured facts:\n{structured.model_dump_json()}\n\n"
            "Output only the final post text."
        ),
    )
    return clean_post(response.output_text)


def save_update(update: DailyUpdateIn, structured: StructuredUpdate) -> str:
    update_id = str(uuid.uuid4())
    row = {
        "id": update_id,
        "user_id": update.user_id,
        "work_date": update.work_date.isoformat(),
        "raw_update": update.raw_update,
        "structured": structured.model_dump(),
        "created_at": utc_now(),
    }
    if STORAGE_MODE == "supabase" and supabase:
        supabase.table("daily_updates").insert(row).execute()
    else:
        LOCAL_UPDATES[update_id] = row
    return update_id


def get_update(update_id: str, user_id: str) -> dict[str, Any]:
    if STORAGE_MODE == "supabase" and supabase:
        res = supabase.table("daily_updates").select("*").eq("id", update_id).eq("user_id", user_id).single().execute()
        if not res.data:
            raise HTTPException(404, "Update not found")
        return res.data
    row = LOCAL_UPDATES.get(update_id)
    if not row or row["user_id"] != user_id:
        raise HTTPException(404, "Update not found")
    return row


def save_post(update_id: str, user_id: str, text: str) -> dict[str, Any]:
    post_id = str(uuid.uuid4())
    row = {
        "id": post_id,
        "daily_update_id": update_id,
        "user_id": user_id,
        "post_text": text,
        "status": "draft",
        "x_post_id": None,
        "created_at": utc_now(),
    }
    if STORAGE_MODE == "supabase" and supabase:
        res = supabase.table("generated_posts").insert(row).execute()
        return res.data[0]
    LOCAL_POSTS[post_id] = row
    return row


def get_post(post_id: str, user_id: str) -> dict[str, Any]:
    if STORAGE_MODE == "supabase" and supabase:
        res = supabase.table("generated_posts").select("*").eq("id", post_id).eq("user_id", user_id).single().execute()
        if not res.data:
            raise HTTPException(404, "Post not found")
        return res.data
    row = LOCAL_POSTS.get(post_id)
    if not row or row["user_id"] != user_id:
        raise HTTPException(404, "Post not found")
    return row


def mark_published(post_id: str, user_id: str, x_post_id: str) -> None:
    payload = {"status": "published", "x_post_id": x_post_id}
    if STORAGE_MODE == "supabase" and supabase:
        supabase.table("generated_posts").update(payload).eq("id", post_id).eq("user_id", user_id).execute()
    else:
        LOCAL_POSTS[post_id].update(payload)


async def publish_to_x(text: str) -> str:
    if not X_ACCESS_TOKEN:
        raise HTTPException(503, "X_ACCESS_TOKEN is not configured")
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://api.x.com/2/tweets",
            headers={"Authorization": f"Bearer {X_ACCESS_TOKEN}"},
            json={"text": text},
        )
    if response.is_error:
        detail = response.text[:1000]
        raise HTTPException(response.status_code, f"X API error: {detail}")
    data = response.json().get("data", {})
    x_post_id = data.get("id")
    if not x_post_id:
        raise HTTPException(502, "X API did not return a post id")
    return x_post_id


@app.get("/")
def root() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "openai_configured": bool(_openai),
        "supabase_configured": bool(supabase),
        "x_publish_configured": bool(X_ACCESS_TOKEN),
        "storage_mode": STORAGE_MODE,
        "model": OPENAI_MODEL,
    }


@app.post("/api/updates")
def create_update(payload: DailyUpdateIn) -> dict[str, Any]:
    try:
        structured = structure_update(payload.raw_update)
        update_id = save_update(payload, structured)
        return {
            "id": update_id,
            "work_date": payload.work_date,
            "structured": structured.model_dump(),
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Failed to save update: {exc}") from exc


@app.post("/api/posts/generate", response_model=PostResponse)
def create_post(payload: GeneratePostIn) -> PostResponse:
    update = get_update(payload.update_id, payload.user_id)
    structured = StructuredUpdate.model_validate(update["structured"])
    post_text = generate_post(
        structured,
        update["raw_update"],
        payload.tone,
        date.fromisoformat(update["work_date"]),
    )
    saved = save_post(payload.update_id, payload.user_id, post_text)
    return PostResponse(
        id=saved["id"],
        update_id=payload.update_id,
        post_text=saved["post_text"],
        character_count=len(saved["post_text"]),
        status=saved["status"],
    )


@app.post("/api/posts/publish")
async def publish_post(payload: PublishIn) -> dict[str, Any]:
    post = get_post(payload.post_id, payload.user_id)
    if post["status"] == "published":
        return {"status": "published", "x_post_id": post["x_post_id"]}
    x_post_id = await publish_to_x(post["post_text"])
    mark_published(payload.post_id, payload.user_id, x_post_id)
    return {"status": "published", "x_post_id": x_post_id}
