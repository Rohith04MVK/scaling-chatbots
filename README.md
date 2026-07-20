# Chat inference logging backend

A Python 3.12/FastAPI service that stores conversations and append-heavy LLM
inference logs in one PostgreSQL database. The code keeps the write path small
and async while separating transactional and analytics data into PostgreSQL
schemas.

## Quick start

Docker is the shortest path. From the project root:

```bash
docker compose up --build
```

The stack starts PostgreSQL, Redis, the API, an independent log consumer, and
the Next.js frontend. The API waits for PostgreSQL and Redis, applies Alembic
migrations, and listens at <http://localhost:8000>. The UI is at
<http://localhost:3000/chat>. OpenAPI docs are at <http://localhost:8000/docs>. In
another terminal, add demo data:

```bash
docker compose exec api uv run python backend/scripts/seed.py
```

## Deploy on AWS (EC2)

The production Compose file runs the same stack behind Caddy on port **80**:
UI at `/chat`, API (and `/docs`, `/health`) on the same host. Postgres and Redis
stay on the Docker network only.

### 1. Launch the instance

1. AMI: **Amazon Linux 2023** (or Ubuntu 22.04+).
2. Instance: **t3.medium** or larger (spaCy/Presidio needs RAM; t3.small often OOMs).
3. Storage: **20 GB** gp3.
4. Security group inbound:
   - **22** from your IP (SSH)
   - **80** from `0.0.0.0/0` (or your IP while testing)
5. Allocate an Elastic IP if you want a stable address.

### 2. Install Docker on the instance

SSH in, then:

```bash
# Amazon Linux 2023
sudo dnf update -y
sudo dnf install -y docker git
sudo systemctl enable --now docker
sudo usermod -aG docker ec2-user
# log out and back in so the docker group applies
exit
```

```bash
# Ubuntu
sudo apt-get update -y
sudo apt-get install -y docker.io docker-compose-v2 git
sudo systemctl enable --now docker
sudo usermod -aG docker ubuntu
exit
```

Install Compose plugin if `docker compose` is missing (Amazon Linux):

```bash
sudo mkdir -p /usr/local/lib/docker/cli-plugins
sudo curl -SL https://github.com/docker/compose/releases/download/v2.29.7/docker-compose-linux-x86_64 \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
docker compose version
```

### 3. Clone, configure, start

```bash
git clone <YOUR_REPO_URL> scaling-chatbots
cd scaling-chatbots

# Public URL browsers will use (no trailing slash, no :80)
PUBLIC_IP=$(curl -s http://checkip.amazonaws.com)
cp .env.example .env
sed -i "s|^PUBLIC_HOST=.*|PUBLIC_HOST=http://${PUBLIC_IP}|" .env
sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(openssl rand -hex 16)|" .env
# Optional: set CHATLOG_GROQ_API_KEY=... in .env for Groq in the UI

docker compose -f docker-compose.prod.yml up -d --build
```

First build pulls images and compiles the frontend with `NEXT_PUBLIC_API_URL`
baked to `PUBLIC_HOST` (several minutes). Then:

```bash
# Wait until healthy
docker compose -f docker-compose.prod.yml ps
curl -s http://127.0.0.1/health

# Optional demo data
docker compose -f docker-compose.prod.yml exec api uv run python backend/scripts/seed.py
```

Open:

- UI: `http://<PUBLIC_IP>/chat`
- API docs: `http://<PUBLIC_IP>/docs`
- Health: `http://<PUBLIC_IP>/health`

### 4. Day-2 commands

```bash
# Logs
docker compose -f docker-compose.prod.yml logs -f api consumer caddy

# Rebuild after a git pull (required if PUBLIC_HOST or frontend changed)
git pull
docker compose -f docker-compose.prod.yml up -d --build

# Stop
docker compose -f docker-compose.prod.yml down

# Stop and wipe DB/Redis volumes
docker compose -f docker-compose.prod.yml down -v
```

If you later attach a domain or HTTPS (ALB, Cloudflare, or Caddy TLS), set
`PUBLIC_HOST` to that URL (e.g. `https://logs.example.com`), rebuild the
frontend, and point CORS at the same value.

For local development with PostgreSQL and Redis already available:

```bash
uv sync
# Presidio needs en_core_web_sm. It is locked in uv.lock so `uv sync` installs
# it; the Dockerfile relies on that same path. If you install outside uv, run:
#   uv run python -m spacy download en_core_web_sm
uv run alembic upgrade head
uv run python backend/scripts/seed.py
# Terminal A — API (producer enqueues to Redis):
uv run uvicorn chatlog.main:app --reload
# Terminal B — independent consumer (stream → redact → Postgres):
uv run python backend/consumer.py
```

