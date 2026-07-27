from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
STATIC_DIR = REPO_ROOT / "tracefix" / "runner_ui" / "static"


def _read(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


def test_runner_ui_static_files_have_no_merge_markers() -> None:
    for name in ("index.html", "styles.css", "app.js"):
        text = _read(name)
        assert "<<<<<<<" not in text, name
        assert "=======" not in text, name
        assert ">>>>>>>" not in text, name


def test_runner_ui_entrypoint_keeps_core_workflows_and_assets() -> None:
    html = _read("index.html")

    assert re.search(r'href="/static/styles\.css\?[^"]+"', html)
    assert re.search(r'src="/static/app\.js\?[^"]+"', html)
    for workflow in ("tellme", "planner", "synth"):
        assert f'data-workflow="{workflow}"' in html
    for element_id in (
        "tellmePanel",
        "tellmeRoute",
        "tellmePrivacy",
        "tellmeTaskCount",
        "tellmeIntent",
        "tellmeTaskSpec",
        "tellmeAnswer",
        "tellmeRunStatus",
        "llmUsageCard",
        "synthPanel",
    ):
        assert f'id="{element_id}"' in html


def test_runner_ui_styles_cover_the_layout_contract() -> None:
    css = _read("styles.css")

    for selector in (
        ".runner-shell",
        ".control-panel",
        ".run-panel",
        ".sidebar-section",
        ".section-body",
        ".tellme-summary-grid",
        ".tellme-card",
        ".tellme-detail-grid",
        ".hidden",
    ):
        assert selector in css
