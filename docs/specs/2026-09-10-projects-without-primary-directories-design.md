# Projects Without Primary Directories

## Status

Approved.

## Context

Taskman is hosted on a web-accessible machine while the work represented by a Project may be
performed on one or more different development machines. A filesystem path stored on the Project
therefore describes, at best, one checkout on one machine. The Taskman server cannot interpret or
validate that path, and another client may need a different path for the same Project.

The current implementation treats `Project.primary_directory` as required domain data. It
normalizes and validates the value against the server filesystem, persists it in a non-null
PostgreSQL column, displays it in the creation form and navigation, exposes it through the JSON API
and CLI, includes it in Project change notifications, and assumes it in fixtures, seeds, and tests.
Current product documents also use the field as the basis for a planned Auggie integration that has
not been implemented.

No external research is needed for this change. The observed deployment topology and the
machine-local meaning of filesystem paths are sufficient to show that the current ownership is
incorrect.

## Decision

A Project is a machine-independent logical work container. Its persisted attributes are its ID,
name, and timestamps; it does not own a filesystem directory.

Remove `primary_directory` from the Project schema, creation flow, database, UI, API, CLI,
notifications, seed data, fixtures, tests, and current product contract. Existing stored directory
values are discarded because they are not authoritative and cannot be translated into useful
host-independent data.

This design does not replace Auggie with Orca and does not decide how agents run, discover work, or
associate a local checkout with a task. Agent integration is deferred to a separate product design.
That later design may consider agents polling or claiming Taskman tasks from development machines,
but this document establishes no such contract.

## Goals

- Let users create and use Projects without supplying any filesystem path.
- Ensure the hosted server never validates a development-machine path.
- Make Project data and Project representations machine-independent.
- Remove the obsolete field consistently from browser, API, CLI, help, completions, and the bundled
  CLI skill.
- Make the current product documentation explicit that agent integration is deferred.
- Preserve historical specifications and research as provenance while clearly marking replaced
  guidance as superseded.

## Non-goals

- Designing an Orca integration or retaining the Auggie integration.
- Deciding whether agents are launched, poll for work, claim tasks, or use another coordination
  model.
- Introducing per-user, per-machine, per-client, per-session, or per-checkout directory records.
- Inferring a directory from the CLI working directory.
- Retaining an optional Project path as metadata.
- Providing compatibility shims for older in-repository API or CLI clients.

## Rejected alternatives

### Make the field optional

An optional path remains machine-specific and ambiguous. Consumers would still need to know which
machine it describes, while stale values would look authoritative. Optionality removes the creation
error but not the modeling error.

### Add Project checkout or machine records now

A separate checkout resource could model several machines correctly, but its identity, ownership,
lifecycle, security, and relationship to future agents depend on an agent workflow that is
deliberately undecided. Adding it now would create speculative architecture.

### Keep the database column as a compatibility field

There are no external code consumers that justify a permanent compatibility field. Keeping the
column would also invite future code to restore the invalid assumption. Historical values can be
recovered from a pre-migration backup if required.

## Domain and persistence changes

`Taskman.Projects.Project` retains only the `name` application field and timestamps. Its changeset
casts and requires `name`, trims it, and performs no filesystem work. `Taskman.Projects` deletes the
directory normalization and validation functions. A successful creation notification reports only
the `name` field.

Generate a new migration with `mix ecto.gen.migration drop_primary_directory_from_projects`, then
remove the `projects.primary_directory` column. Do not edit the historical Project-creation
migration: existing databases need an explicit transition and fresh databases should retain the
same migration history.

This is intentionally a forward data-model transition. A database containing Projects created by
the new release cannot be meaningfully downgraded to an old release that requires valid local
directories. Before deployment, take the normal verified backup. Rollback across this migration
means restoring the matching pre-migration database backup together with the prior application
release; it must not invent placeholder paths. The migration and runbook-facing verification must
make this limitation visible.

## Browser behavior

The New Project form contains only the name input and Create Project button. Submitting a blank name
continues to render the name validation error. A successful submission creates and selects the
Project exactly as today.

Project rows in the workspace navigation show the Project name and existing controls. Remove the
directory popover, its hook, accessibility description, and all path-specific conditional markup.
List navigation and all Task behavior remain unchanged. Copy that describes Taskman as a local
project workspace must be replaced with host-neutral wording.

## API contract

The versioned Project representation changes from:

```json
{"id": 7, "name": "Taskman", "primary_directory": "/work/taskman"}
```

to:

```json
{"id": 7, "name": "Taskman"}
```

