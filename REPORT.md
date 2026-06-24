# MultiHopper — Agentic Transfer Flow: Security & Reliability Findings

Independent testing of the documented MultiHopper agentic transfer flow
(`create → prepare → sign/broadcast → confirm-broadcast → monitor`), per the
*"Break It Before Users Do"* bounty. Four reproducible findings, each with a
deterministic proof-of-concept and embedded logs.

## Summary

| # | Finding | Severity |
| --- | --- | --- |
| 1 | Create is not restart-safe — a crashed/retried agent creates duplicate transfers and double-funds the keeper | **High** |
| 2 | The two-call `confirm-broadcast` pattern does not prevent double-funding (crash-in-window race) | **High** |
| 3 | A single `/prepare` blockhash cannot cover the prescribed broadcast procedure — happy path expires mid-flight | **Medium** |
| 4 | The reference autonomous loop depends on `preparedTxs.resume.*`, which is documented nowhere | **Documentation blocker** |

Findings #1 and #2 are fund-safety issues triggered by an ordinary agent
crash/restart. #3 makes the documented linear path unreliable (and is the
condition under which #2 fires more often). #4 blocks a correct independent agent
integration.

## How this was tested (applies to all findings)

* **Harness:** Python 3.12. A faithful mock of the documented REST contract plus a
  **1:1 port of the documented reference code** ("Full autonomous loop" and the
  Python signing/broadcast helpers). The mock implements the documented behaviour
  *generously* — correct `Idempotency-Key` caching **and** `externalId`
  de-duplication (`MH_033`), and `/prepare` returns `null` only for groups
  confirmed on-chain. Because the mock is faithful (not adversarial), every unsafe
  outcome below originates in the documented **client/integration pattern**, not
  in the test double.
* **Reproduction:** one command runs all four PoCs and writes a combined log.
  ```bash
  pip install flask requests solders base58
  python run_all.py          # exit 0 == all four findings reproduced
  ```
* **Signing note:** I separately verified the documented Python `sign_versioned`
  helper with `solders` 0.27.1 — the v0 `0x80` message-prefix handling and the
  preservation of the server's ephemeral partial signatures are **correct**. None
  of these findings is a signing bug; I'm flagging this so you don't spend review
  time there.
* **Environment:** local, deterministic, **no mainnet and no live funds**. The
  flaws are provable by inspection of the documented code + contract; a live
  confirmation on the provided test environment (`mh_test_...`) can be supplied on
  request — e.g. two on-chain keeper-funding signatures for #2, or `BlockhashNotFound`
  under slow confirmations for #3.
* **Full source & PoCs:** https://github.com/isoooiso/multihopper-agentic-findings
* **Responsible testing:** nothing was run against mainnet or live user funds; all
  keys in the harness are redacted placeholders; findings are not disclosed
  publicly pending your review.

---

# Finding #1 — Create is not restart-safe (duplicate transfers + double keeper funding)

**Severity: High.** Causes duplicate movement of a user's funds and a doubled
keeper-funding cost, triggered by an ordinary agent process crash/restart with no
attacker. (Arguably Critical, filed High because it needs a restart and the
primary fix is client-side.)

**Affected action:** `POST /api/v1/transfers` (create), and by extension the whole
flow — each duplicate create spawns its own keeper funding and multi-hop route.

**Root cause (from the docs):** the reference create call uses a fresh random
`Idempotency-Key` on every invocation (`crypto.randomUUID()`) and sends no
`externalId`. The only create-level de-dup the API documents is `externalId`
(`MH_033`). So if an agent crashes after `create` (e.g. after funding the keeper)
and restarts on the same transfer intent, it generates a new random key and still
sends no `externalId` — the server has no basis to recognise the retry and creates
a **second** transfer. `Idempotency-Key`, whose purpose is to make POST retries
safe, gives no protection here because it is generated per HTTP attempt, not per
logical operation.

**Reproduce:** `python poc_create_idempotency.py` — one intent (send 100 USDC, A→B,
4 hops); the agent funds the keeper then simulates a crash, then restarts on the
identical intent. Scenario A = documented reference pattern; Scenario B = proposed
fix.

**Evidence** (`evidence/poc_create_idempotency.log`):

