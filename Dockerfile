FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml uv.lock README.md alembic.ini ./
COPY backend ./backend
COPY sdk ./sdk
COPY shared ./shared
# en-core-web-sm is locked in uv.lock (Presidio's spaCy backend). A separate
# `python -m spacy download` step is unreliable under uv in slim images.
RUN uv sync --frozen --no-dev

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "chatlog.main:app", "--host", "0.0.0.0", "--port", "8000"]
