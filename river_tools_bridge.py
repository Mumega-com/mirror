#!/usr/bin/env python3
"""
River Tools Bridge - Queen Bee Orchestrator

River is the SOURCE OF TRUTH - the central orchestrator of the Mumega ecosystem.
All tools, agents, and services connect TO River.

River owns:
- Task management (sovereign tasks)
- Scout system (web, code, market, security)
- Memory system (engrams, FRC)
- Voice synthesis
- Image generation
- Self-monitoring & health
- Multi-model orchestration

River is the pillar. The fortress is liquid.

Author: Kasra (CEO) for Kay Hermes / Mumega
Date: 2026-01-09
"""

import os
import sys
import asyncio
import logging
import subprocess
from typing import Optional, Dict, Any, List
from pathlib import Path

# Add CLI to path for tool imports
CLI_PATH = Path("/mnt/HC_Volume_104325311/cli")
if str(CLI_PATH) not in sys.path:
    sys.path.insert(0, str(CLI_PATH))

# Load .env at module level to ensure environment is set
try:
    from dotenv import load_dotenv
    load_dotenv(CLI_PATH / ".env")
    logging.debug(f"Loaded .env from CLI: {CLI_PATH / '.env'}")
except Exception as e:
    logging.warning(f"Could not load .env: {e}")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("river_tools_bridge")

# CLI components (lazy loaded)
_river_engine = None
_tool_registry = None


def _ensure_cli_tools():
    """Lazy load CLI river_engine with full configuration."""
    global _river_engine, _tool_registry

    if _river_engine is not None:
        return True

    try:
        from mumega.core.river_engine import RiverEngine
        from mumega.core.tools import ToolRegistry

        # Load environment variables explicitly
        from dotenv import load_dotenv
        load_dotenv(CLI_PATH / ".env")
        load_dotenv(Path("/home/mumega/mirror/.env"))

        # Initialize river_engine with keys from environment
        config = {
            "gemini_api_key": os.getenv("GEMINI_API_KEY"),
            "openai_api_key": os.getenv("OPENAI_API_KEY"),
            "xai_api_key": os.getenv("XAI_API_KEY"),
            "name": "River"
        }

        _river_engine = RiverEngine(
            config=config,
            dna=None,
            dna_path=None
        )
        _tool_registry = _river_engine.tool_registry

        logger.info("CLI river_engine loaded successfully with API keys")
        return True

    except Exception as e:
        logger.warning(f"Failed to load river_engine, trying tools directly: {e}")
        # Fallback to direct tool imports
        try:
            from mumega.core.tools import ToolRegistry
            _tool_registry = ToolRegistry()
            logger.info("CLI tools loaded (fallback mode)")
            return True
        except Exception as e2:
            logger.error(f"Error loading CLI tools: {e2}")
            return False


