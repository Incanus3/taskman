# Taskman — Lightweight MVP Roadmap

**Status:** Projects and basic Tasks complete
**Updated:** 2026-07-31

This roadmap is intentionally high-level. It describes the order of useful vertical slices without
turning the whole MVP into a detailed implementation backlog. Each slice should be refined only when
it is next or close to next.

## Delivery approach

The durable project-wide working and architecture rules live in the [development guide](../development.md).
This roadmap applies them to the MVP delivery sequence:

- Work top-down through user-visible vertical slices.
- Keep the first implementation narrow and learn from the running application.
- Refine a slice only when it is next or close to next; do not create detailed tickets for the entire
  roadmap in advance.

## Slices

### 0. Application foundation

**Outcome:** A locally started Phoenix application can run against PostgreSQL.

Scope:

- Generate the Phoenix LiveView application skeleton locally.
- Configure the development database.
- Establish the normal test, formatting, and local-run workflow.
- Add only enough home/health surface to prove the application boots.

This is a development foundation, not a product milestone. No deployment or container-orchestration
work is implied.

**Current state:** Complete. The Phoenix LiveView skeleton runs against PostgreSQL using the
standard `run_postgres.sh` startup path. The normal `mix precommit` and `mix setup` workflows have
passed, and the application has been smoke-tested successfully at `http://localhost:4000`.

### 1. Projects and basic Tasks

**Outcome:** A user can create a Project, create and manage Tasks in it, and view them in the
default list-first screen.

Initial scope:

- Project name and required primary local directory.
- Project persistence and selection.
- Task persistence under a Project.
- Task title, description, priority, status, and optional due date-time.
- Create and edit Tasks.
- Human-controlled basic lifecycle changes.
- Focused context and LiveView tests.

This is the first real MVP milestone and the first slice to guide later architectural decisions.

**Current state:** Complete. Users can create and select Projects, create Tasks from the selected
Project's direct list, and open each Task at its canonical
`/projects/:project_id/tasks/:task_id` URL in a modal over the preserved list. The modal autosaves
title, description, status, priority, and optional due date-time changes; every lifecycle state is
human-selectable, and persisted title, status, and priority values refresh in the Task row. Focused
tests, the complete repository gate, and responsive browser acceptance have passed.

### 2. Task detail and navigation

**Outcome:** The core work screen supports inspecting and editing a Task without losing the current
list context.

Scope:

- Project/list navigation shell.
- Task detail modal or dedicated detail route.
- Shareable Task selection in the URL, decided from the actual LiveView interaction.
- Initial hierarchy and Activity/Sessions empty states.
- Basic accessibility and interaction behavior.

The URL, modal-state, and browser/server boundary decisions should be made here, not in advance.

### 3. Lists and nested organization

**Outcome:** Projects can organize Tasks in nested Lists.

Scope:

- Create, rename, and delete Lists.
- Nested List tree.
- Tasks directly under a Project or one List.
- Move Tasks between valid locations in the same Project.
- Optional inclusion of descendant List Tasks while preserving their source List.

The tree representation and query strategy should be designed immediately before this slice.

### 4. Task relationships

**Outcome:** Task work breakdown and explicit relationships are usable in Task detail.

Add relationship types incrementally:

1. Parent-child hierarchy.
2. Relates to.
3. Blocks / Blocked by.
4. Warning confirmation when completing a Task with unresolved blockers.

Each type should add its persistence, domain validation, UI, and focused invariant tests. Do not
build a generalized relationship subsystem beyond the concrete rules that the next relationship
requires.

### 5. Deletion safeguards

**Outcome:** Destructive operations follow the product's explicit impact-warning and confirmation
rules.

Scope:

- Project deletion impact preview and confirmation.
- Recursive List deletion.
- Task deletion.
- Task-with-children choice between subtree deletion and direct-child reparenting.
- Cleanup of relationships and any existing Agent Sessions.
- Tests for cross-Project relationship effects.

Implement this after ownership and relationship behavior exists so impact calculations reflect the
real model.

### 6. Agent Session launch foundation

**Outcome:** A user can launch an Auggie session for a Task through a narrow, testable integration
boundary.

Scope:

- Decide the minimum provider-adapter boundary required by launch.
- Add a deterministic fake provider for tests and local UI development.
- Persist Agent Sessions only after a provider session ID is obtained.
- Add Sessions rail states and launch flow.
- Enforce the Project primary-directory rule.
- Persist launch context and the opaque provider session ID.

ACP process ownership, capability negotiation, and recovery details should be designed immediately
before this slice.

### 7. Attach and resume

**Outcome:** Existing Auggie sessions can be attached and explicitly resumed with clear recovery
behavior.

Scope:

- Attach an existing session after adapter validation in the Project directory.
- Capability-reported model handling.
- Explicit Resume flow.
- Recovery status and unsupported/failed-operation reporting.

Keep this separate from initial launch because attachment and recovery are materially more complex.

### 8. MVP hardening

**Outcome:** The local MVP is coherent, testable, and safe to use.

Scope:

- Accessibility pass.
- Empty, loading, and failure states.
- Database constraint and transaction review.
- Error-path review.
- Formatting and test cleanup.
- Manual end-to-end smoke test.
- Local setup documentation.

## Explicitly deferred until needed

The following are not roadmap blockers and should be decided only when a concrete slice requires
them:

- Full database schema design beyond the current slice.
- Tree-storage strategy before nested Lists or parent-child relationships.
- Generic repository or event/activity abstractions.
- Full filesystem/service architecture before local-directory behavior is implemented.
- ACP supervision and recovery architecture before Agent Session work.
- Complete URL and modal-state design before Task detail/navigation.
- A formal JSON API without a client that needs it.
- Broad JavaScript conventions without a browser behavior that requires a hook.
- Compose/deployment packaging, authentication, and multi-user concerns excluded by the MVP.
