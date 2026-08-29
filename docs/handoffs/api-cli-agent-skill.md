# API, CLI, and Agent Skill Handoff

**Status:** active  
**Updated:** 2026-08-29  
**Resume:** `$resume api-cli-agent-skill`

## Objective

Deliver roadmap Slice 4: a versioned local JSON API, an accompanying `taskman` escript, complete
help and onboarding, Bash and Fish completions, and a CLI-bundled agent skill installable at the
standard agent-skills location.

## Durable references

- [Approved design specification](../specs/2026-08-29-api-cli-agent-skill-design.md)
- [Approved implementation plan](../plans/2026-08-29-api-cli-agent-skill.md)
- [MVP roadmap](../planning/roadmap.md)
- [MVP product specification](../product/mvp-spec.md)

The active Beads feature is `tas-yty`, with child tasks `tas-yty.1` through `tas-yty.9`.

## Current checkpoint

Implementation has started from the approved design and detailed TDD plan. The
`api-cli-agent-skill` branch is stacked above `api-cli-agent-skill-design`; feature `tas-yty` and
Tasks 1–7 are implemented and task-reviewed. API and CLI parity for Projects, Lists, and Tasks is
complete, and Bash/Fish completions plus offline onboarding are registry-driven and reviewed.
Completion constraints now behave consistently across both shells, including mixed descendant
groups and option values that match command names. The latest CLI and `mix precommit` gates pass.
The SDD ledger remains at
`.superpowers/sdd/2026-08-29-api-cli-agent-skill/progress.md`.

## Immediate next actions

1. Continue plan Task 8 / Beads issue `tas-yty.8` using the SDD task/review loop.
2. Bundle the concise `taskman-cli` agent skill and implement the staged, rollback-safe installer.
3. Keep installer tests under an injected temporary skills root; never touch the real
   `~/.agents/skills` directory.

## Constraints

- Use a Mix-installed Elixir escript, not standalone native packaging.
- Keep the API local-only and unauthenticated under the current MVP boundary.
- The implementation must preserve the specification's exact endpoint paths, JSON fields, command
  registry, Bash/Fish completion contract, and numeric exit statuses.
- Preserve the plan's TDD checkpoints and obtain independent verification at the final delivery
  gate.

## Verification baseline

Latest Task 7 evidence: focused completion tests passed (11 tests), the full suite passed
(283 tests), and `mix precommit` passed. Generated Bash/Fish scripts parse successfully, and actual
shell completion queries verify registry constraints, forbidden globals, and option-value
collisions. The final scoped Task 7 review approved with no findings.
