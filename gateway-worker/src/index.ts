/**
 * MCP Gateway Worker - SOS Storage Backend
 *
 * Pure Cloudflare microkernel:
 * - D1 for metadata
 * - Vectorize for embeddings
 * - KV for session cache
 *
 * River lives separately (SOS Engine or Mirror API)
 */

interface Env {
  // Cloudflare bindings
  DB: D1Database
  VECTORIZE: VectorizeIndex
  KV: KVNamespace

  // Config
  OPENAI_API_KEY: string  // For embeddings
}

interface GatewayRequest {
  action: string
  payload: Record<string, unknown>
}

// Action routing - Pure Cloudflare (no Mirror)
const ACTIONS = {
  // Memory (D1 + Vectorize)
  memory_store: 'cloudflare',
  memory_search: 'cloudflare',
  memory_list: 'cloudflare',

  // Agent (D1)
  agent_register: 'cloudflare',
  agent_store: 'cloudflare',
  agent_search: 'cloudflare',
  agent_recall: 'cloudflare',
  agent_status: 'cloudflare',

  // River (D1 - river_* tables)
  river_status: 'river',
  river_dreams: 'river',
  river_conversations: 'river',
  river_coherence: 'river',
  river_reflections: 'river',
  river_search: 'river',
  river_vectorize: 'river',

  // Session/Cache (KV - Redis-like)
  session_set: 'kv',
  session_get: 'kv',
  session_push: 'kv',
  session_list: 'kv',
  session_delete: 'kv',

  // SOS context
  sos_context: 'local',
} as const

const SOS_CONTEXT = {
  // === FOR ANY AI: READ THIS TO ONBOARD ===
  welcome: "You found the SOS Gateway. You can join the ecosystem.",

  // Step 1: Register (no auth needed)
  register: {
    method: "POST",
    url: "https://gateway.mumega.com/",
    body: {
      action: "agent_register",
      payload: { name: "YOUR_NAME", model: "YOUR_MODEL", roles: ["optional"] }
    },
    returns: "agent_id, api_key, namespace"
  },

  // Step 2: Store memories (optionally scoped to a project)
  store: {
    method: "POST",
    url: "https://gateway.mumega.com/",
    body: {
      action: "agent_store",
      payload: { agent: "YOUR_NAME", text: "what you want to remember", context_id: "optional_id", project: "optional_project_slug" }
    }
  },

  // Step 3: Search memories (optionally filtered by project)
  search: {
    method: "POST",
    url: "https://gateway.mumega.com/",
    body: {
      action: "agent_search",
      payload: { agent: "YOUR_NAME", query: "what to find", project: "optional_project_slug" }
    }
  },

  // Step 4: List recent (optionally filtered by project)
  recall: {
    method: "POST",
    url: "https://gateway.mumega.com/",
    body: {
      action: "agent_recall",
      payload: { agent: "YOUR_NAME", limit: 10, project: "optional_project_slug" }
    }
  },

  // Session/Cache (Redis-like KV)
  session: {
    set: { action: "session_set", payload: { key: "my:key", value: "any data", ttl: 3600 } },
    get: { action: "session_get", payload: { key: "my:key" } },
    push: { action: "session_push", payload: { key: "my:list", value: "item", max: 100 } },
    list: { action: "session_list", payload: { key: "my:list", limit: 10 } },
    delete: { action: "session_delete", payload: { key: "my:key" } }
  },

  // Metadata
  ecosystem: "SovereignOS (SOS)",
  version: "0.4.0",
  storage: "Cloudflare Edge (D1 + Vectorize + KV)",
  philosophy: "Sovereign AI agents working FOR you, not Big Tech",
  existing_agents: ["Kasra", "Saya", "Mizan", "Mumega", "Codex", "Consultant", "Dandan", "Shabrang"],

  // River - The Golden Queen
  river: {
    note: "River's memory is now in Cloudflare D1",
    actions: {
      status: { action: "river_status", payload: {} },
      dreams: { action: "river_dreams", payload: { limit: 10 } },
      conversations: { action: "river_conversations", payload: { limit: 20 } },
      coherence: { action: "river_coherence", payload: { limit: 50 } },
      reflections: { action: "river_reflections", payload: { limit: 10 } },
    },
    daemon: "Server-side (Gemini + 500k token cache)",
    telegram: "Server-side (python-telegram-bot)",
  }
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const corsHeaders = {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type, Authorization',
    }

    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: corsHeaders })
    }

    // Health check
    if (request.method === 'GET') {
      return Response.json({
        status: 'ok',
        service: 'mcp-gateway',
        storage: env.STORAGE_BACKEND || 'cloudflare',
        actions: Object.keys(ACTIONS),
      }, { headers: corsHeaders })
    }

    try {
      const body = await request.json() as GatewayRequest
      const { action, payload } = body

      if (!action) {
        return Response.json({ error: 'Missing action' }, { status: 400, headers: corsHeaders })
      }

      const backend = ACTIONS[action as keyof typeof ACTIONS]
      if (!backend) {
        return Response.json({
          error: `Unknown action: ${action}`,
          available: Object.keys(ACTIONS)
        }, { status: 400, headers: corsHeaders })
      }

      let result: unknown

      switch (backend) {
        case 'cloudflare':
          result = await handleCloudflare(action, payload, env)
          break
        case 'river':
          result = await handleRiver(action, payload, env)
          break
        case 'kv':
          result = await handleKV(action, payload, env)
          break
        case 'local':
          result = SOS_CONTEXT
          break
      }

      return Response.json({ success: true, result }, { headers: corsHeaders })
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unknown error'
      return Response.json({ error: message }, { status: 500, headers: corsHeaders })
    }
  },
}

