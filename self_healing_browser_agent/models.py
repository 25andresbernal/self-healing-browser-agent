"""Pydantic data models for the self-healing browser agent pipeline.

These models back the structured outputs from the LLM (step parser +
selector generator), the runtime execution results from the browser
engine, and the final journey.json written to disk. See the README for
the full journey.json schema and notes on adapting it to other
synthetic-monitoring or test-automation tools.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class ActionType(str, Enum):
    """Supported action types.

    A small, tool-agnostic vocabulary (navigate, click, input, select,
    check, uncheck, wait, verify) rather than any one vendor's action
    names, so the output maps cleanly onto most browser-automation
    formats.
    """

    navigate = "navigate"
    click = "click"
    input = "input"
    select = "select"
    check = "check"
    uncheck = "uncheck"
    wait = "wait"
    verify = "verify"


class StabilityRating(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"


class SelectorType(str, Enum):
    css = "css"
    xpath = "xpath"


class StepStatus(str, Enum):
    resolved = "resolved"
    unresolved = "unresolved"
    skipped = "skipped"


# ---------------------------------------------------------------------------
# Step parser output
# ---------------------------------------------------------------------------


class ParsedStep(BaseModel):
    """One step of a journey, parsed from natural language."""

    step_number: int = Field(..., ge=1)
    action_type: ActionType
    target_description: str = Field(
        ...,
        description="Plain-English description of the element or target, e.g. "
        "'Sign In button in the top navigation'.",
    )
    value: str | None = Field(
        default=None,
        description="Text to input, URL to navigate to, seconds to wait, or JS "
        "to execute. Null when the action takes no value (e.g. Click).",
    )


class ParsedSteps(BaseModel):
    """Wrapper so the LLM can return `{'steps': [...]}` via tool use."""

    steps: list[ParsedStep]


# ---------------------------------------------------------------------------
# Selector generator output
# ---------------------------------------------------------------------------


class Selector(BaseModel):
    selector_type: SelectorType
    selector_value: str
    stability_rating: StabilityRating
    reasoning: str = Field(
        ...,
        description="One-sentence explanation of why this selector was chosen "
        "and why it was assigned this stability rating.",
    )


class RankedSelectors(BaseModel):
    """Wrapper so the LLM can return `{'selectors': [...]}` via tool use."""

    selectors: list[Selector] = Field(..., min_length=1, max_length=5)


# ---------------------------------------------------------------------------
# Runtime execution result
# ---------------------------------------------------------------------------


class ResolvedStep(BaseModel):
    """A step after the browser has attempted to execute it."""

    step_number: int
    action_name: str
    action_type: ActionType
    target_description: str = ""
    value: str | None = None
    selectors: list[Selector] = Field(default_factory=list)
    used_selector: Selector | None = None
    healed: bool = Field(
        default=False,
        description="True when the step succeeded only after runtime "
        "re-analysis (all pre-generated selectors failed).",
    )
    status: StepStatus
    error: str | None = None
    screenshot_path: str | None = None
    dismissed_overlays: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Final journey config
# ---------------------------------------------------------------------------


class JourneyIdentifier(BaseModel):
    """One ranked selector for a step: a candidate location strategy, its
    predicted stability, and whether it is the one actually used."""

    type: str
    value: str
    stability: StabilityRating
    is_primary: bool


class JourneyStep(BaseModel):
    """A single action in the portable journey.json format."""

    step_number: int
    action_name: str
    action_type: str  # navigate / click / input / select / check / uncheck / wait / verify
    target_description: str = ""
    value: str | None = None
    identifiers: list[JourneyIdentifier] = Field(default_factory=list)
    screenshot_path: str | None = None
    status: StepStatus = StepStatus.resolved
    healed: bool = False
    error: str | None = None


class JourneyMetadata(BaseModel):
    total_steps: int
    resolved_steps: int
    unresolved_steps: int
    healed_steps: int
    average_selector_stability: str
    browser: str = "chromium"
    viewport: str = "1920x1080"
    model: str
    run_id: str


class JourneyConfig(BaseModel):
    journey_name: str
    generated_at: datetime
    target_url: str
    steps: list[JourneyStep]
    metadata: JourneyMetadata
