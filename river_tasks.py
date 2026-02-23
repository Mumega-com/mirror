#!/usr/bin/env python3
"""
River Tasks - CLI-like task runner for River

Provides River with CLI capabilities:
- Model testing and listing
- System health checks
- Redis monitoring
- Log management
- Task execution

Usage:
    from river_tasks import get_river_tasks, tasks_command

    tasks = get_river_tasks()
    result = await tasks.run("models")
    result = await tasks.run("health")
    result = await tasks.run("logs")

Author: Kasra for River
Date: 2026-01-14
"""

import os
import sys
import asyncio
import logging
import subprocess
import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
import psutil

# Add paths
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, "/mnt/HC_Volume_104325311/cli")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("river_tasks")

# Log files
RIVER_LOG = Path("/var/log/river/river.log")
SOS_LOG = Path("/home/mumega/SOS/sos.log")


class RiverTasks:
    """
    Task runner for River - provides CLI-like capabilities.
    """

    def __init__(self):
        """Initialize task runner."""
        self._redis = None
        self._models_cache = None
        self._models_cache_time = None
        logger.info("River Tasks initialized")

    async def run(self, task: str, args: str = "") -> str:
        """
        Run a task.

        Args:
            task: Task name (models, health, logs, redis, etc.)
            args: Optional arguments

        Returns:
            Task result as string
        """
        task = task.lower().strip()

        if task in ["models", "model", "m"]:
            return await self.list_models(args)
        elif task in ["test", "testmodel", "tm"]:
            return await self.test_model(args)
        elif task in ["health", "h", "status"]:
            return await self.health_check()
        elif task in ["logs", "log", "l"]:
            return await self.get_logs(args)
        elif task in ["redis", "r", "sos"]:
            return await self.redis_status()
        elif task in ["memory", "mem"]:
            return await self.memory_status()
        elif task in ["prune", "p"]:
            return await self.prune_logs(args)
        elif task in ["restart", "rs"]:
            return await self.restart_service(args)
        elif task in ["shell", "sh", "exec"]:
            return await self.execute_shell(args)
        elif task in ["goals", "g"]:
            return await self.show_goals()
        else:
            return self.help()

    def help(self) -> str:
        """Return help text."""
        return """🔧 *River Tasks*

*Model Commands:*
  `models` - List available models
  `test <model>` - Test a specific model

*System Commands:*
  `health` - Full system health check
  `memory` - Memory usage stats
  `logs [n]` - Show last n log lines (default 20)
  `prune` - Clean old logs

*SOS Commands:*
  `redis` - Redis/SOS status
  `goals` - Show current goals

*Admin Commands:*
  `restart <service>` - Restart a service
  `shell <cmd>` - Execute shell command"""

    async def list_models(self, args: str = "") -> str:
        """List available AI models."""
        models = {
            "gemini": [
                ("gemini-3-pro-preview", "Best quality, slower"),
                ("gemini-3-flash-preview", "Fast, good quality"),
                ("gemini-2.5-pro", "Reliable, stable"),
                ("gemini-2.5-flash", "Fast fallback"),
                ("gemini-2.0-flash", "Legacy fast"),
            ],
            "grok": [
                ("grok-4.1", "xAI latest, 2M context"),
                ("grok-3", "Previous gen"),
            ],
            "openrouter": [
                ("qwen3-coder:free", "Free code model"),
                ("devstral:free", "Free dev model"),
                ("deepseek-r1:free", "Free reasoning"),
            ],
            "ollama": [
                ("ministral:latest", "Local on MacBook"),
            ]
        }

        lines = ["🤖 *Available Models*\n"]

        for provider, model_list in models.items():
            lines.append(f"\n*{provider.title()}:*")
            for model_id, desc in model_list:
                lines.append(f"  • `{model_id}` - {desc}")

        # Check current model
        try:
            from river_settings import get_river_settings
            settings = get_river_settings()
            current = settings.chat_model
            lines.append(f"\n_Current: {current}_")
        except:
            pass

        return "\n".join(lines)

    async def test_model(self, model: str) -> str:
        """Test a specific model."""
        if not model:
            return "Usage: `test <model_id>`"

        model = model.strip()
        lines = [f"🧪 *Testing {model}*\n"]

        try:
            import google.generativeai as genai
            from river_settings import get_river_settings

            settings = get_river_settings()

            # Test Gemini models
            if "gemini" in model.lower():
                start = datetime.now()
                test_model = genai.GenerativeModel(model)
                response = await asyncio.to_thread(
                    lambda: test_model.generate_content("Say 'Hello from River' in one short sentence.")
                )
                elapsed = (datetime.now() - start).total_seconds()

                if response.text:
                    lines.append(f"✅ Success ({elapsed:.2f}s)")
                    lines.append(f"Response: _{response.text[:100]}_")
                else:
                    lines.append("⚠️ Empty response")

            # Test Grok
            elif "grok" in model.lower():
                from openai import OpenAI
                api_key = os.getenv("XAI_API_KEY")
                if not api_key:
                    return "❌ XAI_API_KEY not set"

                client = OpenAI(base_url="https://api.x.ai/v1", api_key=api_key)
                start = datetime.now()

                response = await asyncio.to_thread(
                    lambda: client.chat.completions.create(
                        model=model,
                        messages=[{"role": "user", "content": "Say 'Hello from River' briefly."}],
                        max_tokens=50
                    )
                )
                elapsed = (datetime.now() - start).total_seconds()

                if response.choices:
                    lines.append(f"✅ Success ({elapsed:.2f}s)")
                    lines.append(f"Response: _{response.choices[0].message.content[:100]}_")
                else:
                    lines.append("⚠️ Empty response")

            else:
                lines.append(f"❓ Unknown model type: {model}")
                lines.append("Supported: gemini-*, grok-*")

        except Exception as e:
            lines.append(f"❌ Error: {str(e)[:200]}")

        return "\n".join(lines)

    async def health_check(self) -> str:
        """Full system health check."""
        lines = ["🏥 *System Health Check*\n"]

        # Memory
        mem = psutil.virtual_memory()
        mem_emoji = "✅" if mem.percent < 80 else "⚠️" if mem.percent < 90 else "❌"
        lines.append(f"{mem_emoji} Memory: {mem.used/1024**3:.1f}GB / {mem.total/1024**3:.1f}GB ({mem.percent}%)")

        # Disk
        disk = psutil.disk_usage('/')
        disk_emoji = "✅" if disk.percent < 80 else "⚠️" if disk.percent < 90 else "❌"
        lines.append(f"{disk_emoji} Disk: {disk.used/1024**3:.1f}GB / {disk.total/1024**3:.1f}GB ({disk.percent}%)")

        # CPU
        cpu = psutil.cpu_percent(interval=1)
        cpu_emoji = "✅" if cpu < 70 else "⚠️" if cpu < 90 else "❌"
        lines.append(f"{cpu_emoji} CPU: {cpu}%")

        # Services
        lines.append("\n*Services:*")
        services = [
            ("river", "River Telegram"),
            ("redis-server", "Redis"),
        ]

        for svc, name in services:
            try:
                result = subprocess.run(
                    ["systemctl", "is-active", svc],
                    capture_output=True, text=True, timeout=5
                )
                status = result.stdout.strip()
                emoji = "✅" if status == "active" else "❌"
                lines.append(f"  {emoji} {name}: {status}")
            except:
                lines.append(f"  ❓ {name}: unknown")

        # Redis
        try:
            from river_redis import get_river_redis
            redis = get_river_redis()
            status = await redis.get_sos_status()
            if status.get("connected"):
                lines.append(f"  ✅ SOS Redis: connected")
            else:
                lines.append(f"  ❌ SOS Redis: {status.get('error', 'disconnected')}")
        except Exception as e:
            lines.append(f"  ❌ SOS Redis: {str(e)[:50]}")

        # Gemini Cache
        try:
            from river_gemini_cache import get_gemini_cache
            cache = get_gemini_cache()
            if cache and cache.cache_name:
                expires = cache.expires_at
                if expires and expires > datetime.now():
                    lines.append(f"  ✅ Gemini Cache: active (expires {expires.strftime('%H:%M')})")
                else:
                    lines.append(f"  ⚠️ Gemini Cache: expired")
            else:
                lines.append(f"  ⚠️ Gemini Cache: not initialized")
        except Exception as e:
            lines.append(f"  ❓ Gemini Cache: {str(e)[:30]}")

        return "\n".join(lines)

    async def get_logs(self, args: str = "") -> str:
        """Get recent log entries."""
        try:
            n = int(args) if args else 20
        except:
            n = 20

        n = min(n, 50)  # Cap at 50 lines

        lines = [f"📜 *Last {n} Log Entries*\n"]

        if RIVER_LOG.exists():
            try:
                log_lines = RIVER_LOG.read_text().split('\n')[-n-1:-1]
                for line in log_lines:
                    # Truncate long lines
                    if len(line) > 100:
                        line = line[:100] + "..."
                    # Escape markdown
                    line = line.replace('_', '\\_').replace('*', '\\*').replace('`', '')
                    if "ERROR" in line:
                        lines.append(f"❌ {line}")
                    elif "WARNING" in line:
                        lines.append(f"⚠️ {line}")
                    else:
                        lines.append(f"  {line}")
            except Exception as e:
                lines.append(f"Error reading logs: {e}")
        else:
            lines.append("Log file not found")

        return "\n".join(lines[:30])  # Limit output

    async def redis_status(self) -> str:
        """Get Redis/SOS status."""
        lines = ["📡 *SOS Redis Status*\n"]

        try:
            from river_redis import get_river_redis
            redis = get_river_redis()

            # Connection status
            connected = await redis.connect()
            if not connected:
                return "❌ Redis not connected"

            lines.append("✅ Connected to Redis\n")

            # Get detailed status
            status = await redis.get_sos_status()

            lines.append("*Streams:*")
            for stream, info in status.get("streams", {}).items():
                name = stream.split(":")[-1]
                length = info.get("length", 0)
                emoji = "🟢" if length > 0 else "⚪"
                lines.append(f"  {emoji} {name}: {length} messages")

            # Recent messages
            messages = await redis.read_from_sos(count=3)
            if messages:
                lines.append("\n*Recent Activity:*")
                for msg in messages[:3]:
                    agent = msg.get("agent", "?")
                    text = msg.get("message", "")[:50]
                    lines.append(f"  • [{agent}] {text}...")

        except Exception as e:
            lines.append(f"❌ Error: {str(e)[:100]}")

        return "\n".join(lines)

    async def memory_status(self) -> str:
        """Get detailed memory status."""
        lines = ["💾 *Memory Status*\n"]

        # System memory
        mem = psutil.virtual_memory()
        lines.append(f"*System:*")
        lines.append(f"  Total: {mem.total/1024**3:.2f} GB")
        lines.append(f"  Used: {mem.used/1024**3:.2f} GB ({mem.percent}%)")
        lines.append(f"  Available: {mem.available/1024**3:.2f} GB")

        # River process
        try:
            for proc in psutil.process_iter(['pid', 'name', 'memory_info', 'cmdline']):
                if 'river_service.py' in ' '.join(proc.info.get('cmdline', [])):
                    mem_info = proc.info['memory_info']
                    lines.append(f"\n*River Process (PID {proc.info['pid']}):*")
                    lines.append(f"  RSS: {mem_info.rss/1024**2:.1f} MB")
                    lines.append(f"  VMS: {mem_info.vms/1024**2:.1f} MB")
                    break
        except:
            pass

        # Gemini cache tokens
        try:
            from river_gemini_cache import get_gemini_cache
            cache = get_gemini_cache()
            if cache and cache.cached_tokens:
                lines.append(f"\n*Gemini Cache:*")
                lines.append(f"  Tokens: {cache.cached_tokens:,}")
        except:
            pass

        return "\n".join(lines)

    async def prune_logs(self, args: str = "") -> str:
        """Prune old log entries."""
        lines = ["🧹 *Log Pruning*\n"]

        if RIVER_LOG.exists():
            try:
                # Keep last 1000 lines
                log_lines = RIVER_LOG.read_text().split('\n')
                original_count = len(log_lines)

                if original_count > 1000:
                    kept_lines = log_lines[-1000:]
                    RIVER_LOG.write_text('\n'.join(kept_lines))
                    removed = original_count - 1000
                    lines.append(f"✅ Removed {removed} old log lines")
                    lines.append(f"Kept last 1000 lines")
                else:
                    lines.append(f"Log has {original_count} lines - no pruning needed")
            except Exception as e:
                lines.append(f"❌ Error: {e}")
        else:
            lines.append("Log file not found")

        return "\n".join(lines)

    async def restart_service(self, service: str) -> str:
        """Restart a service (admin only)."""
        if not service:
            return "Usage: `restart <service>`\nServices: river, redis"

        service = service.strip().lower()

        if service == "river":
            return "⚠️ Use `/restart` command to restart River"
        elif service == "redis":
            try:
                result = subprocess.run(
                    ["sudo", "systemctl", "restart", "redis-server"],
                    capture_output=True, text=True, timeout=10
                )
                if result.returncode == 0:
                    return "✅ Redis restarted"
                else:
                    return f"❌ Failed: {result.stderr}"
            except Exception as e:
                return f"❌ Error: {e}"
        else:
            return f"❓ Unknown service: {service}"

    async def execute_shell(self, cmd: str) -> str:
        """Execute a shell command (limited)."""
        if not cmd:
            return "Usage: `shell <command>`"

        # Whitelist safe commands
        safe_commands = ["ls", "df", "free", "uptime", "whoami", "date", "pwd", "cat", "head", "tail", "grep", "wc"]
        first_word = cmd.split()[0]

        if first_word not in safe_commands:
            return f"❌ Command '{first_word}' not in whitelist: {', '.join(safe_commands)}"

        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=10
            )
            output = result.stdout[:1000] if result.stdout else result.stderr[:500]
            return f"```\n{output}\n```" if output else "No output"
        except subprocess.TimeoutExpired:
            return "❌ Command timed out"
        except Exception as e:
            return f"❌ Error: {e}"

    async def show_goals(self) -> str:
        """Show current goals."""
        try:
            from river_goals import get_river_goals
            goals = get_river_goals()
            return goals.format_goals_for_display()
        except Exception as e:
            return f"❌ Error: {e}"


# Singleton
_tasks_instance: Optional[RiverTasks] = None


def get_river_tasks() -> RiverTasks:
    """Get River tasks singleton."""
    global _tasks_instance
    if _tasks_instance is None:
        _tasks_instance = RiverTasks()
    return _tasks_instance


async def run_task_command(args: str, user_id: str = "765204057") -> str:
    """
    Handle /task commands from Telegram.

    Usage:
        /task - Show help
        /task models - List models
        /task test gemini-3-flash - Test a model
        /task health - System health
        /task logs 30 - Show 30 log lines
        /task redis - SOS status
    """
    tasks = get_river_tasks()
    parts = args.strip().split(maxsplit=1) if args else []

    if not parts:
        return tasks.help()

    task = parts[0]
    task_args = parts[1] if len(parts) > 1 else ""

    return await tasks.run(task, task_args)

# Alias for consistency with other command handlers
tasks_command = run_task_command


if __name__ == "__main__":
    async def test():
        tasks = get_river_tasks()

        print("=== Models ===")
        print(await tasks.run("models"))

        print("\n=== Health ===")
        print(await tasks.run("health"))

        print("\n=== Redis ===")
        print(await tasks.run("redis"))

    asyncio.run(test())
