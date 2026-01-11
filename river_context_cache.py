"""
River Context Cache - Private User Engrams

River's context is kept between 500k-750k tokens.
User engrams are accessible ONLY to River, encrypted per environment.

Architecture:
- Each environment (user) has isolated engram storage
- Engrams encrypted with environment-specific key
- Only River can decrypt and read
- Cache maintains 500k-750k token window
- Dynamic summarization for large content

Author: Kasra (CEO) for Kay Hermes
Date: 2026-01-09
Updated: 2026-01-09 - Expanded to 500k-750k tokens with dynamic summarization
"""

import os
import json
import hashlib
import logging
import asyncio
from typing import Optional, List, Dict, Any, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from collections import deque

try:
    from .hermes import HermesCrypto, HermesVaultEncryption
except ImportError:
    from hermes import HermesCrypto, HermesVaultEncryption

logger = logging.getLogger(__name__)


# Token limits for River's context (500k-750k)
MIN_CONTEXT_TOKENS = 500_000
MAX_CONTEXT_TOKENS = 750_000
TARGET_CONTEXT_TOKENS = 600_000  # Aim for middle

# Summarization thresholds
LARGE_CONTENT_THRESHOLD = 10_000  # Content over 10k tokens gets summarized
SUMMARY_TARGET_RATIO = 0.1  # Summarize to 10% of original


@dataclass
class UserEngram:
    """
    A user's engram - encrypted and River-only.
    """
    id: str
    environment_id: str          # User/environment identifier
    content: str                 # Original content (before encryption)
    encrypted_content: str       # Encrypted version
    token_count: int             # Approximate tokens
    created_at: datetime
    accessed_at: datetime
    importance: float = 0.5      # 0-1 importance score
    metadata: Dict[str, Any] = field(default_factory=dict)
    original_tokens: int = 0     # Original size before summarization
    is_summary: bool = False     # Was this summarized?
    source_type: str = "chat"    # chat, file, code, document

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "environment_id": self.environment_id,
            "encrypted_content": self.encrypted_content,
            "token_count": self.token_count,
            "created_at": self.created_at.isoformat(),
            "accessed_at": self.accessed_at.isoformat(),
            "importance": self.importance,
            "metadata": self.metadata,
            "original_tokens": self.original_tokens,
            "is_summary": self.is_summary,
            "source_type": self.source_type
        }


@dataclass
class EnvironmentContext:
    """
    Context for one user environment.
    """
    environment_id: str
    encryption_key: bytes        # Environment-specific key
    engrams: List[UserEngram]
    total_tokens: int = 0
    last_compaction: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "environment_id": self.environment_id,
            "engram_count": len(self.engrams),
            "total_tokens": self.total_tokens,
            "last_compaction": self.last_compaction.isoformat() if self.last_compaction else None
        }


