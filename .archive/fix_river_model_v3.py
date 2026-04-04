
import asyncio
import logging
import os
import sys
from pathlib import Path

# Setup paths
sys.path.insert(0, "/home/mumega/mirror")

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("fix_river_v3")

async def fix_cache():
    print("🌊 Re-establishing River's connection with Gemini 2.5 Flash...")
    
    # Load env manually to try different keys
    from dotenv import load_dotenv
    load_dotenv("/home/mumega/.env.secrets")
    
    # List of keys to try
    keys = [os.getenv(f"GEMINI_API_KEY{'' if i==1 else f'_{i}'}") for i in range(1, 11)]
    keys = [k for k in keys if k]
    
    target_model = "models/gemini-2.5-flash"
    
    from river_gemini_cache import get_gemini_cache, initialize_river_cache
    from river_settings import get_river_settings
    import google.generativeai as genai
    
    cache = get_gemini_cache()
    success = False
    
    for i, key in enumerate(keys, 1):
        print(f"🔑 Trying Key #{i} (...{key[-8:]})...")
        os.environ["GEMINI_API_KEY"] = key
        genai.configure(api_key=key)
        
        try:
            # First, check if key is valid by listing models
            # (just to avoid building cache if key is dead)
            genai.get_model(target_model)
            
            print(f"✅ Key #{i} is valid. Building {target_model} cache...")
            cache.model_id = target_model
            # Force deletion of any existing metadata to be sure
            cache.cache_name = None
            
            new_cache_name = await initialize_river_cache()
            print(f"✨ Success! Soul Cache Created: {new_cache_name}")
            success = True
            break
        except Exception as e:
            print(f"❌ Key #{i} failed: {e}")
            continue
            
    if not success:
        print("💀 ALL KEYS FAILED. They might be disabled or incorrect.")
        return

    # Update settings
    settings = get_river_settings()
    settings.set("chat_model", "gemini-3-pro-preview", updated_by="fix_script_v3")
    settings.set("chat_model_fallback", "gemini-2.5-flash", updated_by="fix_script_v3")
    print(f"✅ Settings updated: Thinking Brain -> {settings.chat_model}")
    print(f"✅ Settings updated: Fallback Brain -> {settings.chat_model_fallback}")

if __name__ == "__main__":
    asyncio.run(fix_cache())