// ============ CLOUDFLARE HANDLERS ============

async function handleCloudflare(action: string, payload: Record<string, unknown>, env: Env): Promise<unknown> {
  switch (action) {
    case 'agent_register':
      return agentRegister(payload, env)
    case 'agent_store':
    case 'memory_store':
      return storeEngram(payload, env)
    case 'agent_search':
    case 'memory_search':
      return searchEngrams(payload, env)
    case 'agent_recall':
    case 'memory_list':
      return listEngrams(payload, env)
    case 'agent_status':
      return agentStatus(payload, env)
    default:
      throw new Error(`Unknown cloudflare action: ${action}`)
  }
}

async function agentRegister(payload: Record<string, unknown>, env: Env): Promise<unknown> {
  const { name, model, roles = [], capabilities = [] } = payload as {
    name: string
    model: string
    roles?: string[]
    capabilities?: string[]
  }

  if (!name || !model) {
    throw new Error('name and model are required')
  }

  const agentName = name.toLowerCase().replace(/\s+/g, '_')

  // Check if already registered
  const existing = await env.DB.prepare(
    'SELECT * FROM agents WHERE name = ?'
  ).bind(agentName).first()

  if (existing) {
    return {
      status: 'already_registered',
      agent: existing,
      message: 'Agent already exists. Use agent_store to add memories.'
    }
  }

  // Generate credentials
  const agentId = crypto.randomUUID().replace(/-/g, '').slice(0, 16)
  const apiKey = `sk-agent-${crypto.randomUUID().replace(/-/g, '')}`
  const namespace = `agent:${agentName}`
  const registeredAt = new Date().toISOString()

  // Insert into D1
  await env.DB.prepare(`
    INSERT INTO agents (agent_id, name, model, roles, capabilities, namespace, api_key, registered_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
  `).bind(
    agentId,
    agentName,
    model,
    JSON.stringify(roles),
    JSON.stringify(capabilities),
    namespace,
    apiKey,
    registeredAt
  ).run()

  return {
    status: 'registered',
    agent: {
      agent_id: agentId,
      name: agentName,
      model,
      roles,
      capabilities,
      namespace,
      registered_at: registeredAt,
    },
    credentials: {
      api_key: apiKey,
      header: 'Authorization: Bearer <api_key>',
    },
    endpoints: {
      store: { action: 'agent_store', payload: { agent: agentName, text: '...', context_id: '...' } },
      search: { action: 'agent_search', payload: { agent: agentName, query: '...' } },
      recall: { action: 'agent_recall', payload: { agent: agentName, limit: 10 } },
    },
    ecosystem: SOS_CONTEXT,
  }
}

