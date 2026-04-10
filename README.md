# ObservePoint Journey AI — MVP

A Python CLI that turns a plain-English description of a user journey
into an ObservePoint-compatible Journey JSON file, with LLM-generated
stable selectors.

## What it does

Instead of manually inspecting a site and picking CSS/XPath selectors
for every step of a Journey, you describe the flow in English:

```
Navigate to https://example.com, click the More information link,
go back, click the first link on the page.
```

The tool:

1. Uses Claude (Anthropic API) to parse your text into a structured
   step list.
2. Drives a real headless Chromium browser through the flow with
   Playwright.
3. At each step, extracts a compact DOM context (accessibility tree +
   candidate elements) and asks Claude to generate the **top 3 stable
   selectors** ranked by predicted resilience.
4. Executes each action using the top selector, falling back through
   the ranked list if one breaks — and if all three fail, it calls the
   LLM one more time for **runtime re-analysis** on the live DOM to heal
   the step on the fly.
5. Writes an ObservePoint-style Journey JSON (with `action_type`,
   `identifiers[]`, `value`, etc.) plus per-step screenshots.

## Why it matters

Selector fragility is ObservePoint's #1 customer complaint on G2. Every
time a site ships a redesign, hand-built Journeys break and someone has
to go re-inspect the page. This MVP replaces that entire workflow with
an AI loop that authors, executes, and self-heals the selectors.

## Resilience features

1. **Ranked fallback selectors** — top-3 per step, tried in order.
2. **Runtime re-analysis** — if all fallbacks miss, the LLM gets a
   second shot with the live DOM and the list of failed selectors.
3. **Auto-dismiss common overlays** — cookie banners, consent modals,
   and dismiss/accept-all buttons are cleared before each step.
4. **Adaptive waits** — `networkidle` with a 10s cap, then fall back to
   `domcontentloaded`. No infinite hangs.
5. **Interactability checks** — visibility + scroll-into-view + strict
   mode fallback before clicking.
6. **LLM API retries** — exponential backoff on transient Anthropic
   errors.
7. **Step-level isolation** — one bad step never crashes the run; it is
   recorded as `unresolved` and the pipeline continues.

## Setup

Requires Python 3.9+.

```bash
cd ObservePoint_MVP
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

Set your Anthropic API key:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

## Usage

```bash
# Inline steps
python main.py \
  --url "https://example.com" \
  --steps "Navigate to example.com, click the 'More information' link" \
  --journey-name "Example.com basic flow" \
  --output output/example_flow.json

# Steps from a file
python main.py \
  --url "https://www.bbc.com" \
  --steps-file examples/bbc.txt \
  --output output/bbc.json

# Demo mode: show the browser window
python main.py --url ... --steps "..." --headful
```

CLI flags:

| Flag | Description |
|---|---|
| `--url` | **Required.** Target URL where the journey starts. |
| `--steps` | Inline natural-language description of the flow. |
| `--steps-file` | Path to a text file with the steps. |
| `--output` | Where to write the Journey JSON. Default: `output/journey.json`. |
| `--journey-name` | Human-readable label. Defaults to the URL. |
| `--model` | Override Anthropic model. Default: `claude-opus-4-6`. |
| `--headful` | Show the browser window (great for interview demos). |
| `--no-screenshots` | Skip per-step screenshot capture. |
| `--no-auto-dismiss` | Disable automatic cookie-banner dismissal. |

## Output

Generated files land under `output/`:

```
output/
├── journey.json                       # the Journey config
└── screenshots/
    └── 20260409-143017-ab12ef/        # one folder per run
        ├── step_01.png
        ├── step_02.png
        └── ...
```

Each step in the JSON looks like:

```json
{
  "step_number": 2,
  "action_name": "Click: Sign In button in the top navigation",
  "action_type": "Click",
  "value": null,
  "identifiers": [
    {
      "type": "CSS Selector",
      "value": "[data-testid='login-button']",
      "stability": "high",
      "is_primary": true
    },
    {
      "type": "CSS Selector",
      "value": "button[aria-label='Sign In']",
      "stability": "high",
      "is_primary": false
    },
    {
      "type": "XPath",
      "value": "//nav//button[contains(text(),'Sign In')]",
      "stability": "medium",
      "is_primary": false
    }
  ],
  "screenshot_path": "output/screenshots/20260409-143017-ab12ef/step_02.png",
  "status": "resolved",
  "healed": false,
  "error": null
}
```

The top-level `metadata` block reports total steps, resolved /
unresolved counts, healed-at-runtime count, average selector stability,
model, and run id.

## File structure

```
ObservePoint_MVP/
├── main.py                 # CLI entry point + pipeline orchestration
├── step_parser.py          # Natural language -> structured steps (LLM)
├── browser_engine.py       # Playwright + resilience layer
├── dom_extractor.py        # Compact DOM context for the LLM
├── selector_generator.py   # LLM -> ranked selectors
├── config_builder.py       # Assemble ObservePoint Journey JSON
├── resilience.py           # Retry + overlay dismissal helpers
├── prompts.py              # LLM system prompts + tool schemas
├── models.py               # Pydantic data models
├── requirements.txt
├── README.md
├── .gitignore
└── output/                 # created at runtime (gitignored)
```

## Out of scope (for now)

- No direct ObservePoint API upload — the tool outputs a JSON file you
  can import or use as reference.
- No iframe or shadow-DOM traversal beyond Playwright's defaults.
- No GUI. CLI only.
- Runtime self-healing is bounded at **one** LLM re-analysis per failing
  step, so API cost stays predictable.
