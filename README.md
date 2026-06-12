# Stock Price Fetcher

Automated daily fetcher for 1-minute stock price data. Runs at 10 PM ET (6 hours after market close) and saves to PostgreSQL.

## Remote Access & Deployment

### 1. Login to Remote Server
To access your remote server via SSH:
```bash
ssh root@YOUR_SERVER_IP
```

### 2. Build and Push to Remote
If you are developing locally and want to push your images to a registry (e.g., Docker Hub) to be pulled on the remote server:
```bash
# 1. Login to your registry
docker login

# 2. Build and tag the image
# For remote servers (usually x86_64), use --platform linux/amd64
docker build --platform linux/amd64 -t matveylebedev891/stock-fetcher:latest -f bloomberg_news_fetcher/Dockerfile.news .

# 3. Push to registry
docker push matveylebedev891/stock-fetcher:latest
```

### 3. Start Application (Remote/Local)
Navigate to the project directory and use Docker to start the services:


#### Using Docker Run (Single Command)
If you want to run the container without Compose:
```bash
# 1. Stop and remove existing container (if any)
docker stop stock-fetcher 2>/dev/null; docker rm stock-fetcher 2>/dev/null

# 2. Pull the image
docker pull matveylebedev891/stock-fetcher:latest

# 3. Run the container
docker run -d \
  --name stock-fetcher \
  --restart unless-stopped \
  -e TZ=America/New_York \
  -e FETCH_INTERVAL_MIN=5 \
  -e FETCH_PRICES=true \
  -e DB_HOST="YOUR_SERVER_IP" \
  -e DB_NAME="trade_agent" \
  -e DB_USER="news" \
  -e DB_PORT="5432" \
  --log-driver json-file \
  --log-opt max-size=10m \
  --log-opt max-file=3 \
  -e VERBOSE=false \
  -e FETCH_FULL_TEXT=false \
  -e GRAPH_LLM_MODEL="google/gemini-3.1-flash-lite-preview" \
  -e NEO4J_URI="bolt://YOUR_SERVER_IP:7687" \
  -e NEO4J_USER="neo4j" \
  -e NEO4J_PASSWORD="pass" \
  -e OPENROUTER_API_KEY="your_openrouter_key" \
  -e DB_PASSWORD="your_db_password" \
  matveylebedev891/stock-fetcher:latest
```

## Local Usage Summary

To use this repository locally, ensure you have **Docker** and **Docker Compose** installed.

1. **Setup**: Clone the repository and navigate into it.
2. **Launch**: Run `docker compose up -d`. This starts both the PostgreSQL database and the fetcher service.
3. **Monitor**: Check logs to ensure data is being fetched:
   ```bash
   docker compose logs -f price-fetcher
   ```
4. **Manual Trigger**: If you don't want to wait for the schedule, run a manual fetch:
   ```bash
   docker compose exec price-fetcher python auto_price_fetcher.py --once
   ```
5. **Stop**: To stop all services:
   ```bash
   docker compose down
   ```

## Configuration
Edit `docker-compose.yml` environment variables to customize:
- `FETCH_TIME`: Daily run time (default: 22:00 ET)
- `FETCH_INTERVAL`: Data interval (default: 1m)
- `FETCH_PERIOD`: Data period (default: 1d)
- Database credentials (DB_USER, DB_PASSWORD, etc.)

## Files
- `Dockerfile`: Container definition
- `docker-compose.yml`: Service orchestration (DB + Fetcher)
- `requirements.txt`: Python dependencies
- `price_parser.py`: Core logic for fetching prices
- `auto_price_fetcher.py`: Scheduling and execution logic
