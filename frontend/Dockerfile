# dev image: dependencies only. docker-compose.yml mounts the repo at /app (node_modules and .next stay in
# volumes, since the mac's native builds don't run in linux) and starts next dev
FROM node:25-slim

WORKDIR /app/frontend
COPY package.json package-lock.json ./
RUN npm ci