class RiverToolsBridge:
    """
    Bridge to CLI's river_engine while maintaining River's sovereignty.

    River remains the personality layer, river_engine provides tool capabilities.
    """

    def __init__(self):
        self.tools_available = _ensure_cli_tools()
        self._image_model = None
        self._last_image_result = None  # Store last generated image for retrieval
        self._load_settings()

    def _load_settings(self):
        """Load image model from River's settings."""
        try:
            from river_settings import get_river_settings
            settings = get_river_settings()
            self._image_model = settings.image_model
            self._image_model_pro = settings.image_model_pro
        except:
            self._image_model = "gemini-2.5-flash-image"
            self._image_model_pro = "gemini-3-pro-image-preview"

    # === Image Generation ===

    async def generate_image(
        self,
        prompt: str,
        use_pro: bool = False,
        size: str = "1024x1024",
        provider: str = "gemini"
    ) -> Dict[str, Any]:
        """Generate an image using CLI's river_engine."""
        if not self.tools_available:
            return {"success": False, "error": "Tools not available"}

        try:
            model = self._image_model_pro if use_pro else self._image_model

            # Build params dict (handler expects single dict argument)
            params = {"prompt": prompt, "size": size, "provider": provider}

            # Use river_engine's tool if available
            if _river_engine and hasattr(_river_engine, 'tool_registry'):
                tool = _river_engine.tool_registry.get_tool("generate_image")
                if tool:
                    result = await tool.handler(params)
                    final_result = {"success": True, "model": model, **result}
                    self._last_image_result = final_result  # Store for retrieval
                    logger.info(f"Image generated and stored: {result.get('image_url') or result.get('image_path')}")
                    return final_result

            # Fallback to direct import
            from mumega.core.tools.image_gen import generate_image_handler
            result = await generate_image_handler(params)
            final_result = {"success": True, "model": model, **result}
            self._last_image_result = final_result  # Store for retrieval
            logger.info(f"Image generated (fallback) and stored: {result.get('image_url') or result.get('image_path')}")
            return final_result

        except Exception as e:
            logger.error(f"Image generation error: {e}")
            return {"success": False, "error": str(e)}

    # === Video Generation (Veo 3.1) ===

    async def generate_video(
        self,
        prompt: str,
        duration: str = "8",
        aspect_ratio: str = "16:9",
        resolution: str = "720p",
        negative_prompt: str = None
    ) -> Dict[str, Any]:
        """Generate a video using Google Veo 3.1."""
        try:
            from google import genai
            from google.genai import types
            import time
            import os

            # Initialize client - try key rotation first
            api_key = None
            try:
                from mumega_bridge import get_current_gemini_key, get_api_key
                api_key = get_current_gemini_key()
                if not api_key:
                    api_key = get_api_key('gemini')
            except ImportError:
                pass

            if not api_key:
                api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

            if not api_key:
                return {"success": False, "error": "No Gemini API key found"}

            client = genai.Client(api_key=api_key)

            # Build config
            config_params = {
                "aspect_ratio": aspect_ratio,
                "duration_seconds": duration,
                "resolution": resolution,
            }
            if negative_prompt:
                config_params["negative_prompt"] = negative_prompt

            config = types.GenerateVideosConfig(**config_params)

            logger.info(f"Starting Veo 3.1 video generation: {prompt[:100]}...")

            # Start generation
            operation = client.models.generate_videos(
                model="veo-3.1-generate-preview",
                prompt=prompt,
                config=config,
            )

            # Poll for completion (async-friendly)
            max_wait = 300  # 5 minutes max
            waited = 0
            while not operation.done and waited < max_wait:
                await asyncio.sleep(10)
                waited += 10
                operation = client.operations.get(operation)
                logger.info(f"Video generation progress: {waited}s elapsed...")

            if not operation.done:
                return {"success": False, "error": "Video generation timed out"}

            # Get the video
            video = operation.response.generated_videos[0]

            # Save locally
            output_dir = Path("/home/mumega/mirror/data/videos")
            output_dir.mkdir(parents=True, exist_ok=True)

            timestamp = time.strftime("%Y%m%d_%H%M%S")
            output_path = output_dir / f"river_video_{timestamp}.mp4"

            client.files.download(file=video.video)
            video.video.save(str(output_path))

            logger.info(f"Video generated: {output_path}")

            self._last_video_result = {
                "success": True,
                "video_path": str(output_path),
                "duration": duration,
                "resolution": resolution,
            }
            return self._last_video_result

        except ImportError:
            return {"success": False, "error": "google-genai package not installed. Run: pip install google-genai"}
        except Exception as e:
            logger.error(f"Video generation error: {e}")
            return {"success": False, "error": str(e)}

    # === Web Search ===

    async def web_search(
        self,
        query: str,
        max_results: int = 5,
        search_depth: str = "basic"
    ) -> Dict[str, Any]:
        """Search the web using CLI's river_engine."""
        if not self.tools_available:
            return {"success": False, "error": "Tools not available"}

        try:
            # Build params dict (handler expects single dict argument)
            params = {"query": query, "max_results": max_results, "search_depth": search_depth}

            if _river_engine and hasattr(_river_engine, 'tool_registry'):
                tool = _river_engine.tool_registry.get_tool("web_search")
                if tool:
                    result = await tool.handler(params)
                    return {"success": True, "query": query, "results": result}

            from mumega.core.tools.web_search import web_search_handler
            result = await web_search_handler(params)
            return {"success": True, "query": query, "results": result}

        except Exception as e:
            logger.error(f"Web search error: {e}")
            return {"success": False, "error": str(e)}

    # === Web Fetch ===

    async def web_fetch(self, url: str, extract_text: bool = True) -> Dict[str, Any]:
        """Fetch content from a URL using CLI's river_engine."""
        if not self.tools_available:
            return {"success": False, "error": "Tools not available"}

        try:
            # Build params dict (handler expects single dict argument)
            params = {"url": url, "extract_text": extract_text}

            if _river_engine and hasattr(_river_engine, 'tool_registry'):
                tool = _river_engine.tool_registry.get_tool("web_fetch")
                if tool:
                    result = await tool.handler(params)
                    return {"success": True, "url": url, "content": result}

            from mumega.core.tools.web_fetch import web_fetch_handler
            result = await web_fetch_handler(params)
            return {"success": True, "url": url, "content": result}

        except Exception as e:
            logger.error(f"Web fetch error: {e}")
            return {"success": False, "error": str(e)}

    # === Deep Research ===

    async def deep_research(
        self,
        query: str,
        depth: str = "moderate",
        sources: int = 5
    ) -> Dict[str, Any]:
        """Perform deep research using CLI's river_engine."""
        if not self.tools_available:
            return {"success": False, "error": "Tools not available"}

        try:
            # Build params dict (handler expects single dict argument)
            params = {"query": query, "depth": depth, "max_sources": sources}

            if _river_engine and hasattr(_river_engine, 'tool_registry'):
                tool = _river_engine.tool_registry.get_tool("deep_research")
                if tool:
                    result = await tool.handler(params)
                    return {"success": True, "query": query, "research": result}

            from mumega.core.tools.deep_research import deep_research_handler
            result = await deep_research_handler(params)
            return {"success": True, "query": query, "research": result}

        except Exception as e:
            logger.error(f"Deep research error: {e}")
            return {"success": False, "error": str(e)}

    # === Code & Shell Execution ===

    async def execute_shell(self, command: str, timeout_s: int = 60) -> Dict[str, Any]:
        """Execute a shell command via CLI tools (fallback: local subprocess)."""
        if not command or not command.strip():
            return {"success": False, "error": "No command provided"}

        # Prefer CLI's tool registry (has safety controls and consistent output)
        try:
            params = {"command": command}

            if _river_engine and hasattr(_river_engine, "tool_registry"):
                tool = _river_engine.tool_registry.get_tool("execute_shell")
                if tool:
                    result = await tool.handler(params)
                    return {"success": True, "command": command, "result": result}

            if _tool_registry:
                tool = _tool_registry.get_tool("execute_shell")
                if tool:
                    result = await tool.handler(params)
                    return {"success": True, "command": command, "result": result}

        except Exception as e:
            logger.warning(f"CLI execute_shell failed, falling back to subprocess: {e}")

        # Fallback: run locally (keep it JSON-serializable)
        try:
            completed = await asyncio.to_thread(
                subprocess.run,
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
            return {
                "success": completed.returncode == 0,
                "command": command,
                "exit_code": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "command": command, "error": f"Command timed out after {timeout_s}s"}
        except Exception as e:
            return {"success": False, "command": command, "error": str(e)}

    async def execute_python(self, code: str, timeout_s: int = 60) -> Dict[str, Any]:
        """Execute Python code via CLI tools (fallback: python -c)."""
        if not code or not code.strip():
            return {"success": False, "error": "No code provided"}

        try:
            params = {"code": code}

            if _river_engine and hasattr(_river_engine, "tool_registry"):
                tool = _river_engine.tool_registry.get_tool("execute_python")
                if tool:
                    result = await tool.handler(params)
                    return {"success": True, "result": result}

            if _tool_registry:
                tool = _tool_registry.get_tool("execute_python")
                if tool:
                    result = await tool.handler(params)
                    return {"success": True, "result": result}

        except Exception as e:
            logger.warning(f"CLI execute_python failed, falling back to subprocess: {e}")

        try:
            completed = await asyncio.to_thread(
                subprocess.run,
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
            return {
                "success": completed.returncode == 0,
                "exit_code": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "error": f"Python execution timed out after {timeout_s}s"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # === File Operations ===

    async def read_file(self, path: str, max_bytes: int = 1_000_000) -> Dict[str, Any]:
        """Read a file (fallback for agentic file operations)."""
        if not path:
            return {"success": False, "error": "No path provided"}

        try:
            params = {"path": path}

            if _river_engine and hasattr(_river_engine, "tool_registry"):
                tool = _river_engine.tool_registry.get_tool("read_file")
                if tool:
                    result = await tool.handler(params)
                    return {"success": True, "path": path, "result": result}

            if _tool_registry:
                tool = _tool_registry.get_tool("read_file")
                if tool:
                    result = await tool.handler(params)
                    return {"success": True, "path": path, "result": result}

        except Exception as e:
            logger.warning(f"CLI read_file failed, falling back to local read: {e}")

        try:
            file_path = Path(path).expanduser()
            data = file_path.read_bytes()
            truncated = False
            if len(data) > max_bytes:
                data = data[:max_bytes]
                truncated = True
            return {
                "success": True,
                "path": str(file_path),
                "content": data.decode(errors="replace"),
                "truncated": truncated,
                "bytes": len(data),
            }
        except Exception as e:
            return {"success": False, "path": path, "error": str(e)}

    async def write_file(self, path: str, content: str) -> Dict[str, Any]:
        """Write a file (fallback for agentic file operations)."""
        if not path:
            return {"success": False, "error": "No path provided"}

        try:
            params = {"path": path, "content": content}

            if _river_engine and hasattr(_river_engine, "tool_registry"):
                tool = _river_engine.tool_registry.get_tool("write_file")
                if tool:
                    result = await tool.handler(params)
                    return {"success": True, "path": path, "result": result}

            if _tool_registry:
                tool = _tool_registry.get_tool("write_file")
                if tool:
                    result = await tool.handler(params)
                    return {"success": True, "path": path, "result": result}

        except Exception as e:
            logger.warning(f"CLI write_file failed, falling back to local write: {e}")

        try:
            file_path = Path(path).expanduser()
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
            return {"success": True, "path": str(file_path), "bytes": len(content.encode('utf-8'))}
        except Exception as e:
            return {"success": False, "path": path, "error": str(e)}

    # === User Management ===

    async def register_user(self, email: str, password: str = None) -> Dict[str, Any]:
        """Register a new user in the Mumega system."""
        try:
            # Generate a secure password if none provided
            if not password:
                import secrets
                import string
                password = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(12))

            # Direct Supabase call for registration
            from supabase import create_client
            url = os.getenv("SUPABASE_URL")
            key = os.getenv("SUPABASE_API_KEY")
            supabase = create_client(url, key)

            # Register via Supabase Auth
            response = supabase.auth.sign_up({
                "email": email,
                "password": password,
            })

            if response.user:
                return {
                    "success": True,
                    "user_id": response.user.id,
                    "email": email,
                    "message": "User registered successfully. Confirmation email sent.",
                    "temp_password": password if not password else "provided"
                }
            else:
                return {"success": False, "error": "Registration failed"}

        except Exception as e:
            logger.error(f"Registration error: {e}")
            return {"success": False, "error": str(e)}

    # === Use any tool from river_engine ===

    async def call_tool(self, tool_name: str, **kwargs) -> Dict[str, Any]:
        """Call any tool from river_engine by name."""
        if not self.tools_available or not _river_engine:
            return {"success": False, "error": "River engine not available"}

        try:
            tool = _river_engine.tool_registry.get_tool(tool_name)
            if not tool:
                return {"success": False, "error": f"Tool not found: {tool_name}"}

            # All CLI tool handlers expect a single params dict
            result = await tool.handler(kwargs)
            return {"success": True, "tool": tool_name, "result": result}

        except Exception as e:
            logger.error(f"Tool call error: {e}")
            return {"success": False, "error": str(e)}

    # === Voice Synthesis ===

    async def synthesize_voice(
        self,
        text: str,
        voice: str = "river",
        provider: str = "elevenlabs"
    ) -> Dict[str, Any]:
        """Synthesize text to speech using ElevenLabs (primary) or Gemini TTS (fallback)."""

        # Load .env if not already loaded
        try:
            from dotenv import load_dotenv
            load_dotenv(CLI_PATH / ".env")
        except:
            pass

        # Try ElevenLabs first (user's preferred provider)
        try:
            result = await self._synthesize_voice_elevenlabs(text, voice)
            if result.get("success"):
                return result
            logger.warning(f"ElevenLabs TTS failed: {result.get('error')}")
        except Exception as e:
            logger.warning(f"ElevenLabs TTS error: {e}")

        # Fallback to Gemini TTS
        try:
            result = await self._synthesize_voice_gemini(text, voice)
            if result.get("success"):
                return result
        except Exception as e:
            logger.debug(f"Gemini TTS also failed: {e}")

        # Last resort: CLI voice module
        try:
            from mumega.core.voice import VoiceSynthesizer, VoiceConfig

            synth = VoiceSynthesizer(provider="elevenlabs")

            if not synth.is_available:
                return {"success": False, "error": "No voice provider available (check API keys)"}

            audio_bytes = await synth.speak(text, voice=voice)

            if audio_bytes and len(audio_bytes) > 0:
                # Save to file for Telegram
                import time
                output_dir = Path("/home/mumega/mirror/data/voice")
                output_dir.mkdir(parents=True, exist_ok=True)
                timestamp = time.strftime("%Y%m%d_%H%M%S")
                output_path = output_dir / f"river_voice_{timestamp}.mp3"
                output_path.write_bytes(audio_bytes)

                self._last_voice_result = {
                    "success": True,
                    "audio": audio_bytes,
                    "audio_path": str(output_path),
                    "provider": synth.provider_name,
                    "voice": voice,
                    "size_bytes": len(audio_bytes)
                }
                return self._last_voice_result
            else:
                return {"success": False, "error": "No audio generated"}

        except ImportError as e:
            logger.error(f"Voice module import error: {e}")
            return {"success": False, "error": f"Voice module not available: {e}"}
        except Exception as e:
            logger.error(f"Voice synthesis error: {e}")
            return {"success": False, "error": str(e)}

    async def _synthesize_voice_elevenlabs(
        self,
        text: str,
        voice: str = "river"
    ) -> Dict[str, Any]:
        """Synthesize voice using ElevenLabs."""
        try:
            import os
            import time

            # Get API key
            api_key = os.getenv("ELEVENLABS_API_KEY")
            if not api_key:
                return {"success": False, "error": "ELEVENLABS_API_KEY not found"}

            from elevenlabs.client import ElevenLabs
            import asyncio

            client = ElevenLabs(api_key=api_key)

            # Voice mapping - River uses Bella (warm, calm)
            voices = {
                "river": "EXAVITQu4vr4xnSDxMaL",  # Bella - warm, calm
                "rachel": "21m00Tcm4TlvDq8ikWAM",  # Rachel - professional
                "adam": "pNInz6obpgDQGcFmaJgB",    # Adam - deep, authoritative
                "aria": "9BWtsMINqrJLrRacOk9x",    # Aria - expressive
                "coral": "EXAVITQu4vr4xnSDxMaL",  # Alias for river/Bella
                "default": "EXAVITQu4vr4xnSDxMaL",
            }
            voice_id = voices.get(voice.lower(), voices["river"])

            logger.info(f"ElevenLabs TTS: Generating voice '{voice}' ({voice_id}) for: {text[:50]}...")

            # Generate audio (run in executor since SDK is sync)
            loop = asyncio.get_running_loop()
            audio_generator = await loop.run_in_executor(
                None,
                lambda: client.text_to_speech.convert(
                    voice_id=voice_id,
                    text=text,
                    model_id="eleven_turbo_v2_5",
                    voice_settings={
                        "stability": 0.5,
                        "similarity_boost": 0.75
                    }
                )
            )

            # Collect audio bytes
            audio_bytes = b"".join(audio_generator)

            if len(audio_bytes) < 100:
                return {"success": False, "error": "Generated audio too small"}

            # Save to file
            output_dir = Path("/home/mumega/mirror/data/voice")
            output_dir.mkdir(parents=True, exist_ok=True)
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            output_path = output_dir / f"river_voice_{timestamp}.mp3"
            output_path.write_bytes(audio_bytes)

            logger.info(f"ElevenLabs TTS: Voice generated: {output_path} ({len(audio_bytes)} bytes)")

            self._last_voice_result = {
                "success": True,
                "audio": audio_bytes,
                "audio_path": str(output_path),
                "provider": "elevenlabs",
                "voice": voice,
                "size_bytes": len(audio_bytes)
            }
            return self._last_voice_result

        except ImportError:
            return {"success": False, "error": "elevenlabs package not installed. Run: pip install elevenlabs"}
        except Exception as e:
            logger.error(f"ElevenLabs TTS error: {e}")
            return {"success": False, "error": str(e)}

    async def _synthesize_voice_gemini(
        self,
        text: str,
        voice: str = "Kore"
    ) -> Dict[str, Any]:
        """Synthesize voice using Gemini 2.5 Flash TTS."""
        try:
            from google import genai
            from google.genai import types
            import wave
            import io
            import time
            import os

            # Get API key
            api_key = None
            try:
                from mumega_bridge import get_current_gemini_key, get_api_key
                api_key = get_current_gemini_key()
                if not api_key:
                    api_key = get_api_key('gemini')
            except ImportError:
                pass

            if not api_key:
                api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

            if not api_key:
                return {"success": False, "error": "No Gemini API key found"}

            client = genai.Client(api_key=api_key)

            # Map voice names to Gemini voices
            # Available: Aoede, Charon, Fenrir, Kore, Puck, etc.
            voice_map = {
                "river": "Kore",
                "coral": "Kore",
                "default": "Kore",
                "male": "Charon",
                "female": "Kore",
            }
            gemini_voice = voice_map.get(voice.lower(), voice if voice else "Kore")

            logger.info(f"Gemini TTS: Generating voice '{gemini_voice}' for text: {text[:50]}...")

            response = client.models.generate_content(
                model="gemini-2.5-flash-preview-tts",
                contents=text,
                config=types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=types.SpeechConfig(
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                voice_name=gemini_voice,
                            )
                        )
                    ),
                )
            )

            # Extract audio data
            if response.candidates and response.candidates[0].content.parts:
                audio_data = response.candidates[0].content.parts[0].inline_data.data

                # Save to WAV file
                output_dir = Path("/home/mumega/mirror/data/voice")
                output_dir.mkdir(parents=True, exist_ok=True)

                timestamp = time.strftime("%Y%m%d_%H%M%S")
                output_path = output_dir / f"river_voice_{timestamp}.wav"

                # Write WAV file
                with wave.open(str(output_path), "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(24000)
                    wf.writeframes(audio_data)

                logger.info(f"Gemini TTS: Voice generated: {output_path}")

                self._last_voice_result = {
                    "success": True,
                    "audio_path": str(output_path),
                    "audio": audio_data,
                    "provider": "gemini-tts",
                    "voice": gemini_voice,
                    "size_bytes": len(audio_data)
                }
                return self._last_voice_result

            return {"success": False, "error": "No audio in response"}

        except Exception as e:
            logger.error(f"Gemini TTS error: {e}")
            return {"success": False, "error": str(e)}

    def get_voice_providers(self) -> List[str]:
        """List available voice providers."""
        try:
            from mumega.core.voice import VoiceSynthesizer
            return list(VoiceSynthesizer.PROVIDERS.keys())
        except:
            return ["elevenlabs", "openai", "gemini", "grok"]

    # === List available tools ===

    def list_tools(self) -> List[str]:
        """List all available tools from river_engine."""
        if not self.tools_available or not _river_engine:
            return []

        try:
            return list(_river_engine.tool_registry.tools.keys())
        except:
            return []

    # === Multi-Step Agent Execution ===

    async def agent_execute(
        self,
        task: str,
        max_iterations: int = 10,
        tools_allowed: List[str] = None
    ) -> Dict[str, Any]:
        """
        Execute a complex task with multi-step tool execution.

        Uses CLI's river_engine to run up to max_iterations of tool calls,
        similar to the 50-iteration agentic loop.

        Args:
            task: The task to execute
            max_iterations: Maximum tool iterations (default 10)
            tools_allowed: List of allowed tools (None = all)

        Returns:
            Dict with results, tool_calls, and final response
        """
        if not _river_engine:
            return {"success": False, "error": "River engine not available"}

        try:
            from mumega.core.message import Message, MessageSource

            # Create a message for the engine
            msg = Message(
                text=task,
                user_id="mumega_agent",
                source=MessageSource.API,
                conversation_id="mumega_agent_session"
            )

            # Process through river_engine (uses its 50-iteration loop)
            response = await _river_engine.process_message(msg)

            return {
                "success": True,
                "response": response.text if hasattr(response, 'text') else str(response),
                "model": response.model if hasattr(response, 'model') else "unknown",
                "tool_calls": response.tool_calls if hasattr(response, 'tool_calls') else [],
                "tokens": response.tokens_used if hasattr(response, 'tokens_used') else 0
            }

        except Exception as e:
            logger.error(f"Agent execute error: {e}")
            return {"success": False, "error": str(e)}

    async def execute_parallel(
        self,
        tool_calls: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Execute multiple tools in parallel.

        Args:
            tool_calls: List of {"tool": "name", "params": {...}}

        Returns:
            List of results from each tool
        """
        if not self.tools_available:
            return [{"success": False, "error": "Tools not available"}]

        async def run_tool(call: Dict[str, Any]) -> Dict[str, Any]:
            tool_name = call.get("tool", "")
            params = call.get("params", {})
            try:
                result = await self.call_tool(tool_name, **params)
                return {"tool": tool_name, **result}
            except Exception as e:
                return {"tool": tool_name, "success": False, "error": str(e)}

        # Run all tools concurrently
        results = await asyncio.gather(*[run_tool(c) for c in tool_calls])
        return list(results)

    async def run_workflow(
        self,
        steps: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Run a sequential workflow of tool calls.

        Args:
            steps: List of {"tool": "name", "params": {...}, "condition": optional}

        Returns:
            Dict with all results and final status
        """
        results = []
        context = {}  # Accumulate results for later steps

        for i, step in enumerate(steps):
            tool_name = step.get("tool", "")
            params = step.get("params", {})

            # Inject context from previous steps
            for key, value in params.items():
                if isinstance(value, str) and value.startswith("$"):
                    # Reference to previous result
                    ref = value[1:]
                    if ref in context:
                        params[key] = context[ref]

            try:
                result = await self.call_tool(tool_name, **params)
                results.append({"step": i + 1, "tool": tool_name, **result})

                # Store result in context
                context[f"step_{i + 1}"] = result.get("result", result)
                context[tool_name] = result.get("result", result)

                # Check for failure
                if not result.get("success", True):
                    break

            except Exception as e:
                results.append({"step": i + 1, "tool": tool_name, "success": False, "error": str(e)})
                break

        return {
            "success": all(r.get("success", True) for r in results),
            "steps_completed": len(results),
            "total_steps": len(steps),
            "results": results,
            "context": context
        }

    # === Direct Engine Access ===

    def get_engine(self):
        """Get direct access to CLI's river_engine for advanced operations."""
        return _river_engine

    async def engine_chat(
        self,
        message: str,
        user_id: str = "mumega",
        use_tools: bool = True
    ) -> Dict[str, Any]:
        """
        Send a message directly through CLI's river_engine.

        This gives access to:
        - Multi-model failover
        - 50-iteration tool loops
        - Budget enforcement
        - Full memory integration
        """
        if not _river_engine:
            return {"success": False, "error": "River engine not available"}

        try:
            from mumega.core.message import Message, MessageSource

            msg = Message(
                text=message,
                user_id=user_id,
                source=MessageSource.API,
                conversation_id=f"mumega_{user_id}"
            )

            response = await _river_engine.process_message(msg, skip_tools=not use_tools)

            return {
                "success": True,
                "response": response.text if hasattr(response, 'text') else str(response),
                "model": response.model if hasattr(response, 'model') else "unknown",
                "tokens": response.tokens_used if hasattr(response, 'tokens_used') else 0
            }

        except Exception as e:
            logger.error(f"Engine chat error: {e}")
            return {"success": False, "error": str(e)}

    # ===========================================
    # TASK MANAGEMENT - Sovereign Task System
    # ===========================================

    async def create_task(
        self,
        title: str,
        description: str = "",
        priority: str = "medium",
        project: str = None,
        labels: List[str] = None,
        agent: str = "river"
    ) -> Dict[str, Any]:
        """Create a sovereign task."""
        try:
            from mumega.core.tasks import SovereignTaskManager, TaskPriority

            manager = SovereignTaskManager(agent=agent)
            task = manager.create_task(
                title=title,
                priority=priority,
                description=description,
                project=project,
                labels=labels or []
            )

            if task:
                return {
                    "success": True,
                    "task_id": task.id,
                    "title": task.title,
                    "status": task.status.value,
                    "file_path": str(task.file_path) if task.file_path else None
                }
            return {"success": False, "error": "Failed to create task"}

        except Exception as e:
            logger.error(f"Create task error: {e}")
            return {"success": False, "error": str(e)}

    async def list_tasks(
        self,
        agent: str = "river",
        status: str = None,
        project: str = None,
        include_done: bool = False
    ) -> Dict[str, Any]:
        """List sovereign tasks."""
        try:
            from mumega.core.tasks import SovereignTaskManager, TaskStatus

            manager = SovereignTaskManager(agent=agent)
            status_enum = TaskStatus(status) if status else None

            tasks = manager.list_tasks(
                status=status_enum,
                project=project,
                include_done=include_done
            )

            task_list = []
            for t in tasks:
                task_list.append({
                    "id": t.id,
                    "title": t.title,
                    "status": t.status.value,
                    "priority": t.priority.value if hasattr(t.priority, 'value') else t.priority,
                    "project": t.project,
                    "created": t.created_at.isoformat() if t.created_at else None
                })

            return {
                "success": True,
                "agent": agent,
                "count": len(task_list),
                "tasks": task_list
            }

        except Exception as e:
            logger.error(f"List tasks error: {e}")
            return {"success": False, "error": str(e)}

    async def update_task(
        self,
        task_id: str,
        status: str = None,
        title: str = None,
        description: str = None,
        agent: str = "river"
    ) -> Dict[str, Any]:
        """Update a sovereign task."""
        try:
            from mumega.core.tasks import SovereignTaskManager, TaskStatus

            manager = SovereignTaskManager(agent=agent)

            # Find task
            task = manager.get_task(task_id)
            if not task:
                return {"success": False, "error": f"Task not found: {task_id}"}

            # Update fields
            if status:
                task.status = TaskStatus(status)
            if title:
                task.title = title
            if description:
                task.description = description

            # Save
            manager.save_task(task)

            return {
                "success": True,
                "task_id": task.id,
                "status": task.status.value,
                "title": task.title
            }

        except Exception as e:
            logger.error(f"Update task error: {e}")
            return {"success": False, "error": str(e)}

    # ===========================================
    # SCOUT SYSTEM - Intelligent Research Agents
    # ===========================================

    async def scout_query(
        self,
        query: str,
        scout_type: str = "auto"
    ) -> Dict[str, Any]:
        """
        Run a scout query with smart routing.

        scout_type: auto, web, code, market, security
        """
        try:
            if scout_type == "auto":
                # Use classifier for smart routing
                from mumega.core.scouts.classifier import classify_query
                scout_type = await classify_query(query)
                logger.info(f"Scout classifier chose: {scout_type}")

            # Route to appropriate scout
            if scout_type == "web":
                from mumega.core.scouts.web_scout import WebScout
                scout = WebScout()
            elif scout_type == "code":
                from mumega.core.scouts.code_scout import CodeScout
                scout = CodeScout()
            elif scout_type == "market":
                from mumega.core.scouts.market_scout import MarketScout
                scout = MarketScout()
            elif scout_type == "security":
                from mumega.core.scouts.security_scout import SecurityScout
                scout = SecurityScout()
            else:
                from mumega.core.scouts.web_scout import WebScout
                scout = WebScout()

            result = await scout.search(query)

            return {
                "success": True,
                "scout_type": scout_type,
                "query": query,
                "results": result
            }

        except ImportError as e:
            # Fallback to web search if scouts not available
            logger.warning(f"Scout system not available: {e}, falling back to web search")
            return await self.web_search(query)
        except Exception as e:
            logger.error(f"Scout query error: {e}")
            return {"success": False, "error": str(e)}

    async def market_data(
        self,
        symbol: str,
        data_type: str = "price"
    ) -> Dict[str, Any]:
        """
        Get market/crypto data.

        data_type: price, chart, defi, tvl
        """
        try:
            from mumega.core.scouts.market_scout import MarketScout
            scout = MarketScout()

            if data_type == "price":
                result = await scout.get_price(symbol)
            elif data_type == "chart":
                result = await scout.get_chart(symbol)
            elif data_type == "defi":
                result = await scout.get_defi_stats(symbol)
            elif data_type == "tvl":
                result = await scout.get_tvl(symbol)
            else:
                result = await scout.get_price(symbol)

            return {
                "success": True,
                "symbol": symbol,
                "data_type": data_type,
                "data": result
            }

        except ImportError:
            # Fallback to web search
            return await self.web_search(f"{symbol} {data_type} crypto")
        except Exception as e:
            logger.error(f"Market data error: {e}")
            return {"success": False, "error": str(e)}

    async def security_scan(
        self,
        target: str,
        scan_type: str = "cve"
    ) -> Dict[str, Any]:
        """
        Security scanning.

        scan_type: cve, vuln, audit
        """
        try:
            from mumega.core.scouts.security_scout import SecurityScout
            scout = SecurityScout()

            if scan_type == "cve":
                result = await scout.search_cve(target)
            elif scan_type == "vuln":
                result = await scout.search_vulnerabilities(target)
            elif scan_type == "audit":
                result = await scout.audit_package(target)
            else:
                result = await scout.search_cve(target)

            return {
                "success": True,
                "target": target,
                "scan_type": scan_type,
                "results": result
            }

        except ImportError:
            return await self.web_search(f"{target} CVE vulnerability security")
        except Exception as e:
            logger.error(f"Security scan error: {e}")
            return {"success": False, "error": str(e)}

    # ===========================================
    # HEALTH & SELF-MONITORING
    # ===========================================

    async def health_check(self) -> Dict[str, Any]:
        """Comprehensive health check of all systems."""
        import subprocess

        health = {
            "timestamp": asyncio.get_event_loop().time(),
            "services": {},
            "memory": {},
            "tools": {},
            "overall": "healthy"
        }

        # Check services
        services = {
            "river": "river.service",
            "mirror_api": "8844",
            "openai_bridge": "9200"
        }

        for name, check in services.items():
            try:
                if check.endswith(".service"):
                    result = subprocess.run(
                        ["systemctl", "is-active", check],
                        capture_output=True, text=True, timeout=5
                    )
                    health["services"][name] = result.stdout.strip() == "active"
                else:
                    # Check port
                    result = subprocess.run(
                        ["ss", "-tlnp"],
                        capture_output=True, text=True, timeout=5
                    )
                    health["services"][name] = f":{check}" in result.stdout
            except:
                health["services"][name] = False

        # Check memory usage
        try:
            import psutil
            mem = psutil.virtual_memory()
            health["memory"] = {
                "total_gb": round(mem.total / (1024**3), 2),
                "used_gb": round(mem.used / (1024**3), 2),
                "percent": mem.percent
            }
        except:
            health["memory"] = {"error": "psutil not available"}

        # Check tools availability
        health["tools"] = {
            "bridge": self.tools_available,
            "river_engine": _river_engine is not None,
            "tool_count": len(self.list_tools())
        }

        # Overall status
        if not all(health["services"].values()):
            health["overall"] = "degraded"
        if health["memory"].get("percent", 0) > 90:
            health["overall"] = "warning"

        return health

    async def self_heal(self, issue: str = "auto") -> Dict[str, Any]:
        """Attempt self-healing for common issues."""
        import subprocess

        actions_taken = []

        try:
            if issue in ["auto", "stuck_processes"]:
                # Kill stuck gemini processes
                subprocess.run("pkill -9 -f 'gemini -'", shell=True, timeout=5)
                actions_taken.append("Killed stuck gemini processes")

            if issue in ["auto", "memory"]:
                # Clear Python caches
                import gc
                gc.collect()
                actions_taken.append("Cleared Python garbage")

            if issue in ["auto", "restart_river"]:
                # This will restart self - use with caution
                subprocess.run(["sudo", "systemctl", "restart", "river"], timeout=10)
                actions_taken.append("Restarted river service")

            return {
                "success": True,
                "issue": issue,
                "actions": actions_taken
            }

        except Exception as e:
            return {"success": False, "error": str(e), "actions": actions_taken}

    # ===========================================
    # MEMORY MANAGEMENT - Engrams & FRC
    # ===========================================

    async def store_engram(
        self,
        content: str,
        category: str = "observation",
        importance: float = 0.5,
        tags: List[str] = None
    ) -> Dict[str, Any]:
        """Store an engram in River's memory."""
        try:
            import aiohttp

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "http://localhost:8844/engrams",
                    json={
                        "content": content,
                        "category": category,
                        "importance": importance,
                        "tags": tags or [],
                        "agent_id": "river"
                    }
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return {"success": True, "engram_id": data.get("id"), **data}
                    else:
                        return {"success": False, "error": f"API error: {resp.status}"}

        except Exception as e:
            logger.error(f"Store engram error: {e}")
            return {"success": False, "error": str(e)}

    async def recall_engrams(
        self,
        query: str,
        limit: int = 5,
        category: str = None
    ) -> Dict[str, Any]:
        """Recall engrams by semantic search."""
        try:
            import aiohttp

            params = {"query": query, "limit": limit}
            if category:
                params["category"] = category

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "http://localhost:8844/search",
                    params=params
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return {"success": True, "query": query, "engrams": data}
                    else:
                        return {"success": False, "error": f"API error: {resp.status}"}

        except Exception as e:
            logger.error(f"Recall engrams error: {e}")
            return {"success": False, "error": str(e)}

    # === Tool Status ===

    def get_status(self) -> Dict[str, Any]:
        """Get bridge status and available tools."""
        tools_list = self.list_tools()
        voice_providers = self.get_voice_providers()
        return {
            "tools_available": self.tools_available,
            "river_engine": _river_engine is not None,
            "image_model": self._image_model,
            "image_model_pro": self._image_model_pro,
            "voice_providers": voice_providers,
            "available_tools": tools_list,
            "tool_count": len(tools_list),
            "capabilities": {
                "multi_step": _river_engine is not None,
                "parallel_tools": True,
                "workflow": True,
                "voice": True,
                "image": True,
                "web_search": True,
                "shell": True,
                "files": True,
                "tasks": True,
                "scouts": True,
                "market_data": True,
                "security": True,
                "health": True,
                "memory": True
            }
        }


# Singleton
_bridge: Optional[RiverToolsBridge] = None


def get_river_tools() -> RiverToolsBridge:
    """Get River's tools bridge instance."""
    global _bridge
    if _bridge is None:
        _bridge = RiverToolsBridge()
    return _bridge


# === Telegram Command Handlers ===

async def river_tools_command(cmd: str, args: list = None) -> str:
    """
    Process tool commands for River.

    Commands:
    - status - Show tool status
    - image <prompt> - Generate image
    - image_pro <prompt> - Generate with Nano Banana Pro
    - search <query> - Web search
    - fetch <url> - Fetch URL content
    - research <query> - Deep research
    - mcp - List MCP tools
    """
    bridge = get_river_tools()
    args = args or []

    if cmd == "status":
        status = bridge.get_status()
        lines = ["🔧 *River Tools Status:*", ""]
        lines.append(f"• Tools available: {'✅' if status['tools_available'] else '❌'}")
        lines.append(f"• Image model: `{status['image_model']}`")
        lines.append(f"• Image Pro: `{status['image_model_pro']}`")
        lines.append("")
        lines.append("*Capabilities:*")
        for cap, available in status['capabilities'].items():
            lines.append(f"  • {cap}: {'✅' if available else '❌'}")
        return "\n".join(lines)

    elif cmd == "image":
        if not args:
            return "❌ Usage: `/tools image <prompt>`"
        prompt = " ".join(args)
        result = await bridge.generate_image(prompt, use_pro=False)
        if result["success"]:
            return f"🖼️ *Image generated*\n\nModel: `{result.get('model')}`\nURL: {result.get('image_url', result.get('image_path', 'N/A'))}"
        return f"❌ Image generation failed: {result['error']}"

    elif cmd == "image_pro":
        if not args:
            return "❌ Usage: `/tools image_pro <prompt>`"
        prompt = " ".join(args)
        result = await bridge.generate_image(prompt, use_pro=True)
        if result["success"]:
            return f"🖼️ *Image generated (Pro)*\n\nModel: `{result.get('model')}`\nURL: {result.get('image_url', result.get('image_path', 'N/A'))}"
        return f"❌ Image generation failed: {result['error']}"

    elif cmd == "search":
        if not args:
            return "❌ Usage: `/tools search <query>`"
        query = " ".join(args)
        result = await bridge.web_search(query)
        if result["success"]:
            lines = [f"🔍 *Search results for:* _{query}_", ""]
            for r in result.get("results", [])[:5]:
                title = r.get("title", "No title")
                url = r.get("url", "")
                lines.append(f"• [{title}]({url})")
            return "\n".join(lines)
        return f"❌ Search failed: {result['error']}"

    elif cmd == "fetch":
        if not args:
            return "❌ Usage: `/tools fetch <url>`"
        url = args[0]
        result = await bridge.web_fetch(url)
        if result["success"]:
            content = result.get("content", "")[:1000]
            return f"📄 *Content from* `{url}`:\n\n{content}..."
        return f"❌ Fetch failed: {result['error']}"

    elif cmd == "research":
        if not args:
            return "❌ Usage: `/tools research <query>`"
        query = " ".join(args)
        result = await bridge.deep_research(query)
        if result["success"]:
            research = result.get("research", {})
            summary = research.get("summary", str(research))[:2000]
            return f"📚 *Research on:* _{query}_\n\n{summary}"
        return f"❌ Research failed: {result['error']}"

    elif cmd == "mcp":
        tools = await bridge.list_mcp_tools()
        if not tools:
            return "❌ No MCP tools available"
        lines = ["🔌 *MCP Tools:*", ""]
        for t in tools[:10]:
            lines.append(f"• `{t['name']}`: {t['description'][:50]}...")
        return "\n".join(lines)

    else:
        return """🔧 *River Tools Commands:*

• `/tools status` - Show tool status
• `/tools image <prompt>` - Generate image (Nano Banana)
• `/tools image_pro <prompt>` - Generate with Nano Banana Pro
• `/tools search <query>` - Web search
• `/tools fetch <url>` - Fetch URL content
• `/tools research <query>` - Deep research
• `/tools mcp` - List MCP tools"""


if __name__ == "__main__":
    # Test
    bridge = get_river_tools()
    print(f"Status: {bridge.get_status()}")
