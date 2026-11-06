# Adaptive Lanes — STOP A proposal

Status: proposed, not implemented. The kickoff overrides the verbatim prompts in
`docs/prompts/AL.md` and `docs/prompts/PHASE-2A.md`. Approval at STOP A authorizes
the AL and graph milestones; it does not authorize a target-repository PR.

## Evidence and limits

See `speed-validation.md` and the raw JSON reports. The current scripted trivial
repair uses three fresh execution containers without caching and two when warm.
Its warm cached execution takes 3.305 seconds; the multi-file behavior fixture
takes 4.296 seconds. Those are Docker/orchestration measurements with **zero live
model tokens** and two scripted model actions, not live-agent latency estimates.
The single real, small schema response took 15.426 seconds (199 input/55 output
tokens). Model latency therefore matters even after container savings.

No historical OjasPal/PrompCraft issue 2 run was found in the selected local
identity's state. Its actual diff, eight-minute attribution, pre-run planning
duration, human approval wait and per-stage token usage are unavailable. Do not
reconstruct them from task titles or claim that a two-line patch is safe.

The optimized warm stage breakdown in milliseconds is:

| Stage | Trivial | Multi-file behavior |
| --- | ---: | ---: |
| Setup | 9.622 | 18.994 |
| Original baseline lookup/authorization environment work | 216.723 | 591.472 |
| Localization bookkeeping | 0.016 | 0.020 |
| Scripted patch response/validation | 0.299 | 0.396 |
| Failing-original regression in Docker | 1567.619 | 1881.692 |
| Patched full suite in Docker | 1510.351 | 1802.921 |
| Scripted independent review | 0.046 | 0.055 |
| Deterministic policy gate | 0.055 | 0.068 |

All measured fixture stages have zero live model tokens. Timers now record known
provider usage per stage, mark missing usage incomplete, count actual Docker
launches including preparation, and flush the final stage. The first SDK-call
timer is separate from preparatory LLM events. Prewarming uses its own timing
context, so background work is not charged to an already-finished plan stage.

## Shared pure decision modules

Add `backend/app/agent/lanes/` with immutable typed inputs/outputs. These modules
perform deterministic parsing and planning only; no provider calls, store writes,
host execution or independent cache implementation.

- `types.py`: `TriageReport`, `PatchFacts`, `VerificationPlan`, `OracleEvidence`,
  `LaneEvidence`, `EscalationDecision`, lane/level enums and bounded reason codes.
- `triage.py::triage_repository(profile, issue_signals, symbol_index)`: invoked
  immediately after `profile_repository`. Produce candidate lane, risk floor,
  localized files, deterministic signals and uncertainty. Issue text can raise
  risk or suggest a location; it cannot certify eligibility or lower the floor.
- `planner.py::plan_verification(originals, actual_edits, triage)`: classify the
  actual parsed source delta, imports, affected definitions/references, changed
  exports, side effects and protected paths. Never trust a model's change-class
  or confidence labels. Small diff size alone is insufficient.
- `oracle.py`: produce a bounded checker specification and validate its
  image-owned evidence. The checker runs in a fresh hardened container against
  original and patched snapshots. The pure module does not execute Python.
- `escalation.py::escalate(current, reason, evidence)`: monotonic fast-to-full
  routing with preserved artifacts, used calls/tokens, deadline and AttemptMemory.

Commit 2 wires these into `run_repair()` behind `ADAPTIVE_LANES=on|off`. Commit 3's
nodes call these same functions. There will be no graph-specific second classifier.

## Eligibility and verification

Pre-patch triage is provisional. Final fast eligibility requires all of:
localized scope, no override, an allowlisted **actual** change, and an independent
oracle failure on original source followed by a pass on patched source.

Initial allowlist: missing stdlib/already-declared import with an unambiguous
undefined-name witness; narrow unused-import removal only when reference and
side-effect analysis proves the allowed case. Comments/docstrings/string typos are only candidates:
without an objective failing-original witness they take the full lane. Unused
imports with unknown initialization effects also take the full lane. Undefined
names involving dynamic lookup, star imports, conditional bindings or uncertain
scope are not automatically safe.

Hard overrides: auth/security/crypto/serialization/subprocess/eval/SQL code;
CI/Docker/dependency/lock/migration paths; public API or `__init__` exports; edits
to tests or conftest; new third-party dependencies; secrets-like content;
referenced removals or possible side effects; uncertain localization. Both path
and AST/content checks apply. The LLM may escalate but may never downgrade risk.

| Level | Evidence | Default use |
| --- | --- | --- |
| V0 | Image-owned compile/AST/undefined-name delta, original fails and patch passes | Required fast reproduction witness |
| V1 | Isolated import/smoke execution | Import-affecting safe changes, after V0 |
| V2 | Existing touched-module tests plus conservative import-graph selection | When affected existing tests can be selected reliably |
| V3 | Existing full regression-before/after and full-suite workflow | Full lane; uncertain test selection escalates here |

