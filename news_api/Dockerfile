# Read-only news API — build from repo root:
#   docker build -f news_api/Dockerfile -t news-api .
FROM python:3.11-slim

WORKDIR /app

COPY news_api/requirements.txt /app/news_api/requirements.txt
RUN pip install --no-cache-dir -r /app/news_api/requirements.txt

COPY news_api/ /app/news_api/

EXPOSE 8000
CMD ["uvicorn", "news_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
