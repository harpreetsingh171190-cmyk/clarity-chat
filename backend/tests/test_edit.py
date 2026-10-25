import json

from tests.conftest import register


def parse_events(response):
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def build_chat(client, auth):
    first = client.post("/api/chat", json={"message": "first question"}, headers=auth).json()
    cid = first["conversation"]["id"]
    client.post("/api/chat", json={"message": "second question", "conversation_id": cid}, headers=auth)
    messages = client.get(f"/api/conversations/{cid}/messages", headers=auth).json()
    return cid, messages


def test_edit_replaces_question_and_drops_later_messages(client, auth, calls):
    cid, messages = build_chat(client, auth)
    first_user = messages[0]["id"]

    response = client.post(f"/api/conversations/{cid}/messages/{first_user}/edit",
                           json={"message": "  rewritten question  "}, headers=auth)
    assert response.status_code == 200
    events = parse_events(response)
    assert events[-1]["type"] == "done"
    assert events[-1]["user_message"]["content"] == "rewritten question"

    after = client.get(f"/api/conversations/{cid}/messages", headers=auth).json()
    assert [m["role"] for m in after] == ["user", "assistant"]
    assert after[0]["id"] == first_user and after[0]["content"] == "rewritten question"
    assert after[1]["content"] == "Hello from stream"
    # The model saw only the edited question, not the later turns.
    assert calls[-1] == [{"role": "user", "content": "rewritten question"}]


def test_edit_later_question_keeps_earlier_context(client, auth, calls):
    cid, messages = build_chat(client, auth)
    second_user = messages[2]["id"]
    client.post(f"/api/conversations/{cid}/messages/{second_user}/edit", json={"message": "better second"}, headers=auth)
    assert [m["content"] for m in calls[-1]] == ["first question", "Hello from the fake model", "better second"]
    after = client.get(f"/api/conversations/{cid}/messages", headers=auth).json()
    assert [m["content"] for m in after][-2] == "better second"
    assert len(after) == 4


def test_edit_rejects_assistant_messages_and_unknown_ids(client, auth):
    cid, messages = build_chat(client, auth)
    assistant_id = messages[1]["id"]
    assert client.post(f"/api/conversations/{cid}/messages/{assistant_id}/edit",
                       json={"message": "x"}, headers=auth).status_code == 409
    assert client.post(f"/api/conversations/{cid}/messages/99999/edit",
                       json={"message": "x"}, headers=auth).status_code == 404
    first_user = messages[0]["id"]
    assert client.post(f"/api/conversations/{cid}/messages/{first_user}/edit",
                       json={"message": "   "}, headers=auth).status_code == 422


def test_edit_is_isolated_between_users(client, auth):
    cid, messages = build_chat(client, auth)
    other = register(client, email="bob@example.com", name="Bob")
    response = client.post(f"/api/conversations/{cid}/messages/{messages[0]['id']}/edit",
                           json={"message": "hijack"}, headers=other)
    assert response.status_code == 404
    assert client.get(f"/api/conversations/{cid}/messages", headers=auth).json()[0]["content"] == "first question"


def test_failed_edit_leaves_conversation_untouched(client, auth, monkeypatch):
    from fastapi import HTTPException
    from app import main

    cid, messages = build_chat(client, auth)

    async def broken(history, model=None, temperature=None):
        raise HTTPException(status_code=503, detail="Ollama is down")
        yield ""  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(main, "stream_ollama", broken)
    response = client.post(f"/api/conversations/{cid}/messages/{messages[0]['id']}/edit",
                           json={"message": "will fail"}, headers=auth)
    assert parse_events(response)[-1]["type"] == "error"
    after = client.get(f"/api/conversations/{cid}/messages", headers=auth).json()
    assert [m["content"] for m in after] == [m["content"] for m in messages]
