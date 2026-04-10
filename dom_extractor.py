"""Extract a compact DOM context block for the LLM.

The goal is to feed the selector generator enough information to pick a
stable selector for the target element, without blowing the token budget
on a 500KB page source.

Strategy:
  1. Grab Playwright's accessibility snapshot and flatten it into a short
     outline (role + name + children, indented). This gives the LLM the
     semantic shape of the page.
  2. Collect "candidate" elements whose visible text or attributes hint
     that they match the target description. For each candidate, dump tag,
     id, name, class, data-*, aria-*, role, text, and a 3-level parent
     chain.
  3. Concatenate both blocks. Result is typically under 8K tokens.
"""

from __future__ import annotations

import json
from typing import Any

# How many candidate elements to include in the DOM context.
_MAX_CANDIDATES = 6

# How deep to walk the accessibility tree before truncating.
_A11Y_MAX_DEPTH = 6

# How long each accessibility-tree name may be before we truncate it.
_A11Y_NAME_CHARS = 80


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_context(page, target_description: str) -> str:
    """Build a compact DOM context string for the selector LLM.

    `page` is a Playwright sync `Page`. `target_description` is the
    natural-language target from the parsed step.
    """

    a11y_block = _a11y_outline(page)
    candidate_block = _candidate_elements(page, target_description)

    return (
        "=== Accessibility outline (top of page) ===\n"
        f"{a11y_block}\n\n"
        "=== Candidate elements matching the target description ===\n"
        f"{candidate_block}"
    )


# ---------------------------------------------------------------------------
# Accessibility outline
# ---------------------------------------------------------------------------


def _a11y_outline(page) -> str:
    try:
        snapshot = page.accessibility.snapshot(interesting_only=True)
    except Exception as exc:  # page closed, etc.
        return f"(a11y snapshot unavailable: {exc})"
    if not snapshot:
        return "(empty)"

    lines: list[str] = []
    _walk_a11y(snapshot, lines, depth=0)
    # Cap total lines to keep prompt small
    if len(lines) > 120:
        lines = lines[:120] + ["... (truncated)"]
    return "\n".join(lines)


def _walk_a11y(node: dict[str, Any], out: list[str], depth: int) -> None:
    if depth > _A11Y_MAX_DEPTH:
        return
    role = node.get("role", "")
    name = (node.get("name") or "").strip().replace("\n", " ")
    if len(name) > _A11Y_NAME_CHARS:
        name = name[: _A11Y_NAME_CHARS - 1] + "…"
    indent = "  " * depth
    label = f"{indent}- {role}"
    if name:
        label += f' "{name}"'
    out.append(label)
    for child in node.get("children", []) or []:
        _walk_a11y(child, out, depth + 1)


# ---------------------------------------------------------------------------
# Candidate element collection
# ---------------------------------------------------------------------------
#
# We ask the page (via one JS evaluation) to rank all visible interactive
# elements by how well their text / aria-label / attributes match the
# target description, and return the top N with all relevant attributes
# plus their parent chain. Doing this in-page avoids bouncing dozens of
# Playwright handles across the CDP bridge.


_CANDIDATE_SCRIPT = r"""
(query) => {
  const stopwords = new Set([
    'the','a','an','of','to','in','on','at','for','with','and','or','button',
    'link','field','input','icon','page','top','bottom','left','right','main'
  ]);
  const terms = query
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, ' ')
    .split(/\s+/)
    .filter(t => t && !stopwords.has(t));

  const isVisible = (el) => {
    if (!el || !el.getBoundingClientRect) return false;
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return false;
    const style = window.getComputedStyle(el);
    return style.display !== 'none'
        && style.visibility !== 'hidden'
        && style.opacity !== '0';
  };

  const scoreElement = (el) => {
    const haystackParts = [
      el.innerText || '',
      el.getAttribute('aria-label') || '',
      el.getAttribute('placeholder') || '',
      el.getAttribute('name') || '',
      el.getAttribute('title') || '',
      el.getAttribute('alt') || '',
      el.id || '',
      el.getAttribute('data-testid') || '',
      el.getAttribute('data-test') || '',
      el.getAttribute('data-qa') || '',
      el.className && typeof el.className === 'string' ? el.className : ''
    ];
    const haystack = haystackParts.join(' ').toLowerCase();
    if (!haystack.trim()) return 0;
    let score = 0;
    for (const t of terms) {
      if (haystack.includes(t)) score += 1;
    }
    // Boost for test-intent attributes being present at all
    if (el.getAttribute('data-testid')
      || el.getAttribute('data-test')
      || el.getAttribute('data-qa')) score += 0.5;
    return score;
  };

  const elements = Array.from(document.querySelectorAll(
    'a, button, input, select, textarea, [role], [aria-label], [data-testid], [contenteditable="true"]'
  ));

  const scored = [];
  for (const el of elements) {
    if (!isVisible(el)) continue;
    const s = scoreElement(el);
    if (s <= 0) continue;
    scored.push([s, el]);
  }
  scored.sort((a, b) => b[0] - a[0]);

  const snapshotEl = (el) => {
    const attrs = {};
    for (const a of el.attributes || []) {
      const n = a.name;
      if (n === 'id'
        || n === 'name'
        || n === 'class'
        || n === 'type'
        || n === 'role'
        || n === 'placeholder'
        || n === 'title'
        || n === 'alt'
        || n === 'href'
        || n.startsWith('data-')
        || n.startsWith('aria-')) {
        attrs[n] = a.value;
      }
    }
    const text = (el.innerText || '').trim().slice(0, 120);
    return { tag: el.tagName.toLowerCase(), attrs, text };
  };

  const out = [];
  const MAX = __MAX__;
  for (let i = 0; i < scored.length && out.length < MAX; i++) {
    const el = scored[i][1];
    const parents = [];
    let p = el.parentElement;
    for (let d = 0; d < 3 && p; d++) {
      parents.push(snapshotEl(p));
      p = p.parentElement;
    }
    out.push({
      score: scored[i][0],
      element: snapshotEl(el),
      parents
    });
  }
  return out;
}
"""


def _candidate_elements(page, target_description: str) -> str:
    script = _CANDIDATE_SCRIPT.replace("__MAX__", str(_MAX_CANDIDATES))
    try:
        candidates = page.evaluate(script, target_description)
    except Exception as exc:
        return f"(candidate extraction failed: {exc})"

    if not candidates:
        return "(no candidates matched; LLM should fall back to the a11y outline above)"

    lines: list[str] = []
    for i, cand in enumerate(candidates, start=1):
        el = cand.get("element", {})
        lines.append(f"Candidate {i} (match score {cand.get('score')}):")
        lines.append(f"  tag: <{el.get('tag', '?')}>")
        attrs = el.get("attrs", {})
        if attrs:
            lines.append(f"  attrs: {json.dumps(attrs, sort_keys=True)}")
        text = el.get("text") or ""
        if text:
            lines.append(f"  text: {text!r}")
        parents = cand.get("parents", [])
        if parents:
            chain = " > ".join(_short_parent(p) for p in reversed(parents))
            lines.append(f"  parent chain: {chain}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _short_parent(p: dict[str, Any]) -> str:
    tag = p.get("tag", "?")
    attrs = p.get("attrs", {}) or {}
    bits: list[str] = [tag]
    if "id" in attrs:
        bits.append(f"#{attrs['id']}")
    if "class" in attrs:
        cls = attrs["class"].split()[:2]
        if cls:
            bits.append("." + ".".join(cls))
    role = attrs.get("role")
    if role:
        bits.append(f"[role={role}]")
    return "".join(bits)
