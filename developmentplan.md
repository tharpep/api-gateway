# Development Plan v3 — Personal AI Ecosystem

**Repos:** `api-gateway` · `MY-AI` · `agent` (new) · `local-mcp` (new)
**Date:** February 2026
**Timeline:** No deadline — personal project, prioritize quality over speed
**Goal:** Voice-driven AI agent ("Jarvis") backed by a knowledge base and real-world integrations, accessible from any AI interface.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                                                                    │
│   Claude Desktop ─── local-mcp ───► agent ───┐                   │
│   Agent Frontend ─── REST ───────► agent ───┤                   │
│   Voice (future) ─── REST ───────► agent ───┤                   │
│                                               ▼                   │
│   ┌────────────────────────────────────────────────────────┐     │
│   │  api-gateway (GCP Cloud Run)                            │     │
│   │                                                         │     │
│   │  Integrations:                                          │     │
│   │    /calendar   — Google Calendar (CRUD)                 │     │
│   │    /email      — Gmail (read, send, reply, draft)       │     │
│   │    /tasks      — Google Tasks (CRUD)                    │     │
│   │    /notify     — Pushover                               │     │
│   │    /storage    — Google Drive                           │     │
│   │    /context    — aggregated snapshot                    │     │
│   │                                                         │     │
│   │  Knowledge Base (proxied):                              │     │
│   │    /kb/search  — proxied to MY-AI                       │     │
│   │    /kb/ingest  — proxied to MY-AI                       │     │
│   │    /kb/sources — proxied to MY-AI                       │     │
│   │    /kb/sync    — proxied to MY-AI                       │     │
│   │                                                         │     │
│   │  Single URL, single auth for all consumers.             │     │
│   └─────────────────────┬──────────────────────────────────┘     │
│                          │ internal REST                          │
│                          ▼                                        │
│   ┌────────────────────────────────────────────────────────┐     │
│   │  MY-AI (GCP Cloud Run) — KB service                     │     │
│   │                                                         │     │
│   │  Sources: Google Drive folders (primary)                │     │
│   │  Loaders: md, txt, pdf, docx, xlsx, code                │     │
│   │  Embeddings: Voyage AI API (or OpenAI)                  │     │
│   │  Store: PostgreSQL + pgvector (GCP Cloud SQL)           │     │
│   │  Retrieval: composable pipeline                         │     │
│   │  REST API: /kb/search, /kb/ingest, /kb/sources, /kb/sync│     │
│   │  OpenAI-compatible proxy: /v1/chat/completions          │     │
│   └────────────────────────────────────────────────────────┘     │
│                                                                    │
│   ┌────────────────────────────────────────────────────────┐     │
│   │  agent (GCP Cloud Run) — orchestrator                   │     │
│   │                                                         │     │
│   │  Agent loop: message → LLM (tool calling) → execute     │     │
│   │              → LLM synthesize → respond                 │     │
│   │  Tools: all gateway endpoints (one base URL)            │     │
│   │  Conversation memory + context management               │     │
│   │  REST API: /chat (text now, /voice later)               │     │
│   │  Model routing: Haiku (simple) / Sonnet (complex)       │     │
│   └────────────────────────────────────────────────────────┘     │
│                                                                    │
│   ┌────────────────────────────────────────────────────────┐     │
│   │  local-mcp (runs on your machine)                       │     │
│   │                                                         │     │
│   │  FastMCP + stdio transport                              │     │
│   │  Routes through agent (reasoning layer), not raw gateway│     │
│   │  Built AFTER agent exists                               │     │
│   └────────────────────────────────────────────────────────┘     │
│                                                                    │
└──────────────────────────────────────────────────────────────────┘
```

**Key architectural decisions:**

1. **api-gateway is the single API surface.** Every consumer talks to one URL
   with one API key. KB is proxied through it, same as calendar/email/tasks.
   Adding new capabilities = adding gateway endpoints, all consumers get them.

2. **local-mcp routes through the agent, not raw gateway.** Claude Desktop
   gets the benefit of the agent's reasoning layer, multi-step execution,
   and conversation context. The MCP is just a thin bridge. Built after
   the agent exists.

3. **Google Drive is the source of truth for files.** No separate file
   storage. Documents live in Drive folders organized by category. Vectors
   are derived data, fully rebuildable from sources.

4. **PostgreSQL + pgvector is the single data store.** Vectors, metadata,
   sync state — all in one database. No Qdrant, no GCS buckets. Permanent,
   reliable, no inactivity deletions.

5. **API-based embeddings in prod.** Voyage AI or OpenAI — pennies at
   personal scale. Eliminates the Ollama-in-production infrastructure problem.

---

## Repo Responsibilities

| Repo | Purpose | Deployment |
|------|---------|------------|
| `api-gateway` | Integrations + KB proxy. Single API surface. | GCP Cloud Run (existing) |
| `MY-AI` | Knowledge base service. Ingestion, retrieval, embeddings. | GCP Cloud Run (new) |
| `agent` | AI orchestrator. Agent loop, tool calling, conversation. | GCP Cloud Run (new) |
| `local-mcp` | Claude Desktop → agent bridge. Thin MCP wrapper. | Local (your machine) |

---

## Storage Architecture

### Source Files (pre-embedded)

Google Drive folders, organized by category:

```
📁 Knowledge Base/
├── 📁 school/
├── 📁 work/
├── 📁 personal/
├── 📁 medical/
├── 📁 projects/
└── 📁 reference/
```

Categories become metadata on vectors, not separate collections.
Add/rename folders as needed — ingestion pipeline derives category from path.

### Vectors + Metadata (embedded)

PostgreSQL with pgvector on GCP Cloud SQL. Two core tables:

```sql
-- Chunks with vector embeddings
CREATE TABLE kb_chunks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content         TEXT NOT NULL,
    embedding       VECTOR(1024),
    source_file_id  TEXT NOT NULL,
    source_category TEXT NOT NULL,       -- derived from Drive folder
    source_filename TEXT,
    file_type       TEXT,
    chunk_index     INT,
    metadata        JSONB,               -- page number, sheet name, etc.
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX ON kb_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ON kb_chunks (source_category);
CREATE INDEX ON kb_chunks (source_file_id);

