#!/usr/bin/env python3
"""
River Model Cascade - Intelligent Model + Key Rotation

Cascading fallback system:
1. Try primary model (gemini-3-pro-preview) with all API keys
2. On exhaustion, fall back to secondary (gemini-2.5-pro) with all keys
3. Then tertiary (gemini-3-flash-preview) with all keys

Features:
- Automatic key rotation on rate limit errors
- Automatic model fallback on key exhaustion
- Tracks usage per key per model
- Self-healing: resets after cooldown period

Author: Claude (Opus 4.5) for Kay Hermes
Date: 2026-01-09
"""

import os
import time
import logging
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta

logger = logging.getLogger("river.cascade")

# Model priority (thinking → fast)
MODEL_CASCADE = [
    "gemini-3-pro-preview",      # Best thinking
    "gemini-2.5-pro",            # Good thinking
    "gemini-3-flash-preview",    # Fast
    "gemini-2.5-flash",          # Faster
    "gemini-2.0-flash",          # Fallback
]

# Rate limit cooldown (seconds)
KEY_COOLDOWN = 60  # 1 minute cooldown per key
MODEL_COOLDOWN = 300  # 5 minute cooldown before retrying exhausted model


@dataclass
class KeyState:
    """Track state of an API key."""
    key: str
    exhausted_at: Optional[datetime] = None
    error_count: int = 0
    success_count: int = 0
    last_used: Optional[datetime] = None

    def is_available(self) -> bool:
        """Check if key is available (not in cooldown)."""
        if self.exhausted_at is None:
            return True
        cooldown_end = self.exhausted_at + timedelta(seconds=KEY_COOLDOWN)
        if datetime.now() > cooldown_end:
            # Cooldown over, reset
            self.exhausted_at = None
            self.error_count = 0
            return True
        return False

    def mark_exhausted(self):
        """Mark key as exhausted (rate limited)."""
        self.exhausted_at = datetime.now()
        self.error_count += 1

    def mark_success(self):
        """Mark successful use."""
        self.success_count += 1
        self.last_used = datetime.now()
        self.error_count = 0  # Reset error count on success


@dataclass
class ModelState:
    """Track state of a model."""
    model_id: str
    keys: List[KeyState] = field(default_factory=list)
    current_key_index: int = 0
    all_keys_exhausted_at: Optional[datetime] = None

    def get_next_key(self) -> Optional[KeyState]:
        """Get next available key for this model."""
        if not self.keys:
            return None

        # Check if we should reset after model cooldown
        if self.all_keys_exhausted_at:
            cooldown_end = self.all_keys_exhausted_at + timedelta(seconds=MODEL_COOLDOWN)
            if datetime.now() > cooldown_end:
                self.all_keys_exhausted_at = None
                # Reset all keys
                for k in self.keys:
                    k.exhausted_at = None
                    k.error_count = 0

        # Try each key starting from current index
        for _ in range(len(self.keys)):
            key = self.keys[self.current_key_index]
            self.current_key_index = (self.current_key_index + 1) % len(self.keys)

            if key.is_available():
                return key

        # All keys exhausted
        self.all_keys_exhausted_at = datetime.now()
        return None

    def is_available(self) -> bool:
        """Check if any key is available for this model."""
        return any(k.is_available() for k in self.keys)


