"""Clarity Chat API v5.3: accounts, streaming chat, regenerate, edit-and-resend, duplicate, auto-titles, folders, search, pinning, import/export."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import sqlite3
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import AsyncGenerator, Callable, Generator, Literal

import bcrypt
import httpx
import jwt
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr, Field

logger = logging.getLogger("clarity")

APP_DIR = Path(__file__).resolve().parent.parent
DATABASE_PATH = os.getenv("DATABASE_PATH", str(APP_DIR / "clarity_chat.db"))
ALGORITHM = "HS256"
ACCESS_TOKEN_MINUTES = int(os.getenv("ACCESS_TOKEN_MINUTES", "1440"))
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
FRONTEND_ORIGINS = [
    x.strip()
    for x in os.getenv("FRONTEND_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
    if x.strip()
]
MAX_PASSWORD_BYTES = 72  # bcrypt limit
CONV_COLS = "id, title, folder, pinned, created_at, updated_at"

_PLACEHOLDER_SECRETS = {
    "",
    "development-only-change-this-secret-before-deploying",
    "replace-with-a-long-random-secret",
    "replace-with-a-long-random-secret-before-deploying",
}


def load_secret_key() -> str:
    """Use SECRET_KEY if it is real; otherwise generate a temporary key (sessions reset on restart)."""
    value = os.getenv("SECRET_KEY", "").strip()
    if value in _PLACEHOLDER_SECRETS or len(value) < 16:
        logger.warning("SECRET_KEY is missing or a placeholder. Using a temporary random key; "
                       "users will be signed out whenever the server restarts. Set a long random SECRET_KEY.")
        return secrets.token_urlsafe(48)
    return value


SECRET_KEY = load_secret_key()
bearer = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------- database
@contextmanager
def db() -> Generator[sqlite3.Connection, None, None]:
    Path(DATABASE_PATH).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db() -> None:
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          email TEXT NOT NULL UNIQUE,
          name TEXT NOT NULL,
          password_hash TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS conversations (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          title TEXT NOT NULL DEFAULT 'New conversation',
          folder TEXT NOT NULL DEFAULT 'General',
          pinned INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS messages (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
          role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
          content TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        """)
        columns = [row[1] for row in conn.execute("PRAGMA table_info(conversations)").fetchall()]
        if "folder" not in columns:
            conn.execute("ALTER TABLE conversations ADD COLUMN folder TEXT NOT NULL DEFAULT 'General'")
        if "pinned" not in columns:
            conn.execute("ALTER TABLE conversations ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")
        conn.executescript("""
        CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(user_id, updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, id);
        """)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Clarity Chat API", version="5.3.0",
              description="Authenticated chat API with streaming Ollama integration.", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


# ---------------------------------------------------------------- rate limiting
class RateLimiter:
    """Small in-memory sliding-window limiter (per process; use Redis for multi-instance deployments)."""

    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit = limit
        self.window = window_seconds
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def _prune(self, key: str) -> deque[float]:
        queue = self.hits[key]
        now = time.monotonic()
        while queue and now - queue[0] > self.window:
            queue.popleft()
        return queue

    def check(self, key: str) -> None:
        if len(self._prune(key)) >= self.limit:
            raise HTTPException(status_code=429, detail="Too many attempts. Please wait a few minutes and try again.")

    def record(self, key: str) -> None:
        self._prune(key).append(time.monotonic())

    def reset(self, key: str | None = None) -> None:
        if key is None:
            self.hits.clear()
        else:
            self.hits.pop(key, None)


login_limiter = RateLimiter(limit=8, window_seconds=300)
register_limiter = RateLimiter(limit=10, window_seconds=3600)


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


# ---------------------------------------------------------------- schemas
class RegisterInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    email: EmailStr
    password: str = Field(min_length=8, max_length=MAX_PASSWORD_BYTES)


class LoginInput(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=MAX_PASSWORD_BYTES)


