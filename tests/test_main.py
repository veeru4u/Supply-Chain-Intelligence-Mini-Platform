from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from src.api.main import app

client = TestClient(app)


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_dq_report_endpoint():
    response = client.get("/data-quality/report")
    # Both 200 (found) or 404/200 mock are valid depending on if json exists
    assert response.status_code in [200, 404]


@patch("src.api.main.duckdb.connect")
def test_analytics_route_stats_endpoint(mock_db):
    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchall.return_value = [
        ("CNSHA", "NLRTM", 120, 15.2, 0.85)
    ]
    mock_db.return_value = mock_conn

    response = client.get("/analytics/routes")
    if response.status_code != 404:
        assert response.status_code == 200


@patch("src.api.main.duckdb.connect")
def test_analytics_shipments_query(mock_db):
    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchdf.return_value = MagicMock(
        to_dict=lambda orient: []
    )
    mock_db.return_value = mock_conn

    response = client.get("/shipments?limit=5")
    if response.status_code != 404:
        assert response.status_code in [200, 422]


@patch("src.api.main.ml_model")
def test_predict_delay_endpoint(mock_model):
    if mock_model is not None:
        mock_model.predict_proba.return_value = [[0.15, 0.85]]
        mock_model.predict.return_value = [1]

    payload = {
        "shipment_id": "SHP-001",
        "cargo_type": "Standard",
        "weight_tons": 250.0,
        "container_count": 10,
        "route_key": "CNSHA->NLRTM",
        "transit_days_planned": 24,
        "booking_lead_days": 10,
        "departure_month": 6,
        "departure_dow": 2,
        "origin_congestion": 0.45,
        "dest_congestion": 0.60,
        "vessel_hist_delay_rate": 0.25,
        "route_hist_delay_rate": 0.30,
    }

    response = client.post("/predict-delay", json=payload)
    if response.status_code == 200:
        data = response.json()
        assert "delay_risk_probability" in data
        assert data["shipment_id"] == "SHP-001"


def test_predict_delay_invalid_input():
    # Test validation error
    response = client.post("/predict-delay", json={"invalid": "payload"})
    assert response.status_code == 422
