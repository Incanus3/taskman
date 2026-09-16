# Exact helper failure handling

Status: approved by the operator on 2026-09-16. Implementation plan approval pending.
Tracking: `tas-sr4b.20`, within candidate 3 of the
[ordered register](../research/2026-09-16-operations-simplification-candidates.md).

## Context and authority

The [reconciliation design](2026-09-09-deploy-reconciliation-design.md) owns mutation truth,
reports, final observations, cleanup authority and transport uncertainty. The
[dedicated-host design](2026-09-09-dedicated-host-deployment-design.md) and
[runbook](../deployment.md) retain their authority. This proposal changes only recovery from an
invalid in-process mutation handler result. It does not relax validation of replies received by
controllers. Operator approval amends the reconciliation design's helper failure boundary in this
bounded respect; existing wire schema and controller failure rules remain unchanged.

Baseline: source tip `ff24d0089c6b7d073a16fd1d47314150ea5dd432` on
`dedicated-host-deployment-automation`. Inspection/5 producer correction is committed as
`664926a7`; migration extraction and SOPS narrowing are complete. The workspace uses protocol 3.
No host action, translator removal or new failure recovery has been implemented by this design.

`host_helper/__main__.py` is the sole production consumer of `_legacy_mutation_result`.
Handlers already emit exact state, but translation reads retired `changed`,
`intended_release_id`, `final_observations` and related fields. It can weaken exact known-change
proof to unknown, lose backup/completion evidence and acquire another observation lock.
Source inspection also found deploy's service-stop exception emits retryable `stop`/10:
`_RetryableError("stop")` reaches a category table that excludes stop. The translator currently
repairs that to service/8. Service-start already maps to service/8. The stop producer must be
corrected before removing translation. `_RetryableError` already defaults to possible mutation;
this proposal does not reinterpret native command failures as unchanged.

## Chosen contract

One handler contract remains: a request-correlated `HostResult` containing exact mutation state.
There is no legacy reader or second handler evidence vocabulary. Valid results pass through,
including successful cleanup inspection's existing paginated authority contract. An invalid
success can only become failure; independently passing verification never reconstructs success.

Recovery may reuse independently valid evidence only from an in-process `HostResult` whose
protocol, operation and correlation match the request through `validate_result_for_request`.
Wrong type, mismatched envelope and thrown exceptions contribute no reply evidence. They use
unknown mutation failure (unchanged for cleanup inspection) and the existing safe observer.
This trust boundary concerns cooperative internal producers, not hostile Python objects.
Malformed, mismatched, oversized or lost wire replies remain wholly invalid at the controller:
unknown for the affected dispatch, retaining only earlier validated aggregate evidence.

The helper owns recovery policy. The shared protocol validator remains the single schema owner.
Use a small fixed set of complete failure-state projections against that validator to accept
independent groups; do not create another field parser, generic repair framework or duplicated
schema. Each projection substitutes one group into an otherwise valid unavailable failure base.
The finished result must pass full validation and request-dependent cleanup validation.

## Evidence groups and precedence

The base is a failed exact state with unavailable final facts and a fixed redacted internal-failure
message. Deploy/genesis use retryable inspection/8; restore uses retryable inspection/11;
cleanup uses refused inspection/10. Report is null, restore safety-copy identity is null and
cleanup completion is empty. Request-derived desired release and input backup identities may be
recorded when valid; they establish intent only, never publication or completion.

1. **Mutation classification.** Retain canonical `unchanged`, `changed` or `unknown` from the
   matching internal result. Missing or invalid classification becomes unknown. Cleanup inspection
   is always unchanged and has no completions. Never infer classification from old boolean fields,
   observations, a passing report or failure category. A lock category cannot survive a retained
   changed/unknown classification because full validation requires unchanged.
2. **Identities.** Validate operation-specific release/backup identities as one group using a
   complete failure projection. If invalid, use the safe request-derived defaults above. Do not
   guess a pre-restore backup or restored source from current filesystem facts.
3. **Cleanup completions.** Accept only an entirely valid sorted unique group whose exact
   kind/identifier/path triples belong to the confirmed request batch. Reject the entire group
   if any member is malformed, duplicated or outside that batch. Completion can coexist with
   unchanged mutation for a target already absent. It is evidence only and authorizes no further
   deletion. Move the existing request-membership invariant from `workflows/helper.py` into the
   shared protocol owner and have both consumers call it; retain success/all-target and
   inspection/no-completion checks. Do not duplicate target parsing.
