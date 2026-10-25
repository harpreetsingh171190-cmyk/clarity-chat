import json

from fastapi import HTTPException

from app import main
from tests.conftest import register


def parse_events(response):
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def test_chat_creates_and_persists_conversation(client, auth):
    response = client.post("/api/chat", json={"message": "Explain REST APIs"}, headers=auth)
    assert response.status_code == 200
    body = response.json()
    assert body["assistant_message"]["content"] == "Hello from the fake model"
    conversation_id = body["conversation"]["id"]

    messages = client.get(f"/api/conversations/{conversation_id}/messages", headers=auth).json()
    assert [m["role"] for m in messages] == ["user", "assistant"]

    followup = client.post("/api/chat", json={"message": "More", "conversation_id": conversation_id}, headers=auth)
    assert followup.status_code == 200
    assert len(client.get(f"/api/conversations/{conversation_id}/messages", headers=auth).json()) == 4


def test_custom_instructions_sent_as_system_message(client, auth, calls):
    client.post("/api/chat", json={"message": "hi", "system": "Answer like a pirate."}, headers=auth)
    assert calls[-1][0] == {"role": "system", "content": "Answer like a pirate."}
    assert calls[-1][-1]["content"] == "hi"


def test_failed_ai_call_saves_nothing(client, auth, monkeypatch):
    async def broken(history, model=None, temperature=None):
        raise HTTPException(status_code=503, detail="Ollama is down")

    monkeypatch.setattr(main, "ask_ollama", broken)
    response = client.post("/api/chat", json={"message": "hello"}, headers=auth)
    assert response.status_code == 503
    assert client.get("/api/conversations", headers=auth).json() == []


def test_users_cannot_read_each_others_chats(client, auth):
    conversation_id = client.post("/api/chat", json={"message": "secret"}, headers=auth).json()["conversation"]["id"]
    bob = register(client, email="bob@example.com", name="Bob")
    assert client.get(f"/api/conversations/{conversation_id}/messages", headers=bob).status_code == 404
    assert client.delete(f"/api/conversations/{conversation_id}", headers=bob).status_code == 404
    assert client.patch(f"/api/conversations/{conversation_id}", json={"title": "x"}, headers=bob).status_code == 404
    assert client.post("/api/chat", json={"message": "hi", "conversation_id": conversation_id}, headers=bob).status_code == 404
    assert client.get("/api/conversations", headers=bob).json() == []


def test_rename_move_and_delete(client, auth):
    conversation_id = client.post("/api/chat", json={"message": "first"}, headers=auth).json()["conversation"]["id"]

    renamed = client.patch(f"/api/conversations/{conversation_id}", json={"title": "  Project notes  "}, headers=auth)
    assert renamed.status_code == 200 and renamed.json()["title"] == "Project notes"

    moved = client.patch(f"/api/conversations/{conversation_id}/folder", json={"folder": "Work"}, headers=auth)
    assert moved.status_code == 200 and moved.json()["folder"] == "Work"

    assert client.delete(f"/api/conversations/{conversation_id}", headers=auth).status_code == 204
    assert client.get("/api/conversations", headers=auth).json() == []


def test_blank_message_rejected(client, auth):
    assert client.post("/api/chat", json={"message": "   "}, headers=auth).status_code == 422


def test_stream_returns_tokens_then_done_and_persists(client, auth):
    response = client.post("/api/chat/stream", json={"message": "stream please"}, headers=auth)
    assert response.status_code == 200
    events = parse_events(response)
    assert [e["type"] for e in events] == ["token", "token", "token", "done"]
    assert "".join(e["content"] for e in events if e["type"] == "token") == "Hello from stream"
    done = events[-1]
    assert done["assistant_message"]["content"] == "Hello from stream"

    messages = client.get(f"/api/conversations/{done['conversation']['id']}/messages", headers=auth).json()
    assert len(messages) == 2


def test_stream_error_is_reported_and_nothing_saved(client, auth, monkeypatch):
    async def broken(history, model=None, temperature=None):
        raise HTTPException(status_code=503, detail="Ollama is down")
        yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(main, "stream_ollama", broken)
    response = client.post("/api/chat/stream", json={"message": "hello"}, headers=auth)
    events = parse_events(response)
    assert events == [{"type": "error", "detail": "Ollama is down"}]
    assert client.get("/api/conversations", headers=auth).json() == []


def test_stream_requires_auth(client):
    assert client.post("/api/chat/stream", json={"message": "hi"}).status_code == 401
