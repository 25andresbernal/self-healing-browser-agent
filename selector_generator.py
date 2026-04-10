"""LLM-powered selector generator.

Given a target description and a compact DOM context, returns the top
ranked selectors for the target element. Uses Anthropic forced tool use
so the output is structured and Pydantic-validated.
"""

from __future__ import annotations

from anthropic import Anthropic

from models import RankedSelectors, Selector
from prompts import (
    SELECTOR_SYSTEM,
    SELECTOR_TOOL,
    build_selector_user_message,
)
from resilience import retry_with_backoff


def generate_selectors(
    target_description: str,
    dom_context: str,
    *,
    client: Anthropic,
    model: str,
    retry_hint_selectors: list[Selector] | None = None,
    max_tokens: int = 2048,
) -> list[Selector]:
    """Return a list of Selector objects ranked most-stable-first.

    Pass `retry_hint_selectors` on runtime re-analysis: those are the
    selectors that already failed, and the model will be told not to
    repeat them.
    """

    retry_hint: str | None = None
    if retry_hint_selectors:
        retry_hint = "\n".join(
            f"  - ({s.selector_type.value}) {s.selector_value}"
            for s in retry_hint_selectors
        )

    user_message = build_selector_user_message(
        target_description=target_description,
        dom_context=dom_context,
        retry_hint=retry_hint,
    )

    def _call() -> RankedSelectors:
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=SELECTOR_SYSTEM,
            tools=[SELECTOR_TOOL],
            tool_choice={"type": "tool", "name": SELECTOR_TOOL["name"]},
            messages=[{"role": "user", "content": user_message}],
        )
        tool_input = _extract_tool_use(resp, SELECTOR_TOOL["name"])
        return RankedSelectors.model_validate(tool_input)

    ranked = retry_with_backoff(_call, label="selector_generator")
    return list(ranked.selectors)


def _extract_tool_use(resp, tool_name: str) -> dict:
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == tool_name:
            return block.input
    raise RuntimeError(
        f"LLM response did not contain a tool_use block named {tool_name!r}. "
        f"Stop reason: {getattr(resp, 'stop_reason', 'unknown')}"
    )
