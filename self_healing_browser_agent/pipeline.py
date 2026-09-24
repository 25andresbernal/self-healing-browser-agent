"""Shared step-execution pipeline.

Both `journey-agent build` (parse English -> drive -> emit journey.json)
and `journey-agent run` (re-drive an existing journey.json) walk the same
loop: dismiss overlays, act, fall back through ranked selectors, heal via
runtime re-analysis if every selector misses, screenshot, record. This
module is that loop, factored out so both commands -- and the offline
demo -- share one implementation instead of two copies that drift.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .browser_engine import BrowserEngine, ExecutionResult
from .dom_extractor import build_context
from .llm import LLMClient
from .models import ActionType, ParsedStep, ResolvedStep, Selector, StepStatus
from .selector_generator import generate_selectors


def resolve_step(
    step: ParsedStep,
    engine: BrowserEngine,
    *,
    client: LLMClient,
    model: str,
    screenshots_dir: Path,
    take_screenshots: bool,
    initial_selectors: Optional[list[Selector]] = None,
) -> ResolvedStep:
    """Execute one step and package the result as a ResolvedStep.

    `initial_selectors`, when given, skips the first selector-generation
    call and tries those directly -- this is how `run` re-drives a
    journey.json's already-ranked identifiers instead of asking the LLM
    to re-derive them. Runtime re-analysis (healing) still fires if every
    initial selector fails, exactly as it does on a fresh `build`.

    Wraps everything in a catch-all so one bad step never crashes the
    whole run; it comes back as `unresolved` and the pipeline continues.
    """

    action_name = _action_name(step)
    dismissed: list[str] = []
    try:
        if step.action_type != ActionType.navigate:
            dismissed = engine.dismiss_overlays()

        if step.action_type == ActionType.navigate:
            if not step.value:
                return _unresolved(step, action_name, "navigate step has no URL")
            result = engine.navigate(step.value)
            screenshot_path = _capture_screenshot(
                engine, screenshots_dir, step, take_screenshots
            )
            return ResolvedStep(
                step_number=step.step_number,
                action_name=action_name,
                action_type=step.action_type,
                target_description=step.target_description,
                value=step.value,
                selectors=[],
                used_selector=None,
                healed=False,
                status=result.status,
                error=result.error,
                screenshot_path=screenshot_path,
                dismissed_overlays=dismissed,
            )

        # Interactive action: use the caller's selectors if given, else ask
        # the LLM to rank fresh ones from the live DOM context.
        if initial_selectors:
            selectors = initial_selectors
        else:
            dom_context = build_context(engine.page, step.target_description)
            selectors = generate_selectors(
                step.target_description,
                dom_context,
                client=client,
                model=model,
            )

        def regenerate(failed: list[Selector]) -> list[Selector]:
            fresh_context = build_context(engine.page, step.target_description)
            return generate_selectors(
                step.target_description,
                fresh_context,
                client=client,
                model=model,
                retry_hint_selectors=failed,
            )

        result: ExecutionResult = engine.execute_action(
            step, selectors, regenerate=regenerate
        )

        screenshot_path = _capture_screenshot(
            engine, screenshots_dir, step, take_screenshots
        )

        # Prefer the merged "selectors_tried" list so output JSON shows
        # everything the run evaluated (including healed ones).
        final_selectors = (
            result.selectors_tried if result.selectors_tried else selectors
        )

        return ResolvedStep(
            step_number=step.step_number,
            action_name=action_name,
            action_type=step.action_type,
            target_description=step.target_description,
            value=step.value,
            selectors=final_selectors,
            used_selector=result.used_selector,
            healed=result.healed,
            status=result.status,
            error=result.error,
            screenshot_path=screenshot_path,
            dismissed_overlays=dismissed,
        )
    except Exception as exc:  # one flaky step never kills the run
        return _unresolved(step, action_name, f"{type(exc).__name__}: {exc}")


def _unresolved(step: ParsedStep, action_name: str, error: str) -> ResolvedStep:
    return ResolvedStep(
        step_number=step.step_number,
        action_name=action_name,
        action_type=step.action_type,
        target_description=step.target_description,
        value=step.value,
        selectors=[],
        used_selector=None,
        healed=False,
        status=StepStatus.unresolved,
        error=error,
    )


def _action_name(step: ParsedStep) -> str:
    verb = step.action_type.value.capitalize()
    return f"{verb}: {step.target_description}"


def _capture_screenshot(
    engine: BrowserEngine,
    screenshots_dir: Path,
    step: ParsedStep,
    take: bool,
) -> Optional[str]:
    if not take:
        return None
    path = screenshots_dir / f"step_{step.step_number:02d}.png"
    return engine.screenshot(path)


def print_step_result(step: ResolvedStep) -> None:
    marker = {
        StepStatus.resolved: "OK ",
        StepStatus.unresolved: "FAIL",
        StepStatus.skipped: "SKIP",
    }[step.status]
    tag = " (healed)" if step.healed else ""
    line = f"[{marker}{tag}] step {step.step_number}: {step.action_name}"
    if step.used_selector:
        line += f"  -> {step.used_selector.selector_value}"
    if step.error:
        line += f"  ({step.error})"
    print(line)


def print_summary(meta: dict) -> None:
    print()
    print("=" * 60)
    print("RUN SUMMARY")
    print("=" * 60)
    print(f"  total steps        : {meta['total_steps']}")
    print(f"  resolved           : {meta['resolved_steps']}")
    print(f"  unresolved         : {meta['unresolved_steps']}")
    print(f"  healed at runtime  : {meta['healed_steps']}")
    print(f"  avg stability      : {meta['average_selector_stability']}")
    print(f"  model              : {meta['model']}")
    print(f"  run_id             : {meta['run_id']}")
    print("=" * 60)
