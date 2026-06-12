FROM python:3.11-slim

WORKDIR /app

COPY telegram_bot/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
CMD ["python", "telegram_bot/bot.py"]
