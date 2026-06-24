# MultiHopper — Agentic Flow Security Findings

Reproducible findings for the MultiHopper *"Break It Before Users Do"* bounty,
targeting the documented agentic transfer flow
(<https://dev-docs.multihopper.com/guides/agentic-integration>).

Each PoC runs the **documented reference agent code** against a **faithful mock**
that implements the documented API contract. The mock is deliberately *generous*
— it implements correct `Idempotency-Key` caching **and** `externalId`
de-duplication (`MH_033`). Any unsafe behaviour observed therefore comes from the
documented **client integration pattern**, not from the mock.

The base URL and API key are configurable, so the same harness can be pointed at
the live test environment (`mh_test_...`) to confirm a finding once credentials
are provisioned.

## Setup

```bash
pip install flask requests solders base58
```

## Findings

**The single submission document is [`REPORT.md`](REPORT.md)** — it consolidates
all four findings with embedded log evidence, and is what gets pasted into the
Google Doc / Notion. (`SUBMISSION_finding_0N.md` are the same findings split per
file, kept for reference.) Run everything at once with `python run_all.py`.

Each finding has a runnable PoC (exit code `0` == reproduced).

### #1 — Create is not restart-safe → duplicate fund movement (High)

The documented reference agent creates each transfer with a **random per-call
`Idempotency-Key`** and **no `externalId`**. The only create-level de-dup the API
documents is `externalId` (`MH_033`). So an agent that crashes after funding and
restarts on the same transfer intent issues a brand-new create → a **second**
transfer, a **second** funded keeper, and a **second** multi-hop route.

```bash
python poc_create_idempotency.py
```

### #2 — `confirm-broadcast` double-funding race (High)

The two-call `confirm-broadcast` pattern is meant to prevent double-funding, but
the reference flow records `keeperFundingSignature` only **after** the confirmation
poll (up to ~60s). A crash in that window leaves no record; on resume `/prepare`
sees an unconfirmed keeper with no recorded signature and emits a **new**
`keeperFundingTx` → the keeper is funded twice.

```bash
python poc_02_funding_race.py
```

### #3 — Single-blockhash budget exhaustion (Medium)

One `/prepare` returns one blockhash for all four groups, but the prescribed
"confirm each group before the next + 3s delays" procedure routinely exceeds the
~60s blockhash lifetime, so later groups hit `BlockhashNotFound` and are forced
into the resume path.

```bash
python poc_03_blockhash_budget.py
```

### #4 — `preparedTxs.resume.*` is undocumented (Documentation blocker)

The reference autonomous loop terminates on `preparedTxs.resume.nothingToDo` /
`resume.routeAlreadyDeployed`, but `resume` appears in no documented schema —
including `CLAUDE.md`, the file meant to keep agents from hallucinating fields.

```bash
python poc_04_resume_undocumented.py
```

## Files

| File | Purpose |
| --- | --- |
| `mock_multihopper.py` | Faithful mock of the documented REST contract (`[DOC]` comments map to the docs) |
| `reference_agent.py` | 1:1 Python port of the documented "Full autonomous loop", plus a fixed variant |
| `poc_create_idempotency.py` | Finding #1 — create restart-safety (reference vs fixed) |
| `poc_02_funding_race.py` | Finding #2 — keeper double-funding race (self-contained) |
| `poc_03_blockhash_budget.py` | Finding #3 — single-blockhash budget simulation |
| `poc_04_resume_undocumented.py` | Finding #4 — documented-schema vs reference-code diff |
| `SUBMISSION_finding_0N.md` | Per-finding write-ups in the bounty submission format |
| `evidence/` | Captured PoC logs |

## Responsible testing

No live funds or mainnet are touched. All keys in the harness are redacted
placeholders. Findings are not disclosed publicly until MultiHopper has reviewed
them.
