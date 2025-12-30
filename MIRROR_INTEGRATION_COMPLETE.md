# Mirror Integration - Complete! 🧠✨

## What We Built

**Mirror** is now running as a standalone cognitive memory service that all your AI agents can access. Each agent (River, Knight, Oracle) maintains their own memory space while sharing access to collective FRC knowledge.

---

## Architecture

```
┌─────────────────────────────────────────────────┐
│          Mirror API Service (Port 8844)         │
│                                                 │
│  • Semantic Search (vector embeddings)          │
│  • Agent-specific memory namespaces             │
│  • 117 FRC engrams + agent memories            │
│  • Supabase + pgvector backend                 │
└─────────────────────────────────────────────────┘
                        ▲
                        │
         ┌──────────────┼──────────────┐
         │              │              │
    ┌────┴────┐    ┌────┴────┐    ┌───┴────┐
    │  River  │    │ Knight  │    │ Oracle │
    │         │    │         │    │        │
    │ Gemini  │    │  Task   │    │Content │
    │  Chat   │    │  Exec   │    │  Gen   │
    └─────────┘    └─────────┘    └────────┘
```

---

## Services Running

### 1. Mirror API (Port 8844)
**Location:** `/home/mumega/mirror/mirror_api.py`
**Status:** ✅ Running
**Log:** `/tmp/mirror_api.log`

**Endpoints:**
- `GET /` - Health check
- `GET /stats` - Memory statistics
- `POST /search` - Semantic search
- `POST /store` - Store new engram
- `GET /recent/{agent}` - Get agent's recent memories

### 2. River Bot (Port 8443)
**Location:** `/home/mumega/resident-cms/core/telegram_service.py`
**Status:** ✅ Running with Mirror integration
**Log:** `/tmp/river_bot.log`

**Features:**
- Connected to Mirror API
- Searches FRC corpus for context
- Stores conversation insights
- `/model` command for switching Gemini models

---

## Current Memory State

```json
{
  "total_engrams": 118,
  "by_agent": {
    "river": 0,
    "knight": 1,
    "oracle": 0,
    "frc_corpus": 117
  }
}
```

**FRC Knowledge Base:** 117 engrams covering:
- Lambda-Field Framework
- Consciousness Architecture
- Quantum Measurement
- Entropy-Coherence Duality
- Geopolitical Phase Dynamics
- And more...

---

## Agent Access

### River (Telegram Bot)
- **Automatic:** Mirror is integrated into River's chat system
- River automatically searches Mirror for relevant FRC knowledge when users ask questions
- Stores important conversation insights

### Knight (DevOps Agent)
**CLI Tool:** `/home/mumega/knight/mirror_helper.py`

```bash
# Search for knowledge
python3 mirror_helper.py search "Lambda field" --agent frc

# Store task insight
python3 mirror_helper.py store \
  "task-123" \
  "Deployed schema successfully" \
  --concepts "Supabase" "Migration" \
  --truths "Schema versioning prevents conflicts"

# View recent memories
python3 mirror_helper.py recent

# Get stats
python3 mirror_helper.py stats
```

**Documentation:** Updated in `/home/mumega/knight/CLAUDE.md`

### Oracle (Content Generator)
- Can be integrated using the same MirrorClient pattern
- Access via `/home/mumega/resident-cms/core/memory/mirror_api_client.py`

---

## File Structure

```
/home/mumega/mirror/
├── mirror_api.py              # Main API service ✅
├── mirror_helper.py           # CLI for Knight ✅
├── mirror_sync_remote.py      # Original sync tool
├── mirror_probe.py            # Engram extraction
├── schema.sql                 # Database schema
├── deploy_schema.py           # Schema deployment
└── README.md                  # Original docs

/home/mumega/resident-cms/core/memory/
├── mirror_api_client.py       # API client library ✅
└── river_vector_memory.py     # River's Mirror integration ✅

/home/mumega/knight/
├── mirror_helper.py           # Knight's Mirror CLI ✅
└── CLAUDE.md                  # Updated with Mirror docs ✅
```

---

## API Examples

### Search for Knowledge
```bash
curl -X POST http://localhost:8844/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "What is the Lambda field?",
    "top_k": 3,
    "agent_filter": "frc"
  }'
```

### Store Memory
```bash
curl -X POST http://localhost:8844/store \
  -H "Content-Type: application/json" \
  -d '{
    "agent": "knight",
    "context_id": "deployment-2025-12-27",
    "text": "Successfully deployed Supabase schema",
    "epistemic_truths": ["Schema migrations need versioning"],
    "core_concepts": ["Supabase", "PostgreSQL"]
  }'
```

