import json

from fastapi import HTTPException

from app import main
from tests.conftest import register


def parse_events(response):
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def make_chat(client, auth, text="first question"):
    return client.post("/api/chat", json={"message": text}, headers=auth).json()


# ---------------------------------------------------------------- pinning + rename
def test_pin_moves_conversation_to_top(client, auth):
    first = make_chat(client, auth, "older chat")["conversation"]["id"]
    make_chat(client, auth, "newer chat")
    assert client.get("/api/conversations", headers=auth).json()[0]["title"] == "newer chat"

    pinned = client.patch(f"/api/conversations/{first}", json={"pinned": True}, headers=auth)
    assert pinned.status_code == 200 and pinned.json()["pinned"] is True
    assert client.get("/api/conversations", headers=auth).json()[0]["id"] == first

    client.patch(f"/api/conversations/{first}", json={"pinned": False}, headers=auth)
    assert client.get("/api/conversations", headers=auth).json()[0]["title"] == "newer chat"


def test_update_requires_something(client, auth):
    cid = make_chat(client, auth)["conversation"]["id"]
    assert client.patch(f"/api/conversations/{cid}", json={}, headers=auth).status_code == 422


# ---------------------------------------------------------------- temperature
def test_temperature_is_forwarded_and_validated(client, auth, options):
    client.post("/api/chat", json={"message": "hi", "temperature": 0.3}, headers=auth)
    assert options[-1]["temperature"] == 0.3
    assert client.post("/api/chat", json={"message": "hi", "temperature": 5}, headers=auth).status_code == 422


# ---------------------------------------------------------------- search
def test_search_finds_titles_and_message_text_with_snippet(client, auth):
    make_chat(client, auth, "Plan my garden layout")
    other = make_chat(client, auth, "Something unrelated")["conversation"]["id"]
    client.post("/api/chat", json={"message": "Remember the tomato seeds", "conversation_id": other}, headers=auth)

    by_title = client.get("/api/search", params={"q": "garden"}, headers=auth).json()
    assert [r["title"] for r in by_title] == ["Plan my garden layout"]

    by_text = client.get("/api/search", params={"q": "TOMATO"}, headers=auth).json()
    assert len(by_text) == 1 and by_text[0]["id"] == other
    assert "tomato" in by_text[0]["snippet"].lower()


def test_search_treats_wildcards_literally_and_isolates_users(client, auth):
    make_chat(client, auth, "100% sure about this")
    make_chat(client, auth, "no percent here")
    assert len(client.get("/api/search", params={"q": "100%"}, headers=auth).json()) == 1
    assert client.get("/api/search", params={"q": "%"}, headers=auth).json()[0]["title"] == "100% sure about this"

    bob = register(client, email="bob@example.com", name="Bob")
    assert client.get("/api/search", params={"q": "sure"}, headers=bob).json() == []
    assert client.get("/api/search", params={"q": ""}, headers=auth).status_code == 422
    assert client.get("/api/search", params={"q": "x"}).status_code == 401


# ---------------------------------------------------------------- regenerate
def test_regenerate_replaces_last_answer(client, auth, monkeypatch, calls):
    chat = make_chat(client, auth, "Tell me a joke")
    cid, old_id = chat["conversation"]["id"], chat["assistant_message"]["id"]

    async def new_answer(history, model=None, temperature=None):
        calls.append(history)
        yield "A brand "
        yield "new answer"

    monkeypatch.setattr(main, "stream_ollama", new_answer)
    response = client.post(f"/api/conversations/{cid}/regenerate", json={"system": "Be funny."}, headers=auth)
    events = parse_events(response)
    assert events[-1]["type"] == "done"
    assert events[-1]["assistant_message"]["id"] == old_id
    assert events[-1]["assistant_message"]["content"] == "A brand new answer"
    # the model saw the system prompt and the original question, but not the old answer
    assert calls[-1] == [{"role": "system", "content": "Be funny."}, {"role": "user", "content": "Tell me a joke"}]

    messages = client.get(f"/api/conversations/{cid}/messages", headers=auth).json()
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[1]["content"] == "A brand new answer"


