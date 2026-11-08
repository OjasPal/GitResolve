# Phase 2A execution graph — STOP A design

Status: design only. Commit 3 follows the approved AL implementation, using the
same pure lane modules described in `adaptive-lanes-proposal.md`.

## Execution shape

`intake/triage -> repo_profile -> env_prepare -> baseline/cache -> lane_router`

- Fast: deterministic localization -> one minimal-context patch action -> actual
  diff planner/static oracle -> planner-selected verification -> minimal review
  -> deterministic policy/finalize.
- Full: explore model/tool loop -> reproduce -> patch model/tool loop -> static
  checks -> full suite -> independent review -> policy/finalize.
- Escalation: any fast node may route to full exploration with evidence, current
  overlay, used budget and reasons. No full-to-fast edge. Classified failures
  route to exploration or patch repair; no blind regeneration of all context.

The `StateGraph` replaces repair execution only. Planning, execution and PR remain
separate backend dispatch phases. Nodes return an AgentRunner result; backend
services own statuses and plan/diff approval. No LangGraph `interrupt()` and no
automatic publication. `ask_user` produces a bounded needs-input result that the
backend maps through existing lifecycle contracts.

## Durable state and workspace

Typed `RepairState` holds task/phase/base identity, pinned engine and schema
version, profile/environment references, `TriageReport`, lane/floor/escalations,
bounded hypotheses/evidence, localization, validated edits, attempt summaries,
budget counters, provider metadata and the structured outcome. Use reducers for
messages and attempts, with explicit bounded compaction and retained evidence IDs.
Do not checkpoint hidden reasoning, secrets, absolute temp paths or entire logs.
Large files and outputs are bounded artifacts with ownership checks.

Workspace is a fresh pinned source snapshot plus cumulative validated text edits.
Reads resolve through the overlay. A resumed phase reconstructs that overlay and
checks its base/diff hashes before running anything. Each execution uses a fresh
hardened `ctx.execute(commit_sha, command, patch=overlay_patch)` container; it
cannot reuse a container, trust a stale temporary path or mount host credentials.
The graph consumes speed's cache services and AL's planners/oracle specifications.

Use SQLite only in 2A, with checkpoint thread ID `f"{task_id}:{phase}"`. Resume
tests must crash between meaningful nodes, rebuild workspace, retain counters
and avoid replaying publication. SQLite is a local worker deployment option,
not a Firestore replacement or multi-host saver. Automatic reaper-driven resume
and Firestore saver are deferred to 2B. Validate leases before loading/resuming
and `ctx.checkpoint()` on every step, including after long tools/provider calls.

Pin `AGENT_ENGINE=graph|legacy` and checkpoint schema per phase so changing the
environment cannot silently swap a running phase's implementation. Rollback uses
the legacy engine for new phases; incompatible checkpoints fail closed with a
recoverable outcome. Keep the legacy path until scripted parity and recovery
tests pass. Do not delete `agent/repair.py` while router tests still reference it.

## Actions and tools

Probe result: the current Google SDK rejects Pydantic `discriminator`/`oneOf`
before successful transport. A flat action enum plus bounded typed args succeeded
through ModelRouter (199 input/55 output tokens). Use that flat envelope with
per-tool Pydantic post-parse validation, including argument relationships, limits
and prohibition of unknown fields. Malformed/unknown actions return structured
feedback to the model and count against steps/tools; never a host crash or shell
fallback. Every provider call, retry and review goes through ModelRouter.

Registry:

| Tool family | Contract |
| --- | --- |
| list_tree, grep, numbered read_file | Bounded snapshot/overlay reads; path and result caps |
| find_symbol, find_references | Static AST analysis; dynamic uncertainty is explicit |
| run_tests, run_check | Fresh `ctx.execute` with pinned base + overlay; policy-validated argv |
| run_snippet | New narrow image-owned checker command, not arbitrary host or shell execution |
| edit_file | Exact, uniquely whitespace-matched, or validated line-range edit; ambiguous matches reject |
| create_file | Same protected-path, symlink, extension and size limits as edits |
| git_diff | Deterministic diff from pinned originals and validated overlay |
| ask_user, report_blocker, finish | Structured outcomes; finish cannot bypass evidence/review/policy |

Validate paths against traversal, symlinks, protected files and actual patch/file
limits. Tool output is untrusted data. Bounded `EventType.TOOL_CALL` records expose
tool name, safe path, duration, result summary and attempt—not model reasoning.
Tool execution is not proof of success until the corresponding deterministic
evidence validator accepts it.

## Budgets and required validation

Enforce configurable ceilings in code: 240 steps, 120 tools, 100 provider calls
(including retries/review), 300K input and 40K output tokens, task's 30-minute
deadline and six strategies. AL's tighter fast sub-budget is additional, never
an exemption. Preserve counters across checkpoints/retries/escalation; report
missing provider usage as unknown rather than zero. Keep known consumed usage
even when a provider or infrastructure failure does not consume a repair attempt.

Scripted scenarios: NameError/CSV-export with read/explore/edit/test/failure/new
edit; MCQGen-shaped repair; oracle-eligible PrompCraft #2-shaped fast patch;
fast failure escalating with evidence; SQLite crash/restart mid-phase; legacy
parity. Real Docker tests cover isolation, original regression failure, patched
verification, gate receipts and fresh containers. Lifecycle/publication tests
must prove approval hash changes reject publication and no auto-merge path exists.
Run a shadow full-lane evaluation for fast fixtures and report unsafe-fast cases.

Outstanding implementation risks to resolve in commits 2/3: narrow static-proof
publication receipts, schema migration/pinning, SQLite ownership/concurrency,
safe snippet command validation and conservation of usage on failed actions.
These are implementation work, not permission to weaken existing gates.
