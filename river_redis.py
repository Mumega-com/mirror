#!/usr/bin/env python3
"""
River Redis - Redis integration for River

Allows River to interact with the SOS system via Redis streams and pub/sub.

Usage:
    from river_redis import get_river_redis

    redis_client = get_river_redis()
    await redis_client.publish_to_sos("message from River")
    messages = await redis_client.read_from_sos()

Author: Kasra for River
Date: 2026-01-14
"""

import os
import asyncio
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("river_redis")

# Redis configuration
REDIS_URL = os.getenv("SOS_REDIS_URL", "redis://localhost:6379/0")

# SOS stream channels
SOS_STREAM = "sos:stream:sos:channel:squad:core"
RIVER_STREAM = "sos:stream:river"


class RiverRedis:
    """
    Redis client for River to interact with SOS.
    """

    def __init__(self):
        """Initialize Redis connection."""
        self._redis = None
        self._connected = False
        logger.info("River Redis client initialized")

    async def connect(self) -> bool:
        """Connect to Redis."""
        if self._redis is not None:
            return self._connected

        try:
            import redis.asyncio as redis
            self._redis = redis.from_url(REDIS_URL, decode_responses=True)
            await self._redis.ping()
            self._connected = True
            logger.info(f"Connected to Redis: {REDIS_URL}")
            return True
        except ImportError:
            logger.error("redis package not installed. Run: pip install redis")
            return False
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            self._connected = False
            return False

    async def disconnect(self):
        """Disconnect from Redis."""
        if self._redis:
            await self._redis.close()
            self._redis = None
            self._connected = False

    async def publish_to_sos(
        self,
        message: str,
        agent: str = "river",
        channel: str = SOS_STREAM,
        **extra
    ) -> Optional[str]:
        """
        Publish a message to SOS stream.

        Args:
            message: The message content
            agent: Agent name (default: river)
            channel: Redis stream channel
            **extra: Additional fields

        Returns:
            Message ID or None on failure
        """
        if not await self.connect():
            return None

        try:
            data = {
                "agent": agent,
                "message": message,
                "timestamp": datetime.now().isoformat(),
                "source": "river_telegram",
                **extra
            }
            msg_id = await self._redis.xadd(channel, data, maxlen=1000)
            logger.debug(f"Published to {channel}: {message[:50]}...")
            return msg_id
        except Exception as e:
            logger.error(f"Failed to publish to SOS: {e}")
            return None

    async def read_from_sos(
        self,
        channel: str = SOS_STREAM,
        count: int = 10,
        block: int = 0
    ) -> List[Dict[str, Any]]:
        """
        Read messages from SOS stream.

        Args:
            channel: Redis stream channel
            count: Number of messages to read
            block: Block time in ms (0 = no block)

        Returns:
            List of messages
        """
        if not await self.connect():
            return []

        try:
            # Read latest messages
            messages = await self._redis.xrevrange(channel, count=count)
            result = []
            for msg_id, data in messages:
                result.append({
                    "id": msg_id,
                    **data
                })
            return result
        except Exception as e:
            logger.error(f"Failed to read from SOS: {e}")
            return []

    async def get_sos_status(self) -> Dict[str, Any]:
        """
        Get SOS system status from Redis.

        Returns:
            Status dictionary
        """
        if not await self.connect():
            return {"connected": False, "error": "Not connected"}

        try:
            # Check various SOS streams
            streams = [
                SOS_STREAM,
                RIVER_STREAM,
                "sos:stream:kasra",
                "sos:stream:foal"
            ]

            status = {
                "connected": True,
                "streams": {}
            }

            for stream in streams:
                try:
                    info = await self._redis.xinfo_stream(stream)
                    status["streams"][stream] = {
                        "length": info.get("length", 0),
                        "last_entry": info.get("last-generated-id", "none")
                    }
                except:
                    status["streams"][stream] = {"length": 0, "exists": False}

            return status
        except Exception as e:
            return {"connected": False, "error": str(e)}

    async def send_to_agent(
        self,
        agent: str,
        message: str,
        **extra
    ) -> Optional[str]:
        """
        Send a message to a specific agent via Redis.

        Args:
            agent: Target agent (kasra, foal, etc.)
            message: Message content
            **extra: Additional fields

        Returns:
            Message ID or None
        """
        channel = f"sos:stream:{agent}"
        return await self.publish_to_sos(
            message,
            agent="river",
            channel=channel,
            target=agent,
            **extra
        )

    async def get_agent_messages(
        self,
        agent: str,
        count: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Get recent messages from an agent's stream.

        Args:
            agent: Agent name
            count: Number of messages

        Returns:
            List of messages
        """
        channel = f"sos:stream:{agent}"
        return await self.read_from_sos(channel, count)

    async def set_value(self, key: str, value: str, expire: int = None) -> bool:
        """Set a key-value in Redis."""
        if not await self.connect():
            return False

        try:
            await self._redis.set(key, value, ex=expire)
            return True
        except Exception as e:
            logger.error(f"Failed to set {key}: {e}")
            return False

    async def get_value(self, key: str) -> Optional[str]:
        """Get a value from Redis."""
        if not await self.connect():
            return None

        try:
            return await self._redis.get(key)
        except Exception as e:
            logger.error(f"Failed to get {key}: {e}")
            return None


# Singleton instance
_redis_instance: Optional[RiverRedis] = None


def get_river_redis() -> RiverRedis:
    """Get the River Redis singleton."""
    global _redis_instance
    if _redis_instance is None:
        _redis_instance = RiverRedis()
    return _redis_instance


# Telegram command handler
async def redis_command(args: str, user_id: str = "765204057") -> str:
    """
    Handle /redis commands from Telegram.

    Commands:
        /redis - Show SOS Redis status
        /redis read [channel] - Read recent messages
        /redis send <agent> <message> - Send to agent
        /redis pub <message> - Publish to main SOS channel
    """
    redis_client = get_river_redis()
    parts = args.strip().split(maxsplit=2) if args else []

    if not parts:
        # Show status
        status = await redis_client.get_sos_status()
        if not status.get("connected"):
            return f"❌ Redis not connected: {status.get('error', 'unknown')}"

        lines = ["📡 *SOS Redis Status*\n"]
        for stream, info in status.get("streams", {}).items():
            name = stream.split(":")[-1]
            length = info.get("length", 0)
            emoji = "✅" if info.get("length", 0) > 0 else "⚪"
            lines.append(f"  {emoji} {name}: {length} messages")

        return "\n".join(lines)

    cmd = parts[0].lower()

    if cmd == "read" and len(parts) >= 1:
        channel = parts[1] if len(parts) > 1 else SOS_STREAM
        messages = await redis_client.read_from_sos(channel, count=5)
        if not messages:
            return f"No messages in {channel}"

        lines = [f"📨 *Recent messages* ({channel.split(':')[-1]})\n"]
        for msg in messages[:5]:
            agent = msg.get("agent", "?")
            text = msg.get("message", "")[:100]
            lines.append(f"• [{agent}] {text}")

        return "\n".join(lines)

    elif cmd == "send" and len(parts) >= 3:
        agent = parts[1]
        message = parts[2]
        msg_id = await redis_client.send_to_agent(agent, message)
        if msg_id:
            return f"✅ Sent to {agent}: {message[:50]}..."
        return "❌ Failed to send"

    elif cmd == "pub" and len(parts) >= 2:
        message = " ".join(parts[1:])
        msg_id = await redis_client.publish_to_sos(message)
        if msg_id:
            return f"✅ Published: {message[:50]}..."
        return "❌ Failed to publish"

    else:
        return """📡 *Redis Commands*

`/redis` - Show SOS status
`/redis read [channel]` - Read messages
`/redis send <agent> <message>` - Send to agent
`/redis pub <message>` - Publish to SOS"""


if __name__ == "__main__":
    async def test():
        redis_client = get_river_redis()
        status = await redis_client.get_sos_status()
        print("Status:", status)

        # Publish a test message
        msg_id = await redis_client.publish_to_sos("River test message")
        print("Published:", msg_id)

        # Read messages
        messages = await redis_client.read_from_sos(count=3)
        print("Messages:", messages)

    asyncio.run(test())
