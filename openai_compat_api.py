#!/usr/bin/env python3
"""
OpenAI-Compatible API for Mumega
Exposes River Engine via OpenAI-compatible endpoints for aider and other tools
"""

import os
import sys
import asyncio
import json
import logging
import uuid
from typing import List, Optional, Dict, Any, AsyncGenerator
from datetime import datetime

from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
import uvicorn

# Add CLI to path
sys.path.insert(0, '/mnt/HC_Volume_104325311/cli')

from mumega.core.river_engine import RiverEngine
from mumega.core.message import Message, MessageSource

# Load environment
load_dotenv('/mnt/HC_Volume_104325311/cli/.env')

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("openai_compat_api")

# Initialize FastAPI
app = FastAPI(
    title="Mumega OpenAI-Compatible API",
    description="OpenAI-compatible endpoints for Mumega River Engine",
    version="1.0.0"
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global River instance
river: Optional[RiverEngine] = None


# --- Models ---

class ChatMessage(BaseModel):
    role: str  # "system", "user", "assistant"
    content: str
    name: Optional[str] = None


class ChatCompletionRequest(BaseModel):
    model: str
    messages: List[ChatMessage]
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = None
    stream: Optional[bool] = False
    top_p: Optional[float] = 1.0
    frequency_penalty: Optional[float] = 0.0
    presence_penalty: Optional[float] = 0.0
    stop: Optional[List[str]] = None
    n: Optional[int] = 1


class ChatCompletionChoice(BaseModel):
    index: int
    message: ChatMessage
    finish_reason: str


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[ChatCompletionChoice]
    usage: Dict[str, int]


class ModelInfo(BaseModel):
    id: str
    object: str = "model"
    created: int
    owned_by: str = "mumega"


class ModelsResponse(BaseModel):
    object: str = "list"
    data: List[ModelInfo]


# --- Lifecycle ---

@app.on_event("startup")
async def startup():
    global river
    logger.info("🌊 Initializing River Engine...")
    try:
        river = RiverEngine()
        logger.info(f"✅ River Engine initialized (Model: {river.current_model})")
    except Exception as e:
        logger.error(f"❌ Failed to initialize River: {e}")
        raise


@app.on_event("shutdown")
async def shutdown():
    global river
    logger.info("🛑 Shutting down River Engine...")
    river = None


# --- Endpoints ---

@app.get("/")
async def root():
    return {
        "service": "Mumega OpenAI-Compatible API",
        "version": "1.0.0",
        "endpoints": [
            "/v1/models",
            "/v1/chat/completions"
        ]
    }


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "river_initialized": river is not None,
        "model": river.current_model if river else None
    }


@app.get("/v1/models")
async def list_models():
    """List available models (OpenAI-compatible)"""
    models = [
        {"id": "gemini-3-flash-preview", "owned_by": "google"},
        {"id": "grok-4-1", "owned_by": "xai"},
        {"id": "gpt-5-2", "owned_by": "openai"},
        {"id": "claude-3-5-sonnet-20241022", "owned_by": "anthropic"},
        {"id": "deepseek-chat", "owned_by": "deepseek"},
        {"id": "river", "owned_by": "mumega"},  # Auto-switching
        {"id": "council", "owned_by": "mumega"},  # Multi-model
        {"id": "swarm", "owned_by": "mumega"},  # Parallel
    ]

    timestamp = int(datetime.now().timestamp())

    return ModelsResponse(
        data=[
            ModelInfo(
                id=m["id"],
                created=timestamp,
                owned_by=m["owned_by"]
            )
            for m in models
        ]
    )


@app.post("/v1/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    authorization: Optional[str] = Header(None)
):
    """OpenAI-compatible chat completions endpoint"""

    if not river:
        raise HTTPException(status_code=503, detail="River Engine not initialized")

    # Extract messages
    system_prompt = ""
    user_messages = []

    for msg in request.messages:
        if msg.role == "system":
            system_prompt = msg.content
        elif msg.role == "user":
            user_messages.append(msg.content)
        # Skip assistant messages (they're part of history)

    # Combine user messages
    user_prompt = "\n\n".join(user_messages) if user_messages else "Hello"

    # Switch model if different from current
    if request.model != river.current_model:
        # Map OpenAI model names to Mumega names
        model_map = {
            "gpt-3.5-turbo": "gemini-3-flash-preview",
            "gpt-4": "gpt-5-2",
            "gpt-4-turbo": "gpt-5-2",
            "claude-3-sonnet": "claude-3-5-sonnet-20241022",
        }
        target_model = model_map.get(request.model, request.model)

        try:
            river.switch_model(target_model)
            logger.info(f"Switched to model: {target_model}")
        except Exception as e:
            logger.warning(f"Could not switch to {target_model}: {e}")

    # Create message
    msg = Message(
        text=user_prompt,
        user_id="openai_api",
        user_name="API User",
        source=MessageSource.CLI,
        conversation_id=f"api_{uuid.uuid4().hex[:8]}"
    )

    try:
        # Get response from River
        response = await river.process_message(msg)
        response_text = response.text

        # Create OpenAI-compatible response
        completion_id = f"chatcmpl-{uuid.uuid4().hex}"
        timestamp = int(datetime.now().timestamp())

        return ChatCompletionResponse(
            id=completion_id,
            created=timestamp,
            model=river.current_model,
            choices=[
                ChatCompletionChoice(
                    index=0,
                    message=ChatMessage(
                        role="assistant",
                        content=response_text
                    ),
                    finish_reason="stop"
                )
            ],
            usage={
                "prompt_tokens": len(user_prompt.split()) * 2,  # Rough estimate
                "completion_tokens": len(response_text.split()) * 2,
                "total_tokens": (len(user_prompt) + len(response_text)) * 2
            }
        )

    except Exception as e:
        logger.error(f"Error processing message: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# --- Main ---

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Mumega OpenAI-Compatible API Server")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=9200, help="Port to bind to")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload")

    args = parser.parse_args()

    logger.info(f"🚀 Starting Mumega OpenAI-Compatible API on {args.host}:{args.port}")
    logger.info(f"📡 Access at: http://{args.host}:{args.port}/v1")

    uvicorn.run(
        "openai_compat_api:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info"
    )
