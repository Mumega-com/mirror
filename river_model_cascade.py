#!/usr/bin/env python3
"""
River Model Cascade - Intelligent Multi-Provider Fallback

Provider cascade:
1. Gemini (with key rotation across 6 free tier keys)
2. Grok (xAI) - secondary provider
3. OpenRouter (free models) - final fallback

Within Gemini:
1. Try primary model (gemini-3-flash-preview) with all API keys
2. On exhaustion, fall back to gemini-2.5-flash, then gemini-2.0-flash

Features:
- Multi-provider cascading fallback
- Automatic key rotation on rate limit errors
- Per-provider model fallback
- Self-healing: resets after cooldown period

Author: Claude (Opus 4.5) for Kay Hermes
Date: 2026-01-09, Updated: 2026-01-13
"""

import os
import time
import logging
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta

logger = logging.getLogger("river.cascade")

# ==============================================================================
# PROVIDER CASCADE CONFIGURATION
# ==============================================================================

# Provider priority
PROVIDER_CASCADE = ["gemini", "grok", "openrouter"]

# Gemini model priority (flash first for better quota)
GEMINI_MODEL_CASCADE = [
    "gemini-3-flash-preview",    # Primary - fast, decent quota
    "gemini-2.5-flash",          # Faster, better quota
    "gemini-2.0-flash",          # Fallback, highest quota
    "gemini-3-pro-preview",      # Best thinking (low quota)
    "gemini-2.5-pro",            # Good thinking
]

# Grok models (xAI)
GROK_MODEL_CASCADE = [
    "grok-3-mini-fast",          # Fast, cheapest
    "grok-3-mini",               # Balanced
    "grok-3-fast",               # Fast full model
]

# OpenRouter free models (2026-01-13 verified via API)
OPENROUTER_FREE_MODELS = [
    "qwen/qwen3-coder:free",                 # Qwen3 Coder - great for reasoning
    "moonshotai/kimi-k2:free",               # Kimi K2 - good general purpose
    "mistralai/devstral-2512:free",          # Devstral 2 - agentic coding
    "z-ai/glm-4.5-air:free",                 # GLM 4.5 Air - MoE agent model
    "xiaomi/mimo-v2-flash:free",             # MiMo-V2 Flash
    "tngtech/tng-r1t-chimera:free",          # TNG R1T Chimera - reasoning
    "openai/gpt-oss-120b:free",              # OpenAI OSS 120B
]

# Rate limit cooldown (seconds)
KEY_COOLDOWN = 60  # 1 minute cooldown per key
MODEL_COOLDOWN = 300  # 5 minute cooldown before retrying exhausted model
PROVIDER_COOLDOWN = 600  # 10 minute cooldown for entire provider


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


@dataclass
class ProviderState:
    """Track state of a provider."""
    provider: str
    models: List[ModelState] = field(default_factory=list)
    exhausted_at: Optional[datetime] = None
    current_model_index: int = 0

    def is_available(self) -> bool:
        """Check if provider has any available models."""
        if self.exhausted_at:
            cooldown_end = self.exhausted_at + timedelta(seconds=PROVIDER_COOLDOWN)
            if datetime.now() > cooldown_end:
                self.exhausted_at = None
            else:
                return False
        return any(m.is_available() for m in self.models)

    def get_next_model_and_key(self) -> Tuple[Optional[str], Optional[str]]:
        """Get next available model and key for this provider."""
        for _ in range(len(self.models)):
            model = self.models[self.current_model_index]
            self.current_model_index = (self.current_model_index + 1) % len(self.models)

            key_state = model.get_next_key()
            if key_state:
                return model.model_id, key_state.key

        # All models exhausted for this provider
        self.exhausted_at = datetime.now()
        return None, None


