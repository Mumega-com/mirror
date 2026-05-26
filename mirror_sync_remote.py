import os
import json
import uuid
import argparse
import datetime
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Dict
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from kernel.embeddings import TARGET_EMBEDDING_MODEL, get_embedding

# Load credentials from the master .env
load_dotenv("/home/mumega/.env.secrets")

# Note: You will need to install these:
# pip install supabase
try:
    from supabase import create_client, Client
except ImportError:
    print("Warning: supabase not installed. Script will run in simulation mode.")
    create_client = None

# --- CONFIGURATION ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_ANON_KEY") or os.environ.get("SUPABASE_API_KEY")
class MirrorSync:
    def __init__(self):
        self.supabase: Client = None
        
        if create_client and SUPABASE_URL:
            self.supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    def get_embedding(self, text: str) -> List[float]:
        return get_embedding(text)

    def sync_engram(self, file_path: str):
        with open(file_path, 'r') as f:
            engram = json.load(f)
        
        print(f"Syncing Engram: {engram['context_id']}...")
        
        # Prepare content for embedding
        search_text = f"{engram['context_id']} {engram.get('series', '')} "
        search_text += " ".join(engram['epistemic_state']['verified_truths'])
        search_text += " " + " ".join(engram['epistemic_state'].get('core_concepts', []))
        
        embedding = self.get_embedding(search_text)
        
        data = {
            "context_id": engram['context_id'],
            "timestamp": engram.get('timestamp', datetime.now().isoformat()),
            "series": engram.get('series', 'FRC Foundation'),
            "epistemic_truths": engram.get('epistemic_state', {}).get('verified_truths', []),
            "core_concepts": engram.get('epistemic_state', {}).get('core_concepts', []),
            "affective_vibe": engram.get('affective_state', {}).get('collaboration_vibe', 'Formal'),
            "energy_level": engram.get('affective_state', {}).get('energy_levels', 'Stable'),
            "next_attractor": engram.get('next_attractor', 'Further Research'),
            "raw_data": engram,
            "embedding": embedding,
            "embedding_model": TARGET_EMBEDDING_MODEL,
        }
        
        if self.supabase:
            try:
                # Upsert based on context_id into mirror_engrams
                res = self.supabase.table("mirror_engrams").upsert(data, on_conflict="context_id").execute()
                print(f"Successfully synced to Supabase (mirror_engrams): {engram['context_id']}")
            except Exception as e:
                print(f"Error syncing to Supabase: {e}")
        else:
            print(f"SIMULATION MODE: Prepared data for {engram['context_id']} using table 'mirror_engrams'")

    def search_engrams(self, query: str, limit: int = 3):
        if not self.supabase:
            print("Search requires Supabase credentials.")
            return
            
        print(f"Searching for Cognitive State: '{query}'...")
        query_embedding = self.get_embedding(query)
        
        # RPC call to mirror_match_engrams
        res = self.supabase.rpc("mirror_match_engrams", {
            "query_embedding": query_embedding,
            "match_threshold": 0.5,
            "match_count": limit
        }).execute()
        
        return res.data

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync local Engrams to Supabase mirror_engrams store.")
    parser.add_argument("path", help="Path to engram file or directory")
    parser.add_argument("--search", help="Search query for retrieval")
    args = parser.parse_args()
    
    sync = MirrorSync()
    
    if args.search:
        results = sync.search_engrams(args.search)
        print(json.dumps(results, indent=2))
    elif os.path.isdir(args.path):
        for root, _, files in os.walk(args.path):
            for file in files:
                if file.endswith(".json"):
                    sync.sync_engram(os.path.join(root, file))
    else:
        sync.sync_engram(args.path)
