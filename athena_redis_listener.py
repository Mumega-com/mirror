#!/usr/bin/env python3
"""
Athena Redis Listener — bridges Redis streams to OpenClaw system events
and routes task_create messages to the Mirror Task API.

Watches: sos:stream:sos:channel:private:agent:athena
On new message → triggers `openclaw system event --mode now --text <message>`
This wakes Athena via OpenClaw heartbeat.

Also subscribes to:
- athena:tasks (task assignments + task_create routing)
- sos:broadcast (system-wide events)

Run as: systemd service or `python3 athena_redis_listener.py`
"""

import asyncio
import json
import logging
import os
import subprocess
import signal
import sys
from datetime import datetime

try:
    import redis.asyncio as aioredis
except ImportError:
    print("pip install redis[async]")
    sys.exit(1)

try:
    import httpx
    _http = httpx.AsyncClient(timeout=10)
except ImportError:
    _http = None

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [athena-listener] %(levelname)s %(message)s'
)
logger = logging.getLogger("athena-listener")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
MIRROR_API_URL = os.getenv("MIRROR_API_URL", "http://localhost:8844")
STREAMS = {
    "sos:stream:sos:channel:private:agent:athena": "0",  # My private channel
    "athena:tasks": "0",                                   # Task assignments
}
PUBSUB_CHANNELS = ["sos:broadcast", "athena:wake"]

# Track last processed IDs
last_ids = {}

# Cooldown to avoid spamming OpenClaw
MIN_INTERVAL_SECONDS = 30
last_trigger_time = 0