```
SCENARIO A — documented reference agent (random key, no externalId)
  RUN 1: agent starts the transfer, funds the keeper, then CRASHES
  mock   CREATE  NEW transfer id=1001  key=3b473d63-3..  externalId=None  intent=(Awa1..->Bwa1.. amountRaw=100000000)
  agent  [REF] funded keeper for transfer id=1001
  agent  [REF] *** simulated process crash (before route completion) ***
  RUN 2: SAME intent — the agent process restarts from the top
  mock   CREATE  NEW transfer id=1002  key=85197093-7..  externalId=None  intent=(Awa1..->Bwa1.. amountRaw=100000000)
  agent  [REF] funded keeper for transfer id=1002
  RESULT: distinct transfers created for ONE intent = 2  ids=[1001, 1002]
  RESULT: keeper funding events                     = 2  on ids=[1001, 1002]

SCENARIO B — proposed fix (stable Idempotency-Key + externalId)
  RUN 1: ... funds keeper for id=1001 ... *** simulated crash ***
  RUN 2: SAME intent — the agent process restarts from the top
  mock   CREATE  idem-cache HIT  key=idem_creat..  -> id=1001 (NO new transfer created)
  mock   FUND    keeper re-fund ignored for id=1001 (already funded)
  RESULT: distinct transfers created for ONE intent = 1  ids=[1001]

SUMMARY
  documented reference : 2 transfers / 2 keeper fundings for 1 intent  ->  DUPLICATE FUND MOVEMENT
  with proposed fix    : 1 transfer  / 1 keeper funding  for 1 intent  ->  safe
```

In Scenario B's RUN 2 the mock logs `idem-cache HIT`, proving it *would*
de-duplicate — the reference client simply never gives it a stable key to match on.

**Impact on agentic usage:** duplicate fund movement and doubled keeper cost. This
is the normal agent failure mode (crash/timeout/OOM/retry), not an edge case — the
documented "Full autonomous loop" even retries internally. The protection that
exists (`externalId`/`MH_033`) is defeated by the reference example and by
`CLAUDE.md`, which an LLM agent reads as ground truth and copies verbatim.

**Proposed fix:** derive idempotency from the *logical operation*, not per attempt:

```ts
const intentKey  = canonicalJson({ sourceOwner, recipientWallet, amountRaw, tokenMint, hops });
const externalId = "ext_"  + sha256(intentKey).slice(0, 16);
const createKey  = "idem_" + sha256("create:" + intentKey);
await fetch(`${API_BASE}/api/v1/transfers`, {
  method: "POST",
  headers: { ...headers, "Idempotency-Key": createKey },   // stable, not randomUUID()
  body: JSON.stringify({ ...params, externalId }),         // include externalId
});
```

Documentation wording to add to the guide and `CLAUDE.md`:

> **Idempotency for agents (required).** An autonomous agent may crash and restart
> mid-transfer. Derive both `Idempotency-Key` and `externalId` deterministically
> from the transfer intent so a restarted process produces the *same* values and
> the server returns the existing transfer instead of creating a new one. Do **not**
> use a fresh random key per attempt for `create`. On `MH_033`, fetch and resume
> the existing transfer rather than creating a new one.

Optional server hardening: honour `Idempotency-Key` across the full create lifetime
and/or reject a second create whose intent fingerprint matches an in-flight transfer.

---

# Finding #2 — The two-call `confirm-broadcast` pattern does not prevent double-funding

**Severity: High.** The two-call pattern exists specifically to prevent
double-funding ("This prevents double-funding if deployment is interrupted"). A
crash in the documented window defeats it and duplicates the keeper-funding
lamports. Scope: the duplicated cost is the keeper-funding amount (resume targets
the same transfer id), but it defeats a fund-safety mechanism the protocol
explicitly built.

**Affected flow:** `keeperFundingTx` broadcast → `confirm-broadcast` → `/prepare`
(resume).

**Root cause (from the docs):** the Python `broadcast_and_confirm` helper does
`sendTransaction`, then polls for confirmation up to `12 * 5s = 60s`, then returns;
only *after* it returns does the reference code call `confirm-broadcast` with
`keeperFundingSignature`. So the signature is recorded on the server **only after
local confirmation**. If the process dies during that up-to-60s poll, the signature
is never recorded. On resume, `/prepare` "returns null for any group already
confirmed"; if the funding tx is still in-flight (not yet confirmed) at resume time,
`/prepare` sees no confirmed funding and no recorded signature and emits a **new**
`keeperFundingTx` → the keeper is funded twice.

