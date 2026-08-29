# API, CLI, and Agent Skill Handoff

**Status:** active  
**Updated:** 2026-08-29  
**Resume:** `$resume api-cli-agent-skill`

## Objective

Deliver roadmap Slice 4: a versioned local JSON API, an accompanying `taskman` escript, complete
help and onboarding, Bash and Fish completions, and a CLI-bundled agent skill installable at the
standard agent-skills location.

## Durable references

- [Design specification awaiting written review](../specs/2026-08-29-api-cli-agent-skill-design.md)
- [MVP roadmap](../planning/roadmap.md)
- [MVP product specification](../product/mvp-spec.md)

There is no active Beads issue for this slice yet.

## Current checkpoint

The design was approved section by section. The written specification, product contract, roadmap
ordering, and documentation index have been updated. Slice 4 backfills Project/List/Task parity;
later slices must maintain UI/API/CLI/help/completion/skill parity. Bash and Fish completions are
generated from the shared command registry without backend lookups. `taskman serve` is reserved as
a future extension and excluded from this slice.

## Immediate next actions

1. Obtain operator review of the written specification.
2. After approval, use the writing-plans workflow to create the implementation plan and
   repository-local Beads delivery graph.
3. Update this handoff, then begin implementation in a fresh session with
   `$resume api-cli-agent-skill`.

## Constraints and pending decisions

- Use a Mix-installed Elixir escript, not standalone native packaging.
- Keep the API local-only and unauthenticated under the current MVP boundary.
- The implementation must preserve the specification's exact endpoint paths, JSON fields, command
  registry, Bash/Fish completion contract, and numeric exit statuses.
- No implementation is authorized until the written specification is reviewed.

## Verification baseline

The workspace was clean at upstream `5f1e3874bd8789b38435ca93c47b866db248c542` before documentation
edits. The latest onboarding installation-path, `PATH`, stale-wording, relative-link, and diff
checks passed.
