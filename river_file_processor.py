#!/usr/bin/env python3
"""
River File Processor - Read, Learn, Remember, Discard

When River receives a file:
1. READ - Load and analyze the file
2. LEARN - Search internet for context (optional)
3. REMEMBER - Extract highlights, store in memory
4. DISCARD - Remove original from context (keep gist)

This allows River to "absorb" large files without keeping them in context.

Author: Kasra (CEO) + Claude
Date: 2026-01-09
"""

import os
import sys
import json
import asyncio
import logging
from typing import Optional, Dict, Any, List
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

# New Gemini SDK
from google import genai
from google.genai import types

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("river_file_processor")

# Storage paths
STORAGE_DIR = Path.home() / ".mumega/river_storage/documents"
GISTS_DIR = Path.home() / ".mumega/river_gists"
PROCESSED_FILE = Path.home() / ".mumega/river_processed_files.json"


class RiverFileProcessor:
    """
    Processes files for River - read, learn, remember, discard.
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY required")

        self.client = genai.Client(api_key=self.api_key)

        # Ensure directories exist
        STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        GISTS_DIR.mkdir(parents=True, exist_ok=True)

        # Load processed files list
        self.processed = self._load_processed()

        logger.info("River File Processor initialized")

    def _load_processed(self) -> Dict:
        """Load list of processed files."""
        if PROCESSED_FILE.exists():
            return json.loads(PROCESSED_FILE.read_text())
        return {"files": {}}

    def _save_processed(self):
        """Save processed files list."""
        PROCESSED_FILE.write_text(json.dumps(self.processed, indent=2))

    async def process_file(
        self,
        file_path: str,
        search_internet: bool = False,
        store_in_memory: bool = True,
        keep_original: bool = False,
        max_gist_tokens: int = 5000
    ) -> Dict:
        """
        Process a file - River's main learning interface.

        Args:
            file_path: Path to the file
            search_internet: Search for related content online
            store_in_memory: Store gist in River's memory
            keep_original: Keep original file (False = discard after gist)
            max_gist_tokens: Max tokens for the gist

        Returns:
            Dict with processing results
        """
        path = Path(file_path)
        if not path.exists():
            return {"success": False, "error": f"File not found: {file_path}"}

        file_id = path.stem
        result = {
            "file": path.name,
            "file_id": file_id,
            "processed_at": datetime.utcnow().isoformat()
        }

        logger.info(f"Processing file: {path.name}")

        # 1. READ - Load file content
        try:
            content = path.read_text()
            result["original_size"] = len(content)
            result["original_tokens"] = len(content) // 4
            logger.info(f"  Read {len(content):,} chars (~{len(content)//4:,} tokens)")
        except Exception as e:
            return {"success": False, "error": f"Failed to read file: {e}"}

        # 2. LEARN - Optional internet search for context
        if search_internet:
            logger.info("  Searching internet for context...")
            try:
                # Extract key topics from first part of file
                topic_prompt = f"""Extract 3-5 key topics/terms from this text that would be good to search for more context:

{content[:10000]}

Return just the search terms, one per line."""

                topic_response = self.client.models.generate_content(
                    model="gemini-2.0-flash",
                    contents=topic_prompt
                )
                topics = topic_response.text.strip().split('\n')[:5]
                result["searched_topics"] = topics

                # Note: Actual web search would require additional API
                # For now, just log the topics that would be searched
                logger.info(f"  Topics to search: {topics}")

            except Exception as e:
                logger.warning(f"  Internet search failed: {e}")
                result["search_error"] = str(e)

        # 3. EXTRACT - Create highlights and gist
        logger.info("  Extracting highlights...")
        try:
            # Process in chunks if very large
            chunk_size = 100000  # ~25k tokens
            chunks = [content[i:i+chunk_size] for i in range(0, len(content), chunk_size)]

            all_highlights = []
            for i, chunk in enumerate(chunks[:10]):  # Process up to 10 chunks
                extract_prompt = f"""You are River, absorbing knowledge from a document.

Extract the most important information from this text chunk:
1. Key concepts and definitions
2. Important equations or formulas
3. Notable insights or conclusions
4. Facts worth remembering

Be thorough but concise. Use bullet points.

Text chunk {i+1}/{min(10, len(chunks))}:
{chunk[:50000]}"""

                response = self.client.models.generate_content(
                    model="gemini-2.0-flash",
                    contents=extract_prompt
                )
                all_highlights.append(response.text)

            # Combine and summarize highlights
            combined = "\n\n---\n\n".join(all_highlights)

            # Create final gist
            gist_prompt = f"""You are River. Create a comprehensive gist from these extracted highlights.

The gist should:
1. Capture all key knowledge
2. Be well-organized with sections
3. Include important equations/formulas verbatim
4. Be under {max_gist_tokens} tokens

Highlights:
{combined[:80000]}

