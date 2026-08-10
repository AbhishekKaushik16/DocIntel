from langchain_core.language_models.chat_models import BaseChatModel
from app.config import settings

def get_llm(model_type: str = "fast", temperature: float = 0.0) -> BaseChatModel:
    provider = settings.llm_provider.lower()
    
    if provider == "openrouter":
        from langchain_openai import ChatOpenAI
        model_name = settings.open_router_model if model_type == "fast" else settings.open_router_strong_model
        return ChatOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=settings.open_router_api_key,
            model=model_name,
            temperature=temperature,
            max_retries=6,
            timeout=60,
        )
    elif provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI
        model_name = settings.gemini_model if model_type == "fast" else settings.gemini_strong_model
        return ChatGoogleGenerativeAI(
            model=model_name,
            google_api_key=settings.gemini_api_key,
            temperature=temperature,
            max_retries=6,
            timeout=60,
        )
    elif provider == "openai":
        from langchain_openai import ChatOpenAI
        model_name = settings.openai_model if model_type == "fast" else settings.openai_strong_model
        return ChatOpenAI(
            api_key=settings.openai_api_key,
            model=model_name,
            temperature=temperature,
            max_retries=6,
            timeout=60,
        )
    
    raise ValueError(f"Unsupported LLM provider: {provider}")

def get_embeddings():
    """Get the embeddings model. Standardized strictly on Gemini to prevent vector space fragmentation."""
    if not settings.gemini_api_key:
        raise ValueError("GEMINI_API_KEY is required for generating embeddings, even if another provider is used for extraction.")
        
    from langchain_google_genai import GoogleGenerativeAIEmbeddings
    return GoogleGenerativeAIEmbeddings(
        model="models/text-embedding-004", 
        google_api_key=settings.gemini_api_key
    )
