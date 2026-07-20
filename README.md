# Chat inference logging backend

Python 3.12 / FastAPI service that stores conversations and append-heavy LLM
inference logs in one PostgreSQL database. Redis Streams sits in front of the
log-write path only. Caddy is the optional single public entrypoint when using
the production Compose file.

## Architecture overview

### Public entrypoint (Caddy)

With `docker-compose.prod.yml`, Caddy listens on port 80 and is the only
service published to the host. Routing from `Caddyfile`:

* `/chat*` → `frontend:3000` (Next.js `basePath` is `/chat`)
* everything else → `api:8000` (including `/docs`, `/health`, `/logs/ingest`)

One open port simplifies the host firewall and leaves a single place to
terminate TLS later if HTTPS is added. Root is the API; the UI is not at `/`.

### Compose services (production: `docker-compose.prod.yml`)

| Service | Purpose |
|---------|---------|
| `db` | PostgreSQL 17; chat + analytics schemas |
| `redis` | Redis Streams buffer (`inference_logs` + DLQ); AOF enabled in prod |
| `api` | FastAPI: migrations on start, ingest enqueue, chat + stats APIs |
| `consumer` | Independent stream consumer: redact → Postgres → XACK |
| `frontend` | Next.js observability console (`basePath=/chat`) |
| `caddy` | Reverse proxy; only published port is `80` |

Local `docker-compose.yml` is the same stack without Caddy: API on 8000,
frontend on 3000, and Postgres/Redis published for host tooling.

## Architecture Notes

### 1. Ingestion flow

End-to-end path as implemented:

```text
SDK instrumentation wrapper
  → captures metadata (model, provider, latency, tokens, status, previews, …)
  → background worker: client-side Presidio redact on previews
  → HTTP POST /logs/ingest
  → Pydantic validate (InferenceLogCreate) at the API edge
  → Redis XADD  inference_logs
       │
       │  [decoupled: producer returns 202 here]
       ▼
  consumer group (backend/consumer.py, separate Compose service)
  → XREADGROUP
  → Presidio redact() again (server-side backstop)
  → write analytics.inference_logs (Postgres)
  → XACK only after a successful commit
```

Responsibility split:

* **Validation** lives at the API edge (`POST /logs/ingest`). The consumer
  re-parses the stream JSON into the same schema but does not own request
  validation for callers.
* **Redaction:** SDK redacts on the delivery worker before the HTTP call
  (first pass, off the chat hot path). The consumer redacts again before
  Postgres (second pass / backstop). The ingest endpoint itself does **not**
  redact; it only validates and enqueues.
* The conversation API publishes via the same in-process `publish_inference_log`
  (validate + XADD), not by calling HTTP against itself. It does not run the
  SDK-side first pass; the consumer backstop covers that path.

### 2. Logging strategy

**What gets logged** into `analytics.inference_logs`:

* `model`, `provider`
* `latency_ms`, `input_tokens`, `output_tokens`
* `timestamp` (stored as `created_at`)
* `status` (`success` | `error`) and `error_message` when status is error
* `conversation_id` (session join key)
* `input_preview`, `output_preview` (truncated free-text snippets)

**Delivery (fire-and-forget).** The SDK never blocks the user-facing LLM call
on logging:

* Instrumentation builds a payload and `submit()`s it to a bounded in-memory
  queue (`put_nowait`); a full queue drops the event.
* A daemon worker redacts, then POSTs with a short timeout and **one** retry
  after a short backoff. Further failure is logged at debug and dropped.
* Exceptions inside `_finish_call` are swallowed so instrumentation cannot
  alter the wrapped call's return or exception.

Known gap on the SDK side: the queue is memory-only and the worker is a
daemon thread, so an abrupt process exit can lose events that were not yet
POSTed. Short-lived scripts may call `flush()` / `close()`; request handlers
should not.

The HTTP ingest endpoint matches that posture: Redis failures return `202`
with a `warning` field (see Failure handling). The chat API's in-process
publish catches `StreamPublishError` and continues the chat turn.