The default local URLs are
`postgresql+asyncpg://chatlog:chatlog@localhost:5432/chatlog` and
`redis://localhost:6379/0`. Override with `CHATLOG_DATABASE_URL` /
`CHATLOG_REDIS_URL`. Run checks with:

```bash
uv run pytest
uv run ruff check .
```

## API

- `POST /logs/ingest` validates an inference event at the edge, `XADD`s it onto
  the `inference_logs` Redis Stream, and returns `202 Accepted` with the stream
  entry ID. It does **not** write Postgres or redact — that is the consumer's
  job. If Redis is unreachable, the endpoint still returns `202` with a
  `warning` field (same fire-and-forget principle as the SDK) rather than
  failing the caller with `500`.
- `POST /conversations` creates a conversation from its first user message and
  appends an assistant response from the selected provider.
- `GET /providers` lists supported LLM providers, default models, and whether a
  browser-supplied API key is required.
- `GET /conversations?status=active|cancelled` lists conversations.
- `GET /conversations/{id}` returns a conversation with messages ordered by
  sequence number.
- `POST /conversations/{id}/messages` appends a user turn and assistant reply.
- `POST /conversations/{id}/cancel` idempotently marks a conversation cancelled.
- `GET /stats?window_hours=24` groups average latency, error rate, and token
  totals by model and provider.
- `GET /dashboard?window_minutes=60` returns summary totals, grouped metrics,
  latency time-series points, and recent log rows for the observability console.

FastAPI/Pydantic returns structured `422` responses for malformed request
bodies. Unknown `conversation_id` values are no longer rejected at ingest time
(the producer never talks to Postgres); a bad FK fails in the consumer and,
after enough retries, lands on the DLQ stream.

## Schema

`conversation_id` is the single join key across chat and analytics data.

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
               created_at TIMESTAMPTZ (the submitted timestamp)
```

The inference table has only two secondary indexes:

- `(conversation_id, created_at)` supports a conversation's inference timeline
  and time-ordered joins back to chat data.
- `(model, created_at)` supports model-focused, time-window analytics.

The provider dimension is grouped in `/stats`, but it is deliberately not
indexed without a demonstrated provider-filtered query. Every extra index adds
work to this append-heavy write path.

## Architecture decisions

PostgreSQL is used for both workloads to keep deployment, migrations, backup,
and local development to one system. Separate `chat` and `analytics` schemas
make ownership and future extraction visible without adding infrastructure.
This is appropriate for modest event volume, where operational simplicity is
more valuable than specialized analytical throughput.

### Ingestion flow (Redis Streams)

Producers never write Postgres. After edge validation they only append to a
Redis Stream; an independent consumer process owns redaction and persistence:

```text
SDK (or chat API)
  → POST /logs/ingest  (or in-process publish from conversations)
  → Pydantic validate
  → Redis XADD  inference_logs
       │
       │  [decoupled — producer returns 202 here]
       ▼
  consumer group (backend/consumer.py)
  → XREADGROUP
  → redact()  (second-pass Presidio)
  → Postgres analytics.inference_logs
  → XACK  (only after a successful commit)

  after N failed deliveries → XADD inference_logs_dlq + XACK
  (production would alert on DLQ depth)
