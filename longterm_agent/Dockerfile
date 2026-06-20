# Long-term agent image — used by BOTH the weekly scheduler and the stats bot
# (compose picks the entrypoint per service via `command:`).
# Build context is the repo ROOT (the agent imports news_agent, graph_db, model_config,
# and bloomberg_news_fetcher), e.g.:
#   docker build -f longterm_agent/Dockerfile -t longterm-agent .
FROM python:3.12-slim

WORKDIR /app

ENV TZ=America/New_York
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

RUN apt-get update && apt-get install -y \
    gcc \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY longterm_agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Whole repo (cross-module imports). .dockerignore keeps the image lean.
COPY . .

ENV PYTHONUNBUFFERED=1

# Default entrypoint = the weekly scheduler. The stats-bot service overrides `command:`.
CMD ["python", "-u", "-m", "longterm_agent.auto_longterm_agent"]
