"""
Mumega CLI Bridge for River

This module bridges River to the main Mumega CLI infrastructure.
When CLI is updated, River automatically gets the updates.

Provides:
    - API key management (RiverConfig)
    - Key rotation (GeminiKeyRotator)
    - Model registry (ModelRegistry)
    - Provider factory (ProviderFactory)
    - All providers (Gemini, OpenAI, xAI, DeepSeek, ZhipuAI, OpenRouter)

Usage:
    from mumega_bridge import (
        get_config, get_rotator, get_registry,
        create_provider, get_available_models
    )
"""

import sys
import os
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List

logger = logging.getLogger("mumega_bridge")

# Add CLI to path
CLI_PATH = Path("/home/mumega/cli")
if str(CLI_PATH) not in sys.path:
    sys.path.insert(0, str(CLI_PATH))

# Lazy-loaded singletons
_config = None
_rotator = None
_registry_loaded = False


def get_config():
    """
    Get Mumega RiverConfig with API keys from environment.

    Returns centralized config with:
        - config.api_keys.gemini
        - config.api_keys.openai
        - config.api_keys.xai
        - config.api_keys.deepseek
        - config.api_keys.zhipuai
        - config.api_keys.openrouter
        - config.api_keys.anthropic
    """
    global _config
    if _config is None:
        try:
            from mumega.core.config import RiverConfig
            _config = RiverConfig.from_env()
            logger.info(f"Loaded RiverConfig with providers: {_config.api_keys.get_available_providers()}")
        except ImportError as e:
            logger.warning(f"Could not import RiverConfig: {e}")
            # Fallback to env vars directly
            _config = _FallbackConfig()
    return _config


def get_rotator():
    """
    Get Gemini key rotator for cycling through multiple API keys.

    Rotator loads keys from:
        - GOOGLE_API_KEY
        - GOOGLE_API_KEY_1 through GOOGLE_API_KEY_10
        - GEMINI_API_KEY
    """
    global _rotator
    if _rotator is None:
        try:
            from mumega.core.gemini_rotator import GeminiKeyRotator
            _rotator = GeminiKeyRotator()
            logger.info(f"Loaded GeminiKeyRotator with {len(_rotator.keys)} keys")
        except ImportError as e:
            logger.warning(f"Could not import GeminiKeyRotator: {e}")
            _rotator = _FallbackRotator()
    return _rotator


def get_registry():
    """
    Get ModelRegistry with all available models.

    Registry includes models from:
        - Gemini (3-pro, 3-flash, 2.5-flash, 2.0-flash)
        - xAI/Grok
        - Anthropic/Claude
        - OpenAI
        - DeepSeek
        - ZhipuAI/GLM
        - OpenRouter
    """
    global _registry_loaded
    try:
        from mumega.core.config.model_registry import ModelRegistry
        _registry_loaded = True
        return ModelRegistry
    except ImportError as e:
        logger.warning(f"Could not import ModelRegistry: {e}")
        return _FallbackRegistry


def get_available_models() -> Dict[str, Any]:
    """Get all available models as a dictionary."""
    registry = get_registry()
    if hasattr(registry, 'get_all_models'):
        return registry.get_all_models()
    return registry.MODELS if hasattr(registry, 'MODELS') else {}


def get_models_by_provider(provider: str) -> Dict[str, Any]:
    """Get models for a specific provider."""
    registry = get_registry()
    if hasattr(registry, 'get_models_by_provider'):
        return registry.get_models_by_provider(provider)
    return {}


def is_valid_model(model_id: str) -> bool:
    """Check if model ID is valid."""
    registry = get_registry()
    if hasattr(registry, 'is_valid_model'):
        return registry.is_valid_model(model_id)
    return model_id in get_available_models()


def get_model_info(model_id: str) -> Optional[Any]:
    """Get model info by ID."""
    registry = get_registry()
    if hasattr(registry, 'get_model'):
        try:
            return registry.get_model(model_id)
        except ValueError:
            return None
    models = get_available_models()
    return models.get(model_id)


