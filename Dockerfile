FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Set timezone to US Eastern
ENV TZ=America/New_York
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY price_parser.py .
COPY auto_price_fetcher.py .
COPY graph_db/ graph_db/

# Create log directory
RUN mkdir -p /app/logs

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Run the automated fetcher
CMD ["python", "-u", "auto_price_fetcher.py"]










