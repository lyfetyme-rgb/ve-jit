from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_compliance_status():
    response = client.get("/api/v1/compliance/status")
    assert response.status_code == 200
    body = response.json()
    assert "soc2_foundation" in body


def test_audit_log_info():
    response = client.get("/api/v1/compliance/audit-log-info")
    assert response.status_code == 200
    assert "exists" in response.json()