-- Sync tracking
CREATE TABLE kb_sources (
    file_id         TEXT PRIMARY KEY,    -- Google Drive file ID
    filename        TEXT,
    category        TEXT,
    file_hash       TEXT,                -- content hash for change detection
    last_modified   TIMESTAMPTZ,
    last_synced     TIMESTAMPTZ,
    chunk_count     INT DEFAULT 0,
    status          TEXT DEFAULT 'active' -- active, deleted, error
);
```

### Sync Flow

```
Manual trigger (or future cron/webhook)
    ↓
MY-AI calls gateway /storage to list Drive folder contents
    ↓
Diff against kb_sources table (new / changed / deleted files)
    ↓
Fetch changed files via gateway /storage
    ↓
Load → chunk (file-type-aware) → embed (Voyage/OpenAI API) → INSERT into kb_chunks
    ↓
Update kb_sources with new hash + timestamp
    ↓
DELETE chunks for removed files
```

**Future automation:** Google Drive webhooks (changes.watch API) can push
notifications to a gateway endpoint, which triggers MY-AI sync. Channels
expire every ~24 hours and need auto-renewal. Not needed now — manual
trigger and/or scheduled polling is the starting point.

### Query Flow

```sql
-- Unfiltered search (all categories)
SELECT id, content, source_category, source_filename, metadata,
       1 - (embedding <=> $query_vector) AS similarity
FROM kb_chunks
ORDER BY embedding <=> $query_vector
LIMIT 10;

-- Filtered search (specific categories)
SELECT id, content, source_category, source_filename, metadata,
       1 - (embedding <=> $query_vector) AS similarity
