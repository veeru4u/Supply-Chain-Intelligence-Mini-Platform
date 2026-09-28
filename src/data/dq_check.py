import argparse
import json
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _resolve_project_path(value: str | None, default_name: str) -> Path:
    if value is None or value == "":
        return PROJECT_ROOT / default_name
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    return (PROJECT_ROOT / candidate).resolve()


def run_dq_checks(data_dir: str, output_file: str | None = None):
    data_path = _resolve_project_path(data_dir, "data")
    report_path = _resolve_project_path(output_file, "dq_report.json")
    report = {"summary": "Data Quality Report", "issues": []}

    # Load all datasets together to allow for relational checks
    try:
        ports_df = pd.read_csv(data_path / "ports.csv")
        shipments_df = pd.read_csv(data_path / "shipments.csv")
        events_df = pd.read_csv(data_path / "port_events.csv")
    except FileNotFoundError as e:
        print(f"❌ Error loading data files: {e}")
        return report

    def add_issue(check_name: str, affected: int, severity: str, action: str):
        """Helper to append discovered issues to the report."""
        if affected > 0:
            report["issues"].append(
                {
                    "check": check_name,
                    "rows_affected": int(affected),
                    "severity": severity,
                    "action": action,
                }
            )

    # --- 1. SHIPMENTS CHECKS ---

    # Physical constraints
    add_issue(
        "Negative weight_tons",
        (shipments_df["weight_tons"] < 0).sum(),
        "critical",
        "Quarantine",
    )
    add_issue(
        "Zero or negative container_count",
        (shipments_df["container_count"] <= 0).sum(),
        "critical",
        "Quarantine",
    )

    # Missing crucial features
    add_issue(
        "Missing weight_tons",
        shipments_df["weight_tons"].isnull().sum(),
        "warning",
        "Impute/Flag",
    )
    add_issue(
        "Missing status", shipments_df["status"].isnull().sum(), "warning", "Impute"
    )
    add_issue(
        "Missing cargo_type",
        shipments_df["cargo_type"].isnull().sum(),
        "info",
        "Impute",
    )

    # Relational integrity: Invalid UN/LOCODEs mapped against ports.csv
    valid_ports = set(ports_df["port_code"])
    invalid_routing = (~shipments_df["origin_port"].isin(valid_ports)) | (
        ~shipments_df["destination_port"].isin(valid_ports)
    )
    add_issue(
        "Invalid origin or destination port",
        invalid_routing.sum(),
        "critical",
        "Drop",
    )

    # Primary Key integrity
    add_issue(
        "Duplicate shipment_ids",
        shipments_df.duplicated(subset=["shipment_id"]).sum(),
        "critical",
        "Drop",
    )

    # Chronological integrity (Booking occurs after actual departure)
    b_dt = pd.to_datetime(shipments_df["booking_date"], errors="coerce")
    ad_dt = pd.to_datetime(shipments_df["actual_departure"], errors="coerce")
    add_issue(
        "Booking date after Actual Departure",
        (b_dt > ad_dt).sum(),
        "warning",
        "Drop",
    )

    # Categorical standardization
    bad_status = shipments_df["status"].isin(["COMPLETED", "Complete", "delivered"])
    add_issue(
        "Inconsistent status casing",
        bad_status.sum(),
        "warning",
        "Standardize to uppercase DELIVERED",
    )

    # --- 2. PORTS CHECKS ---
    add_issue(
        "Missing port country",
        ports_df["country"].isnull().sum(),
        "info",
        "Impute manually (e.g. BEANR -> Belgium)",
    )

    # --- 3. EVENTS CHECKS ---
    add_issue(
        "Missing event_type",
        events_df["event_type"].isnull().sum(),
        "critical",
        "Drop",
    )
    add_issue(
        "Duplicate event_ids",
        events_df.duplicated(subset=["event_id"]).sum(),
        "critical",
        "Drop",
    )

    # Relational integrity: Telemetry for vessels that have no active shipments
    valid_vessels = set(shipments_df["vessel_id"])
    orphan_events = ~events_df["vessel_id"].isin(valid_vessels)
    add_issue(
        "Orphaned events (unknown vessel_id)",
        orphan_events.sum(),
        "warning",
        "Drop",
    )

    # --- WRITE REPORT ---
    with open(report_path, "w") as f:
        json.dump(report, f, indent=4)

    print(
        f"✅ Data Quality checks complete. Found {len(report['issues'])} distinct issue types."
    )
    print(f"📄 Report saved to {report_path}")

    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="./data")
    parser.add_argument("--output", default="dq_report.json")
    args = parser.parse_args()

    run_dq_checks(args.input, args.output)
