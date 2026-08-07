import asyncio
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage
import os
from dotenv import load_dotenv

load_dotenv()

async def test_model(model_name):
    print(f"Testing model: {model_name}")
    try:
        llm = ChatGoogleGenerativeAI(
            model=model_name,
            google_api_key=os.getenv("GEMINI_API_KEY"),
        )
        response = await llm.ainvoke([HumanMessage(content="Return a JSON object with key 'status' and value 'ok'.")])
        print("Success:", response.content)
        return True
    except Exception as e:
        print(f"Error for {model_name}:", type(e), e)
        return False

async def main():
    models = ["gemini-3.6-flash", "gemini-2.5-flash", "gemini-2.0-flash"]
    for model in models:
        await test_model(model)

if __name__ == "__main__":
    asyncio.run(main())
