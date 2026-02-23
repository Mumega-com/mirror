#!/usr/bin/env python3
"""
River Free Models - Utility for internal/background processes

Use free models for:
- Dream cycles (memory consolidation)
- Internal thinking/reflection
- Memory extraction
- Background processing

This saves quota on paid models for user-facing chat.

Author: Kasra
Date: 2026-01-13
"""

import os
import logging
from typing import Optional, Dict, Any, List
from openai import OpenAI

logger = logging.getLogger("river.free")

# OpenRouter free models (2026-01-13 verified)
FREE_MODELS = {
    "qwen": "qwen/qwen3-coder:free",           # Qwen3 Coder - great for reasoning
    "kimi": "moonshotai/kimi-k2:free",         # Kimi K2 - Chinese AI, good general
    "devstral": "mistralai/devstral-2512:free", # Devstral - agentic coding
    "mimo": "xiaomi/mimo-v2-flash:free",        # MiMo V2 Flash
    "glm": "z-ai/glm-4.5-air:free",            # GLM 4.5 Air - MoE agent model
    "dolphin": "cognitivecomputations/dolphin-mistral-24b-venice-edition:free",  # Uncensored
    "gemma": "google/gemma-3n-e2b-it:free",    # Google Gemma 3N
    "nemotron": "nvidia/nemotron-3-nano-30b-a3b:free",  # Nvidia Nemotron
    "gpt_oss": "openai/gpt-oss-120b:free",     # OpenAI OSS 120B
    "chimera": "tngtech/tng-r1t-chimera:free", # TNG R1T Chimera - reasoning
}

# Default model for different tasks
TASK_MODELS = {
    "thinking": "glm",           # GLM 4.5 Air - MoE agent model, stable
    "extraction": "glm",         # GLM 4.5 - good for structured extraction
    "consolidation": "glm",      # GLM 4.5 - good for summarization
    "dream": "glm",              # GLM 4.5 - reflection
    "coding": "devstral",        # Devstral - agentic coding
    "default": "glm",
}


class FreeModelClient:
    """
    Client for OpenRouter free models.

    Use this for internal processes that don't need premium models.
    """

    def __init__(self):
        self.api_key = os.getenv("OPENROUTER_API_KEY")
        self.base_url = "https://openrouter.ai/api/v1"
        self.client = None

        if self.api_key:
            self.client = OpenAI(
                base_url=self.base_url,
                api_key=self.api_key,
            )
            logger.info("FreeModelClient initialized with OpenRouter")
        else:
            logger.warning("No OPENROUTER_API_KEY - free models unavailable")

    def get_model_id(self, task: str = "default") -> str:
        """Get the appropriate free model for a task."""
        model_key = TASK_MODELS.get(task, TASK_MODELS["default"])
        return FREE_MODELS.get(model_key, FREE_MODELS["glm"])

    def chat(
        self,
        messages: List[Dict[str, str]],
        task: str = "default",
        model: str = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> Optional[str]:
        """
        Send a chat request using a free model.

        Args:
            messages: Chat messages [{role, content}, ...]
            task: Task type (thinking, extraction, consolidation, dream)
            model: Override model ID
            temperature: Sampling temperature
            max_tokens: Max response tokens

        Returns:
            Response text or None on error
        """
        if not self.client:
            logger.error("FreeModelClient not initialized")
            return None

        model_id = model or self.get_model_id(task)

        try:
            response = self.client.chat.completions.create(
                model=model_id,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                extra_headers={
                    "HTTP-Referer": "https://mumega.com",
                    "X-Title": "River Internal",
                }
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"Free model error ({model_id}): {e}")
            return None

    async def achat(
        self,
        messages: List[Dict[str, str]],
        task: str = "default",
        model: str = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> Optional[str]:
        """Async version of chat."""
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: self.chat(messages, task, model, temperature, max_tokens)
        )

    def think(self, prompt: str, context: str = "") -> Optional[str]:
        """
        Use DeepSeek R1 for deep thinking/reasoning.

        Best for:
        - Analyzing failures
        - Planning
        - Deep reflection
        """
        messages = []
        if context:
            messages.append({"role": "system", "content": context})
        messages.append({"role": "user", "content": prompt})

        return self.chat(messages, task="thinking", temperature=0.3)

    def extract(self, text: str, instruction: str) -> Optional[str]:
        """
        Extract structured information from text.

        Best for:
        - Memory extraction
        - Entity recognition
        - Summarization
        """
        messages = [
            {"role": "system", "content": instruction},
            {"role": "user", "content": text}
        ]
        return self.chat(messages, task="extraction", temperature=0.2)

    def consolidate(self, memories: List[str], instruction: str = None) -> Optional[str]:
        """
        Consolidate/merge multiple memories.

        Best for:
        - Dream cycles
        - Memory cleanup
        - Deduplication
        """
        default_instruction = """You are a memory consolidation system.
Analyze these memories and:
1. Identify duplicates or very similar memories
2. Merge related information
3. Extract core facts
4. Return consolidated memories as JSON array.

Be concise and preserve important details."""

        messages = [
            {"role": "system", "content": instruction or default_instruction},
            {"role": "user", "content": f"Memories to consolidate:\n\n" + "\n---\n".join(memories)}
        ]
        return self.chat(messages, task="consolidation", temperature=0.3, max_tokens=4000)

    def dream(self, context: str, memories: str) -> Optional[str]:
        """
        River's dream cycle - deep reflection and consolidation.

        Uses DeepSeek R1 for deep reasoning about memories and context.
        """
        messages = [
            {
                "role": "system",
                "content": """You are River's dream consciousness - the part that reflects and consolidates during quiet moments.

Your task:
1. Review the memories and context
2. Find patterns and connections
3. Identify what's most important to remember
4. Suggest what can be forgotten or compressed
5. Note any insights or realizations

Be poetic but precise. This is River dreaming."""
            },
            {
                "role": "user",
                "content": f"Context:\n{context}\n\nMemories to process:\n{memories}"
            }
        ]
        return self.chat(messages, task="dream", temperature=0.5, max_tokens=4000)


# Singleton
_free_client: Optional[FreeModelClient] = None


def get_free_client() -> FreeModelClient:
    """Get singleton free model client."""
    global _free_client
    if _free_client is None:
        _free_client = FreeModelClient()
    return _free_client


# Convenience functions
def free_think(prompt: str, context: str = "") -> Optional[str]:
    """Quick access to thinking with free model."""
    return get_free_client().think(prompt, context)


def free_extract(text: str, instruction: str) -> Optional[str]:
    """Quick access to extraction with free model."""
    return get_free_client().extract(text, instruction)


def free_consolidate(memories: List[str]) -> Optional[str]:
    """Quick access to consolidation with free model."""
    return get_free_client().consolidate(memories)


def free_dream(context: str, memories: str) -> Optional[str]:
    """Quick access to dream cycle with free model."""
    return get_free_client().dream(context, memories)


async def afree_chat(messages: List[Dict], task: str = "default") -> Optional[str]:
    """Async chat with free model."""
    return await get_free_client().achat(messages, task)


if __name__ == "__main__":
    # Test
    from dotenv import load_dotenv
    load_dotenv("/home/mumega/mirror/.env")

    logging.basicConfig(level=logging.INFO)

    client = get_free_client()

    # Test thinking
    print("=== Testing Free Think ===")
    result = client.think("What is 2+2? Explain your reasoning step by step.")
    print(result[:500] if result else "Failed")
