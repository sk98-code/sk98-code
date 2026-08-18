from fastapi.testclient import TestClient

from api.index import app

client = TestClient(app)


def test_root_lists_entrypoints():
    body = client.get("/").json()
    assert body["docs"] == "/docs"
    assert body["health"] == "/api/health"


def test_health_ok():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_echo_roundtrip():
    response = client.post("/api/echo", json={"message": "vercel"})
    assert response.status_code == 200
    assert response.json() == {"original": "vercel", "reversed": "lecrev", "length": 6}


def test_echo_rejects_empty_message():
    assert client.post("/api/echo", json={"message": ""}).status_code == 422


def test_item_validation():
    assert client.get("/api/items/7?q=hi").json() == {"item_id": 7, "q": "hi"}
    assert client.get("/api/items/0").status_code == 400