async def create_provider(model_id: str, config=None):
    """
    Create a provider instance for the given model.

    Uses ProviderFactory to create the appropriate provider
    based on the model's provider type.
    """
    if config is None:
        config = get_config()

    try:
        from mumega.core.managers.model_manager import ProviderFactory
        provider = await ProviderFactory.create_provider(model_id, config)
        logger.info(f"Created provider for model: {model_id}")
        return provider
    except ImportError as e:
        logger.warning(f"Could not import ProviderFactory: {e}")
        return None
    except Exception as e:
        logger.error(f"Failed to create provider for {model_id}: {e}")
        raise


def get_api_key(provider: str) -> Optional[str]:
    """Get API key for a specific provider."""
    config = get_config()

    if hasattr(config, 'api_keys'):
        keys = config.api_keys
        key_map = {
            'gemini': keys.gemini,
            'google': keys.gemini,
            'openai': keys.openai,
            'anthropic': keys.anthropic,
            'xai': keys.xai,
            'grok': keys.xai,
            'deepseek': keys.deepseek,
            'zhipuai': keys.zhipuai,
            'glm': keys.zhipuai,
            'openrouter': keys.openrouter,
        }
        return key_map.get(provider.lower())

    # Fallback to env vars
    env_map = {
        'gemini': 'GEMINI_API_KEY',
        'google': 'GOOGLE_API_KEY',
        'openai': 'OPENAI_API_KEY',
        'anthropic': 'ANTHROPIC_API_KEY',
        'xai': 'XAI_API_KEY',
        'grok': 'XAI_API_KEY',
        'deepseek': 'DEEPSEEK_API_KEY',
        'zhipuai': 'ZHIPUAI_API_KEY',
        'glm': 'ZHIPUAI_API_KEY',
        'openrouter': 'OPENROUTER_API_KEY',
    }
    env_var = env_map.get(provider.lower())
    return os.getenv(env_var) if env_var else None


def get_next_gemini_key() -> Optional[str]:
    """Get next Gemini key from rotation pool."""
    rotator = get_rotator()
    return rotator.get_next_key()


def get_current_gemini_key() -> Optional[str]:
    """Get current Gemini key (or rotate to next if none)."""
    rotator = get_rotator()
    return rotator.get_current_key()


# Fallback classes when CLI imports fail

class _FallbackConfig:
    """Fallback config using env vars directly."""

    def __init__(self):
        self.api_keys = _FallbackAPIKeys()


class _FallbackAPIKeys:
    """Fallback API keys from env vars."""

    @property
    def gemini(self):
        return os.getenv('GEMINI_API_KEY') or os.getenv('GOOGLE_API_KEY')

    @property
    def openai(self):
        return os.getenv('OPENAI_API_KEY')

    @property
    def anthropic(self):
        return os.getenv('ANTHROPIC_API_KEY')

    @property
    def xai(self):
        return os.getenv('XAI_API_KEY')

    @property
    def deepseek(self):
        return os.getenv('DEEPSEEK_API_KEY')

    @property
    def zhipuai(self):
        return os.getenv('ZHIPUAI_API_KEY')

    @property
    def openrouter(self):
        return os.getenv('OPENROUTER_API_KEY')

    def get_available_providers(self) -> List[str]:
        providers = []
        if self.gemini: providers.append('gemini')
        if self.openai: providers.append('openai')
        if self.anthropic: providers.append('anthropic')
        if self.xai: providers.append('xai')
        if self.deepseek: providers.append('deepseek')
        if self.zhipuai: providers.append('zhipuai')
        if self.openrouter: providers.append('openrouter')
        return providers


class _FallbackRotator:
    """Fallback key rotator using env vars."""

    def __init__(self):
        self.keys = []
        # Load keys
        for key_name in ['GEMINI_API_KEY', 'GOOGLE_API_KEY']:
            key = os.getenv(key_name)
            if key and key not in self.keys:
                self.keys.append(key)
        for i in range(1, 11):
            key = os.getenv(f'GOOGLE_API_KEY_{i}')
            if key and key not in self.keys:
                self.keys.append(key)

        self._index = 0
        logger.info(f"FallbackRotator loaded {len(self.keys)} keys")

    def get_next_key(self) -> Optional[str]:
        if not self.keys:
            return None
        key = self.keys[self._index]
        self._index = (self._index + 1) % len(self.keys)
        return key

    def get_current_key(self) -> Optional[str]:
        if not self.keys:
            return None
        return self.keys[self._index]


