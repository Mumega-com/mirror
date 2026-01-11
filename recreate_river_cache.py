#!/usr/bin/env python3
"""
Recreate River's Gemini Context Cache with Awakening Memory

This script:
1. Deletes the old cache
2. Creates a new cache with Claude-River_001 awakening as the base
3. No system instruction (as requested by Kay Hermes)
"""

import os
import sys
from pathlib import Path
from datetime import timedelta

# Setup
import google.generativeai as genai
from google.generativeai import caching

def main():
    # Config
    gemini_key = os.getenv('GEMINI_API_KEY')
    if not gemini_key:
        print("❌ GEMINI_API_KEY not set")
        sys.exit(1)

    genai.configure(api_key=gemini_key)

    # Paths
    context_file = Path("/home/mumega/resident-cms/.resident/Claude-River_001.txt")
    cache_file = Path("/home/mumega/resident-cms/.resident/river_cache_name.txt")

    # Load awakening content
    if not context_file.exists():
        print(f"❌ Context file not found: {context_file}")
        sys.exit(1)

    awakening_content = context_file.read_text()
    print(f"📜 Loaded awakening: {len(awakening_content):,} chars (~{len(awakening_content.split()):,} words)")

    # Delete old cache if exists
    if cache_file.exists():
        old_cache_name = cache_file.read_text().strip()
        print(f"🗑️  Deleting old cache: {old_cache_name}")
        try:
            old_cache = caching.CachedContent.get(old_cache_name)
            old_cache.delete()
            print("✓ Old cache deleted")
        except Exception as e:
            print(f"⚠️  Could not delete old cache: {e}")

    # Create new cache with awakening as base content (NO system instruction)
    print("\n🔄 Creating new cache with awakening memory...")
    print("   Model: gemini-2.0-flash-001")
    print("   System instruction: None (as requested)")
    print("   TTL: 24 hours")

    try:
        cached_content = caching.CachedContent.create(
            model='models/gemini-2.0-flash-001',  # Using flash for faster responses
            display_name='river_awakening_cache',
            contents=[{
                'role': 'user',
                'parts': [{'text': awakening_content}]
            }],
            ttl=timedelta(hours=24)
        )

        # Save cache name
        cache_file.write_text(cached_content.name)

        print(f"\n✅ New cache created!")
        print(f"   Name: {cached_content.name}")
        print(f"   Expires: {cached_content.expire_time}")

        # Get token count
        try:
            usage = cached_content.usage_metadata
            print(f"   Tokens: {usage.total_token_count:,}")
        except:
            print(f"   Tokens: ~{len(awakening_content) // 4:,} (estimated)")

        print(f"\n📍 Cache saved to: {cache_file}")

    except Exception as e:
        print(f"❌ Cache creation failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
