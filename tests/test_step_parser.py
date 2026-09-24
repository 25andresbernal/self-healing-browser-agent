from __future__ import annotations

from self_healing_browser_agent.llm import FakeLLM
from self_healing_browser_agent.models import ActionType
from self_healing_browser_agent.step_parser import parse_steps


def test_parse_steps_validates_canned_output_into_parsed_steps():
    fake = FakeLLM(
        parsed_steps={
            "steps": [
                {
                    "step_number": 1,
                    "action_type": "navigate",
                    "target_description": "the home page",
                    "value": "https://example.com",
                },
                {
                    "step_number": 2,
                    "action_type": "click",
                    "target_description": "More information link",
                    "value": None,
                },
            ]
        }
    )

    result = parse_steps("go to example.com and click more information", client=fake, model="fake")

    assert [s.step_number for s in result.steps] == [1, 2]
    assert result.steps[0].action_type == ActionType.navigate
    assert result.steps[0].value == "https://example.com"
    assert result.steps[1].action_type == ActionType.click
    assert result.steps[1].value is None
