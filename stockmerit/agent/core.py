"""The agent loop: Claude + local analysis tools + optional server-side web search."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from . import data, profile
from .tools import TOOLS, run_tool

PROMPTS = Path(__file__).resolve().parent / "prompts"

# Model id and web-search tool version change over time; both can be overridden by
# the host app. Check current values at https://docs.claude.com before first use.
DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_WEB_SEARCH_TOOL = "web_search_20250305"
MAX_STEPS = 30

DAILY_REPORT_PROMPT = (
    "Generate today's Daily Market Analysis Report exactly as defined in section 10 "
    "(A to G). Use the tools for every figure, time-stamp the data, mark anything you "
    "could not verify as unavailable, and recommend staying in cash if nothing qualifies.")


class Agent:
    """One conversation. Holds its own message history and a reference to the
    session's profile dict, so separate users never share state."""

    def __init__(self, client=None, api_key: str | None = None,
                 profile_store: dict | None = None,
                 extra_tools: list[tuple[dict, Callable]] | None = None,
                 on_event: Callable[[str, str], None] | None = None,
                 model: str | None = None, web_search: bool = True,
                 web_search_tool: str | None = None, max_tokens: int = 8000):
        if client is None:
            import anthropic  # lazy: the rest of the app runs without it installed
            client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        self.client = client
        self.profile = profile_store if profile_store is not None else {}
        self.on_event = on_event
        self.model = model or DEFAULT_MODEL
        self.max_tokens = max_tokens
        self.web_search = web_search
        self.messages: list[dict] = []
        self.extra = {schema["name"]: fn for schema, fn in (extra_tools or [])}
        self.tools = list(TOOLS) + [schema for schema, _ in (extra_tools or [])]
        if web_search:
            self.tools.append({"type": web_search_tool or DEFAULT_WEB_SEARCH_TOOL,
                               "name": "web_search", "max_uses": 8})

    def _system(self) -> list[dict]:
        text = (PROMPTS / "spec.md").read_text() + (PROMPTS / "operating_rules.md").read_text()
        st = profile.status(self.profile)
        session = (
            f"\n\n# SESSION CONTEXT\nCurrent time: {data.now_ist()}\n"
            f"Web search available: {'yes' if self.web_search else 'no'}\n"
            f"Saved user profile: {json.dumps(st['profile'])}\n"
            f"Profile fields still missing: {list(st['missing_fields'])}\n")
        return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": session}]

    def _emit(self, kind: str, detail: str) -> None:
        if self.on_event:
            try:
                self.on_event(kind, detail)
            except Exception:
                pass

    def ask(self, user_text: str) -> str:
        """Send one user turn, run tools until the model finishes, return its text."""
        self.messages.append({"role": "user", "content": user_text})
        answer: list[str] = []
        sources: dict[str, str] = {}
        for _ in range(MAX_STEPS):
            resp = self.client.messages.create(
                model=self.model, max_tokens=self.max_tokens, system=self._system(),
                tools=self.tools, messages=self.messages)
            self.messages.append({"role": "assistant", "content": resp.content})
            results = []
            for block in resp.content:
                btype = getattr(block, "type", None)
                if btype == "text":
                    answer.append(block.text)
                    for c in getattr(block, "citations", None) or []:
                        url = getattr(c, "url", None)
                        if url:
                            sources[url] = getattr(c, "title", None) or url
                elif btype == "tool_use":
                    self._emit("tool", f"{block.name} {json.dumps(block.input)[:120]}")
                    results.append({"type": "tool_result", "tool_use_id": block.id,
                                    "content": run_tool(block.name, block.input,
                                                        self.profile, self.extra)})
                elif btype == "server_tool_use":
                    q = getattr(block, "input", None) or {}
                    self._emit("search", str(q.get("query", "")) if isinstance(q, dict) else "")
            if resp.stop_reason == "tool_use" and results:
                self.messages.append({"role": "user", "content": results})
                continue
            if resp.stop_reason == "pause_turn":  # long server-tool turn: send back to continue
                continue
            if resp.stop_reason == "max_tokens":
                answer.append("\n\n[Response cut off at the token limit. Ask me to continue.]")
            break
        else:
            answer.append("\n\n[Stopped: too many tool steps in one turn.]")
        text = "".join(answer).strip()
        if sources:
            text += "\n\n**Sources**\n" + "\n".join(f"- [{t}]({u})" for u, t in sources.items())
        return text