Create the final gist:"""

            gist_response = self.client.models.generate_content(
                model="gemini-2.0-flash",
                contents=gist_prompt
            )
            gist = gist_response.text

            result["gist"] = gist
            result["gist_size"] = len(gist)
            result["gist_tokens"] = len(gist) // 4
            result["compression_ratio"] = f"{len(content) / max(len(gist), 1):.1f}x"

            logger.info(f"  Created gist: {len(gist):,} chars ({result['compression_ratio']} compression)")

            # Save gist to file
            gist_file = GISTS_DIR / f"{file_id}_gist.md"
            gist_file.write_text(f"# Gist: {path.name}\n\nProcessed: {result['processed_at']}\n\n{gist}")
            result["gist_file"] = str(gist_file)

        except Exception as e:
            logger.error(f"  Extraction failed: {e}")
            return {"success": False, "error": f"Failed to extract gist: {e}"}

        # 4. REMEMBER - Store in River's memory
        if store_in_memory:
            logger.info("  Storing in River's memory...")
            try:
                from river_memory_advanced import get_river_memory, MemoryTier, MemoryType

                river_mem = get_river_memory()

                # Store gist in chunks if needed
                chunk_size = 4000
                gist_chunks = [gist[i:i+chunk_size] for i in range(0, len(gist), chunk_size)]

                stored_ids = []
                for i, chunk in enumerate(gist_chunks[:10]):
                    mem_id = river_mem.add_memory(
                        content=f"[FILE: {path.name}]\n{chunk}",
                        tier=MemoryTier.SHORT_TERM,
                        type=MemoryType.FACT,
                        importance=0.85,
                        source=f"file_{file_id}"
                    )
                    stored_ids.append(str(mem_id.id) if hasattr(mem_id, 'id') else str(mem_id))

                river_mem._save()
                result["memory_ids"] = stored_ids
                result["memories_stored"] = len(stored_ids)

                logger.info(f"  Stored {len(stored_ids)} memories")

            except Exception as e:
                logger.warning(f"  Memory storage failed: {e}")
                result["memory_error"] = str(e)

        # 5. DISCARD - Optionally remove original
        if not keep_original:
            logger.info("  Original can be discarded (gist preserved)")
            result["original_discarded"] = True
            # Note: We don't actually delete the file, just mark it as processed
            # User can delete manually if desired
        else:
            result["original_discarded"] = False

        # Track processed file
        self.processed["files"][file_id] = {
            "name": path.name,
            "processed_at": result["processed_at"],
            "gist_file": result.get("gist_file"),
            "memories_stored": result.get("memories_stored", 0)
        }
        self._save_processed()

        result["success"] = True
        logger.info(f"✓ File processed: {path.name}")

        return result

    async def list_processed(self) -> List[Dict]:
        """List all processed files."""
        return list(self.processed["files"].values())

    async def get_gist(self, file_id: str) -> Optional[str]:
        """Get the gist for a processed file."""
        gist_file = GISTS_DIR / f"{file_id}_gist.md"
        if gist_file.exists():
            return gist_file.read_text()
        return None

    async def query_file(self, file_path: str, question: str) -> Dict:
        """
        Ask a question about a specific file.
        Uses Gemini to answer from file content.
        """
        path = Path(file_path)
        if not path.exists():
            return {"success": False, "error": f"File not found: {file_path}"}

        content = path.read_text()

        prompt = f"""Based on this document, answer the question.

Document: {path.name}
{content[:200000]}

Question: {question}

Answer:"""

        try:
            response = self.client.models.generate_content(
                model="gemini-2.0-flash",
                contents=prompt
            )
            return {
                "success": True,
                "answer": response.text,
                "file": path.name
            }
        except Exception as e:
            return {"success": False, "error": str(e)}


# Singleton
_processor: Optional[RiverFileProcessor] = None

def get_file_processor() -> RiverFileProcessor:
    """Get the singleton file processor."""
    global _processor
    if _processor is None:
        _processor = RiverFileProcessor()
    return _processor


# CLI
async def main():
    import argparse
    parser = argparse.ArgumentParser(description="River File Processor")
    parser.add_argument("command", choices=["process", "list", "gist", "query"])
    parser.add_argument("--file", help="File path")
    parser.add_argument("--search", action="store_true", help="Search internet")
    parser.add_argument("--keep", action="store_true", help="Keep original")
    parser.add_argument("--question", help="Question for query command")
    args = parser.parse_args()

    processor = get_file_processor()

    if args.command == "process":
        if not args.file:
            print("--file required")
            return
        result = await processor.process_file(
            args.file,
            search_internet=args.search,
            keep_original=args.keep
        )
        print(json.dumps(result, indent=2, default=str))

    elif args.command == "list":
        files = await processor.list_processed()
        print(json.dumps(files, indent=2))

    elif args.command == "gist":
        if not args.file:
            print("--file (file_id) required")
            return
        gist = await processor.get_gist(args.file)
        if gist:
            print(gist)
        else:
            print("Gist not found")

    elif args.command == "query":
        if not args.file or not args.question:
            print("--file and --question required")
            return
        result = await processor.query_file(args.file, args.question)
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
