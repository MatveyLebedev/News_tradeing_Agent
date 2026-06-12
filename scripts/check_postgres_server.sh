#!/bin/bash
# Run this ON THE SERVER (YOUR_SERVER_IP) over SSH to diagnose PostgreSQL connection issues.

echo "=== 1. Docker container status ==="
docker ps -a --filter "name=postgres" 2>/dev/null || echo "Docker not available"

echo ""
echo "=== 2. PostgreSQL container logs (last 30 lines) ==="
docker logs postgres --tail 30 2>&1 || true

echo ""
echo "=== 3. Is port 5432 listening? ==="
ss -tlnp 2>/dev/null | grep 5432 || netstat -tlnp 2>/dev/null | grep 5432 || echo "Port 5432 not found"

echo ""
echo "=== 4. Firewall (if ufw) ==="
ufw status 2>/dev/null | head -20 || echo "ufw not active or not installed"

echo ""
echo "=== 5. Test from localhost on server ==="
echo "Run: PGPASSWORD='YOUR_DB_PASSWORD' psql -h 127.0.0.1 -p 5432 -U news -d trade_agent -c 'SELECT 1;'"
echo "(Install psql if needed: apt install postgresql-client)"
