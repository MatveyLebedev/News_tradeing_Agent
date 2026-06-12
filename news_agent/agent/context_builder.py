"""
Context management utilities for the trading agent - Message-Based Architecture

Handles message-based state and context accumulation using LangChain message format
"""
from typing import Dict, Any, List, Optional
from datetime import datetime
import json
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage, ToolMessage


# ============================================================================
# Message Utilities
# ============================================================================

def add_message(
    state: Dict[str, Any],
    role: str,
    content: str,
    name: Optional[str] = None,
    data: Optional[Dict[str, Any]] = None
) -> None:
    """
    Add a message to the state's message history using LangChain format
    
    Args:
        state: Current agent state
        role: Message role ("system", "human", "ai", "tool")
        content: Message content/reasoning
        name: Optional name (node name or tool name)
        data: Optional structured data (stored in additional_kwargs)
    """
    if "messages" not in state:
        state["messages"] = []
    
    # Create appropriate LangChain message type
    additional_kwargs = {}
    if name:
        additional_kwargs["name"] = name
    if data:
        additional_kwargs["data"] = data
    
    if role == "system":
        message = SystemMessage(content=content, additional_kwargs=additional_kwargs)
    elif role == "human":
        message = HumanMessage(content=content, additional_kwargs=additional_kwargs)
    elif role == "ai" or role == "assistant":
        message = AIMessage(content=content, additional_kwargs=additional_kwargs)
    elif role == "tool":
        # ToolMessage requires tool_call_id, use name as fallback
        tool_call_id = additional_kwargs.get("tool_call_id", name or "unknown")
        message = ToolMessage(content=content, tool_call_id=tool_call_id, additional_kwargs=additional_kwargs)
    else:
        # Default to AIMessage
        message = AIMessage(content=content, additional_kwargs=additional_kwargs)
    
    state["messages"].append(message)


def get_messages_for_llm(state: Dict[str, Any], max_messages: Optional[int] = None) -> str:
    """
    Format LangChain messages for LLM consumption
    
    Args:
        state: Current agent state
        max_messages: Optional limit on number of recent messages
        
    Returns:
        Formatted message history string
    """
    messages = state.get("messages", [])
    
    if max_messages:
        messages = messages[-max_messages:]
    
    if not messages:
        return "No previous messages."
    
    formatted_parts = ["=== Message History ===\n"]
    
    for i, msg in enumerate(messages, 1):
        # Get message type
        msg_type = msg.__class__.__name__.replace("Message", "").upper()
        content = msg.content
        
        # Get name from additional_kwargs if available
        name = msg.additional_kwargs.get("name", "")
        
        if name:
            formatted_parts.append(f"{i}. [{msg_type}:{name}] {content}")
        else:
            formatted_parts.append(f"{i}. [{msg_type}] {content}")
    
    return "\n".join(formatted_parts)


def get_last_n_messages(state: Dict[str, Any], n: int) -> List[BaseMessage]:
    """
    Get the last N messages from state
    
    Args:
        state: Current agent state
        n: Number of recent messages to retrieve
        
    Returns:
        List of last N LangChain messages
    """
    messages = state.get("messages", [])
    return messages[-n:] if len(messages) >= n else messages


def format_messages_compact(messages: List[BaseMessage]) -> str:
    """
    Format LangChain messages in a compact format
    
    Args:
        messages: List of LangChain messages
        
    Returns:
        Compact formatted string
    """
    if not messages:
        return "No messages"
    
    parts = []
    for msg in messages:
        msg_type = msg.__class__.__name__.replace("Message", "").upper()
        name = msg.additional_kwargs.get("name", "")
        content = msg.content[:100] if msg.content else ""  # Truncate long content
        
        if name:
            parts.append(f"[{msg_type}:{name}] {content}...")
        else:
            parts.append(f"[{msg_type}] {content}...")
    
    return " → ".join(parts)


# ============================================================================
# Legacy Context Utilities (kept for backward compatibility)
# ============================================================================

def build_context_prompt(state: Dict[str, Any]) -> str:
    """
    Build a formatted context prompt from message history
    
    Args:
        state: Current agent state with messages
        
    Returns:
        Formatted context string for LLM
    """
    # Use message-based approach
    return get_messages_for_llm(state)


def format_tool_results(results: Dict[str, Any]) -> str:
    """
    Format tool results for context display
    
    Args:
        results: Dictionary of tool results
        
    Returns:
        Formatted string representation
    """
    if not results:
        return "No tool calls"
    
    formatted_parts = []
    
    for key, value in results.items():
        if isinstance(value, dict):
            # Format dict results compactly
            formatted_parts.append(f"{key}: {json.dumps(value, indent=2)[:200]}...")
        elif isinstance(value, list):
            formatted_parts.append(f"{key}: {len(value)} items")
        else:
            formatted_parts.append(f"{key}: {value}")
    
    return "; ".join(formatted_parts)


