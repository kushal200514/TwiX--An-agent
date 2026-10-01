# Daily X Post Agent — MVP

A small personal AI agent that turns a messy daily work log into an X-ready post and can publish it to your X account.

## Flow

`raw daily update -> fact extraction -> post generation -> approval -> X API -> publish history`

## Stack

- Python + FastAPI
- OpenAI Responses API
- Supabase Postgres
- X API v2
- Small browser UI served from FastAPI

## 1. Setup

```powershell
cd daily-x-agent
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Fill `.env` with your OpenAI and Supabase values.

For X publishing, add an X user access token with write permission to `X_ACCESS_TOKEN`.

## 2. Supabase

Open the Supabase SQL editor and run `schema.sql`.

For this first single-user prototype, the API uses `user_id=default`. Authentication and per-user encrypted token storage should be added before turning it into a public multi-user product.

## 3. Run

```powershell
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000

## 4. API

- `GET /health`
- `POST /api/updates` — save + structure raw work notes
- `POST /api/posts/generate` — generate an X draft
- `POST /api/posts/publish` — publish a saved draft to X

## 5. Test without keys

Set `STORAGE_MODE=local` and leave the AI/X keys empty. The app will use a lightweight local fallback so you can test the UI and API flow.

## Important next upgrade

Replace the single-user `X_ACCESS_TOKEN` env variable with an OAuth 2.0 PKCE login flow and encrypted per-user token storage. Then add a scheduler/reminder and a long-term writing-style memory.