```

Compose runs the consumer as its own service next to the API, so the boundary
is visible: kill the consumer, send chat messages, watch entries pile up
(`XLEN inference_logs` / `XRANGE inference_logs - +`), restart the consumer,
and it catches up. That backlog-and-drain behavior is the point of the pattern.

This take-home uses one producer path and one consumer instance. Consumer
groups already track pending vs acknowledged messages, so adding more consumer
replicas later is a config/ops change (more processes with distinct consumer
names in the same group), not a redesign.

**Redis vs Kafka (honest tradeoff).** Redis Streams is a lighter-weight
stand-in for Kafka here — fine for this scale and demo clarity. At production
volume I would use Kafka for partitioning, longer retention, and
multi-consumer-group fan-out (e.g. one group writes to Postgres, another feeds
a real-time alerting service, off the same topic). Compose Redis also runs
without AOF/RDB persistence; production would enable and tune persistence so a
Redis restart cannot silently drop the stream.

`LogStore` remains the narrow Postgres write interface used by the consumer
(`PostgresLogStore` is its only implementation). PII redaction runs in the
ingestion service on the consumer side, so a future sink cannot accidentally
bypass the server-side pass.

### PII redaction (Presidio)

Redaction is a defense-in-depth pair, not a single choke point:

1. **SDK (first pass)** — the instrumentation wrapper queues raw preview text;
   the background delivery worker runs `redact()` on `input_preview` /
   `output_preview` before `POST /logs/ingest`. Redaction never sits on the
   synchronous chat path.
2. **Backend consumer (second pass)** — the stream consumer runs `redact()`
   again before writing to Postgres. If this pass changes a preview, a debug
   log notes that the caller may have bypassed SDK-side redaction (direct API
   use, or a future adapter that forgot the SDK path). The HTTP ingest
   endpoint does **not** redact; it only validates and enqueues.

Only free-text preview fields (and, for this take-home, stored message
`content`) are redacted. Structural metadata — model, timestamps,
`conversation_id`, token counts — is left alone.

**Shared module choice.** Implementation lives once in
`shared/src/chatlog_pii/pii_redactor.py` (`chatlog_pii`), published as a tiny
local package in the same wheel as the backend and SDK. Thin re-exports in
`chatlog_sdk.redaction` and `chatlog.services.redaction` keep existing import
paths. Tradeoff versus vendoring two copies: one source of truth and no sync
drift, at the cost of a third package path in the monorepo. Given remaining
time, a shared package was faster and safer than duplicated files with
"keep in sync" comments.

`redact(text) -> str` uses Microsoft Presidio (`presidio-analyzer` +
`presidio-anonymizer`) with a healthcare-adjacent entity set: `PERSON`,
`EMAIL_ADDRESS`, `PHONE_NUMBER`, `US_SSN`, `LOCATION`, `DATE_TIME` (only when
the span looks like a DOB), and `MEDICAL_LICENSE`. Matches become typed
placeholders (`<PERSON>`, `<EMAIL_ADDRESS>`, …) so debugging retains
structure without the raw value. The `AnalyzerEngine` is constructed once at
module import and reused.

**spaCy model.** Presidio's NLP backend needs `en_core_web_sm` (faster than
`en_core_web_lg`). That wheel is locked in `uv.lock` and installed by
`uv sync` / the Dockerfile — do not rely on a separate
`python -m spacy download` in Docker, which often fails under uv-managed
envs in slim images. If you install the project without the lockfile, download
the model explicitly.

**Why Presidio (and when to reconsider).** Presidio is NER-based rather than
fragile regex-only matching, which is a stronger fit for names/locations and
closer to what a real healthcare-adjacent logging product would show. The
cost is latency plus the model download. At higher request volume I would
reconsider a hybrid: fast regex for structured patterns (SSN, email, phone,
credit card) and Presidio NER only for unstructured entities, plus caching or
batching of analyzer calls on the worker.

**Chat history at rest.** This take-home also runs `redact()` on stored
`chat.messages.content`. In production I would **not** redact primary chat
history at rest by default: placeholders break faithful resume and re-render
of a real conversation (the UI and any multi-turn LLM context would see
`<PERSON>` instead of the patient's name). Prefer encrypting history at rest,
scoping access, and redacting only derived surfaces (inference previews,
exports, support tools). Redacting history here demonstrates the shared
function end-to-end; it is not the production recommendation.

At higher analytical volume I would still batch from the stream (or from Kafka)
into ClickHouse for high-cardinality, long-window analytics, with idempotency
keys, consumer-lag monitoring, and an explicit consistency contract beyond the
basic DLQ this take-home ships.

## With more time

- Add authenticated callers, request size/rate limits, and tenant isolation.
- Add idempotency keys and provider trace IDs to prevent duplicate events.
- Partition `analytics.inference_logs` by time once volume warrants it, plus
  retention/archival policies.
- Improve PII handling with measured false-positive/false-negative rates,
  hybrid regex+NER pipelines, and optional redaction policies per field
  (previews vs chat history).
- Add PostgreSQL integration tests (including migration upgrade/downgrade),
  pagination for conversation lists, and observability for write latency and
  failures.

## Python instrumentation SDK

The provider-neutral SDK lives in `sdk/src/chatlog_sdk` and is included in this
repository's existing Python package; it does not introduce another project
file. Install the project in a chatbot environment with `uv sync` (or
`uv pip install -e .`), then set:

```bash
export LLM_LOG_BACKEND_URL=http://localhost:8000
# Optional:
export LLM_LOG_API_KEY=secret
export LLM_LOG_TIMEOUT_SECONDS=1.0
export LLM_LOG_PREVIEW_CHARS=1000
export LLM_LOG_QUEUE_CAPACITY=1000
```

Set the conversation once at the request boundary. Nested decorated calls pick
it up through a `contextvar`, including across `asyncio` tasks:

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

Provider and model can be inferred from recognized successful responses, but
passing the provider explicitly is recommended so failed calls are attributed
correctly. For an inline call, assign the response to the context:

```python
from chatlog_sdk import instrumented_call

