# API, CLI, and Agent Skill Design

**Status:** Approved
**Date:** 2026-08-29

## Context

Taskman is a locally started, single-user Phoenix application whose delivered UI supports Projects,
nested Lists, and Tasks. The browser currently calls the public `Taskman.Projects`,
`Taskman.Lists`, and `Taskman.Tasks` contexts through LiveView, and the router contains an unused
JSON API pipeline.

The original roadmap deferred a formal JSON API until a client needed it. An accompanying CLI is
now that client. Its primary motivation is to let agents manage the user's Taskman data reliably,
while remaining useful as a human-operated terminal interface.

This design inserts a new delivery slice before Task relationships. It backfills API and CLI parity
for the domain operations delivered in Slices 1–3 and establishes parity as a standing completion
requirement for later slices.

The implementation baseline is upstream commit `5f1e3874bd8789b38435ca93c47b866db248c542`.
At design time the workspace is clean and there is no active Beads issue for this slice.

## Outcome

A user or agent can use an installed `taskman` escript to perform the meaningful Project, List, and
Task operations available in the browser. The CLI talks only to the running Taskman JSON API,
provides both readable and structured output, explains itself thoroughly, and can install its own
version-matched agent skill in the standard agent-skills location.

Success means:

- every supported CLI operation is backed by a versioned JSON endpoint;
- the API and LiveView reuse the same public domain contexts and invariants;
- humans receive concise readable output and agents can request deterministic JSON;
- top-level, group, and leaf-command help are complete and discoverable;
- Bash and Fish completion scripts cover every registered command, option, and fixed value;
- agent onboarding explains the product, installation, configuration, and basic workflows;
- the bundled skill installs and updates safely at `~/.agents/skills/taskman-cli/`;
- later product slices treat UI, API, CLI, help, completion, and skill parity as one delivery
  contract; and
- focused API, CLI, installer, and end-to-end verification pass.

## Scope

### Included

- A versioned `/api/v1` JSON boundary.
- An Elixir escript named `taskman`.
- Project listing, inspection, and creation.
- List listing/tree inspection, inspection, creation, and rename.
- Task listing, inspection, creation, editable-field and lifecycle updates, and same-Project
  movement.
- Direct and descendant-aware Task queries that preserve owning-location information.
- Human-readable output and a global structured JSON mode.
- Complete top-level, group, and leaf-command help.
- Bash and Fish shell completions generated from the CLI command registry.
- Agent onboarding output.
- A bundled, version-matched `taskman-cli` agent skill and safe installation command.
- A durable parity rule for later product operations.

### Excluded

- Project, List, and Task deletion, which remains in the deletion-safeguards slice.
- Task relationships, which remain in the next domain slice.
- Agent Session launch, attachment, and resume.
- Browser-only presentation state such as opening a modal, expanding navigation nodes, or searching
  within a move popover.
- Authentication, remote access, hosting, synchronization, and multi-user authorization.
- OpenAPI generation, generated clients, or a general client SDK.
- Name-based resource resolution whose result could be ambiguous.
- Starting the Taskman backend from the CLI.
- Standalone native executable packaging.

## Architecture

### Equal clients of the domain layer

The LiveView and JSON controllers are separate adapters over the same public context APIs. JSON
controllers remain thin: they parse transport input, call `Taskman.Projects`, `Taskman.Lists`, or
`Taskman.Tasks`, and translate context results into the shared API representation. They do not call
`Taskman.Repo`, construct Ecto queries, or duplicate domain validation.

The escript is an HTTP client. It uses Req against a running Taskman server and never opens the
database or embeds server-side business rules. CLI validation is limited to command syntax and
locally knowable argument shape; the server remains authoritative for resource identity,
ownership, lifecycle, and persistence validation.

The Mix project configures `escript: [main_module: Taskman.CLI, app: nil]`. Ordinary CLI startup
therefore does not start the Phoenix endpoint, Repo, or the complete Taskman supervision tree.
Commands start only the runtime applications needed by their own work.

### Versioned API

All endpoints live below `/api/v1`. The version covers resource representations, error shapes, and
operation semantics, not just route paths. Backward-incompatible changes require a new API version
or an explicit migration decision.

The API is unauthenticated because the current product is local-only and single-user. This does not
authorize remote exposure: the default server and CLI configuration remain loopback-oriented.
Authentication and remote operation require a later product decision.

