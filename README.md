# Stockpot

[![CI](https://github.com/Constantin-Hentgen/stockpot/actions/workflows/ci.yml/badge.svg)](https://github.com/Constantin-Hentgen/stockpot/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**Stockpot** is a small, self-hosted service for the kitchen side of life: **shopping lists** grouped by aisle
and ticked off in the shop, the **kitchen stock** (perishables with a "keeps N days" due date and "still there?"
checks), **batch-cooked boxes**, and **recipes** linked to a list. It can optionally sync the shopping list to
any **CalDAV** server, as one event with a checklist or as one task per item.

## Why another one?

[Grocy](https://grocy.info) and [Mealie](https://mealie.io) are full applications built around their own web UI.
Stockpot is **API-first and bot-friendly**. It was built as the backend of a chat assistant, and it serves just
as well behind a script, a home-automation flow or your own front end.

- **Deterministic: no LLM inside.** Your client (an assistant, a person, a script) decides *what* to buy or cook.
  Stockpot stores, edits, checks, expires and syncs, using rules that are explicit and tested.
- **Library or service.** Use the pure Python core (`stockpot.core`) directly, or run the HTTP API in Docker.
- **Households.** Several users with their own API keys share one list and one stock.

## Quick start

```sh
git clone https://github.com/Constantin-Hentgen/stockpot.git && cd stockpot
cp .env.example .env        # optional: every variable has a default
docker compose up -d
curl http://localhost:18010/health
```

The API docs are served at `http://localhost:18010/docs`.

For development, use `uv sync`, then `uv run --frozen python -m pytest -q`.

## Documentation

Everything else (configuration, API, CalDAV sync, the library, operations) is in the
**[wiki](https://github.com/Constantin-Hentgen/stockpot/wiki)**.

## Licence

[MIT](LICENSE)