4. **Report and primary failure.** Cleanup reports are always ineligible, even if their standalone
   schema validates; discard them while retaining eligible cleanup proof and its coherent failure
   tuple. For deploy/genesis/restore, validate the report independently through the existing report
   validator, then choose a coherent failure tuple. If a valid failed report exists, verification
   with its exit 8 or 9 takes precedence over an incompatible or malformed supplied category;
   emit the fixed internal-failure message and warning when repairing that contradiction. If the
   supplied nonzero outcome/status/boundary tuple is already coherent with retained evidence,
   preserve it and its safe message. A valid passing report survives a later primary failure.
   An invalid report is omitted. If the remaining supplied tuple is invalid, or deploy/genesis
   verification lacks its required valid report, use the base internal-failure category. Never
   attach a failed report to inspection/history or convert it into a passing report.
5. **Final facts.** Treat observations, unavailable_fields and inspection_error as one validated
   group. Restore's database arrangement and top-level migrations remain a dependent unit.
   Reuse a valid operation-owned final group without another observation. For missing/invalid
   groups, make at most one existing read-only post-invocation observation attempt under the
   lifecycle lock. Do not salvage arbitrary individual fields or reuse an earlier snapshot as
   final. On lock/error/invalid observation output, retain unavailable facts with the appropriate
   safe inspection error. Follow-up failure never replaces the chosen primary category/report.

Construct a fresh exact failed result with a fixed warning that invalid internal evidence was
rejected. Do not expose raw exceptions or add arbitrary invalid original warnings. Validate once
more before encoding. Contradictions in optional sibling groups cannot erase separately accepted
mutation, report or completion proof. No recovery path invokes the mutation handler twice.

The observer uses the existing five-second lock acquisition timeout and existing per-command
bounds; five seconds is not a total observation deadline. Cleanup observes filesystem authority
only. This proposal adds no service, database or filesystem mutation during recovery.

## Encoding boundary

Encoding failure must not trigger another observation or the current generic evidence-erasing
fallback. From a semantically validated result, retain classification, coherent primary failure,
report, identities and completions. If the original result was successful, choose the operation
base nonzero failure category/outcome above, preserving any eligible passing report and completion
proof; succeeded/0/null cannot survive unavailable final facts. Replace the final-fact group with
unavailable facts, discard
optional original diagnostics, and emit a fixed redacted encoding warning. Revalidate and encode
this small failure envelope. At most two encoding attempts are allowed.

Required report and completion evidence must not be truncated to fit. Implementation must prove
that the reduced envelope fits the existing 1 MiB bound using maximum valid report strings and
64 maximum-sized completed targets, JSON escaping and all identity fields. Existing input/output,
collection, string, migration and depth limits remain unchanged. Large observational integers or
other encoding failures are removed with the whole fact group. If the existing validator permits
required evidence exceeding the reduced envelope's bound, stop and return to design review rather
than silently dropping proof or introducing a retry loop.

## File boundaries and excluded work

- `ops/taskman_ops/host_helper/__main__.py`: exact dispatch validation, bounded recovery and
  evidence-preserving encoding fallback. Remove `_legacy_mutation_result` and helpers used only
  by it after a consumer search. Keep read-only contracts and cleanup inspection pagination.
- `ops/taskman_ops/host_protocol/mutation_results.py`: shared request-dependent cleanup invariant;
  existing schema/report validation remains strict. No new module or packaging allowlist required.
- `ops/taskman_ops/workflows/helper.py`: consume that shared invariant; wire rejection and earlier
  batch aggregation retain current behavior.
- `ops/taskman_ops/host_helper/operations/deploy.py`: emit canonical service/8 for stop failures;
  canonicalize the directly related start producer if removing its private alias. Remove only
  obsolete category aliases proved unused. Preserve command order and possible/known mutation.
- Corresponding helper, protocol, controller and packaged-boundary tests own verification below.

No import/startup optimization, new protocol version, backup/restore mechanism, compatibility shim,
CLI surface, unrelated alias removal, global test deletion or external operation is included.
No line-count or wall-time saving is claimed; implementation review must assess whether the fixed
recovery policy removes interpretation branches without replacing them with a larger framework.