### Future server command seam

`taskman serve` is reserved as the preferred future command for starting the backend. It is not
implemented by this slice. The escript entry point keeps command dispatch, HTTP-client startup, and
application-service startup separate so a future command can start Phoenix and PostgreSQL-facing
services without coupling server startup to ordinary client commands.

Until that feature exists, onboarding explains how to start the Phoenix application with the
repository's current workflow.

## Supported operation contract

The initial parity surface is deliberately limited to meaningful persisted or query operations
already delivered in the UI.

| Resource | Operations |
| --- | --- |
| Project | List, inspect, create. |
| List | List or inspect the Project tree, inspect one List, create at the Project root or below a parent List, rename. |
| Task | List by Project or List, optionally include descendants, inspect, create, update editable fields and lifecycle state, move within its Project. |

Project selection, List selection, and Task-detail navigation correspond to inspection and listing;
they do not need separate state-changing API operations. Transient LiveView interaction state is
not part of parity.

Resource IDs are the stable CLI operands in the first release. Display output includes names and
location paths, but the CLI does not silently choose among same-named resources.

## API contract

Controller and route-module names are internal implementation details. The following HTTP methods,
paths, request bodies, response envelopes, and statuses are the external contract.

### Resources and routes

| Method and path | Operation | Request |
| --- | --- | --- |
| `GET /api/v1/projects` | List Projects. | None. |
| `POST /api/v1/projects` | Create a Project. | `{"project":{"name":"…","primary_directory":"…"}}` |
| `GET /api/v1/projects/:project_id` | Inspect a Project. | None. |
| `GET /api/v1/projects/:project_id/lists` | List the Project's Lists in stable tree order. | None. |
| `POST /api/v1/projects/:project_id/lists` | Create a root or child List. | `{"list":{"name":"…","parent_list_id":null}}`; a positive parent ID creates a child. |
| `GET /api/v1/projects/:project_id/lists/:list_id` | Inspect a List. | None. |
| `PATCH /api/v1/projects/:project_id/lists/:list_id` | Rename a List. | `{"list":{"name":"…"}}` |
| `GET /api/v1/projects/:project_id/tasks` | List Tasks for a location. | Query described below. |
| `POST /api/v1/projects/:project_id/tasks` | Create a Task in a location. | `{"task":{…,"list_id":null}}`; a positive List ID selects that List. |
| `GET /api/v1/projects/:project_id/tasks/:task_id` | Inspect a Task. | None. |
| `PATCH /api/v1/projects/:project_id/tasks/:task_id` | Update a Task's editable fields or lifecycle state. | `{"task":{…}}` with one or more editable fields. |
| `POST /api/v1/projects/:project_id/tasks/:task_id/move` | Move a Task. | `{"destination":{"list_id":null}}`; a positive List ID selects that List. |

Task listing has two optional query parameters:

- absent `list_id` selects direct Project Tasks; a positive integer selects that List;
- `include_descendants=true` includes descendant List Tasks; missing or any other value is false.

Task creation accepts `title`, `description`, `status`, `priority`, `due_at`, and `list_id`. Task
update accepts `title`, `description`, `status`, `priority`, and `due_at`; JSON `null` clears
`due_at`. API lifecycle values for both creation and update are `icebox`, `pending`, `in_progress`,
`in_review`, `done`, and `will_not_do`. Priority values are `none`, `low`, `medium`, `high`, and
`urgent`. `due_at` is either `null` or a local ISO 8601 date-time without a timezone offset,
matching the MVP's local-time contract.

Task movement is an explicit member operation rather than an incidental field update because it
has same-Project destination invariants and distinct unchanged/not-found outcomes.

All nested routes require and revalidate the Project even where database IDs are globally unique.
Malformed, stale, and cross-Project IDs never escape scope or mutate persistence.

### Representations

Every successful response is `{"data": value}`. A collection uses a JSON array for `value`; a
member or mutation uses one object. Create responses use `201 Created`; every other successful
operation uses `200 OK`.

Project objects contain `id`, `name`, and `primary_directory`. List objects contain `id`,
`project_id`, `parent_list_id`, `name`, and `path`, where `path` is the ordered array of List names
from the root through that List. Task objects contain `id`, `project_id`, `list_id`, `title`,
`description`, `status`, `priority`, `due_at`, and `location`. `location` is
`{"kind":"project","list_id":null,"path":[]}` for a direct Project Task or
`{"kind":"list","list_id":ID,"path":[…]}` for a List-owned Task.