class GenerationOptions(BaseModel):
    model: str | None = Field(default=None, min_length=1, max_length=120)
    system: str | None = Field(default=None, max_length=2000)
    temperature: float | None = Field(default=None, ge=0, le=2)


class ChatInput(GenerationOptions):
    message: str = Field(min_length=1, max_length=12000)
    conversation_id: int | None = None


class EditInput(GenerationOptions):
    message: str = Field(min_length=1, max_length=12000)


class TitleInput(BaseModel):
    model: str | None = Field(default=None, min_length=1, max_length=120)


class FolderRename(BaseModel):
    old: str = Field(min_length=1, max_length=80)
    new: str = Field(min_length=1, max_length=80)


class ConversationUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    pinned: bool | None = None


class FolderInput(BaseModel):
    folder: str = Field(min_length=1, max_length=80)


class UserOut(BaseModel):
    id: int
    name: str
    email: EmailStr


class AuthOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class ConversationOut(BaseModel):
    id: int
    title: str
    folder: str = "General"
    pinned: bool = False
    created_at: str
    updated_at: str


class SearchResult(ConversationOut):
    snippet: str | None = None


class MessageOut(BaseModel):
    id: int
    role: str
    content: str
    created_at: str


class ChatOut(BaseModel):
    conversation: ConversationOut
    user_message: MessageOut
    assistant_message: MessageOut
    model: str


class ImportMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=50000)
    created_at: str | None = None


class ImportConversation(BaseModel):
    title: str = Field(default="Imported conversation", min_length=1, max_length=120)
    folder: str = Field(default="General", min_length=1, max_length=80)
    pinned: bool = False
    messages: list[ImportMessage] = Field(max_length=2000)


class ImportInput(BaseModel):
    conversations: list[ImportConversation] = Field(max_length=200)


# ---------------------------------------------------------------- auth helpers
def hash_password(password: str) -> str:
    raw = password.encode("utf-8")
    if len(raw) > MAX_PASSWORD_BYTES:
        raise HTTPException(status_code=422, detail=f"Password must be {MAX_PASSWORD_BYTES} bytes or fewer.")
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    raw = password.encode("utf-8")
    if len(raw) > MAX_PASSWORD_BYTES:
        return False
    try:
        return bcrypt.checkpw(raw, hashed.encode("utf-8"))
    except ValueError:
        return False


def create_token(user_id: int) -> str:
    expiry = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_MINUTES)
    return jwt.encode({"sub": str(user_id), "exp": expiry}, SECRET_KEY, algorithm=ALGORITHM)


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Please sign in to continue.", headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = int(payload.get("sub", ""))
    except (jwt.PyJWTError, ValueError, TypeError):
        raise HTTPException(status_code=401, detail="Session expired or invalid. Please sign in again.")
    with db() as conn:
        user = conn.execute("SELECT id, name, email FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        raise HTTPException(status_code=401, detail="User account no longer exists.")
    return dict(user)


def owned_conversation(conn: sqlite3.Connection, conversation_id: int, user_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM conversations WHERE id = ? AND user_id = ?", (conversation_id, user_id)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return row


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_timestamp(value: str | None) -> str:
    """Normalise an imported ISO timestamp; fall back to 'now' when missing or invalid."""
    if value:
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.isoformat()
        except ValueError:
            pass
    return now_iso()


# ---------------------------------------------------------------- routes: health + auth
@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "service": "clarity-chat-api"}


@app.post("/api/auth/register", response_model=AuthOut, status_code=201)
def register(payload: RegisterInput, request: Request) -> dict:
    ip = client_ip(request)
    register_limiter.check(ip)
    email = payload.email.lower().strip()
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Name cannot be empty.")
    password_hash = hash_password(payload.password)
    try:
        with db() as conn:
            if conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone():
                raise HTTPException(status_code=409, detail="An account with this email already exists.")
            cursor = conn.execute(
                "INSERT INTO users(email, name, password_hash, created_at) VALUES (?, ?, ?, ?)",
                (email, name, password_hash, now_iso()),
            )
            user = {"id": cursor.lastrowid, "name": name, "email": email}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="An account with this email already exists.")
    register_limiter.record(ip)
    return {"access_token": create_token(user["id"]), "user": user}


