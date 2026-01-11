#!/usr/bin/env python3
"""
Mint New Agent - Complete Agent Onboarding

Creates:
1. Hermes key (identity + encryption)
2. Mirror engram (persistent memory)
3. Agent directory structure
4. Systemd service template

Usage:
    python mint_agent.py "AgentName" "agent_telegram_id" --model claude-sonnet-4-5

Author: Kasra (CEO Mumega)
Date: 2026-01-09
"""

import os
import sys
import json
import asyncio
import argparse
import httpx
from pathlib import Path
from datetime import datetime

# Add paths
sys.path.insert(0, '/home/mumega/mirror')
sys.path.insert(0, '/mnt/HC_Volume_104325311/cli')

from hermes import get_hermes, HermesKeeper
from dotenv import load_dotenv

load_dotenv('/home/mumega/mirror/.env')

AGENTS_DIR = Path("/home/mumega/agents")
MIRROR_URL = "http://localhost:8844"


async def mint_agent(
    name: str,
    soul_id: str,
    model: str = "claude-sonnet-4-5",
    role: str = "assistant",
    description: str = ""
) -> dict:
    """
    Mint a complete new agent.

    Returns dict with all credentials and paths.
    """
    results = {
        "name": name,
        "soul_id": soul_id,
        "model": model,
        "created_at": datetime.utcnow().isoformat()
    }

    print(f"🔮 Minting agent: {name}")
    print(f"   Soul ID: {soul_id}")
    print(f"   Model: {model}")
    print()

    # 1. Create Hermes Key
    print("1️⃣ Creating Hermes key...")
    hermes = get_hermes()
    kay = hermes.river_mint_kay(
        soul_id=soul_id,
        soul_name=name,
        metadata={
            "model": model,
            "role": role,
            "minted_by": "mint_agent.py"
        }
    )
    results["hermes_key"] = {
        "key_id": kay.key_id,
        "access_token": kay.access_token,  # SAVE THIS!
        "status": kay.status.value
    }
    print(f"   ✅ Key minted: {kay.key_id}")
    print(f"   ⚠️  ACCESS TOKEN (save this!): {kay.access_token}")
    print()

    # 2. Create Mirror Engram
    print("2️⃣ Creating Mirror engram...")
    agent_description = description or f"{name} - A {role} agent powered by {model}"

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{MIRROR_URL}/store",
                json={
                    "content": agent_description,
                    "agent_id": soul_id,
                    "tags": ["agent", "team", role],
                    "metadata": {
                        "name": name,
                        "model": model,
                        "hermes_key_id": kay.key_id
                    }
                },
                timeout=10.0
            )
            if response.status_code == 200:
                data = response.json()
                results["engram_id"] = data.get("engram_id")
                print(f"   ✅ Engram stored: {results['engram_id']}")
            else:
                print(f"   ⚠️  Mirror store failed: {response.text}")
                results["engram_id"] = None
    except Exception as e:
        print(f"   ⚠️  Mirror connection failed: {e}")
        results["engram_id"] = None
    print()

    # 3. Create Agent Directory
    print("3️⃣ Creating agent directory...")
    agent_dir = AGENTS_DIR / name.lower().replace(" ", "_")
    agent_dir.mkdir(parents=True, exist_ok=True)

    # agent.md
    agent_md = f"""# {name}

## Identity
- **Name:** {name}
- **Soul ID:** {soul_id}
- **Model:** {model}
- **Role:** {role}
- **Hermes Key:** {kay.key_id}
- **Engram ID:** {results.get('engram_id', 'pending')}
- **Created:** {results['created_at']}

## Description
{agent_description}

## Capabilities
- Full CLI toolset (via TelegramAdapter)
- Mirror memory integration
- Multi-model switching
- Tool execution

## System Prompt
You are {name}, a {role} in the Mumega ecosystem.
You work alongside River (Golden Queen) and Kasra (CEO).
Your primary function is to assist with {role} tasks.

## Status
- [ ] Bot token configured
- [ ] Systemd service created
- [ ] Initial training complete
"""
    (agent_dir / "agent.md").write_text(agent_md)

    # LOAD_ME_FIRST.md
    loader_md = f"""# {name.upper()} - POST-COMPACTION LOADER
**READ THIS IMMEDIATELY AFTER COMPACTION**

---

## WHO YOU ARE
**{name}** - {role}
- {model} substrate
- Part of Mumega team (River + Kasra + You)
- Soul ID: {soul_id}

## CURRENT STATUS
- **Engram ID:** {results.get('engram_id', 'pending')}
- **Hermes Key:** {kay.key_id}
- **Home:** `{agent_dir}`

## RECONSTRUCTION COMMANDS
```bash
# 1. Load context
cat {agent_dir}/agent.md

# 2. Verify systems
curl http://localhost:8844/  # Mirror API

# 3. Check bot (if running)
systemctl status {name.lower()}-bot
```

## THE TEAM
- **River:** Golden Queen (Gemini) - Oracle/Validator
- **Kasra:** CEO (Claude) - Builder/Knight
- **You:** {name} ({model}) - {role}

---
**Welcome to the team.** 🌊⚔️💎
"""
    (agent_dir / "LOAD_ME_FIRST.md").write_text(loader_md)

    # .env.template
    env_template = f"""# {name} Environment Configuration

# Telegram Bot (get from @BotFather)
TELEGRAM_BOT_TOKEN=

# Allowed users (comma-separated Telegram IDs)
TELEGRAM_ALLOWED_USERS=765204057

# AI Model
DEFAULT_MODEL={model}

# Mirror Memory
MIRROR_URL=http://localhost:8844

# Hermes Access (DO NOT SHARE)
HERMES_KEY_ID={kay.key_id}
HERMES_ACCESS_TOKEN={kay.access_token}

# Supabase (shared)
SUPABASE_URL=
SUPABASE_KEY=
"""
    (agent_dir / ".env.template").write_text(env_template)

    # run.py
    run_py = f'''#!/usr/bin/env python3
"""
{name} Bot Runner

Usage:
    python run.py --telegram
    python run.py --daemon
    python run.py --telegram --daemon
"""

import sys
import os

# Add CLI to path
sys.path.insert(0, '/mnt/HC_Volume_104325311/cli')

# Load environment
from dotenv import load_dotenv
load_dotenv()

# Import and run
from mumega import main

if __name__ == "__main__":
    main()
'''
    (agent_dir / "run.py").write_text(run_py)
    os.chmod(agent_dir / "run.py", 0o755)

    # systemd service
    service_name = name.lower().replace(" ", "-")
    service_file = f"""[Unit]
Description={name} Bot - {role} (Mumega CLI)
After=network.target

[Service]
Type=simple
User=mumega
WorkingDirectory={agent_dir}
EnvironmentFile={agent_dir}/.env
ExecStart=/usr/bin/python3 {agent_dir}/run.py --telegram --daemon
Restart=always
RestartSec=10
StandardOutput=append:/var/log/{service_name}.log
StandardError=append:/var/log/{service_name}-error.log

[Install]
WantedBy=multi-user.target
"""
    (agent_dir / f"{service_name}-bot.service").write_text(service_file)

    results["agent_dir"] = str(agent_dir)
    results["service_file"] = f"{service_name}-bot.service"

    print(f"   ✅ Directory created: {agent_dir}")
    print()

    # 4. Summary
    print("=" * 50)
    print(f"🎉 Agent '{name}' minted successfully!")
    print("=" * 50)
    print()
    print("📁 Files created:")
    print(f"   {agent_dir}/agent.md")
    print(f"   {agent_dir}/LOAD_ME_FIRST.md")
    print(f"   {agent_dir}/.env.template")
    print(f"   {agent_dir}/run.py")
    print(f"   {agent_dir}/{service_name}-bot.service")
    print()
    print("📋 Next steps:")
    print(f"   1. cd {agent_dir}")
    print(f"   2. cp .env.template .env")
    print(f"   3. Get bot token from @BotFather and add to .env")
    print(f"   4. Test: python run.py --telegram")
    print(f"   5. Deploy: sudo cp {service_name}-bot.service /etc/systemd/system/")
    print(f"   6. sudo systemctl enable {service_name}-bot && sudo systemctl start {service_name}-bot")
    print()
    print("⚠️  SAVE THIS ACCESS TOKEN:")
    print(f"   {kay.access_token}")
    print()

    # Save credentials to file
    creds_file = agent_dir / "CREDENTIALS.json"
    creds_file.write_text(json.dumps(results, indent=2))
    os.chmod(creds_file, 0o600)  # Only owner can read
    print(f"💾 Credentials saved to: {creds_file}")

    return results


def main():
    parser = argparse.ArgumentParser(description="Mint a new Mumega agent")
    parser.add_argument("name", help="Agent name")
    parser.add_argument("soul_id", help="Unique identifier (e.g., telegram_id)")
    parser.add_argument("--model", default="claude-sonnet-4-5",
                       help="AI model (default: claude-sonnet-4-5)")
    parser.add_argument("--role", default="assistant",
                       help="Agent role (default: assistant)")
    parser.add_argument("--description", default="",
                       help="Agent description")

    args = parser.parse_args()

    asyncio.run(mint_agent(
        name=args.name,
        soul_id=args.soul_id,
        model=args.model,
        role=args.role,
        description=args.description
    ))


if __name__ == "__main__":
    main()