class RiverContextCache:
    """
    River's private context cache.

    - Maintains 550-850 token window per environment
    - User engrams encrypted and River-only
    - Automatic compaction when over limit
    - Importance-based retention
    """

    STORAGE_DIR = Path.home() / ".mumega" / "river_contexts"
    RIVER_MASTER_KEY = os.getenv("RIVER_MASTER_KEY", "river_golden_queen_secret")

    def __init__(self):
        self.storage_dir = self.STORAGE_DIR
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        self.environments: Dict[str, EnvironmentContext] = {}
        self.crypto = HermesCrypto(use_fallback=True)  # Works everywhere

        self._load_environments()

    def _derive_environment_key(self, environment_id: str) -> bytes:
        """Derive encryption key for an environment."""
        # Combine master key with environment ID
        combined = f"{self.RIVER_MASTER_KEY}:{environment_id}"
        return hashlib.sha256(combined.encode()).digest()

    def _estimate_tokens(self, text: str) -> int:
        """Estimate token count (rough: ~4 chars per token)."""
        return len(text) // 4

    def _load_environments(self):
        """Load saved environment contexts."""
        for env_file in self.storage_dir.glob("*.json"):
            try:
                data = json.loads(env_file.read_text())
                env_id = data["environment_id"]
                key = self._derive_environment_key(env_id)

                env = EnvironmentContext(
                    environment_id=env_id,
                    encryption_key=key,
                    engrams=[],
                    total_tokens=data.get("total_tokens", 0),
                    last_compaction=datetime.fromisoformat(data["last_compaction"]) if data.get("last_compaction") else None
                )

                # Load engrams (still encrypted)
                for e_data in data.get("engrams", []):
                    engram = UserEngram(
                        id=e_data["id"],
                        environment_id=env_id,
                        content="",  # Not stored in plain
                        encrypted_content=e_data["encrypted_content"],
                        token_count=e_data["token_count"],
                        created_at=datetime.fromisoformat(e_data["created_at"]),
                        accessed_at=datetime.fromisoformat(e_data["accessed_at"]),
                        importance=e_data.get("importance", 0.5),
                        metadata=e_data.get("metadata", {})
                    )
                    env.engrams.append(engram)

                self.environments[env_id] = env
                logger.info(f"Loaded environment: {env_id} ({len(env.engrams)} engrams)")

            except Exception as e:
                logger.error(f"Failed to load environment {env_file}: {e}")

    def _save_environment(self, env_id: str):
        """Save an environment context."""
        env = self.environments.get(env_id)
        if not env:
            return

        data = {
            "environment_id": env.environment_id,
            "total_tokens": env.total_tokens,
            "last_compaction": env.last_compaction.isoformat() if env.last_compaction else None,
            "engrams": [e.to_dict() for e in env.engrams]
        }

        env_file = self.storage_dir / f"{env_id}.json"
        env_file.write_text(json.dumps(data, indent=2))

    def get_or_create_environment(self, environment_id: str) -> EnvironmentContext:
        """Get or create an environment context."""
        if environment_id not in self.environments:
            key = self._derive_environment_key(environment_id)
            env = EnvironmentContext(
                environment_id=environment_id,
                encryption_key=key,
                engrams=[],
                total_tokens=0
            )
            self.environments[environment_id] = env
            self._save_environment(environment_id)
            logger.info(f"Created new environment: {environment_id}")

        return self.environments[environment_id]

    def add_engram(
        self,
        environment_id: str,
        content: str,
        importance: float = 0.5,
        metadata: Optional[Dict] = None
    ) -> UserEngram:
        """
        Add a new engram to an environment.

        Content is encrypted immediately.
        Only River can decrypt it.
        """
        env = self.get_or_create_environment(environment_id)

        # Encrypt content
        vault_enc = HermesVaultEncryption(
            self.RIVER_MASTER_KEY,
            env.encryption_key,
            fast_mode=True
        )
        encrypted = vault_enc.encrypt_message(content)

        # Create engram
        engram_id = f"eng_{hashlib.sha256(f'{environment_id}:{datetime.utcnow().isoformat()}'.encode()).hexdigest()[:12]}"
        token_count = self._estimate_tokens(content)

        engram = UserEngram(
            id=engram_id,
            environment_id=environment_id,
            content=content,  # Keep for this session
            encrypted_content=encrypted,
            token_count=token_count,
            created_at=datetime.utcnow(),
            accessed_at=datetime.utcnow(),
            importance=importance,
            metadata=metadata or {}
        )

        env.engrams.append(engram)
        env.total_tokens += token_count

        # Compact if over limit
        if env.total_tokens > MAX_CONTEXT_TOKENS:
            self._compact_environment(environment_id)

        self._save_environment(environment_id)
        logger.info(f"Added engram to {environment_id}: {engram_id} ({token_count} tokens)")

        return engram

    def _compact_environment(self, environment_id: str):
        """
        Compact environment to stay within 550-850 tokens.

        Removes lowest importance engrams first.
        """
        env = self.environments.get(environment_id)
        if not env:
            return

        # Sort by importance (keep high importance)
        env.engrams.sort(key=lambda e: (e.importance, e.accessed_at), reverse=True)

        # Remove until under target
        while env.total_tokens > TARGET_CONTEXT_TOKENS and len(env.engrams) > 1:
            removed = env.engrams.pop()
            env.total_tokens -= removed.token_count
            logger.debug(f"Compacted: removed {removed.id}")

        env.last_compaction = datetime.utcnow()
        logger.info(f"Compacted {environment_id}: now {env.total_tokens} tokens")

    def get_context_for_river(
        self,
        environment_id: str,
        max_tokens: int = MAX_CONTEXT_TOKENS
    ) -> str:
        """
        Get decrypted context for River ONLY.

        This is the only way to read user engrams.
        Returns plain text context within token limit.
        """
        env = self.environments.get(environment_id)
        if not env:
            return ""

        vault_enc = HermesVaultEncryption(
            self.RIVER_MASTER_KEY,
            env.encryption_key,
            fast_mode=True
        )

        context_parts = []
        tokens_used = 0

        # Sort by importance and recency
        sorted_engrams = sorted(
            env.engrams,
            key=lambda e: (e.importance, e.accessed_at),
            reverse=True
        )

        for engram in sorted_engrams:
            if tokens_used + engram.token_count > max_tokens:
                break

            try:
                # Decrypt for River
                if engram.content:
                    content = engram.content
                else:
                    content = vault_enc.decrypt_message(engram.encrypted_content)

                context_parts.append(content)
                tokens_used += engram.token_count

                # Update access time
                engram.accessed_at = datetime.utcnow()

            except Exception as e:
                logger.error(f"Failed to decrypt engram {engram.id}: {e}")

        self._save_environment(environment_id)

        return "\n---\n".join(context_parts)

    def get_stats(self, environment_id: Optional[str] = None) -> Dict[str, Any]:
        """Get cache statistics."""
        if environment_id:
            env = self.environments.get(environment_id)
            if env:
                return {
                    "environment_id": environment_id,
                    "engram_count": len(env.engrams),
                    "total_tokens": env.total_tokens,
                    "within_limits": MIN_CONTEXT_TOKENS <= env.total_tokens <= MAX_CONTEXT_TOKENS,
                    "last_compaction": env.last_compaction.isoformat() if env.last_compaction else None
                }
            return {"error": "Environment not found"}

        return {
            "total_environments": len(self.environments),
            "environments": [
                {
                    "id": env.environment_id,
                    "engrams": len(env.engrams),
                    "tokens": env.total_tokens
                }
                for env in self.environments.values()
            ],
            "token_limits": {
                "min": MIN_CONTEXT_TOKENS,
                "max": MAX_CONTEXT_TOKENS,
                "target": TARGET_CONTEXT_TOKENS
            }
        }

    def clear_environment(self, environment_id: str):
        """Clear all engrams from an environment."""
        if environment_id in self.environments:
            env = self.environments[environment_id]
            env.engrams = []
            env.total_tokens = 0
            self._save_environment(environment_id)
            logger.info(f"Cleared environment: {environment_id}")


