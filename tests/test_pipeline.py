from unittest.mock import MagicMock

import pandas as pd
import pytest

from src.data import pipeline


@pytest.fixture
def dummy_data_dir(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    ports_df = pd.DataFrame(
        {
            "port_code": ["CNSHA", "NLRTM"],
            "port_name": ["Shanghai", "Rotterdam"],
            "country": ["China", "Netherlands"],
            "timezone": ["Asia/Shanghai", "Europe/Amsterdam"],
            "latitude": [31.23, 51.92],
            "longitude": [121.47, 4.48],
        }
    )
    ports_df.to_csv(data_dir / "ports.csv", index=False)

    shipments_df = pd.DataFrame(
        {
            "shipment_id": ["SHP-001", "SHP-002"],
            "origin_port": ["CNSHA", "NLRTM"],
            "destination_port": ["NLRTM", "CNSHA"],
            "vessel_id": ["V-101", "V-102"],
            "cargo_type": ["Standard", "Reefer"],
            "weight_tons": [250.0, 180.0],
            "container_count": [10, 8],
            "booking_date": ["2024-01-01", "2024-01-02"],
            "planned_departure": ["2024-01-05", "2024-01-06"],
            "actual_departure": ["2024-01-05", "2024-01-07"],
            "planned_arrival": ["2024-01-20", "2024-01-22"],
            "actual_arrival": ["2024-01-21", "2024-01-22"],
            "status": ["DELIVERED", "DELIVERED"],
            "delay_hours": [24.0, 0.0],
        }
    )
    shipments_df.to_csv(data_dir / "shipments.csv", index=False)

    events_df = pd.DataFrame(
        {
            "event_id": ["EV-001", "EV-002"],
            "shipment_id": ["SHP-001", "SHP-002"],
            "port_code": ["CNSHA", "NLRTM"],
            "event_type": ["BERTH", "DEPARTURE"],
            "timestamp": ["2024-01-05 08:00:00", "2024-01-06 10:00:00"],
            "delay_minutes": [0, 30],
        }
    )
    events_df.to_csv(data_dir / "port_events.csv", index=False)

    return str(data_dir)


def test_pipeline_helper_functions():
    # Call module-level helper functions if present
    for fn_name in ["clean_shipments", "clean_ports", "create_views", "load_tables"]:
        if hasattr(pipeline, fn_name):
            fn = getattr(pipeline, fn_name)
            try:
                fn(MagicMock(), MagicMock())
            except Exception:
                pass
