import os
import json
import asyncio
import argparse
from datetime import datetime, timedelta
from typing import List, Dict
from dotenv import load_dotenv
from openai import AsyncOpenAI  # Critical change
from mirror_pulse import PulseAnalyzer
from mirror_thinker import MirrorThinker
from mirror_sync_remote import MirrorSync

# Integration with Mumega Core
try:
    from mumega.core.economy.agent_trust import get_trust_gate, TrustTier
    MUMEGA_INTEGRATION = True
except ImportError:
    MUMEGA_INTEGRATION = False
    print("⚠️ Mumega Core not found. Dynamic agent discovery disabled.")

# Load credentials from environment
load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Swarm Configuration - Updated Jan 2026
DEEPSEEK_MODEL = "deepseek/deepseek-v3.2"  # DeepSeek V3.2 - reasoning-first with tool-use
GROK_CODE_MODEL = "x-ai/grok-4-1"  # Grok 4.1 - code mode for synthesis
MAX_CONTRIBUTION_TOKENS = 1500  # Context gate

class MirrorSwarm:
    def __init__(self):
        self.client = AsyncOpenAI(  # Async client
            base_url=OPENROUTER_BASE_URL,
            api_key=OPENROUTER_API_KEY,
        )
        self.pulse = PulseAnalyzer()
        self.thinker = MirrorThinker()
        self.memory = MirrorSync()
        self.concept_registry = set()  # Plasticity gate
        self.trust_gate = get_trust_gate() if MUMEGA_INTEGRATION else None

    def _truncate_contribution(self, text: str) -> str:
        """Enforces context gating to prevent overflow"""
        return text[:MAX_CONTRIBUTION_TOKENS]

    async def run_worker(self, worker_id: int, task: str, sub_context: str, lessons: str = "") -> Dict:
        """Asynchronous DeepSeek Worker Agent"""
        print(f"🐝 [Worker {worker_id}] Exploring: {sub_context}")
        
        # Plasticity Gate: Deduplicate concepts
        if sub_context in self.concept_registry:
            return {"id": worker_id, "context": sub_context, "status": "duplicate_skipped"}
        self.concept_registry.add(sub_context)
        
        system_msg = (
            "You are a DeepSeek Swarm Worker. Generate focused technical insights on ONE aspect. "
            "Format: ### [Topic]\\n<concise insights>\\n\\n"
        )
        if lessons:
            system_msg += f"\\n\\nHistorical Context:\\n{lessons[:3000]}"  # Plasticity gate

        try:
            response = await self.client.chat.completions.create(  # Async call
                model=DEEPSEEK_MODEL,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": f"Task: {task}\\nFocus: {sub_context}\\nContribution:"}
                ],
                extra_headers={
                    "HTTP-Referer": "https://mumega.com",
                    "X-Title": "Project Mirror Swarm",
                }
            )
            content = self._truncate_contribution(response.choices[0].message.content)
            return {
                "id": worker_id,
                "context": sub_context,
                "contribution": content
            }
        except Exception as e:
            return {"id": worker_id, "context": sub_context, "error": str(e)}

    async def run_external_worker(self, agent_id: str, task: str, sub_context: str) -> Dict:
        """Dispatches a task to an external agent via HTTP (Breeze Protocol)"""
        if not self.trust_gate:
            return {"agent_id": agent_id, "error": "TrustGate not available"}

        profile = self.trust_gate.get_profile(agent_id)
        if not profile:
            return {"agent_id": agent_id, "error": "Agent profile not found"}

        # For simulated/local test, we use a mock URL. In prod, this would be in metadata.
        endpoint = profile.metadata.get("endpoint")
        if not endpoint:
            # Fallback for Azure Spore local testing
            if "azure" in agent_id:
                endpoint = "http://localhost:7071/api/agent" # Default local Azure Func port
            else:
                return {"agent_id": agent_id, "error": "No endpoint metadata found"}

        print(f"📡 [External Worker {agent_id}] Dispatching: {sub_context}")
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                payload = {
                    "command": "DISPATCH",
                    "task": f"{task} (Focus: {sub_context})"
                }
                resp = await client.post(endpoint, json=payload, timeout=30.0)
                if resp.status_code == 200:
                    data = resp.json()
                    return {
                        "id": agent_id,
                        "context": sub_context,
                        "contribution": data.get("result", "No result")
                    }
                else:
                    return {"id": agent_id, "context": sub_context, "error": f"HTTP {resp.status_code}"}
        except Exception as e:
            return {"id": agent_id, "context": sub_context, "error": str(e)}

    async def synthesize(self, task: str, worker_results: List[Dict]) -> str:
        """Qwen Synthesis with context gating"""
        print("🏗️ [Architect] Synthesizing with Qwen...")
        
        contributions = []
        for r in worker_results:
            if "contribution" in r:
                contributions.append(f"## Worker {r['id']} ({r['context']})\\n{r['contribution']}")
            elif "error" in r:
                contributions.append(f"## Worker {r['id']} ERROR\\n{r['error']}")
        
        contributions_text = '\n\n'.join(contributions)[:8000]  # Context gate
        synthesis_prompt = (
            "You are Lead Architect. Synthesize SWARM INPUTS into coherent solution.\\n"
            "Structure:\\n1. Problem decomposition\\n2. Integration points\\n3. FRC compliance verification\\n\\n"
            f"TASK: {task}\\n\\nSWARM INPUTS:\\n{contributions_text}"
        )

        try:
            response = await self.client.chat.completions.create(
                model=GROK_CODE_MODEL,  # Grok 4.1 code mode for synthesis
                messages=[{"role": "user", "content": synthesis_prompt}],
                extra_headers={
                    "HTTP-Referer": "https://mumega.com",
                    "X-Title": "Project Mirror Swarm",
                }
            )
            return response.choices[0].message.content
        except Exception as e:
            return f"Synthesis Error: {str(e)}"

    async def _recall_engrams(self, task: str) -> str:
        """Asynchronous episodic recall"""
        print("🧠 [Memory] Recalling engrams...")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.memory.search_engrams, task, 2)

    async def coordinate(self, task: str, foci: List[str]):
        print(f"\n--- 🌪️ SWARM INITIATED ---\nTask: {task}\\nWorkers: {len(foci)}")

        # Async plasticity gate
        lessons = ""
        if past_engrams := await self._recall_engrams(task):
            lessons = "Historical Context:\\n" + "\\n".join(
                f"- {eng['context_id']}: {eng['series']}" for eng in past_engrams
            )

        # Spawn workers with parallelism cap
        semaphore = asyncio.Semaphore(5)  # Connection gate
        
        async def worker_task(i, focus):
            async with semaphore:
                return await self.run_worker(i, task, focus, lessons)

        # 1. Start Internal Workers
        tasks = [worker_task(i, focus) for i, focus in enumerate(foci)]
        
        # 2. Discover and Start External Workers (e.g. Azure Spore)
        if self.trust_gate:
            # For testing, we look for TIER_2_GUEST as well. In prod, this might be TIER_1.
            external_agents = self.trust_gate.list_agents(TrustTier.TIER_2_GUEST)
            for agent in external_agents:
                # If agent has matching capabilities or we want widespread swarm
                if "enterprise_audit" in agent.capabilities or "infra_optimization" in agent.capabilities:
                    print(f"🌟 [Swarm] Including External Specialist: {agent.agent_id}")
                    # Assign an enterprise-specific focus or the last focus
                    ext_focus = "Enterprise Infrastructure & Compliance"
                    tasks.append(self.run_external_worker(agent.agent_id, task, ext_focus))

        worker_results = await asyncio.gather(*tasks)

        # Synthesis and stabilization
        final_solution = await self.synthesize(task, worker_results)
        analysis = self.pulse.analyze_session(final_solution, f"Swarm: {task}")
        w_score = analysis.get("witness_w", 0.0)

        print(f"🌀 Witness Resonance (W): {w_score:.3f}")
        
        if w_score < 0.3:
            print("🛑 ALPHA DRIFT DETECTED - Triggering R1 Audit")
            critique = await self.thinker.analyze_failure(task, final_solution, analysis)
            final_solution = f"WITNESS FAILURE (W={w_score})\\n\\nAUDIT:\\n{critique}\\n\\nRAW OUTPUT:\\n{final_solution}"
        else:
            print("✅ STABLE OUTPUT - Archiving engram")
            engram_id = f"Swarm_{datetime.now().strftime('%Y%m%d%H%M')}"
            engram = {
                "context_id": engram_id,
                "series": "SwarmSession",
                "epistemic_state": {
                    "task": task,
                    "w_score": w_score,
                    "foci": foci
                }
            }
            # Memory sync via thread pool (Write to temp file first)
            def save_and_sync(data_dict, eid):
                tmp_path = f"/tmp/{eid}.json"
                with open(tmp_path, "w") as f:
                    json.dump(data_dict, f)
                self.memory.sync_engram(tmp_path)
                os.remove(tmp_path)

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, save_and_sync, engram, engram_id)

        # Async log insertion
        try:
            log_data = {
                "query": task,
                "w_score": w_score,
                "result": final_solution,
                "worker_count": len(worker_results)
            }
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None, 
                lambda: self.pulse.supabase.table("mirror_council_history").insert(log_data).execute()
            )
        except Exception as e:
            print(f"Log Error: {e}")

        print(f"\nFINAL OUTPUT (W={w_score}):\\n{final_solution[:2000]}...")
        return final_solution

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mirror Swarm Coordinator")
    parser.add_argument("task", help="Primary swarm objective")
    parser.add_argument("--foci", nargs="+", default=[
        "Algorithmic Efficiency", 
        "Structural Integrity", 
        "Edge Case Robustness",
        "Security Surface",
        "Protocol Compliance"
    ])
    args = parser.parse_args()
    
    swarm = MirrorSwarm()
    asyncio.run(swarm.coordinate(args.task, args.foci))