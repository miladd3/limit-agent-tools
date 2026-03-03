# Limit Agent (OpenAI Agents SDK + Direct API Tools)

Production-style CLI agent that uses the OpenAI Agents SDK with direct HTTP tool calling against `limit-api`.

This project mirrors the MCP agent behavior, but tools call FastAPI routes directly instead of MCP.

## What it does

- Uses `openai-agents` as the agent runtime
- Exposes direct function tools that call `limit-api`
- Follows the debit card limit flow (select card, type, amount, temporary/permanent, confirm)
- Uses API responses as the only source of truth for card and limit data

## Prerequisites

- Python 3.10+
- Running REST API server from `limit-api`
- OpenAI API key

## 1) Start limit-api server

From `limit-api`:

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 2010
```

Default API base URL:

```text
http://127.0.0.1:2010
```

## 2) Setup this agent

From `limit-agent-using-tools`:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Set your key in `.env`:

```text
OPENAI_API_KEY=...
```

## 3) Run agent

```bash
python agent.py
```

Interactive multi-turn mode: type prompts continuously. Type `exit` to quit.

You can also pass a prompt directly:

```bash
python agent.py "show my current card limits"
```

Argument mode is single-turn and exits after one response.

## Environment variables

- `OPENAI_API_KEY` (required)
- `OPENAI_MODEL` (default: `gpt-4.1`)
- `LIMIT_API_BASE_URL` (default: `http://127.0.0.1:2010`)
- `LIMIT_API_TIMEOUT` (default: `15`; HTTP timeout in seconds)

## Notes

- The agent keeps chat continuity using an SDK `SQLiteSession` instance.
- If `limit-api` is unavailable, tool calls return structured errors and the agent gives a retry-friendly response.
- For production deployment, inject secrets from your runtime secret manager instead of plain `.env` files.
