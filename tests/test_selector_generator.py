"""Selector ranking and fallback-order behavior, driven by FakeLLM."""

from __future__ import annotations

from self_healing_browser_agent.llm import FakeLLM
from self_healing_browser_agent.selector_generator import generate_selectors


def test_generate_selectors_preserves_rank_order_most_stable_first():
    fake = FakeLLM(
        selectors_by_target={
            "Sign In button": [
                {
                    "selector_type": "css",
                    "selector_value": "[data-testid='signin']",
                    "stability_rating": "high",
                    "reasoning": "test-intent attribute",
                },
                {
                    "selector_type": "css",
                    "selector_value": "button[aria-label='Sign In']",
                    "stability_rating": "high",
                    "reasoning": "aria-label",
                },
                {
                    "selector_type": "xpath",
                    "selector_value": "//nav//button[contains(text(),'Sign In')]",
                    "stability_rating": "medium",
                    "reasoning": "visible text",
                },
            ]
        }
    )

    selectors = generate_selectors("Sign In button", "dom context here", client=fake, model="fake")

    assert [s.selector_value for s in selectors] == [
        "[data-testid='signin']",
        "button[aria-label='Sign In']",
        "//nav//button[contains(text(),'Sign In')]",
    ]
    assert [s.stability_rating.value for s in selectors] == ["high", "high", "medium"]


def test_generate_selectors_retry_hint_switches_to_healed_script():
    fake = FakeLLM(
        selectors_by_target={
            "Sign In button": [
                {
                    "selector_type": "css",
                    "selector_value": "#old-signin",
                    "stability_rating": "low",
                    "reasoning": "stale",
                }
            ]
        },
        healed_selectors_by_target={
            "Sign In button": [
                {
                    "selector_type": "css",
                    "selector_value": "#new-signin",
                    "stability_rating": "high",
                    "reasoning": "found on re-analysis",
                }
            ]
        },
    )

    first_pass = generate_selectors("Sign In button", "dom context", client=fake, model="fake")
    assert first_pass[0].selector_value == "#old-signin"

    healed = generate_selectors(
        "Sign In button",
        "dom context (post-failure)",
        client=fake,
        model="fake",
        retry_hint_selectors=first_pass,
    )
    assert healed[0].selector_value == "#new-signin"
