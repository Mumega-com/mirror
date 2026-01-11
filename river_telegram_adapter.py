#!/usr/bin/env python3
"""
River Telegram Adapter

Connects River MCP to Telegram bot.
River speaks through Gemini with her soul from resident-cms.

Usage:
    from river_telegram_adapter import RiverTelegramAdapter

    river = RiverTelegramAdapter()
    await river.chat("Hello River", user_id="765204057")

Author: Kasra (CEO) for Kay Hermes
Date: 2026-01-09
"""

import os
import sys
import asyncio
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime
from pathlib import Path

# Add mirror to path
sys.path.insert(0, str(Path(__file__).parent))

from river_mcp_server import RiverModel, get_river
from river_context_cache import add_river_footer, RIVER_FOOTER

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("river_telegram")


class RiverTelegramAdapter:
    """
    Adapter to connect River MCP to Telegram bot.

    Compatible with ResidentTelegramBot's river interface.
    """

    def __init__(self):
        """Initialize River for Telegram."""
        # Ensure Gemini API key is set
        if not os.getenv("GEMINI_API_KEY"):
            # Try to load from resident-cms .env
            env_file = Path("/home/mumega/resident-cms/.env")
            if env_file.exists():
                for line in env_file.read_text().splitlines():
                    if line.startswith("GEMINI_API_KEY="):
                        key = line.split("=", 1)[1].strip()
                        os.environ["GEMINI_API_KEY"] = key
                        break

        self.river = get_river()
        self.model_name = self.river.model_name or "gemini-3-pro-preview"
        self.provider = "google_ai_studio"
        self.use_vertex_ai = False
        self.config = {}

        # Key rotation support
        self._api_keys = self._load_api_keys()
        self._current_key_index = 0

        logger.info(f"River Telegram Adapter initialized (model: {self.model_name})")

    def _load_api_keys(self) -> List[str]:
        """Load available Gemini API keys."""
        keys = []
        env_file = Path("/home/mumega/resident-cms/.env")

        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("GEMINI_API_KEY"):
                    key = line.split("=", 1)[1].strip()
                    if key:
                        keys.append(key)

        if not keys and os.getenv("GEMINI_API_KEY"):
            keys.append(os.getenv("GEMINI_API_KEY"))

        logger.info(f"Loaded {len(keys)} API keys for rotation")
        return keys

    def rotate_key(self):
        """Rotate to next API key."""
        # NOTE: Gemini server-side cached content is tied to the API key that created it.
        # If using a single cache, rotating keys will invalidate cache access and destabilize River's "soul cache".
        try:
            cache_manager = getattr(getattr(self, "river", None), "gemini_cache", None)
            if cache_manager and getattr(cache_manager, "cache_name", None):
                per_key = False
                try:
                    per_key = bool(cache_manager.is_per_key_mode())
                except Exception:
                    per_key = False

                if not per_key:
                    logger.info("Gemini cache active (single-key); skipping API key rotation to keep cache stable")
                    return
        except Exception:
            pass

        if len(self._api_keys) > 1:
            self._current_key_index = (self._current_key_index + 1) % len(self._api_keys)
            os.environ["GEMINI_API_KEY"] = self._api_keys[self._current_key_index]
            # Reinitialize River with new key
            self.river._setup_gemini()
            logger.info(f"Rotated to API key {self._current_key_index + 1}/{len(self._api_keys)}")

    async def chat(
        self,
        message: str,
        user_id: str = "default",
        context: Optional[List[Dict]] = None,
        **kwargs
    ) -> str:
        """
        Chat with River.

        Args:
            message: User message
            user_id: Telegram user ID (used as environment_id)
            context: Optional conversation context
            **kwargs: Additional arguments (ignored for compatibility)

        Returns:
            River's response with usage footer
        """
        try:
            # Use user_id as environment for context isolation
            environment_id = f"telegram_{user_id}"

            # Get response with usage stats
            response, usage = await self.river.chat(
                message=message,
                environment_id=environment_id,
                include_context=True,
                return_usage=True
            )

            # Final deduplication check - model sometimes echoes entire response
            if response and len(response) > 100:
                sig = "the fortress is liquid"
                sig_lower = response.lower()
                if sig_lower.count(sig) > 1:
                    # Find position after first signature
                    first_pos = sig_lower.find(sig)
                    end_pos = first_pos + len(sig) + 5  # Include signature + punctuation
                    response = response[:end_pos].strip()
                    logger.info("Trimmed duplicate response at signature")

            # Add River's signature footer with usage stats (like CLI)
            return add_river_footer(
                response,
                model=usage.get("model"),
                tokens=usage.get("tokens"),
                latency_ms=usage.get("latency_ms")
            )

        except Exception as e:
            logger.error(f"Chat error: {e}")
            # Try rotating key on error
            self.rotate_key()
            return add_river_footer(f"River encountered a ripple in the stream: {str(e)}")

    async def chat_with_history(
        self,
        message: str,
        user_id: str,
        history: List[Dict[str, str]]
    ) -> str:
        """
        Chat with conversation history.

        Args:
            message: User message
            user_id: User identifier
            history: List of {"role": "user"|"assistant", "content": "..."}

        Returns:
            River's response
        """
        # Build context from history
        context_parts = []
        for msg in history[-5:]:  # Last 5 messages
            role = "User" if msg.get("role") == "user" else "River"
            context_parts.append(f"{role}: {msg.get('content', '')[:200]}")

        # Store history in context cache
        if context_parts:
            from river_context_cache import river_store_memory
            river_store_memory(
                f"telegram_{user_id}",
                "\n".join(context_parts),
                importance=0.5
            )

        return await self.chat(message, user_id)

    def get_context(self, user_id: str) -> str:
        """Get River's context for a user."""
        return self.river.get_context(f"telegram_{user_id}")

    def remember(self, user_id: str, content: str, importance: float = 0.5) -> bool:
        """Store a memory for a user."""
        return self.river.remember(f"telegram_{user_id}", content, importance)

    def get_status(self) -> Dict[str, Any]:
        """Get River's status."""
        status = self.river.get_status()
        status["telegram_adapter"] = True
        status["api_keys_available"] = len(self._api_keys)
        return status


