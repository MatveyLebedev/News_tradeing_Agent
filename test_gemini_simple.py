import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

load_dotenv()

def test_llm():
    """Simple test to connect and send a test request via Open Router"""
    
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        print("Set OPENROUTER_API_KEY in .env (get key at https://openrouter.ai/keys)")
        return
    
    model_name = os.getenv("LLM_MODEL")
    temperature = float(os.getenv("LLM_TEMPERATURE", "0"))
    
    print(f"✓ Using model: {model_name} (via Open Router)")
    
    try:
        llm = ChatOpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
            model=model_name,
            temperature=temperature,
        )
        
        test_prompt = "Hello! Please respond with a short message confirming you're working."
        print(f"\nSending test request...")
        print(f"Prompt: {test_prompt}\n")
        
        response = llm.invoke(test_prompt)
        
        print("Response received:")
        print("-" * 50)
        print(response.content)
        print("-" * 50)
        print("\nTest successful!")
        
    except Exception as e:
        print(f"\nError: {e}")
        print(f"Error type: {type(e).__name__}")

if __name__ == "__main__":
    test_llm()