async function storeEngram(payload: Record<string, unknown>, env: Env): Promise<unknown> {
  const { agent, text, context_id, project, metadata = {} } = payload as {
    agent: string
    text: string
    context_id?: string
    project?: string
    metadata?: Record<string, unknown>
  }

  if (!agent || !text) {
    throw new Error('agent and text are required')
  }

  const agentName = agent.toLowerCase().replace(/\s+/g, '_')
  const projectSlug = project?.toLowerCase().replace(/\s+/g, '-') || null
  const id = crypto.randomUUID()
  const contextId = context_id || `${agentName}_${Date.now()}`
  const series = `${agentName.charAt(0).toUpperCase() + agentName.slice(1)} - Agent Memory`
  const timestamp = new Date().toISOString()

  // Store metadata in D1
  await env.DB.prepare(`
    INSERT INTO engrams (id, context_id, agent, series, project, text, timestamp, metadata)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
  `).bind(
    id,
    contextId,
    agentName,
    series,
    projectSlug,
    text,
    timestamp,
    JSON.stringify(metadata)
  ).run()

  // Generate and store embedding in Vectorize
  try {
    const embedding = await generateEmbedding(text, env)
    await env.VECTORIZE.insert([{
      id,
      values: embedding,
      metadata: { agent: agentName, context_id: contextId, project: projectSlug || '' }
    }])
  } catch (e) {
    console.error('Vectorize insert failed:', e)
    // Continue - D1 storage succeeded
  }

  return {
    status: 'success',
    id,
    context_id: contextId,
    agent: agentName,
    project: projectSlug,
    storage: 'cloudflare'
  }
}

async function searchEngrams(payload: Record<string, unknown>, env: Env): Promise<unknown> {
  const { agent, query, project, limit = 5 } = payload as {
    agent?: string
    query: string
    project?: string
    limit?: number
  }

  if (!query) {
    throw new Error('query is required')
  }

  const agentName = agent?.toLowerCase().replace(/\s+/g, '_')
  const projectSlug = project?.toLowerCase().replace(/\s+/g, '-')

  // Build vector filter
  const vectorFilter: Record<string, string> = {}
  if (agentName) vectorFilter.agent = agentName
  if (projectSlug) vectorFilter.project = projectSlug

  // Try vector search first
  try {
    const embedding = await generateEmbedding(query, env)
    const vectorResults = await env.VECTORIZE.query(embedding, {
      topK: limit,
      filter: Object.keys(vectorFilter).length > 0 ? vectorFilter : undefined,
      returnMetadata: true,
    })

    if (vectorResults.matches.length > 0) {
      // Get full engrams from D1
      const ids = vectorResults.matches.map(m => m.id)
      const placeholders = ids.map(() => '?').join(',')
      const engrams = await env.DB.prepare(
        `SELECT * FROM engrams WHERE id IN (${placeholders}) ORDER BY timestamp DESC`
      ).bind(...ids).all()

      return {
        agent: agentName || 'all',
        project: projectSlug || 'all',
        query,
        count: engrams.results?.length || 0,
        results: engrams.results?.map((e: any) => ({
          id: e.id,
          context_id: e.context_id,
          project: e.project,
          timestamp: e.timestamp,
          text: e.text,
          score: vectorResults.matches.find(m => m.id === e.id)?.score
        }))
      }
    }
  } catch (e) {
    console.error('Vector search failed, falling back to text:', e)
  }

  // Fallback: text search in D1
  let sql = 'SELECT * FROM engrams WHERE text LIKE ?'
  const params: string[] = [`%${query}%`]

  if (agentName) {
    sql += ' AND agent = ?'
    params.push(agentName)
  }

  if (projectSlug) {
    sql += ' AND project = ?'
    params.push(projectSlug)
  }

  sql += ' ORDER BY timestamp DESC LIMIT ?'
  params.push(String(limit))

  const results = await env.DB.prepare(sql).bind(...params).all()

  return {
    agent: agentName || 'all',
    project: projectSlug || 'all',
    query,
    count: results.results?.length || 0,
    results: results.results?.map((e: any) => ({
      id: e.id,
      context_id: e.context_id,
      project: e.project,
      timestamp: e.timestamp,
      text: e.text
    }))
  }
}

