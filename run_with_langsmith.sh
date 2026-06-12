#!/bin/bash
# Quick script to run the trading agent with LangSmith enabled

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}======================================================================${NC}"
echo -e "${GREEN}✓ Activating trading-agent environment${NC}"
echo -e "${BLUE}======================================================================${NC}"

# Source conda
source /opt/miniconda3/etc/profile.d/conda.sh

# Activate environment
conda activate trading-agent

# Set LangSmith environment variables
export LANGCHAIN_TRACING_V2=true
export LANGCHAIN_API_KEY=REMOVED_LANGSMITH_KEY
export LANGCHAIN_PROJECT=news-trading-agent
export LANGCHAIN_ENDPOINT=https://api.smith.langchain.com

echo -e "${GREEN}✓ LangSmith tracing enabled${NC}"
echo -e "${GREEN}✓ Project: news-trading-agent${NC}"
echo -e "${BLUE}======================================================================${NC}"
echo ""

# Run the agent
python run_agent_with_langsmith.py

echo ""
echo -e "${BLUE}======================================================================${NC}"
echo -e "${GREEN}✓ View traces at: https://smith.langchain.com${NC}"
echo -e "${BLUE}======================================================================${NC}"