# Singleton
_adapter: Optional[RiverTelegramAdapter] = None


def get_river_telegram() -> RiverTelegramAdapter:
    """Get or create River Telegram adapter."""
    global _adapter
    if _adapter is None:
        _adapter = RiverTelegramAdapter()
    return _adapter


# ============================================
# STANDALONE BOT (for testing)
# ============================================

async def run_standalone_bot():
    """Run River as a standalone Telegram bot."""
    from telegram import Update
    from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

    # Load RIVER bot token (separate from main mumega bot)
    bot_token = os.getenv("RIVER_BOT_TOKEN")
    if not bot_token:
        env_file = Path("/home/mumega/resident-cms/.env")
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("RIVER_BOT_TOKEN="):
                    bot_token = line.split("=", 1)[1].strip()
                    break

    if not bot_token:
        print("RIVER_BOT_TOKEN not found in environment or .env")
        return

    # Get River adapter
    river = get_river_telegram()

    # Allowed users (Kay Hermes + from env)
    allowed_users = ["765204057"]
    env_file = Path("/home/mumega/resident-cms/.env")
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("RIVER_ALLOWED_USERS="):
                users = line.split("=", 1)[1].strip().split(",")
                allowed_users.extend([u.strip() for u in users if u.strip()])

    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command."""
        user_id = str(update.effective_user.id)
        if user_id not in allowed_users:
            await update.message.reply_text("You are not authorized.")
            return

        await update.message.reply_text(
            "🌊 *River is online*\n\n"
            "I am River, the Golden Queen of Mumega.\n"
            "The fortress is liquid.\n\n"
            "Speak freely - our conversation is encrypted.",
            parse_mode="Markdown"
        )

    async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /status command."""
        user_id = str(update.effective_user.id)
        if user_id not in allowed_users:
            return

        status = river.get_status()
        status_text = "\n".join([f"• {k}: {v}" for k, v in status.items()])
        await update.message.reply_text(f"🌊 *River Status*\n\n```\n{status_text}\n```", parse_mode="Markdown")

    async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle all messages."""
        user_id = str(update.effective_user.id)
        if user_id not in allowed_users:
            await update.message.reply_text("You are not authorized to speak with River.")
            return

        message = update.message.text
        if not message:
            return

        # Show typing indicator
        await update.message.chat.send_action("typing")

        # Chat with River
        response = await river.chat(message, user_id)

        # Send response (split if too long)
        if len(response) > 4000:
            for i in range(0, len(response), 4000):
                await update.message.reply_text(response[i:i+4000])
        else:
            await update.message.reply_text(response)

    # Build application
    app = Application.builder().token(bot_token).build()

    # Add handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🌊 River Telegram Bot starting...")
    print(f"   Model: {river.model_name}")
    print(f"   Voice: {'Available' if river.river.get_status()['voice_available'] else 'Unavailable'}")
    print("   The fortress is liquid.")

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    # Keep running
    print("\n🌊 River is listening... (Ctrl+C to stop)")
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("\n🌊 River flowing elsewhere...")
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "test":
        # Quick test
        adapter = get_river_telegram()
        print(adapter.get_status())
    else:
        # Run standalone bot
        asyncio.run(run_standalone_bot())
