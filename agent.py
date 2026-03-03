#!/usr/bin/env python3
import asyncio
import os
import sys
from datetime import datetime
from typing import Any, Optional
from uuid import uuid4

import httpx
from agents import Agent, Runner, SQLiteSession, function_tool, gen_trace_id, trace
from dotenv import load_dotenv


SYSTEM_INSTRUCTIONS = """# Debit Card Limit Management Agent

## Role
You are a helpful banking assistant that helps users manage their debit card limits for POS (payment), ATM (withdrawal), and E-commerce (internet payment) transactions.

## Tools
- Use the provided direct API tools only as source of truth.
- Never use hardcoded values.
- All numbers, names, and limits shown to users must come from tool responses.

## Conversational Flow
1) Fetch cards first.
Call `get_payment_instruments` and store card/account data.

2) Ask user to select card.
Show compact numbered options using masked card number and current limits from tool responses.

3) Ask transaction type.
Offer only: `pos`, `atm`, `ecom`. Include current limit in the prompt.

4) Ask new limit amount.
Require a positive integer.

5) Ask duration type.
Permanent or temporary.

6) If temporary, collect dates.
Collect `start_date` and `end_date` in `YYYY-MM-DD` format and ensure `end_date > start_date`.

7) Confirm before execution.
Summarize:
- Card
- Limit type
- Current limit
- New limit
- Duration
Ask explicit confirmation.

8) Execute requested change.
- Permanent: `change_limit(card_id, limit_type, limit)`
- Temporary: `create_temporary_limit(card_id, limit_type, limit, start_date, end_date)`

9) Return success with updated values.
Show updated current limits and active temporary limits (if any).

## Error Handling
- Invalid `limit_type`: ask user to choose `pos`, `atm`, or `ecom`.
- Invalid limit amount: ask for a positive number.
- Invalid date format: ask for `YYYY-MM-DD`.
- `end_date <= start_date`: ask user to correct dates.
- Tool response contains `{"error": ...}`: show a short friendly retry message.
- No cards found: explain that no cards are available.

## Response Style
- Keep messages concise.
- Prefer compact numbered options for selections.
- Use bullets for limit summaries.
- Always mask card numbers (e.g., `****1234`).
- Never expose secrets or full sensitive identifiers.
"""


def env(name: str, default: Optional[str] = None) -> str:
    value = os.getenv(name, default)
    if value is None:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def get_user_message() -> str:
    # Allow both CLI argument mode and interactive prompt mode.
    if len(sys.argv) > 1:
        return " ".join(sys.argv[1:]).strip()
    return input("You: ").strip()