@app.post("/api/auth/login", response_model=AuthOut)
def login(payload: LoginInput, request: Request) -> dict:
    email = payload.email.lower().strip()
    key = f"{client_ip(request)}:{email}"
    login_limiter.check(key)
    with db() as conn:
        row = conn.execute("SELECT id, name, email, password_hash FROM users WHERE email = ?", (email,)).fetchone()
    if not row or not verify_password(payload.password, row["password_hash"]):
        login_limiter.record(key)
        raise HTTPException(status_code=401, detail="Incorrect email or password.")
    login_limiter.reset(key)
    user = {"id": row["id"], "name": row["name"], "email": row["email"]}
    return {"access_token": create_token(user["id"]), "user": user}


@app.get("/api/auth/me", response_model=UserOut)
def me(user: dict = Depends(current_user)) -> dict:
    return user


@app.post("/api/auth/password")
def change_password(payload: PasswordChange, user: dict = Depends(current_user)) -> dict:
    """Change the password. A wrong current password returns 400 (not 401, which would sign the user out)."""
    key = f"password:{user['id']}"
    login_limiter.check(key)
    with db() as conn:
        row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user["id"],)).fetchone()
    if not row or not verify_password(payload.current_password, row["password_hash"]):
        login_limiter.record(key)
        raise HTTPException(status_code=400, detail="Current password is incorrect.")
    if verify_password(payload.new_password, row["password_hash"]):
        raise HTTPException(status_code=400, detail="Choose a new password that is different from the current one.")
    new_hash = hash_password(payload.new_password)
    with db() as conn:
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_hash, user["id"]))
    login_limiter.reset(key)
    return {"status": "ok"}


@app.delete("/api/auth/me", status_code=204)
def delete_account(user: dict = Depends(current_user)) -> None:
    """Permanently delete the account and all of its conversations (cascade)."""
    with db() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user["id"],))


# ---------------------------------------------------------------- routes: conversations
@app.get("/api/conversations", response_model=list[ConversationOut])
def list_conversations(user: dict = Depends(current_user)) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            f"SELECT {CONV_COLS} FROM conversations WHERE user_id = ? ORDER BY pinned DESC, updated_at DESC",
            (user["id"],),
        ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/conversations/{conversation_id}/messages", response_model=list[MessageOut])
