"""ObservePoint Journey AI -- MVP CLI.

Takes a plain-English description of a user journey and a target URL,
walks the site with Playwright, asks Claude to generate ranked stable
selectors for each step, and writes an ObservePoint-compatible Journey
JSON file.

Usage:
    python main.py \
        --url "https://example.com" \
        --steps "Navigate to example.com, click the More information link" \
        --output output/example.json
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from anthropic import Anthropic

from browser_engine import BrowserEngine, ExecutionResult
from config_builder import build_journey_config, write_journey_config
from dom_extractor import build_context
from models import (
    ActionType,
    ParsedStep,
    ResolvedStep,
    Selector,
    StepStatus,
)
from selector_generator import generate_selectors
from step_parser import parse_steps


DEFAULT_MODEL = "claude-opus-4-6"


def main() -> int:
    args = _parse_args()

    # 1. Validate inputs
    raw_steps = _load_steps_text(args)
    if not raw_steps:
        _err("no steps provided -- use --steps or --steps-file")
        return 2

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        _err(
            "ANTHROPIC_API_KEY is not set. Export it before running, e.g.:\n"
            "    export ANTHROPIC_API_KEY=sk-ant-..."
        )
        return 2

    client = Anthropic(api_key=api_key)
    model = args.model
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    screenshots_dir = Path(args.output).parent / "screenshots" / run_id

    _info(f"run_id: {run_id}")
    _info(f"model : {model}")

    # 2. Parse natural language into structured steps
    _info("parsing natural-language steps...")
    try:
        parsed = parse_steps(raw_steps, client=client, model=model)
    except Exception as exc:
        _err(f"step parser failed: {exc}")
        return 1
    steps: list[ParsedStep] = list(parsed.steps)
    _info(f"parsed {len(steps)} step(s)")
    for s in steps:
        _info(f"  {s.step_number}. [{s.action_type.value}] {s.target_description}"
              + (f"  = {s.value!r}" if s.value else ""))

    # 3. Walk through the pipeline
    engine = BrowserEngine(
        headless=not args.headful,
        auto_dismiss=not args.no_auto_dismiss,
    )
    engine.start()

    resolved_steps: list[ResolvedStep] = []
    try:
        for step in steps:
            resolved = _run_step(
                step=step,
                engine=engine,
                client=client,
                model=model,
                screenshots_dir=screenshots_dir,
                take_screenshots=not args.no_screenshots,
            )
            resolved_steps.append(resolved)
            _print_step_result(resolved)
    finally:
        engine.close()

    # 4. Build + write the final journey config
    config = build_journey_config(
        journey_name=args.journey_name or _derive_journey_name(args.url),
        target_url=args.url,
        resolved_steps=resolved_steps,
        model=model,
        run_id=run_id,
    )
    output_path = Path(args.output)
    write_journey_config(config, output_path)
    _info(f"wrote journey config -> {output_path}")
    _print_summary(config.metadata.model_dump())
    return 0


# ---------------------------------------------------------------------------
# Step orchestration
# ---------------------------------------------------------------------------


def _run_step(
    *,
    step: ParsedStep,
    engine: BrowserEngine,
    client: Anthropic,
    model: str,
    screenshots_dir: Path,
    take_screenshots: bool,
) -> ResolvedStep:
    """Execute a single step and package the result as a ResolvedStep.

    Wraps everything in try/except (Resilience §7) so a single bad step
    never crashes the whole run.
    """

    action_name = _action_name(step)
    dismissed: list[str] = []
    try:
        # Cookie banners / modals get cleared before we look at the page
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
                value=step.value,
                selectors=[],
                used_selector=None,
                healed=False,
                status=result.status,
                error=result.error,
                screenshot_path=screenshot_path,
                dismissed_overlays=dismissed,
            )

        # Interactive action: build DOM context and generate selectors
        dom_context = build_context(engine.page, step.target_description)
        selectors = generate_selectors(
            step.target_description,
            dom_context,
            client=client,
            model=model,
        )

        # Execute with runtime self-healing wired into the browser engine
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
            value=step.value,
            selectors=final_selectors,
            used_selector=result.used_selector,
            healed=result.healed,
            status=result.status,
            error=result.error,
            screenshot_path=screenshot_path,
            dismissed_overlays=dismissed,
        )
    except Exception as exc:
        # Catch-all so one flaky step doesn't kill the pipeline
        return _unresolved(
            step, action_name, f"{type(exc).__name__}: {exc}"
        )


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _unresolved(step: ParsedStep, action_name: str, error: str) -> ResolvedStep:
    return ResolvedStep(
        step_number=step.step_number,
        action_name=action_name,
        action_type=step.action_type,
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


def _load_steps_text(args: argparse.Namespace) -> str:
    if args.steps:
        return args.steps
    if args.steps_file:
        return Path(args.steps_file).read_text()
    return ""


def _derive_journey_name(url: str) -> str:
    # strip protocol + trailing slashes for a readable default
    return url.replace("https://", "").replace("http://", "").rstrip("/")


def _print_step_result(step: ResolvedStep) -> None:
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


def _print_summary(meta: dict) -> None:
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


def _info(msg: str) -> None:
    print(f"[mvp] {msg}")


def _err(msg: str) -> None:
    print(f"[mvp] error: {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="observepoint-journey-ai",
        description=(
            "Generate an ObservePoint-compatible Journey JSON from a "
            "plain-English description of a user flow."
        ),
    )
    p.add_argument(
        "--url",
        required=True,
        help="Target URL to start the journey on.",
    )
    steps_group = p.add_mutually_exclusive_group()
    steps_group.add_argument(
        "--steps",
        help="Inline natural-language description of the journey steps.",
    )
    steps_group.add_argument(
        "--steps-file",
        help="Path to a text file containing the journey steps.",
    )
    p.add_argument(
        "--output",
        default="output/journey.json",
        help="Path to write the generated Journey JSON. Default: output/journey.json",
    )
    p.add_argument(
        "--journey-name",
        help="Human-readable name for the journey. Defaults to the target URL.",
    )
    p.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Anthropic model name. Default: {DEFAULT_MODEL}",
    )
    p.add_argument(
        "--headful",
        action="store_true",
        help="Show the browser window while running (useful for demos).",
    )
    p.add_argument(
        "--no-screenshots",
        action="store_true",
        help="Skip per-step screenshot capture.",
    )
    p.add_argument(
        "--no-auto-dismiss",
        action="store_true",
        help="Disable automatic cookie-banner / modal dismissal.",
    )
    return p.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
