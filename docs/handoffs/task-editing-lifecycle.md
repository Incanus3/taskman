# Task editing lifecycle handoff

**Status:** active  
**Updated:** 2026-07-31  
**Resume:** `$resume task-editing-lifecycle`

## Objective

Implement the approved canonical Task modal so a person can edit persisted Task fields and control
every lifecycle transition without losing the selected Project list.

## Durable references

- [Approved design](../specs/2026-07-31-task-editing-lifecycle-design.md)
- [Implementation plan](../plans/2026-07-31-task-editing-lifecycle.md)
- Active implementation task: `tas-task-editing-lifecycle-nr1.2`
- Parent delivery task: `tas-task-editing-lifecycle-nr1`

## Checkpoint

The design and implementation plan are approved and self-reviewed. Their baseline commits are on
the local `task-editing-lifecycle-design` branch through revision `f4a4424`. Design task
`tas-task-editing-lifecycle-nr1.1` is closed and `.2` is ready. No implementation task has started.

The superseded remote design branch contained no implementation code or unique accepted decision
and has been deleted. Do not recover or apply its `/edit`-route and explicit-save design.

## Next actions

1. Resume in a clean session and read the complete approved design before the plan.
2. Use subagent-driven execution and review checkpoints for the plan's five tasks.
3. Start with the non-null description invariant, then complete
   `tas-task-editing-lifecycle-nr1.2` as specified by the plan.
4. Preserve unrelated workspace changes and do not push, merge, or publish without explicit
   authorization.

## Blockers and constraints

There is no current blocker or pending product decision. The implementation must retain the
canonical `/projects/:project_id/tasks/:task_id` route, server-managed autosave, empty-string
description semantics, and the exclusions recorded in the approved design.
