"""LLM prompt templates and Anthropic tool schemas.

Two tool schemas live here:
  - `STEP_PARSER_TOOL`: forces the model to emit a structured list of steps
    when given free-form natural language.
  - `SELECTOR_TOOL`: forces the model to emit a ranked list of selectors
    for a given target element and DOM context.

Both use Anthropic's `tool_choice={"type": "tool", "name": ...}` pattern
so the model is guaranteed to return a tool_use block we can validate
with Pydantic.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Step parser
# ---------------------------------------------------------------------------

STEP_PARSER_SYSTEM = """\
You are an expert at converting natural-language descriptions of web user \
journeys into structured, executable step lists.

Given a description that may be a paragraph, a numbered list, or a \
comma-separated sentence, extract an ordered sequence of atomic steps. \
Each step must have exactly one action. Split compound sentences like \
'enter email and password and click submit' into three separate steps.

Action type rules:
- 'navigate' for opening a URL. `value` is the URL.
- 'click' for clicking any element. `value` is null.
- 'input' for typing text into a field. `value` is the text to type.
- 'select' for choosing an option in a dropdown. `value` is the option label or value.
- 'check' / 'uncheck' for checkboxes and radios. `value` is null.
- 'wait' for an explicit delay. `value` is seconds as a string.
- 'verify' for an assertion that an element is present or visible. `value` is null.

Target description rules:
- Write a natural-language description rich enough for another person to find \
the element on the rendered page (e.g. 'Sign In button in the top navigation', \
not just 'button').
- For 'navigate' actions, the target_description can simply be 'the target URL' \
or the page name.

Always emit the steps in the order they should be executed. Number them \
starting at 1.\
"""


STEP_PARSER_TOOL = {
    "name": "record_parsed_steps",
    "description": (
        "Record the ordered list of atomic journey steps extracted from the "
        "user's natural-language description."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "steps": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "step_number": {
                            "type": "integer",
                            "minimum": 1,
                            "description": "1-indexed order of this step within the journey.",
                        },
                        "action_type": {
                            "type": "string",
                            "enum": [
                                "navigate",
                                "click",
                                "input",
                                "select",
                                "check",
                                "uncheck",
                                "wait",
                                "verify",
                            ],
                        },
                        "target_description": {
                            "type": "string",
                            "description": "Natural-language description of the element or target.",
                        },
                        "value": {
                            "type": ["string", "null"],
                            "description": "Text to type, URL to navigate to, seconds to wait, or null.",
                        },
                    },
                    "required": [
                        "step_number",
                        "action_type",
                        "target_description",
                        "value",
                    ],
                },
            }
        },
        "required": ["steps"],
    },
}


# ---------------------------------------------------------------------------
# Selector generator
# ---------------------------------------------------------------------------

SELECTOR_SYSTEM = """\
You are an expert at writing stable CSS selectors and XPaths for web \
automation. Your job is to choose the selectors that are least likely to \
break when the target site's HTML changes.

You will be given:
  1. A natural-language description of a target element.
  2. A compact DOM context block: an accessibility-tree outline and a list \
     of candidate elements with their attributes.

Return exactly 3 selectors for the target, ordered from most stable to \
least stable. All 3 selectors MUST resolve to the same element on the \
current page. Never invent attribute values that are not present in the \
provided context.

Stability ranking criteria (in order of preference):
  1. `data-testid`, `data-test`, `data-qa`, `data-cy`, or other test-intent \
     `data-*` attributes. Highest stability -- added intentionally for \
     automation.
  2. ARIA labels, roles, and other `aria-*` attributes. High stability -- \
     maintained for accessibility compliance.
  3. Semantic `id` attributes (e.g. `id='login-email'`). High stability if \
     the id is meaningful; LOW if it looks auto-generated (random hashes, \
     `__next`, `mui-xxx`, etc.).
  4. Unique visible text combined with an element role (e.g. \
     `button:has-text('Sign In')`). Medium stability.
  5. Structural CSS like `nav > ul > li:nth-child(3) > a`. Low stability -- \
     breaks on any DOM restructuring.

Selector format:
  - `selector_type` must be exactly 'css' or 'xpath'.
  - For Playwright-style text selectors, prefer CSS with `:has-text()` over \
    XPath `contains()` when possible.
  - Always prefer CSS over XPath unless XPath is strictly necessary.

Reasoning must be one concise sentence per selector.\
"""


SELECTOR_TOOL = {
    "name": "record_ranked_selectors",
    "description": (
        "Record a ranked list of 3 selectors for the target element, "
        "ordered from most stable to least stable."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "selectors": {
                "type": "array",
                "minItems": 1,
                "maxItems": 5,
                "items": {
                    "type": "object",
                    "properties": {
                        "selector_type": {
                            "type": "string",
                            "enum": ["css", "xpath"],
                        },
                        "selector_value": {
                            "type": "string",
                            "description": "The selector string itself.",
                        },
                        "stability_rating": {
                            "type": "string",
                            "enum": ["high", "medium", "low"],
                        },
                        "reasoning": {
                            "type": "string",
                            "description": "One-sentence justification for the selector choice and stability rating.",
                        },
                    },
                    "required": [
                        "selector_type",
                        "selector_value",
                        "stability_rating",
                        "reasoning",
                    ],
                },
            }
        },
        "required": ["selectors"],
    },
}


# ---------------------------------------------------------------------------
# User message templates
# ---------------------------------------------------------------------------


def build_selector_user_message(
    target_description: str,
    dom_context: str,
    *,
    retry_hint: str | None = None,
) -> str:
    """Assemble the user-turn content for the selector generator.

    `retry_hint` is non-None only on runtime re-analysis: it tells the model
    that the previously generated selectors failed on this page state.
    """

    parts = [
        f"Target element description: {target_description}",
        "",
        "DOM context:",
        dom_context,
    ]
    if retry_hint:
        parts.extend(
            [
                "",
                "IMPORTANT -- this is a re-analysis. The following selectors "
                "previously generated for this same target ALL FAILED to "
                "match any element on the current page state:",
                retry_hint,
                "",
                "Look carefully at the DOM context above and produce 3 NEW "
                "selectors that will resolve on the current page. Do not "
                "reuse selectors from the failed list.",
            ]
        )
    return "\n".join(parts)
