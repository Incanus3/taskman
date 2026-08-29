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
Tasks 1–3 are implemented and task-reviewed. Commits `mpn`, `npk`, and `ymn` deliver complete
Project/List/Task JSON API parity; `mkp` enforces the reviewed loopback-only production default.
All API controller tests and the latest `mix precommit` gate pass. The SDD ledger remains at
`.superpowers/sdd/2026-08-29-api-cli-agent-skill/progress.md`.

## Immediate next actions

1. Continue plan Task 4 / Beads issue `tas-yty.4` using the SDD task/review loop.
2. Establish the complete declarative command registry, parser/help boundary, and offline escript
   without starting Phoenix or Repo services.
3. Preserve every exact command path, option, constraint, lifecycle token, and global precedence.

## Constraints

- Use a Mix-installed Elixir escript, not standalone native packaging.
- Keep the API local-only and unauthenticated under the current MVP boundary.
- The implementation must preserve the specification's exact endpoint paths, JSON fields, command
  registry, Bash/Fish completion contract, and numeric exit statuses.
- Preserve the plan's TDD checkpoints and obtain independent verification at the final delivery
  gate.

## Verification baseline

Latest Task 3 evidence: Task API tests passed (20 tests), Task/domain/List tests passed (53 tests),
all API controller tests passed (34 tests), and `mix precommit` passed (215 tests). Task review
approved the implementation with only two deferred test-hardening suggestions.
