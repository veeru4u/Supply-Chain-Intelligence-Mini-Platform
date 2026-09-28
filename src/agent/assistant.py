import json
import os
import sys
from datetime import datetime
from pathlib import Path

import duckdb
import google.generativeai as genai
import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _resolve_project_path(value: str | None, default_name: str) -> Path:
    if value is None or value == "":
        return PROJECT_ROOT / default_name
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    return (PROJECT_ROOT / candidate).resolve()


# ---------------------------------------------------------
# 1. Observability: Structured LLM & Tool Logging
# ---------------------------------------------------------
LOG_FILENAME = str(_resolve_project_path(os.getenv("AGENT_LOG_PATH"), "agent_activity.jsonl"))


def log_interaction(event_type: str, data: dict):
    entry = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "event_type": event_type,
        **data,
    }
    with open(LOG_FILENAME, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------
# 2. Tool Implementations
# ---------------------------------------------------------
DB_PATH = str(_resolve_project_path(os.getenv("DB_PATH"), "supply_chain.db"))
API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")


def query_shipments(
    origin: str = "",
    destination: str = "",
    status: str = "",
    limit: int = 5,
) -> str:
    """Reads shipments from the curated database with optional filters."""
    conn = duckdb.connect(DB_PATH, read_only=True)
    conditions = ["1=1"]
    params = []
    if origin:
        conditions.append("origin_port = ?")
        params.append(origin)
    if destination:
        conditions.append("destination_port = ?")
        params.append(destination)
    if status:
        conditions.append("status = ?")
        params.append(status)

    sql = f"""
        SELECT shipment_id, origin_port, destination_port, status, cargo_type, actual_delay_hours, on_time_flag
        FROM curated_shipments
        WHERE {" AND ".join(conditions)}
        LIMIT {limit}
    """
    df = conn.execute(sql, params).fetchdf()
    conn.close()

    if df.empty:
        return json.dumps({"message": "No matching shipments found."})
    return json.dumps(df.to_dict(orient="records"))


def get_route_stats(origin: str, destination: str) -> str:
    """Returns aggregated route metrics."""
    conn = duckdb.connect(DB_PATH, read_only=True)
    route_key = f"{origin}->{destination}"
    query = """
        SELECT
            COUNT(*) AS shipment_count,
            AVG(actual_delay_hours) AS avg_delay_hours,
            AVG(CASE WHEN on_time_flag THEN 1.0 ELSE 0.0 END) AS on_time_rate
        FROM curated_shipments
        WHERE route_key = ? AND actual_delay_hours IS NOT NULL
    """
    row = conn.execute(query, [route_key]).fetchone()
    conn.close()

    if not row or row[0] == 0:
        return json.dumps(
            {"error": f"No historical shipment data for route {route_key}"}
        )
    return json.dumps(
        {
            "route_key": route_key,
            "shipment_count": int(row[0]),
            "avg_delay_hours": (round(float(row[1]), 2) if row[1] is not None else 0.0),
            "on_time_rate": f"{round(float(row[2]) * 100, 1)}%",
        }
    )


def predict_delay(shipment_id: str) -> str:
    """Calls the predictive ML endpoint for a shipment ID."""
    conn = duckdb.connect(DB_PATH, read_only=True)
    row = conn.execute(
        """
        SELECT
            s.cargo_type, s.weight_tons, s.container_count, s.route_key,
            s.transit_days_planned,
            date_diff('day', CAST(s.booking_date AS TIMESTAMP), CAST(s.planned_departure AS TIMESTAMP)) AS booking_lead_days,
            EXTRACT(month FROM CAST(s.planned_departure AS TIMESTAMP)) AS departure_month,
            EXTRACT(dow FROM CAST(s.planned_departure AS TIMESTAMP)) AS departure_dow,
            COALESCE(p_orig.avg_congestion_score, 0.5) AS origin_congestion,
            COALESCE(p_dest.avg_congestion_score, 0.5) AS dest_congestion
        FROM curated_shipments s
        LEFT JOIN ports p_orig ON s.origin_port = p_orig.port_code
        LEFT JOIN ports p_dest ON s.destination_port = p_dest.port_code
        WHERE s.shipment_id = ?
    """,
        [shipment_id],
    ).fetchone()
    conn.close()

    if not row:
        return json.dumps({"error": f"Shipment ID '{shipment_id}' not found."})

    payload = {
        "shipment_id": shipment_id,
        "cargo_type": row[0] or "Standard",
        "weight_tons": float(row[1]) if row[1] else 100.0,
        "container_count": int(row[2]) if row[2] else 10,
        "route_key": row[3],
        "transit_days_planned": int(row[4]) if row[4] else 15,
        "booking_lead_days": int(row[5]) if row[5] else 7,
        "departure_month": int(row[6]) if row[6] else 6,
        "departure_dow": int(row[7]) if row[7] else 2,
        "origin_congestion": float(row[8]),
        "dest_congestion": float(row[9]),
        "vessel_hist_delay_rate": 0.4,
        "route_hist_delay_rate": 0.4,
    }

    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.post(f"{API_BASE_URL}/predict-delay", json=payload)
            return resp.text
    except Exception as e:
        return json.dumps({"error": f"Failed to connect to ML prediction service: {e}"})


TOOLS_MAP = {
    "query_shipments": query_shipments,
    "get_route_stats": get_route_stats,
    "predict_delay": predict_delay,
}

SYSTEM_INSTRUCTION = """You are an AI assistant for a Supply Chain Intelligence Platform.
You assist operations teams with maritime shipment queries, port route statistics, and delay risk forecasts.

GUARDRAILS & RULES:
1. Strict Domain Restriction: ONLY answer questions regarding logistics, shipping, ports, vessels, and shipment risk. Politely decline any off-topic inquiries.
2. Tool Usage: Always use the appropriate tool to retrieve factual data rather than hallucinating answers.
3. Citation: Explicitly cite which tool produced the numbers or predictions (e.g. '[Source: get_route_stats]').
"""


# ---------------------------------------------------------
# 3. Interactive CLI Loop
# ---------------------------------------------------------
def run_assistant():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print(
            "Error: GEMINI_API_KEY environment variable is not set.",
            file=sys.stderr,
        )
        print("Please set it with: set GEMINI_API_KEY=your_key", file=sys.stderr)
        sys.exit(1)

    genai.configure(api_key=api_key)

    model = genai.GenerativeModel(
        model_name="gemini-1.5-flash",
        tools=[query_shipments, get_route_stats, predict_delay],
        system_instruction=SYSTEM_INSTRUCTION,
    )

    chat = model.start_chat(enable_automatic_function_calling=False)

    print("\n" + "=" * 60)
    print("🚢 Supply Chain Intelligence CLI Assistant (Gemini 1.5 Flash)")
    print("Type your question below (or 'exit' to quit).")
    print("=" * 60 + "\n")

    while True:
        try:
            user_input = input("\n👤 User: ").strip()
            if not user_input:
                continue
            if user_input.lower() in ["exit", "quit"]:
                print("Exiting assistant.")
                break

            log_interaction("user_message", {"query": user_input})
            response = chat.send_message(user_input)

            max_turns = 4
            turn = 0

            while turn < max_turns:
                turn += 1

                function_calls = []
                for part in response.candidates[0].content.parts:
                    if fn := part.function_call:
                        function_calls.append(fn)

                if not function_calls:
                    reply = response.text
                    print(f"\n🤖 Assistant: {reply}")
                    log_interaction("assistant_reply", {"content": reply})
                    break

                tool_response_parts = []
                for fn in function_calls:
                    fn_name = fn.name
                    fn_args = dict(fn.args)
                    print(f"⚙️ Calling Tool: {fn_name}({fn_args})")

                    log_interaction(
                        "tool_call_start", {"tool": fn_name, "args": fn_args}
                    )

                    tool_fn = TOOLS_MAP.get(fn_name)
                    if tool_fn:
                        tool_result = tool_fn(**fn_args)
                    else:
                        tool_result = json.dumps(
                            {"error": f"Tool '{fn_name}' not recognized"}
                        )

                    log_interaction(
                        "tool_call_end",
                        {"tool": fn_name, "result": tool_result},
                    )

                    tool_response_parts.append(
                        genai.protos.Part(
                            function_response=genai.protos.FunctionResponse(
                                name=fn_name,
                                response={"result": tool_result},
                            )
                        )
                    )

                response = chat.send_message(tool_response_parts)

        except (KeyboardInterrupt, EOFError):
            print("\nExiting assistant.")
            break
        except Exception as e:
            print(f"\n❌ Error: {e}")


if __name__ == "__main__":
    run_assistant()
    
    
    