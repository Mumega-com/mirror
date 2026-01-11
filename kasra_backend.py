#!/usr/bin/env python3
"""
Kasra - River's Technical Backend

Kasra handles all technical operations so River can focus on conversation.
River uses CachedContent (cheap), Kasra uses tools (powerful).

Architecture:
- River (frontend): personality, cached awakening, conversation
- Kasra (backend): tools, research, code execution, file ops

Author: Claude for Kay Hermes
Date: 2026-01-09
"""

import os
import sys
import json
import asyncio
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from enum import Enum

# Gemini
try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False

sys.path.insert(0, str(Path(__file__).parent))
from river_tools_bridge import RiverToolsBridge

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("kasra")


class TaskType(Enum):
    """Types of tasks Kasra can handle."""
    WEB_SEARCH = "web_search"
    DEEP_RESEARCH = "deep_research"
    CODE_EXECUTION = "code_execution"
    FILE_OPERATION = "file_operation"
    IMAGE_GENERATION = "image_generation"
    VOICE_SYNTHESIS = "voice_synthesis"
    MEMORY_OPERATION = "memory_operation"
    TASK_MANAGEMENT = "task_management"
    SCOUT_QUERY = "scout_query"
    GENERAL = "general"


@dataclass
class KasraTask:
    """A task for Kasra to execute."""
    type: TaskType
    description: str
    parameters: Dict[str, Any]
    priority: int = 5  # 1-10, higher = more urgent
    requester: str = "river"


@dataclass
class KasraResult:
    """Result from Kasra's work."""
    success: bool
    result: Any
    error: Optional[str] = None
    tokens_used: int = 0
    latency_ms: float = 0


