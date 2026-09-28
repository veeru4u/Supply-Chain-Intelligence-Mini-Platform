from pathlib import Path
import json
import logging
import os
import pickle
import time
from typing import Optional

import duckdb
import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _resolve_project_path(value: Optional[str], default_name: str) -> Path:
    if value is None or value == "":
        return PROJECT_ROOT / default_name
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    return (PROJECT_ROOT / candidate).resolve()


# Structured JSON Logging
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("supply_chain_api")

app = FastAPI(title="Supply Chain Intelligence API", version="1.0.0")
DB_PATH = str(_resolve_project_path(os.getenv("DB_PATH"), "supply_chain.db"))
MODEL_PATH = str(_resolve_project_path(os.getenv("MODEL_PATH"), "model.pkl"))
DQ_REPORT_PATH = str(_resolve_project_path(os.getenv("DQ_REPORT_PATH"), "dq_report.json"))

# Load ML Model at startup
ml_model = None
if Path(MODEL_PATH).exists():
    with open(MODEL_PATH, "rb") as f:
        ml_model = pickle.load(f)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    duration_ms = round((time.time() - start_time) * 1000, 2)

    log_payload = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "method": request.method,
        "path": request.url.path,
        "status_code": response.status_code,
        "latency_ms": duration_ms,
    }
    logger.info(json.dumps(log_payload))
    return response


def get_db():
    return duckdb.connect(DB_PATH, read_only=True)


# --- Endpoints ---


@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "version": "1.0.0",
        "model_loaded": ml_model is not None,
    }


@app.get("/data-quality/report")
def get_dq_report():
    if Path(DQ_REPORT_PATH).exists():
        with open(DQ_REPORT_PATH, "r") as f:
            return json.load(f)
    raise HTTPException(status_code=404, detail="Data quality report not found")


@app.get("/shipments")
def list_shipments(
    origin: Optional[str] = Query(None, description="Filter by origin port code"),
    destination: Optional[str] = Query(
        None, description="Filter by destination port code"
    ),
    status: Optional[str] = Query(None, description="Filter by shipment status"),
    start_date: Optional[str] = Query(
        None, description="Min booking_date (YYYY-MM-DD)"
    ),
    end_date: Optional[str] = Query(None, description="Max booking_date (YYYY-MM-DD)"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    conn = get_db()
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
    if start_date:
        conditions.append("CAST(booking_date AS DATE) >= ?")
        params.append(start_date)
    if end_date:
        conditions.append("CAST(booking_date AS DATE) <= ?")
        params.append(end_date)

    where_clause = " AND ".join(conditions)
    query = f"""
        SELECT * FROM curated_shipments
        WHERE {where_clause}
        LIMIT {limit} OFFSET {offset}
    """
    df = conn.execute(query, params).fetchdf()

    total_query = f"SELECT COUNT(*) FROM curated_shipments WHERE {where_clause}"
    total_count = conn.execute(total_query, params).fetchone()[0]
    conn.close()

    records = df.fillna("").to_dict(orient="records")
    return {
        "total": total_count,
        "limit": limit,
        "offset": offset,
        "data": records,
    }


@app.get("/shipments/{id}")
def get_shipment(id: str):
    conn = get_db()
    result = conn.execute(
        "SELECT * FROM curated_shipments WHERE shipment_id = ?", [id]
    ).fetchdf()
    conn.close()

    if result.empty:
        raise HTTPException(
            status_code=404, detail=f"Shipment with ID '{id}' not found"
        )

    return result.fillna("").to_dict(orient="records")[0]


@app.get("/routes/{origin}/{destination}/stats")
def get_route_stats(origin: str, destination: str):
    conn = get_db()
    route_key = f"{origin}->{destination}"
    query = """
        SELECT
            COUNT(*) AS shipment_count,
            AVG(actual_delay_hours) AS avg_delay_hours,
            AVG(CASE WHEN on_time_flag THEN 1.0 ELSE 0.0 END) AS on_time_rate
        FROM curated_shipments
        WHERE route_key = ? AND actual_delay_hours IS NOT NULL
    """
    res = conn.execute(query, [route_key]).fetchone()
    conn.close()

    if not res or res[0] == 0:
        raise HTTPException(
            status_code=404,
            detail=f"No completed shipments found for route {route_key}",
        )

    return {
        "route_key": route_key,
        "shipment_count": int(res[0]),
        "avg_delay_hours": round(float(res[1]), 2) if res[1] is not None else 0.0,
        "on_time_rate": round(float(res[2]), 4) if res[2] is not None else 0.0,
    }


class DelayPredictionRequest(BaseModel):
    shipment_id: Optional[str] = None
    cargo_type: str = "Standard"
    route_key: str
    weight_tons: float
    container_count: int
    transit_days_planned: int = 15
    booking_lead_days: int = 7
    departure_month: int = 6
    departure_dow: int = 2
    origin_congestion: float = 0.5
    dest_congestion: float = 0.5
    vessel_hist_delay_rate: float = 0.4
    route_hist_delay_rate: float = 0.4


@app.post("/predict-delay")
def predict_delay(payload: DelayPredictionRequest):
    if ml_model is None:
        raise HTTPException(
            status_code=503,
            detail="ML model is not available or failed to load",
        )

    input_data = pd.DataFrame([payload.model_dump()])
    prob_delayed = float(ml_model.predict_proba(input_data)[0][1])

    return {
        "shipment_id": payload.shipment_id,
        "delay_risk_probability": round(prob_delayed, 4),
        "predicted_delayed_gt_24h": bool(prob_delayed > 0.5),
        "risk_tier": (
            "HIGH"
            if prob_delayed >= 0.6
            else "MEDIUM"
            if prob_delayed >= 0.4
            else "LOW"
        ),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
    
    
    