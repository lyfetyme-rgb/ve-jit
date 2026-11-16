from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "operational"


def test_queue():
    response = client.get("/api/v1/queue/recommendation")
    assert response.status_code == 200
    assert "recommended_registers" in response.json()


def test_jit_demo():
    response = client.get("/api/v1/jit/demo")
    assert response.status_code == 200
    body = response.json()
    assert "economic_order_quantity" in body


def test_ui_redirect():
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (302, 307)
