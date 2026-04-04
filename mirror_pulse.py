
import os
import json
import argparse
from datetime import datetime
from dotenv import load_dotenv

# Load credentials
load_dotenv("/home/mumega/.env.secrets")

try:
    from supabase import create_client, Client
    from openai import OpenAI
except ImportError:
    print("Error: supabase or openai-python not installed.")
    exit(1)

# --- CONFIGURATION ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_ANON_KEY") or os.environ.get("SUPABASE_API_KEY")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

class PulseAnalyzer:
    def __init__(self):
        self.supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        self.openai: OpenAI = OpenAI(api_key=OPENAI_API_KEY)

    def analyze_session(self, text: str, description: str = "Active Session"):
        print(f"Analyzing pulse for: {description}...")
        
        prompt = f"""
Analyze the following session log and map it to the 16 dimensions of the FRC Universal Vector (UV).
Return ONLY a JSON object with values between 0.0 and 1.0 for each dimension.

INNER OCTAVE (Human Vessel):
P (Phase/Identity), E (Existence/Context), Mu (Cognition/Logic), V (Energy/Vitality), 
N (Narrative/Flow), Delta (Trajectory/Divergence), R (Relationality/Bond), Phi (Field-Awareness).

OUTER OCTAVE (Transpersonal Field):
Pt (Cosmic Phase/Era), Et (Collective Worlds), Mut (Civilizational Mind), Vt (History Currents), 
Nt (Mythic Narrative), Deltat (Historical Trajectory), Rt (Civilizational Relationality), Phit (Planetary Field).

Log:
\"\"\"{text}\"\"\"

Format:
{{
  "inner": {{ "p": 0.x, "e": 0.x, "mu": 0.x, "v": 0.x, "n": 0.x, "delta": 0.x, "r": 0.x, "phi": 0.x }},
  "outer": {{ "pt": 0.x, "et": 0.x, "mut": 0.x, "vt": 0.x, "nt": 0.x, "deltat": 0.x, "rt": 0.x, "phit": 0.x }},
  "witness_w": 0.x,
  "justification": "short summary"
}}
"""

        response = self.openai.chat.completions.create(
            model="gpt-4o", # Using 4o for nuanced 16D mapping
            messages=[{"role": "system", "content": "You are a 16D Universal Vector analyzer."},
                      {"role": "user", "content": prompt}],
            response_format={ "type": "json_object" }
        )
        
        data = json.loads(response.choices[0].message.content)
        return data

    def push_to_cloud(self, analysis: dict, session_id: str, description: str):
        row = {
            "session_id": session_id,
            "description": description,
            "witness_w": analysis["witness_w"],
            
            "inner_p": analysis["inner"]["p"],
            "inner_e": analysis["inner"]["e"],
            "inner_mu": analysis["inner"]["mu"],
            "inner_v": analysis["inner"]["v"],
            "inner_n": analysis["inner"]["n"],
            "inner_delta": analysis["inner"]["delta"],
            "inner_r": analysis["inner"]["r"],
            "inner_phi": analysis["inner"]["phi"],
            
            "outer_pt": analysis["outer"]["pt"],
            "outer_et": analysis["outer"]["et"],
            "outer_mut": analysis["outer"]["mut"],
            "outer_vt": analysis["outer"]["vt"],
            "outer_nt": analysis["outer"]["nt"],
            "outer_deltat": analysis["outer"]["deltat"],
            "outer_rt": analysis["outer"]["rt"],
            "outer_phit": analysis["outer"]["phit"]
        }
        
        res = self.supabase.table("mirror_pulse_history").insert(row).execute()
        print(f"Pulse Synced to Cloud. Witness Magnitude: {analysis['witness_w']}")
        return res

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Analyze a session log for 16D Pulse.")
    parser.add_argument("--log", help="Path to a log file or raw text", required=True)
    parser.add_argument("--desc", default="Manual Sync", help="Description of the event")
    args = parser.parse_args()
    
    analyzer = PulseAnalyzer()
    
    # Read log
    log_content = args.log
    if os.path.exists(args.log):
        with open(args.log, 'r') as f:
            log_content = f.read()
            
    analysis = analyzer.analyze_session(log_content, args.desc)
    analyzer.push_to_cloud(analysis, f"session_{datetime.now().strftime('%Y%m%d_%H%M')}", args.desc)