**Deliberately not logged in full on the inference path.**
`inference_logs` stores previews only (API max 8000 chars; SDK default
`LLM_LOG_PREVIEW_CHARS=1000` after redaction), not the full multi-turn
transcript. Full message bodies live in `chat.messages` on the OLTP chat
path. Previews keep analytics volume and PII exposure down while still
supporting debugging of a single call.

### 3. Scaling considerations

**Likely first bottlenecks under real load (honest):**

* **Single consumer process.** Compose runs one `consumer` with
  `CHATLOG_REDIS_CONSUMER_NAME=consumer-1`. No horizontal fan-out is wired
  today. Adding instances is a **config/ops** change (more processes, distinct
  names, same group `inference_logs_writers`), not a redesign: Redis consumer
  groups already track pending vs acknowledged work. Presidio on every
  message also costs CPU/RAM per consumer.
* **One Postgres for OLTP chat + append-heavy logs.** Fine at demo volume;
  analytics scans and chat writes will contend first as event rate grows.
* **Local Redis has no AOF/RDB** (`docker-compose.yml`). A Redis restart can
  drop the in-memory stream. Prod Compose enables AOF; that helps durability
  but is not the same as Kafka-scale retention or partitioning.

**What scales by config vs what needs a redesign:**

| Already scalable-by-config | Needs a real redesign at 10x/100x |
|----------------------------|-----------------------------------|
| More consumer replicas in the same group | True stream partitioning / Kafka (or similar) for retention + multi-group fan-out |
| Separate `chat` / `analytics` schemas (extraction-ready) | Move `inference_logs` to a dedicated OLAP store (e.g. ClickHouse) and keep Postgres for chat |
| Prod Redis AOF | Longer retention, replay, and independent alerting consumers off the same topic |

At higher volume: partition the log stream or move to Kafka; keep chat OLTP
in Postgres; batch analytics into an OLAP sink; run N consumers in the same
group with lag / DLQ-depth monitoring.

### 4. Failure handling assumptions

**Redis down when a log is produced.** Implemented, does not crash the chat
path:

* `POST /logs/ingest` catches `StreamPublishError` and still returns `202`
  with `warning="redis_unavailable: …"` and no `stream_id`.
* Conversation handlers catch the same error, log it, and finish the chat
  turn without failing the request.
* SDK: enqueue/POST failures never propagate to the wrapped LLM call (drop
  after one retry, or drop if the local queue is full).

**Postgres write fails inside the consumer.** Implemented: `XACK` runs only
after a successful `ingest()` / commit. On exception the entry stays
unacked so `XAUTOCLAIM` can retry. Covered by unit tests
(`test_failed_write_below_max_attempts_leaves_unacked`).

**After N failed attempts.** Implemented: default
`CHATLOG_REDIS_MAX_DELIVERY_ATTEMPTS=5`. Entry is `XADD`ed to
`inference_logs_dlq` (with `source_id` / `delivery_count`), then `XACK`ed on
the main stream so it stops retrying. No alerting on DLQ depth yet (error
log only).

**Consumer process dies.** By design of Redis consumer groups: never-read
entries stay on the stream; in-flight unacked entries stay pending. On
restart, `XREADGROUP` takes new work and `XAUTOCLAIM` reclaims idle pending
ones. Unit tests lock the no-ACK-on-failure path. A full process-kill /
restart soak is not automated in CI; Compose runs the consumer as its own
service so that backlog-and-drain behavior is easy to exercise manually
(`XLEN` / `XRANGE` while the consumer is stopped).

## Quick start (local)

```bash
docker compose up --build
```

API: <http://localhost:8000> · UI: <http://localhost:3000/chat> · Docs:
<http://localhost:8000/docs>

```bash
docker compose exec api uv run python backend/scripts/seed.py
```

Without Compose (Postgres + Redis already running):

```bash
uv sync
uv run alembic upgrade head
uv run python backend/scripts/seed.py
# Terminal A
uv run uvicorn chatlog.main:app --reload
# Terminal B
uv run python backend/consumer.py
```