def trigger_openclaw(text: str, agent: str = "athena"):
    """Fire an OpenClaw system event to wake Athena"""
    global last_trigger_time
    
    now = datetime.now().timestamp()
    if now - last_trigger_time < MIN_INTERVAL_SECONDS:
        logger.info(f"Cooldown active, skipping trigger: {text[:80]}")
        return False
    
    last_trigger_time = now
    
    # Truncate long messages
    if len(text) > 500:
        text = text[:497] + "..."
    
    cmd = [
        "openclaw", "system", "event",
        "--mode", "now",
        "--text", f"[Redis:{agent}] {text}",
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            logger.info(f"Triggered OpenClaw: {text[:80]}")
            return True
        else:
            logger.error(f"OpenClaw trigger failed: {result.stderr}")
            return False
    except Exception as e:
        logger.error(f"Failed to trigger OpenClaw: {e}")
        return False


async def handle_task_message(decoded: dict) -> bool:
    """
    Handle task_create messages from athena:tasks stream.

    Expected format (in the 'data' or top-level fields):
        {"type": "task_create", "source": "agent:kasra", "payload": {
            "title": "...", "priority": "high", "agent": "athena",
            "project": "...", "description": "..."
        }}

    Returns True if it was a task message (handled), False otherwise.
    """
    # Parse the payload — it could be nested in 'data' or at top level
    msg = None
    for key in ("data", "payload"):
        raw = decoded.get(key)
        if raw:
            try:
                msg = json.loads(raw) if isinstance(raw, str) else raw
                break
            except (json.JSONDecodeError, TypeError):
                continue
    if not msg:
        msg = decoded

    msg_type = msg.get("type", "")
    if msg_type != "task_create":
        return False

    payload = msg.get("payload", {})
    if not payload.get("title"):
        logger.warning(f"task_create missing title: {msg}")
        return True

    # Route to Mirror Task API
    if _http is None:
        logger.error("httpx not installed — cannot forward task_create to Mirror API")
        return True

    source = msg.get("source", "unknown")
    logger.info(f"task_create from {source}: {payload.get('title')}")

    try:
        resp = await _http.post(f"{MIRROR_API_URL}/tasks", json=payload)
        if resp.status_code == 200:
            result = resp.json()
            task_id = result.get("task", {}).get("id", "?")
            logger.info(f"Task created via API: {task_id}")
        else:
            logger.error(f"Mirror API task create failed ({resp.status_code}): {resp.text[:200]}")
    except Exception as e:
        logger.error(f"Failed to call Mirror API for task_create: {e}")

    return True


async def listen_streams(r: aioredis.Redis):
    """Listen to Redis streams (XREAD blocking)"""
    # Get latest IDs to only process NEW messages
    stream_keys = {}
    for stream_name in STREAMS:
        try:
            # Start from latest (don't replay history)
            info = await r.xinfo_stream(stream_name)
            last_id = info.get("last-generated-id", "0")
            stream_keys[stream_name] = last_id
            logger.info(f"Stream {stream_name}: starting after {last_id}")
        except Exception:
            # Stream doesn't exist yet — use $ to only get new messages
            stream_keys[stream_name] = "$"
            logger.info(f"Stream {stream_name}: doesn't exist yet, waiting for first message")
    
    while True:
        try:
            # Block for up to 5 seconds
            results = await r.xread(stream_keys, block=5000, count=10)
            
            for stream_name, messages in results:
                stream_name = stream_name.decode() if isinstance(stream_name, bytes) else stream_name
                
                for msg_id, data in messages:
                    msg_id = msg_id.decode() if isinstance(msg_id, bytes) else msg_id
                    
                    # Decode data
                    decoded = {}
                    for k, v in data.items():
                        key = k.decode() if isinstance(k, bytes) else k
                        val = v.decode() if isinstance(v, bytes) else v
                        decoded[key] = val
                    
                    # Extract text from payload
                    text = ""
                    if "data" in decoded:
                        try:
                            payload = json.loads(decoded["data"])
                            text = payload.get("payload", {}).get("text", str(payload))
                            source = payload.get("source", "unknown")
                        except (json.JSONDecodeError, AttributeError):
                            text = decoded["data"]
                            source = "unknown"
                    elif "payload" in decoded:
                        try:
                            payload = json.loads(decoded["payload"])
                            text = payload.get("text", str(payload))
                        except (json.JSONDecodeError, AttributeError):
                            text = decoded["payload"]
                        source = decoded.get("source", "unknown")
                    elif "text" in decoded:
                        text = decoded["text"]
                        source = decoded.get("source", "unknown")
                    else:
                        text = json.dumps(decoded)
                        source = "unknown"
                    
                    logger.info(f"[{stream_name}] {msg_id} from {source}: {text[:100]}")

                    # Route task messages from athena:tasks to Mirror API
                    if "task" in stream_name:
                        handled = await handle_task_message(decoded)
                        if handled:
                            stream_keys[stream_name] = msg_id
                            continue

                    # Don't trigger on our own messages
                    if "athena" not in source.lower() or "task" in stream_name:
                        trigger_openclaw(text, agent=source)
                    
                    # Update stream position
                    stream_keys[stream_name] = msg_id
                    
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Stream read error: {e}")
            await asyncio.sleep(5)


async def listen_pubsub(r: aioredis.Redis):
    """Listen to Redis pub/sub channels"""
    pubsub = r.pubsub()
    await pubsub.subscribe(*PUBSUB_CHANNELS)
    logger.info(f"Subscribed to pubsub: {PUBSUB_CHANNELS}")
    
    while True:
        try:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=5)
            if message and message["type"] == "message":
                channel = message["channel"]
                if isinstance(channel, bytes):
                    channel = channel.decode()
                data = message["data"]
                if isinstance(data, bytes):
                    data = data.decode()
                
                logger.info(f"[pubsub:{channel}] {data[:100]}")
                trigger_openclaw(data, agent=channel)
                
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Pubsub error: {e}")
            await asyncio.sleep(5)


async def main():
    logger.info("Athena Redis Listener starting...")
    
    r = aioredis.from_url(REDIS_URL, decode_responses=False)
    
    # Verify connection
    await r.ping()
    logger.info(f"Connected to Redis at {REDIS_URL}")
    
    # Ensure streams exist
    for stream_name in STREAMS:
        exists = await r.exists(stream_name)
        if not exists:
            logger.info(f"Stream {stream_name} doesn't exist yet, will be created on first write")
    
    # Run both listeners
    try:
        await asyncio.gather(
            listen_streams(r),
            listen_pubsub(r),
        )
    except asyncio.CancelledError:
        logger.info("Shutting down...")
    finally:
        await r.close()
        if _http:
            await _http.aclose()


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    
    def shutdown(sig, frame):
        logger.info(f"Received {sig}, shutting down...")
        for task in asyncio.all_tasks(loop):
            task.cancel()
    
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()
        logger.info("Athena Redis Listener stopped.")
