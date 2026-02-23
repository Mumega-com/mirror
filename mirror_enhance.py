"""
Mirror Enhance - Mem0-Inspired Memory Capabilities
===================================================
Adds automatic memory extraction, consolidation, decay, and graph relationships
to the existing Mirror memory system.

Features adapted from Mem0:
1. Auto-extraction: Automatically extract key facts from conversations
2. Consolidation: Merge similar/duplicate memories
3. Decay: Relevance scoring based on access patterns
4. Relationships: Graph connections between engrams

Usage:
    from mirror_enhance import MirrorEnhance

    enhance = MirrorEnhance()

    # Auto-extract memories from conversation
    memories = await enhance.extract_memories(conversation_text, user_id="user_123")

    # Consolidate similar memories
    await enhance.consolidate(user_id="user_123")

    # Search with decay-aware ranking
    results = await enhance.smart_search(query, user_id="user_123")
"""

import os
import json
import logging
import hashlib
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv("/home/mumega/resident-cms/.env")

try:
    from supabase import create_client, Client
    from openai import OpenAI
except ImportError:
    raise ImportError("Install: pip install supabase openai")

logger = logging.getLogger("mirror.enhance")

# Initialize clients
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")


@dataclass
class ExtractedMemory:
    """A single extracted memory fact."""
    content: str
    category: str  # fact, preference, context, relationship
    confidence: float
    source_hash: str
    metadata: Dict = field(default_factory=dict)


@dataclass
class EngramRelationship:
    """Relationship between two engrams."""
    source_id: str
    target_id: str
    relation_type: str  # related_to, contradicts, extends, supersedes
    strength: float  # 0.0 - 1.0


