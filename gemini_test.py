import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

load_dotenv()

def test_llm_api():
    """Test call to LLM API via Open Router using LangChain"""
    
    llm = ChatOpenAI(
        api_key=os.getenv("OPENROUTER_API_KEY"),
        base_url="https://openrouter.ai/api/v1",
        model="google/gemini-2.0-flash",
        temperature=0,
    )
    
    prompt = """How this news can impact market price: Super Micro Computer stock fell as much as 5% after the bell Tuesday after the company lowered its full-year revenue outlook and missed Wall Street's expectations for third quarter earnings.
The AI server maker and Nvidia (NVDA) customer said it expects its full-year revenue for 2025 to fall between $21.8 billion and $22.6 billion, down from its prior guidance of $23.5 billion to $25 billion.
Super Micro had previously moderated expectations ahead of its third quarter earnings report, lowering its projected range for its financial results for the March period in a regulatory filing last week. The update sent the stock plummeting 11.5% the following day.
As of Tuesday's close, Super Micro shares were down nearly 39% in 2025. The stock has plunged as investors weigh the company's AI-fueled growth against questionable accounting practices, growing competition, and macroeconomic uncertainties.


Answer in json with ticker (example BRN, TSLA) names reason and price change in prosentage (example -15%, +3%) in 10 min after news publication"""
    
    print("Sending prompt...")
    print("-" * 50)
    
    try:
        response = llm.invoke(prompt)
        print(response.content)
        print("-" * 50)
        print("Test successful!")
        
    except Exception as e:
        print(f"Error calling LLM API: {e}")

if __name__ == "__main__":
    test_llm_api()
