FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# Install curl only (required for docker-compose healthchecks)
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Cache dependency layer
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy only runtime code and datasets (tests omitted)
COPY src/ ./src/
COPY data/ ./data/

# Run preprocessing and model generation
RUN python -m src.data.dq_check --input ./data \
    && python -m src.data.pipeline --input ./data \
    && python -m src.ml.train

EXPOSE 8000

CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]