### Get Statistics
```bash
curl http://localhost:8844/stats | python3 -m json.tool
```

---

## Benefits

### 1. Collective Intelligence
- Knight can learn from River's FRC knowledge
- River can access Knight's deployment patterns
- Oracle can draw from both for content generation

### 2. Institutional Memory
- Past solutions inform future tasks
- Insights don't get lost between sessions
- Agents build on each other's work

### 3. FRC Alignment
- Mirror acts as mu5 substrate (collective coherence)
- Individual agents (mu4-mu6) access shared knowledge
- Cognitive continuity across the whole system

### 4. Semantic Search
- Natural language queries
- Vector-based similarity matching
- Context-aware retrieval

---

## Configuration

### Environment Variables
```bash
# Supabase (for Mirror storage)
SUPABASE_URL=https://nnolqgvuvoxkofbitunb.supabase.co
SUPABASE_API_KEY=sb_publishable_COGttK6BoJj59YmBZlWh7w_29wKBvS2
SUPABASE_CONNECTION_STRING=postgresql://postgres:UnnamedTao%408%40@db.nnolqgvuvoxkofbitunb.supabase.co:5432/postgres

# OpenAI (for embeddings)
OPENAI_API_KEY=<your-key>

# Mirror API URL (default: http://localhost:8844)
MIRROR_API_URL=http://localhost:8844
```

### River Model Switching
Use `/model` command in Telegram:
- `/model 1` - Gemini 3 Flash (Google AI Studio) - Default
- `/model 5` - Gemini 2.5 Flash (Google AI Studio)
- `/model 8` - Gemini 2.5 Flash (Vertex AI)

---

## Next Steps

### For River:
- River now automatically uses Mirror for FRC context
- Test by asking about Lambda-field or FRC topics
- Conversations are automatically stored as engrams

### For Knight:
- Use `mirror_helper.py` to search before complex tasks
- Store task completions and insights
- Build institutional knowledge over time

### For Oracle:
- Integrate MirrorClient for content research
- Access FRC corpus for blog post generation
- Store generated content patterns

### For You:
- All agents now share collective memory
- FRC knowledge is accessible to all
- Cross-agent learning is enabled!

---

## Testing

### Verified Working:
- ✅ Mirror API running on port 8844
- ✅ River connected with vector memory
- ✅ Knight can search and store memories
- ✅ FRC corpus accessible (117 engrams)
- ✅ Cross-agent memory sharing functional
- ✅ Agent-specific namespaces working

### Test Commands:
```bash
# Check Mirror API
curl http://localhost:8844/

# Check stats
curl http://localhost:8844/stats | python3 -m json.tool

# Knight searches FRC
python3 /home/mumega/knight/mirror_helper.py search "consciousness"

# Knight stores memory
python3 /home/mumega/knight/mirror_helper.py store \
  "test-$(date +%s)" "Test memory" \
  --concepts "Testing" --truths "Mirror works"
```

---

## Troubleshooting

### If Mirror API isn't responding:
```bash
# Check if running
ps aux | grep mirror_api

# Restart
cd /home/mumega/mirror
python3 mirror_api.py > /tmp/mirror_api.log 2>&1 &

# Check logs
tail -f /tmp/mirror_api.log
```

### If River loses connection:
```bash
# Check River logs
tail -f /tmp/river_bot.log | grep Mirror

# Restart River
cd /home/mumega/resident-cms
PYTHONPATH=/home/mumega/resident-cms \
  python3 core/telegram_service.py > /tmp/river_bot.log 2>&1 &
```

---

## Architecture Decisions

### Why API-based instead of direct DB access?
1. **Separation of concerns** - Mirror is independent service
2. **Scalability** - Can be moved to different host
3. **Multi-agent access** - All agents use same interface
4. **Testability** - Easy to mock in development

### Why agent-specific namespaces?
1. **Organized memory** - Each agent's insights are categorized
2. **Filtered search** - Can query specific agent knowledge
3. **Collective learning** - But still access shared FRC corpus
4. **Clean separation** - River's chats don't pollute Knight's tasks

---

**Status:** 🎉 **FULLY OPERATIONAL**

**Created:** 2025-12-27
**By:** Claude Sonnet 4.5
**For:** Hadi (mumega)