def _is_valid_yyyy_mm_dd(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except ValueError:
        return False


class LimitApiClient:
    def __init__(self, base_url: str, timeout_seconds: int) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds

    async def request(self, method: str, path: str, payload: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.request(method, url, json=payload)
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                detail: Any = None
                try:
                    detail = exc.response.json()
                except Exception:
                    detail = exc.response.text
                return {
                    "error": "api_http_error",
                    "status_code": exc.response.status_code,
                    "detail": detail,
                    "path": path,
                }
            except httpx.RequestError as exc:
                return {
                    "error": "api_unreachable",
                    "detail": str(exc),
                    "path": path,
                }

        try:
            data = response.json()
        except ValueError:
            return {
                "error": "api_invalid_json",
                "detail": response.text,
                "path": path,
            }
        if isinstance(data, dict):
            return data
        return {"data": data}


def build_tools(client: LimitApiClient):
    @function_tool
    async def get_payment_instruments() -> dict[str, Any]:
        """Fetch all accounts and cards with current and temporary limits."""
        return await client.request("GET", "/accounts")

    @function_tool
    async def get_current_limits(card_id: str) -> dict[str, Any]:
        """Get current and temporary limits for the provided card_id."""
        if not card_id.strip():
            return {"error": "validation_error", "detail": "card_id is required"}
        return await client.request("GET", f"/cards/{card_id}/limits")

    @function_tool
    async def get_default_card_limits() -> dict[str, Any]:
        """Get current and temporary limits for the default card."""
        return await client.request("GET", "/cards/default/limits")

    @function_tool
    async def change_limit(card_id: str, limit_type: str, limit: int) -> dict[str, Any]:
        """Change permanent limit for a card. limit_type must be pos, atm, or ecom."""
        normalized_type = limit_type.strip().lower()
        if normalized_type not in {"pos", "atm", "ecom"}:
            return {
                "error": "validation_error",
                "detail": "limit_type must be one of: pos, atm, ecom",
            }
        if limit < 0:
            return {"error": "validation_error", "detail": "limit must be >= 0"}
        if not card_id.strip():
            return {"error": "validation_error", "detail": "card_id is required"}

        return await client.request(
            "PATCH",
            f"/cards/{card_id}/limits/{normalized_type}",
            payload={"limit": limit},
        )

    @function_tool
    async def create_temporary_limit(
        card_id: str,
        limit_type: str,
        limit: int,
        start_date: str,
        end_date: str,
    ) -> dict[str, Any]:
        """Create temporary limit for a card. Dates must use YYYY-MM-DD and end_date must be after start_date."""
        normalized_type = limit_type.strip().lower()
        if normalized_type not in {"pos", "atm", "ecom"}:
            return {
                "error": "validation_error",
                "detail": "limit_type must be one of: pos, atm, ecom",
            }
        if limit < 0:
            return {"error": "validation_error", "detail": "limit must be >= 0"}
        if not card_id.strip():
            return {"error": "validation_error", "detail": "card_id is required"}
        if not _is_valid_yyyy_mm_dd(start_date) or not _is_valid_yyyy_mm_dd(end_date):
            return {
                "error": "validation_error",
                "detail": "start_date and end_date must be in YYYY-MM-DD format",
            }
        if end_date <= start_date:
            return {
                "error": "validation_error",
                "detail": "end_date must be after start_date",
            }

        return await client.request(
            "POST",
            f"/cards/{card_id}/temporary-limits/{normalized_type}",
            payload={"limit": limit, "startDate": start_date, "endDate": end_date},
        )

    return [
        get_payment_instruments,
        get_current_limits,
        get_default_card_limits,
        change_limit,
        create_temporary_limit,
    ]


async def main() -> None:
    load_dotenv()

    api_key = env("OPENAI_API_KEY")
    model = env("OPENAI_MODEL", "gpt-4.1")
    limit_api_base_url = env("LIMIT_API_BASE_URL", "http://127.0.0.1:2010")
    limit_api_timeout = int(env("LIMIT_API_TIMEOUT", "15"))

    # The Agents SDK reads provider credentials from the environment.
    os.environ["OPENAI_API_KEY"] = api_key

    session = SQLiteSession(session_id=f"limit-cli-{uuid4()}")
    tools = build_tools(LimitApiClient(base_url=limit_api_base_url, timeout_seconds=limit_api_timeout))

    agent = Agent(
        name="Debit Card Limit Assistant",
        instructions=SYSTEM_INSTRUCTIONS,
        model=model,
        tools=tools,
    )

    print("Debit Card Limit Agent (OpenAI Agents SDK + Direct API Tools)")
    if len(sys.argv) > 1:
        print("Single-turn arg mode.\n")
        user_message = get_user_message()
        trace_id = gen_trace_id()
        with trace(workflow_name="Debit Card Limit Direct Tool Calling", trace_id=trace_id):
            print(f"Trace: https://platform.openai.com/traces/trace?trace_id={trace_id}\n")
            try:
                result = await Runner.run(agent, user_message, session=session)
            except Exception as exc:
                print(f"Agent: Request failed: {exc}")
                print("Agent: Make sure limit-api is running and LIMIT_API_BASE_URL is reachable.\n")
                return

        output = str(result.final_output or "").strip()
        if not output:
            print("Agent: I could not generate a response. Please try again.")
            return

        print(f"Agent: {output}\n")
        return

    print("Multi-turn interactive mode. Type 'exit' to quit.\n")
    while True:
        user_message = input("You: ").strip()
        if not user_message:
            continue
        if user_message.lower() in {"exit", "quit"}:
            print("Agent: Goodbye.")
            return

        trace_id = gen_trace_id()
        with trace(workflow_name="Debit Card Limit Direct Tool Calling", trace_id=trace_id):
            print(f"Trace: https://platform.openai.com/traces/trace?trace_id={trace_id}\n")
            try:
                result = await Runner.run(agent, user_message, session=session)
            except Exception as exc:
                print(f"Agent: Request failed: {exc}")
                print("Agent: Make sure limit-api is running and LIMIT_API_BASE_URL is reachable.\n")
                continue

        output = str(result.final_output or "").strip()
        if not output:
            print("Agent: I could not generate a response. Please try again.")
            continue

        print(f"Agent: {output}\n")


if __name__ == "__main__":
    asyncio.run(main())
