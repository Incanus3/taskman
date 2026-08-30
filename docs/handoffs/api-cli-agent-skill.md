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

The design, written specification, detailed TDD implementation plan, and Beads dependency graph are
approved. The workstream is at a clean implementation boundary. Slice 4 backfills
Project/List/Task parity; later slices must maintain UI/API/CLI/help/completion/skill parity. Bash
and Fish completions are generated from the shared command registry without backend lookups.
`taskman serve` is reserved as a future extension and excluded from this slice.

## Immediate next actions

1. Resume in a fresh session and read the complete approved specification and implementation plan.
2. Use the subagent-driven-development workflow unless the operator chooses another approach.
3. Begin with plan Task 1 / Beads issue `tas-yty.1`; Task 4 / `tas-yty.4` is also dependency-ready
   if the chosen execution workflow explicitly coordinates parallel work.

## Constraints

- Use a Mix-installed Elixir escript, not standalone native packaging.
- Keep the API local-only and unauthenticated under the current MVP boundary.
- The implementation must preserve the specification's exact endpoint paths, JSON fields, command
  registry, Bash/Fish completion contract, and numeric exit statuses.
- Preserve the plan's TDD checkpoints and obtain independent verification at the final delivery
  gate.

## Verification baseline

Planning commit `rwz` contains the detailed plan and Beads graph on
`api-cli-agent-skill-design`. The plan self-check found no unresolved placeholders or whitespace
errors, all local Markdown links resolved, and `br sync --status` reported the tracker in sync.
