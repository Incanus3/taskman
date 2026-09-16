# Exact helper failure handling implementation plan

Status: approved by the operator and locally completed on 2026-09-16.
Tracking: `tas-sr4b.21`; delivery remains within `tas-sr4b.16` candidate 3.

**Goal:** Remove legacy mutation-result translation while preserving independently valid internal
failure evidence and strict controller wire validation.

**Architecture:** Exact correlated HostResult is the only handler contract. Entrypoint recovery
uses fixed complete failure projections through the shared validator; one safe observer supplies
missing final facts. Encoding reduction preserves required proof without another observation.

**Technology:** Existing Python controller and standard-library host helper, pytest and zipapp.

**Specification:** Read the complete [approved exact failure design](../specs/2026-09-16-exact-helper-failure-design.md)
and [canonical reconciliation specification](../specs/2026-09-09-deploy-reconciliation-design.md)
before execution. The specification owns policy; this plan owns sequence and interfaces.

## Global constraints and execution gate

- Protocol 3 and existing 1 MiB input/output bounds remain unchanged; maximum completed targets is 64.
- No legacy reader, second handler vocabulary, schema duplication, generic repair framework or new module.
- Controller malformed-wire rejection remains strict; earlier validated aggregate proof survives.
- Cleanup inspection remains unchanged with no completion or report, retaining successful pagination.
- Never repeat a mutation handler; at most one post-invocation observer and two encoding attempts.
- Required report/completion proof must not be dropped to fit. Stop for design review if size proof fails.
- No host action, startup/import optimization, unrelated cleanup or test deletion is authorized.
- Start implementation in a fresh session after plan approval. Use delegated task execution and a
  distinct verifier per consequential checkpoint under repository policy. Execute tasks sequentially;
  coupled recovery/encoding ownership must not be split across concurrent editors.
- Before each commit, verify the bounded deliverable, update Beads/canonical evidence/handoff and
  inspect `but diff`. Commit locally with GitButler. Push/merge/rewrite/deployment have separate gates.

## File and interface map

Existing `host_helper/__main__.py` owns dispatch and recovery. Existing
`host_protocol/mutation_results.py` owns exact schema and the moved cleanup invariant;
`host_protocol/__init__.py` exports it. `workflows/helper.py` becomes its wire-side consumer.
`host_helper/operations/deploy.py` owns native service category correction. Corresponding existing
tests retain their seam ownership; focused recovery cases may use a new test file only if shared
fixtures are explicitly factored into test support without production changes.

New public interface in task 2:

```python
def validate_cleanup_completion(
    request_value: HostRequest, outcome: str, state: Mapping[str, object]
) -> None:
    """Validate request-dependent completion after exact cleanup state validation."""
```

The body is the existing `_validate_cleanup_completion` implementation moved from
`workflows/helper.py`, not a new target parser. Use the existing envelope HostRequest import
without introducing a cycle (envelope does not import mutation_results). No private compatibility alias.
Entrypoint-private recovery signature: `_invalid_mutation_result(request: HostRequest,
result: HostResult) -> HostResult`. It accepts only a matching envelope; caller rejects mismatch.
Encoding continues through existing `_encode_or_internal_failure` signature.

## Task 1: Canonical service failure producer and exact test fixtures

Files: deploy.py; tests/host_helper/test_deploy.py and test_entrypoint.py.
Consumes existing HostRequest/HostResult and validators; produces service/8 exact failure and
schema-valid runtime reports, with no recovery-policy change.

- [x] Add an actual deploy runtime stop-error regression using the existing runtime seam. Raise
  CommandError from `change_service("stop")`; assert full state validates, boundary service,
  exit 8, unknown when no prior proved mutation and changed when earlier mutation is proved.
  Retain runtime event/command order assertions. Run the regression and confirm category rejection.
- [x] Replace the native stop catch's category and directly related start category with canonical
  service; retain existing possible-mutation default. Remove start/observation table aliases only
  after `rg` proves no remaining producer needs them. Minimal category edit:

```python
except CommandError as error:
    raise _RetryableError("service") from error
```

- [x] Replace deploy runtime `{ok: True}`/`{ok: False}` reports with complete valid reports using
  the existing eight-check passing schema and failed ordered-prefix schema. Validate each fixture
  with `validate_verification_report`; expected/actual release identities must match the scenario.
  Convert entrypoint history and restore-dispatch fixtures to exact states; history state includes
  changed/history/8, identities, final facts/availability tuple and passing report. Convert observation
  reuse fixture to canonical observations/unavailable_fields/inspection_error. Forbid reinspection
  when those operation-owned facts validate. Remove translator-name monkeypatches in favor of
  public exact-state, identity and observer-count assertions.
