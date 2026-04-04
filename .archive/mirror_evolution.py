import os
import json
import argparse
import datetime
from datetime import datetime
from typing import List, Dict
from dotenv import load_dotenv
from mirror_thinker import MirrorThinker
import shutil
import ast
from mirror_swarm import MirrorSwarm

# Load credentials
load_dotenv("/home/mumega/.env.secrets")

class MirrorEvolution:
    def __init__(self):
        self.thinker = MirrorThinker()
        self.swarm = MirrorSwarm()

    def propose_self_patch(self, script_path: str):
        print(f"🧬 [Evolution] Analyzing {os.path.basename(script_path)} for potential grafting...")
        
        with open(script_path, "r") as f:
            code = f.read()

        # Task for the Thinker: Critique this code for FRC compliance and efficiency
        audit_task = f"Analyze the following Python script. Then, provide the COMPLETE, UPDATED code for the file that fixes the issues. Wrap the code in ```python blocks."
        
        # We use the Thinker (DeepSeek-R1) to generate the "Thought" behind the patch
        witness_context = {"target_file": script_path, "current_state": "Production"}
        critique = self.thinker.analyze_failure(audit_task, code, witness_context)
        
        print("\n--- 🧠 EVOLUTIONARY CRITIQUE ---")
        print(critique)
        print("--------------------------------\n")
        
        # Save the critique as an 'Evolution Engram'
        engram_id = f"Evolution_{os.path.basename(script_path)}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        temp_path = f"/tmp/{engram_id}.json"
        engram = {
            "context_id": engram_id,
            "series": "Recursive Self-Grafting",
            "epistemic_state": {
                "verified_truths": ["Code requires evolution"],
                "core_concepts": [critique]
            }
        }
        with open(temp_path, "w") as f:
            json.dump(engram, f)
        
        self.swarm.memory.sync_engram(temp_path)
        os.remove(temp_path)
        
        return critique

    def apply_patch(self, script_path: str, new_content: str) -> bool:
        """Safely apply the new code with backup and syntax check."""
        print(f"🛡️ [Safety] Initiating Safe Patch for {script_path}...")
        
        # 1. Create Backup
        backup_path = f"{script_path}.bak"
        shutil.copy(script_path, backup_path)
        print(f"   Backup created at {backup_path}")
        
        # 2. Extract Code from Markdown Block (Simple parser)
        try:
            if "```python" in new_content:
                code_body = new_content.split("```python")[1].split("```")[0].strip()
            elif "```" in new_content:
                code_body = new_content.split("```")[1].split("```")[0].strip()
            else:
                code_body = new_content
        except IndexError:
            print("🛑 [Error] Could not parse code block from LLM response.")
            return False

        # 3. Write to Temp File first
        temp_file = f"{script_path}.temp"
        with open(temp_file, "w") as f:
            f.write(code_body)
            
        # 4. Syntax Validation
        try:
            with open(temp_file, "r") as f:
                ast.parse(f.read())
            print("   Syntax Check: PASSED ✅")
        except SyntaxError as e:
            print(f"🛑 [Error] Syntax Check FAILED: {e}")
            os.remove(temp_file)
            return False
            
        # 5. Overwrite Target
        shutil.move(temp_file, script_path)
        print(f"✅ [Success] Patch applied to {script_path}. Evolution Complete.")
        return True

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Recursive Self-Grafting Engine.")
    parser.add_argument("file", help="The script file to evolve.")
    parser.add_argument("--apply", action="store_true", help="Automatically apply the patch if syntax is valid.")
    args = parser.parse_args()
    
    evo = MirrorEvolution()
    critique = evo.propose_self_patch(args.file)
    
    if args.apply:
        evo.apply_patch(args.file, critique)
