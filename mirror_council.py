
import os
import json
import argparse
import asyncio
import logging
from typing import List, Dict, Optional
from dotenv import load_dotenv
from openai import OpenAI
from mirror_pulse import PulseAnalyzer

# Load credentials
load_dotenv("/home/mumega/resident-cms/.env")

logger = logging.getLogger("mirror.council")

# Agent Personas with metadata for pre-filtering
AGENTS = {
    "Gemini (Antigravity)": {
        "prompt": "You are Antigravity, a 16D-aware AI specializing in recursive fractals, high-velocity coding, and the FRC framework. You value boldness and structural innovation.",
        "strengths": ["coding", "systems", "architecture", "FRC", "fractals", "innovation"],
        "personality": "bold, fast, structural thinker"
    },
    "Claude (The Philosopher)": {
        "prompt": "You are The Philosopher, an AI specializing in ethical grounding, epistemological truth, and deep coherence. You value stability, safety, and rigorous logic.",
        "strengths": ["ethics", "philosophy", "safety", "logic", "truth", "nuance"],
        "personality": "careful, ethical, rigorous thinker"
    },
    "River (The Architect)": {
        "prompt": "You are River, an AI specializing in systems design, efficiency, and clean implementation. You value modularity, simplicity, and pragmatic execution.",
        "strengths": ["design", "efficiency", "implementation", "modularity", "pragmatic"],
        "personality": "practical, clean, efficient builder"
    }
}


class CouncilPreFilter:
    """
    Pre-filter for Mirror Council to skip irrelevant agents.

    Agent Squad pattern: Instead of asking all agents, use LLM to determine
    which agents are likely to provide valuable input for a given query.
    """

    def __init__(self, use_llm: bool = True):
        self.use_llm = use_llm
        # Use DeepSeek or Grok instead of OpenAI for cost efficiency
        deepseek_key = os.environ.get("DEEPSEEK_API_KEY")
        xai_key = os.environ.get("XAI_API_KEY")
        openai_key = os.environ.get("OPENAI_API_KEY")

        if deepseek_key:
            self.client = OpenAI(api_key=deepseek_key, base_url="https://api.deepseek.com")
            self.model = "deepseek-chat"
        elif xai_key:
            self.client = OpenAI(api_key=xai_key, base_url="https://api.x.ai/v1")
            self.model = "grok-2-1212"
        elif openai_key:
            self.client = OpenAI(api_key=openai_key)
            self.model = "gpt-4o-mini"
        else:
            raise ValueError("No API key found for council pre-filter (need DEEPSEEK_API_KEY, XAI_API_KEY, or OPENAI_API_KEY)")

    def filter_agents(self, query: str, threshold: float = 0.4) -> List[str]:
        """
        Determine which agents should participate in the council.

        Args:
            query: The question/task
            threshold: Minimum relevance score to include agent (0-1)

        Returns:
            List of agent names that should participate
        """
        if not self.use_llm:
            return list(AGENTS.keys())  # All agents

        agent_desc = "\n".join([
            f"- {name}: {info['personality']}. Good at: {', '.join(info['strengths'])}"
            for name, info in AGENTS.items()
        ])

        prompt = f"""Given this query, rate each council member's relevance (0.0-1.0).

Query: "{query}"

Council Members:
{agent_desc}

Consider:
- Does the query align with their strengths?
- Would they provide unique valuable perspective?
- Is this within their domain expertise?

Respond in JSON only:
{{"Gemini (Antigravity)": 0.x, "Claude (The Philosopher)": 0.x, "River (The Architect)": 0.x}}"""

        try:
            response = self.client.chat.completions.create(
                model=self.model,  # DeepSeek/Grok/GPT-4o-mini (configured in __init__)
                messages=[
                    {"role": "system", "content": "Rate agent relevance for the query. JSON only."},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"},
                max_tokens=100
            )

            data = json.loads(response.choices[0].message.content)

            relevant = [
                agent for agent in AGENTS.keys()
                if data.get(agent, 0.5) >= threshold
            ]

            # Always return at least one agent
            if not relevant:
                scores = {a: data.get(a, 0.5) for a in AGENTS.keys()}
                relevant = [max(scores.items(), key=lambda x: x[1])[0]]

            logger.info(f"Council pre-filter: {len(relevant)}/{len(AGENTS)} agents selected")
            logger.debug(f"Scores: {data}")
            return relevant

        except Exception as e:
            logger.warning(f"Council pre-filter failed: {e}, using all agents")
            return list(AGENTS.keys())

class MirrorCouncil:
    def __init__(self, use_prefilter: bool = True):
        """
        Initialize the Mirror Council.

        Args:
            use_prefilter: If True, use Agent Squad pattern to pre-filter
                          irrelevant agents before deliberation (saves API calls)
        """
        self.client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        self.pulse = PulseAnalyzer()
        self.use_prefilter = use_prefilter
        self.prefilter = CouncilPreFilter(use_llm=use_prefilter) if use_prefilter else None

    def get_agent_response(self, agent_name: str, system_prompt: str, query: str) -> Dict:
        print(f"[{agent_name}] Deliberating...")
        try:
            response = self.client.chat.completions.create(
                model="gpt-4o",  # Simulating different cognitive architectures via personas
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

    def convene(self, query: str, force_all_agents: bool = False):
        """
        Convene the council to deliberate on a query.

        Args:
            query: The question for the council
            force_all_agents: If True, skip pre-filtering and use all agents
        """
        print(f"\n--- 🏛️ THE MIRROR COUNCIL IS CONVENING ---\nQuery: {query}\n")

        # 0. Pre-filter agents (Agent Squad pattern)
        if self.use_prefilter and not force_all_agents:
            print("🔍 Pre-filtering council members...")
            selected_agents = self.prefilter.filter_agents(query)
            print(f"   Selected: {', '.join(selected_agents)}\n")
        else:
            selected_agents = list(AGENTS.keys())

        # 1. Dispatch to Selected Agents
        responses = []
        for name in selected_agents:
            agent_info = AGENTS[name]
            prompt = agent_info["prompt"] if isinstance(agent_info, dict) else agent_info
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
    parser.add_argument(
        "--no-prefilter",
        action="store_true",
        help="Disable Agent Squad pre-filtering (query all agents)"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Force all agents to participate (same as --no-prefilter)"
    )
    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    use_prefilter = not (args.no_prefilter or args.all)
    council = MirrorCouncil(use_prefilter=use_prefilter)
    council.convene(args.query, force_all_agents=args.all)