with instrumented_call(
    provider="anthropic",
    model="claude-sonnet-4",
    input_data=prompt,
) as call:
    response = call.set_response(anthropic_client.messages.create(...))
```

Both forms preserve the wrapped function's return value. If the provider raises,
the SDK queues an error event and re-raises the original exception unchanged.
OpenAI and Anthropic response adapters normalize text, model, and token usage.
To add another provider, implement the small `ProviderAdapter` protocol and
pass it to `InstrumentationClient(adapters=[...])` or call
`client.register_adapter(adapter)`; core instrumentation does not change.

### Delivery behavior

Instrumentation only puts a payload into a bounded in-memory queue. A daemon
worker redacts `input_preview` / `output_preview` with Presidio, then sends
`POST /logs/ingest` with a short timeout and retries once after a short
backoff. A full queue, timeout, non-2xx response, or unavailable backend
drops telemetry silently after that retry; none of these conditions can fail or
delay the chatbot's LLM response. The backend consumer still redacts again as
defense in depth after reading from the Redis Stream.

Send-per-call is intentional at this size. Batching would improve network and
ingestion efficiency at higher volume, but adds flush timing, partial-failure,
and shutdown semantics. Since the queue is memory-only and the worker is a
daemon, abrupt process exit can lose queued events. Short-lived scripts may call
`client.flush()` or `client.close()` for a best-effort drain; request handlers
should not do so.

Run the end-to-end mocked example after starting and seeding the backend:

```bash
uv run python sdk/examples/mock_chatbot.py
```

With more time, the SDK would add event IDs for idempotency, batch uploads,
metrics for dropped events, framework integrations, and an optional bounded
local disk buffer for offline resilience and crash recovery.

## Frontend observability console

The Next.js frontend lives in `frontend/`. Start the API and database first,
then run:

```bash
cp .env.example .env
# Edit .env and set CHATLOG_GROQ_API_KEY=your-groq-key

docker compose up --build

# In another terminal:
cd frontend
cp .env.example .env.local
npm install
npm run dev
```

Open <http://localhost:3000/chat>. `NEXT_PUBLIC_API_URL` defaults to
`http://localhost:8000`.

If you need `sudo` for Docker, keep the key in the project `.env` file
(Compose loads it for substitution). A shell `export` alone is dropped by
`sudo` and will leave Groq unconfigured.

The console supports three OpenAI-compatible providers:

| Provider | Auth |
|----------|------|
| Groq | Server key via `CHATLOG_GROQ_API_KEY` |
| OpenAI | Browser-supplied API key (held only in browser memory) |
| Gemini | Browser-supplied API key (held only in browser memory) |

Pick provider and model in the composer. Client API keys are sent with each
chat request for proxying and are never written to Postgres. Without
`CHATLOG_GROQ_API_KEY`, Groq shows as not configured; OpenAI and Gemini still
work when the user pastes their own key.

All runtime content is real backend data: conversation creation, multi-turn
messages, cancellation, history, and telemetry use fetch calls to the FastAPI
service. The frontend contains no conversation or stats fixtures. The
`sdk/examples/mock_chatbot.py` script remains an SDK demonstration only and is
not imported by the UI.

### Frontend design rationale

The interface uses a dense three-pane console: session index, ordered message
trace, and aggregate runtime health. Its cool blue-slate structure avoids both
warm cream/clay branding and the black-plus-neon pattern. Color is semantic:
muted blue marks selection/live activity, green means healthy, amber means slow,
and red is reserved for errors or cancellation. UI labels use a restrained
system sans; message content, IDs, timestamps, model names, token counts, and
latency use monospace because they are machine data that benefits from stable
character rhythm.

The signature moment is the inline inference trace lane shown while a provider
request is running. Sequenced cells make the wait state read like a live trace,
in the exact place where the assistant response will appear. It is otherwise a
quiet, rule-based interface with no gradients on controls, floating cards,
glass, or decorative process numbering. The trace animation and all transitions
are disabled when `prefers-reduced-motion` is enabled. On narrower screens,
conversation and telemetry views become keyboard-accessible tabs and the
session index becomes a dismissible drawer.
