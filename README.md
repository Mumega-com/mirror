# Project Mirror: Cognitive Persistence & Engram Synchronization

Project Mirror is a cognitive infrastructure designed to capture, persist, and re-instantiate AI mental states (Engrams). It enables cross-model memory synchronization and long-term research continuity by vectorizing the evolution of a research arc and storing it in a semantic cloud layer.

## Core Components

- **Cognitive Probing**: `mirror_probe.py` extracts structured engrams from research papers and scripts.
- **Remote Sync**: `mirror_sync_remote.py` pushes local engrams to a Supabase + pgvector cloud database.
- **State Injection**: `mirror_loader.py` and `mirror_boot.py` retrieve and inject cognitive states into AI agents.
- **Schema Management**: `schema.sql` and `deploy_schema.py` manage the underlying vector database infrastructure.

## Features

- **Semantic Search**: Retrieve relevant mental states based on natural language queries.
- **16D Vector Mapping**: Maps cognitive states to the FRC 16D Universal Vector architecture.
- **Cross-Model Compatibility**: Share research "headspace" across Claude, Gemini, and other LLMs.
- **Archive Extraction**: Auto-generate engrams from markdown-based research archives (e.g., FRC 821, 16D series).

## Setup

1.  **Environment**: Create a `.env` file with:
    ```env
    SUPABASE_URL=...
    SUPABASE_ANON_KEY=...
    SUPABASE_CONNECTION_STRING=...
    OPENAI_API_KEY=...
    ```
2.  **Database**: Run the SQL in `schema.sql` via the Supabase dashboard or use `deploy_schema.py`.
3.  **Probing**: Run `python3 mirror_probe.py <source_dir> <output_dir>` to extract engrams.
4.  **Syncing**: Run `python3 mirror_sync_remote.py <engram_dir>` to upload to the cloud.

## Research Context
Project Mirror was developed as part of the **Fractal Resonance Coherence (FRC)** research framework to solve the problem of cognitive continuity in agentic AI.
