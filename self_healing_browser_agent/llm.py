"""LLM provider abstraction.

The step parser and selector generator only ever call one method:
`client.messages.create(model=..., max_tokens=..., system=..., tools=...,
tool_choice=..., messages=...)`, and read back a response with a
`.content` list of tool_use blocks. That is the Anthropic Messages API
shape, and `anthropic.Anthropic` already satisfies it directly, so
`AnthropicLLM` is just that client re-exported under a name that makes
the provider swap explicit at the call site.

`FakeLLM` implements the same shape from a canned script instead of a
network call. It powers the offline demo (`journey-agent demo`) and the
test suite, so the whole pipeline -- parsing, ranking, fallback,
runtime re-analysis -- can be exercised with no API key and no
network access.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

DEFAULT_MODEL_ENV_VAR = "ANTHROPIC_MODEL"
DEFAULT_MODEL = "claude-sonnet-5"


def default_model() -> str:
    import os

    return os.environ.get(DEFAULT_MODEL_ENV_VAR, DEFAULT_MODEL)


class LLMClient(Protocol):
    """Structural type both the real Anthropic client and FakeLLM satisfy."""

    @property
    def messages(self) -> _MessagesLike: ...


class _MessagesLike(Protocol):
    def create(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str,
        tools: list[dict],
        tool_choice: dict,
        messages: list[dict],
    ) -> Any: ...


def AnthropicLLM(api_key: str) -> Any:
    """Build the real Anthropic client. A thin, explicitly-named wrapper so
    call sites read `AnthropicLLM(...)` instead of a bare `Anthropic(...)`."""

    from anthropic import Anthropic

    return Anthropic(api_key=api_key)


# ---------------------------------------------------------------------------
# Fake provider
# ---------------------------------------------------------------------------


@dataclass
class _ToolUseBlock:
    input: dict
    name: str
    type: str = "tool_use"


@dataclass
class _FakeResponse:
    content: list[_ToolUseBlock]
    stop_reason: str = "tool_use"


class FakeLLMError(RuntimeError):
    """Raised when FakeLLM is asked for a script entry it was not given."""


@dataclass
class FakeLLM:
    """A canned stand-in for the Anthropic client.

    `parsed_steps` answers the step-parser tool call unconditionally --
    one journey description per FakeLLM instance is enough for the demo
    and for tests.

    `selectors_by_target` maps a target description to the ranked
    selector list returned on the *first* selector-generator call for
    that target. `healed_selectors_by_target` maps a target description
    to the list returned on a *re-analysis* call (i.e. one that carries
    a retry hint) -- this is what the self-healing tests exercise.
    """

    parsed_steps: dict | None = None
    selectors_by_target: dict[str, list[dict]] = field(default_factory=dict)
    healed_selectors_by_target: dict[str, list[dict]] = field(default_factory=dict)
    model_name: str = "fake-llm"
    calls: list[dict] = field(default_factory=list, repr=False)

    @property
    def messages(self) -> _FakeMessages:
        return _FakeMessages(self)


class _FakeMessages:
    def __init__(self, fake: FakeLLM) -> None:
        self._fake = fake

    def create(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str,
        tools: list[dict],
        tool_choice: dict,
        messages: list[dict],
    ) -> _FakeResponse:
        tool_name = tool_choice["name"]
        self._fake.calls.append({"tool": tool_name, "messages": messages})

        if tool_name == "record_parsed_steps":
            return self._answer_step_parser()
        if tool_name == "record_ranked_selectors":
            return self._answer_selector_generator(messages)
        raise FakeLLMError(f"FakeLLM has no script for tool {tool_name!r}")

    def _answer_step_parser(self) -> _FakeResponse:
        if self._fake.parsed_steps is None:
            raise FakeLLMError(
                "FakeLLM.parsed_steps was not set; nothing to return for the step parser."
            )
        block = _ToolUseBlock(name="record_parsed_steps", input=self._fake.parsed_steps)
        return _FakeResponse(content=[block])

    def _answer_selector_generator(self, messages: list[dict]) -> _FakeResponse:
        user_content = messages[-1]["content"]
        target = _extract_target_description(user_content)
        is_reanalysis = "re-analysis" in user_content

        script = (
            self._fake.healed_selectors_by_target
            if is_reanalysis
            else self._fake.selectors_by_target
        )
        # A re-analysis call falls back to the first-pass script if no
        # dedicated healed entry exists, so callers only need to script
        # the cases they actually care about.
        selectors = script.get(target) or self._fake.selectors_by_target.get(target)
        if selectors is None:
            raise FakeLLMError(
                f"FakeLLM has no scripted selectors for target {target!r} "
                f"(re-analysis={is_reanalysis})"
            )
        block = _ToolUseBlock(name="record_ranked_selectors", input={"selectors": selectors})
        return _FakeResponse(content=[block])


def _extract_target_description(user_content: str) -> str:
    prefix = "Target element description: "
    for line in user_content.splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    return user_content.strip()
