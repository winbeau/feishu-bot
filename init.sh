#!/usr/bin/env bash
set -euo pipefail

if ! uv --version >/dev/null 2>&1; then
  echo "uv is required. Install it from https://docs.astral.sh/uv/getting-started/installation/" >&2
  exit 1
fi

uv sync --group dev

if docker-compose version >/dev/null 2>&1; then
  COMPOSE_CMD=(docker-compose)
elif docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD=(docker compose)
else
  echo "Docker Compose is required. Install docker-compose or the docker compose plugin." >&2
  exit 1
fi

"${COMPOSE_CMD[@]}" up -d redis

uv run pytest tests/ -q
