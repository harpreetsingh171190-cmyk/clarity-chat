from tests.conftest import register


def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_register_login_and_me(client):
    headers = register(client)
    me = client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["email"] == "alice@example.com"

    login = client.post("/api/auth/login", json={"email": "ALICE@example.com", "password": "correct-horse-1"})
    assert login.status_code == 200
    assert login.json()["user"]["name"] == "Alice"


def test_duplicate_email_rejected(client):
    register(client)
    response = client.post("/api/auth/register", json={"name": "Again", "email": "alice@example.com", "password": "another-pass-1"})
    assert response.status_code == 409


def test_wrong_password_rejected(client):
    register(client)
    response = client.post("/api/auth/login", json={"email": "alice@example.com", "password": "nope-nope-1"})
    assert response.status_code == 401


def test_weak_or_oversized_password_rejected(client):
    short = client.post("/api/auth/register", json={"name": "A", "email": "a@example.com", "password": "short"})
    assert short.status_code == 422
    long = client.post("/api/auth/register", json={"name": "A", "email": "a@example.com", "password": "x" * 80})
    assert long.status_code == 422


def test_protected_routes_need_token(client):
    assert client.get("/api/conversations").status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer not-a-token"}).status_code == 401


def test_login_is_rate_limited(client):
    register(client)
    for _ in range(8):
        assert client.post("/api/auth/login", json={"email": "alice@example.com", "password": "bad-pass-123"}).status_code == 401
    blocked = client.post("/api/auth/login", json={"email": "alice@example.com", "password": "correct-horse-1"})
    assert blocked.status_code == 429


def test_delete_account_removes_data(client, auth):
    client.post("/api/chat", json={"message": "hi"}, headers=auth)
    assert client.delete("/api/auth/me", headers=auth).status_code == 204
    assert client.get("/api/auth/me", headers=auth).status_code == 401
    login = client.post("/api/auth/login", json={"email": "alice@example.com", "password": "correct-horse-1"})
    assert login.status_code == 401