FROM kb_chunks
WHERE source_category IN ('work', 'projects')
ORDER BY embedding <=> $query_vector
LIMIT 10;
```

---

## Phase 1 — Gateway Write Operations

Add mutation endpoints to existing routers. Read side, OAuth, router
structure all exist — this is adding POST/PATCH/DELETE handlers.

**Calendar:**
- `POST   /calendar/events`         — create event
- `PATCH  /calendar/events/{id}`    — update event
- `DELETE /calendar/events/{id}`    — cancel event

**Email:**
- `POST   /email/send`              — send email
- `POST   /email/reply/{id}`        — reply to thread
- `POST   /email/draft`             — save draft

**Tasks:**
- `POST   /tasks`                   — create task
- `PATCH  /tasks/{id}`              — update task
- `DELETE /tasks/{id}`              — delete task

**Deliverables:**
- [ ] Calendar write endpoints + tests
- [ ] Email send/reply/draft endpoints + tests
- [ ] Task CRUD endpoints + tests
- [ ] Updated GatewayClient in automations

---

## Phase 2 — MY-AI: Pure Knowledge Base Service

Strip MY-AI down to *only* knowledge base concerns. Everything else goes.

### 2A. Cleanup — Remove Dead Weight

**Remove:**
- Gateway proxy logic (MY-AI calls embedding API + Anthropic directly)
- Purdue GenAI provider
- Tuning module (`/tuning`)
- Empty connector stubs
- Local-first hardware detection config
- Qdrant integration (replaced by pgvector)

**Archive to branch (revived in agent repo):**
- Agent framework (`agents/` — ToolRouter, ToolRegistry, BaseTool)

**Simplify:**
- LLM provider config → Anthropic for generation (Haiku/Sonnet),
  Voyage AI or OpenAI for embeddings, OpenRouter as fallback
- Config → cloud-first, no hardware-aware logic

### 2B. KB Ingestion Pipeline

**Source abstraction:**
```
kb/sources/
  base.py          — BaseSource interface (discover, fetch, get_state)
  google_drive.py  — Google Drive via api-gateway /storage endpoints
  manual.py        — direct file upload via API
```

Google Drive source:
- Configured with folder ID(s) from the Knowledge Base/ Drive folder
- Calls gateway /storage to list files and fetch content
- Derives `source_category` from subfolder name
- Tracks state via kb_sources table for incremental sync

**Loader abstraction:**
```
kb/loaders/
  base.py       — BaseLoader interface
  markdown.py   — .md files
  text.py       — .txt files
  pdf.py        — .pdf (pymupdf or pdfplumber)
  docx.py       — .docx (python-docx)
  xlsx.py       — .xlsx (openpyxl) — row/sheet-aware chunking
  code.py       — source code files — function/class-aware chunking
```

Each loader returns a standardized Document:
```python
@dataclass
class Document:
    content: str
    metadata: dict  # source, filename, file_type, category, modified, etc.
    chunks: list[Chunk] | None = None
```

**Chunking:** file-type-aware, not one-size-fits-all.
- Prose (md, txt, docx, pdf): semantic chunking with paragraph/section boundaries
- Spreadsheets (xlsx): row-based or sheet-based chunks with column headers preserved
- Code: function/class-level chunks with file path + language metadata
- Configurable chunk size + overlap per loader type

**Embedding:** Voyage AI API (`voyage-3.5` at $0.06/M tokens, or
`voyage-3.5-lite` at $0.02/M tokens). 200M tokens free to start.
Fallback: OpenAI `text-embedding-3-small` at $0.02/M tokens.

### 2C. KB Retrieval Pipeline

Composable pipeline wrapping existing working components:

```python
pipeline = RetrievalPipeline([
    QueryExpand(model="haiku", enabled=config.query_expansion),
    VectorRetrieve(db=pgvector_pool, top_k=20, category_filter=None),
    Rerank(model="cross-encoder", top_k=5, enabled=config.rerank),
    Synthesize(model="sonnet", enabled=config.synthesize),
])

