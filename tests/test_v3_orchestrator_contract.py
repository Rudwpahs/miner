from pathlib import Path

PROMPT = Path(__file__).parents[1] / "docs" / "formpath-v3-orchestrator-prompt.md"


def _prompt_text() -> str:
    return PROMPT.read_text(encoding="utf-8")


def test_orchestrator_prompt_declares_shadow_mode_and_exact_role_order():
    text = _prompt_text()
    assert "SHADOW MODE" in text
    assert "REVIEW > JUDGE > DEEP > TRIAGE" in text
    assert "Triage never ACCEPTs" in text
    assert "Judge CONFIRM" in text
    assert "no canonical promotion" in text


def test_orchestrator_prompt_targets_only_v3_private_staging():
    text = _prompt_text()
    assert "Rudwpahs/hoopDB" in text
    assert "ml/coach/miner-data/v3/staging/" in text
    assert "ml/coach/miner-data/v3/leases/" in text
    assert "ml/coach/miner-data/v3/runs/" in text
    assert "distilled/accepted" in text
    assert "distilled/review" in text
    assert "distilled/manifests" in text
    assert "Do not write" in text


def test_orchestrator_prompt_makes_previous_seoul_day_audit_absolute_priority():
    text = _prompt_text()
    assert "Asia/Seoul" in text
    assert "previous local day" in text
    assert "COMPLETED" in text
    assert "BLOCKED" in text
    assert "absolute priority" in text


def test_orchestrator_prompt_claims_one_batch_and_is_fail_closed():
    text = _prompt_text()
    assert "exactly one eligible batch" in text
    assert "conflicting existing bytes" in text
    assert "BLOCKED" in text
    assert "never invent" in text
    assert "exact blocking prerequisite" in text


def test_orchestrator_prompt_contains_exact_stage_decision_contracts():
    text = _prompt_text()
    assert "TRIAGE: REJECT | DUPLICATE | DEEP_PENDING" in text
    assert "DEEP: PROPOSE_ACCEPT | REVIEW | REJECT" in text
    assert "JUDGE: CONFIRM | REVIEW | REJECT" in text
    assert "REVIEW: PROPOSE_ACCEPT | REVIEW | REJECT | BLOCKED" in text


def test_orchestrator_prompt_never_allows_raw_to_training_bypass():
    text = _prompt_text()
    assert "Raw candidates must never be used directly as training data" in text
    assert "raw-to-training bypass is forbidden" in text


def test_orchestrator_prompt_defines_exact_materializer_owned_batch_eligibility():
    text = _prompt_text()
    assert "batch.status == PENDING" in text
    assert "batch_id not in ledger.completed_batch_ids" in text
    assert "no active lease exists for batch_id" in text
    assert "Stage Materializer" in text
    assert "sole next-queue owner" in text
    assert "must never create next-stage queue files" in text
    assert "must never mutate the ledger" in text


def test_orchestrator_prompt_materializes_existing_staging_before_claiming_new_work():
    text = _prompt_text()
    assert "materialize existing staging before claiming semantic work" in text
    assert "Do not perform materialization yourself" in text


def test_orchestrator_prompt_requires_atomic_staging_envelope():
    text = _prompt_text()
    assert "staging envelope" in text
    for field in (
        "run_id",
        "batch_id",
        "stage",
        "worker",
        "created_at",
        "input_fingerprints",
        "records",
    ):
        assert f"`{field}`" in text
    assert "every candidate in the source batch must appear exactly once" in text