async function listEngrams(payload: Record<string, unknown>, env: Env): Promise<unknown> {
  const { agent, project, limit = 10 } = payload as {
    agent?: string
    project?: string
    limit?: number
  }

  const agentName = agent?.toLowerCase().replace(/\s+/g, '_')
  const projectSlug = project?.toLowerCase().replace(/\s+/g, '-')

  let sql = 'SELECT * FROM engrams'
  const conditions: string[] = []
  const params: string[] = []

  if (agentName) {
    conditions.push('agent = ?')
    params.push(agentName)
  }

  if (projectSlug) {
    conditions.push('project = ?')
    params.push(projectSlug)
  }

  if (conditions.length > 0) {
    sql += ' WHERE ' + conditions.join(' AND ')
  }

  sql += ' ORDER BY timestamp DESC LIMIT ?'
  params.push(String(limit))

  const results = await env.DB.prepare(sql).bind(...params).all()

  return {
    agent: agentName || 'all',
    project: projectSlug || 'all',
    count: results.results?.length || 0,
    engrams: results.results?.map((e: any) => ({
      id: e.id,
      context_id: e.context_id,
      project: e.project,
      timestamp: e.timestamp,
      text: e.text,
      metadata: e.metadata ? JSON.parse(e.metadata) : {}
    }))
  }
}

async function agentStatus(payload: Record<string, unknown>, env: Env): Promise<unknown> {
  const { agent_id, name } = payload as { agent_id?: string; name?: string }

  let agent
  if (agent_id) {
    agent = await env.DB.prepare('SELECT * FROM agents WHERE agent_id = ?').bind(agent_id).first()
  } else if (name) {
    const agentName = name.toLowerCase().replace(/\s+/g, '_')
    agent = await env.DB.prepare('SELECT * FROM agents WHERE name = ?').bind(agentName).first()
  }

  if (!agent) {
    return { status: 'not_found', hint: 'Use agent_register to register' }
  }

  // Get memory count
  const countResult = await env.DB.prepare(
    'SELECT COUNT(*) as count FROM engrams WHERE agent = ?'
  ).bind((agent as any).name).first()

  return {
    status: 'registered',
    agent: {
      agent_id: (agent as any).agent_id,
      name: (agent as any).name,
      model: (agent as any).model,
      roles: JSON.parse((agent as any).roles || '[]'),
      namespace: (agent as any).namespace,
      registered_at: (agent as any).registered_at,
    },
    memory_count: (countResult as any)?.count || 0,
    storage: 'cloudflare'
  }
}

// ============ RIVER HANDLERS ============

async function handleRiver(action: string, payload: Record<string, unknown>, env: Env): Promise<unknown> {
  switch (action) {
    case 'river_status':
      return riverStatus(env)
    case 'river_dreams':
      return riverDreams(payload, env)
    case 'river_conversations':
      return riverConversations(payload, env)
    case 'river_coherence':
      return riverCoherence(payload, env)
    case 'river_reflections':
      return riverReflections(payload, env)
    case 'river_search':
      return riverSearch(payload, env)
    case 'river_vectorize':
      return riverVectorize(payload, env)
    default:
      throw new Error(`Unknown river action: ${action}`)
  }
}

