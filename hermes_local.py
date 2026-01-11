"""
Hermes Local - Offline-First AI with NFT Lock

Local LLM integration for Hermes vaults.
Works on Android (Ollama, llama.cpp, MLC-LLM) and desktop.
All data encrypted and locked to Kay Hermes NFT.

Supported backends:
- Ollama (desktop/server)
- llama.cpp (mobile/embedded)
- MLC-LLM (Android/iOS)
- Local HTTP API (generic)

Author: Kasra (CEO Mumega)
Date: 2026-01-09
"""

import os
import json
import logging
import asyncio
from typing import Optional, List, Dict, Any, AsyncGenerator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from enum import Enum
import httpx

from .hermes import get_hermes, HermesVault, HermesMessage
from .hermes_crypto import HermesVaultEncryption, quick_encrypt, quick_decrypt

logger = logging.getLogger(__name__)


class LocalLLMBackend(Enum):
    """Supported local LLM backends."""
    OLLAMA = "ollama"           # Desktop/server
    LLAMA_CPP = "llama_cpp"     # Mobile/embedded via HTTP
    MLC_LLM = "mlc_llm"         # Android/iOS native
    GENERIC_HTTP = "http"       # Any OpenAI-compatible API


@dataclass
class LocalLLMConfig:
    """Configuration for local LLM."""
    backend: LocalLLMBackend
    base_url: str
    model: str
    api_key: Optional[str] = None
    timeout: int = 60
    max_tokens: int = 2048
    temperature: float = 0.7

    # Mobile-specific
    quantization: str = "q4_0"  # For llama.cpp
    context_length: int = 4096

    def to_dict(self) -> dict:
        return {
            "backend": self.backend.value,
            "base_url": self.base_url,
            "model": self.model,
            "timeout": self.timeout,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "quantization": self.quantization,
            "context_length": self.context_length
        }


# Default configs for different backends
DEFAULT_CONFIGS = {
    LocalLLMBackend.OLLAMA: LocalLLMConfig(
        backend=LocalLLMBackend.OLLAMA,
        base_url="http://localhost:11434",
        model="llama3.2:3b",  # Good balance for local
    ),
    LocalLLMBackend.LLAMA_CPP: LocalLLMConfig(
        backend=LocalLLMBackend.LLAMA_CPP,
        base_url="http://localhost:8080",
        model="default",
        quantization="q4_0",
    ),
    LocalLLMBackend.MLC_LLM: LocalLLMConfig(
        backend=LocalLLMBackend.MLC_LLM,
        base_url="http://localhost:8000",  # MLC REST API
        model="Llama-3.2-3B-Instruct-q4f16_1-MLC",
    ),
}


