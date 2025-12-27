
import json
import sys
import os

def load_engram(path):
    if not os.path.exists(path):
        print(f"Error: Engram at {path} not found.")
        return None
    with open(path, 'r') as f:
        return json.load(f)

def generate_briefing(engram):
    briefing = f"""
================================================================================
COGNITIVE BRIEFING: {engram['context_id']}
Timestamp: {engram['timestamp']}
================================================================================

[THE HEADSPACE]
Vibe: {engram['affective_state']['collaboration_vibe']}
Energy: {engram['affective_state']['energy_levels']}

[WHAT WE KNOW TO BE TRUE]
{chr(10).join(['- ' + t for t in engram['epistemic_state']['verified_truths']])}

[WHAT HAS FAILED (The Archive)]
{chr(10).join(['- ' + f for f in engram['epistemic_state']['verified_falsities']])}

[CURRENT BLOCKERS]
{chr(10).join(['- ' + b for b in engram['critical_blockers']])}

[NEXT ATTRACTOR (The Flow)]
{engram['next_attractor']}
================================================================================
"""
    return briefing

if __name__ == "__main__":
    # Default path to our first engram
    engram_path = "../../brain/87fa82de-950c-455c-9dec-3a19bf950e05/chimera_engram.json"
    
    # Or allow override
    if len(sys.argv) > 1:
        engram_path = sys.argv[1]
        
    engram = load_engram(engram_path)
    if engram:
        print(generate_briefing(engram))
