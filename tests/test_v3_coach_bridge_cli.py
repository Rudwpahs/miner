from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "export_coach_linked_v1.py"
FIXTURES = ROOT / "tests" / "fixtures" / "coach_bridge"


def test_cli_exports_linked_bundle(tmp_path: Path) -> None:
    output = tmp_path / "bundle"
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--canonical",
            str(FIXTURES / "canonical.jsonl"),
            "--projections",
            str(FIXTURES / "projections.jsonl"),
            "--output",
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert sorted(path.name for path in output.iterdir()) == [
        "manifest.json",
        "sources.jsonl",
        "units.jsonl",
    ]
    assert "exported_units=1" in completed.stdout
    assert "sources=1" in completed.stdout


def test_cli_failure_leaves_no_partial_bundle(tmp_path: Path) -> None:
    bad_projection = tmp_path / "bad-projections.jsonl"
    bad_projection.write_text("{not-json}\n", encoding="utf-8")
    output = tmp_path / "bundle"

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--canonical",
            str(FIXTURES / "canonical.jsonl"),
            "--projections",
            str(bad_projection),
            "--output",
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert not (output / "units.jsonl").exists()
    assert not (output / "sources.jsonl").exists()
    assert not (output / "manifest.json").exists()