`POST /api/v1/projects` accepts this documented request:

```json
{"project": {"name": "Taskman"}}
```

The success status remains `201 Created`, and list/show statuses and envelopes remain unchanged.
Missing or blank `name` returns the existing `422 validation_failed` response with only the `name`
field error. `primary_directory` is no longer a documented input or output field. As elsewhere in
the current controller boundary, extra input keys that reach the Project changeset are ignored;
this change does not add a new strict unknown-field policy.

This is an intentional breaking representation change. The application and its bundled CLI are
version-matched in this repository, and no compatibility shim is required.

## CLI and bundled skill contract

Project creation becomes:

```text
taskman projects create --name NAME
```

Remove `--directory PATH` from the registry, help, examples, parser expectations, completion
fixtures, and HTTP request body. Human-readable Project collections contain `ID` and `NAME` columns;
Project detail output contains `ID` and `NAME`. JSON output mirrors the new API representation.

The CLI client validates Project objects using `id` and `name` only. The bundled Taskman CLI skill
must treat the returned Project ID and name as authoritative and must not describe, infer, or
preserve Project directories.

## Documentation authority

Update current product documents so that:

- Project definitions and the core journey do not mention a primary or local directory;
- the current MVP does not promise a specific agent provider or direct agent launch workflow;
- Agent Session implementation is explicitly deferred to a separate accepted design; and
- `docs/product/agent-sessions.md` is clearly marked as superseded rather than remaining a resolved
  current workflow.

The older Projects first-slice and API/CLI design specifications remain for provenance. Add clear
status notes that this specification supersedes their Project-directory and Project-representation
requirements. Archived plans and research documents remain historical and are not rewritten.

Update `docs/README.md`, the root `README.md`, and any active roadmap language needed to prevent the
old assumptions from appearing current. Do not replace them with guesses about Orca or another
agent architecture.

## Expected file boundaries

Implementation is expected to touch these responsibility groups:

- Project persistence and behavior under `lib/taskman/projects*` and a newly generated migration;
- Project creation and navigation under `lib/taskman_web/live/` and
  `lib/taskman_web/components/workspace_navigation.ex`;
- Project API serialization under `lib/taskman_web/controllers/api/`;
- Project CLI registry, command, response validation, and presentation under `lib/taskman/cli/`;
- seed data and the bundled CLI skill under `priv/`;
- focused Project, LiveView, navigation, API, CLI, notification, seed, compatibility, and account
  tests plus shared Project fixtures; and
- current product, roadmap, specification-status, and documentation-index files described above.

Do not modify archived implementation plans or historical research solely to erase old terminology.

## Testing and verification

Use test-driven development for the behavior change. The focused checks must establish that:

- `Projects.create_project/1` succeeds with a trimmed non-empty name alone and fails only for an
  invalid name;
- Project creation publishes `fields: [:name]`;
- the LiveView form has no directory input and creates a Project from name-only parameters;
- workspace navigation contains no directory popover or path text;
- API list, show, and create return only `id` and `name`, and validation errors mention only `name`;
- CLI help, parsing, request construction, response validation, readable output, JSON output,
  completions, and bundled-skill assertions use the name-only contract;
- seeds, fixtures, account-deletion coverage, repository compatibility tests, and external-update
  tests create valid Projects without a path; and
- the forward migration succeeds against a database with existing Project rows and the resulting
  schema has no `primary_directory` column.

Run focused tests while implementing, then run `mix precommit`. Because this changes a documented
CLI surface, also inspect `taskman projects create --help`, generated Bash and Fish completion
coverage, local Markdown links and whitespace, and implementation-facing files for leaked planning
terminology. Deployment verification must confirm that a current backup exists before applying the
forward-only data-model transition.

## Known caveats

- Older CLI binaries reject the new Project response because they require `primary_directory`.
  Server and CLI releases must therefore be upgraded together.
- A prior application release cannot operate correctly on Projects created without paths. Restore
  the matching database backup when rolling back across this change.
- Future agent work will need a separate source for machine-local execution context if it needs one;
  this design intentionally does not choose that source.

## Next-session checklist

1. Write and review an implementation plan from this complete design.
2. Create repository-local Beads work items for the implementation and verification units.
3. Start implementation in a clean session after the plan is approved.
4. Generate, rather than hand-name, the migration.
5. Implement the vertical change across domain, UI, API, CLI, bundled skill, tests, and canonical
   documentation.
6. Verify the migration and rollback limitation explicitly, then run the full completion gates.
