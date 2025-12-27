
import os
import json
import argparse
from dotenv import load_dotenv

# Load credentials from the master .env
load_dotenv("/Users/hadi/.gemini/.env")

try:
    from supabase import create_client, Client
    from openai import OpenAI
except ImportError:
    print("Error: supabase or openai-python not installed. Please run setup first.")
    exit(1)

# --- CONFIGURATION ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_ANON_KEY")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

class EngramLoader:
    def __init__(self):
        self.supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        self.openai: OpenAI = OpenAI(api_key=OPENAI_API_KEY)

    def get_embedding(self, text: str):
        response = self.openai.embeddings.create(
            input=text,
            model="text-embedding-3-small"
        )
        return response.data[0].embedding

    def search_and_load(self, query: str):
        print(f"Loading headspace for: '{query}'...")
        query_embedding = self.get_embedding(query)
        
        # Search for the most relevant engram
        res = self.supabase.rpc("mirror_match_engrams", {
            "query_embedding": query_embedding,
            "match_threshold": 0.5,
            "match_count": 1
        }).execute()
        
        if res.data:
            engram_summary = res.data[0]
            print(f"Matched: {engram_summary['context_id']} (Similarity: {engram_summary['similarity']:.4f})")
            
            # Fetch the full raw_data
            full_res = self.supabase.table("mirror_engrams").select("raw_data").eq("id", engram_summary["id"]).execute()
            return full_res.data[0]["raw_data"]
        else:
            print("No matching cognitive state found.")
            return None

    def export_briefing(self, engram_data: dict):
        """Generates a text-based briefing as a prompt injection."""
        briefing = f"""
# COGNITIVE STATE BRIEFING: {engram_data['context_id']}
## SERIES: {engram_data['series']}

### VERIFIED TRUTHS:
{chr(10).join(['- ' + t for t in engram_data['epistemic_state']['verified_truths']])}

### CORE CONCEPTS:
{', '.join(engram_data['epistemic_state']['core_concepts'])}

### AFFECTIVE CONTEXT:
Vibe: {engram_data['affective_state']['collaboration_vibe']}
Energy: {engram_data['affective_state']['energy_levels']}

### NEXT ATTRACTOR:
{engram_data['next_attractor']}
"""
        return briefing

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Load a cognitive engram from Supabase.")
    parser.add_argument("query", help="Semantic query to find the right mental state")
    args = parser.parse_args()
    
    loader = EngramLoader()
    state = loader.search_and_load(args.query)
    
    if state:
        print("\n--- INJECTION PROMPT ---")
        print(loader.export_briefing(state))
