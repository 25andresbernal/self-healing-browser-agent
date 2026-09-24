"""End-to-end offline demo: FakeLLM + real headless Chromium + the fixture site."""

from __future__ import annotations

import json
from pathlib import Path

from self_healing_browser_agent.demo import run_demo


def test_demo_runs_offline_and_heals_the_seeded_broken_step(tmp_path: Path):
    output = tmp_path / "journey.json"

    exit_code = run_demo(output=output, headful=False, take_screenshots=True)

    assert exit_code == 0
    data = json.loads(output.read_text())
    assert data["metadata"]["total_steps"] == 4
    assert data["metadata"]["resolved_steps"] == 4
    assert data["metadata"]["healed_steps"] == 1

    steps_by_number = {s["step_number"]: s for s in data["steps"]}
    assert steps_by_number[1]["action_type"] == "navigate"
    assert steps_by_number[1]["status"] == "resolved"

    healed_step = steps_by_number[3]
    assert healed_step["healed"] is True
    assert len(healed_step["identifiers"]) == 6  # 3 seeded-broken + 3 from re-analysis
    assert Path(steps_by_number[1]["screenshot_path"]).exists()
    assert Path(healed_step["screenshot_path"]).exists()