class MirrorEnhance:
    """
    Mem0-inspired enhancements for Mirror memory system.

    Works alongside existing mirror_api.py without modifying it.
    """

    def __init__(self, use_free_models: bool = True):
        self.supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        self.use_free_models = use_free_models

        # Try free models first (saves OpenAI quota)
        self.free_client = None
        if use_free_models:
            try:
                from river_free_models import get_free_client
                self.free_client = get_free_client()
                logger.info("MirrorEnhance using FREE models for extraction")
            except ImportError:
                logger.warning("Free models not available, falling back to OpenAI")
                self.use_free_models = False

        # Fallback to OpenAI
        if not self.use_free_models or not self.free_client:
            self.openai = OpenAI(api_key=OPENAI_API_KEY)
        else:
            self.openai = None

        # Decay parameters
        self.decay_half_life_days = 30  # Memories lose half relevance in 30 days
        self.access_boost = 0.1  # Each access boosts relevance by 10%

        logger.info("MirrorEnhance initialized")

    # =========================================================================
    # 1. AUTOMATIC MEMORY EXTRACTION
    # =========================================================================

    async def extract_memories(
        self,
        text: str,
        user_id: str,
        agent: str = "river",
        store: bool = True
    ) -> List[ExtractedMemory]:
        """
        Automatically extract key facts/memories from conversation text.

        This is the core Mem0 feature - instead of manually storing,
        we analyze text and extract what's worth remembering.
        """
        prompt = f"""Analyze this conversation and extract key memories worth storing.

For each memory, provide:
- content: The specific fact, preference, or context (1-2 sentences)
- category: One of [fact, preference, context, relationship, goal, instruction]
- confidence: How certain this is true (0.0-1.0)

Focus on:
- User preferences and opinions
- Important facts mentioned
- Relationships between entities
- Goals or intentions expressed
- Instructions or rules given

Ignore:
- Greetings and small talk
- Uncertain or hypothetical statements
- Already well-known facts

Text:
\"\"\"
{text}
\"\"\"

Return JSON array:
[{{"content": "...", "category": "...", "confidence": 0.x}}, ...]

If nothing worth extracting, return empty array: []
"""

        try:
            # Use free models when available
            if self.free_client and self.use_free_models:
                raw_response = self.free_client.extract(
                    text=prompt,
                    instruction="You extract key memories from conversations. Return only valid JSON array."
                )
                # Parse JSON from response
                if raw_response:
                    # Find JSON in response
                    import re
                    json_match = re.search(r'\[.*\]', raw_response, re.DOTALL)
                    if json_match:
                        data = json.loads(json_match.group())
                    else:
                        data = json.loads(raw_response)
                else:
                    data = []
            else:
                # Fallback to OpenAI
                response = self.openai.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": "You extract key memories from conversations. Return only valid JSON."},
                        {"role": "user", "content": prompt}
                    ],
                    response_format={"type": "json_object"}
                )
                data = json.loads(response.choices[0].message.content)
            memories_data = data.get("memories", data) if isinstance(data, dict) else data

            if not isinstance(memories_data, list):
                memories_data = []

            # Create ExtractedMemory objects
            source_hash = hashlib.md5(text.encode()).hexdigest()[:12]
            memories = []

            for m in memories_data:
                if not m.get("content"):
                    continue

                memory = ExtractedMemory(
                    content=m["content"],
                    category=m.get("category", "fact"),
                    confidence=float(m.get("confidence", 0.7)),
                    source_hash=source_hash,
                    metadata={"user_id": user_id, "agent": agent}
                )
                memories.append(memory)

            logger.info(f"Extracted {len(memories)} memories from text")

            # Store if requested
            if store and memories:
                await self._store_extracted_memories(memories, user_id, agent)

            return memories

        except Exception as e:
            logger.error(f"Memory extraction failed: {e}")
            return []

    async def _store_extracted_memories(
        self,
        memories: List[ExtractedMemory],
        user_id: str,
        agent: str
    ):
        """Store extracted memories to Mirror."""
        for memory in memories:
            # Check for duplicates first
            is_duplicate = await self._check_duplicate(memory.content, user_id)
            if is_duplicate:
                logger.debug(f"Skipping duplicate: {memory.content[:50]}...")
                continue

            # Generate embedding
            embedding = self._get_embedding(memory.content)

            # Store in mirror_engrams
            context_id = f"auto_{user_id}_{memory.source_hash}_{datetime.utcnow().strftime('%H%M%S')}"

            data = {
                "context_id": context_id,
                "timestamp": datetime.utcnow().isoformat(),
                "series": f"{agent.title()} - Auto Extracted",
                "epistemic_truths": [memory.content],
                "core_concepts": [memory.category],
                "affective_vibe": "Extracted",
                "energy_level": f"{memory.confidence:.0%} confidence",
                "raw_data": {
                    "agent": agent,
                    "text": memory.content,
                    "metadata": {
                        "user_id": user_id,
                        "category": memory.category,
                        "confidence": memory.confidence,
                        "source_hash": memory.source_hash,
                        "auto_extracted": True
                    }
                },
                "embedding": embedding,
                # Enhanced fields
                "relevance_score": 1.0,  # Fresh memory starts at 1.0
                "access_count": 0,
                "last_accessed": datetime.utcnow().isoformat()
            }

            self.supabase.table("mirror_engrams").insert(data).execute()
            logger.info(f"Stored: {memory.content[:50]}...")

    async def _check_duplicate(self, content: str, user_id: str, threshold: float = 0.92) -> bool:
        """Check if similar memory already exists."""
        embedding = self._get_embedding(content)

        try:
            response = self.supabase.rpc(
                "mirror_match_engrams",
                {
                    "query_embedding": embedding,
                    "match_threshold": threshold,
                    "match_count": 1
                }
            ).execute()

            return len(response.data) > 0
        except:
            return False

    # =========================================================================
    # 2. MEMORY CONSOLIDATION
    # =========================================================================

    async def consolidate(
        self,
        user_id: str = None,
        agent: str = None,
        similarity_threshold: float = 0.88
    ) -> Dict[str, int]:
        """
        Consolidate similar memories by merging them.

        This prevents memory bloat and creates stronger, unified memories.
        """
        stats = {"checked": 0, "merged": 0, "kept": 0}

        # Get all engrams for user/agent
        query = self.supabase.table("mirror_engrams").select("*")

        if agent:
            query = query.ilike("series", f"%{agent}%")

        response = query.order("timestamp", desc=True).limit(500).execute()
        engrams = response.data
        stats["checked"] = len(engrams)

        # Find clusters of similar engrams
        merged_ids = set()

        for i, engram in enumerate(engrams):
            if engram["id"] in merged_ids:
                continue

            # Find similar engrams
            similar = await self._find_similar(
                engram["embedding"],
                exclude_id=engram["id"],
                threshold=similarity_threshold
            )

            if similar:
                # Merge into primary engram
                await self._merge_engrams(engram, similar)
                merged_ids.update([s["id"] for s in similar])
                stats["merged"] += len(similar)
            else:
                stats["kept"] += 1

        logger.info(f"Consolidation: {stats}")
        return stats

    async def _find_similar(
        self,
        embedding: List[float],
        exclude_id: str,
        threshold: float
    ) -> List[Dict]:
        """Find engrams similar to given embedding."""
        try:
            response = self.supabase.rpc(
                "mirror_match_engrams",
                {
                    "query_embedding": embedding,
                    "match_threshold": threshold,
                    "match_count": 10
                }
            ).execute()

            return [r for r in response.data if r["id"] != exclude_id]
        except:
            return []

    async def _merge_engrams(self, primary: Dict, duplicates: List[Dict]):
        """Merge duplicate engrams into primary."""
        # Combine epistemic truths
        all_truths = set(primary.get("epistemic_truths", []))
        all_concepts = set(primary.get("core_concepts", []))

        for dup in duplicates:
            all_truths.update(dup.get("epistemic_truths", []))
            all_concepts.update(dup.get("core_concepts", []))

        # Update primary with merged data
        self.supabase.table("mirror_engrams").update({
            "epistemic_truths": list(all_truths)[:10],  # Limit
            "core_concepts": list(all_concepts)[:10],
            "energy_level": f"Consolidated from {len(duplicates) + 1} memories"
        }).eq("id", primary["id"]).execute()

        # Delete duplicates
        for dup in duplicates:
            self.supabase.table("mirror_engrams").delete().eq("id", dup["id"]).execute()

        logger.info(f"Merged {len(duplicates)} engrams into {primary['context_id']}")

    # =========================================================================
    # 3. RELEVANCE DECAY
    # =========================================================================

    def calculate_decay(self, last_accessed: str, access_count: int = 0) -> float:
        """
        Calculate relevance score based on time decay and access patterns.

        Uses exponential decay with access boosts.
        """
        try:
            last_access = datetime.fromisoformat(last_accessed.replace("Z", ""))
        except:
            last_access = datetime.utcnow()

        days_since_access = (datetime.utcnow() - last_access).days

        # Exponential decay: relevance = 0.5^(days/half_life)
        decay_factor = 0.5 ** (days_since_access / self.decay_half_life_days)

        # Access boost: each access adds 10%, capped at 2x
        access_boost = min(1 + (access_count * self.access_boost), 2.0)

        # Final relevance (0.0 - 1.0)
        relevance = min(decay_factor * access_boost, 1.0)

        return relevance

    async def smart_search(
        self,
        query: str,
        user_id: str = None,
        agent: str = None,
        top_k: int = 5,
        include_decay: bool = True
    ) -> List[Dict]:
        """
        Search with decay-aware ranking.

        Combines semantic similarity with relevance decay for better results.
        """
        embedding = self._get_embedding(query)

        # Get more results than needed for re-ranking
        response = self.supabase.rpc(
            "mirror_match_engrams",
            {
                "query_embedding": embedding,
                "match_threshold": 0.5,
                "match_count": top_k * 3
            }
        ).execute()

        results = response.data

        # Apply decay-aware scoring
        if include_decay:
            for result in results:
                similarity = result.get("similarity", 0.5)
                decay = self.calculate_decay(
                    result.get("last_accessed", result.get("timestamp", "")),
                    result.get("access_count", 0)
                )

                # Combined score: 70% similarity + 30% relevance
                result["final_score"] = (similarity * 0.7) + (decay * 0.3)
                result["relevance_decay"] = decay

            # Re-rank by final score
            results.sort(key=lambda x: x.get("final_score", 0), reverse=True)

        # Update access counts for returned results
        for result in results[:top_k]:
            await self._record_access(result["id"])

        return results[:top_k]

    async def _record_access(self, engram_id: str):
        """Record that an engram was accessed (for decay calculation)."""
        try:
            # Increment access count and update last_accessed
            self.supabase.rpc(
                "increment_access_count",  # Needs to be created
                {"engram_id": engram_id}
            ).execute()
        except:
            # Fallback if function doesn't exist
            pass

    # =========================================================================
    # 4. ENGRAM RELATIONSHIPS (Graph)
    # =========================================================================

    async def create_relationship(
        self,
        source_id: str,
        target_id: str,
        relation_type: str = "related_to",
        strength: float = 0.8
    ) -> bool:
        """Create a relationship between two engrams."""
        try:
            data = {
                "source_id": source_id,
                "target_id": target_id,
                "relation_type": relation_type,
                "strength": strength,
                "created_at": datetime.utcnow().isoformat()
            }

            self.supabase.table("mirror_relationships").upsert(
                data,
                on_conflict="source_id,target_id"
            ).execute()

            return True
        except Exception as e:
            logger.error(f"Failed to create relationship: {e}")
            return False

    async def auto_relate(self, engram_id: str, threshold: float = 0.75) -> List[str]:
        """Automatically find and create relationships for an engram."""
        # Get the engram
        engram = self.supabase.table("mirror_engrams")\
            .select("*").eq("id", engram_id).single().execute()

        if not engram.data:
            return []

        # Find related engrams
        similar = await self._find_similar(
            engram.data["embedding"],
            exclude_id=engram_id,
            threshold=threshold
        )

        related_ids = []
        for s in similar[:5]:  # Limit to 5 relationships
            await self.create_relationship(
                source_id=engram_id,
                target_id=s["id"],
                relation_type="related_to",
                strength=s.get("similarity", 0.8)
            )
            related_ids.append(s["id"])

        return related_ids

    async def get_related(self, engram_id: str, depth: int = 1) -> List[Dict]:
        """Get related engrams (graph traversal)."""
        try:
            response = self.supabase.table("mirror_relationships")\
                .select("*, target:mirror_engrams!target_id(*)")\
                .eq("source_id", engram_id)\
                .order("strength", desc=True)\
                .execute()

            return response.data
        except:
            return []

    # =========================================================================
    # UTILITIES
    # =========================================================================

    def _get_embedding(self, text: str) -> List[float]:
        """Generate embedding for text."""
        response = self.openai.embeddings.create(
            input=text,
            model="text-embedding-3-small"
        )
        return response.data[0].embedding


