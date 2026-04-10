"""Cross-cutting resilience helpers: LLM retries + overlay dismissal.

These live in a separate module so both the step parser and the selector
generator can share the same retry wrapper, and so `browser_engine.py`
can import `dismiss_common_overlays` without taking a dependency on the
LLM layer.
"""

from __future__ import annotations

import time
from typing import Callable, TypeVar

try:  # these are the retryable SDK errors; import defensively so tests
    # that stub the anthropic client still work.
    from anthropic import (
        APIConnectionError,
        APIStatusError,
        APITimeoutError,
        InternalServerError,
        RateLimitError,
    )
except Exception:  # pragma: no cover
    APIConnectionError = APIStatusError = APITimeoutError = (
        InternalServerError
    ) = RateLimitError = Exception  # type: ignore[assignment,misc]


T = TypeVar("T")

_RETRYABLE = (
    APIConnectionError,
    APITimeoutError,
    RateLimitError,
    InternalServerError,
)


def retry_with_backoff(
    fn: Callable[[], T],
    *,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    label: str = "llm",
) -> T:
    """Run `fn` up to `max_attempts` times with exponential backoff on
    transient Anthropic SDK errors. Any non-retryable exception propagates
    immediately."""

    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except _RETRYABLE as exc:
            last_exc = exc
            if attempt == max_attempts:
                break
            delay = base_delay * (2 ** (attempt - 1))
            print(
                f"[resilience] {label} attempt {attempt}/{max_attempts} "
                f"failed ({type(exc).__name__}); retrying in {delay:.1f}s..."
            )
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc


# ---------------------------------------------------------------------------
# Overlay / cookie banner auto-dismissal
# ---------------------------------------------------------------------------
#
# Runs a page-side JavaScript function that looks for anything that smells
# like a cookie banner or modal and clicks its accept/close button. Returns
# the list of textual labels of whatever it dismissed so we can log it in
# the step output.
#
# The script is intentionally conservative: it only clicks buttons whose
# accessible name matches a known accept/close phrase AND that live inside
# a container whose attributes hint at cookies/consent/gdpr/modal.

_OVERLAY_SCRIPT = r"""
(() => {
  const acceptPhrases = [
    'accept all', 'accept cookies', 'accept', 'agree', 'i agree',
    'got it', 'ok', 'okay', 'dismiss', 'close', 'continue',
    'allow all', 'allow'
  ];
  const containerHints = /(cookie|consent|gdpr|privacy|modal|overlay|banner|dialog)/i;
  const dismissed = [];

  const isVisible = (el) => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return false;
    const style = window.getComputedStyle(el);
    return style.display !== 'none'
        && style.visibility !== 'hidden'
        && style.opacity !== '0';
  };

  const looksLikeOverlay = (el) => {
    if (!el) return false;
    let node = el;
    for (let i = 0; i < 6 && node; i++) {
      const idCls = (node.id || '') + ' ' + (node.className || '');
      if (typeof idCls === 'string' && containerHints.test(idCls)) return true;
      if (node.getAttribute && (
        node.getAttribute('role') === 'dialog'
        || node.getAttribute('aria-modal') === 'true'
      )) return true;
      node = node.parentElement;
    }
    return false;
  };

  const buttons = Array.from(document.querySelectorAll(
    'button, [role="button"], a'
  ));

  for (const btn of buttons) {
    if (!isVisible(btn)) continue;
    const label = (btn.innerText || btn.getAttribute('aria-label') || '')
      .trim()
      .toLowerCase();
    if (!label || label.length > 40) continue;
    if (!acceptPhrases.some(p => label === p || label.includes(p))) continue;
    if (!looksLikeOverlay(btn)) continue;
    try {
      btn.click();
      dismissed.push(label);
      if (dismissed.length >= 3) break;
    } catch (e) { /* swallow */ }
  }
  return dismissed;
})()
"""


def dismiss_common_overlays(page) -> list[str]:
    """Dismiss any visible cookie banners or modal overlays on `page`.

    Returns the labels of anything dismissed. Safe to call before every
    step -- if nothing matches, it's a sub-millisecond no-op.
    """

    try:
        result = page.evaluate(_OVERLAY_SCRIPT)
    except Exception as exc:  # page closed, JS error, etc.
        print(f"[resilience] overlay dismissal skipped: {exc}")
        return []
    if not isinstance(result, list):
        return []
    if result:
        print(f"[resilience] dismissed overlays: {result}")
    return [str(x) for x in result]
