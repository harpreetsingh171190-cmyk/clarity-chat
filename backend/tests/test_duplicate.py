from tests.conftest import register


def make_chat(client, auth):
    first = client.post("/api/chat", json={"message": "hello there"}, headers=auth).json()
    cid = first["conversation"]["id"]
    client.post("/api/chat", json={"message": "and more", "conversation_id": cid}, headers=auth)
    return cid


def test_duplicate_copies_messages_into_a_new_unpinned_chat(client, auth):
    cid = make_chat(client, auth)
    client.patch(f"/api/conversations/{cid}", json={"pinned": True}, headers=auth)
    client.patch(f"/api/conversations/{cid}/folder", json={"folder": "Work"}, headers=auth)

    response = client.post(f"/api/conversations/{cid}/duplicate", headers=auth)
    assert response.status_code == 201
    copy = response.json()
    assert copy["id"] != cid
    assert copy["title"].endswith("(copy)")
    assert copy["folder"] == "Work" and copy["pinned"] is False

    original = client.get(f"/api/conversations/{cid}/messages", headers=auth).json()
    cloned = client.get(f"/api/conversations/{copy['id']}/messages", headers=auth).json()
    assert [(m["role"], m["content"]) for m in cloned] == [(m["role"], m["content"]) for m in original]
    assert len(original) == 4


def test_duplicate_is_independent_and_isolated(client, auth):
    cid = make_chat(client, auth)
    copy_id = client.post(f"/api/conversations/{cid}/duplicate", headers=auth).json()["id"]
    client.delete(f"/api/conversations/{copy_id}", headers=auth)
    assert len(client.get(f"/api/conversations/{cid}/messages", headers=auth).json()) == 4

    other = register(client, email="bob@example.com", name="Bob")
    assert client.post(f"/api/conversations/{cid}/duplicate", headers=other).status_code == 404
    assert client.post("/api/conversations/99999/duplicate", headers=auth).status_code == 404
