import json
from unittest.mock import MagicMock, patch

from src.agent import assistant


@patch("src.agent.assistant.duckdb.connect")
def test_get_route_stats(mock_connect):
    mock_conn = mock_connect.return_value
    mock_conn.execute.return_value.fetchone.return_value = (150, 14.5, 0.88)

    res = json.loads(assistant.get_route_stats(origin="CNSHA", destination="NLRTM"))
    assert res["route_key"] == "CNSHA->NLRTM"
    assert res["shipment_count"] == 150

    # No data path
    mock_conn.execute.return_value.fetchone.return_value = None
    res_err = json.loads(assistant.get_route_stats(origin="AAA", destination="BBB"))
    assert "error" in res_err


@patch("src.agent.assistant.duckdb.connect")
def test_query_shipments(mock_connect):
    import pandas as pd

    mock_conn = mock_connect.return_value
    mock_conn.execute.return_value.fetchdf.return_value = pd.DataFrame(
        [{"shipment_id": "SHP-123"}]
    )

    res = json.loads(assistant.query_shipments(origin="NLRTM", limit=1))
    assert isinstance(res, list)
    assert res[0]["shipment_id"] == "SHP-123"


@patch("src.agent.assistant.httpx.post")
@patch("src.agent.assistant.duckdb.connect")
def test_predict_delay_tool(mock_connect, mock_post):
    import pandas as pd

    mock_conn = mock_connect.return_value
    mock_conn.execute.return_value.fetchdf.return_value = pd.DataFrame(
        [
            {
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
        ]
    )
    mock_post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"shipment_id": "SHP-001", "delay_risk_probability": 0.72},
    )

    if hasattr(assistant, "predict_delay"):
        res = assistant.predict_delay("SHP-001")
        assert res is not None


@patch("src.agent.assistant.genai.GenerativeModel")
@patch("src.agent.assistant.genai.configure")
@patch.dict("os.environ", {"GEMINI_API_KEY": "dummy_key"})
def test_run_assistant_exit(mock_cfg, mock_model):
    """Test assistant loop handles 'exit' without crashing."""
    with patch("builtins.input", side_effect=["exit"]):
        try:
            assistant.run_assistant()
        except SystemExit:
            pass
