#!/bin/bash
# Run PostgreSQL container matching .env: DB_PORT=5432, DB_USER=news, DB_NAME=trade_agent
# Run this script on the server (e.g. over SSH).

set -e

CONTAINER_NAME="postgres"
IMAGE="postgres"
PORT="${DB_PORT:-5432}"
POSTGRES_USER="${DB_USER:-news}"
POSTGRES_DB="${DB_NAME:-trade_agent}"
# Set DB_PASSWORD in env or below
POSTGRES_PASSWORD="${DB_PASSWORD:?Set DB_PASSWORD in .env or export it}"

# Remove old container if it exists (optional)
docker rm -f "$CONTAINER_NAME" 2>/dev/null || true

docker run -d \
  --name "$CONTAINER_NAME" \
  --restart unless-stopped \
  -e POSTGRES_USER="$POSTGRES_USER" \
  -e POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
  -e POSTGRES_DB="$POSTGRES_DB" \
  -p "${PORT}:5432" \
  -v postgres_data:/var/lib/postgresql/data \
  "$IMAGE"

echo "PostgreSQL container started. Port: $PORT, User: $POSTGRES_USER, DB: $POSTGRES_DB"
docker ps --filter "name=$CONTAINER_NAME"