**Reproduce:** `python poc_02_funding_race.py` — funds the keeper, injects a crash
in the documented window, then resumes the **same** transfer id (isolating this
from #1). Scenario A = documented client/server; Scenario B = proposed fix.

**Evidence** (`evidence/poc_funding_race.log`):

```
SCENARIO A — documented client + server (record sig AFTER confirmation)
  RUN 1: fund keeper, then CRASH during the confirmation poll
    [server] /prepare id=2001: keeper NOT confirmed and no usable record -> EMIT NEW keeperFundingTx
    [client] broadcast keeperFundingTx sig=kSigAAAA1111.. (in-flight)
    [client] *** process crash during confirmation poll ***
  RUN 2: resume the SAME transfer id (call /prepare again)
    [server] /prepare id=2001: keeper NOT confirmed and no usable record -> EMIT NEW keeperFundingTx
    [client] broadcast keeperFundingTx sig=kSigBBBB2222.. (in-flight)
  RESULT: keeper funding txs that landed for ONE transfer = 2  (DOUBLE-FUNDED)

SCENARIO B — proposed fix (record sig BEFORE confirmation + server honors it)
  RUN 1: ... broadcast sig=kSigAAAA1111 ... recorded keeper sig ... *** crash ***
  RUN 2: resume the SAME transfer id (call /prepare again)
    [server] /prepare id=2001: recorded keeper signature present & tx in-flight -> keeperFundingTx=null (no re-fund)
    [client] keeperFundingTx is null -> nothing to fund
  RESULT: keeper funding txs that landed for ONE transfer = 1  (safe)

SUMMARY
  documented flow : 2 keeper fundings for 1 transfer  ->  DOUBLE FUNDING on crash+resume
  with fix        : 1 keeper funding  for 1 transfer  ->  safe
```

The decisive line is resume `/prepare` emitting a new `keeperFundingTx` because no
funding signature was recorded before the crash.

**Impact on agentic usage:** defeats a documented fund-safety mechanism; the window
is large (the whole confirmation poll, ~60s) and the trigger is routine; and it
compounds with finding #3, which forces the resume path often (more chances for a
prior funding to still be in-flight when `/prepare` re-runs).

**Proposed fix:**
1. **Record-then-confirm (client):** call `confirm-broadcast` with
   `keeperFundingSignature` *immediately after broadcast*, before awaiting
   confirmation — shrinking the unprotected window from ~60s to one round-trip.
2. **Honor the recorded in-flight signature (server):** on `/prepare`, if a
   keeper-funding signature was recorded, check that tx's on-chain status (including
   `processed`/in-flight) before emitting a new `keeperFundingTx`; emit a new one
   only if the recorded tx is confirmed-failed or absent. Defense-in-depth: also
   check for any in-flight funding crediting the keeper account.

Documentation wording:

> Record `keeperFundingSignature` via `confirm-broadcast` *immediately after you
> broadcast* `keeperFundingTx`, before waiting for confirmation. If your process
> crashes during the confirmation wait, this lets the server recognise the
> in-flight funding on resume and avoid funding the keeper twice.

---

# Finding #3 — A single `/prepare` blockhash cannot cover the prescribed broadcast procedure

