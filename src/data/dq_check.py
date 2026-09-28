import argparse
import json
from pathlib import Path

import pandas as pd


def check_shipments(df: pd.DataFrame) -> list:
    issues = []

    # 1. Missing Timestamps
    missing_dep = df["planned_departure"].isnull().sum()
    if missing_dep > 0:
        issues.append(
            {
                "check": "Missing planned_departure",
                "rows_affected": int(missing_dep),
                "severity": "critical",
                "action": "Drop rows",
            }
        )

    # 2. Chronological Impossibility (Arrival before Departure)
    if "actual_departure" in df.columns and "actual_arrival" in df.columns:
        chron_issues = (
            pd.to_datetime(df["actual_arrival"])
            < pd.to_datetime(df["actual_departure"])
        ).sum()
        if chron_issues > 0:
            issues.append(
                {
                    "check": "Arrival before Departure",
                    "rows_affected": int(chron_issues),
                    "severity": "critical",
                    "action": "Quarantine",
                }
            )

    # 3. Negative Weights (Updated to match weight_tons)
    if "weight_tons" in df.columns:
        neg_weights = (df["weight_tons"] < 0).sum()
        if neg_weights > 0:
            issues.append(
                {
                    "check": "Negative weight_tons",
                    "rows_affected": int(neg_weights),
                    "severity": "warning",
                    "action": "Take absolute value",
                }
            )

    return issues


def check_ports(df: pd.DataFrame) -> list:
    issues = []
    # Check for invalid timezones or missing congestion scores
    if "timezone" in df.columns:
        missing_tz = df["timezone"].isnull().sum()
        if missing_tz > 0:
            issues.append(
                {
                    "check": "Missing timezone",
                    "rows_affected": int(missing_tz),
                    "severity": "warning",
                    "action": "Default to UTC",
                }
            )
    return issues


def check_port_events(df: pd.DataFrame) -> list:
    issues = []
    # Check for negative delays
    if "delay_minutes" in df.columns:
        neg_delay = (df["delay_minutes"] < 0).sum()
        if neg_delay > 0:
            issues.append(
                {
                    "check": "Negative delay_minutes",
                    "rows_affected": int(neg_delay),
                    "severity": "warning",
                    "action": "Flag as potential early arrival or data error",
                }
            )
    return issues


def run_dq_checks(data_dir: str):
    data_path = Path(data_dir)
    report = {"summary": "Data Quality Report", "issues": []}

    files = {
        "shipments.csv": check_shipments,
        "ports.csv": check_ports,
        "port_events.csv": check_port_events,
    }

    for filename, check_func in files.items():
        file_path = data_path / filename
        if file_path.exists():
            df = pd.read_csv(file_path)
            report["issues"].extend(check_func(df))
        else:
            print(f"Warning: {filename} not found in {data_dir}")

    with open("dq_report.json", "w") as f:
        json.dump(report, f, indent=4)
    print("✅ Data Quality checks complete. Report saved to dq_report.json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="./data")
    args = parser.parse_args()
    run_dq_checks(args.input)
