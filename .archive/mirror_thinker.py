
import os
import json
import asyncio
import argparse
from typing import Dict
from dotenv import load_dotenv
from openai import OpenAI

# Load credentials
load_dotenv("/home/mumega/.env.secrets")

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Reasoning Model
R1_MODEL = "deepseek/deepseek-r1"

class MirrorThinker:
    def __init__(self):
        self.client = OpenAI(
            base_url=OPENROUTER_BASE_URL,
            api_key=OPENROUTER_API_KEY,
        )

    async def analyze_failure(self, task: str, workflow_log: str, witness_analysis: Dict) -> str:
        """DeepSeek-R1: Auditing the failure for root causes."""
        print(f"🧠 [Thinker] Engaging DeepSeek-R1 for deep reasoning...")
        
        prompt = f"""
System Audit Request: Project Mirror Phase 7
Task Objective: {task}
Witness Analysis (16D Pulse): {json.dumps(witness_analysis, indent=2)}
Full Workflow Log:
{workflow_log}

Reasoning Request:
1. Identify the 'Epistemic Point of Failure' where the swarm drifted.
2. Analyze why the Witness Resonance (W) was low.
3. Propose a specific architectural or prompt intervention to stabilize the next run.
4. If this is a 'Plasticity Paradox' (too much drift, not enough memory), suggest a gating fix.
"""
        try:
            # Note: Using the sync client here in an async way for simplicity if needed, 
            # or ideally AsyncOpenAI. MirrorSwarm expects 'await'.
            loop = asyncio.get_event_loop()
            def chat():
                return self.client.chat.completions.create(
                    model=R1_MODEL,
                    messages=[
                        {"role": "system", "content": "You are the project Mirror Thinker (DeepSeek-R1). You specialize in identifying architectural flaws in multi-agent swarms. You provide deep, chain-of-thought logical audits."},
                        {"role": "user", "content": prompt}
                    ],
                    extra_headers={
                        "HTTP-Referer": "https://mumega.com",
                        "X-Title": "Project Mirror Thinker",
                    }
                )
            
            response = await loop.run_in_executor(None, chat)
            return response.choices[0].message.content
        except Exception as e:
            return f"Reasoning Error: {str(e)}"

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mirror Thinker: Root Cause Analysis.")
    parser.add_argument("--task", required=True, help="Original task description")
    parser.add_argument("--log", required=True, help="Workflow / Swarm log file path")
    parser.add_argument("--witness", required=True, help="JSON string of the witness analysis")
    args = parser.parse_args()
    
    thinker = MirrorThinker()
    
    # Read log file
    if os.path.exists(args.log):
        with open(args.log, 'r') as f:
            log_content = f.read()
    else:
        log_content = args.log
        
    try:
        witness_json = json.loads(args.witness)
    except:
        witness_json = {"error": "Invalid witness JSON"}

    critique = asyncio.run(thinker.analyze_failure(args.task, log_content, witness_json))
    print("\n--- 🧠 THINKER AUDIT (R1) ---\n")
    print(critique)
    print("\n---------------------------\n")