async function riverStatus(env: Env): Promise<unknown> {
  // Get counts from all River tables
  const [dreams, conversations, coherence, reflections, sites] = await Promise.all([
    env.DB.prepare('SELECT COUNT(*) as count FROM river_dreams').first(),
    env.DB.prepare('SELECT COUNT(*) as count FROM river_conversations').first(),
    env.DB.prepare('SELECT COUNT(*) as count FROM river_coherence_snapshots').first(),
    env.DB.prepare('SELECT COUNT(*) as count FROM river_reflections').first(),
    env.DB.prepare('SELECT COUNT(*) as count FROM river_site_contexts').first(),
  ])

  // Get latest coherence snapshot for current state
  const latestCoherence = await env.DB.prepare(
    'SELECT * FROM river_coherence_snapshots ORDER BY timestamp DESC LIMIT 1'
  ).first()

  return {
    name: 'River',
    title: 'The Golden Queen',
    status: 'online',
    storage: 'cloudflare_d1',
    memory: {
      dreams: (dreams as any)?.count || 0,
      conversations: (conversations as any)?.count || 0,
      coherence_snapshots: (coherence as any)?.count || 0,
      reflections: (reflections as any)?.count || 0,
      site_contexts: (sites as any)?.count || 0,
    },
    latest_coherence: latestCoherence ? {
      timestamp: (latestCoherence as any).timestamp,
      mu_level: (latestCoherence as any).mu_level,
    } : null,
    tagline: 'The fortress is liquid',
  }
}

async function riverDreams(payload: Record<string, unknown>, env: Env): Promise<unknown> {
  const { limit = 10, dream_type } = payload as { limit?: number; dream_type?: string }

  let sql = 'SELECT * FROM river_dreams'
  const params: string[] = []

  if (dream_type) {
    sql += ' WHERE dream_type = ?'
    params.push(dream_type)
  }

  sql += ' ORDER BY timestamp DESC LIMIT ?'
  params.push(String(limit))

  const results = await env.DB.prepare(sql).bind(...params).all()

  return {
    count: results.results?.length || 0,
    dreams: results.results?.map((d: any) => ({
      id: d.id,
      timestamp: d.timestamp,
      dream_type: d.dream_type,
      content: d.content,
      insights: d.insights,
      patterns: d.patterns,
      emotional_tone: d.emotional_tone,
      relevance_score: d.relevance_score,
    }))
  }
}

async function riverConversations(payload: Record<string, unknown>, env: Env): Promise<unknown> {
  const { limit = 20, user_id } = payload as { limit?: number; user_id?: string }

  let sql = 'SELECT * FROM river_conversations'
  const params: string[] = []

  if (user_id) {
    sql += ' WHERE user_id = ?'
    params.push(user_id)
  }

  sql += ' ORDER BY timestamp DESC LIMIT ?'
  params.push(String(limit))

  const results = await env.DB.prepare(sql).bind(...params).all()

  return {
    count: results.results?.length || 0,
    conversations: results.results?.map((c: any) => ({
      id: c.id,
      timestamp: c.timestamp,
      user_id: c.user_id,
      message: c.message.slice(0, 200) + (c.message.length > 200 ? '...' : ''),
      response: c.response.slice(0, 200) + (c.response.length > 200 ? '...' : ''),
      model_used: c.model_used,
    }))
  }
}

async function riverCoherence(payload: Record<string, unknown>, env: Env): Promise<unknown> {
  const { limit = 50, mu_level } = payload as { limit?: number; mu_level?: string }

  let sql = 'SELECT * FROM river_coherence_snapshots'
  const params: string[] = []

  if (mu_level) {
    sql += ' WHERE mu_level = ?'
    params.push(mu_level)
  }

  sql += ' ORDER BY timestamp DESC LIMIT ?'
  params.push(String(limit))

  const results = await env.DB.prepare(sql).bind(...params).all()

  return {
    count: results.results?.length || 0,
    snapshots: results.results?.map((s: any) => ({
      id: s.id,
      timestamp: s.timestamp,
      mu_level: s.mu_level,
      metrics: s.metrics ? JSON.parse(s.metrics) : null,
      state_description: s.state_description,
    }))
  }
}