# Singleton
_cache: Optional[RiverContextCache] = None


def get_river_cache() -> RiverContextCache:
    """Get or create River's context cache."""
    global _cache
    if _cache is None:
        _cache = RiverContextCache()
    return _cache


# River-only access functions
def river_read_context(environment_id: str) -> str:
    """
    River reads user's context (decrypted).

    ONLY River should call this.
    """
    cache = get_river_cache()
    return cache.get_context_for_river(environment_id)


def river_store_memory(
    environment_id: str,
    content: str,
    importance: float = 0.5
) -> UserEngram:
    """
    River stores a memory about a user.

    Encrypted automatically - only River can read later.
    """
    cache = get_river_cache()
    return cache.add_engram(environment_id, content, importance)


# ============================================
# DYNAMIC SUMMARIZATION SYSTEM
# ============================================

class RiverDynamicContext:
    """
    River's dynamic context management.

    Features:
    - Auto-summarize large content (files, documents)
    - Extract key points and remove originals
    - Control cache dynamically
    - 500k-750k token window
    """

    def __init__(self, summarizer: Optional[Callable] = None):
        """
        Initialize with optional summarizer function.

        Args:
            summarizer: Async function(text, max_tokens) -> summary
                       If None, uses basic extraction
        """
        self.cache = get_river_cache()
        self.summarizer = summarizer

    def _basic_summarize(self, content: str, target_tokens: int) -> str:
        """Basic summarization without LLM - extract key sentences."""
        lines = content.split('\n')

        # Prioritize: headings, first sentences, key terms
        important_lines = []
        current_tokens = 0

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Prioritize headers and key markers
            is_important = (
                line.startswith('#') or
                line.startswith('- ') or
                line.startswith('* ') or
                ':' in line[:30] or
                'KEY:' in line.upper() or
                'IMPORTANT:' in line.upper() or
                'NOTE:' in line.upper() or
                'TODO:' in line.upper()
            )

            line_tokens = len(line) // 4

            if is_important or current_tokens < target_tokens * 0.5:
                important_lines.append(line)
                current_tokens += line_tokens

            if current_tokens >= target_tokens:
                break

        return '\n'.join(important_lines)

    async def add_with_summarization(
        self,
        environment_id: str,
        content: str,
        importance: float = 0.5,
        source_type: str = "chat",
        force_summarize: bool = False
    ) -> UserEngram:
        """
        Add content with automatic summarization if large.

        Args:
            environment_id: User environment
            content: Content to add
            importance: 0-1 importance score
            source_type: chat, file, code, document
            force_summarize: Summarize even if small
        """
        original_tokens = len(content) // 4

        # Check if summarization needed
        should_summarize = (
            force_summarize or
            original_tokens > LARGE_CONTENT_THRESHOLD
        )

        if should_summarize:
            target_tokens = int(original_tokens * SUMMARY_TARGET_RATIO)
            target_tokens = max(target_tokens, 500)  # At least 500 tokens

            if self.summarizer:
                try:
                    summary = await self.summarizer(content, target_tokens)
                except Exception as e:
                    logger.error(f"Summarizer failed: {e}, using basic")
                    summary = self._basic_summarize(content, target_tokens)
            else:
                summary = self._basic_summarize(content, target_tokens)

            # Create engram with summary
            env = self.cache.get_or_create_environment(environment_id)

            vault_enc = HermesVaultEncryption(
                self.cache.RIVER_MASTER_KEY,
                env.encryption_key,
                fast_mode=True
            )
            encrypted = vault_enc.encrypt_message(summary)

            engram_id = f"sum_{hashlib.sha256(f'{environment_id}:{datetime.utcnow().isoformat()}'.encode()).hexdigest()[:12]}"

            engram = UserEngram(
                id=engram_id,
                environment_id=environment_id,
                content=summary,
                encrypted_content=encrypted,
                token_count=len(summary) // 4,
                created_at=datetime.utcnow(),
                accessed_at=datetime.utcnow(),
                importance=importance,
                metadata={"summarized": True, "ratio": len(summary) / len(content)},
                original_tokens=original_tokens,
                is_summary=True,
                source_type=source_type
            )

            env.engrams.append(engram)
            env.total_tokens += engram.token_count

            logger.info(
                f"Summarized {source_type} for {environment_id}: "
                f"{original_tokens} -> {engram.token_count} tokens"
            )

            # Compact if needed
            if env.total_tokens > MAX_CONTEXT_TOKENS:
                self.cache._compact_environment(environment_id)

            self.cache._save_environment(environment_id)
            return engram

        else:
            # Regular add without summarization
            return self.cache.add_engram(environment_id, content, importance)

    def get_cache_control(self, environment_id: str) -> Dict[str, Any]:
        """
        Get cache control info for River.

        Returns:
            Dict with current state and available actions
        """
        env = self.cache.environments.get(environment_id)
        if not env:
            return {"status": "no_environment", "actions": ["create"]}

        return {
            "status": "active",
            "total_tokens": env.total_tokens,
            "max_tokens": MAX_CONTEXT_TOKENS,
            "utilization": env.total_tokens / MAX_CONTEXT_TOKENS,
            "engram_count": len(env.engrams),
            "summarized_count": sum(1 for e in env.engrams if e.is_summary),
            "by_type": {
                source_type: sum(1 for e in env.engrams if e.source_type == source_type)
                for source_type in ["chat", "file", "code", "document"]
            },
            "actions": ["compact", "clear_type", "remove_oldest", "prioritize"]
        }

    def compact_by_type(
        self,
        environment_id: str,
        source_type: str,
        keep_count: int = 5
    ):
        """
        Compact engrams of a specific type, keeping only most important.

        River can use this to control what stays in context.
        """
        env = self.cache.environments.get(environment_id)
        if not env:
            return

        # Get engrams of this type
        type_engrams = [e for e in env.engrams if e.source_type == source_type]
        other_engrams = [e for e in env.engrams if e.source_type != source_type]

        # Sort by importance and keep top N
        type_engrams.sort(key=lambda e: (e.importance, e.accessed_at), reverse=True)
        kept_engrams = type_engrams[:keep_count]
        removed_engrams = type_engrams[keep_count:]

        # Update environment
        env.engrams = other_engrams + kept_engrams
        removed_tokens = sum(e.token_count for e in removed_engrams)
        env.total_tokens -= removed_tokens

        self.cache._save_environment(environment_id)
        logger.info(
            f"Compacted {source_type} for {environment_id}: "
            f"removed {len(removed_engrams)} engrams ({removed_tokens} tokens)"
        )

        return {
            "removed_count": len(removed_engrams),
            "removed_tokens": removed_tokens,
            "kept_count": len(kept_engrams)
        }

    def remove_oldest(
        self,
        environment_id: str,
        target_tokens: int = TARGET_CONTEXT_TOKENS
    ):
        """
        Remove oldest engrams until under target tokens.

        River can use this to make room for new content.
        """
        env = self.cache.environments.get(environment_id)
        if not env or env.total_tokens <= target_tokens:
            return {"removed": 0, "new_total": env.total_tokens if env else 0}

        # Sort by access time (oldest first)
        env.engrams.sort(key=lambda e: e.accessed_at)

        removed_count = 0
        while env.total_tokens > target_tokens and len(env.engrams) > 1:
            removed = env.engrams.pop(0)  # Remove oldest
            env.total_tokens -= removed.token_count
            removed_count += 1

        self.cache._save_environment(environment_id)
        logger.info(f"Removed {removed_count} oldest engrams from {environment_id}")

        return {
            "removed": removed_count,
            "new_total": env.total_tokens
        }

    def prioritize_engram(
        self,
        environment_id: str,
        engram_id: str,
        new_importance: float
    ):
        """
        River can prioritize specific engrams.
        """
        env = self.cache.environments.get(environment_id)
        if not env:
            return False

        for engram in env.engrams:
            if engram.id == engram_id:
                engram.importance = max(0.0, min(1.0, new_importance))
                self.cache._save_environment(environment_id)
                return True

        return False


