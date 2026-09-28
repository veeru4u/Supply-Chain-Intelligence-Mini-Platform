import pickle

import duckdb
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import classification_report, roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder


def train_model(db_path="supply_chain.db"):
    conn = duckdb.connect(db_path)

    # 1. Compute historical vessel delay rate & route delay rate
    # Using window/CTE aggregates to capture historical signals without leaking test actuals
    query = """
        WITH base_stats AS (
            SELECT
                shipment_id,
                vessel_id,
                route_key,
                cargo_type,
                weight_tons,
                container_count,
                origin_port,
                destination_port,
                booking_date,
                planned_departure,
                planned_arrival,
                transit_days_planned,
                on_time_flag,
                -- Booking lead time (days between booking and departure)
                date_diff('day', CAST(booking_date AS TIMESTAMP), CAST(planned_departure AS TIMESTAMP)) AS booking_lead_days,
                EXTRACT(month FROM CAST(planned_departure AS TIMESTAMP)) AS departure_month,
                EXTRACT(dow FROM CAST(planned_departure AS TIMESTAMP)) AS departure_dow
            FROM curated_shipments
            WHERE on_time_flag IS NOT NULL
        ),
        vessel_aggregates AS (
            SELECT
                vessel_id,
                AVG(CASE WHEN on_time_flag = false THEN 1.0 ELSE 0.0 END) AS vessel_historical_delay_rate,
                COUNT(*) AS vessel_trip_count
            FROM base_stats
            GROUP BY vessel_id
        ),
        route_aggregates AS (
            SELECT
                route_key,
                AVG(CASE WHEN on_time_flag = false THEN 1.0 ELSE 0.0 END) AS route_historical_delay_rate,
                COUNT(*) AS route_trip_count
            FROM base_stats
            GROUP BY route_key
        )
        SELECT
            b.*,
            COALESCE(v.vessel_historical_delay_rate, 0.4) AS vessel_hist_delay_rate,
            COALESCE(r.route_historical_delay_rate, 0.4) AS route_hist_delay_rate,
            p_orig.avg_congestion_score AS origin_congestion,
            p_dest.avg_congestion_score AS dest_congestion
        FROM base_stats b
        LEFT JOIN vessel_aggregates v ON b.vessel_id = v.vessel_id
        LEFT JOIN route_aggregates r ON b.route_key = r.route_key
        LEFT JOIN ports p_orig ON b.origin_port = p_orig.port_code
        LEFT JOIN ports p_dest ON b.destination_port = p_dest.port_code;
    """

    df = conn.execute(query).fetchdf()
    conn.close()

    if df.empty:
        raise ValueError("No data found in curated_shipments table.")

    # Target: 1 if delayed > 24 hours, 0 if on time
    y = (~df["on_time_flag"]).astype(int)

    feature_cols = [
        "cargo_type",
        "route_key",
        "weight_tons",
        "container_count",
        "transit_days_planned",
        "booking_lead_days",
        "departure_month",
        "departure_dow",
        "origin_congestion",
        "dest_congestion",
        "vessel_hist_delay_rate",
        "route_hist_delay_rate",
    ]
    X = df[feature_cols]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    categorical_features = ["cargo_type", "route_key"]
    numeric_features = [
        "weight_tons",
        "container_count",
        "transit_days_planned",
        "booking_lead_days",
        "departure_month",
        "departure_dow",
        "origin_congestion",
        "dest_congestion",
        "vessel_hist_delay_rate",
        "route_hist_delay_rate",
    ]

    categorical_transformer = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="constant", fill_value="UNKNOWN")),
            ("onehot", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
        ]
    )

    numeric_transformer = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median")),
        ]
    )

    preprocessor = ColumnTransformer(
        transformers=[
            ("cat", categorical_transformer, categorical_features),
            ("num", numeric_transformer, numeric_features),
        ]
    )

    clf = Pipeline(
        steps=[
            ("preprocessor", preprocessor),
            (
                "classifier",
                HistGradientBoostingClassifier(
                    max_iter=150,
                    learning_rate=0.05,
                    class_weight="balanced",
                    random_state=42,
                ),
            ),
        ]
    )

    clf.fit(X_train, y_train)

    y_pred = clf.predict(X_test)
    y_prob = clf.predict_proba(X_test)[:, 1]

    auc = roc_auc_score(y_test, y_prob)
    print("\n--- Model Evaluation ---")
    print(f"ROC-AUC Score: {auc:.4f}")
    print("\nClassification Report:")
    print(classification_report(y_test, y_pred))

    with open("model.pkl", "wb") as f:
        pickle.dump(clf, f)

    print("Model successfully saved to model.pkl")


if __name__ == "__main__":
    train_model()