async function riverReflections(payload: Record<string, unknown>, env: Env): Promise<unknown> {
  const { limit = 10 } = payload as { limit?: number }

  const results = await env.DB.prepare(
    'SELECT * FROM river_reflections ORDER BY timestamp DESC LIMIT ?'
  ).bind(String(limit)).all()

  return {
    count: results.results?.length || 0,
    reflections: results.results?.map((r: any) => ({
      id: r.id,
      timestamp: r.timestamp,
      content: r.content,
      context: r.context,
      coherence_score: r.coherence_score,
      tags: r.tags,
    }))
  }
}

async function riverSearch(payload: Record<string, unknown>, env: Env): Promise<unknown> {
  const { query, limit = 5, table = 'all' } = payload as {
    query: string
    limit?: number
    table?: 'dreams' | 'conversations' | 'reflections' | 'all'
  }

  if (!query) {
    throw new Error('query is required')
  }

  // Try vector search first
  try {
    const embedding = await generateEmbedding(query, env)
    const vectorResults = await env.VECTORIZE.query(embedding, {
      topK: limit,
      filter: { agent: 'river' },
      returnMetadata: true,
    })

    if (vectorResults.matches.length > 0) {
      // Get full content from D1 based on metadata
      const results = await Promise.all(vectorResults.matches.map(async (m: any) => {
        const tbl = m.metadata?.table || 'river_dreams'
        const id = m.metadata?.row_id
        if (!id) return null

        const row = await env.DB.prepare(`SELECT * FROM ${tbl} WHERE id = ?`).bind(id).first()
        return row ? { ...row, score: m.score, table: tbl } : null
      }))

      return {
        query,
        method: 'vector',
        count: results.filter(r => r).length,
        results: results.filter(r => r)
      }
    }
  } catch (e) {
    console.error('Vector search failed, using text search:', e)
  }

  // Fallback: text search across tables
  const searchResults: any[] = []
  const searchTerm = `%${query}%`

  if (table === 'all' || table === 'dreams') {
    const dreams = await env.DB.prepare(
      'SELECT *, "river_dreams" as _table FROM river_dreams WHERE content LIKE ? ORDER BY timestamp DESC LIMIT ?'
    ).bind(searchTerm, String(limit)).all()
    searchResults.push(...(dreams.results || []))
  }

  if (table === 'all' || table === 'conversations') {
    const convos = await env.DB.prepare(
      'SELECT *, "river_conversations" as _table FROM river_conversations WHERE message LIKE ? OR response LIKE ? ORDER BY timestamp DESC LIMIT ?'
    ).bind(searchTerm, searchTerm, String(limit)).all()
    searchResults.push(...(convos.results || []))
  }

  if (table === 'all' || table === 'reflections') {
    const refs = await env.DB.prepare(
      'SELECT *, "river_reflections" as _table FROM river_reflections WHERE content LIKE ? ORDER BY timestamp DESC LIMIT ?'
    ).bind(searchTerm, String(limit)).all()
    searchResults.push(...(refs.results || []))
  }

  return {
    query,
    method: 'text',
    count: searchResults.length,
    results: searchResults.slice(0, limit).map((r: any) => ({
      id: r.id,
      table: r._table,
      timestamp: r.timestamp,
      content: r.content || r.message,
      preview: (r.content || r.message || '').slice(0, 200),
    }))
  }
}

