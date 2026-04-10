"""Playwright wrapper + per-step resilience layer.

This is where most of the "doesn't break so easy" behavior lives:
  - Adaptive waits (Resilience §4)
  - Overlay auto-dismissal (Resilience §3, via resilience.dismiss_common_overlays)
  - Visibility / scroll-into-view checks before acting (Resilience §5)
  - Fallback selector execution + runtime re-analysis (Resilience §2)
  - Step-level try/except so one bad step never crashes the run (Resilience §7)
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Error as PlaywrightError,
    Page,
    Playwright,
    sync_playwright,
)

from models import ActionType, ParsedStep, Selector, SelectorType, StepStatus
from resilience import dismiss_common_overlays


NETWORKIDLE_TIMEOUT_MS = 10_000
ACTION_TIMEOUT_MS = 8_000
DEFAULT_VIEWPORT = {"width": 1920, "height": 1080}


@dataclass
class ExecutionResult:
    status: StepStatus
    used_selector: Optional[Selector] = None
    selectors_tried: list[Selector] = field(default_factory=list)
    healed: bool = False
    error: Optional[str] = None
    dismissed_overlays: list[str] = field(default_factory=list)


class BrowserEngine:
    def __init__(self, *, headless: bool = True, auto_dismiss: bool = True) -> None:
        self._headless = headless
        self._auto_dismiss = auto_dismiss
        self._pw: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self._headless)
        self._context = self._browser.new_context(viewport=DEFAULT_VIEWPORT)
        self._page = self._context.new_page()
        self._page.set_default_timeout(ACTION_TIMEOUT_MS)

    def close(self) -> None:
        for closer in (
            lambda: self._context and self._context.close(),
            lambda: self._browser and self._browser.close(),
            lambda: self._pw and self._pw.stop(),
        ):
            try:
                closer()
            except Exception:  # pragma: no cover
                pass
        self._page = self._context = self._browser = self._pw = None

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("BrowserEngine.start() has not been called")
        return self._page

    # ------------------------------------------------------------------
    # Resilience helpers
    # ------------------------------------------------------------------

    def adaptive_wait(self) -> None:
        """wait_for_load_state('networkidle') with a graceful fallback."""
        try:
            self.page.wait_for_load_state(
                "networkidle", timeout=NETWORKIDLE_TIMEOUT_MS
            )
        except PlaywrightError:
            try:
                self.page.wait_for_load_state(
                    "domcontentloaded", timeout=NETWORKIDLE_TIMEOUT_MS
                )
            except PlaywrightError:
                pass
            time.sleep(0.5)

    def dismiss_overlays(self) -> list[str]:
        if not self._auto_dismiss:
            return []
        return dismiss_common_overlays(self.page)

    def screenshot(self, path: Path) -> Optional[str]:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(path), full_page=False)
            return str(path)
        except Exception as exc:
            print(f"[browser] screenshot failed: {exc}")
            return None

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def navigate(self, url: str) -> ExecutionResult:
        try:
            self.page.goto(url, timeout=30_000)
            self.adaptive_wait()
            return ExecutionResult(status=StepStatus.resolved)
        except Exception as exc:
            return ExecutionResult(
                status=StepStatus.unresolved,
                error=f"navigation failed: {exc}",
            )

    # ------------------------------------------------------------------
    # Action execution
    # ------------------------------------------------------------------

    def execute_action(
        self,
        step: ParsedStep,
        selectors: list[Selector],
        *,
        regenerate: Optional[Callable[[list[Selector]], list[Selector]]] = None,
    ) -> ExecutionResult:
        """Try each selector in order; on total failure, invoke `regenerate`
        once for runtime re-analysis and try again.

        `regenerate` takes the list of failed selectors and returns a fresh
        ranked list. Pass `None` to disable self-healing for this step.
        """

        result = self._try_selectors(step, selectors)
        if result.status == StepStatus.resolved:
            return result

        # All pre-generated selectors failed. If we have a regenerate
        # callback, try one round of runtime re-analysis.
        if regenerate is None:
            return result

        print(
            f"[browser] step {step.step_number}: all {len(selectors)} "
            "selectors failed, attempting runtime re-analysis..."
        )
        try:
            new_selectors = regenerate(selectors)
        except Exception as exc:
            result.error = (
                (result.error or "") + f" | re-analysis errored: {exc}"
            )
            return result

        if not new_selectors:
            return result

        healed_result = self._try_selectors(step, new_selectors)
        healed_result.healed = healed_result.status == StepStatus.resolved
        # Merge the pre-regen failures into the tried list so output JSON
        # shows everything that was attempted.
        healed_result.selectors_tried = (
            selectors + healed_result.selectors_tried
        )
        if healed_result.status != StepStatus.resolved and result.error:
            healed_result.error = (
                f"{result.error} | after re-analysis: {healed_result.error}"
            )
        return healed_result

    def _try_selectors(
        self, step: ParsedStep, selectors: list[Selector]
    ) -> ExecutionResult:
        tried: list[Selector] = []
        last_error: Optional[str] = None

        for selector in selectors:
            tried.append(selector)
            try:
                locator = self._locator_for(selector)
                # Resolve visibility / interactability before doing the
                # action, so we fail fast on stale selectors.
                self._prepare_element(locator)
                self._perform_action(step, locator)
                return ExecutionResult(
                    status=StepStatus.resolved,
                    used_selector=selector,
                    selectors_tried=tried,
                )
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}".splitlines()[0][:200]
                continue

        return ExecutionResult(
            status=StepStatus.unresolved,
            selectors_tried=tried,
            error=last_error or "all selectors failed",
        )

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------

    def _locator_for(self, selector: Selector):
        if selector.selector_type == SelectorType.css:
            return self.page.locator(selector.selector_value)
        # XPath path: Playwright accepts `xpath=//...` prefix
        value = selector.selector_value
        if not value.startswith("xpath="):
            value = f"xpath={value}"
        return self.page.locator(value)

    def _prepare_element(self, locator) -> None:
        """Pick the first visible match, scroll into view, wait for attached.

        If the locator resolves to multiple elements (strict mode), fall
        back to `.first` — Resilience §5's "prefer the first visible match"
        rule.
        """
        try:
            count = locator.count()
        except PlaywrightError as exc:
            raise RuntimeError(f"locator.count() failed: {exc}") from exc

        if count == 0:
            raise RuntimeError("selector matched zero elements")

        target = locator if count == 1 else locator.first
        target.wait_for(state="attached", timeout=ACTION_TIMEOUT_MS)
        try:
            target.scroll_into_view_if_needed(timeout=2_000)
        except PlaywrightError:
            pass  # not every element needs scrolling; ignore

    def _perform_action(self, step: ParsedStep, locator) -> None:
        target = locator.first
        action = step.action_type
        value = step.value

        if action == ActionType.click:
            target.click(timeout=ACTION_TIMEOUT_MS)
        elif action == ActionType.input:
            target.fill(value or "", timeout=ACTION_TIMEOUT_MS)
        elif action == ActionType.select:
            target.select_option(value or "", timeout=ACTION_TIMEOUT_MS)
        elif action == ActionType.check:
            target.check(timeout=ACTION_TIMEOUT_MS)
        elif action == ActionType.uncheck:
            target.uncheck(timeout=ACTION_TIMEOUT_MS)
        elif action == ActionType.verify:
            # For 'verify', we just need the element to be visible.
            target.wait_for(state="visible", timeout=ACTION_TIMEOUT_MS)
        elif action == ActionType.wait:
            seconds = _seconds_from_value(value)
            time.sleep(seconds)
        else:
            raise RuntimeError(f"unsupported action_type {action!r}")

        # Let the page react to the action before the next step runs.
        self.adaptive_wait()


def _seconds_from_value(value: Optional[str]) -> float:
    if not value:
        return 1.0
    match = re.search(r"[\d.]+", value)
    return float(match.group()) if match else 1.0