def test_regenerate_failure_keeps_old_answer(client, auth, monkeypatch):
    chat = make_chat(client, auth)
    cid = chat["conversation"]["id"]

    async def broken(history, model=None, temperature=None):
        raise HTTPException(status_code=503, detail="Ollama is down")
        yield  # pragma: no cover

    monkeypatch.setattr(main, "stream_ollama", broken)
    events = parse_events(client.post(f"/api/conversations/{cid}/regenerate", json={}, headers=auth))
    assert events == [{"type": "error", "detail": "Ollama is down"}]
    messages = client.get(f"/api/conversations/{cid}/messages", headers=auth).json()
    assert messages[1]["content"] == "Hello from the fake model"


def test_regenerate_checks_ownership_and_state(client, auth):
    cid = make_chat(client, auth)["conversation"]["id"]
    bob = register(client, email="bob@example.com", name="Bob")
    assert client.post(f"/api/conversations/{cid}/regenerate", json={}, headers=bob).status_code == 404
    assert client.post("/api/conversations/9999/regenerate", json={}, headers=auth).status_code == 404

    imported = client.post("/api/import", json={"conversations": [
        {"title": "Only a question", "messages": [{"role": "user", "content": "hello?"}]}]}, headers=auth)
    assert imported.status_code == 201
    only_user = [c for c in client.get("/api/conversations", headers=auth).json() if c["title"] == "Only a question"][0]
    assert client.post(f"/api/conversations/{only_user['id']}/regenerate", json={}, headers=auth).status_code == 409


# ---------------------------------------------------------------- password
def test_change_password_flow(client, auth):
    wrong = client.post("/api/auth/password", json={"current_password": "nope-nope-1", "new_password": "brand-new-pass-2"}, headers=auth)
    assert wrong.status_code == 400  # never 401, which would sign the user out in the UI

    same = client.post("/api/auth/password", json={"current_password": "correct-horse-1", "new_password": "correct-horse-1"}, headers=auth)
    assert same.status_code == 400

    ok = client.post("/api/auth/password", json={"current_password": "correct-horse-1", "new_password": "brand-new-pass-2"}, headers=auth)
    assert ok.status_code == 200
    assert client.post("/api/auth/login", json={"email": "alice@example.com", "password": "correct-horse-1"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "alice@example.com", "password": "brand-new-pass-2"}).status_code == 200


def test_change_password_is_rate_limited(client, auth):
    for _ in range(8):
        client.post("/api/auth/password", json={"current_password": "wrong-pass-12", "new_password": "brand-new-pass-2"}, headers=auth)
    blocked = client.post("/api/auth/password", json={"current_password": "correct-horse-1", "new_password": "brand-new-pass-2"}, headers=auth)
    assert blocked.status_code == 429


# ---------------------------------------------------------------- export / import
def test_export_and_import_round_trip(client, auth):
    cid = make_chat(client, auth, "keep this chat")["conversation"]["id"]
    client.patch(f"/api/conversations/{cid}", json={"pinned": True}, headers=auth)
    client.patch(f"/api/conversations/{cid}/folder", json={"folder": "Work"}, headers=auth)

    exported = client.get("/api/export", headers=auth).json()
    assert exported["app"] == "clarity-chat" and len(exported["conversations"]) == 1
    assert exported["conversations"][0]["messages"][0]["content"] == "keep this chat"

    bob = register(client, email="bob@example.com", name="Bob")
    result = client.post("/api/import", json={"conversations": exported["conversations"]}, headers=bob)
    assert result.status_code == 201 and result.json() == {"imported": 1}

    imported = client.get("/api/conversations", headers=bob).json()
    assert len(imported) == 1 and imported[0]["folder"] == "Work" and imported[0]["pinned"] is True
    messages = client.get(f"/api/conversations/{imported[0]['id']}/messages", headers=bob).json()
    assert [m["role"] for m in messages] == ["user", "assistant"]


def test_import_validates_input(client, auth):
    bad_role = {"conversations": [{"messages": [{"role": "system", "content": "x"}]}]}
    assert client.post("/api/import", json=bad_role, headers=auth).status_code == 422
    empty = client.post("/api/import", json={"conversations": [{"title": "Empty", "messages": []}]}, headers=auth)
    assert empty.json() == {"imported": 0}
    bad_time = {"conversations": [{"messages": [{"role": "user", "content": "hi", "created_at": "not a date"}]}]}
    assert client.post("/api/import", json=bad_time, headers=auth).json() == {"imported": 1}
    assert client.post("/api/import", json={"conversations": []}).status_code == 401