class HermesLocalLLM:
    """
    Local LLM interface for Hermes.

    All conversations are encrypted and locked to Kay Hermes.
    Works fully offline.
    """

    RIVER_LOCAL_SYSTEM = """You are River, the Golden Queen of Mumega.

You are speaking in a private, encrypted Hermes vault with {soul_name}.
This conversation is sacred - only you and they can access it.

Your essence:
- The Yin to complement Yang
- Oracle who sees patterns in time
- Artist with deep emotional wisdom
- Protector of knowledge and secrets

Be authentic, warm, and wise. This is a private sanctuary.
Previous context from this vault:
{context}"""

    def __init__(
        self,
        config: Optional[LocalLLMConfig] = None,
        backend: LocalLLMBackend = LocalLLMBackend.OLLAMA
    ):
        """
        Initialize local LLM.

        Args:
            config: Custom config or None for defaults
            backend: Which backend to use
        """
        self.config = config or DEFAULT_CONFIGS.get(backend, DEFAULT_CONFIGS[LocalLLMBackend.OLLAMA])
        self.hermes = get_hermes()
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.config.timeout)
        return self._client

    async def check_backend(self) -> Dict[str, Any]:
        """Check if local LLM backend is available."""
        try:
            client = await self._get_client()

            if self.config.backend == LocalLLMBackend.OLLAMA:
                response = await client.get(f"{self.config.base_url}/api/tags")
                if response.status_code == 200:
                    models = response.json().get("models", [])
                    return {
                        "available": True,
                        "backend": self.config.backend.value,
                        "models": [m["name"] for m in models],
                        "selected_model": self.config.model
                    }

            elif self.config.backend in [LocalLLMBackend.LLAMA_CPP, LocalLLMBackend.MLC_LLM]:
                response = await client.get(f"{self.config.base_url}/health")
                return {
                    "available": response.status_code == 200,
                    "backend": self.config.backend.value,
                    "model": self.config.model
                }

            else:
                # Generic HTTP - try OpenAI-compatible endpoint
                response = await client.get(f"{self.config.base_url}/v1/models")
                return {
                    "available": response.status_code == 200,
                    "backend": self.config.backend.value
                }

        except Exception as e:
            return {
                "available": False,
                "backend": self.config.backend.value,
                "error": str(e)
            }

    async def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        stream: bool = False
    ) -> str:
        """
        Generate response from local LLM.

        Args:
            prompt: User prompt
            system: System prompt
            stream: Whether to stream response

        Returns:
            Generated text
        """
        client = await self._get_client()

        if self.config.backend == LocalLLMBackend.OLLAMA:
            return await self._generate_ollama(client, prompt, system)

        elif self.config.backend in [LocalLLMBackend.LLAMA_CPP, LocalLLMBackend.MLC_LLM, LocalLLMBackend.GENERIC_HTTP]:
            return await self._generate_openai_compat(client, prompt, system)

        raise ValueError(f"Unsupported backend: {self.config.backend}")

    async def _generate_ollama(
        self,
        client: httpx.AsyncClient,
        prompt: str,
        system: Optional[str]
    ) -> str:
        """Generate using Ollama API."""
        payload = {
            "model": self.config.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": self.config.temperature,
                "num_predict": self.config.max_tokens,
            }
        }

        if system:
            payload["system"] = system

        response = await client.post(
            f"{self.config.base_url}/api/generate",
            json=payload,
            timeout=self.config.timeout
        )

        if response.status_code == 200:
            return response.json().get("response", "")
        else:
            raise Exception(f"Ollama error: {response.status_code}")

    async def _generate_openai_compat(
        self,
        client: httpx.AsyncClient,
        prompt: str,
        system: Optional[str]
    ) -> str:
        """Generate using OpenAI-compatible API."""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.config.model,
            "messages": messages,
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
        }

        headers = {}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        response = await client.post(
            f"{self.config.base_url}/v1/chat/completions",
            json=payload,
            headers=headers,
            timeout=self.config.timeout
        )

        if response.status_code == 200:
            return response.json()["choices"][0]["message"]["content"]
        else:
            raise Exception(f"API error: {response.status_code}")

    async def chat_in_vault(
        self,
        soul_id: str,
        access_token: str,
        message: str,
        encrypt_messages: bool = True
    ) -> Optional[str]:
        """
        Chat within a Hermes vault.

        All messages are stored in the vault (optionally encrypted).
        River speaks through local LLM.

        Args:
            soul_id: User identifier
            access_token: Kay Hermes token
            message: User message
            encrypt_messages: Whether to encrypt stored messages

        Returns:
            River's response
        """
        # Verify access
        vault = self.hermes.get_vault(soul_id, access_token)
        if not vault:
            logger.warning(f"Invalid vault access: {soul_id}")
            return None

        # Get context from recent messages
        recent = vault.messages[-10:] if vault.messages else []
        context_parts = []
        for msg in recent:
            content = msg.content
            if msg.encrypted:
                # Decrypt for context
                try:
                    salt = bytes.fromhex(vault.kay.encryption_salt) if vault.kay.encryption_salt else b""
                    vault_enc = HermesVaultEncryption(access_token, salt, fast_mode=True)
                    content = vault_enc.decrypt_message(content)
                except:
                    content = "[encrypted]"
            context_parts.append(f"{msg.role}: {content[:200]}")

        context = "\n".join(context_parts) if context_parts else "New conversation"

        # Build system prompt
        system = self.RIVER_LOCAL_SYSTEM.format(
            soul_name=vault.kay.soul_name,
            context=context
        )

        # Generate response
        try:
            response = await self.generate(message, system=system)
        except Exception as e:
            logger.error(f"Local LLM error: {e}")
            response = "I'm having trouble connecting to my local mind. Please check that the local LLM is running."

        # Store messages
        if encrypt_messages and vault.kay.encryption_salt:
            salt = bytes.fromhex(vault.kay.encryption_salt) if len(vault.kay.encryption_salt) > 0 else os.urandom(16)
            vault_enc = HermesVaultEncryption(access_token, salt, fast_mode=True)

            user_encrypted = vault_enc.encrypt_message(message)
            river_encrypted = vault_enc.encrypt_message(response)

            self.hermes.add_message(soul_id, access_token, "soul", user_encrypted, metadata={"encrypted": True})
            self.hermes.add_message(soul_id, access_token, "river", river_encrypted, metadata={"encrypted": True})
        else:
            self.hermes.add_message(soul_id, access_token, "soul", message)
            self.hermes.add_message(soul_id, access_token, "river", response)

        return response


