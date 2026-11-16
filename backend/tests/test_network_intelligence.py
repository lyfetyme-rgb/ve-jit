from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_network_insights():
    response = client.get("/api/v1/network/insights")
    assert response.status_code == 200
    assert isinstance(response.json(), list)
    assert len(response.json()) > 0


def test_network_percentile():
    response = client.get("/api/v1/network/percentile")
    assert response.status_code == 200
    assert "percentile" in response.json()


def test_vendor_lookup():
    response = client.get("/api/v1/network/vendors")
    assert response.status_code == 200
    assert isinstance(response.json(), list)
