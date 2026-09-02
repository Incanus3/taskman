# Taskman — Lightweight MVP Roadmap

**Status:** Authenticated hosted access next; Task relationships resume afterward
**Updated:** 2026-09-02

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

**Current state:** Complete. The list-first Project workspace keeps the selected Project's direct
Task list behind a canonical URL-backed Task-detail modal. The modal supports complete autosaved
editing, explicit human-controlled lifecycle changes, recoverable invalid URLs, a collapsible
parent-child hierarchy shell, and separate truthful Activity and Sessions empty states. Hierarchy
preference lasts for the current workspace LiveView, wide layouts push the detail, and narrower
layouts use an internal overlay while Activity and Sessions reflow below the form. Focused tests,
the complete repository gate, independent implementation review, and responsive browser acceptance
have passed.

### 3. Lists and nested organization

**Outcome:** Projects can organize Tasks in nested Lists.

Scope:

- Create and rename Lists.
- Nested List tree.
- Tasks directly under a Project or one List.
- Move Tasks between valid locations in the same Project.
- Optional inclusion of descendant List Tasks while preserving their source List.

The tree representation and query strategy should be designed immediately before this slice.

List deletion is deferred to slice 6 so it ships with the complete recursive impact-warning and
confirmation contract rather than an unsafe or temporary deletion rule.

**Current state:** Complete. Users can create and rename nested Lists, expand a semantic hierarchy,
and create Tasks directly under a Project or List. Project and List routes support direct or
descendant Task views with full Location paths, and same-Project Task movement is available from
rows and detail while preserving URL and autosave behavior. Focused tests, the final
`ERL_FLAGS='+S 4' mix precommit` gate (124 passed), wide and measured 390×844 responsive browser
acceptance, and independent implementation review have passed. The written
[`Lists and nested organization specification`](../specs/2026-08-26-lists-nested-organization-design.md)
is approved, implemented, and delivered. Its
[`implementation plan`](../archive/plans/2026-08-26-lists-nested-organization.md) is archived, and the
repository-local delivery graph is closed through `tas-lists-nested-organization-8qe.6`.

### 4. API, CLI, and agent skill

**Outcome:** Users and agents can manage Taskman through a documented CLI backed by a versioned
JSON API.

Scope:

- Add `/api/v1` endpoints for meaningful Project, List, and Task operations delivered in Slices
  1–3.
- Build an installable Elixir escript named `taskman` that calls the API with Req.
- Provide human-readable output by default and deterministic JSON through a global `--json` option.
- Provide complete top-level, command-group, and leaf-command help.
- Generate Bash and Fish completions from the same command registry used by help.
- Add `taskman agent onboarding` with purpose, installation, backend-start, configuration, and
  basic-usage guidance.
- Bundle a version-matched agent skill and install it safely to
  `~/.agents/skills/taskman-cli/` through `taskman agent skill install`.
- Establish UI, API, CLI, help, completion, and skill parity as a completion requirement for
  meaningful operations in every later slice.

Browser-only presentation state is not part of parity. This slice originally delivered a local-only,
unauthenticated API and CLI; the authenticated hosted-access insertion below supersedes that
boundary. Starting the backend through a future `taskman serve` command is an explicit extension
point, not part of this slice.

**Current state:** Complete. The versioned loopback JSON API, Req-backed `taskman` escript,
readable and JSON output, command help, Bash and Fish completions, onboarding, and version-matched
skill installer are delivered. Real loopback HTTP coverage, focused API/CLI and installer suites,
the complete repository gate, implementation-surface scans, independent verification, and final
whole-branch review have passed.

The
[`API, CLI, and agent skill specification`](../specs/2026-08-29-api-cli-agent-skill-design.md)
defines the operation surface and maintenance contract.

**Next slice:** Authenticated hosted access, then resume Slice 5 Task relationships.

### Priority insertion: authenticated hosted access

**Outcome:** Explicitly provisioned users can access one shared Taskman workspace through arbitrary
browsers and authenticated API clients on a web-accessible server.

Scope:

- Add an isolated Ash Accounts domain with AshAuthentication password sessions and expiring API
  keys.
- Bootstrap the first administrator locally and manage later invitations through a constrained
  AshAdmin surface.
- Add confirmed email changes, password recovery, session/API-key management, immediate account
  disablement, and permanent self-service/administrator account deletion.
- Add administrator email correction and confirmation, including fresh setup invitations after
  changing a pending user's address.
- Add protected XDG configuration for the `taskman` CLI.
- Deliver Resend transactional mail through Swoosh and Req.
- Package Taskman as an OTP release with migration/bootstrap commands and documented systemd/Caddy
  operation.
- Preserve all existing Project, List, Task, API, CLI, and notification behavior behind an
  application-wide access gate.

This increment does not add domain ownership or migrate existing domain resources to Ash. A later
workstream will design a complete gradual Ash migration; ordinary feature work must not create a
permanent Ecto/Ash hybrid.

**Current state:** Specification and implementation plan approved; delivery starts with
`tas-authenticated-hosted-access-2a8.1`. See the
[`authenticated hosted access specification`](../specs/2026-09-02-authenticated-hosted-access-design.md)
and [`implementation plan`](../plans/2026-09-02-authenticated-hosted-access.md).

### 5. Task relationships

**Outcome:** Task work breakdown and explicit relationships are usable in Task detail.

Add relationship types incrementally:

1. Parent-child hierarchy.
2. Relates to.
3. Blocks / Blocked by.
4. Warning confirmation when completing a Task with unresolved blockers.

Each type should add its persistence, domain validation, UI, and focused invariant tests. Do not
build a generalized relationship subsystem beyond the concrete rules that the next relationship
requires.

**Delivered increment:** Parent-child Task hierarchy is defined by the
[`Parent-child Task hierarchy specification`](../specs/2026-08-30-parent-child-task-hierarchy-design.md).
Users can create and edit same-Project parentage, add subtasks with inherited Task location,
navigate a progressive hierarchy in Task detail, and use matching JSON API and CLI contracts while
List ownership remains independent. Focused and complete repository gates, implementation-surface
scans, responsive browser acceptance, independent verification, and final whole-branch review
passed.

**Next increment after authenticated hosted access:** Relates to.

### 6. Deletion safeguards

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

### 7. Agent Session launch foundation

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

### 8. Attach and resume

**Outcome:** Existing Auggie sessions can be attached and explicitly resumed with clear recovery
behavior.

Scope:

- Attach an existing session after adapter validation in the Project directory.
- Capability-reported model handling.
- Explicit Resume flow.
- Recovery status and unsupported/failed-operation reporting.

Keep this separate from initial launch because attachment and recovery are materially more complex.

### 9. MVP hardening

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
- Broad JavaScript conventions without a browser behavior that requires a hook.
- Synchronization, collaboration, managed hosting, and per-user domain ownership beyond the
  accepted authenticated hosted-access boundary.
- Native standalone CLI packaging before distribution beyond the Elixir/Mix development
  environment requires it.
- Starting the backend through `taskman serve` before a dedicated server-lifecycle slice defines
  its process, database, and failure semantics.
