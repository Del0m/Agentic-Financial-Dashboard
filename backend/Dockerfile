# dev image: dependencies only. docker-compose.yml mounts the repo at /app and starts uvicorn --reload
FROM python:3.12-slim

WORKDIR /app/backend
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