result = await pipeline.run(query="how do we handle auth refresh?")
```

Each stage: `async def run(self, ctx: PipelineContext) -> PipelineContext`.
Stages toggleable, swappable, configurable independently.

**Default pipelines:**
- `search` — expand → retrieve → rerank (returns chunks with scores)
- `answer` — expand → retrieve → rerank → synthesize (returns generated answer)
- `raw` — retrieve only (fastest, cheapest)

### 2D. KB API Surface

- `POST  /kb/search`     — query the knowledge base (pipeline, filters, top_k)
- `POST  /kb/ingest`     — ingest file(s) or trigger folder sync
- `GET   /kb/sources`    — list configured sources + sync status
- `POST  /kb/sync`       — trigger sync for a source
- `GET   /kb/stats`      — chunk count, category breakdown, last sync times
- `GET   /v1/chat/completions` — OpenAI-compatible proxy with RAG injection

### 2E. Gateway KB Proxy

Add `/kb/*` routes to api-gateway that proxy to MY-AI:
- Same API key auth as everything else
- All consumers access KB through the single gateway URL

### 2F. Infrastructure

- Cloud SQL PostgreSQL instance with pgvector extension
- MY-AI deployed to Cloud Run
- MY-AI connects to Cloud SQL via private IP or Cloud SQL Auth Proxy

### 2G. Deliverables

- [ ] Dead code removed, agent framework archived to branch
- [ ] Cloud SQL instance provisioned with pgvector
- [ ] kb_chunks + kb_sources tables created
- [ ] Google Drive source implementation (via gateway /storage)
- [ ] Loaders for md, txt, pdf, docx, xlsx, code
- [ ] File-type-aware chunking
- [ ] Sync engine (manual trigger, incremental via hash diff)
- [ ] Voyage AI / OpenAI embedding integration
- [ ] Composable retrieval pipeline wrapping existing components
- [ ] KB REST API on MY-AI (/kb/*)
- [ ] /kb/* proxy routes on api-gateway
- [ ] MY-AI deployed to Cloud Run
- [ ] End-to-end: drop file in Drive → sync → search returns results

---

## Phase 3 — Agent (new repo, text-only)

Everything is wired through the gateway. The agent just needs one URL.

### 3A. Core Agent Loop

```
User message
    ↓
LLM with tool schemas (Anthropic tool_use)
    ↓
Tool calls → httpx to gateway REST endpoints
    ↓
Tool results back to LLM
    ↓
LLM synthesizes response (or calls more tools, max 3-5 steps)
    ↓
Response to user
```

### 3B. Tool Registry

- Manually defined tool schemas matching gateway endpoints
- Each tool = name, description, JSON schema, HTTP method + endpoint
- All tools hit one base URL (gateway) with one API key
- Tools include: calendar CRUD, email read/send/reply, tasks CRUD,
  notifications, context snapshot, KB search

### 3C. Conversation Management

- In-memory conversation history (start simple)
- Persist to Postgres later (same Cloud SQL instance)
- Context window management (truncation, summarization)
- System prompt with persona, available tools, user context

### 3D. Model Routing

- Haiku: simple tool selection, quick factual responses
- Sonnet: complex planning, multi-step reasoning, synthesis
- Decision heuristic: message complexity, tools needed, conversation depth

### 3E. API Surface

- `POST /chat`                     — send message, get response
- `GET  /conversations`            — list conversations
- `GET  /conversations/{id}`       — get conversation history
- `WS   /chat/stream`             — streaming (nice-to-have)

### 3F. Deliverables

- [ ] Agent loop with Anthropic tool_use
- [ ] Tool registry mapping to gateway endpoints
- [ ] Conversation history (in-memory, then Postgres)
- [ ] Model routing (Haiku/Sonnet)
- [ ] REST API (/chat)
- [ ] Deployed to Cloud Run
- [ ] End-to-end: "What's on my calendar?" → correct answer via tool call
- [ ] End-to-end: "Find my notes about X" → KB search → cited answer

---

## Phase 4 — Local MCP (after agent exists)

Now that the agent exists, the MCP is trivially simple — it routes
Claude Desktop through the agent's reasoning layer.

**Option A (recommended):** MCP tools call agent's `/chat` endpoint.
Claude Desktop messages go through the agent loop, getting reasoning,
multi-step execution, and context. Simplest implementation.

**Option B:** MCP tools call gateway directly for simple reads,
agent for complex operations. More granular but more code.

### Deliverables

- [ ] FastMCP server, stdio transport
- [ ] Routes to agent /chat (or individual gateway endpoints)
- [ ] Claude Desktop config
- [ ] End-to-end: Claude Desktop → MCP → agent → gateway → response

---

## Phase 5 — Frontend (React)

Chat UI talking to agent's /chat endpoint.
KB management UI talking to gateway's /kb/* endpoints.

- [ ] Vite + React + TypeScript
- [ ] Chat interface → agent /chat
- [ ] KB management (sources, sync status, browse indexed docs)
- [ ] Deploy to Vercel or Cloud Run

---

## Phase 6 — Voice

Add STT/TTS to the agent. The agent loop is already built.

- [ ] STT: Whisper (local via whisper.cpp) or Deepgram (cloud, low latency)
- [ ] TTS: ElevenLabs (~$5/mo, quality) or Piper (local, free)
- [ ] /voice endpoint on agent
- [ ] Android app (Pixel 4XL) — phone is just I/O, processing server-side
- [ ] Desktop app (Electron/Tauri)
- [ ] End-to-end: "What's on my calendar today?" → spoken answer

---

## Order of Operations

```
Phase 1:  Gateway write ops                    ← START HERE
Phase 2:  MY-AI → pure KB service              ← biggest chunk
          + Cloud SQL + pgvector
          + gateway /kb/* proxy
          + deploy MY-AI
Phase 3:  Agent repo (text-only)               ← the fun part
Phase 4:  Local MCP (routes through agent)     ← Claude Desktop unlocked
Phase 5:  Frontend (chat + KB management)
Phase 6:  Voice (STT/TTS layer)
```

Each phase is independently deployable and testable.
No phase depends on a later phase.

---

## Decision Log

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Vector store | PostgreSQL + pgvector (Cloud SQL) | One DB for everything. Permanent, no inactivity deletion. Performance identical to Qdrant at personal scale. |
| Embeddings | Voyage AI API (prod), Ollama optional (dev) | Pennies at personal scale. Eliminates Ollama-in-production infra problem. Voyage 200M tokens free. |
| File storage | Google Drive folders | Source of truth. No duplication. Categories via folder structure → metadata on vectors. |
| MCP routing | Through agent, not raw gateway | Claude Desktop gets reasoning layer, multi-step execution, conversation context. Built after agent. |
| KB proxy | Through api-gateway | Single URL, single auth for all consumers. MY-AI is internal backend service. |
| Agent framework | Separate repo from MY-AI | Different concerns: KB = ingestion/retrieval, Agent = orchestration/conversation. Separate deployments. |
| Primary LLM | Anthropic (Haiku + Sonnet) | Low cost tier split. Haiku for simple, Sonnet for complex. |
| Fallback LLM | OpenRouter | Model shopping, testing. |
| Sync strategy | Manual trigger first, polling later, webhooks eventual | Drive webhooks have quirks (24h expiry, empty payloads). Not worth complexity yet. |

---

## Cost Estimate (Steady State)

| Component | Cost |
|-----------|------|
| Anthropic Haiku | ~$0.25/M input — negligible |
| Anthropic Sonnet | ~$3/M input — few $/month |
| Embedding API (Voyage/OpenAI) | ~$0.02-0.06/M tokens — negligible |
| GCP Cloud SQL (PostgreSQL) | Free tier or ~$7-9/month |
| GCP Cloud Run ×3 | Free tier or minimal |
| Google Drive | Free (15GB shared) |
| TTS (if ElevenLabs) | ~$5/month |
| **Total** | **$5–25/month** |