Errors use one shared JSON shape containing:

- `{"error":{"code":"CODE","message":"…"}}`; and
- an optional `fields` object beneath `error`, mapping input field names to arrays of messages.

The public error/status vocabulary is:

| Status | Code | Meaning |
| --- | --- | --- |
| `400 Bad Request` | `invalid_request` | Malformed JSON, invalid ID syntax, or an unsupported query/field value that cannot reach domain validation. |
| `404 Not Found` | `not_found` | A resource is missing, stale, or outside the required Project scope. |
| `409 Conflict` | `unchanged_location` | A Task move selected its current location. |
| `422 Unprocessable Entity` | `validation_failed` | A changeset or domain validation rejected otherwise well-formed input. |
| `500 Internal Server Error` | `internal_error` | An unexpected server failure. |

An unsupported route retains Phoenix's ordinary API 404 behavior. Internal exceptions, changeset
internals, and stack traces are never returned as the public contract.

## CLI contract

### Command organization

The CLI uses discoverable noun groups plus focused utility commands:

```text
taskman projects <command>
taskman lists <command>
taskman tasks <command>
taskman agent <command>
```

The exact initial command registry is:

```text
taskman projects list
taskman projects show PROJECT_ID
taskman projects create --name NAME --directory PATH

taskman lists list --project PROJECT_ID
taskman lists show --project PROJECT_ID LIST_ID
taskman lists create --project PROJECT_ID --name NAME [--parent LIST_ID]
taskman lists rename --project PROJECT_ID LIST_ID --name NAME

taskman tasks list --project PROJECT_ID [--list LIST_ID] [--include-descendants]
taskman tasks show --project PROJECT_ID TASK_ID
taskman tasks create --project PROJECT_ID --title TITLE [--list LIST_ID]
  [--description TEXT]
  [--status icebox|pending|in_progress|in_review|done|will_not_do]
  [--priority none|low|medium|high|urgent] [--due-at LOCAL_ISO_8601]
taskman tasks update --project PROJECT_ID TASK_ID
  [--title TITLE] [--description TEXT]
  [--status icebox|pending|in_progress|in_review|done|will_not_do]
  [--priority none|low|medium|high|urgent]
  [--due-at LOCAL_ISO_8601 | --clear-due-at]
taskman tasks move --project PROJECT_ID TASK_ID
  (--to-list LIST_ID | --to-project-root)

taskman completions bash
taskman completions fish

taskman agent onboarding
taskman agent skill install [--force]
```

`tasks update` requires at least one editable-field option. The two move destinations and the two
due-date options are mutually exclusive. CLI lifecycle tokens use the API's underscore values
directly: `in_progress`, `in_review`, and `will_not_do`. Option names remain hyphenated, such as
`--due-at`, so option-word separators remain visually distinct from separators inside enum values.

### Global configuration

The global surface includes:

- `--api-url URL`, with `TASKMAN_API_URL` as its environment equivalent;
- `--json`;
- `--help`; and
- `--version`.

Global options may appear before or after the command path. The default API URL is
`http://localhost:4000`. An explicit command-line value takes precedence over the environment,
which takes precedence over the default.

### Output and exit behavior

Human-readable output is the default. Data-producing commands accept the global `--json` mode and
write exactly one documented JSON value to stdout. Diagnostics and human-oriented error messages
go to stderr so agents can parse successful stdout without filtering.

Exit statuses are:

| Status | Meaning |
| --- | --- |
| `0` | Success, including `--help`, `--version`, and an already-current skill installation. |
| `2` | Invalid invocation, locally invalid option value, or missing required argument. |
| `3` | Server-reported `invalid_request`, `not_found`, `unchanged_location`, or `validation_failed`. |
| `4` | Connection refusal, DNS failure, or timeout. |
| `5` | Server `internal_error`, another 5xx response, malformed JSON, or a response that violates the expected API contract. |
| `6` | Skill installation filesystem or ownership failure. |

Once released, these values are part of the CLI compatibility contract.

