"""FakeLLM routes tool calls to canned answers, offline."""

from __future__ import annotations

import pytest

from self_healing_browser_agent.llm import FakeLLM, FakeLLMError


def test_fake_llm_answers_step_parser_tool():
    fake = FakeLLM(
        parsed_steps={
            "steps": [
                {
                    "step_number": 1,
                    "action_type": "navigate",
                    "target_description": "home page",
                    "value": "https://example.com",
                }
            ]
        }
    )
    resp = fake.messages.create(
        model="fake",
        max_tokens=100,
        system="irrelevant",
        tools=[{"name": "record_parsed_steps"}],
        tool_choice={"type": "tool", "name": "record_parsed_steps"},
        messages=[{"role": "user", "content": "go to the home page"}],
    )
    block = resp.content[0]
    assert block.name == "record_parsed_steps"
    assert block.input["steps"][0]["action_type"] == "navigate"


def test_fake_llm_answers_selector_tool_by_target_description():
    fake = FakeLLM(
        selectors_by_target={
            "Sign In button": [
                {
                    "selector_type": "css",
                    "selector_value": "[data-testid='signin']",
                    "stability_rating": "high",
                    "reasoning": "test-intent attribute",
                }
            ]
        }
    )
    resp = fake.messages.create(
        model="fake",
        max_tokens=100,
        system="irrelevant",
        tools=[{"name": "record_ranked_selectors"}],
        tool_choice={"type": "tool", "name": "record_ranked_selectors"},
        messages=[
            {
                "role": "user",
                "content": "Target element description: Sign In button\n\nDOM context:\n...",
            }
        ],
    )
    block = resp.content[0]
    assert block.input["selectors"][0]["selector_value"] == "[data-testid='signin']"


def test_fake_llm_uses_healed_script_on_reanalysis():
    fake = FakeLLM(
        selectors_by_target={
            "Target": [
                {
                    "selector_type": "css",
                    "selector_value": "#stale",
                    "stability_rating": "low",
                    "reasoning": "x",
                }
            ]
        },
        healed_selectors_by_target={
            "Target": [
                {
                    "selector_type": "css",
                    "selector_value": "#fresh",
                    "stability_rating": "high",
                    "reasoning": "found on re-analysis",
                }
            ]
        },
    )
    reanalysis_message = (
        "Target element description: Target\n\nDOM context:\n...\n\n"
        "IMPORTANT -- this is a re-analysis. The following selectors "
        "previously generated for this same target ALL FAILED:\n  - (css) #stale"
    )
    resp = fake.messages.create(
        model="fake",
        max_tokens=100,
        system="irrelevant",
        tools=[{"name": "record_ranked_selectors"}],
        tool_choice={"type": "tool", "name": "record_ranked_selectors"},
        messages=[{"role": "user", "content": reanalysis_message}],
    )
    assert resp.content[0].input["selectors"][0]["selector_value"] == "#fresh"


def test_fake_llm_raises_clear_error_for_unscripted_target():
    fake = FakeLLM()
    with pytest.raises(FakeLLMError):
        fake.messages.create(
            model="fake",
            max_tokens=100,
            system="irrelevant",
            tools=[{"name": "record_ranked_selectors"}],
            tool_choice={"type": "tool", "name": "record_ranked_selectors"},
            messages=[{"role": "user", "content": "Target element description: nope"}],
        )
