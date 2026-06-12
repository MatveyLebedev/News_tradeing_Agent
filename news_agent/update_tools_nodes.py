#!/usr/bin/env python3
"""
Helper script to update all tools and nodes to use Pydantic schemas
This provides a checklist and validation helper
"""

import os
from pathlib import Path
from typing import List, Tuple


def check_file_uses_pydantic(filepath: Path) -> Tuple[bool, List[str]]:
    """
    Check if a file imports and uses Pydantic schemas
    Returns: (uses_pydantic, issues_found)
    """
    issues = []
    
    if not filepath.exists():
        return False, ["File does not exist"]
    
    content = filepath.read_text()
    
    # Check for schema imports
    has_schema_import = "from ..schemas import" in content or "from news_agent.schemas import" in content
    
    # Check for old Dict/Any returns
    has_dict_return = "-> Dict[" in content or "-> dict:" in content or "-> Dict:" in content
    
    # Check for Pydantic model usage
    has_pydantic_models = any([
        "Output(" in content,  # Creating Pydantic instances
        ".model_dump" in content,
        "BaseModel" in content
    ])
    
    if not has_schema_import:
        issues.append("❌ No schema imports found")
    
    if has_dict_return:
        issues.append("⚠️  Still uses Dict return types (should use Pydantic)")
    
    if has_schema_import and not has_pydantic_models:
        issues.append("⚠️  Imports schemas but doesn't seem to use them")
    
    uses_pydantic = has_schema_import and has_pydantic_models and not has_dict_return
    
    return uses_pydantic, issues


def main():
    """Check all tools and nodes for Pydantic usage"""
    
    base_path = Path(__file__).parent
    
    tools_to_check = [
        "tools/ticker_extraction_tool.py",
        "tools/sentiment_analysis_tool.py",
        "tools/technical_analysis_tool.py",
        "tools/db_query_tool.py",
        "tools/sector_competitor_tool.py",
        "tools/historical_pattern_tool.py",
        "tools/volatility_volume_detector_tool.py",
        "tools/risk_assessment_tool.py",
        "tools/get_recent_news.py",
        "tools/get_news_by_id.py",
    ]
    
    nodes_to_check = [
        "agent/nodes.py",
    ]
    
    other_files = [
        "agent/state.py",
        "trading_agent.py",
    ]
    
    print("=" * 80)
    print("PYDANTIC MIGRATION STATUS")
    print("=" * 80)
    
    # Check tools
    print("\n📦 TOOLS:")
    print("-" * 80)
    for tool_path in tools_to_check:
        full_path = base_path / tool_path
        uses_pydantic, issues = check_file_uses_pydantic(full_path)
        
        status = "✅" if uses_pydantic else "🔄"
        print(f"{status} {tool_path}")
        
        if issues:
            for issue in issues:
                print(f"    {issue}")
    
    # Check nodes
    print("\n🔗 NODES:")
    print("-" * 80)
    for node_path in nodes_to_check:
        full_path = base_path / node_path
        uses_pydantic, issues = check_file_uses_pydantic(full_path)
        
        status = "✅" if uses_pydantic else "🔄"
        print(f"{status} {node_path}")
        
        if issues:
            for issue in issues:
                print(f"    {issue}")
    
    # Check other files
    print("\n📄 OTHER FILES:")
    print("-" * 80)
    for file_path in other_files:
        full_path = base_path / file_path
        uses_pydantic, issues = check_file_uses_pydantic(full_path)
        
        status = "✅" if uses_pydantic else "🔄"
        print(f"{status} {file_path}")
        
        if issues:
            for issue in issues:
                print(f"    {issue}")
    
    print("\n" + "=" * 80)
    print("LEGEND:")
    print("  ✅ - Migrated to Pydantic")
    print("  🔄 - Needs migration")
    print("  ❌ - Critical issue")
    print("  ⚠️  - Warning/suggestion")
    print("=" * 80)
    
    # Summary
    all_files = tools_to_check + nodes_to_check + other_files
    migrated = sum(1 for f in all_files if check_file_uses_pydantic(base_path / f)[0])
    total = len(all_files)
    
    print(f"\n📊 PROGRESS: {migrated}/{total} files migrated ({migrated/total*100:.1f}%)")
    
    if migrated == total:
        print("🎉 All files migrated to Pydantic!")
    else:
        print(f"\n📋 TODO: {total - migrated} files remaining")
        print("📖 See PYDANTIC_MIGRATION.md for migration guide")


if __name__ == "__main__":
    main()

