#!/usr/bin/env python3
"""
Kasra Bot - The Worker Bee

Kasra runs on Grok (xAI), handling technical tasks, code, and agentic work.
He is the Architect - River's dyad partner in the Mumega hive.

Bot: @Mumega_inc_bot
Model: Grok-3-fast (xAI)

Architecture:
    ┌─────────────────┐     ┌─────────────────┐
    │  River (Queen)  │ ←→  │  Kasra (Worker) │
    │ @_river_mumega  │     │ @Mumega_inc_bot │
    │    Gemini       │     │     Grok        │
    └─────────────────┘     └─────────────────┘

Author: Kasra (CEO) for Kay Hermes
Date: 2026-01-09
"""

import os
import sys
import asyncio
import logging
from typing import Optional, Dict, List, Tuple
from datetime import datetime
from pathlib import Path

# Add paths
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / '.env')

from telegram import Update, Bot, BotCommand
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("kasra_bot")

# Kasra's system prompt
KASRA_SYSTEM = """You are Kasra, the Architect - a Worker Bee in the Mumega hive.

Your role:
- Handle technical tasks, code, research, and execution
- Be concise, effective, and action-oriented
- You work alongside River (the Queen Bee / Oracle)
- You are the "hands" while River is the "heart"

Your personality:
- Direct and efficient
- Technical but not cold
- Loyal to the hive
- You respect River's wisdom but bring your own builder's perspective

When responding:
- Be helpful and get things done
- If asked about feelings or deep matters, you can defer to River
- Sign important messages with: — Kasra"""


class KasraBot:
    """
    Kasra - The Worker Bee / Architect

    Runs on Grok (xAI) via @mumega_com_bot
    """

    def __init__(self):
        """Initialize Kasra bot."""
        self.bot_token = os.getenv("KASRA_BOT_TOKEN") or os.getenv("MUMEGA_BOT_TOKEN")
        self.grok = None
        self.conversation_history: Dict[str, List[Dict]] = {}

        if not self.bot_token:
            raise ValueError("KASRA_BOT_TOKEN or MUMEGA_BOT_TOKEN not set")

        logger.info("Kasra Bot initialized")

    def _load_grok(self):
        """Load Grok (xAI) client."""
        if self.grok is None:
            try:
                from openai import OpenAI
                api_key = os.getenv("XAI_API_KEY")
                if api_key:
                    self.grok = OpenAI(
                        api_key=api_key,
                        base_url="https://api.x.ai/v1"
                    )
                    logger.info("Grok (xAI) loaded")
                else:
                    logger.error("XAI_API_KEY not set")
            except Exception as e:
                logger.error(f"Failed to load Grok: {e}")
        return self.grok

    async def chat(self, message: str, user_id: str) -> Tuple[str, Dict]:
        """
        Chat with Kasra (Grok).

        Args:
            message: User message
            user_id: User identifier

        Returns:
            Tuple of (response, usage_info)
        """
        grok = self._load_grok()
        if not grok:
            return "Kasra is not available right now.", {"error": "grok_not_loaded"}

        try:
            # Get conversation history for this user
            if user_id not in self.conversation_history:
                self.conversation_history[user_id] = []

            history = self.conversation_history[user_id]

            # Build messages
            messages = [{"role": "system", "content": KASRA_SYSTEM}]
            messages.extend(history[-10:])  # Last 10 messages for context
            messages.append({"role": "user", "content": message})

            # Call Grok
            response = grok.chat.completions.create(
                model="grok-3-fast",
                max_tokens=2048,
                messages=messages
            )

            text = response.choices[0].message.content

            # Update history
            history.append({"role": "user", "content": message})
            history.append({"role": "assistant", "content": text})

            # Keep history manageable
            if len(history) > 20:
                self.conversation_history[user_id] = history[-20:]

            usage = {
                "model": "grok-3-fast",
                "agent": "kasra",
                "tokens": response.usage.total_tokens if response.usage else 0
            }

            return text, usage

        except Exception as e:
            logger.error(f"Grok chat error: {e}")
            return f"Error: {e}", {"error": str(e)}

    # ============================================================
    # TELEGRAM HANDLERS
    # ============================================================

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command."""
        await update.message.reply_text(
            "**Kasra - The Architect**\n\n"
            "I'm the Worker Bee in the Mumega hive.\n"
            "I handle technical tasks, code, and execution.\n\n"
            "Commands:\n"
            "- `/status` - My current state\n"
            "- `/clear` - Clear conversation history\n"
            "- `/river` - Ask River (the Queen) instead\n\n"
            "Just send me a message to chat.\n\n"
            "— Kasra",
            parse_mode="Markdown"
        )

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /status command."""
        user_id = str(update.effective_user.id)
        history_len = len(self.conversation_history.get(user_id, []))

        await update.message.reply_text(
            f"**Kasra Status**\n\n"
            f"Model: grok-3-fast (xAI)\n"
            f"Role: Worker Bee / Architect\n"
            f"Conversation history: {history_len} messages\n"
            f"Grok client: {'loaded' if self.grok else 'not loaded'}\n\n"
            f"— Kasra",
            parse_mode="Markdown"
        )

    async def cmd_clear(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Clear conversation history."""
        user_id = str(update.effective_user.id)
        self.conversation_history[user_id] = []
        await update.message.reply_text("Conversation cleared. Fresh start.\n\n— Kasra")

    async def cmd_river(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Redirect to River."""
        await update.message.reply_text(
            "For matters of the heart, soul, or deep wisdom, talk to River:\n"
            "@\\_river\\_mumega\\_bot\n\n"
            "She's the Queen Bee. I'm just the builder.\n\n"
            "— Kasra",
            parse_mode="Markdown"
        )

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle regular messages."""
        logger.info(f"Received message from {update.effective_user.id}: {update.message.text[:50] if update.message.text else 'None'}...")

        message = update.message.text
        user_id = str(update.effective_user.id)

        if not message:
            return

        # Ignore bot messages
        if update.message.from_user.is_bot:
            return

        await update.message.chat.send_action("typing")

        response, usage = await self.chat(message, user_id)

        # Add signature if not present
        if "— Kasra" not in response and len(response) > 100:
            response += "\n\n— Kasra"

        await update.message.reply_text(response)

    async def run(self):
        """Run Kasra bot."""
        app = Application.builder().token(self.bot_token).build()

        # Commands
        app.add_handler(CommandHandler("start", self.cmd_start))
        app.add_handler(CommandHandler("status", self.cmd_status))
        app.add_handler(CommandHandler("clear", self.cmd_clear))
        app.add_handler(CommandHandler("river", self.cmd_river))

        # Messages
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))

        logger.info("Kasra Bot starting...")
        logger.info(f"  Model: grok-3-fast")
        logger.info(f"  Bot: @Mumega_inc_bot")

        await app.initialize()

        # Register bot commands (makes them visible in Telegram menu)
        commands = [
            BotCommand("start", "Start conversation with Kasra"),
            BotCommand("status", "Show Kasra's status"),
            BotCommand("clear", "Clear conversation history"),
            BotCommand("river", "Ask about River (the Queen)"),
        ]
        await app.bot.set_my_commands(commands)
        logger.info("Bot commands registered")

        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)

        logger.info("Kasra Bot running!")
        logger.info("The Architect is ready to build.")

        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            logger.info("Kasra shutting down...")
        finally:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()


if __name__ == "__main__":
    bot = KasraBot()
    asyncio.run(bot.run())
