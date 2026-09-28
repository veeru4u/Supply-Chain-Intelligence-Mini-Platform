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
    - Table: port_events (Streaming log of vessel events)
    - Table: curated_shipments (Core fact table with derived analytical fields)
      - Primary Key: shipment_id
      - Foreign Keys: origin_port/destination_port -> ports.port_code
    """
    db_path = _resolve_project_path(db_path, "supply_chain.db")
    data_dir = _resolve_project_path(data_dir, "data")

    print(f"Connecting to DuckDB at {db_path}...")
    conn = duckdb.connect(str(db_path))

    conn.execute(f"""
        CREATE OR REPLACE TABLE ports AS
        SELECT * FROM read_csv_auto('{data_dir}/ports*.csv', normalize_names=True);
    """)

    conn.execute(f"""
        CREATE OR REPLACE TABLE port_events AS
        SELECT * FROM read_csv_auto('{data_dir}/port_events*.csv', normalize_names=True);
    """)

    shipments_query = f"""
        CREATE OR REPLACE TABLE curated_shipments AS
        WITH raw_shipments AS (
            SELECT * FROM read_csv_auto('{data_dir}/shipments*.csv', normalize_names=True)
            WHERE planned_departure IS NOT NULL AND planned_arrival IS NOT NULL
        )
        SELECT
            *,
            origin_port || '->' || destination_port AS route_key,

            CASE
                WHEN actual_arrival IS NOT NULL
                THEN date_diff('hour', CAST(planned_arrival AS TIMESTAMP), CAST(actual_arrival AS TIMESTAMP))
                ELSE NULL
            END AS actual_delay_hours,

            CASE
                WHEN actual_arrival IS NOT NULL
                THEN (date_diff('hour', CAST(planned_arrival AS TIMESTAMP), CAST(actual_arrival AS TIMESTAMP)) <= 24)
                ELSE NULL
            END AS on_time_flag,

            date_diff('day', CAST(planned_departure AS TIMESTAMP), CAST(planned_arrival AS TIMESTAMP)) AS transit_days_planned,

            CASE
                WHEN actual_arrival IS NOT NULL AND actual_departure IS NOT NULL
                THEN date_diff('day', CAST(actual_departure AS TIMESTAMP), CAST(actual_arrival AS TIMESTAMP))
                ELSE NULL
            END AS transit_days_actual

        FROM raw_shipments;
    """

    conn.execute(shipments_query)

    count = conn.execute("SELECT COUNT(*) FROM curated_shipments").fetchone()[0]
    print(
        f"✅ Pipeline executed successfully. {count} curated shipments loaded into DuckDB."
    )

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
        
        
        