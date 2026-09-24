"""Break a selector on the fixture site and confirm the runner heals it.

Two mechanisms are exercised, matching the two layers described in the
README: ranked fallback (a later candidate in the same list works) and
runtime re-analysis (every candidate misses, so the browser engine asks
for a fresh ranked list from the live DOM, here stood in for by a fake
"regenerate" callback the way `pipeline.resolve_step` wires one to
`selector_generator.generate_selectors` with a FakeLLM in `demo.py`).
"""

from __future__ import annotations

from pathlib import Path

from self_healing_browser_agent.browser_engine import BrowserEngine
from self_healing_browser_agent.models import (
    ActionType,
    ParsedStep,
    Selector,
    SelectorType,
    StabilityRating,
    StepStatus,
)

SITE_DIR = Path(__file__).resolve().parent.parent / "examples" / "site"


def site_url(name: str) -> str:
    return SITE_DIR.joinpath(name).resolve().as_uri()


def _step(target_description: str) -> ParsedStep:
    return ParsedStep(
        step_number=1,
        action_type=ActionType.click,
        target_description=target_description,
        value=None,
    )


def _selector(value: str, stability: StabilityRating = StabilityRating.low) -> Selector:
    return Selector(
        selector_type=SelectorType.css,
        selector_value=value,
        stability_rating=stability,
        reasoning="test fixture",
    )


def test_fallback_heals_when_a_later_ranked_selector_still_matches(page):
    page.goto(site_url("about.html"))
    engine = BrowserEngine()
    engine._page = page  # reuse the already-navigated page from the fixture

    selectors = [
        _selector("#this-id-does-not-exist"),
        _selector("[data-testid='signup-button']", StabilityRating.high),
    ]
    result = engine.execute_action(_step("Join the newsletter button"), selectors)

    assert result.status == StepStatus.resolved
    assert result.healed is False
    assert result.used_selector.selector_value == "[data-testid='signup-button']"


def test_runtime_reanalysis_heals_when_every_ranked_selector_misses(page):
    page.goto(site_url("about.html"))
    engine = BrowserEngine()
    engine._page = page

    broken_selectors = [
        _selector("#stale-signup-1"),
        _selector("[data-testid='old-signup-btn']"),
        _selector(".no-longer-exists"),
    ]

    def regenerate(failed: list[Selector]) -> list[Selector]:
        # Stand-in for selector_generator.generate_selectors(..., client=FakeLLM(...)):
        # a real re-analysis call would look at the live DOM and return this.
        assert failed == broken_selectors
        return [_selector("[data-testid='signup-button']", StabilityRating.high)]

    result = engine.execute_action(
        _step("Join the newsletter button"), broken_selectors, regenerate=regenerate
    )

    assert result.status == StepStatus.resolved
    assert result.healed is True
    assert result.used_selector.selector_value == "[data-testid='signup-button']"
    # The healed journey.json keeps the whole chain: the 3 that failed,
    # then the one the re-analysis produced.
    assert [s.selector_value for s in result.selectors_tried] == [
        "#stale-signup-1",
        "[data-testid='old-signup-btn']",
        ".no-longer-exists",
        "[data-testid='signup-button']",
    ]


def test_step_stays_unresolved_when_reanalysis_also_misses(page):
    page.goto(site_url("about.html"))
    engine = BrowserEngine()
    engine._page = page

    broken_selectors = [_selector("#stale-1"), _selector("#stale-2")]

    def regenerate(failed: list[Selector]) -> list[Selector]:
        return [_selector("#still-wrong")]

    result = engine.execute_action(
        _step("Join the newsletter button"), broken_selectors, regenerate=regenerate
    )

    assert result.status == StepStatus.unresolved
    assert result.healed is False
    assert result.error
