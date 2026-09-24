"""Offline demo: the full build pipeline against a bundled fixture site.

`journey-agent demo` needs no API key and no network access. It drives a
real headless Chromium through a small two-page fixture site bundled
under `examples/site/`, using a `FakeLLM` with a canned step parse and
canned selector rankings instead of a live Anthropic call.

One step is scripted to demonstrate self-healing end to end: the
canned selectors for the newsletter button are all wrong (as if a
redesign had renamed the button's `data-testid`), so every fallback
misses and the pipeline falls through to runtime re-analysis, where a
second canned answer -- keyed to the "this is a re-analysis" retry
hint -- returns a selector that actually matches the live DOM. The run
summary reports that step as `healed: true`.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .browser_engine import BrowserEngine
from .journey_io import build_journey_config, write_journey_config
from .llm import FakeLLM
from .models import ResolvedStep
from .pipeline import print_step_result, print_summary, resolve_step
from .step_parser import parse_steps

SITE_DIR = Path(__file__).resolve().parent.parent / "examples" / "site"

DEMO_DESCRIPTION = (
    "Open the Fixture Co. home page, click the About link, click the "
    "newsletter signup button, then verify the confirmation message appears."
)

_PARSED_STEPS = {
    "steps": [
        {
            "step_number": 1,
            "action_type": "navigate",
            "target_description": "the fixture site home page",
            "value": None,  # filled in at run time with the file:// URL
        },
        {
            "step_number": 2,
            "action_type": "click",
            "target_description": "About link in the navigation",
            "value": None,
        },
        {
            "step_number": 3,
            "action_type": "click",
            "target_description": "Join the newsletter button",
            "value": None,
        },
        {
            "step_number": 4,
            "action_type": "verify",
            "target_description": "newsletter signup confirmation message",
            "value": None,
        },
    ]
}

# First-pass selector rankings the fake selector generator returns.
_SELECTORS_BY_TARGET = {
    "About link in the navigation": [
        {
            "selector_type": "css",
            "selector_value": "[data-testid='about-link']",
            "stability_rating": "high",
            "reasoning": "Test-intent data-testid attribute, added on purpose for automation.",
        },
        {
            "selector_type": "css",
            "selector_value": "a[aria-label='About Fixture Co.']",
            "stability_rating": "high",
            "reasoning": "Aria-label is maintained for accessibility and unlikely to drift.",
        },
        {
            "selector_type": "css",
            "selector_value": "nav a:has-text('About')",
            "stability_rating": "medium",
            "reasoning": "Visible text is readable but breaks if the copy changes.",
        },
    ],
    # These three are deliberately wrong -- as if scraped before a
    # redesign renamed the button -- so every fallback misses and
    # execute_action falls through to the regenerate() callback.
    "Join the newsletter button": [
        {
            "selector_type": "css",
            "selector_value": "[data-testid='newsletter-signup-btn']",
            "stability_rating": "high",
            "reasoning": "Stale data-testid from before the button was renamed.",
        },
        {
            "selector_type": "css",
            "selector_value": "#newsletter-cta",
            "stability_rating": "medium",
            "reasoning": "Stale id that no longer exists on the page.",
        },
        {
            "selector_type": "css",
            "selector_value": "button.old-signup-button",
            "stability_rating": "low",
            "reasoning": "Stale class name, structural and already fragile.",
        },
    ],
    "newsletter signup confirmation message": [
        {
            "selector_type": "css",
            "selector_value": "[data-testid='signup-confirmation']",
            "stability_rating": "high",
            "reasoning": "Test-intent data-testid attribute on the confirmation text.",
        },
        {
            "selector_type": "css",
            "selector_value": "#signup-confirmation",
            "stability_rating": "high",
            "reasoning": "Meaningful, hand-written id.",
        },
        {
            "selector_type": "css",
            "selector_value": "p:has-text('You are signed up')",
            "stability_rating": "medium",
            "reasoning": "Unique visible text, but breaks if the copy changes.",
        },
    ],
}

# Runtime re-analysis answer for the target that was seeded to fail --
# this is what the healing pass returns once it looks at the live DOM.
_HEALED_SELECTORS_BY_TARGET = {
    "Join the newsletter button": [
        {
            "selector_type": "css",
            "selector_value": "[data-testid='signup-button']",
            "stability_rating": "high",
            "reasoning": "Re-analysis of the live DOM found the button's current data-testid.",
        },
        {
            "selector_type": "css",
            "selector_value": "#signup-button",
            "stability_rating": "high",
            "reasoning": "Meaningful id present on the current page.",
        },
        {
            "selector_type": "css",
            "selector_value": "button:has-text('Join the newsletter')",
            "stability_rating": "medium",
            "reasoning": "Unique visible text on the current page.",
        },
    ]
}


def build_demo_llm() -> FakeLLM:
    parsed = {
        "steps": [dict(step) for step in _PARSED_STEPS["steps"]],
    }
    parsed["steps"][0]["value"] = SITE_DIR.joinpath("index.html").resolve().as_uri()
    return FakeLLM(
        parsed_steps=parsed,
        selectors_by_target=_SELECTORS_BY_TARGET,
        healed_selectors_by_target=_HEALED_SELECTORS_BY_TARGET,
        model_name="fake-demo-v1",
    )


def run_demo(
    *,
    output: Path,
    headful: bool = False,
    take_screenshots: bool = True,
) -> int:
    """Run the offline demo journey end to end. Returns a process exit code."""

    if not SITE_DIR.joinpath("index.html").exists():
        print(f"[demo] fixture site not found under {SITE_DIR}")
        return 1

    client = build_demo_llm()
    model = client.model_name
    run_id = "demo-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    screenshots_dir = output.parent / "screenshots" / run_id

    print(f"[demo] run_id: {run_id}")
    print(f"[demo] model : {model}  (FakeLLM -- no API key or network used)")
    print(f"[demo] site  : {SITE_DIR}")
    print()
    print(f"English description:\n  {DEMO_DESCRIPTION}\n")

    print("[demo] parsing natural-language steps (canned)...")
    parsed = parse_steps(DEMO_DESCRIPTION, client=client, model=model)
    steps = list(parsed.steps)
    print(f"[demo] parsed {len(steps)} step(s)")
    for s in steps:
        suffix = f"  = {s.value!r}" if s.value else ""
        print(f"  {s.step_number}. [{s.action_type.value}] {s.target_description}{suffix}")
    print()

    engine = BrowserEngine(headless=not headful, auto_dismiss=True)
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
                take_screenshots=take_screenshots,
            )
            resolved_steps.append(resolved)
            print_step_result(resolved)
    finally:
        engine.close()

    config = build_journey_config(
        journey_name="Fixture Co. demo journey",
        target_url=SITE_DIR.joinpath("index.html").resolve().as_uri(),
        resolved_steps=resolved_steps,
        model=model,
        run_id=run_id,
    )
    write_journey_config(config, output)
    print()
    print(f"[demo] wrote journey.json -> {output}")
    print_summary(config.metadata.model_dump())

    healed = any(s.healed for s in resolved_steps)
    unresolved = any(s.status.value == "unresolved" for s in resolved_steps)
    print()
    if healed:
        print(
            "[demo] the newsletter-button step failed on all 3 seeded "
            "selectors and was healed by a runtime re-analysis call -- "
            "see step 3 above (healed) and the journey.json 'healed' field."
        )
    return 1 if unresolved else 0
