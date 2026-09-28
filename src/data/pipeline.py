import argparse
from pathlib import Path

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _resolve_project_path(value: str | None, default_name: str) -> Path:
    if value is None or value == "":
        return PROJECT_ROOT / default_name
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    return (PROJECT_ROOT / candidate).resolve()


def run_pipeline(db_path="supply_chain.db", data_dir="./data"):
    """
    Data Model Documentation:
    - Table: ports (Reference data for port codes and congestion)
    - Table: curated_shipments (Core fact table with derived analytical fields)
    - Table: port_events (Streaming log of vessel events)
    """
    db_path = _resolve_project_path(db_path, "supply_chain.db")
    data_dir = _resolve_project_path(data_dir, "data")

    print(f"Connecting to DuckDB at {db_path}...")
    conn = duckdb.connect(str(db_path))

    # 1. Ingest and clean ports
    conn.execute(f"""
        CREATE OR REPLACE TABLE ports AS
        SELECT
            port_code,
            port_name,
            -- Impute missing country for Antwerp
            CASE WHEN port_code = 'BEANR' THEN 'Belgium' ELSE country END AS country,
            region,
            timezone,
            avg_congestion_score
        FROM read_csv_auto('{data_dir}/ports*.csv', normalize_names=True);
    """)

    # 2. Ingest and curate shipments
    shipments_query = f"""
        CREATE OR REPLACE TABLE curated_shipments AS
        WITH raw_shipments AS (
            -- Drop exact duplicates by shipment_id
            SELECT DISTINCT ON (shipment_id) *
            FROM read_csv_auto('{data_dir}/shipments*.csv', normalize_names=True)
            WHERE planned_departure IS NOT NULL
              AND planned_arrival IS NOT NULL
              -- Drop impossible physical constraints
              AND container_count > 0
              AND (weight_tons IS NULL OR weight_tons >= 0)
              -- Drop chronological inversions
              AND (CAST(booking_date AS TIMESTAMP) <= CAST(actual_departure AS TIMESTAMP) OR actual_departure IS NULL)
        )
        SELECT
            r.shipment_id,
            r.customer_id,
            r.origin_port,
            r.destination_port,
            r.vessel_id,
            r.booking_date,
            r.planned_departure,
            r.actual_departure,
            r.planned_arrival,
            r.actual_arrival,
            r.container_count,

            -- Standardize and impute categorical fields
            COALESCE(r.cargo_type, 'OTHER') AS cargo_type,
            r.weight_tons,
            COALESCE(UPPER(r.status), 'UNKNOWN') AS status,

            -- Derived metrics
            r.origin_port || '->' || r.destination_port AS route_key,

            CASE
                WHEN r.actual_arrival IS NOT NULL
                THEN date_diff('hour', CAST(r.planned_arrival AS TIMESTAMP), CAST(r.actual_arrival AS TIMESTAMP))
                ELSE NULL
            END AS actual_delay_hours,

            CASE
                WHEN r.actual_arrival IS NOT NULL
                THEN (date_diff('hour', CAST(r.planned_arrival AS TIMESTAMP), CAST(r.actual_arrival AS TIMESTAMP)) <= 24)
                ELSE NULL
            END AS on_time_flag,

            date_diff('day', CAST(r.planned_departure AS TIMESTAMP), CAST(r.planned_arrival AS TIMESTAMP)) AS transit_days_planned,

            CASE
                WHEN r.actual_arrival IS NOT NULL AND r.actual_departure IS NOT NULL
                THEN date_diff('day', CAST(r.actual_departure AS TIMESTAMP), CAST(r.actual_arrival AS TIMESTAMP))
                ELSE NULL
            END AS transit_days_actual

        FROM raw_shipments r
        -- Enforce relational integrity (Drops invalid UN/LOCODEs)
        INNER JOIN ports p_orig ON r.origin_port = p_orig.port_code
        INNER JOIN ports p_dest ON r.destination_port = p_dest.port_code;
    """
    conn.execute(shipments_query)

    # 3. Ingest and clean port events
    conn.execute(f"""
        CREATE OR REPLACE TABLE port_events AS
        WITH raw_events AS (
            -- Drop duplicate event_ids
            SELECT DISTINCT ON (event_id) *
            FROM read_csv_auto('{data_dir}/port_events*.csv', normalize_names=True)
            -- Drop missing event types
            WHERE event_type IS NOT NULL
        )
        SELECT e.*
        FROM raw_events e
        -- Drop orphaned events (Vessels that don't exist in our active shipments)
        WHERE e.vessel_id IN (SELECT vessel_id FROM curated_shipments);
    """)

    shipments_count = conn.execute("SELECT COUNT(*) FROM curated_shipments").fetchone()[
        0
    ]
    events_count = conn.execute("SELECT COUNT(*) FROM port_events").fetchone()[0]

    print("✅ Pipeline executed successfully.")
    print(f"   - {shipments_count} curated shipments loaded.")
    print(f"   - {events_count} valid port events loaded.")

    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input", default="./data", help="Directory containing raw CSVs"
    )
    parser.add_argument(
        "--db", default="supply_chain.db", help="Path to DuckDB database file"
    )
    args = parser.parse_args()

    if not Path(args.input).exists() and not (PROJECT_ROOT / args.input).exists():
        print(f"Error: Data directory '{args.input}' not found.")
    else:
        run_pipeline(data_dir=args.input, db_path=args.db)
