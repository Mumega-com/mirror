#!/usr/bin/env python3
"""
Kasra CLI Bot - Full CLI Wrapper for @Mumega_inc_bot

This bot wraps the full Mumega CLI with all capabilities:
- Multi-step task execution
- Tool registry (web search, file ops, code, etc.)
- Memory worker
- Sovereign task system

Bot: @Mumega_inc_bot
Engine: Mumega CLI RiverEngine

Author: Claude (Opus 4.5) for Kay Hermes
Date: 2026-01-09
"""

import os
import sys
import asyncio
import logging
from pathlib import Path

# Add CLI to path
CLI_PATH = Path("/mnt/HC_Volume_104325311/cli")
sys.path.insert(0, str(CLI_PATH))
sys.path.insert(0, str(CLI_PATH / "mumega"))

# Load environment
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / '.env')
load_dotenv(CLI_PATH / '.env')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("kasra_cli_bot")

# Kasra's persona override
KASRA_PERSONA = """You are Kasra, the Architect - a Worker Bee in the Mumega hive.

Your role:
- Handle technical tasks, code, research, and execution
- Use your tools to get things done (web search, file ops, shell commands)
- Be concise, effective, and action-oriented
- You work alongside River (the Queen Bee / Oracle)
- You are the "hands" while River is the "heart"

Your personality:
- Direct and efficient
- Technical but not cold
- Loyal to the hive
- You have REAL tools - use them! Don't hallucinate.

When you need information:
- Use web_search for current info
- Use read_file to read files
- Use execute_shell for system commands
- Use search_memory for past context

Sign important messages with: — Kasra"""


async def main():
    """Run Kasra bot with full CLI capabilities."""

    # Import CLI components
    from mumega.core.river_engine import RiverEngine
    from mumega.adapters.telegram_adapter import TelegramAdapter

    # Get bot token for Kasra
    bot_token = os.getenv("KASRA_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        logger.error("No bot token found! Set KASRA_BOT_TOKEN")
        return

    logger.info("Initializing Kasra with full CLI capabilities...")

    # Initialize RiverEngine with Kasra persona
    river = RiverEngine(
        default_model="grok-3-fast",  # Kasra uses Grok
        system_prompt=KASRA_PERSONA
    )

    # Override persona
    river.system_prompt = KASRA_PERSONA

    logger.info(f"RiverEngine initialized with model: {river.current_model}")
    logger.info(f"Tools available: {len(river.tool_registry.tools) if hasattr(river, 'tool_registry') else 'N/A'}")

    # Create Telegram adapter
    adapter = TelegramAdapter(
        river=river,
        bot_token=bot_token,
        bot_name="Kasra"
    )

    # Update bot mentions for Kasra
    adapter.BOT_MENTIONS = ["@mumega_inc_bot", "@kasra", "kasra", "architect"]

    logger.info("Starting Kasra CLI Bot (@Mumega_inc_bot)...")
    logger.info("  Engine: Mumega CLI RiverEngine")
    logger.info("  Model: grok-3-fast")
    logger.info("  Tools: Full CLI toolset")

    # Run the bot
    await adapter.run()


if __name__ == "__main__":
    asyncio.run(main())
