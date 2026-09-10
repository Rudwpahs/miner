# Basketball Knowledge Miner

Public collector for Hooper's Hub / FormPath. V1 is designed to run as a lightweight GitHub Actions batch job every three hours, inspect at most 500 source records per run, keep only basketball-relevant metadata, deduplicate it, and export accepted candidates to a separate private candidate inbox.

## V1 sources

- Crossref scholarly metadata
- Explicitly allowlisted YouTube channels for high-quality coaching or direct-expert material
- The architecture leaves room for additional official/API-backed adapters after they receive the same fixture, rate-limit, and provenance tests

Reddit, broad social scraping, paywall bypass, anti-bot bypass, raw video, full transcripts, full copyrighted articles, private FormPath research-unit text, and Coach private evidence do **not** belong in this repository.

## Safety boundary

The public repository contains collector code plus non-sensitive checkpoint state only. Candidate titles, URLs, summaries, authors, and candidate records are intended for a **private** downstream repository. The runner performs a GitHub repository-privacy preflight and refuses live export when the configured target is not private.

The current configured target name is `Rudwpahs/shooting-profile-coach-ios`. At the time of implementation verification it is publicly visible, so live export is intentionally blocked until that data boundary is corrected. Do not disable the privacy preflight to work around this gate.

## Schedule and limits

The production workflow is configured for `17 */3 * * *` UTC, or eight scheduled runs per day, with a hard 500-record inspection budget per run. GitHub scheduled jobs can start later than the nominal cron time. The design targets no incremental paid API/cloud usage, but GitHub/API policies and quotas can change and are not guaranteed by this project.

## Development

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
python -m ruff check src tests scripts
```

A no-export run can be launched locally with:

```bash
python scripts/run_miner.py --budget 50 --no-export
```

It prints aggregate counters only and does not persist state or candidate payloads.

Operational setup, secret permissions, state handling, and the live-export gate are documented in [`docs/OPERATIONS.md`](docs/OPERATIONS.md).
