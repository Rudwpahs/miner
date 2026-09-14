from __future__ import annotations

import pytest

from basketball_miner.distill_v3.coach_bridge import stable_research_unit_id


def test_stable_id_is_repeatable_and_reserved() -> None:
    first = stable_research_unit_id("KU-SHOOT-DISTANCE-MILLER-1996-001")
    second = stable_research_unit_id("KU-SHOOT-DISTANCE-MILLER-1996-001")
    assert first == second
    assert 1_000_000_000_000 <= first < 2_100_000_000_000