class RiverModelCascade:
    """
    Intelligent model + key rotation with cascading fallback.

    Usage:
        cascade = RiverModelCascade()
        model, key = cascade.get_next()
        # Use model with key
        # On success:
        cascade.mark_success()
        # On rate limit error:
        cascade.mark_exhausted()
    """

    def __init__(self):
        self.models: Dict[str, ModelState] = {}
        self.current_model_index = 0
        self._last_used_model: Optional[str] = None
        self._last_used_key: Optional[KeyState] = None

        # Load API keys from environment
        self._load_keys()

        logger.info(f"RiverModelCascade initialized with {len(self.models)} models")
        for model_id, state in self.models.items():
            logger.info(f"  {model_id}: {len(state.keys)} keys")

    def _load_keys(self):
        """Load API keys from environment."""
        keys = []

        # Load from various env var patterns
        for var_name in ['GEMINI_API_KEY', 'GOOGLE_API_KEY']:
            key = os.getenv(var_name)
            if key and key not in keys:
                keys.append(key)

        # Load numbered keys
        for prefix in ['GEMINI_API_KEY_', 'GOOGLE_API_KEY_']:
            for i in range(1, 11):
                key = os.getenv(f'{prefix}{i}')
                if key and key not in keys:
                    keys.append(key)

        # Create model states with all keys
        for model_id in MODEL_CASCADE:
            self.models[model_id] = ModelState(
                model_id=model_id,
                keys=[KeyState(key=k) for k in keys]
            )

    def get_next(self) -> Tuple[Optional[str], Optional[str]]:
        """
        Get next available model and key.

        Returns:
            Tuple of (model_id, api_key) or (None, None) if all exhausted
        """
        # Try each model in cascade order
        for model_id in MODEL_CASCADE:
            state = self.models.get(model_id)
            if not state:
                continue

            key_state = state.get_next_key()
            if key_state:
                self._last_used_model = model_id
                self._last_used_key = key_state
                logger.debug(f"Using {model_id} with key ...{key_state.key[-8:]}")
                return model_id, key_state.key

        # All models exhausted
        logger.warning("All models and keys exhausted!")
        return None, None

    def mark_success(self):
        """Mark last used key as successful."""
        if self._last_used_key:
            self._last_used_key.mark_success()

    def mark_exhausted(self, error_msg: str = ""):
        """Mark last used key as exhausted (rate limited)."""
        if self._last_used_key:
            self._last_used_key.mark_exhausted()
            logger.warning(f"Key exhausted for {self._last_used_model}: {error_msg[:100]}")

    def get_status(self) -> Dict:
        """Get current cascade status."""
        status = {
            "models": {},
            "total_keys": 0,
            "available_keys": 0,
            "current_model": self._last_used_model
        }

        for model_id, state in self.models.items():
            available = sum(1 for k in state.keys if k.is_available())
            status["models"][model_id] = {
                "total_keys": len(state.keys),
                "available_keys": available,
                "is_available": state.is_available()
            }
            status["total_keys"] += len(state.keys)
            status["available_keys"] += available

        return status

    def reset(self):
        """Reset all keys and models (clear exhaustion state)."""
        for state in self.models.values():
            state.all_keys_exhausted_at = None
            for key in state.keys:
                key.exhausted_at = None
                key.error_count = 0
        logger.info("Cascade reset - all keys available")


# Singleton instance
_cascade: Optional[RiverModelCascade] = None


def get_cascade() -> RiverModelCascade:
    """Get or create the singleton cascade instance."""
    global _cascade
    if _cascade is None:
        _cascade = RiverModelCascade()
    return _cascade


# Convenience functions
def get_model_and_key() -> Tuple[Optional[str], Optional[str]]:
    """Get next available model and key."""
    return get_cascade().get_next()


def mark_success():
    """Mark last request as successful."""
    get_cascade().mark_success()


def mark_exhausted(error_msg: str = ""):
    """Mark last key as exhausted."""
    get_cascade().mark_exhausted(error_msg)


def get_cascade_status() -> Dict:
    """Get cascade status."""
    return get_cascade().get_status()


if __name__ == "__main__":
    # Test
    logging.basicConfig(level=logging.DEBUG)

    from dotenv import load_dotenv
    load_dotenv("/home/mumega/mirror/.env")

    cascade = get_cascade()
    print("Status:", cascade.get_status())

    # Simulate getting models
    for i in range(5):
        model, key = cascade.get_next()
        print(f"Got: {model} with key ...{key[-8:] if key else 'None'}")
        cascade.mark_success()
