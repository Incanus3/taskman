# PR 28 review follow-ups

Status: active. Updated: 2026-10-27. Resume: `$resume pr28-review-followups`.

## Objective and baseline

Address the five [post-merge bot findings](https://github.com/Incanus3/taskman/pull/28#pullrequestreview-5657459967)
against merged baseline `6a22a0e054ae7481e5ea1642260d9220ffd1efcc` on the local
`pr28-review-followups` branch. The operator authorized these bounded corrections in this session.
The unrelated operations branch is preserved unapplied. No existing terminal Task was reopened.

Canonical contracts: [task counts](../specs/2026-10-26-list-task-counts-design.md),
[onboarding](../specs/2026-08-29-api-cli-agent-skill-design.md#onboarding-command),
[browser verification](../guides/development.md#verification-expectations), and
[Alpine CI](../specs/2026-08-10-alpine-elixir-ci-design.md#acceptance-boundary).

## Current implemented state and verification

All five corrections are implemented: portable Mix installation commands, supported-atom count
normalization, table-only sort refresh, display/Xvfb guidance, and specification cleanup retaining
verification criteria and limits. No dependency or lockfile changes were needed.

- Regression tests failed for the expected reasons before fixes, then passed: 30 focused tests.
- Broader ProjectLive/count/onboarding coverage: 418 passed, 18 browser tests excluded.
- CLI suite: 290 passed, three rebuilt-executable checks skipped in the source-only run.
- `mix precommit`: 1,306 passed, three skipped, 34 browser tests excluded.
- `mix escript.build` succeeded; the rebuilt executable gate ran all three checks successfully.
  An isolated offline invocation also emitted the expected portable onboarding commands.
- Headed count-badge browser suite: two passed. The first run served stale ignored `.gz` assets;
  a direct endpoint request confirmed the mismatch. Regenerating them with
  `MIX_ENV=test mix phx.digest` resolved both failures. The development guide records the cause
  and rebuild command; no application change was needed for this environment issue.
- Independent scoped specification-compliance and code-quality review passed with no material
  findings, including a scoped check of the compressed-asset troubleshooting guidance.

## Remaining sequence and current position

Verification and independent review are complete; corrections are ready for operator review.

1. Operator reviews the corrections and the implementation rulings below. Revision requests stay
   local; acceptance does not itself authorize publication.
2. After explicit completion and ruling acknowledgement, retire this handoff.
3. Publication and merge require separate operator authorization; neither is authorized now.

## Rulings awaiting operator acknowledgement

- Ignore unsupported/non-atom count statuses before deriving either count set, following existing
  location-query normalization and keeping the map return type. A new error tuple would complicate
  an internal caller already receiving normalized input. Cost: programmer mistakes in status
  values are ignored rather than explicitly rejected.
- Emit portable quoted shell expressions for the effective Mix escript directory rather than
  embedding the generating machine's absolute path. Cost: copied commands resolve the executing
  shell's environment, which must match the intended installation.
- Preserve headed hover tests and document desktop/virtual-display prerequisites. Cost: headless
  machines must provide Xvfb and xauth; no automatic display management is added to the test runner.

## Verification limits

The current environment has Chromium, ChromeDriver and a desktop display, but no Xvfb wrapper.
Desktop verification established headed hover behavior; the documented Xvfb command still needs a
machine with those prerequisites for execution. No physical-device acceptance, hosted CI run,
release runtime acceptance, deployment or GitHub thread reply is part of these corrections.
