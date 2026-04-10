"""Natural language -> structured journey steps, via Claude tool use."""

from __future__ import annotations

from anthropic import Anthropic

from models import ParsedSteps
from prompts import STEP_PARSER_SYSTEM, STEP_PARSER_TOOL
from resilience import retry_with_backoff


def parse_steps(
    raw_text: str,
    *,
    client: Anthropic,
    model: str,
    max_tokens: int = 4096,
) -> ParsedSteps:
    """Convert free-form user input into an ordered list of ParsedStep.

    Uses Anthropic's forced tool use so the response is guaranteed to
    include a `record_parsed_steps` tool_use block we can validate.
    """

    def _call() -> ParsedSteps:
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=STEP_PARSER_SYSTEM,
            tools=[STEP_PARSER_TOOL],
            tool_choice={"type": "tool", "name": STEP_PARSER_TOOL["name"]},
            messages=[{"role": "user", "content": raw_text}],
        )
        tool_use = _extract_tool_use(resp, STEP_PARSER_TOOL["name"])
        return ParsedSteps.model_validate(tool_use)

    return retry_with_backoff(_call, label="step_parser")


def _extract_tool_use(resp, tool_name: str) -> dict:
    """Pull the first tool_use block matching `tool_name` out of an Anthropic
    response, raising a clear error if none is found."""

    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == tool_name:
            return block.input
    raise RuntimeError(
        f"LLM response did not contain a tool_use block named {tool_name!r}. "
        f"Stop reason: {getattr(resp, 'stop_reason', 'unknown')}"
    )