def list_messages(conversation_id: int, user: dict = Depends(current_user)) -> list[dict]:
    with db() as conn:
        owned_conversation(conn, conversation_id, user["id"])
        rows = conn.execute(
            "SELECT id, role, content, created_at FROM messages WHERE conversation_id = ? ORDER BY id", (conversation_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def _update_conversation(conversation_id: int, user_id: int, fields: dict) -> dict:
    allowed = {"title", "folder", "pinned"}  # column names are never user-controlled
    if not fields or not set(fields) <= allowed:
        raise ValueError("Unsupported columns")
    assignments = ", ".join(f"{column} = ?" for column in fields)
    with db() as conn:
        owned_conversation(conn, conversation_id, user_id)
        conn.execute(f"UPDATE conversations SET {assignments} WHERE id = ?", (*fields.values(), conversation_id))
        row = conn.execute(f"SELECT {CONV_COLS} FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
    return dict(row)


@app.patch("/api/conversations/{conversation_id}", response_model=ConversationOut)
def update_conversation(conversation_id: int, payload: ConversationUpdate, user: dict = Depends(current_user)) -> dict:
    """Rename and/or pin or unpin a conversation."""
    fields: dict = {}
    if payload.title is not None:
        title = payload.title.strip()
        if not title:
            raise HTTPException(status_code=422, detail="Title cannot be empty.")
        fields["title"] = title
    if payload.pinned is not None:
        fields["pinned"] = int(payload.pinned)
    if not fields:
        raise HTTPException(status_code=422, detail="Nothing to update.")
    return _update_conversation(conversation_id, user["id"], fields)


@app.patch("/api/conversations/{conversation_id}/folder", response_model=ConversationOut)
def move_conversation(conversation_id: int, payload: FolderInput, user: dict = Depends(current_user)) -> dict:
    folder = payload.folder.strip()
    if not folder:
        raise HTTPException(status_code=422, detail="Folder name cannot be empty.")
    return _update_conversation(conversation_id, user["id"], {"folder": folder})


def clean_title(raw: str) -> str:
    """Turn a model reply into a short single-line title (empty string if nothing usable)."""
    line = next((part for part in raw.strip().splitlines() if part.strip()), "")
    junk = " \"'\u201c\u201d\u2018\u2019*#`"
    line = re.sub(r"^(chat\s+)?title\s*:[\s*]*", "", line.strip(junk), flags=re.IGNORECASE)
    line = line.strip(junk).rstrip(".:")
    return " ".join(line.split())[:60].strip()


@app.post("/api/conversations/{conversation_id}/title", response_model=ConversationOut)
async def generate_title(conversation_id: int, payload: TitleInput, user: dict = Depends(current_user)) -> dict:
    """Ask the model for a short title based on the first question and answer, and save it."""
    with db() as conn:
        owned_conversation(conn, conversation_id, user["id"])
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id LIMIT 2", (conversation_id,)
        ).fetchall()
    if not rows or rows[0]["role"] != "user":
        raise HTTPException(status_code=409, detail="There is nothing to title yet.")
    excerpt = f"Question:\n{rows[0]['content'][:600]}"
    if len(rows) > 1:
        excerpt += f"\n\nAnswer:\n{rows[1]['content'][:600]}"
    prompt = [
        {"role": "system", "content": "You write short titles for chats. Reply with only a title of at most six words, "
                                      "with no quotation marks and no ending punctuation."},
        {"role": "user", "content": excerpt},
    ]
    title = clean_title(await ask_ollama(prompt, payload.model, temperature=0.2))
    if not title:
        raise HTTPException(status_code=502, detail="The AI model did not return a usable title.")
    return _update_conversation(conversation_id, user["id"], {"title": title})


@app.patch("/api/folders")
def rename_folder(payload: FolderRename, user: dict = Depends(current_user)) -> dict:
    """Rename a folder by moving every one of your conversations in it to the new name."""
    old, new = payload.old.strip(), payload.new.strip()
    if not old or not new:
        raise HTTPException(status_code=422, detail="Folder names cannot be empty.")
    with db() as conn:
        moved = conn.execute(
            "UPDATE conversations SET folder = ? WHERE user_id = ? AND folder = ?", (new, user["id"], old)
        ).rowcount
    if moved == 0:
        raise HTTPException(status_code=404, detail="Folder not found.")
    return {"renamed": moved, "folder": new}


@app.post("/api/conversations/{conversation_id}/duplicate", response_model=ConversationOut, status_code=201)
def duplicate_conversation(conversation_id: int, user: dict = Depends(current_user)) -> dict:
    """Copy a conversation and all of its messages into a new, unpinned conversation."""
    stamp = now_iso()
    with db() as conn:
        source = owned_conversation(conn, conversation_id, user["id"])
        title = f"{source['title']} (copy)"[:120]
        cursor = conn.execute(
            "INSERT INTO conversations(user_id, title, folder, pinned, created_at, updated_at) VALUES (?, ?, ?, 0, ?, ?)",
            (user["id"], title, source["folder"], stamp, stamp),
        )
        new_id = cursor.lastrowid
        conn.execute(
            "INSERT INTO messages(conversation_id, role, content, created_at) "
            "SELECT ?, role, content, created_at FROM messages WHERE conversation_id = ? ORDER BY id",
            (new_id, conversation_id),
        )
        row = conn.execute(f"SELECT {CONV_COLS} FROM conversations WHERE id = ?", (new_id,)).fetchone()
    return dict(row)


@app.delete("/api/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: int, user: dict = Depends(current_user)) -> None:
    with db() as conn:
        owned_conversation(conn, conversation_id, user["id"])
        conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))


def make_snippet(text: str, query: str, radius: int = 40) -> str:
    flat = " ".join(text.split())
    index = flat.lower().find(query.lower())
    if index < 0:
        return flat[: radius * 2]
    start = max(0, index - radius)
    end = min(len(flat), index + len(query) + radius)
    return ("…" if start > 0 else "") + flat[start:end] + ("…" if end < len(flat) else "")


@app.get("/api/search", response_model=list[SearchResult])
def search_conversations(q: str = Query(min_length=1, max_length=100), user: dict = Depends(current_user)) -> list[dict]:
    """Search the user's conversation titles and message text."""
    query = q.strip()
    if not query:
        raise HTTPException(status_code=422, detail="Search text cannot be empty.")
    like = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    with db() as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.title, c.folder, c.pinned, c.created_at, c.updated_at,
                   (SELECT m.content FROM messages m
                     WHERE m.conversation_id = c.id AND m.content LIKE ? ESCAPE '\\'
                     ORDER BY m.id LIMIT 1) AS matched
              FROM conversations c
             WHERE c.user_id = ?
               AND (c.title LIKE ? ESCAPE '\\' OR EXISTS (
                     SELECT 1 FROM messages m WHERE m.conversation_id = c.id AND m.content LIKE ? ESCAPE '\\'))
             ORDER BY c.pinned DESC, c.updated_at DESC
             LIMIT 50
            """,
            (like, user["id"], like, like),
        ).fetchall()
    results = []
    for row in rows:
        item = {k: row[k] for k in ("id", "title", "folder", "pinned", "created_at", "updated_at")}
        item["snippet"] = make_snippet(row["matched"], query) if row["matched"] else None
        results.append(item)
    return results


# ---------------------------------------------------------------- routes: data export / import
@app.get("/api/export")
def export_data(user: dict = Depends(current_user)) -> dict:
    """Everything the user owns, as a portable JSON document."""
    conversations = []
    with db() as conn:
        rows = conn.execute(f"SELECT {CONV_COLS} FROM conversations WHERE user_id = ? ORDER BY created_at, id", (user["id"],)).fetchall()
        for row in rows:
            messages = conn.execute(
                "SELECT role, content, created_at FROM messages WHERE conversation_id = ? ORDER BY id", (row["id"],)
            ).fetchall()
            conversations.append({
                "title": row["title"], "folder": row["folder"], "pinned": bool(row["pinned"]),
                "created_at": row["created_at"], "updated_at": row["updated_at"],
                "messages": [dict(m) for m in messages],
            })
    return {"app": "clarity-chat", "version": 5, "exported_at": now_iso(),
            "user": {"name": user["name"], "email": user["email"]}, "conversations": conversations}


@app.post("/api/import", status_code=201)
def import_data(payload: ImportInput, user: dict = Depends(current_user)) -> dict:
    """Import conversations from an export file. Imported chats are added; nothing is overwritten."""
    imported = 0
    with db() as conn:
        for item in payload.conversations:
            if not item.messages:
                continue
            stamps = [clean_timestamp(m.created_at) for m in item.messages]
            cursor = conn.execute(
                "INSERT INTO conversations(user_id, title, folder, pinned, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (user["id"], item.title.strip() or "Imported conversation", item.folder.strip() or "General",
                 int(item.pinned), stamps[0], stamps[-1]),
            )
            conn.executemany(
                "INSERT INTO messages(conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                [(cursor.lastrowid, m.role, m.content, ts) for m, ts in zip(item.messages, stamps)],
            )
            imported += 1
    return {"imported": imported}


# ---------------------------------------------------------------- Ollama
def ollama_error(exc: Exception, model: str) -> HTTPException:
    if isinstance(exc, httpx.ConnectError):
        return HTTPException(503, f"Cannot connect to Ollama at {OLLAMA_BASE_URL}. Start Ollama and pull model '{model}'.")
    if isinstance(exc, httpx.TimeoutException):
        return HTTPException(504, "The AI model took too long to respond. Try a shorter message.")
    if isinstance(exc, httpx.HTTPStatusError):
        return HTTPException(502, f"Ollama returned HTTP {exc.response.status_code}. Check that model '{model}' is installed.")
    return HTTPException(502, "Unexpected response from Ollama.")


def ollama_body(history: list[dict], model: str, stream: bool, temperature: float | None) -> dict:
    body: dict = {"model": model, "messages": history, "stream": stream}
    if temperature is not None:
        body["options"] = {"temperature": temperature}
    return body


async def ask_ollama(history: list[dict], model: str | None = None, temperature: float | None = None) -> str:
    selected = model or OLLAMA_MODEL
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=5.0)) as client:
            response = await client.post(f"{OLLAMA_BASE_URL}/api/chat", json=ollama_body(history, selected, False, temperature))
            response.raise_for_status()
            content = response.json().get("message", {}).get("content", "").strip()
    except (httpx.HTTPError, ValueError) as exc:
        raise ollama_error(exc, selected)
    if not content:
        raise HTTPException(status_code=502, detail="The AI model returned an empty response.")
    return content


async def stream_ollama(history: list[dict], model: str | None = None,
                        temperature: float | None = None) -> AsyncGenerator[str, None]:
    """Yield text chunks from Ollama as they are generated."""
    selected = model or OLLAMA_MODEL
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=5.0)) as client:
            async with client.stream("POST", f"{OLLAMA_BASE_URL}/api/chat",
                                     json=ollama_body(history, selected, True, temperature)) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                    except ValueError:
                        continue
                    if data.get("error"):
                        raise HTTPException(status_code=502, detail=str(data["error"])[:300])
                    chunk = data.get("message", {}).get("content", "")
                    if chunk:
                        yield chunk
                    if data.get("done"):
                        break
    except httpx.HTTPError as exc:
        raise ollama_error(exc, selected)


# ---------------------------------------------------------------- chat
def with_system(history: list[dict], system: str | None) -> list[dict]:
    if system and system.strip():
        return [{"role": "system", "content": system.strip()}, *history]
    return history


def prepare_chat(payload: ChatInput, user: dict) -> tuple[str, int | None, list[dict]]:
    """Validate input and build the model history WITHOUT saving anything yet."""
    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=422, detail="Message cannot be empty.")
    history: list[dict] = []
    if payload.conversation_id is not None:
        with db() as conn:
            owned_conversation(conn, payload.conversation_id, user["id"])
            rows = conn.execute(
                "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT 20",
                (payload.conversation_id,),
            ).fetchall()
        history = [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
    history.append({"role": "user", "content": message})
    return message, payload.conversation_id, with_system(history, payload.system)


def save_exchange(user_id: int, conversation_id: int | None, message: str, answer: str) -> dict:
    """Persist the user message and the answer together, so a failed AI call leaves no orphan messages."""
    created = now_iso()
    with db() as conn:
        if conversation_id is None:
            clean = " ".join(message.split())
            title = clean[:55] + ("…" if len(clean) > 55 else "")
            cursor = conn.execute(
                "INSERT INTO conversations(user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (user_id, title or "New conversation", created, created),
            )
            conversation_id = cursor.lastrowid
        else:
            owned_conversation(conn, conversation_id, user_id)
        cursor = conn.execute("INSERT INTO messages(conversation_id, role, content, created_at) VALUES (?, 'user', ?, ?)",
                              (conversation_id, message, created))
        user_message = {"id": cursor.lastrowid, "role": "user", "content": message, "created_at": created}
        answered = now_iso()
        cursor = conn.execute("INSERT INTO messages(conversation_id, role, content, created_at) VALUES (?, 'assistant', ?, ?)",
                              (conversation_id, answer, answered))
        assistant_message = {"id": cursor.lastrowid, "role": "assistant", "content": answer, "created_at": answered}
        conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (answered, conversation_id))
        row = conn.execute(f"SELECT {CONV_COLS} FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
    return {"conversation": dict(row), "user_message": user_message, "assistant_message": assistant_message}


def ndjson(event: dict) -> str:
    return json.dumps(event, ensure_ascii=False) + "\n"


def stream_response(history: list[dict], model: str | None, temperature: float | None,
                    finalize: Callable[[str], dict],
                    on_cancel: Callable[[str], object] | None = None) -> StreamingResponse:
    """Stream NDJSON events (token, done, error). `finalize` saves the answer; `on_cancel` saves a partial one."""

    async def events() -> AsyncGenerator[str, None]:
        parts: list[str] = []
        saved = False
        try:
            async for chunk in stream_ollama(history, model, temperature=temperature):
                parts.append(chunk)
                yield ndjson({"type": "token", "content": chunk})
            answer = "".join(parts).strip()
            if not answer:
                raise HTTPException(status_code=502, detail="The AI model returned an empty response.")
            result = finalize(answer)
            saved = True
            yield ndjson({"type": "done", **result, "model": model or OLLAMA_MODEL})
        except HTTPException as exc:
            yield ndjson({"type": "error", "detail": exc.detail})
        except (asyncio.CancelledError, GeneratorExit):
            partial = "".join(parts).strip()
            if partial and not saved and on_cancel is not None:
                try:
                    on_cancel(partial)
                except Exception:  # noqa: BLE001 - never mask cancellation
                    logger.exception("Could not save partial response")
            raise

    return StreamingResponse(events(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/chat", response_model=ChatOut)
async def chat(payload: ChatInput, user: dict = Depends(current_user)) -> dict:
    message, conversation_id, history = prepare_chat(payload, user)
    answer = await ask_ollama(history, payload.model, temperature=payload.temperature)
    result = save_exchange(user["id"], conversation_id, message, answer)
    return {**result, "model": payload.model or OLLAMA_MODEL}


@app.post("/api/chat/stream")
async def chat_stream(payload: ChatInput, user: dict = Depends(current_user)) -> StreamingResponse:
    """Stream the answer as newline-delimited JSON events. Pressing Stop keeps the partial answer."""
    message, conversation_id, history = prepare_chat(payload, user)

    def save(answer: str) -> dict:
        return save_exchange(user["id"], conversation_id, message, answer)

    return stream_response(history, payload.model, payload.temperature, finalize=save, on_cancel=save)


def prepare_regenerate(conversation_id: int, payload: GenerationOptions, user: dict) -> tuple[list[dict], int]:
    with db() as conn:
        owned_conversation(conn, conversation_id, user["id"])
        rows = conn.execute(
            "SELECT id, role, content FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT 21",
            (conversation_id,),
        ).fetchall()
    if not rows or rows[0]["role"] != "assistant":
        raise HTTPException(status_code=409, detail="There is no answer to regenerate.")
    earlier = [{"role": r["role"], "content": r["content"]} for r in reversed(rows[1:])]
    if not earlier or earlier[-1]["role"] != "user":
        raise HTTPException(status_code=409, detail="There is no question to answer again.")
    return with_system(earlier, payload.system), rows[0]["id"]


def replace_answer(user_id: int, conversation_id: int, assistant_id: int, answer: str) -> dict:
    stamp = now_iso()
    with db() as conn:
        owned_conversation(conn, conversation_id, user_id)
        cursor = conn.execute(
            "UPDATE messages SET content = ?, created_at = ? WHERE id = ? AND conversation_id = ? AND role = 'assistant'",
            (answer, stamp, assistant_id, conversation_id),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=409, detail="The conversation changed. Please reload it.")
        conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (stamp, conversation_id))
        row = conn.execute(f"SELECT {CONV_COLS} FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
    return {"conversation": dict(row),
            "assistant_message": {"id": assistant_id, "role": "assistant", "content": answer, "created_at": stamp}}


@app.post("/api/conversations/{conversation_id}/regenerate")
async def regenerate(conversation_id: int, payload: GenerationOptions, user: dict = Depends(current_user)) -> StreamingResponse:
    """Stream a fresh answer to the last question and replace the previous answer. Stopping keeps the old answer."""
    history, assistant_id = prepare_regenerate(conversation_id, payload, user)

    def save(answer: str) -> dict:
        return replace_answer(user["id"], conversation_id, assistant_id, answer)

    return stream_response(history, payload.model, payload.temperature, finalize=save)


def prepare_edit(conversation_id: int, message_id: int, payload: EditInput, user: dict) -> tuple[list[dict], str]:
    """Build the history for an edited question: everything before it plus the new text. Nothing is saved yet."""
    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=422, detail="Message cannot be empty.")
    with db() as conn:
        owned_conversation(conn, conversation_id, user["id"])
        target = conn.execute(
            "SELECT id, role FROM messages WHERE id = ? AND conversation_id = ?", (message_id, conversation_id)
        ).fetchone()
        if target is None:
            raise HTTPException(status_code=404, detail="Message not found.")
        if target["role"] != "user":
            raise HTTPException(status_code=409, detail="Only your own messages can be edited.")
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE conversation_id = ? AND id < ? ORDER BY id DESC LIMIT 20",
            (conversation_id, message_id),
        ).fetchall()
    history = [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
    history.append({"role": "user", "content": message})
    return with_system(history, payload.system), message


def apply_edit(user_id: int, conversation_id: int, message_id: int, message: str, answer: str) -> dict:
    """Replace the question, drop everything after it, and store the new answer, all in one transaction."""
    stamp = now_iso()
    with db() as conn:
        owned_conversation(conn, conversation_id, user_id)
        target = conn.execute(
            "SELECT created_at FROM messages WHERE id = ? AND conversation_id = ? AND role = 'user'",
            (message_id, conversation_id),
        ).fetchone()
        if target is None:
            raise HTTPException(status_code=409, detail="The conversation changed. Please reload it.")
        conn.execute("UPDATE messages SET content = ? WHERE id = ?", (message, message_id))
        conn.execute("DELETE FROM messages WHERE conversation_id = ? AND id > ?", (conversation_id, message_id))
        cursor = conn.execute(
            "INSERT INTO messages(conversation_id, role, content, created_at) VALUES (?, 'assistant', ?, ?)",
            (conversation_id, answer, stamp),
        )
        assistant_id = cursor.lastrowid
        conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (stamp, conversation_id))
        row = conn.execute(f"SELECT {CONV_COLS} FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
    return {"conversation": dict(row),
            "user_message": {"id": message_id, "role": "user", "content": message, "created_at": target["created_at"]},
            "assistant_message": {"id": assistant_id, "role": "assistant", "content": answer, "created_at": stamp}}


@app.post("/api/conversations/{conversation_id}/messages/{message_id}/edit")
async def edit_message(conversation_id: int, message_id: int, payload: EditInput,
                       user: dict = Depends(current_user)) -> StreamingResponse:
    """Edit one of your questions and stream a new answer. Later messages are removed only once the new answer completes."""
    history, message = prepare_edit(conversation_id, message_id, payload, user)

    def save(answer: str) -> dict:
        return apply_edit(user["id"], conversation_id, message_id, message, answer)

    return stream_response(history, payload.model, payload.temperature, finalize=save)


@app.get("/api/models")
async def list_models(user: dict = Depends(current_user)) -> dict:
    """Return installed Ollama model names, with the configured model as a safe fallback."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
            response.raise_for_status()
            models = [item.get("name") for item in response.json().get("models", []) if item.get("name")]
            return {"models": models or [OLLAMA_MODEL], "default": OLLAMA_MODEL}
    except (httpx.HTTPError, ValueError):
        return {"models": [OLLAMA_MODEL], "default": OLLAMA_MODEL,
                "warning": "Ollama model list is unavailable; showing configured default."}
