"""journey.json schema validation: build, write, and reload."""

from __future__ import annotations

import json
from pathlib import Path

from self_healing_browser_agent.journey_io import (
    build_journey_config,
    load_journey_config,
    write_journey_config,
)
from self_healing_browser_agent.models import (
    ActionType,
    JourneyConfig,
    ResolvedStep,
    Selector,
    SelectorType,
    StabilityRating,
    StepStatus,
)


def _resolved_step(
    step_number: int,
    *,
    stability: StabilityRating = StabilityRating.high,
    healed: bool = False,
) -> ResolvedStep:
    selector = Selector(
        selector_type=SelectorType.css,
        selector_value=f"#step-{step_number}",
        stability_rating=stability,
        reasoning="fixture",
    )
    return ResolvedStep(
        step_number=step_number,
        action_name=f"Click: step {step_number}",
        action_type=ActionType.click,
        target_description=f"step {step_number}",
        selectors=[selector],
        used_selector=selector,
        healed=healed,
        status=StepStatus.resolved,
        screenshot_path=f"output/screenshots/step_{step_number:02d}.png",
    )


def test_build_journey_config_computes_metadata():
    steps = [
        _resolved_step(1, stability=StabilityRating.high),
        _resolved_step(2, stability=StabilityRating.medium),
        _resolved_step(3, stability=StabilityRating.high, healed=True),
    ]
    config = build_journey_config(
        journey_name="Test journey",
        target_url="https://example.com",
        resolved_steps=steps,
        model="fake-model",
        run_id="run-1",
    )

    assert config.metadata.total_steps == 3
    assert config.metadata.resolved_steps == 3
    assert config.metadata.unresolved_steps == 0
    assert config.metadata.healed_steps == 1
    # (high + medium + high) / 3 = (3 + 2 + 3) / 3 = 2.67 -> "high" (>= 2.5)
    assert config.metadata.average_selector_stability == "high"
    assert config.steps[0].action_type == "click"
    assert config.steps[0].identifiers[0].type == "css"
    assert config.steps[0].identifiers[0].is_primary is True


def test_write_and_load_round_trip(tmp_path: Path):
    steps = [_resolved_step(1)]
    config = build_journey_config(
        journey_name="Round trip",
        target_url="https://example.com",
        resolved_steps=steps,
        model="fake-model",
        run_id="run-2",
    )
    out = tmp_path / "journey.json"
    write_journey_config(config, out)

    raw = json.loads(out.read_text())
    assert raw["journey_name"] == "Round trip"
    assert raw["steps"][0]["identifiers"][0]["value"] == "#step-1"

    reloaded = load_journey_config(out)
    assert isinstance(reloaded, JourneyConfig)
    assert reloaded.journey_name == "Round trip"
    assert reloaded.steps[0].target_description == "step 1"
    assert reloaded.metadata.run_id == "run-2"


def test_average_stability_is_na_with_no_used_selectors():
    step = ResolvedStep(
        step_number=1,
        action_name="Navigate: home",
        action_type=ActionType.navigate,
        target_description="home",
        value="https://example.com",
        status=StepStatus.resolved,
    )
    config = build_journey_config(
        journey_name="Nav only",
        target_url="https://example.com",
        resolved_steps=[step],
        model="fake-model",
        run_id="run-3",
    )
    assert config.metadata.average_selector_stability == "n/a"