def execute_tool_calls(tool_calls: List[Any], tools_dict: Dict[str, Any]) -> Dict[str, Any]:
    """
    Execute tool calls requested by LLM
    
    Args:
        tool_calls: List of tool calls from LLM response
        tools_dict: Dictionary mapping tool names to tool functions
        
    Returns:
        Dictionary of tool results
    """
    results = {}
    
    if not tool_calls:
        return results
    
    for tool_call in tool_calls:
        tool_name = tool_call.get("name") if isinstance(tool_call, dict) else getattr(tool_call, "name", None)
        tool_args = tool_call.get("args", {}) if isinstance(tool_call, dict) else getattr(tool_call, "args", {})
        
        if tool_name in tools_dict:
            try:
                tool_func = tools_dict[tool_name]
                result = tool_func(**tool_args)
                results[tool_name] = result
            except Exception as e:
                results[tool_name] = {"error": str(e)}
                print(f"Error executing tool {tool_name}: {e}")
        else:
            print(f"Warning: Tool {tool_name} not found in tools_dict")
    
    return results


def add_context_entry(
    state: Dict[str, Any],
    node_name: str,
    reasoning: str,
    tool_calls: Optional[Dict[str, Any]] = None
) -> None:
    """
    Add a context entry (legacy - now uses messages)
    
    Args:
        state: Current agent state
        node_name: Name of the current node
        reasoning: LLM reasoning/explanation
        tool_calls: Results from tool calls (optional)
    """
    # Convert to message format
    data = {}
    if tool_calls:
        data["tool_calls"] = tool_calls
    
    add_message(state, "assistant", reasoning, name=node_name, data=data)


def format_context_for_llm(state: Dict[str, Any], include_keys: Optional[List[str]] = None) -> str:
    """
    Format full state context for LLM consumption
    
    Args:
        state: Current agent state
        include_keys: Optional list of state keys to include
        
    Returns:
        Formatted context string
    """
    parts = []
    
    # Add news text
    if "news_text" in state:
        parts.append(f"NEWS ARTICLE:\n{state['news_text']}\n")
    
    # Add timestamp
    if "timestamp" in state:
        parts.append(f"TIMESTAMP: {state['timestamp']}\n")
    
    # Add tickers if available
    if "tickers" in state and state["tickers"]:
        parts.append(f"IDENTIFIED TICKERS: {', '.join(state['tickers'])}\n")
    
    # Add message history
    message_history = get_messages_for_llm(state)
    parts.append(f"\n{message_history}\n")
    
    # Add specific state keys if requested
    if include_keys:
        for key in include_keys:
            if key in state and state[key]:
                parts.append(f"\n{key.upper()}: {state[key]}")
    
    return "\n".join(parts)


def summarize_context_history(state: Dict[str, Any], max_entries: int = 5) -> str:
    """
    Create a summarized version of message history
    
    Args:
        state: Current agent state
        max_entries: Maximum number of recent messages to include
        
    Returns:
        Summarized context string
    """
    messages = get_last_n_messages(state, max_entries)
    
    if not messages:
        return "No previous context"
    
    summary_parts = [f"Recent messages (last {len(messages)}):"]
    
    for msg in messages:
        msg_type = msg.__class__.__name__.replace("Message", "").upper()
        name = msg.additional_kwargs.get("name", "")
        content = msg.content[:100] if msg.content else ""  # Truncate
        
        if name:
            summary_parts.append(f"- [{msg_type}:{name}] {content}...")
        else:
            summary_parts.append(f"- [{msg_type}] {content}...")
    
    return "\n".join(summary_parts)


# ============================================================================
# New Utilities for Message-Based Architecture
# ============================================================================

def get_messages_by_role(state: Dict[str, Any], role: str) -> List[BaseMessage]:
    """
    Get all messages with a specific role/type
    
    Args:
        state: Current agent state
        role: Role to filter by (e.g., "AI", "Human", "System", "Tool")
        
    Returns:
        List of LangChain messages with that role
    """
    messages = state.get("messages", [])
    role_upper = role.upper()
    return [msg for msg in messages if msg.__class__.__name__.replace("Message", "").upper() == role_upper]


def get_messages_by_name(state: Dict[str, Any], name: str) -> List[BaseMessage]:
    """
    Get all messages from a specific node/tool
    
    Args:
        state: Current agent state
        name: Node or tool name
        
    Returns:
        List of LangChain messages from that node/tool
    """
    messages = state.get("messages", [])
    return [msg for msg in messages if msg.additional_kwargs.get("name") == name]


def format_plan_for_llm(plan: Dict[str, Any]) -> str:
    """
    Format action plan for LLM display
    
    Args:
        plan: Action plan dictionary
        
    Returns:
        Formatted plan string
    """
    if not plan:
        return "No plan available"
    
    parts = ["=== Action Plan ==="]
    
    if "steps" in plan:
        parts.append("\nSteps:")
        for i, step in enumerate(plan["steps"], 1):
            parts.append(f"{i}. {step}")
    
    if "reasoning" in plan:
        parts.append(f"\nReasoning: {plan['reasoning']}")
    
    return "\n".join(parts)


def count_messages_by_node(state: Dict[str, Any]) -> Dict[str, int]:
    """
    Count messages per node for debugging
    
    Args:
        state: Current agent state
        
    Returns:
        Dict mapping node names to message counts
    """
    messages = state.get("messages", [])
    counts = {}
    
    for msg in messages:
        name = msg.get("name", "unknown")
        counts[name] = counts.get(name, 0) + 1
    
    return counts
