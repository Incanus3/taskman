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
- [Implementation plan awaiting review](../plans/2026-08-29-api-cli-agent-skill.md)
- [MVP roadmap](../planning/roadmap.md)
- [MVP product specification](../product/mvp-spec.md)

The active Beads feature is `tas-yty`, with child tasks `tas-yty.1` through `tas-yty.9`.

## Current checkpoint

The design and written specification are approved. A detailed TDD implementation plan and Beads
dependency graph are ready for operator review. Slice 4 backfills Project/List/Task parity; later
slices must maintain UI/API/CLI/help/completion/skill parity. Bash and Fish completions are
generated from the shared command registry without backend lookups. `taskman serve` is reserved as
a future extension and excluded from this slice.

## Immediate next actions

1. Obtain operator review and approval of the implementation plan.
2. Mark the plan approved and update this handoff with any accepted changes.
3. Begin implementation in a fresh session with `$resume api-cli-agent-skill`, defaulting to the
   subagent-driven-development workflow unless the operator chooses another approach.

## Constraints and pending decisions

- Use a Mix-installed Elixir escript, not standalone native packaging.
- Keep the API local-only and unauthenticated under the current MVP boundary.
- The implementation must preserve the specification's exact endpoint paths, JSON fields, command
  registry, Bash/Fish completion contract, and numeric exit statuses.
- No implementation is authorized until the implementation plan is approved.

## Verification baseline

The workspace was clean at upstream `5f1e3874bd8789b38435ca93c47b866db248c542` before documentation
edits. The latest onboarding installation-path, `PATH`, stale-wording, relative-link, and diff
checks passed.
