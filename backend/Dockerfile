# dev image: dependencies only. docker-compose.yml mounts the repo at /app and starts uvicorn --reload
FROM python:3.12-slim

WORKDIR /app/backend
# the lock pins every package to the version the results were produced with; requirements.txt is the short list
COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir -r requirements.txt -c requirements.lock
