import pytest
from fastapi.testclient import TestClient

from app import main


@pytest.fixture()
def calls():
    """Records the message history sent to the fake model."""
    return []


@pytest.fixture()
def options():
    """Records the model and temperature sent to the fake model."""
    return []


@pytest.fixture()
def client(tmp_path, monkeypatch, calls, options):
    monkeypatch.setattr(main, "DATABASE_PATH", str(tmp_path / "test.db"))
    main.login_limiter.reset()
    main.register_limiter.reset()
    main.init_db()

    async def fake_ask(history, model=None, temperature=None):
        calls.append(history)
        options.append({"model": model, "temperature": temperature})
        return "Hello from the fake model"

    async def fake_stream(history, model=None, temperature=None):
        calls.append(history)
        options.append({"model": model, "temperature": temperature})
        for part in ("Hello ", "from ", "stream"):
            yield part

    monkeypatch.setattr(main, "ask_ollama", fake_ask)
    monkeypatch.setattr(main, "stream_ollama", fake_stream)
    return TestClient(main.app)


def register(client, email="alice@example.com", name="Alice", password="correct-horse-1"):
    response = client.post("/api/auth/register", json={"name": name, "email": email, "password": password})
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture()
def auth(client):
    return register(client)