## Alternatives and accepted trade-offs

Making every invalid internal result unknown with no report/completions is simpler, but loses
consequential evidence already required by the failure contract. Retaining the translator keeps
old-shape inference and its demonstrated degradation. A second typed handler interface adds a
migration contract without removing more behavior. The proposed fixed projections reuse strict
validation, at the cost of explicit local precedence and focused malformed-result coverage.
Rejecting a whole invalid final-fact group can lose otherwise valid individual facts; one bounded
fresh observer is the accepted replacement. Controller wire consumers receive no partial salvage.

## Verification and acceptance

Use test-first implementation in bounded checkpoints; exact producer regression precedes removal.
Retain all existing consequential outcomes and update invalid injected reports to schema-valid
fixtures, rather than removing their tests. Required mapping:

- Entrypoint history/report test and operation-owned observation-reuse test use exact states;
  restore dispatch fixture uses exact refused/input/2/unchanged state. Preserve exception redaction,
  one observer, no success reinspection and inspect-only unchanged outcomes.
- Actual deploy stop failure validates service/8 and possible/known mutation. Deploy runtime
  fixtures currently returning `{ok: true}` or `{ok: false}` become valid full passing/failed
  reports. Preserve actual later-history failure and inspection/5 evidence regressions.
- New focused recovery cases cover changed with malformed sibling facts/report, passing report
  with later failure, failed report/category contradiction, invalid success, invalid/mismatched
  envelope, valid completion subset with invalid sibling, invalid/out-of-batch/duplicate
  completions, cleanup with schema-valid but ineligible passing/failed reports, malformed identities,
  observer lock/error and serialization failure of both valid success and valid failure. Assert exact
  state and observer invocation count, not private translator names.
- Existing restore lock reacquisition retains its two operation-owned acquisitions; entrypoint
  adds none when final facts validate. Cleanup partial deletion and earlier completed targets
  retain distinct proof. Shared/controller tests still reject malformed wire replies and
  unauthorized completion claims, retaining earlier batches and known aggregate change.
- At least one isolated packaged-helper boundary proves exact failure encoding/report retention;
  preserve current real-filesystem public cleanup and lost-reply restore cases. A substituted
  CLI WorkflowResult alone is insufficient. Maximum-size reduced envelope tests prove bounds.

Run focused helper/protocol/controller tests, actual archive entrypoint coverage, then the full
four-worker operations gate, locked dependency sync, compileall, shell syntax and `mix precommit`.
Check current implementation surfaces for leaked planning terminology. Obtain distinct scoped
correctness review of this increment, not a full-branch review. Local evidence establishes neither
native systemd/PostgreSQL/UFW/ACME/email/reboot nor destructive restore acceptance.

## Design checkpoint evidence

Distinct scoped correctness review inspected the design and relevant producers/validators/tests.
Two findings were corrected and independently closed: cleanup report ineligibility and encoding
failure of valid success. No remaining blocking design findings were reported. Source inspection
identified the stop-category defect; no runtime reproduction or implementation validation is
claimed. Local Markdown links/anchors, whitespace, portable markup, eight-candidate register and
six-step handoff/gate checks passed. Fresh `mix precommit` passed 805 tests in 43.4 seconds.
Operations runtime tests were not rerun for this documentation-only checkpoint; the preceding
source checkpoint passed 1,530 cases, as recorded in the handoff.

## Approval and next-session checklist

The operator approved this written design on 2026-09-16 after discussing risks and consequences.
Approval selects the
in-process evidence policy, failed-report precedence, whole-group observation rejection and bounded
encoding fallback. Revision changes those decisions before planning; deferral leaves the translator
and stop defect tracked. After approved design and approved plan, update the handoff and resume in
a fresh session before implementation by default. Local checkpoint commits are authorized;
push, history rewriting, merge and host actions retain their separate gates.

Next session: read this complete design and canonical reconciliation specification; confirm current
source/Beads state; review the [bounded implementation plan](../plans/2026-09-16-exact-helper-failure.md); retain ordered
candidate review 4–8 and pre-merge steps 4–6 from the
[workstream handoff](../handoffs/ops-vps-readiness.md). Do not implement from a proposed design.