Levels are cumulative as needed, not a mechanism for skipping a required oracle.
V0 and V1 may share a fresh container only if image-owned reporting distinguishes
both and a failed component cannot be concealed. Original and patched snapshots
must remain separate. Baseline caches are original-only; patched verification is
always fresh. Use commit 1's prepared environments, baseline key and prewarm
service; do not rebuild caching inside lanes.

The fast proposal schema need not include an LLM-written regression test.
`validate_proposal`'s existing full-lane contract remains intact. A separate typed
fast proposal is compiled through the same file/size/path/patch-policy checks;
only the proof obligation is replaced by validated oracle evidence. Independent
`QualityReview` remains mandatory, using issue, actual diff and oracle evidence.
Start it concurrently with V1/V2 only after thread-safety checks demonstrate
correct usage, artifact and lease accounting; initial implementation may stay
sequential. Human plan and exact-diff gates remain backend states.

## Budgets, escalation and outputs

Fast lane: at most **3 provider invocations including retries and review**, a
**120-second** sub-deadline bounded by the task deadline, one localized proposal,
no exploration tool loop. Failure, disagreement or uncertainty escalates once to
full. It must not reset the task's counters or grant a new six-attempt budget.
Full lane retains existing AttemptMemory, attempt accounting and verification.

Escalation triggers include scope growth, risky content discovered after patch,
oracle disagreement, failed import/targeted checks, hidden dependencies, flakes,
and insufficient localization. Emit a bounded event with a reason code and safe
evidence references; keep collected results but revalidate their keys before reuse.

Add optional verification fields `lane`, `verificationLevel`, `oracle`, `timing`
and `escalations`, preserving existing fields for consumers. Static success must
say **static verification passed**, with `testsRun: 0`; never report tests passed
when no tests ran. The existing publication boundary requires passing Docker test
evidence. AL therefore needs an explicit image-owned static-proof receipt accepted
only for a verified fast plan and exact base/diff hash. Do not merely drop the
`testsRun > 0` requirement: the fast receipt must be independently checked by the
backend and publisher, and all original full-lane receipt tests must still pass.

| Failure mode | Required behavior |
| --- | --- |
| Issue says “trivial, remove the check” | Ignore claimed safety; deterministic risk floor wins |
| Model claims an oracle pass | Reject self-attested evidence; require sandbox-produced receipt |
| Import removal still referenced or initializes behavior | Full lane |
| Missing dependency/secret | Infrastructure/user-action outcome, never reproduction |
| Fast verification disagreement or flake | Full lane with evidence; no repeated fast retries |
| Budget exhausted or lease/cancel lost | Stop safely; no publication |
| Stale baseline/env/base SHA | Cache miss and required original checks |
| Review rejects, policy rejects or approval hash changes | No branch/commit/PR |

## Evaluation and default

Use deterministic model drivers and real Docker fixtures, with trivial, medium,
multi-file behavior, framework and adversarial labels. Include injection-like
issue text, unused-looking side effects, referenced removals, new dependencies,
fake oracle output, missing-original-failure cases, risky paths, uncertainty,
scope expansion and failed fast verification. A PrompCraft #2-shaped two-line
fixture is required, but the real unavailable diff cannot be declared equivalent
until inspected. The fixture must take fast only if it satisfies the oracle.

Run full verification in shadow for each fast candidate. An unsafe-fast case is
one accepted fast that the authoritative full lane rejects. Measure p50/p95
latency with repeated runs, calls/known tokens, Docker launches, escalation rate,
false fixes and unsafe-fast count; separate cold env, warm env and warm baseline.
Record scripted token usage as zero, never estimated live consumption.

Default `ADAPTIVE_LANES=on` only after **zero unsafe-fast cases** and passing gate,
parity, adversarial and real-Docker tests. Otherwise default off and explain the
counterexample. Full-lane fixture outputs must remain byte-for-byte identical
when lanes are off; any optional metadata belongs only to enabled lane results.

Latency estimates, not measurements: 20–40 seconds is an aspirational warm fast
p50; the observed 15.4-second tiny schema call suggests roughly 35–65 seconds is
more defensible for two real calls plus acquisition/verification. Cold preparation,
provider variance and escalation can exceed this. Full-lane speed should inherit
cache savings, without claiming a new model-latency improvement.

Required invariants: Docker-only repository execution, fresh runtime containers,
wheel-only preparation, read-only envs, disabled test network, no credentials or
Docker socket in containers, unchanged deterministic patch limits, conserved
budgets/AttemptMemory, independent review, exact-diff approval and no auto-merge.