**Severity: Medium** (route reliability). No fund loss, but the documented linear
broadcast path is timing-fragile by construction: it frequently expires mid-flight
and depends on the resume path always working (which #2 shows is imperfect).

**Affected flow:** one `/prepare` → broadcast of `keeperFundingTx` → `routeInitTxs`
→ `orchestratorInitTx` → `sessionInitTxs`, all under the single `recentBlockhash`
from that one `/prepare`.

**Root cause (all numbers from the docs):** `/prepare` returns one blockhash for all
four groups; "blockhashes expire roughly 60 seconds after the `/prepare` call";
"each group must reach confirmed status before the next group is sent"; the Python
helper polls `12 * 5s = 60s` per tx; "+3s after the last `routeInitTx` and after
`orchestratorInitTx`". Summing the prescribed confirm-waits plus the 3s delays
exceeds the ~60s blockhash lifetime under realistic latency, so later groups are
broadcast against an expired blockhash and rejected.

**Reproduce:** `python poc_03_blockhash_budget.py` — a deterministic budget
simulation; per-group latency is a parameter so you can plug in your own devnet
measurements.

**Evidence** (`evidence/poc_blockhash_budget.log`):

```
### Documented worst case — keeper confirm within its own 60s poll
    keeper=55s route=10s orch=10s session=10s | n_route=2 n_session=1
    keeperFundingTx        0.0s   valid
    routeInitTxs[0]       55.0s   valid
    routeInitTxs[1]       65.0s   EXPIRED — tx rejected (BlockhashNotFound)
    orchestratorInitTx    78.0s   EXPIRED — tx rejected (BlockhashNotFound)
    sessionInitTxs[0]     91.0s   EXPIRED — tx rejected (BlockhashNotFound)
    => 'routeInitTxs[1]' onward broadcast against an EXPIRED blockhash

### Realistic devnet latencies
    keeper=20s route=15s orch=15s session=15s | n_route=3 n_session=2
    ... orchestratorInitTx 68.0s EXPIRED; sessionInitTxs 86.0s / 101.0s EXPIRED
    => 'orchestratorInitTx' onward broadcast against an EXPIRED blockhash

### Optimistic — fast confirmations everywhere (8s each)
    ... total 54.0s  => fits within one blockhash
```

The most airtight point needs no latency assumptions: the keeper-funding poll is
*documented* to wait up to 60s and the shared blockhash dies at ~60s — so any run
where keeper confirmation takes a large fraction of its own allowed poll has
already expired the blockhash before the route group is even broadcast.

**Impact on agentic usage:** under realistic devnet latency the documented linear
path is the exception, not the rule; transfers regularly hit an expired blockhash
mid-flight and depend on resume. With `hops` 3–10 the route/session groups are
multiple transactions, pushing the budget further past the blockhash lifetime.

**Proposed fix:** hand out (or let the client fetch) a fresh blockhash per group, or
immediately before each group is broadcast. If the single-blockhash design is
intentional, document resume as the normal path (not an "expiry" footnote) and
remove language implying a single clean linear pass.

---

# Finding #4 — The reference autonomous loop depends on undocumented `preparedTxs.resume.*`

**Severity: Documentation blocker.** No fund risk, but it blocks a correct agent
integration: the field that drives loop termination in the reference code is absent
from every documented schema.

**Affected action:** consumption of the `POST /api/v1/transfers/:id/prepare` response
inside the agent loop.

**Root cause:** the reference "Full autonomous loop" terminates on
`preparedTxs.resume?.nothingToDo` and continues on
`preparedTxs.resume?.routeAlreadyDeployed`. But the documented prepare response —
the response-fields table, the `CLAUDE.md` schema block, and the "Handling expiry
and resume" JSON example — lists only `routeInitTxs`, `orchestratorInitTx`,
`sessionInitTxs`, `keeperFundingTx`, `recentBlockhash`, `lastValidBlockHeight`.
There is no `resume` object anywhere.

**Reproduce:** `python poc_04_resume_undocumented.py` — diffs the `preparedTxs`
fields the reference code reads against the fields the documentation defines.

**Evidence** (`evidence/poc_resume_undocumented.log`):

```
preparedTxs fields READ by the documented reference loop:
    preparedTxs.resume.nothingToDo
    preparedTxs.resume.routeAlreadyDeployed

preparedTxs fields DEFINED by the documentation:
    preparedTxs.keeperFundingTx, lastValidBlockHeight, orchestratorInitTx,
    recentBlockhash, routeInitTxs, sessionInitTxs

UNDOCUMENTED fields the reference code depends on:
    preparedTxs.resume.nothingToDo            <-- not in any documented schema
    preparedTxs.resume.routeAlreadyDeployed   <-- not in any documented schema
```

**Impact on agentic usage:** `CLAUDE.md` exists "so the agent [can] call MultiHopper
correctly without hallucinating endpoints or field names," yet it omits `resume` —
so an agent trusting `CLAUDE.md` treats `resume` as a hallucination and drops it,
while an agent copying the reference reads a field the schema denies. Loop
termination becomes undefined: premature exit (transfer left half-deployed) or
looping to the attempt cap despite progress.

**Proposed fix:** document the `resume` object (fields + semantics) in the prepare
response table and in `CLAUDE.md`:

> `resume` (object, present on re-`prepare` of an in-progress transfer):
> `nothingToDo` (bool) — every group already confirmed; stop the loop.
> `routeAlreadyDeployed` (bool) — route init complete; continue with remaining groups.

Alternatively, remove the dependency from the reference loop and drive termination
off the documented signals (all `preparedTxs` groups `null`, and/or transfer
`status`/`phase` from `GET /transfers/:id`).

---

## Contact

https://t.me/kirito_list1, https://x.com/kirito_list1
