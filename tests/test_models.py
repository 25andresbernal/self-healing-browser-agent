"""Pydantic model validation for the LLM-facing schemas."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from self_healing_browser_agent.models import (
    ActionType,
    ParsedStep,
    ParsedSteps,
    RankedSelectors,
    Selector,
    SelectorType,
    StabilityRating,
)


def test_parsed_step_requires_a_valid_action_type():
    step = ParsedStep(
        step_number=1,
        action_type="click",
        target_description="Sign In button",
        value=None,
    )
    assert step.action_type == ActionType.click

    with pytest.raises(ValidationError):
        ParsedStep(
            step_number=1,
            action_type="teleport",
            target_description="nowhere",
            value=None,
        )


def test_parsed_steps_wraps_a_list_of_steps():
    parsed = ParsedSteps.model_validate(
        {
            "steps": [
                {
                    "step_number": 1,
                    "action_type": "navigate",
                    "target_description": "home",
                    "value": "https://example.com",
                }
            ]
        }
    )
    assert len(parsed.steps) == 1
    assert parsed.steps[0].action_type == ActionType.navigate


def test_ranked_selectors_enforces_between_one_and_five_entries():
    selector = {
        "selector_type": "css",
        "selector_value": "#x",
        "stability_rating": "high",
        "reasoning": "test",
    }
    RankedSelectors.model_validate({"selectors": [selector]})

    with pytest.raises(ValidationError):
        RankedSelectors.model_validate({"selectors": []})


def test_selector_type_and_stability_are_constrained_enums():
    selector = Selector(
        selector_type=SelectorType.xpath,
        selector_value="//div",
        stability_rating=StabilityRating.low,
        reasoning="structural",
    )
    assert selector.selector_type.value == "xpath"
    assert selector.stability_rating.value == "low"

    with pytest.raises(ValidationError):
        Selector(
            selector_type="css",
            selector_value="#x",
            stability_rating="extremely-high",
            reasoning="not a real rating",
        )
