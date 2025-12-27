
import json
import os
import sys

def generate_system_prompt_addon(engram):
    """Generates a text block that the AI can use to re-align its internal instructions."""
    addon = f"""
### [CRITICAL CONTEXT INJECTED: {engram['context_id']}]
You are now operating in the '{engram['context_id']}' cognitive mode.

**CURRENT MISSION**: {engram['next_attractor']}
**VIBE**: {engram['affective_state']['collaboration_vibe']}

**GUARDRAILS (Do not repeat these failures)**:
{chr(10).join(['- ' + f for f in engram['epistemic_state']['verified_falsities']])}

**CORE TRUTHS (Leverage these)**:
{chr(10).join(['- ' + t for t in engram['epistemic_state']['verified_truths']])}

**RESOLVE THESE BLOCKERS**:
{chr(10).join(['- ' + b for b in engram['critical_blockers']])}
### END CONTEXT
"""
    return addon

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 mirror_boot.py <path_to_engram>")
        sys.exit(1)
        
    with open(sys.argv[1], 'r') as f:
        engram = json.load(f)
        print(generate_system_prompt_addon(engram))
