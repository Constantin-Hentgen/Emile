FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first: this layer stays cached while pyproject.toml and uv.lock are unchanged.
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --extra service --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev --extra service

# /data holds the SQLite database (named volume; it inherits these permissions when created).
RUN useradd --system --no-create-home stockpot && mkdir -p /data && chown stockpot /data
VOLUME /data
USER stockpot

ENV PATH="/app/.venv/bin:$PATH" \
    STOCKPOT_HOST=0.0.0.0 \
    STOCKPOT_PORT=8000 \
    STOCKPOT_DATABASE_URL=sqlite:////data/stockpot.db
EXPOSE 8000

HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

CMD ["stockpot", "serve"]
