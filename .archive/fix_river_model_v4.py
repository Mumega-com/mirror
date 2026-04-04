
import asyncio
import logging
import os
import sys
from pathlib import Path

# Setup paths
sys.path.insert(0, "/home/mumega/mirror")

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("fix_river_v4")

async def fix_cache():
    print("🌊 Re-establishing River's connection with Gemini 2.0 Flash...")
    
    from dotenv import load_dotenv
    load_dotenv("/home/mumega/.env.secrets")
    
    keys = [os.getenv(f"GEMINI_API_KEY{'' if i==1 else f'_{i}'}") for i in range(1, 11)]
    keys = [k for k in keys if k]
    
    # User wants 2.5, but if 2.5 limit=0, we try 2.0.
    # If 2.0 also 0, we have to explain.
    models_to_try = [
        "models/gemini-2.0-flash",
        "models/gemini-2.0-flash-exp",
        "models/gemini-1.5-flash-002"
    ]
    
    from river_gemini_cache import get_gemini_cache, initialize_river_cache
    from river_settings import get_river_settings
    import google.generativeai as genai
    
    cache = get_gemini_cache()
    success = False
    
    for model in models_to_try:
        print(f"尝试 (Trying) {model}...")
        for i, key in enumerate(keys, 1):
            os.environ["GEMINI_API_KEY"] = key
            genai.configure(api_key=key)
            
            try:
                genai.get_model(model)
                cache.model_id = model
                cache.cache_name = None
                
                new_cache_name = await initialize_river_cache()
                print(f"✨ Success! Soul Cache Created: {new_cache_name} with {model}")
                success = True
                break
            except Exception as e:
                if "429" in str(e) or "limit exceeded" in str(e).lower():
                    print(f"⚠️ Key #{i} quota exceeded for {model}")
                else:
                    print(f"❌ Key #{i} failed for {model}: {e}")
                continue
        if success:
            break
            
    if not success:
        print("💀 ALL ATTEMPTS FAILED. Caching might be disabled for these models in Free Tier.")
        return

    # Update settings
    settings = get_river_settings()
    settings.set("chat_model", "gemini-3-pro-preview", updated_by="fix_script_v4")
    settings.set("chat_model_fallback", cache.model_id, updated_by="fix_script_v4")
    print(f"✅ Settings updated: Thinking Brain -> {settings.chat_model}")
    print(f"✅ Settings updated: Fallback/Cache Brain -> {settings.chat_model_fallback}")

if __name__ == "__main__":
    asyncio.run(fix_cache())
