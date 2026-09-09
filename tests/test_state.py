from pathlib import Path

import pytest

from basketball_miner.models import Checkpoint
from basketball_miner.state import load_checkpoint, save_checkpoint


def test_missing_checkpoint_returns_named_empty_checkpoint(tmp_path: Path):
    checkpoint = load_checkpoint(tmp_path / "crossref.json", "crossref")
    assert checkpoint == Checkpoint(adapter="crossref")


def test_checkpoint_round_trip_is_atomic_and_strict(tmp_path: Path):
    path = tmp_path / "crossref.json"
    expected = Checkpoint(
        adapter="crossref",
        last_checked_at="2026-09-09T00:00:00Z",
        cursor="next",
        last_stable_id_hash="0" * 64,
    )
    save_checkpoint(path, expected)
    assert load_checkpoint(path, "crossref") == expected
    assert not path.with_suffix(".tmp").exists()

    path.write_text('{"adapter":"wrong","unexpected":true}', encoding="utf-8")
    with pytest.raises(ValueError):
        load_checkpoint(path, "crossref")