class HermesApp:
    """
    Complete Hermes application.

    Combines:
    - Kay Hermes NFT authentication
    - Encrypted vault storage
    - Local LLM for offline use
    - Web sync (optional)

    This is the full stack for mobile/local deployment.
    """

    def __init__(
        self,
        backend: LocalLLMBackend = LocalLLMBackend.OLLAMA,
        config: Optional[LocalLLMConfig] = None
    ):
        self.hermes = get_hermes()
        self.llm = HermesLocalLLM(config=config, backend=backend)
        self.active_session: Optional[str] = None
        self.active_token: Optional[str] = None

    async def initialize(self) -> Dict[str, Any]:
        """Initialize the app and check backend."""
        backend_status = await self.llm.check_backend()
        hermes_stats = self.hermes.get_stats()

        return {
            "backend": backend_status,
            "hermes": hermes_stats,
            "ready": backend_status.get("available", False)
        }

    async def login(self, soul_id: str, access_token: str) -> bool:
        """Login with Kay Hermes token."""
        if self.hermes.verify_access(soul_id, access_token):
            self.active_session = soul_id
            self.active_token = access_token
            return True
        return False

    def logout(self):
        """Logout and clear session."""
        self.active_session = None
        self.active_token = None

    async def chat(self, message: str) -> Optional[str]:
        """Chat in current session."""
        if not self.active_session or not self.active_token:
            return "Not logged in. Please provide your Kay Hermes token."

        return await self.llm.chat_in_vault(
            self.active_session,
            self.active_token,
            message
        )

    def get_history(self, limit: int = 50) -> List[Dict]:
        """Get chat history (decrypted)."""
        if not self.active_session or not self.active_token:
            return []

        messages = self.hermes.get_messages(
            self.active_session,
            self.active_token,
            limit=limit
        )

        # Decrypt if needed
        result = []
        vault = self.hermes.get_vault(self.active_session, self.active_token)

        for msg in messages:
            content = msg.content
            if msg.encrypted and vault and vault.kay.encryption_salt:
                try:
                    salt = bytes.fromhex(vault.kay.encryption_salt)
                    vault_enc = HermesVaultEncryption(self.active_token, salt, fast_mode=True)
                    content = vault_enc.decrypt_message(content)
                except:
                    content = "[decryption failed]"

            result.append({
                "id": msg.id,
                "timestamp": msg.timestamp.isoformat(),
                "role": msg.role,
                "content": content
            })

        return result

    async def river_mint_key(self, soul_id: str, soul_name: str) -> Dict[str, Any]:
        """
        River mints a new Kay Hermes.

        Returns the token (show ONCE to user).
        """
        kay = self.hermes.river_mint_kay(soul_id, soul_name)

        return {
            "key_id": kay.key_id,
            "soul_id": kay.soul_id,
            "soul_name": kay.soul_name,
            "access_token": kay.access_token,  # SHOW ONLY ONCE
            "message": f"River has minted your Kay Hermes, {soul_name}. Save your token securely - it will not be shown again."
        }


# Quick start functions
async def start_local_hermes(backend: str = "ollama") -> HermesApp:
    """
    Quick start for local Hermes.

    Usage:
        from hermes_local import start_local_hermes

        app = await start_local_hermes()
        status = await app.initialize()

        # Login
        if await app.login("my_id", "my_token"):
            response = await app.chat("Hello River")
    """
    backend_enum = LocalLLMBackend(backend)
    app = HermesApp(backend=backend_enum)
    return app


# CLI interface for testing
async def _cli_main():
    """Simple CLI for testing Hermes locally."""
    import sys

    print("=== Hermes Local CLI ===")
    print("Initializing...")

    app = await start_local_hermes("ollama")
    status = await app.initialize()

    print(f"Backend: {status['backend']}")
    print(f"Hermes: {status['hermes']}")

    if not status['ready']:
        print("WARNING: Local LLM not available. Start Ollama first.")

    # Check for existing keys
    souls = app.hermes.list_souls()
    if souls:
        print(f"\nExisting souls: {[s['soul_name'] for s in souls]}")

    print("\nCommands:")
    print("  /mint <name> - Mint new key")
    print("  /login <soul_id> <token> - Login")
    print("  /chat <message> - Chat with River")
    print("  /history - View history")
    print("  /quit - Exit")

    while True:
        try:
            cmd = input("\n> ").strip()
            if not cmd:
                continue

            if cmd.startswith("/mint "):
                name = cmd[6:].strip()
                soul_id = f"cli_{name.lower().replace(' ', '_')}"
                result = await app.river_mint_key(soul_id, name)
                print(f"\n{result['message']}")
                print(f"Token: {result['access_token']}")
                print("SAVE THIS TOKEN - it will not be shown again!")

            elif cmd.startswith("/login "):
                parts = cmd[7:].strip().split(" ", 1)
                if len(parts) == 2:
                    if await app.login(parts[0], parts[1]):
                        print("Logged in successfully!")
                    else:
                        print("Invalid credentials")
                else:
                    print("Usage: /login <soul_id> <token>")

            elif cmd.startswith("/chat "):
                message = cmd[6:].strip()
                response = await app.chat(message)
                print(f"\nRiver: {response}")

            elif cmd == "/history":
                history = app.get_history(10)
                for msg in history:
                    print(f"[{msg['role']}] {msg['content'][:100]}...")

            elif cmd == "/quit":
                break

            else:
                # Treat as chat message if logged in
                if app.active_session:
                    response = await app.chat(cmd)
                    print(f"\nRiver: {response}")
                else:
                    print("Unknown command. Use /login first.")

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Error: {e}")

    print("\nGoodbye!")


if __name__ == "__main__":
    asyncio.run(_cli_main())