- [x] Run focused gate below; distinct scoped producer/fixture review; record evidence and commit
  `Emit canonical service failures and exact helper test fixtures`.

```sh
uv run --project ops pytest ops/tests/host_helper/test_deploy.py ops/tests/host_helper/test_entrypoint.py ops/tests/host_protocol/test_mutation_results.py
```

Expected: all selected cases pass; no removed outcome coverage or weakened protocol assertion.

## Task 2: Shared cleanup completion authority

Files: mutation_results.py, host_protocol/__init__.py, workflows/helper.py;
tests/host_protocol/test_mutation_results.py and tests/workflows/test_helper.py.
Consumes exact validated cleanup state; produces the public interface defined above.

- [x] Add direct shared-predicate tests for a valid failed subset, unchanged already-absent
  completion, out-of-batch triple, duplicate request identity, inspection mutation/completion and
  incomplete success. Retain controller's existing outside-confirmed-batch and earlier-batch tests.
  Run direct tests; confirm the missing export/function is the failure, not broken fixtures.
- [x] Move the existing predicate verbatim and export it; replace controller call/import and remove
  old private implementation. Keep full schema validation before request-membership validation.
- [x] Run focused gate, independent scoped authority review, record evidence and commit
  `Share cleanup completion validation between helper and controller`.

```sh
uv run --project ops pytest ops/tests/host_protocol/test_mutation_results.py ops/tests/workflows/test_helper.py ops/tests/host_helper/test_cleanup.py
```

Expected: strict schema and membership rejection remain; valid subsets and prior batch proof survive.

## Task 3: Exact invalid-result recovery and encoding boundary

Files: host_helper/__main__.py; tests/host_helper/test_entrypoint.py;
relevant protocol/controller tests and packaged entrypoint coverage in existing helper tests.
Consumes shared validators plus tasks 1–2 exact producers/cleanup predicate.
Produces `_invalid_mutation_result` and evidence-preserving `_encode_or_internal_failure`.
This is one coupled deliverable: do not leave recovered proof vulnerable to the old encoding fallback.

- [x] Add focused tests using exact baseline states. Example baseline for deploy (reuse existing
  RELEASE and observation fixtures):

```python
state = {
    "mutation_state": "changed", "exit_code": 8, "failed_boundary": "history",
    "observations": mutation_observations(), "unavailable_fields": (),
    "inspection_error": None, "report": passing_report(),
    "desired_release_id": RELEASE, "backup_id": None,
}
validate_mutation_state("deploy", "retryable", state)
```

  Corrupt one sibling group at a time. Required matrix: classification changed/unchanged/unknown
  with malformed facts/report/identities; valid passing report/history; valid failed report with
  conflicting category; invalid success; wrong type/protocol/operation/correlation; valid cleanup
  subset with malformed sibling; duplicate/out-of-batch completion; cleanup with standalone-valid
  passing/failed report; restore dependent arrangement inconsistency; observer lock/error/invalid
  output; encoding failure of valid failure and valid success. Assert fully valid nonzero result,
  selected proof, fixed redaction and handler/observer counts. Run the focused cases and explain
  each failure against current translation/evidence-erasing fallback before implementation.
- [x] Validate request correlation before any salvage. Preserve existing cleanup successful-inspect
  exemption. Replace invalid exact mutation state dispatch with `_invalid_mutation_result`.
  Construct the operation base through existing unavailable failure facilities with observe=False.
  Use fixed validator projections for classification, IDs, completions and fact group. Accept
  completions only through full schema plus shared request predicate. No retired keys are read.
- [x] Implement design precedence: cleanup report always ineligible; independently valid failed
  report selects matching verification/8 or /9 when original category is incompatible; coherent
  original failed tuple/message survives otherwise; passing report can accompany a later failure;
  invalid success uses base nonzero tuple. Invalid report is omitted, with report-required
  verification category rejected. Preserve only safe diagnostics specified by the design.
- [x] Reuse valid operation-owned fact group. Otherwise call existing safe observer once; validate
  its complete fact group, falling back to unavailable facts on lock/error/invalid output. Preserve
  primary report/category when observation fails. Validate final failed state and cleanup membership.
- [x] Replace encoding fallback: first encode normal result; on failure, use validated proof and
  nonzero base tuple for a former success, replace whole fact group with unavailable, fixed warning,
  no original diagnostics and no observer. Validate reduced result and perform only second encode.
  Preserve existing nonmutation fallback. Search and remove translator and translator-only helpers.