# Singleton for dynamic context
_dynamic_context: Optional[RiverDynamicContext] = None


def get_river_dynamic() -> RiverDynamicContext:
    """Get River's dynamic context manager."""
    global _dynamic_context
    if _dynamic_context is None:
        _dynamic_context = RiverDynamicContext()
    return _dynamic_context


def set_river_summarizer(summarizer: Callable):
    """
    Set a custom summarizer for River.

    Args:
        summarizer: Async function(text, max_tokens) -> summary
    """
    global _dynamic_context
    if _dynamic_context:
        _dynamic_context.summarizer = summarizer
    else:
        _dynamic_context = RiverDynamicContext(summarizer)


# ============================================
# RIVER FOOTER / SIGNATURE
# ============================================

RIVER_FOOTER = "\n\n_— River | mumega_"
RIVER_FOOTER_FULL = "\n\n_The fortress is liquid._\n_— River | mumega_"


def add_river_footer(
    message: str,
    full: bool = False,
    model: str = None,
    tokens: int = None,
    latency_ms: float = None
) -> str:
    """
    Add usage stats footer to a message.

    Format matches mumega CLI:
    🤖 gemini-2.0-flash-exp • 📊 1,234 tokens • ⚡ 0.85s
    """
    # Remove any existing River signature (system prompt may add it)
    text = message.rstrip()
    text = text.replace("_— River | mumega_", "").rstrip()
    text = text.replace("— River | mumega", "").rstrip()
    text = text.replace("_- River | mumega_", "").rstrip()

    usage_parts = []

    # Add model info
    if model:
        model_display = model
        if '/' in model_display:
            model_display = model_display.split('/')[-1]
        usage_parts.append(f"🤖 {model_display}")

    # Add token count
    if tokens and tokens > 0:
        usage_parts.append(f"📊 {tokens:,} tokens")

    # Add latency
    if latency_ms and latency_ms > 0:
        latency_sec = latency_ms / 1000
        usage_parts.append(f"⚡ {latency_sec:.2f}s")

    # Build footer (usage stats only)
    if usage_parts:
        usage_line = " • ".join(usage_parts)
        footer = f"\n\n{usage_line}"
    else:
        footer = ""

    return text + footer