# SQL migrations needed for enhanced features
ENHANCED_SCHEMA = """
-- Add new columns to mirror_engrams (run if not exists)
ALTER TABLE mirror_engrams
ADD COLUMN IF NOT EXISTS relevance_score FLOAT DEFAULT 1.0,
ADD COLUMN IF NOT EXISTS access_count INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS last_accessed TIMESTAMP DEFAULT NOW();

-- Create relationships table
CREATE TABLE IF NOT EXISTS mirror_relationships (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id UUID REFERENCES mirror_engrams(id) ON DELETE CASCADE,
    target_id UUID REFERENCES mirror_engrams(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL DEFAULT 'related_to',
    strength FLOAT DEFAULT 0.8,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(source_id, target_id)
);

-- Function to increment access count
CREATE OR REPLACE FUNCTION increment_access_count(engram_id UUID)
RETURNS VOID AS $$
BEGIN
    UPDATE mirror_engrams
    SET access_count = access_count + 1,
        last_accessed = NOW()
    WHERE id = engram_id;
END;
$$ LANGUAGE plpgsql;
"""


if __name__ == "__main__":
    import asyncio

    async def test():
        enhance = MirrorEnhance()

        # Test extraction
        test_text = """
        User: I prefer Python over JavaScript for backend work.
        Also, my API key for the project is stored in .env files.
        We should always use async/await for I/O operations.
        The deadline for the MVP is next Friday.
        """

        memories = await enhance.extract_memories(
            test_text,
            user_id="test_user",
            agent="river",
            store=False  # Don't actually store in test
        )

        print("Extracted Memories:")
        for m in memories:
            print(f"  [{m.category}] {m.content} (conf: {m.confidence})")

    asyncio.run(test())