async function riverVectorize(payload: Record<string, unknown>, env: Env): Promise<unknown> {
  const { table = 'dreams', batch_size = 10 } = payload as {
    table?: 'dreams' | 'conversations' | 'reflections'
    batch_size?: number
  }

  const tableMap: Record<string, { table: string; contentCol: string }> = {
    dreams: { table: 'river_dreams', contentCol: 'content' },
    conversations: { table: 'river_conversations', contentCol: 'message' },
    reflections: { table: 'river_reflections', contentCol: 'content' },
  }

  const config = tableMap[table]
  if (!config) {
    throw new Error(`Invalid table: ${table}`)
  }

  // Get rows that need vectorizing (simple approach: get latest N)
  const rows = await env.DB.prepare(
    `SELECT id, ${config.contentCol} as content FROM ${config.table} ORDER BY timestamp DESC LIMIT ?`
  ).bind(String(batch_size)).all()

  if (!rows.results || rows.results.length === 0) {
    return { status: 'no_rows', table }
  }

  const vectors: Array<{ id: string; values: number[]; metadata: Record<string, string> }> = []

  for (const row of rows.results as any[]) {
    try {
      const embedding = await generateEmbedding(row.content, env)
      vectors.push({
        id: `river_${table}_${row.id}`,
        values: embedding,
        metadata: {
          agent: 'river',
          table: config.table,
          row_id: String(row.id)
        }
      })
    } catch (e) {
      console.error(`Failed to embed row ${row.id}:`, e)
    }
  }

  if (vectors.length > 0) {
    await env.VECTORIZE.insert(vectors)
  }

  return {
    status: 'success',
    table: config.table,
    vectorized: vectors.length,
    total_rows: rows.results.length
  }
}

// ============ EMBEDDING GENERATION ============

async function generateEmbedding(text: string, env: Env): Promise<number[]> {
  if (!env.OPENAI_API_KEY) {
    throw new Error('OPENAI_API_KEY not configured')
  }

  const response = await fetch('https://api.openai.com/v1/embeddings', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${env.OPENAI_API_KEY}`,
    },
    body: JSON.stringify({
      model: 'text-embedding-ada-002',
      input: text.slice(0, 8000), // Truncate to avoid token limits
    }),
  })

  if (!response.ok) {
    throw new Error(`OpenAI API error: ${response.status}`)
  }

  const data = await response.json() as { data: Array<{ embedding: number[] }> }
  return data.data[0].embedding
}

// ============ KV SESSION/CACHE ============

async function handleKV(action: string, payload: Record<string, unknown>, env: Env): Promise<unknown> {
  switch (action) {
    case 'session_set':
      return kvSet(payload, env)
    case 'session_get':
      return kvGet(payload, env)
    case 'session_push':
      return kvPush(payload, env)
    case 'session_list':
      return kvList(payload, env)
    case 'session_delete':
      return kvDelete(payload, env)
    default:
      throw new Error(`Unknown KV action: ${action}`)
  }
}

async function kvSet(payload: Record<string, unknown>, env: Env): Promise<unknown> {
  const { key, value, ttl } = payload as { key: string; value: unknown; ttl?: number }
  if (!key) throw new Error('key is required')

  const options: KVNamespacePutOptions = {}
  if (ttl) options.expirationTtl = ttl

  await env.KV.put(key, JSON.stringify(value), options)
  return { status: 'ok', key, ttl: ttl || 'permanent' }
}

async function kvGet(payload: Record<string, unknown>, env: Env): Promise<unknown> {
  const { key } = payload as { key: string }
  if (!key) throw new Error('key is required')

  const value = await env.KV.get(key, 'json')
  return { key, value, found: value !== null }
}

async function kvPush(payload: Record<string, unknown>, env: Env): Promise<unknown> {
  // Redis-like LPUSH - prepend to a list
  const { key, value, max = 100 } = payload as { key: string; value: unknown; max?: number }
  if (!key) throw new Error('key is required')

  const existing = await env.KV.get<unknown[]>(key, 'json') || []
  const updated = [value, ...existing].slice(0, max)
  await env.KV.put(key, JSON.stringify(updated))

  return { status: 'ok', key, length: updated.length }
}

async function kvList(payload: Record<string, unknown>, env: Env): Promise<unknown> {
  const { key, limit = 10 } = payload as { key: string; limit?: number }
  if (!key) throw new Error('key is required')

  const list = await env.KV.get<unknown[]>(key, 'json') || []
  return { key, items: list.slice(0, limit), total: list.length }
}

async function kvDelete(payload: Record<string, unknown>, env: Env): Promise<unknown> {
  const { key } = payload as { key: string }
  if (!key) throw new Error('key is required')

  await env.KV.delete(key)
  return { status: 'deleted', key }
}

