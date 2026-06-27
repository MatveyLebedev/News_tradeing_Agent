docker run -d \
  --name neo4j \
  --restart unless-stopped \
  -p 7474:7474 \
  -p 7687:7687 \
  -e NEO4J_AUTH="neo4j/your_password_here" \
  -e NEO4J_server_memory_heap_initial__size=256m \
  -e NEO4J_server_memory_heap_max__size=512m \
  -e NEO4J_server_memory_pagecache_size=64m \
  -v neo4j_data:/data \
  --log-driver json-file --log-opt max-size=50m --log-opt max-file=5 \
  neo4j:5

# Note: the global /etc/docker/daemon.json cap (deploy/DISK_SAFETY.md) also covers
# this container; the --log-opt flags above are belt-and-suspenders.

  