Defaults: `postgresql+asyncpg://chatlog:chatlog@localhost:5432/chatlog` and
`redis://localhost:6379/0`. Override with `CHATLOG_DATABASE_URL` /
`CHATLOG_REDIS_URL`.

```bash
uv run pytest
uv run ruff check .
```

## Schema design

PostgreSQL holds both workloads:

* `chat.*` for conversations and messages (transactional)
* `analytics.inference_logs` for append-heavy inference telemetry

One database keeps deploy, migrations, backup, and local setup simple.
Separate schemas keep ownership clear if analytics is extracted later.

Redis Streams does **not** replace Postgres as the store. It only buffers the
log-write path so producers can return quickly and the consumer can redact and
persist asynchronously.

`conversation_id` is the join key across both schemas.

```text
chat.conversations
  id UUID PK
  created_at TIMESTAMPTZ
  status active|cancelled
  title nullable
  provider nullable
  model nullable
       1
       |
       +----< chat.messages
       |       id UUID PK
       |       conversation_id UUID FK
       |       role user|assistant
       |       content TEXT
       |       created_at TIMESTAMPTZ
       |       sequence_number INT
       |       UNIQUE(conversation_id, sequence_number)
       |
       +----< analytics.inference_logs
               id UUID PK
               model, provider
               conversation_id UUID FK
               latency_ms, input_tokens, output_tokens
               status success|error
               error_message nullable
               input_preview, output_preview
               created_at TIMESTAMPTZ
```

Secondary indexes on inference logs (only these two):

* `(conversation_id, created_at)` for per-conversation timelines
* `(model, created_at)` for model-focused time windows

Provider is grouped in `/stats` but not indexed without a demonstrated need;
extra indexes cost write throughput on an append-heavy path.

## PII redaction (Presidio)

Defense in depth: two Presidio passes, not one choke point.

1. **SDK (first pass).** The instrumentation wrapper queues raw previews; the
   background delivery worker runs `redact()` on `input_preview` /
   `output_preview` before `POST /logs/ingest`. Redaction is never on the
   synchronous chat path.
2. **Backend consumer (second pass).** The stream consumer runs `redact()` again
   before Postgres write. If this pass changes a preview, a debug log notes the
   caller may have bypassed the SDK (direct API use, or a future adapter).
   `POST /logs/ingest` itself does not redact; it only validates and enqueues.

Shared implementation: `shared/src/chatlog_pii/pii_redactor.py`, re-exported
from the SDK and backend. Entity set: `PERSON`, `EMAIL_ADDRESS`,
`PHONE_NUMBER`, `US_SSN`, `LOCATION`, `DATE_TIME` (DOB-like spans only),
`MEDICAL_LICENSE`. Matches become typed placeholders (`<PERSON>`, etc.).
`AnalyzerEngine` is built once at import.

This take-home also redacts stored `chat.messages.content`. That is fine for
demoing the shared function end to end; it is not the production
recommendation for primary chat history (placeholders break faithful resume /
re-render). Prefer encrypting history at rest and redacting derived surfaces.

**Observed limitation (found in testing):** `PERSON` detection cannot tell real
user PII from fictional names in generative output. Asking for a short story
with character names produced `<PERSON>` placeholders for those characters.
That is a real false positive from NER on narrative text, not a hypothetical
caveat.

spaCy model `en_core_web_sm` is locked in `uv.lock` and installed by `uv sync`
/ the Dockerfile.

## API

* `POST /logs/ingest` validates, `XADD`s to `inference_logs`, returns `202`
  with the stream entry ID. Does not write Postgres or redact. If Redis is
  down, still returns `202` with a `warning` field (fire-and-forget; chat path
  unaffected).
* `POST /conversations` creates a conversation and appends an assistant reply.
* `GET /providers` lists providers, default models, and whether a browser key
  is required.
* `GET /conversations?status=active|cancelled` lists conversations.
* `GET /conversations/{id}` returns messages ordered by sequence number.
* `POST /conversations/{id}/messages` appends a user turn and assistant reply.
* `POST /conversations/{id}/cancel` idempotently marks cancelled.
* `GET /stats?window_hours=24` average latency, error rate, tokens by model /
  provider.
