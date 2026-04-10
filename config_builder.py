"""Assemble the final Journey JSON in ObservePoint-compatible format."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from models import (
    JourneyConfig,
    JourneyIdentifier,
    JourneyMetadata,
    JourneyStep,
    ResolvedStep,
    StabilityRating,
    StepStatus,
)


# ObservePoint uses TitleCase action names (Navigate, Click, Input, ...);
# the parser stores them lowercase internally. This map bridges the two.
_ACTION_NAME_MAP = {
    "navigate": "Navigate",
    "click": "Click",
    "input": "Input",
    "select": "Select",
    "check": "Check",
    "uncheck": "Uncheck",
    "wait": "Watch",  # ObservePoint calls explicit delays "Watch"
    "verify": "Watch",  # MVP treats verify like a wait-for-visible
}

# ObservePoint identifier types
_SELECTOR_TYPE_MAP = {
    "css": "CSS Selector",
    "xpath": "XPath",
}


_STABILITY_RANK = {
    StabilityRating.high: 3,
    StabilityRating.medium: 2,
    StabilityRating.low: 1,
}


def build_journey_config(
    *,
    journey_name: str,
    target_url: str,
    resolved_steps: list[ResolvedStep],
    model: str,
    run_id: str,
    viewport: str = "1920x1080",
) -> JourneyConfig:
    journey_steps = [_to_journey_step(s) for s in resolved_steps]
    metadata = _build_metadata(
        resolved_steps=resolved_steps,
        model=model,
        run_id=run_id,
        viewport=viewport,
    )
    return JourneyConfig(
        journey_name=journey_name,
        generated_at=datetime.now(tz=timezone.utc),
        target_url=target_url,
        steps=journey_steps,
        metadata=metadata,
    )


def write_journey_config(config: JourneyConfig, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = config.model_dump(mode="json")
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=False))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _to_journey_step(step: ResolvedStep) -> JourneyStep:
    identifiers: list[JourneyIdentifier] = []
    primary = step.used_selector or (step.selectors[0] if step.selectors else None)

    for selector in step.selectors:
        identifiers.append(
            JourneyIdentifier(
                type=_SELECTOR_TYPE_MAP.get(
                    selector.selector_type.value, "CSS Selector"
                ),
                value=selector.selector_value,
                stability=selector.stability_rating,
                is_primary=(
                    primary is not None
                    and selector.selector_value == primary.selector_value
                    and selector.selector_type == primary.selector_type
                ),
            )
        )

    return JourneyStep(
        step_number=step.step_number,
        action_name=step.action_name,
        action_type=_ACTION_NAME_MAP.get(step.action_type.value, step.action_type.value),
        value=step.value,
        identifiers=identifiers,
        screenshot_path=step.screenshot_path,
        status=step.status,
        healed=step.healed,
        error=step.error,
    )


def _build_metadata(
    *,
    resolved_steps: list[ResolvedStep],
    model: str,
    run_id: str,
    viewport: str,
) -> JourneyMetadata:
    total = len(resolved_steps)
    resolved = sum(1 for s in resolved_steps if s.status == StepStatus.resolved)
    unresolved = sum(1 for s in resolved_steps if s.status == StepStatus.unresolved)
    healed = sum(1 for s in resolved_steps if s.healed)

    rated = [
        s.used_selector.stability_rating
        for s in resolved_steps
        if s.used_selector is not None
    ]
    if rated:
        avg_rank = sum(_STABILITY_RANK[r] for r in rated) / len(rated)
        if avg_rank >= 2.5:
            avg_label = "high"
        elif avg_rank >= 1.5:
            avg_label = "medium"
        else:
            avg_label = "low"
    else:
        avg_label = "n/a"

    return JourneyMetadata(
        total_steps=total,
        resolved_steps=resolved,
        unresolved_steps=unresolved,
        healed_steps=healed,
        average_selector_stability=avg_label,
        viewport=viewport,
        model=model,
        run_id=run_id,
    )
