# Basketball Knowledge Miner Operations

## Runtime model

`mine.yml` is the only workflow that is allowed to receive the downstream export credential. It runs on the default branch every three hours at minute 17 and can also be started manually. Routine pull-request CI uses `test.yml`, has read-only repository permission, and never receives the export secret.

The miner inspects at most 500 records per run. Source adapters normalize metadata, deterministic relevance rules reject unrelated material, fingerprints remove obvious duplicates, and only relevance-passed `CandidateRecord` objects reach the export sink. This collector does not assign final FormPath evidence grades and does not promote anything into the canonical RU corpus.

## Public/private boundary

The miner repository is public. It may contain source code, tests, fixtures, the allowlist, checkpoint cursors, and hashes of previously inspected metadata. It must not contain private candidate payloads, private FormPath RU text, Coach evidence, raw user video, full YouTube transcripts, or full copyrighted articles.

Live candidate export is permitted only to a GitHub repository whose API metadata reports `private: true`. `scripts/run_miner.py` performs this preflight before constructing the live sink. A public target is a hard failure; do not remove or bypass this check.

The approved production target is `Rudwpahs/hoopDB`, a separate private candidate-data repository. The workflow sets `HOOPHUB_TARGET_REPO=Rudwpahs/hoopDB`; do not redirect production export back into a public application repository.

## Secret setup

After an approved private target exists, create a fine-grained GitHub personal access token whose repository access is limited to that single target repository. Grant only the minimum repository permission needed by the Contents API to create candidate inbox files: **Contents: Read and write**. Do not grant organization administration, Actions administration, Issues, Pull Requests, or other unrelated permissions.

In `Rudwpahs/miner` add the token as an Actions repository secret named exactly `HOOPHUB_MINER_TOKEN`. Never commit the token, place it in workflow inputs, print it, echo environment variables, or add it to pull-request workflows. External/untrusted pull requests must never execute the secret-bearing mining job.

## State branch

The `miner-state` branch is intentionally separate from application code. Only these files are persisted by the scheduled workflow:

```text
state/crossref.json
state/youtube_rss.json
state/seen_hashes.jsonl
```

The mining job reads the state, performs collection/export, and writes `_state_next`. A separate `persist-state` job receives only that three-file artifact and has `contents: write` permission. If export fails, the mining job fails before the state artifact is uploaded, so the checkpoint is not advanced.

Do not add candidate titles, URLs, abstracts, transcripts, tokens, or other source payloads to `miner-state`.

## Manual verification sequence

Before enabling scheduled live export, use this order:

1. Merge the reviewed miner PR so `mine.yml` exists on the default branch.
2. Keep `Rudwpahs/hoopDB` private and configure the least-privilege `HOOPHUB_MINER_TOKEN` secret.
3. Run `Basketball Knowledge Miner` manually with `export=false`. This performs collection with export disabled and prints aggregate counters only; it does not advance persisted state.
4. Confirm the log contains counts only and no candidate URL/title/summary or secret material.
5. For the first live integration check, use a controlled synthetic metadata item marked `[MINER-INTEGRATION-TEST]` rather than treating live web material as approved knowledge. Confirm exactly one JSONL batch appears under `ml/coach/miner-data/inbox/YYYY/MM/DD/` in the private target, then remove the synthetic inbox file after verification.
6. Only after the privacy, redaction, and inbox checks pass should the scheduled export path be considered operational.

The current connector cannot create GitHub Actions secrets, so secret creation is an explicit account-side setup step rather than something the code can silently perform.

## Failure behavior

Crossref and YouTube adapters treat rate limiting as a bounded non-advancing result. The GitHub export sink fails closed on non-2xx responses and exposes only the HTTP status class, not the response body, token, candidate title, summary, or URL. Invalid target privacy metadata also fails closed.

A failed scheduled run should be investigated before rerunning with broader permissions. Do not solve authentication or rate-limit failures by logging secrets, disabling source limits, bypassing site controls, or granting a broad token.

## Verification commands

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
python -m ruff check src tests scripts
```

`tests/test_end_to_end_dry_run.py` exercises Crossref plus YouTube using local fixtures and `httpx.MockTransport`, so the end-to-end verification does not depend on external network access. `tests/test_workflow_policy.py` enforces the three-hour schedule, read-only routine CI, pinned third-party actions, and absence of `pull_request_target`.
