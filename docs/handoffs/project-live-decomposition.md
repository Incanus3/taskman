# ProjectLive decomposition

- Status: active
- Updated: 2026-09-18
- Resume: `$resume project-live-decomposition`

Operations changes are merged. The operator selected this workstream and approved the dedicated
`project-live-decomposition` branch from refreshed `origin/main` at `73725e54`.
Implementation has resumed through the existing clean-session boundary.

## Objective

Split `TaskmanWeb.ProjectLive` into explicit workflow modules without changing its routes, socket
ownership, streams, events, or user-visible behavior.

## Durable references

- Design: [ProjectLive workflow decomposition](../specs/2026-09-03-project-live-decomposition-design.md)
- Plan: [ProjectLive workflow decomposition implementation](../plans/2026-09-04-project-live-decomposition.md)
- Beads feature: `tas-1tq`; implementation tasks: `tas-1tq.1` through `tas-1tq.9`

## Current checkpoint

All nine extraction tasks (`tas-1tq.1`–`.9`) are implemented and reviewed through
`b3933e99`, including the final cross-workflow picker fix. ProjectLive is the sole workspace
LiveView and a 182-line coordinator. Final independent review and scoped fix re-review are clean;
264 focused tests and 829 full `mix precommit` tests pass, with format/structural checks clean.
Existing negative-path runtime logs are nonblocking; compilation is warning-free.

`tas-1tq` stays open for `tas-1tq.10`. Three rolled-back test-database disappearance probes
established stale creation can save after hiding, pending autosave can clear a hidden draft,
and stale movement can retain an inaccessible error. Evidence and fixture limitations are in the
[design follow-up](../specs/2026-09-03-project-live-decomposition-design.md#missing-location-investigation-evidence).
No missing-location behavior fix or product List deletion has been implemented. The next gate is
behavior-design approval, including creation resumption versus copying/discarding input.

If directory removal runs first, preserve its accepted form/navigation behavior; neither
workstream requires the other. Missing-location extraction clears only listing results and
preserves creation/movement drafts. The separately gated behavior follow-up `tas-1tq.10` depends
on `.9`; keep it in this workstream until resolved. Explicit operator completion confirmation is
required before handoff retirement. Use the existing clean-session boundary; do not request a
second boundary solely for document refresh.

## Remaining execution sequence

1. Extraction and final review/fix verification are complete; preserve the verified baseline.
2. Current position: separately scope the
   [missing-location behavior follow-up](../specs/2026-09-03-project-live-decomposition-design.md#missing-location-behavior-follow-up)
   (`tas-1tq.10`). Review the reproduced creation/detail/movement failures and remaining
   source-only stale/pending action variants. Specify accessible recovery and action invalidation,
   explicit destination choice, surviving/missing Task recovery, movement reopening and draft
   lifetime. Obtain behavior-design approval before repository regression coverage or a fix.
3. After approved design and planning, implement the bounded recovery behavior, independently
   review it and run covering checks plus `mix precommit`. Do not implement List deletion.
4. Publication and merge require separate operator authorization and refreshed target state.
   Explicit workstream completion confirmation is required before closing the feature and retiring
   this handoff; if the follow-up is deferred instead, record that operator decision explicitly.

## Pending decision

The approved extraction is authorized on the selected branch. After extraction, the missing-location
behavior design still requires approval. Publication, merge, and explicit workstream completion
remain separate operator gates.

Before any eventual publication, refresh the target: GitButler currently reports a historical
same-name remote branch (`fb945c12`) requiring force. No push or history rewrite is authorized;
resolve the publication target explicitly rather than overwriting that remote by default.