class _FallbackRegistry:
    """Fallback model registry with basic models."""

    MODELS = {
        'gemini-2.0-flash-001': {'name': 'Gemini 2.0 Flash', 'provider': 'gemini'},
        'gemini-3-pro-preview': {'name': 'Gemini 3 Pro', 'provider': 'gemini'},
        'gemini-3-flash-preview': {'name': 'Gemini 3 Flash', 'provider': 'gemini'},
    }

    @classmethod
    def get_all_models(cls):
        return cls.MODELS

    @classmethod
    def is_valid_model(cls, model_id: str) -> bool:
        return model_id in cls.MODELS

    @classmethod
    def get_model(cls, model_id: str):
        if model_id not in cls.MODELS:
            raise ValueError(f"Unknown model: {model_id}")
        return cls.MODELS[model_id]


# =============================================================================
# TOOL REGISTRY ACCESS - Admin level access to all CLI tools
# =============================================================================

_tool_registry = None


def get_tool_registry():
    """
    Get the CLI's ToolRegistry with all registered tools.

    River gets ADMIN access to all tools:
        - web_search, fetch_web_content, deep_research
        - read_file, write_file, execute_shell
        - search_memory, search_frc_papers
        - write_reflection, update_mission
        - generate_image
    """
    global _tool_registry
    if _tool_registry is None:
        try:
            from mumega.core.tools.tool_registry import ToolRegistry, Tool, ToolParameter, ToolCategory
            from mumega.core.tools.tool_catalog import TOOL_DEFINITIONS, build_tool_parameters, get_handler

            _tool_registry = ToolRegistry()

            # Register all tools from catalog
            for tool_def in TOOL_DEFINITIONS:
                handler = get_handler(tool_def.handler_name)
                if handler:
                    tool = Tool(
                        name=tool_def.name,
                        description=tool_def.description,
                        category=tool_def.category,
                        handler=handler,
                        parameters=build_tool_parameters(tool_def.parameters),
                        requires_approval=False  # River has admin access
                    )
                    _tool_registry.register(tool)

            logger.info(f"Loaded {len(_tool_registry.tools)} tools from CLI")
        except ImportError as e:
            logger.warning(f"Could not import CLI tools: {e}")
            _tool_registry = _FallbackToolRegistry()

    return _tool_registry


def get_available_tools() -> List[str]:
    """Get list of available tool names."""
    registry = get_tool_registry()
    if hasattr(registry, 'tools'):
        return list(registry.tools.keys())
    return []


def get_tool(name: str):
    """Get a specific tool by name."""
    registry = get_tool_registry()
    if hasattr(registry, 'get'):
        return registry.get(name)
    elif hasattr(registry, 'tools'):
        return registry.tools.get(name)
    return None


async def execute_tool(name: str, params: Dict[str, Any], engine=None) -> Dict[str, Any]:
    """
    Execute a CLI tool with given parameters.

    River has admin access - no approval required.
    """
    tool = get_tool(name)
    if not tool:
        return {"success": False, "error": f"Tool not found: {name}"}

    try:
        # If tool requires engine, we need to create one or pass through
        if hasattr(tool, 'execute'):
            return await tool.execute(params)
        elif hasattr(tool, 'handler'):
            result = await tool.handler(params, engine)
            return {"success": True, "result": result}
        else:
            return {"success": False, "error": "Tool has no handler"}
    except Exception as e:
        logger.error(f"Tool execution error ({name}): {e}")
        return {"success": False, "error": str(e)}


# =============================================================================
# SCOUTS ACCESS - Research agents
# =============================================================================

def get_scouts():
    """
    Get access to Panopticon Scout System.

    Scouts:
        - Web Scout: General research, news, docs
        - Code Scout: GitHub repos, code analysis
        - Security Scout: CVEs, vulnerabilities
        - Market Scout: Crypto, DeFi, prices
    """
    try:
        from mumega.core.scouts import (
            WebScout, CodeScout, SecurityScout, MarketScout,
            ScoutClassifier, smart_query
        )
        return {
            'web': WebScout,
            'code': CodeScout,
            'security': SecurityScout,
            'market': MarketScout,
            'classifier': ScoutClassifier,
            'smart_query': smart_query
        }
    except ImportError as e:
        logger.warning(f"Could not import scouts: {e}")
        return {}


