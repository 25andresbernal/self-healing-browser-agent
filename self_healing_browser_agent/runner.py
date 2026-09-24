"""`journey-agent run`: re-drive an existing journey.json with self-healing.

Loads a journey.json, tries each step's already-ranked identifiers in
order exactly as `build` would have, and only calls the LLM for a
runtime re-analysis if every one of them fails on the current page --
the same healing path `build` uses on a first pass. Useful for
re-checking a journey against a site that may have changed since it was
authored, without re-parsing the English description.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .browser_engine import BrowserEngine
from .journey_io import build_journey_config, load_journey_config, write_journey_config
from .llm import LLMClient
from .models import ActionType, JourneyStep, ParsedStep, ResolvedStep, Selector, SelectorType
from .pipeline import print_step_result, print_summary, resolve_step


def run_journey(
    *,
    journey_path: Path,
    output: Path,
    client: LLMClient,
    model: str,
    headful: bool = False,
    take_screenshots: bool = True,
    auto_dismiss: bool = True,
) -> int:
    """Re-execute an existing journey.json. Returns a process exit code."""

    config = load_journey_config(journey_path)
    print(f"[run] loaded {len(config.steps)} step(s) from {journey_path}")
    print(f"[run] target: {config.target_url}")

    run_id = "rerun-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    screenshots_dir = output.parent / "screenshots" / run_id

    engine = BrowserEngine(headless=not headful, auto_dismiss=auto_dismiss)
    engine.start()

    resolved_steps: list[ResolvedStep] = []
    try:
        for journey_step in config.steps:
            parsed_step = _to_parsed_step(journey_step)
            initial_selectors = _to_selectors(journey_step)
            resolved = resolve_step(
                parsed_step,
                engine,
                client=client,
                model=model,
                screenshots_dir=screenshots_dir,
                take_screenshots=take_screenshots,
                initial_selectors=initial_selectors or None,
            )
            resolved_steps.append(resolved)
            print_step_result(resolved)
    finally:
        engine.close()

    updated = build_journey_config(
        journey_name=config.journey_name,
        target_url=config.target_url,
        resolved_steps=resolved_steps,
        model=model,
        run_id=run_id,
    )
    write_journey_config(updated, output)
    print()
    print(f"[run] wrote journey.json -> {output}")
    print_summary(updated.metadata.model_dump())

    unresolved = any(s.status.value == "unresolved" for s in resolved_steps)
    return 1 if unresolved else 0


def _to_parsed_step(step: JourneyStep) -> ParsedStep:
    return ParsedStep(
        step_number=step.step_number,
        action_type=ActionType(step.action_type),
        target_description=step.target_description,
        value=step.value,
    )


def _to_selectors(step: JourneyStep) -> list[Selector]:
    return [
        Selector(
            selector_type=SelectorType(identifier.type),
            selector_value=identifier.value,
            stability_rating=identifier.stability,
            reasoning="carried over from the existing journey.json",
        )
        for identifier in step.identifiers
    ]
