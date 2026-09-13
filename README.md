# Basketball Knowledge Miner

Public collector for Hooper's Hub / FormPath. V1 is designed to run as a lightweight GitHub Actions batch job every three hours, inspect at most 20,000 source records per run, keep only basketball-relevant metadata, deduplicate it, and export accepted candidates to a separate private candidate inbox.

## V1 sources

- Crossref scholarly metadata
- Explicitly allowlisted YouTube channels for high-quality coaching or direct-expert material
- The architecture leaves room for additional official/API-backed adapters after they receive the same fixture, rate-limit, and provenance tests

Reddit, broad social scraping, paywall bypass, anti-bot bypass, raw video, full transcripts, full copyrighted articles, private FormPath research-unit text, and Coach private evidence do **not** belong in this repository.

## Safety boundary

The public repository contains collector code plus non-sensitive checkpoint state only. Candidate titles, URLs, summaries, authors, and candidate records are intended for a **private** downstream repository. The runner performs a GitHub repository-privacy preflight and refuses live export when the configured target is not private.

The production workflow targets `Rudwpahs/hoopDB`, a separate private candidate-data repository. Do not disable the privacy preflight or broaden the export token beyond that approved target.

The public workflows do not run self-hosted GPU jobs, CUDA workloads, FormQuant, QLoRA, or other private model-compute workloads. Those belong in a separately controlled private compute environment.

## Schedule and limits

The production workflow is configured for `17 */3 * * *` UTC, or eight scheduled runs per day, with a hard 20,000-record inspection budget per run. This is 40× the prior 500-record ceiling while keeping the same schedule frequency, for a theoretical maximum of 160,000 inspected source records per day. GitHub scheduled jobs can start later than the nominal cron time, and actual exported candidate volume will be lower because duplicate and basketball-relevance filters remain active. The design targets no incremental paid API/cloud usage, but GitHub/API policies and quotas can change and are not guaranteed by this project.

## Distillation V3 core dry-run

The V3 core adds deterministic validation, exact deduplication, queue/lease contracts, historical knowledge indexing, immutable staging, Judge-gated promotion validation, and release metrics. Semantic decisions remain external to this deterministic core.

Run the side-effect-free fixture pipeline with:

```bash
python scripts/run_distill_v3.py \
  --inbox-jsonl tests/fixtures/v3/inbox.jsonl \
  --state-dir _v3_state \
  --batch-size 100 \
  --dry-run
```

The core dry-run intentionally performs **no semantic ACCEPT decision, no canonical write, no private-repository write, and no GPU execution**. `--state-dir` is required to preserve the future storage interface, but the current `--dry-run` does not create or modify it. Persistent V3 storage/orchestration is a separate integration step and is rejected by this CLI until that layer is implemented.

The V3 safety rules are structural: raw inbox data cannot be promoted directly; Triage cannot ACCEPT; a Deep `PROPOSE_ACCEPT` cannot become canonical knowledge without a Judge `CONFIRM`; immutable staging permits an identical retry but refuses changed-byte overwrites; and only the future Auditor integration may perform canonical promotion.

## Development

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
python -m ruff check src tests scripts
```

A no-export Miner collection run can be launched locally with:

```bash
python scripts/run_miner.py --budget 50 --no-export
```

It prints aggregate counters only and does not persist state or candidate payloads.

Operational setup, secret permissions, state handling, and the live-export gate are documented in [`docs/OPERATIONS.md`](docs/OPERATIONS.md).
