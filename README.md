# Clarity Chat — Full-Stack React AI Workspace

A full-stack chat app: **React + Vite** frontend, **FastAPI + SQLite** backend, JWT accounts, and **Ollama** for local AI. Replies stream in word by word.

## Features

**New in v5.3**
- **Auto-generated titles**: after the first answer the model writes a short title for the chat (switch it off in Preferences)
- **Rename folders**: pick a folder in the sidebar and tap the pencil to rename it for every chat inside

**New in v5.2**
- **Duplicate a conversation** (copies every message into a new chat you can take in a different direction)
- **Saved prompts**: save the message you are typing and reuse it later (stored in this browser, up to 20)
- Press **Up arrow** in an empty message box to edit your last message; a character counter appears as you near the 12,000 limit

**New in v5.1**
- **Edit and resend**: change any earlier question and get a fresh answer (later messages are replaced only after the new answer finishes, so stopping or a failure keeps the chat intact)
- **Attach text and code files** (up to 5 files, 100 KB each) so the model can read them; they are added to your message as labelled code blocks

**New in v5**
- **Regenerate** the last answer (the old answer is kept if you stop or it fails)
- **Pin** conversations to the top of the list
- **Full-text search** across titles *and* message text, with match snippets
- **Creativity (temperature)** slider next to custom instructions
- **Change password** from Preferences
- **Back up and restore**: export all data to JSON and import it again (also accepts single-chat JSON exports)
- **Copy button on every code block**
- **Voice input** (browser speech recognition) and **Read aloud** (browser speech synthesis), shown only where the browser supports them
- Auto-growing message box, `Esc` closes Preferences

**Core**
- Streaming replies with a **Stop** button (partial answers are kept)
- Accounts: register, sign in, rate-limited login, auto sign-out on expired sessions, delete account
- Saved conversations: rename, folders, delete with confirmation
- Ollama model picker, custom instructions sent with every message
- Markdown rendering with syntax-highlighted code, export a chat to Markdown or JSON
- Dark/light theme (remembered), `Ctrl/⌘ + K` for a new chat, installable PWA
- Failed AI calls never leave half-saved messages

## Architecture

```text
Browser (React + Vite)  --Bearer JWT / NDJSON stream-->  FastAPI  -- SQLite
                                                           |
                                                           v
                                                     Ollama (local model)
```

## Quick start (local)

Requirements: Node.js 20+, Python 3.11+, [Ollama](https://ollama.com/).

```bash
ollama pull qwen2.5:3b          # once
```

**Backend** (terminal 1):

```bash
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"   # PowerShell: $env:SECRET_KEY = "<long random value>"
uvicorn app.main:app --reload --port 8000
```

If `SECRET_KEY` is missing the server starts with a temporary random key (and logs a warning); everyone is signed out whenever it restarts.

**Frontend** (terminal 2, project root):

```bash
npm install
npm run dev
```

Open http://localhost:5173. API docs: http://127.0.0.1:8000/docs.

## Docker Compose

```bash
cp .env.example .env             # then set SECRET_KEY
docker compose up --build -d
docker compose exec ollama ollama pull qwen2.5:3b
```

Open http://localhost:8080. The API and Ollama ports are bound to localhost only.

## Tests and quality checks

```bash
npm run lint && npm test && npm run build
cd backend && pytest
```

Backend tests cover auth, rate limiting, password change, account deletion, ownership isolation, streaming, regenerate, edit-and-resend, duplicating, auto-titles, folder renaming, pinning, search, import/export, and failed-AI handling (Ollama is mocked, so no model is needed).

## Environment variables

| Variable | Where | Purpose |
|---|---|---|
| `SECRET_KEY` | backend | JWT signing key (required in production) |
| `DATABASE_PATH` | backend | SQLite file location |
| `OLLAMA_BASE_URL`, `OLLAMA_MODEL` | backend | Ollama address and default model |
| `FRONTEND_ORIGINS` | backend | Comma-separated allowed CORS origins |
| `ACCESS_TOKEN_MINUTES` | backend | Session length (default 1440) |
| `VITE_API_URL` | frontend | API base URL when not using the dev proxy / nginx |

## API

| Method | Endpoint | Purpose | Auth |
|---|---|---|---|
| GET | `/api/health` | Health check | No |
| POST | `/api/auth/register` | Create account (rate limited) | No |
| POST | `/api/auth/login` | Sign in (rate limited) | No |
| GET / DELETE | `/api/auth/me` | Current user / delete account | Yes |
| POST | `/api/auth/password` | Change password `{current_password, new_password}` | Yes |
| GET | `/api/models` | Installed Ollama models | Yes |
| GET | `/api/conversations` | List chats | Yes |
| GET | `/api/conversations/{id}/messages` | Read a chat | Yes |
| GET | `/api/search?q=` | Search titles and message text | Yes |
| PATCH | `/api/conversations/{id}` | Rename `{title}` and/or pin `{pinned}` | Yes |
| PATCH | `/api/conversations/{id}/folder` | Move `{folder}` | Yes |
| POST | `/api/conversations/{id}/title` | Ask the model for a short title `{model?}` and save it | Yes |
| PATCH | `/api/folders` | Rename a folder `{old, new}` | Yes |
| POST | `/api/conversations/{id}/duplicate` | Copy a chat and its messages into a new chat | Yes |
| DELETE | `/api/conversations/{id}` | Delete a chat | Yes |
| POST | `/api/chat` | Send a message, get the full reply | Yes |
| POST | `/api/chat/stream` | Send a message, stream NDJSON events (`token`, `done`, `error`) | Yes |
| POST | `/api/conversations/{id}/regenerate` | Stream a new answer to the last question | Yes |
| POST | `/api/conversations/{id}/messages/{message_id}/edit` | Edit one of your questions `{message}` and stream a new answer; later messages are replaced | Yes |
| GET | `/api/export` | Export all of your data as JSON | Yes |
| POST | `/api/import` | Import conversations from an export | Yes |

## Security notes

Good for a portfolio or private network. Before exposing it publicly: use HTTPS, prefer HttpOnly cookies over `localStorage` tokens, add email verification and password reset, use a shared rate limiter (the built-in one is per-process), restrict CORS to your exact domain, and never expose Ollama publicly.

## Roadmap

Image and PDF attachments, PostgreSQL + migrations, OAuth, TypeScript migration, end-to-end tests.

## License

MIT — see [LICENSE](LICENSE).