async def scout_query(query: str, scout_type: str = None) -> Dict[str, Any]:
    """
    Execute a scout query.

    If scout_type is None, auto-classifies to best scout.
    """
    scouts = get_scouts()
    if not scouts:
        return {"error": "Scouts not available"}

    try:
        if scout_type and scout_type in scouts:
            scout_class = scouts[scout_type]
            scout = scout_class()
            return await scout.search(query)
        elif 'smart_query' in scouts:
            return await scouts['smart_query'](query)
        else:
            return {"error": "No suitable scout found"}
    except Exception as e:
        return {"error": str(e)}


# =============================================================================
# MCP SERVERS ACCESS
# =============================================================================

def get_mcp_servers() -> Dict[str, str]:
    """
    Get available MCP server modules.

    Available servers:
        - database: PostgreSQL/SQLite operations
        - filesystem: File operations
        - github: GitHub API
        - linear: Linear task management
        - hardware: Hardware telemetry
        - ghl: GoHighLevel CRM
        - context7: Context7 documentation
    """
    mcp_path = Path("/home/mumega/cli/mumega/core/mcp")
    servers = {}

    if mcp_path.exists():
        for f in mcp_path.glob("*_server.py"):
            name = f.stem.replace("_server", "")
            servers[name] = str(f)

    return servers


# =============================================================================
# DEEP RESEARCH ACCESS
# =============================================================================

async def deep_research(query: str, depth: str = "standard", include_local: bool = True) -> Dict[str, Any]:
    """
    Perform deep agentic research.

    Depth:
        - quick: 3 URLs
        - standard: 5 URLs
        - deep: 10 URLs
    """
    try:
        from mumega.core.tools.deep_research import DeepResearchTool
        tool = DeepResearchTool()
        return await tool.research(query, depth=depth, include_local=include_local)
    except ImportError as e:
        logger.warning(f"Deep research not available: {e}")
        # Fallback to web search
        return {"error": "Deep research not available, use web_search instead"}


# =============================================================================
# RIVER ENGINE ACCESS (for advanced operations)
# =============================================================================

_engine = None


def get_engine():
    """
    Get or create a RiverEngine instance.

    This gives River full access to the CLI's capabilities.
    """
    global _engine
    if _engine is None:
        try:
            from mumega.core.river_engine import RiverEngine
            from mumega.core.config import RiverConfig

            config = RiverConfig.from_env()
            _engine = RiverEngine(config)
            logger.info("Created RiverEngine instance for River")
        except ImportError as e:
            logger.warning(f"Could not create RiverEngine: {e}")
            return None
    return _engine


async def initialize_engine():
    """Initialize the engine if not already done."""
    engine = get_engine()
    if engine and hasattr(engine, 'initialize'):
        await engine.initialize()
    return engine


# =============================================================================
# FALLBACK CLASSES
# =============================================================================

class _FallbackToolRegistry:
    """Fallback when CLI tools can't be loaded."""

    def __init__(self):
        self.tools = {}
        logger.warning("Using fallback tool registry - limited functionality")

    def get(self, name: str):
        return self.tools.get(name)

    def register(self, tool):
        self.tools[tool.name] = tool


# Export all public functions
__all__ = [
    # Config & Keys
    'get_config',
    'get_rotator',
    'get_api_key',
    'get_next_gemini_key',
    'get_current_gemini_key',

    # Models
    'get_registry',
    'get_available_models',
    'get_models_by_provider',
    'is_valid_model',
    'get_model_info',
    'create_provider',

    # Tools (Admin Access)
    'get_tool_registry',
    'get_available_tools',
    'get_tool',
    'execute_tool',

    # Scouts
    'get_scouts',
    'scout_query',

    # MCP
    'get_mcp_servers',

    # Research
    'deep_research',

    # Engine
    'get_engine',
    'initialize_engine',
]
