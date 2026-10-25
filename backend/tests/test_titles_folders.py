from fastapi import HTTPException

from app import main
from tests.conftest import register


def make_chat(client, auth, text="tell me about tomatoes"):
    return client.post("/api/chat", json={"message": text}, headers=auth).json()["conversation"]["id"]


def test_clean_title_strips_decoration():
    assert main.clean_title('  "Garden Plan for Spring."  ') == "Garden Plan for Spring"
    assert main.clean_title("Title: Tomato Basics\nextra line") == "Tomato Basics"
    assert main.clean_title("**Chat title:** Weekend Trip") == "Weekend Trip"
    assert main.clean_title("   \n  ") == ""
    assert len(main.clean_title("word " * 40)) <= 60


def test_generate_title_updates_conversation(client, auth, calls, monkeypatch):
    cid = make_chat(client, auth)

    async def fake_ask(history, model=None, temperature=None):
        calls.append(history)
        return '"Growing Tomatoes."'

    monkeypatch.setattr(main, "ask_ollama", fake_ask)
    response = client.post(f"/api/conversations/{cid}/title", json={}, headers=auth)
    assert response.status_code == 200 and response.json()["title"] == "Growing Tomatoes"
    sent = calls[-1]
    assert "tell me about tomatoes" in sent[-1]["content"] and "Hello from the fake model" in sent[-1]["content"]
    listed = client.get("/api/conversations", headers=auth).json()
    assert listed[0]["title"] == "Growing Tomatoes"


def test_generate_title_failure_keeps_old_title(client, auth, monkeypatch):
    cid = make_chat(client, auth)
    original = client.get("/api/conversations", headers=auth).json()[0]["title"]

    async def down(history, model=None, temperature=None):
        raise HTTPException(status_code=503, detail="Ollama is down")

    monkeypatch.setattr(main, "ask_ollama", down)
    assert client.post(f"/api/conversations/{cid}/title", json={}, headers=auth).status_code == 503
    assert client.get("/api/conversations", headers=auth).json()[0]["title"] == original

    async def blank(history, model=None, temperature=None):
        return '""'

    monkeypatch.setattr(main, "ask_ollama", blank)
    assert client.post(f"/api/conversations/{cid}/title", json={}, headers=auth).status_code == 502
    assert client.get("/api/conversations", headers=auth).json()[0]["title"] == original


def test_generate_title_is_isolated(client, auth):
    cid = make_chat(client, auth)
    other = register(client, email="bob@example.com", name="Bob")
    assert client.post(f"/api/conversations/{cid}/title", json={}, headers=other).status_code == 404


def test_rename_folder_moves_only_own_conversations(client, auth):
    first, second = make_chat(client, auth, "one"), make_chat(client, auth, "two")
    for cid in (first, second):
        client.patch(f"/api/conversations/{cid}/folder", json={"folder": "Work"}, headers=auth)
    other = register(client, email="bob@example.com", name="Bob")
    bobs = make_chat(client, other, "bob chat")
    client.patch(f"/api/conversations/{bobs}/folder", json={"folder": "Work"}, headers=other)

    response = client.patch("/api/folders", json={"old": "Work", "new": "  Projects "}, headers=auth)
    assert response.status_code == 200 and response.json() == {"renamed": 2, "folder": "Projects"}
    assert {c["folder"] for c in client.get("/api/conversations", headers=auth).json()} == {"Projects"}
    assert client.get("/api/conversations", headers=other).json()[0]["folder"] == "Work"


def test_rename_missing_or_blank_folder(client, auth):
    make_chat(client, auth)
    assert client.patch("/api/folders", json={"old": "Nope", "new": "X"}, headers=auth).status_code == 404
    assert client.patch("/api/folders", json={"old": "General", "new": "   "}, headers=auth).status_code == 422