In `--json` mode, successful stdout preserves the API `{"data":…}` envelope. A failed server
operation writes the API error envelope to stderr. Locally detected and connection/contract errors
use the same error shape and stable codes `invalid_invocation`, `connection_failed`,
`invalid_response`, or `skill_install_failed`. Readable mode may use tables for collections and
labelled fields for members, but it must preserve every field necessary to identify the returned
resource. Onboarding uses `{"data":{"onboarding":"…"}}` in JSON mode. Skill installation uses a
data object containing `action`, `path`, `skill`, and `cli_version`; `action` is `installed`,
`updated`, or `current`.

### Help

`taskman --help` identifies Taskman, lists every command group and utility command, and documents all
global options and configuration precedence. Each group lists its subcommands. Each leaf command
documents its purpose, required operands, options, output behavior, and representative examples.

Unknown commands and missing required arguments produce concise diagnostics, the relevant usage
line, and the invalid-invocation exit status. A user or agent must not need repository source code
to discover supported commands.

### Shell completions

`taskman completions bash` and `taskman completions fish` print the corresponding completion script
to stdout and do not require a running Taskman backend. The output is shell source rather than
Taskman data, so combining either command with `--json` is an invalid invocation with exit status
`2`.

Both scripts are generated from the same declarative command registry used to render help. They
complete:

- top-level groups and every nested command path;
- global and command-specific long options;
- lifecycle, priority, and other fixed option values; and
- only mutually valid options where the shell's completion facilities make that practical.

The initial scripts do not call the backend and do not complete Project, List, or Task IDs or
names. This keeps tab completion fast, deterministic, and usable when the server is stopped.
Resource-aware completion requires a later explicit design.

Installation remains user-controlled. Completion help and onboarding contain these exact default
installation examples:

```sh
mkdir -p ~/.local/share/bash-completion/completions
taskman completions bash > ~/.local/share/bash-completion/completions/taskman
```

```fish
mkdir -p ~/.config/fish/completions
taskman completions fish > ~/.config/fish/completions/taskman.fish
```

The CLI does not write shell configuration or completion files itself. Users may redirect the
generated scripts elsewhere when their shell setup uses different completion paths.

## Agent onboarding and skill

### Onboarding command

`taskman agent onboarding` produces self-contained plain text suitable for a person or agent. It
explains:

- what Taskman is, which data it manages, and which operations are available through this CLI
  version;
- that ordinary commands require a running backend;
- how to install or update the escript with
  `mix do escript.build + escript.install --force`;
- that Mix installs it at `~/.mix/escripts/taskman`;
- that `~/.mix/escripts` must be on `PATH` to invoke it as `taskman`, while
  `~/.mix/escripts/taskman` remains directly invokable without that `PATH` entry;
- how to start the backend with the current repository workflow;
- how API URL configuration works;
- representative Project, List, and Task commands;
- how to install Bash or Fish completions;
- when and how agents should use `--json`; and
- how to install the bundled skill and consult command help.

Onboarding text is versioned with the CLI. It must not depend on a live server, so it remains
available while diagnosing installation or connectivity.

### Skill installation

`taskman agent skill install` installs the CLI-bundled skill to:

```text
~/.agents/skills/taskman-cli/
```

The installed directory is owned by this installer and contains the skill metadata needed to
identify its source and CLI version. The ownership marker is `.taskman-managed.json` with
`installer`, `skill`, and `cli_version` fields. Installation creates parent directories when needed
and stages the complete new skill in a sibling directory. For an update, it renames the existing
target to a sibling backup, renames the staged directory into the target path, and restores the
backup if the second rename fails. After a successful replacement it removes the backup. A failed
install therefore leaves either the previous complete skill or the new complete skill at the
target, never a partially copied directory.

Re-running the command is idempotent and updates an installer-owned older or different bundled
version. If the target exists without recognizable ownership metadata, installation refuses rather
than destroying custom content. An explicit `--force` option may replace that unrecognized target
after clearly reporting the action.

The production command always defaults to the standard location. Installer internals accept an
injected destination root for tests so verification never mutates the developer's real
`~/.agents/skills`.

### Skill content

The `taskman-cli` skill teaches an agent:

- when Taskman is the appropriate system of record;
- how to verify CLI availability and backend connectivity;
- how to discover commands through help;
- how to configure the API URL;
- how to use structured output safely;
- common Project, List, Task, and movement workflows;
- how to interpret validation, not-found, connection, and server failures; and
- that only explicit user authority permits consequential operations such as future deletions.

