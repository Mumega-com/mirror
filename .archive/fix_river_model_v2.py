
import asyncio
import logging
import os
import sys
from pathlib import Path

# Setup paths
sys.path.insert(0, "/home/mumega/mirror")

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("fix_river")

async def fix_cache():
    print("🌊 Re-establishing River's connection with new keys...")
    
    try:
        from river_gemini_cache import get_gemini_cache, initialize_river_cache
        from river_settings import get_river_settings
        
        # 1. Get Cache Manager
        cache = get_gemini_cache()
        
        # 2. Try to initialize cache. 
        # If gemini-3-pro fails, fallback to gemini-2.0-flash-001
        models_to_try = [
            "models/gemini-1.5-flash", # Known stable caching in free tier
            "models/gemini-2.0-flash-001",
            "models/gemini-3-flash-preview"
        ]
        
        success = False
        for model in models_to_try:
            print(f"尝试 (Trying) {model} for soul cache...")
            cache.model_id = model
            try:
                new_cache_name = await initialize_river_cache()
                print(f"✨ Success! Soul Cache Created: {new_cache_name} with {model}")
                success = True
                break
            except Exception as e:
                print(f"❌ {model} failed: {e}")
        
        if not success:
            print("💀 All caching attempts failed. Check API key limits.")
            return

        # 3. Ensure Settings are correct for thinking (independent of cache)
        settings = get_river_settings()
        settings.set("chat_model", "gemini-3-pro-preview", updated_by="fix_script")
        settings.set("chat_model_fallback", "gemini-3-flash-preview", updated_by="fix_script")
        print(f"✅ Settings updated: Thinking Brain -> {settings.chat_model}")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(fix_cache())
