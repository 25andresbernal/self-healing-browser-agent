"""self-healing-browser-agent CLI.

Takes a plain-English description of a browser journey and a target
URL, walks the site with Playwright, asks Claude to generate ranked
stable selectors for each step, and writes a portable journey.json file
with per-step screenshots.

Subcommands:
    journey-agent build   English description -> journey.json (drives the browser)
    journey-agent run     Re-execute an existing journey.json, with self-healing
    journey-agent demo    Offline demo against a bundled fixture site, no API key
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path

from .browser_engine import BrowserEngine
from .journey_io import build_journey_config, write_journey_config
from .llm import DEFAULT_MODEL, AnthropicLLM, default_model
from .models import ParsedStep, ResolvedStep
from .pipeline import print_step_result, print_summary, resolve_step
from .runner import run_journey
from .step_parser import parse_steps


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.command == "build":
        return _cmd_build(args)
    if args.command == "run":
        return _cmd_run(args)
    if args.command == "demo":
        return _cmd_demo(args)
    _err(f"unknown command {args.command!r}")
    return 2


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------


def _cmd_build(args: argparse.Namespace) -> int:
    raw_steps = _load_steps_text(args)
    if not raw_steps:
        _err("no steps provided -- use --steps or --steps-file")
        return 2

    client = _require_anthropic_client()
    if client is None:
        return 2
    model = args.model

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    screenshots_dir = Path(args.output).parent / "screenshots" / run_id

    _info(f"run_id: {run_id}")
    _info(f"model : {model}")

    _info("parsing natural-language steps...")
    try:
        parsed = parse_steps(raw_steps, client=client, model=model)
    except Exception as exc:
        _err(f"step parser failed: {exc}")
        return 1
    steps: list[ParsedStep] = list(parsed.steps)
    _info(f"parsed {len(steps)} step(s)")
    for s in steps:
        suffix = f"  = {s.value!r}" if s.value else ""
        _info(f"  {s.step_number}. [{s.action_type.value}] {s.target_description}{suffix}")

    engine = BrowserEngine(
        headless=not args.headful,
        auto_dismiss=not args.no_auto_dismiss,
    )
    engine.start()

    resolved_steps: list[ResolvedStep] = []
    try:
        for step in steps:
            resolved = resolve_step(
                step,
                engine,
                client=client,
                model=model,
                screenshots_dir=screenshots_dir,
                take_screenshots=not args.no_screenshots,
            )
            resolved_steps.append(resolved)
            print_step_result(resolved)
    finally:
        engine.close()

    config = build_journey_config(
        journey_name=args.journey_name or _derive_journey_name(args.url),
        target_url=args.url,
        resolved_steps=resolved_steps,
        model=model,
        run_id=run_id,
    )
    output_path = Path(args.output)
    write_journey_config(config, output_path)
    _info(f"wrote journey.json -> {output_path}")
    print_summary(config.metadata.model_dump())
    return 0


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


def _cmd_run(args: argparse.Namespace) -> int:
    client = _require_anthropic_client()
    if client is None:
        return 2
    journey_path = Path(args.journey)
    if not journey_path.exists():
        _err(f"journey file not found: {journey_path}")
        return 2
    output_path = Path(args.output) if args.output else journey_path
    return run_journey(
        journey_path=journey_path,
        output=output_path,
        client=client,
        model=args.model,
        headful=args.headful,
        take_screenshots=not args.no_screenshots,
        auto_dismiss=not args.no_auto_dismiss,
    )


# ---------------------------------------------------------------------------
# demo
# ---------------------------------------------------------------------------


def _cmd_demo(args: argparse.Namespace) -> int:
    from .demo import run_demo

    return run_demo(
        output=Path(args.output),
        headful=args.headful,
        take_screenshots=not args.no_screenshots,
    )


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _require_anthropic_client():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        _err(
            "ANTHROPIC_API_KEY is not set. Export it before running, e.g.:\n"
            "    export ANTHROPIC_API_KEY=sk-ant-...\n"
            "Or try the offline demo instead: journey-agent demo"
        )
        return None
    return AnthropicLLM(api_key)


def _load_steps_text(args: argparse.Namespace) -> str:
    if args.steps:
        return args.steps
    if args.steps_file:
        return Path(args.steps_file).read_text()
    return ""


def _derive_journey_name(url: str) -> str:
    return url.replace("https://", "").replace("http://", "").rstrip("/")


def _info(msg: str) -> None:
    print(f"[journey-agent] {msg}")


def _err(msg: str) -> None:
    print(f"[journey-agent] error: {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="journey-agent",
        description=(
            "Parse an English description of a browser journey, drive it with "
            "Playwright, and emit a portable journey.json with ranked, "
            "self-healing selectors."
        ),
    )
    sub = p.add_subparsers(dest="command", required=True)

    build_p = sub.add_parser(
        "build", help="Parse an English description and drive the browser to build a journey.json."
    )
    build_p.add_argument("--url", required=True, help="Target URL to start the journey on.")
    steps_group = build_p.add_mutually_exclusive_group()
    steps_group.add_argument("--steps", help="Inline natural-language description of the journey.")
    steps_group.add_argument(
        "--steps-file", help="Path to a text file containing the journey steps."
    )
    build_p.add_argument(
        "--output",
        default="output/journey.json",
        help="Where to write the generated journey.json. Default: output/journey.json",
    )
    build_p.add_argument("--journey-name", help="Human-readable name. Defaults to the target URL.")
    build_p.add_argument(
        "--model",
        default=default_model(),
        help=f"Anthropic model name. Default: {DEFAULT_MODEL} (or $ANTHROPIC_MODEL).",
    )
    build_p.add_argument("--headful", action="store_true", help="Show the browser window.")
    build_p.add_argument("--no-screenshots", action="store_true", help="Skip per-step screenshots.")
    build_p.add_argument(
        "--no-auto-dismiss", action="store_true", help="Disable automatic cookie-banner dismissal."
    )

    run_p = sub.add_parser("run", help="Re-execute an existing journey.json, with self-healing.")
    run_p.add_argument("--journey", required=True, help="Path to an existing journey.json.")
    run_p.add_argument(
        "--output", help="Where to write the updated journey.json. Default: overwrite --journey."
    )
    run_p.add_argument(
        "--model",
        default=default_model(),
        help=f"Anthropic model name, used only if healing is needed. Default: {DEFAULT_MODEL}.",
    )
    run_p.add_argument("--headful", action="store_true", help="Show the browser window.")
    run_p.add_argument("--no-screenshots", action="store_true", help="Skip per-step screenshots.")
    run_p.add_argument(
        "--no-auto-dismiss", action="store_true", help="Disable automatic cookie-banner dismissal."
    )

    demo_p = sub.add_parser(
        "demo", help="Run the offline demo against a bundled fixture site. No API key needed."
    )
    demo_p.add_argument(
        "--output",
        default="output/demo/journey.json",
        help="Where to write the demo journey.json. Default: output/demo/journey.json",
    )
    demo_p.add_argument("--headful", action="store_true", help="Show the browser window.")
    demo_p.add_argument("--no-screenshots", action="store_true", help="Skip per-step screenshots.")

    return p.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
