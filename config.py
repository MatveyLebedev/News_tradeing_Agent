"""
Configuration file - loads from .env file for secure secret storage
"""
import os
from pathlib import Path

# Load .env file if it exists
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent / '.env'
    if env_path.exists():
        load_dotenv(env_path)
        print(f"✓ Loaded environment variables from {env_path}")
    else:
        # Try root directory
        root_env = Path(__file__).parent / '.env'
        if root_env.exists():
            load_dotenv(root_env)
            print(f"✓ Loaded environment variables from {root_env}")
except ImportError:
    print("⚠️  python-dotenv not installed. Install with: pip install python-dotenv")
except Exception as e:
    print(f"⚠️  Could not load .env file: {e}")

# Database configuration - from environment variables
DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "database": os.getenv("DB_NAME", "news"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "port": os.getenv("DB_PORT", "5432")
}

# LangSmith Configuration
LANGCHAIN_TRACING_V2 = False
LANGCHAIN_ENDPOINT = "https://api.smith.langchain.com"
LANGCHAIN_API_KEY = os.getenv("LANGCHAIN_API_KEY", "")