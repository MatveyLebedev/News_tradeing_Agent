#!/usr/bin/env python3
"""
Setup script to create .env file for secure secret storage
"""
import os
from pathlib import Path

def create_env_file():
    """Create .env file from .env.example template"""
    
    root_dir = Path(__file__).parent
    env_example = root_dir / '.env.example'
    env_file = root_dir / '.env'
    
    if env_file.exists():
        response = input(f".env file already exists. Overwrite? (y/N): ")
        if response.lower() != 'y':
            print("Cancelled. Existing .env file preserved.")
            return
    
    if not env_example.exists():
        print("❌ .env.example not found. Creating template...")
        template = """# Trading Agent Environment Variables
# Fill in your actual values below

# Database Configuration (REQUIRED)
DB_HOST=
DB_NAME=trade_agent
DB_USER=
DB_PASSWORD=
DB_PORT=5432

# Open Router Configuration (REQUIRED)
# Get your API key at https://openrouter.ai/keys
OPENROUTER_API_KEY=

# LangSmith Configuration (OPTIONAL)
LANGCHAIN_TRACING_V2=false
LANGCHAIN_API_KEY=
LANGCHAIN_PROJECT=news-trading-agent

# LLM Models — ЕДИНАЯ ТОЧКА СМЕНЫ МОДЕЛЕЙ (меняй только здесь)
LLM_MODEL=google/gemini-3-flash-preview
GRAPH_LLM_MODEL=google/gemini-3.1-flash-lite-preview
EMBEDDING_MODEL=google/gemini-embedding-001
EMBEDDING_DIMENSIONS=1536
"""
        env_example.write_text(template)
        print(f"✓ Created {env_example}")
    
    # Copy example to .env
    env_content = env_example.read_text()
    env_file.write_text(env_content)
    
    print(f"\n✓ Created {env_file}")
    print("\n📝 Next steps:")
    print(f"   1. Edit {env_file} and fill in your secrets")
    print("   2. Make sure .env is in .gitignore (already added)")
    print("   3. Never commit .env to git!\n")
    
    # Show which values need to be filled
    required_vars = [
        "DB_HOST", "DB_USER", "DB_PASSWORD",
        "OPENROUTER_API_KEY"
    ]
    
    print("⚠️  Required variables to fill:")
    for var in required_vars:
        if var in env_content and "your_" in env_content or var not in env_content:
            print(f"   - {var}")

if __name__ == "__main__":
    create_env_file()