It references command help rather than duplicating every option. Examples included in the skill
must be executable against the matching CLI version.

## Ongoing parity and maintenance

After this slice, a later feature is not complete merely because it is available in LiveView. For
every meaningful persisted or query operation that ships through the UI, the same slice must:

1. expose the operation through the versioned API;
2. expose it through the CLI where terminal use makes sense;
3. document it in top-level, group, and leaf help as applicable;
4. update Bash and Fish completions when commands, options, or fixed values change;
5. update onboarding when the basic workflow or installation/configuration changes;
6. update the bundled skill when agent usage changes; and
7. add proportionate API, CLI, completion, and skill verification.

Exceptions are presentation-only state and operations for which terminal access would be unsafe or
meaningless. Any other exception requires an explicit product decision recorded in the relevant
slice specification.

CLI-visible changes include commands, options, defaults, environment variables, output fields,
exit statuses, API behavior, error semantics, installation behavior, and agent workflows. Reviews
must treat help, completions, onboarding text, and the bundled skill as compatibility artifacts
rather than optional extras.

## Error behavior

- A server validation failure preserves field detail in JSON mode and produces a concise readable
  explanation otherwise.
- A not-found or cross-Project resource returns the same stable public error category without
  revealing unrelated resource data.
- A connection refusal or timeout identifies the configured API URL and points to onboarding or
  backend-start guidance.
- Invalid JSON or a response that violates the expected API contract fails visibly; the CLI never
  presents it as a successful empty result.
- Structured mode never mixes progress or diagnostics into stdout.
- Skill installation reports its target and whether it installed, updated, or was already current.
- Refusal to overwrite an unrecognized skill directory is non-zero and explains `--force` without
  applying a partial change.

## Testing and verification

### API

- Controller tests cover every route, successful representation, malformed input, validation
  failure, not-found/cross-Project scope, and correct HTTP status.
- Context tests remain the primary invariant coverage; API tests prove that controllers reuse
  those contracts without bypassing them.
- Task listing tests cover direct Project Tasks, direct List Tasks, descendant inclusion, and
  owning-location paths.
- Task update and movement tests cover editable fields, lifecycle values, same-Project movement,
  unchanged destinations, and cross-Project rejection.

### CLI

- Parser and dispatch tests cover every command, required operand, option, and global-option
  precedence.
- Req uses a controlled test adapter for success, each public API error, timeout/connection
  behavior, and malformed responses.
- Output tests prove readable mode, single-value JSON stdout, stderr separation, and exact exit
  statuses.
- Help tests prove that the top level lists every group, every group lists every command, and every
  leaf documents its required arguments and options.
- Completion tests generate Bash and Fish scripts from the command registry and prove that every
  command path, option, and fixed value is represented without backend access.
- Focused verification parses the generated scripts with `bash -n` and `fish -n`; the verification
  environment provides both interpreters.
- Onboarding tests assert the installation command, installed path, `PATH` guidance, backend,
  configuration, structured-output, help, completion, and skill-install guidance.

### Skill installer and maintenance

- Installer tests use a temporary destination and cover first install, idempotence, update,
  unrecognized-target refusal, forced replacement, and failure without partial output.
- The bundled skill is validated as a complete skill artifact.
- Examples and command references in the skill are checked against the actual command registry or
  an equivalent single source of truth so drift is caught by tests.

### Integrated verification

- A focused smoke test starts the Phoenix application in the test environment and exercises at
  least one read and one mutation through the real CLI-to-HTTP path.
- The complete repository gate remains `mix precommit`.
- Manual acceptance checks top-level and representative leaf help, readable and JSON output,
  connectivity guidance, onboarding, Bash and Fish completion installation, and a temporary skill
  installation.

## Documentation responsibilities

- The MVP product specification owns the durable requirement for local API/CLI access and ongoing
  parity.
- The roadmap owns delivery order.
- This specification owns the initial Slice 4 behavior and design decisions.
- The development guide may reference the parity gate as a delivery rule but must not duplicate the
  product contract.
- CLI help and onboarding own installed-command guidance.
- The shared command registry owns command metadata consumed by help and shell-completion
  generation.
- The bundled skill owns agent-operational guidance and must remain version-matched to the CLI.

## Next-session checklist

1. Review and approve the implementation plan and repository-local Beads delivery graph.
2. Begin implementation in a fresh session after updating the handoff.