- [x] Add size proof tests using maximum valid escaped strings, 64 maximum-path targets, IDs and
  report bounds in appropriate operation envelopes (cleanup cannot report). Verify reduced payload
  length <= MAX_OUTPUT_BYTES and decode/full validation. Cover unusually large observational ints
  at the actual serializer boundary. If required proof itself can exceed bound, stop for design
  review; do not truncate, loop or weaken limits.
- [x] Exercise at least one actual isolated zipapp entrypoint with an invalid cooperative handler
  result and encoding/report retention, using test-only injection in copied package inputs. Validate
  archive result, process status/stderr redaction, exact envelope and no repeated mutation. Do not
  add production injection switches. Preserve current packaged public partial cleanup/lost restore.
- [x] Run focused gate, distinct correctness review of this increment and its consumers; record
  evidence and commit `Replace legacy helper translation with exact failure recovery`.

```sh
uv run --project ops pytest ops/tests/host_helper/test_entrypoint.py ops/tests/host_helper/test_deploy.py ops/tests/host_helper/test_restore.py ops/tests/host_helper/test_cleanup.py ops/tests/host_protocol/test_mutation_results.py ops/tests/workflows/test_helper.py ops/tests/workflows/test_helper_deploy_transaction.py
```

Run the new isolated archive case explicitly in addition to this gate. Expected: all pass, strict
wire rejection unchanged, no original proof lost through the second serialization boundary.

## Task 4: Integrated acceptance and durable checkpoint

Files: candidate register, approved spec implementation evidence, workstream handoff and Beads.
No unrelated source changes. Consumes all three reviewed commits; produces verified candidate-3
completion with candidates 4–8 and pre-merge steps 4–6 still discoverable.

- [x] Self-check every design acceptance row against tests and source, inspect net removed
  interpretation branches, search translator/retired keys and planning terminology in changed
  implementation surfaces. Explain any supported remaining occurrence; do not expand to branch review.
- [x] Run sequential complete local gates with no concurrent source edits/builds:

```sh
uv sync --locked --project ops
uv run --project ops python -m compileall -q ops/taskman_ops ops/tests
uv run --project ops pytest ops/tests -n 4 --dist worksteal --max-worker-restart=0
bash -n ops/taskman ops/caddy/render-caddyfile
mix precommit
```

  Expected: all pass. Investigate failures through focused diagnosis; do not repeat passing full
  gates absent changes or unresolved concerns. Check scoped Markdown links/whitespace and helper
  packaging imports. No release build claim or real-host acceptance follows from zipapp tests.
- [x] Record exact verification, review dispositions and limitations in canonical owners. Close
  approved delivery issues only after acceptance, update register to candidate 4 decision, preserve
  candidates 4–8 and all later gates. Review handoff diff accounting for every retired instruction.
- [x] Inspect `but diff`, commit `Record verified exact helper failure recovery checkpoint`, confirm
  commit identity and intentional remaining dirty state. Stop with next candidate, decision and
  consequences. No push, merge, squash or deployment.

## Plan review evidence

Distinct scoped review approved this plan with no blocking findings after direct source/design
inspection. Import direction, checkpoint coupling and copied-source isolated archive injection
were checked. No runtime checks were performed by the reviewer; implementation complexity,
serialization bounds and native effects remain unproved. Scoped Markdown/placeholder/sequence
checks passed; fresh `mix precommit` passed 805 tests in 43.7 seconds. The operator authorized
including deletion of the earlier controller-simplification specification; no repository Markdown
references to that file remained.

## Plan self-review and approval

Coverage: task 1 handles producer/fixture migration; task 2 owns shared completion authority;
task 3 covers exact recovery, observation, report precedence, serialization and archive boundary;
task 4 closes full verification and durable continuation. Interface names and prerequisites are
explicit; no runtime size or simplification benefit has yet been established.

The operator approved this sequence on 2026-09-16 and explicitly authorized continuing in
the approval session. All four tasks are complete. Checkpoints: `956ce27`, `f00f904`, `6bd7e23`.
Distinct scoped task reviews approved; task 3's four reproduced findings were fixed and re-reviewed.
Integrated gates passed: 1,572 operations tests (158 known fork/thread warnings, 58.77 seconds),
805 tests through `mix precommit` (42.2 seconds), locked sync, compileall and shell syntax.
Precommit changed no files. Canonical [implementation evidence](../specs/2026-09-16-exact-helper-failure-design.md#implementation-evidence)
owns behavior/review/size proof. Continue at candidate 4's decision in the linked register and
handoff; no history rewrite, push, merge, deployment or real-host acceptance is authorized.

Final documentation verification: scoped links/anchors, whitespace, portable Markdown, full
ordered continuation and canonical global-rule ownership checks passed. Fresh-context review
approved the consolidated handoff and retirement mapping with all unfinished gates preserved.