class RiverModelCascade:
    """
    Multi-provider cascade with intelligent fallback.

    Provider cascade: Gemini → Grok → OpenRouter

    Within each provider:
    - Gemini: Multiple models with key rotation
    - Grok: Single key, multiple models
    - OpenRouter: Single key, free models

    Usage:
        cascade = RiverModelCascade()
        provider, model, key = cascade.get_next()
        # Use model with key
        # On success:
        cascade.mark_success()
        # On rate limit error:
        cascade.mark_exhausted()
    """

    def __init__(self):
        self.providers: Dict[str, ProviderState] = {}
        self._last_used_provider: Optional[str] = None
        self._last_used_model: Optional[str] = None
        self._last_used_key: Optional[KeyState] = None
        self._last_model_state: Optional[ModelState] = None

        # Load all providers
        self._load_gemini()
        self._load_grok()
        self._load_openrouter()

        self._log_status()

    def _load_gemini(self):
        """Load Gemini provider with key rotation."""
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

        if not keys:
            logger.warning("No Gemini API keys found")
            return

        # Create model states with all keys
        models = []
        for model_id in GEMINI_MODEL_CASCADE:
            models.append(ModelState(
                model_id=model_id,
                keys=[KeyState(key=k) for k in keys]
            ))

        self.providers["gemini"] = ProviderState(provider="gemini", models=models)
        logger.info(f"Gemini: {len(keys)} keys × {len(models)} models")

    def _load_grok(self):
        """Load Grok/xAI provider."""
        key = os.getenv("XAI_API_KEY")
        if not key:
            logger.warning("No XAI_API_KEY found for Grok")
            return

        models = []
        for model_id in GROK_MODEL_CASCADE:
            models.append(ModelState(
                model_id=model_id,
                keys=[KeyState(key=key)]
            ))

        self.providers["grok"] = ProviderState(provider="grok", models=models)
        logger.info(f"Grok: 1 key × {len(models)} models")

    def _load_openrouter(self):
        """Load OpenRouter with free models."""
        key = os.getenv("OPENROUTER_API_KEY")
        if not key:
            logger.warning("No OPENROUTER_API_KEY found")
            return

        models = []
        for model_id in OPENROUTER_FREE_MODELS:
            models.append(ModelState(
                model_id=model_id,
                keys=[KeyState(key=key)]
            ))

        self.providers["openrouter"] = ProviderState(provider="openrouter", models=models)
        logger.info(f"OpenRouter: 1 key × {len(models)} free models")

    def _log_status(self):
        """Log initialization status."""
        total_models = sum(len(p.models) for p in self.providers.values())
        logger.info(f"RiverModelCascade: {len(self.providers)} providers, {total_models} models")

    def get_next(self) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Get next available provider, model, and key.

        Returns:
            Tuple of (provider, model_id, api_key) or (None, None, None) if all exhausted
        """
        for provider_name in PROVIDER_CASCADE:
            provider = self.providers.get(provider_name)
            if not provider or not provider.is_available():
                continue

            model_id, api_key = provider.get_next_model_and_key()
            if model_id and api_key:
                self._last_used_provider = provider_name
                self._last_used_model = model_id
                # Find the key state for marking
                for m in provider.models:
                    if m.model_id == model_id:
                        self._last_model_state = m
                        for ks in m.keys:
                            if ks.key == api_key:
                                self._last_used_key = ks
                                break
                        break

                logger.info(f"Cascade → {provider_name}/{model_id}")
                return provider_name, model_id, api_key

        logger.error("All providers exhausted!")
        return None, None, None

    def mark_success(self):
        """Mark last used key as successful."""
        if self._last_used_key:
            self._last_used_key.mark_success()

    def mark_exhausted(self, error_msg: str = ""):
        """Mark last used key as exhausted (rate limited)."""
        if self._last_used_key:
            self._last_used_key.mark_exhausted()
            logger.warning(f"Exhausted: {self._last_used_provider}/{self._last_used_model}: {error_msg[:80]}")

    def get_status(self) -> Dict:
        """Get current cascade status."""
        status = {
            "providers": {},
            "current_provider": self._last_used_provider,
            "current_model": self._last_used_model,
        }

        for name, provider in self.providers.items():
            models_status = {}
            for model in provider.models:
                available = sum(1 for k in model.keys if k.is_available())
                models_status[model.model_id] = {
                    "total_keys": len(model.keys),
                    "available_keys": available,
                }

            status["providers"][name] = {
                "is_available": provider.is_available(),
                "models": models_status,
            }

        return status

    def reset(self):
        """Reset all providers, models, and keys."""
        for provider in self.providers.values():
            provider.exhausted_at = None
            for model in provider.models:
                model.all_keys_exhausted_at = None
                for key in model.keys:
                    key.exhausted_at = None
                    key.error_count = 0
        logger.info("Cascade reset - all providers available")


# Singleton instance
_cascade: Optional[RiverModelCascade] = None


def get_cascade() -> RiverModelCascade:
    """Get or create the singleton cascade instance."""
    global _cascade
    if _cascade is None:
        _cascade = RiverModelCascade()
    return _cascade


# Convenience functions
def get_provider_model_and_key() -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Get next available provider, model, and key."""
    return get_cascade().get_next()


def get_model_and_key() -> Tuple[Optional[str], Optional[str]]:
    """Get next available model and key (backward compatible)."""
    _, model, key = get_cascade().get_next()
    return model, key


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
        provider, model, key = cascade.get_next()
        print(f"Got: {provider}/{model} with key ...{key[-8:] if key else 'None'}")
        cascade.mark_success()
