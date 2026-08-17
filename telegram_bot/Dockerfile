FROM python:3.11-slim

WORKDIR /app

COPY telegram_bot/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1

# `python telegram_bot/bot.py` puts /app/telegram_bot on sys.path — NOT /app — so the
# repo-root packages the bot imports (`news_agent`, `graph_db`, `core`) are invisible and
# startup dies on ModuleNotFoundError. The pre-2026-06 image predates that import and never
# hit it; any rebuild from the current tree does.
ENV PYTHONPATH=/app

# NOTE: bot.py calls load_dotenv("/app/.env"). That file is deliberately absent from the
# image (it is what leaked publicly — docs/SECURITY_INCIDENT_2026-08-11.md); the call is a
# harmless no-op and the environment arrives via `--env-file` at run time.
CMD ["python", "telegram_bot/bot.py"]
