# Adaptive Lanes handoff

Updated 2026-11-06. See the newest dated handoff and `docs/ROADMAP.md` first.

The speed implementation, measurements and schema probe are complete for commit 1;
browser/teammate/tunnel verification limits are explicit in `speed-validation.md`.
The AL proposal is `docs/adaptive-lanes-proposal.md`; graph design is
`docs/phase-2a-graph-design.md`. Original prompts are preserved verbatim.

**STOP A approval is still pending.** No AL implementation or graph engine exists.
After explicit approval, implement exactly the next two milestones (`AL`,
`2A-graph`) on `agent/workflow-optimization`; no additional planned approval stop,
no push, no merge. Shared pure lane modules must work in legacy run_repair first,
then be called by graph nodes. Default ADAPTIVE_LANES on only if shadow evaluation
finds zero unsafe-fast cases. Preserve all policy, exact-diff approval, sandbox,
budget, original/patch evidence and independent-review gates.