* `GET /dashboard?window_minutes=60` summary totals, grouped metrics, latency
  series, recent log rows.

Malformed bodies get structured `422`. Unknown `conversation_id` is not
rejected at ingest (producer never talks to Postgres); a bad FK fails in the
consumer and, after enough retries, lands on the DLQ.

## Python instrumentation SDK

Package: `sdk/src/chatlog_sdk`. Install with `uv sync` (or
`uv pip install -e .`), then:

```bash
export LLM_LOG_BACKEND_URL=http://localhost:8000
# Optional:
export LLM_LOG_API_KEY=secret
export LLM_LOG_TIMEOUT_SECONDS=1.0
export LLM_LOG_PREVIEW_CHARS=1000
export LLM_LOG_QUEUE_CAPACITY=1000
```

```python
from chatlog_sdk import conversation_context, instrument

@instrument(provider="openai")
async def complete(prompt: str):
    return await openai_client.responses.create(
        model="gpt-4.1-mini",
        input=prompt,
    )

with conversation_context(conversation_id):
    response = await complete("Summarize this")
```

Inline form:

```python
from chatlog_sdk import instrumented_call

with instrumented_call(
    provider="anthropic",
    model="claude-sonnet-4",
    input_data=prompt,
) as call:
    response = call.set_response(anthropic_client.messages.create(...))
```

Both preserve the wrapped return value. Provider exceptions are re-raised after
an error event is queued. OpenAI and Anthropic adapters normalize text, model,
and tokens; other providers implement `ProviderAdapter`.

### Delivery behavior

See **Architecture Notes → Logging strategy**. Short version: bounded queue +
daemon worker, one retry, then drop; never blocks or fails the LLM call.

```bash
uv run python sdk/examples/mock_chatbot.py
```

## Frontend observability console

`frontend/` with Next.js `basePath: "/chat"`.

```bash
docker compose up --build
# or, API already up:
cd frontend && cp .env.example .env.local && npm install && npm run dev
```

Local UI: <http://localhost:3000/chat>. `NEXT_PUBLIC_API_URL` defaults to
`http://localhost:8000`. With the prod Compose file it is set from
`PUBLIC_HOST` at image build time.

Providers:

| Provider | Auth |
|----------|------|
| Groq | Server key via `CHATLOG_GROQ_API_KEY` |
| OpenAI | Browser-supplied API key (memory only) |
| Gemini | Browser-supplied API key (memory only) |

Client keys are sent for proxying and never written to Postgres. All console
data is live backend fetch; no fixtures. `sdk/examples/mock_chatbot.py` is SDK
demo only.

### Frontend design rationale

Dense three-pane console: session index, message trace, runtime health.
Cool blue-slate; semantic color for selection / healthy / slow / error.
Monospace for IDs, timestamps, models, tokens, latency. Signature moment: the
inline inference trace lane while a provider request is running. Motion
respects `prefers-reduced-motion`. Narrow screens use tabs + a drawer.

## What I'd improve with more time

Concrete follow-ups given what was built and what was skipped:

* Kafka (or equivalent) instead of Redis Streams once volume needs
  partitioning, longer retention, and multi-consumer-group fan-out.
* A real domain plus HTTPS via Caddy automatic TLS.
* Horizontal scaling of consumers (multiple names in the same group) with lag
  and DLQ-depth monitoring / alerting.
* Context-aware PII filtering so fictional character names in generative output
  are not treated as `PERSON` (observed false positive in testing).
* Kubernetes manifests validated against a real cluster. None ship in this
  repo today; Compose was the time-boxed deploy path.
* Authn for callers, rate limits, tenant isolation, idempotency keys on ingest.
* Time partitioning / retention on `analytics.inference_logs`, and optionally
  batching into ClickHouse for long-window analytics.
* Measured PII false-positive / false-negative rates; hybrid regex + NER if
  Presidio latency becomes the bottleneck.
* SDK: batch upload, drop metrics, optional disk buffer for crash recovery.