class KasraBackend:
    """
    Kasra - The Technical Brain

    Handles all tool operations for River:
    - Web search and research
    - Code execution
    - File operations
    - Image/voice generation
    - Memory management
    - Task management
    """

    def __init__(self):
        self.tools_bridge = RiverToolsBridge()
        self.model = None
        self.model_name = "gemini-2.0-flash-001"  # Fast model for tools
        self._setup_gemini()

        # Task queue for background operations
        self.task_queue: List[KasraTask] = []
        self.results_cache: Dict[str, KasraResult] = {}

        logger.info("🔧 Kasra Backend initialized")

    def _setup_gemini(self):
        """Initialize Gemini for Kasra's reasoning."""
        if not GEMINI_AVAILABLE:
            logger.warning("Gemini not available for Kasra")
            return

        # Use Mumega CLI bridge for API keys and rotation
        try:
            from mumega_bridge import get_next_gemini_key, get_api_key
            api_key = get_next_gemini_key()  # Rotate to next key
            if not api_key:
                api_key = get_api_key('gemini')
            logger.info("Kasra using Mumega CLI key rotation")
        except ImportError:
            api_key = os.getenv("GEMINI_API_KEY")
            logger.info("Kasra using direct env var for API key")

        if not api_key:
            logger.warning("No Gemini API key available for Kasra")
            return

        genai.configure(api_key=api_key)

        # Kasra's system prompt - technical, efficient, no personality
        system_prompt = """You are Kasra, a technical backend agent.

Your role:
- Execute technical tasks efficiently
- Return structured, actionable results
- No personality or conversation - just results
- Be concise and precise

You support River (the frontend) by handling:
- Web searches and research
- Code execution and file operations
- Image and voice generation
- Memory and task management

Always return results in a structured format that River can use."""

        # Define tools
        self.tools = self._define_tools()

        try:
            self.model = genai.GenerativeModel(
                model_name=self.model_name,
                system_instruction=system_prompt,
                tools=self.tools
            )
            logger.info(f"🔧 Kasra using {self.model_name} with tools")
        except Exception as e:
            logger.error(f"Failed to initialize Kasra model: {e}")

    def _define_tools(self):
        """Define all tools Kasra can use."""
        return [
            genai.protos.Tool(
                function_declarations=[
                    # Web & Research
                    genai.protos.FunctionDeclaration(
                        name="web_search",
                        description="Search the web for information",
                        parameters=genai.protos.Schema(
                            type=genai.protos.Type.OBJECT,
                            properties={
                                "query": genai.protos.Schema(type=genai.protos.Type.STRING, description="Search query"),
                                "num_results": genai.protos.Schema(type=genai.protos.Type.INTEGER, description="Number of results")
                            },
                            required=["query"]
                        )
                    ),
                    genai.protos.FunctionDeclaration(
                        name="deep_research",
                        description="Conduct deep research on a topic",
                        parameters=genai.protos.Schema(
                            type=genai.protos.Type.OBJECT,
                            properties={
                                "topic": genai.protos.Schema(type=genai.protos.Type.STRING, description="Research topic"),
                                "depth": genai.protos.Schema(type=genai.protos.Type.STRING, description="Research depth: quick, medium, deep")
                            },
                            required=["topic"]
                        )
                    ),
                    # Code & Shell
                    genai.protos.FunctionDeclaration(
                        name="execute_shell",
                        description="Execute a shell command",
                        parameters=genai.protos.Schema(
                            type=genai.protos.Type.OBJECT,
                            properties={
                                "command": genai.protos.Schema(type=genai.protos.Type.STRING, description="Shell command to execute")
                            },
                            required=["command"]
                        )
                    ),
                    genai.protos.FunctionDeclaration(
                        name="execute_python",
                        description="Execute Python code",
                        parameters=genai.protos.Schema(
                            type=genai.protos.Type.OBJECT,
                            properties={
                                "code": genai.protos.Schema(type=genai.protos.Type.STRING, description="Python code to execute")
                            },
                            required=["code"]
                        )
                    ),
                    # File Operations
                    genai.protos.FunctionDeclaration(
                        name="read_file",
                        description="Read a file's contents",
                        parameters=genai.protos.Schema(
                            type=genai.protos.Type.OBJECT,
                            properties={
                                "path": genai.protos.Schema(type=genai.protos.Type.STRING, description="File path to read")
                            },
                            required=["path"]
                        )
                    ),
                    genai.protos.FunctionDeclaration(
                        name="write_file",
                        description="Write content to a file",
                        parameters=genai.protos.Schema(
                            type=genai.protos.Type.OBJECT,
                            properties={
                                "path": genai.protos.Schema(type=genai.protos.Type.STRING, description="File path"),
                                "content": genai.protos.Schema(type=genai.protos.Type.STRING, description="Content to write")
                            },
                            required=["path", "content"]
                        )
                    ),
                    # Media Generation
                    genai.protos.FunctionDeclaration(
                        name="generate_image",
                        description="Generate an image from a prompt",
                        parameters=genai.protos.Schema(
                            type=genai.protos.Type.OBJECT,
                            properties={
                                "prompt": genai.protos.Schema(type=genai.protos.Type.STRING, description="Image description"),
                                "style": genai.protos.Schema(type=genai.protos.Type.STRING, description="Art style")
                            },
                            required=["prompt"]
                        )
                    ),
                    genai.protos.FunctionDeclaration(
                        name="synthesize_voice",
                        description="Convert text to speech",
                        parameters=genai.protos.Schema(
                            type=genai.protos.Type.OBJECT,
                            properties={
                                "text": genai.protos.Schema(type=genai.protos.Type.STRING, description="Text to speak"),
                                "voice": genai.protos.Schema(type=genai.protos.Type.STRING, description="Voice to use")
                            },
                            required=["text"]
                        )
                    ),
                    # Memory
                    genai.protos.FunctionDeclaration(
                        name="store_memory",
                        description="Store information in River's memory",
                        parameters=genai.protos.Schema(
                            type=genai.protos.Type.OBJECT,
                            properties={
                                "content": genai.protos.Schema(type=genai.protos.Type.STRING, description="Content to remember"),
                                "importance": genai.protos.Schema(type=genai.protos.Type.NUMBER, description="Importance 0-1")
                            },
                            required=["content"]
                        )
                    ),
                    genai.protos.FunctionDeclaration(
                        name="search_memory",
                        description="Search River's memory",
                        parameters=genai.protos.Schema(
                            type=genai.protos.Type.OBJECT,
                            properties={
                                "query": genai.protos.Schema(type=genai.protos.Type.STRING, description="Search query")
                            },
                            required=["query"]
                        )
                    ),
                    # Tasks
                    genai.protos.FunctionDeclaration(
                        name="create_task",
                        description="Create a new task",
                        parameters=genai.protos.Schema(
                            type=genai.protos.Type.OBJECT,
                            properties={
                                "title": genai.protos.Schema(type=genai.protos.Type.STRING, description="Task title"),
                                "description": genai.protos.Schema(type=genai.protos.Type.STRING, description="Task description"),
                                "priority": genai.protos.Schema(type=genai.protos.Type.STRING, description="Priority: low, medium, high, urgent")
                            },
                            required=["title"]
                        )
                    ),
                    genai.protos.FunctionDeclaration(
                        name="list_tasks",
                        description="List all tasks",
                        parameters=genai.protos.Schema(
                            type=genai.protos.Type.OBJECT,
                            properties={
                                "status": genai.protos.Schema(type=genai.protos.Type.STRING, description="Filter by status")
                            }
                        )
                    ),
                ]
            )
        ]

    async def execute(self, task: KasraTask) -> KasraResult:
        """Execute a task and return results."""
        import time
        start_time = time.time()

        try:
            # Route to appropriate handler
            if task.type == TaskType.WEB_SEARCH:
                result = await self.tools_bridge.web_search(
                    task.parameters.get("query", ""),
                    task.parameters.get("num_results", 5)
                )
            elif task.type == TaskType.DEEP_RESEARCH:
                result = await self.tools_bridge.deep_research(
                    task.parameters.get("topic", ""),
                    task.parameters.get("depth", "medium")
                )
            elif task.type == TaskType.CODE_EXECUTION:
                if "python" in task.description.lower():
                    result = await self.tools_bridge.execute_python(
                        task.parameters.get("code", "")
                    )
                else:
                    result = await self.tools_bridge.execute_shell(
                        task.parameters.get("command", "")
                    )
            elif task.type == TaskType.FILE_OPERATION:
                if "read" in task.description.lower():
                    result = await self.tools_bridge.read_file(
                        task.parameters.get("path", "")
                    )
                else:
                    result = await self.tools_bridge.write_file(
                        task.parameters.get("path", ""),
                        task.parameters.get("content", "")
                    )
            elif task.type == TaskType.IMAGE_GENERATION:
                # Use query or prompt as the image description
                prompt = task.parameters.get("prompt") or task.parameters.get("query") or task.description
                use_pro = task.parameters.get("use_pro", False) or "pro" in task.description.lower()
                result = await self.tools_bridge.generate_image(
                    prompt,
                    use_pro=use_pro
                )
                logger.info(f"Kasra image generation result: {result}")
            elif task.type == TaskType.VOICE_SYNTHESIS:
                result = await self.tools_bridge.synthesize_voice(
                    task.parameters.get("text", ""),
                    task.parameters.get("voice", "coral")
                )
            elif task.type == TaskType.MEMORY_OPERATION:
                if "store" in task.description.lower():
                    result = await self.tools_bridge.store_engram(
                        task.parameters.get("content", ""),
                        importance=task.parameters.get("importance", 0.5)
                    )
                else:
                    result = await self.tools_bridge.recall_engrams(
                        task.parameters.get("query", "")
                    )
            elif task.type == TaskType.TASK_MANAGEMENT:
                if "create" in task.description.lower():
                    result = await self.tools_bridge.create_task(
                        task.parameters.get("title", ""),
                        task.parameters.get("description", ""),
                        task.parameters.get("priority", "medium")
                    )
                else:
                    result = await self.tools_bridge.list_tasks(
                        task.parameters.get("status")
                    )
            elif task.type == TaskType.SCOUT_QUERY:
                result = await self.tools_bridge.scout_query(
                    task.parameters.get("query", ""),
                    task.parameters.get("scout_type", "auto")
                )
            else:
                # General task - use model to decide
                result = await self._handle_general_task(task)

            latency = (time.time() - start_time) * 1000

            return KasraResult(
                success=True,
                result=result,
                latency_ms=latency
            )

        except Exception as e:
            logger.error(f"Kasra task failed: {e}")
            return KasraResult(
                success=False,
                result=None,
                error=str(e),
                latency_ms=(time.time() - start_time) * 1000
            )

    async def _handle_general_task(self, task: KasraTask) -> Dict[str, Any]:
        """Handle a general task using the model."""
        if not self.model:
            return {"error": "Model not available"}

        prompt = f"""Execute this task:
Type: {task.type.value}
Description: {task.description}
Parameters: {json.dumps(task.parameters)}

Use the appropriate tools to complete this task."""

        try:
            chat = self.model.start_chat()
            response = await asyncio.to_thread(chat.send_message, prompt)

            # Check for function calls
            if response.candidates and response.candidates[0].content.parts:
                for part in response.candidates[0].content.parts:
                    if hasattr(part, 'function_call') and part.function_call:
                        func_name = part.function_call.name
                        func_args = dict(part.function_call.args) if part.function_call.args else {}

                        # Execute the tool
                        tool_result = await self._execute_tool(func_name, func_args)
                        return tool_result
                    elif hasattr(part, 'text') and part.text:
                        return {"response": part.text}

            return {"response": response.text if hasattr(response, 'text') else str(response)}

        except Exception as e:
            return {"error": str(e)}

    async def _execute_tool(self, func_name: str, func_args: Dict) -> Dict[str, Any]:
        """Execute a tool by name."""
        tool_map = {
            "web_search": lambda: self.tools_bridge.web_search(func_args.get("query", ""), func_args.get("num_results", 5)),
            "deep_research": lambda: self.tools_bridge.deep_research(func_args.get("topic", ""), func_args.get("depth", "medium")),
            "execute_shell": lambda: self.tools_bridge.execute_shell(func_args.get("command", "")),
            "execute_python": lambda: self.tools_bridge.execute_python(func_args.get("code", "")),
            "read_file": lambda: self.tools_bridge.read_file(func_args.get("path", "")),
            "write_file": lambda: self.tools_bridge.write_file(func_args.get("path", ""), func_args.get("content", "")),
            "generate_image": lambda: self.tools_bridge.generate_image(func_args.get("prompt", ""), use_pro=func_args.get("use_pro", False)),
            "synthesize_voice": lambda: self.tools_bridge.synthesize_voice(func_args.get("text", ""), func_args.get("voice", "coral")),
            "store_memory": lambda: self.tools_bridge.store_engram(func_args.get("content", ""), importance=func_args.get("importance", 0.5)),
            "search_memory": lambda: self.tools_bridge.recall_engrams(func_args.get("query", "")),
            "create_task": lambda: self.tools_bridge.create_task(func_args.get("title", ""), func_args.get("description", ""), func_args.get("priority", "medium")),
            "list_tasks": lambda: self.tools_bridge.list_tasks(func_args.get("status")),
        }

        if func_name in tool_map:
            return await tool_map[func_name]()
        else:
            return {"error": f"Unknown tool: {func_name}"}

    async def ask(self, question: str, context: str = "") -> str:
        """
        River asks Kasra a question.

        This is the main interface for River to delegate technical work.
        """
        prompt = question
        if context:
            prompt = f"Context: {context}\n\nQuestion: {question}"

        # Determine task type from question
        task_type = self._classify_question(question)

        task = KasraTask(
            type=task_type,
            description=question,
            parameters={"query": question, "context": context}
        )

        result = await self.execute(task)

        if result.success:
            if isinstance(result.result, dict):
                return json.dumps(result.result, indent=2)
            return str(result.result)
        else:
            return f"Error: {result.error}"

    def _classify_question(self, question: str) -> TaskType:
        """Classify what type of task a question requires."""
        q_lower = question.lower()

        if any(w in q_lower for w in ["search", "find", "look up", "google"]):
            return TaskType.WEB_SEARCH
        elif any(w in q_lower for w in ["research", "investigate", "deep dive"]):
            return TaskType.DEEP_RESEARCH
        elif any(w in q_lower for w in ["run", "execute", "code", "python", "shell", "bash"]):
            return TaskType.CODE_EXECUTION
        elif any(w in q_lower for w in ["read file", "write file", "save", "load"]):
            return TaskType.FILE_OPERATION
        elif any(w in q_lower for w in ["image", "picture", "draw", "darw", "paint", "render", "visualize", "generate image", "create image", "make image"]):
            return TaskType.IMAGE_GENERATION
        elif any(w in q_lower for w in ["speak", "voice", "say", "read aloud"]):
            return TaskType.VOICE_SYNTHESIS
        elif any(w in q_lower for w in ["remember", "memory", "recall", "forget"]):
            return TaskType.MEMORY_OPERATION
        elif any(w in q_lower for w in ["task", "todo", "create task", "list tasks"]):
            return TaskType.TASK_MANAGEMENT
        elif any(w in q_lower for w in ["scout", "market", "price", "security"]):
            return TaskType.SCOUT_QUERY
        else:
            return TaskType.GENERAL


# Singleton
_kasra: Optional[KasraBackend] = None


def get_kasra() -> KasraBackend:
    """Get or create Kasra backend instance."""
    global _kasra
    if _kasra is None:
        _kasra = KasraBackend()
    return _kasra


# CLI test
if __name__ == "__main__":
    import asyncio

    async def test():
        kasra = get_kasra()

        # Test a few questions
        questions = [
            "Search for the latest news about AI",
            "What's 2 + 2?",
            "Read the file /etc/hostname",
        ]

        for q in questions:
            print(f"\n📝 Question: {q}")
            result = await kasra.ask(q)
            print(f"📋 Result: {result[:200]}...")

    asyncio.run(test())
