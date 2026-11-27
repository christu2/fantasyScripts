FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY cloud_run/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy only the FantasyRecap Python package + static web_hub assets (never secrets/DBs!)
COPY . /app/FantasyRecap
RUN rm -rf /app/FantasyRecap/live_state /app/FantasyRecap/output /app/FantasyRecap/tests /app/FantasyRecap/.pytest_cache \
    && mkdir -p /app/FantasyRecap/live_state/telemetry /app/FantasyRecap/web_hub/leagues

ENTRYPOINT ["python3", "/app/FantasyRecap/cloud_run/entrypoint.py"]
