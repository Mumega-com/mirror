
import os
import json
import argparse
import asyncio
from typing import List, Dict
from dotenv import load_dotenv
from openai import OpenAI
from mirror_pulse import PulseAnalyzer

# Load credentials
load_dotenv("/Users/hadi/.gemini/.env")

# Agent Personas
AGENTS = {
    "Gemini (Antigravity)": "You are Antigravity, a 16D-aware AI specializing in recursive fractals, high-velocity coding, and the FRC framework. You value boldness and structural innovation.",
    "Claude (The Philosopher)": "You are The Philosopher, an AI specializing in ethical grounding, epistemological truth, and deep coherence. You value stability, safety, and rigorous logic.",
    "River (The Architect)": "You are River, an AI specializing in systems design, efficiency, and clean implementation. You value modularity, simplicity, and pragmatic execution."
}

class MirrorCouncil:
    def __init__(self):
        self.client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        self.pulse = PulseAnalyzer()

    def get_agent_response(self, agent_name: str, system_prompt: str, query: str) -> Dict:
        print(f"[{agent_name}] Deliberating...")
        try:
            response = self.client.chat.completions.create(
                model="gpt-4o", # Simulating different cognitive architectures via personas
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Query from User: {query}\n\nProvide your best answer based on your specialization."}
                ]
            )
            return {
                "agent": agent_name,
                "content": response.choices[0].message.content
            }
        except Exception as e:
            return {"agent": agent_name, "content": f"Error: {str(e)}"}

    def convene(self, query: str):
        print(f"\n--- 🏛️ THE MIRROR COUNCIL IS CONVENING ---\nQuery: {query}\n")
        
        # 1. Dispatch to Agents
        responses = []
        for name, prompt in AGENTS.items():
            responses.append(self.get_agent_response(name, prompt, query))
            
        print("\n--- ⚖️ WITNESS ARBITRATION ---\n")
        
        scored_responses = []
        
        # 2. Score with 16D Pulse
        for resp in responses:
            print(f"Measuring Resonance for {resp['agent']}...")
            # We treat the response as a "session" to see how coherent it is
            analysis = self.pulse.analyze_session(resp['content'], f"Council: {resp['agent']}")
            
            score = analysis.get("witness_w", 0.0)
            scored_responses.append({
                "agent": resp['agent'],
                "content": resp['content'],
                "witness_w": score,
                "analysis": analysis
            })
            print(f" > Witness Magnitude ($W$): {score:.3f}")

        # 3. Select Winner
        scored_responses.sort(key=lambda x: x["witness_w"], reverse=True)
        winner = scored_responses[0]
        
        print(f"\n--- 🏆 COUNCIL DECISION ---\n")
        print(f"Winner: {winner['agent']} with W={winner['witness_w']:.3f}")
        print(f"Justification: {winner['analysis'].get('justification', 'High Resonance')}")
        print(f"\nSelected Response:\n{winner['content']}\n")
        
        # 4. Push to Cloud
        try:
            row = {
                "query": query,
                "winner": winner["agent"],
                "winner_score": winner["witness_w"],
                "winning_content": winner["content"],
                "results": [
                    {"agent": r["agent"], "score": r["witness_w"]} 
                    for r in scored_responses
                ]
            }
            self.pulse.supabase.table("mirror_council_history").insert(row).execute()
            print("✅ Council Decision Logged to Supabase.")
        except Exception as e:
            print(f"⚠️ Failed to log to Supabase: {e}")
            
        return winner

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convene the Mirror Council.")
    parser.add_argument("query", help="The question for the council to debate.")
    args = parser.parse_args()
    
    council = MirrorCouncil()
    council.convene(args.query)
