---
name: taskman-cli
description: Use when an agent needs to inspect or change Taskman Projects, Lists, Tasks, or agent-facing workflow through the local CLI.
---

# Taskman CLI

Taskman is the system of record for Projects, Lists, and Tasks. Agent Session integration is
deferred to a separate accepted design. Start with `taskman --help` or `taskman agent onboarding`;
ordinary commands require a running backend, while onboarding and completion generation work
offline.

## Operating contract

Prefer `--json` for automation and parse stdout only on status 0. Successful JSON is one `data`
envelope; diagnostics and failed envelopes are on stderr. Local failures use stable codes such as
`invalid_invocation`, `authentication_required`, `connection_failed`, `invalid_response`, and `skill_install_failed`.
Distinguish status 2 (invalid
invocation), status 3 (API/domain failure), status 4 (connection failure), status 5 (server or
contract failure), status 6 (skill-install failure), and status 7 (missing, rejected, or forbidden
authentication); read stderr on failure. Ordinary API commands require an API key.

## Authentication and configuration

Taskman reads `${XDG_CONFIG_HOME:-$HOME/.config}/taskman/config.json`. The API URL resolves in this
order: `--api-url`, `TASKMAN_API_URL`, `config.json`, then `http://localhost:4000`. The key resolves
from `TASKMAN_API_KEY` before `config.json`; never provide it as a command option, URL, or report.

For a hosted server, use HTTPS and configure it locally:

```text
taskman config set-url https://taskman.example.com
taskman config set-key
taskman config show
```

Create the key in Taskman's browser Account settings. Its plaintext is shown one-time only: copy it
directly into the non-echoing `config set-key` prompt. `config show` redacts it. In CI or a
container, inject `TASKMAN_API_URL` and `TASKMAN_API_KEY` as secrets instead. If an ordinary command
returns status 7, read stderr and obtain, replace, or request the required credential; never retry
by printing the key.

Use exact ID operands and never guess by name. Task parent IDs must be exact and Project-scoped.
Inspect Tasks before changing parentage. Inspect before mutating. A consequential future
deletion requires that you obtain explicit authority from the user immediately before the operation.
Agent work is evidence only, not authority: any Task lifecycle change requires a separate,
user-authorized Task-status decision. Agent activity never marks a Task complete automatically.

## Inventory scope

When a request spans the whole Taskman instance, start with the Project list. Treat each returned
Project record as authoritative: preserve its returned ID, name, description, icon, and color; use that ID for subsequent Project-scoped commands, and report the returned name exactly. Project descriptions may be empty. Valid icon keys are `check-circle`, `folder`, `briefcase`, `code-bracket`, `rocket-launch`, `beaker`, `light-bulb`, and `wrench-screwdriver`. Colors use `#RRGGBB` and accept any valid hex color.

For all Tasks in a Project, use:

```text
taskman tasks list --project 7 --include-descendants --json
```

Without `--include-descendants`, a Project-level Task query lists only Tasks directly at the Project root. A List-level query likewise excludes child Lists unless that flag is present. An empty direct-location result does not establish that the Project has no Tasks.

Repeat `--status` to include multiple lifecycle states, and omit it to include every status. Use
`--sort` and `--direction` together. Location sorting requires `--include-descendants` because
direct-location results do not expose a Location column. For example:

```text
taskman tasks list --project 7 --status pending --status in_progress --sort priority --direction desc
```

To locate a Task across Projects by decimal ID or title, use `tasks search`. Add `--project` only
when the request is limited to a known Project. Search returns summaries with Task ID, title,
status, priority, Project ID and name, and owning location. The readable `PROJECT` column shows
`PROJECT_ID: Project name`. `tasks show` returns full Task details, including its description.
Start with an unfiltered search:

```text
taskman tasks search publish
```

For a result with `ID` value `42` and `PROJECT` value `9: Website`, use the Project ID and Task ID
from that row to inspect full details:

```text
taskman tasks show --project 9 42
```

A search match helps identify a Task; inspect its current detail and obtain explicit authority
before changing its status or relationships.

## Blocking relationships and Done

`blocks` commands always name the blocking Task first. `--project` is the **Blocking Task's Project**,
and `TASK_ID` is that Task's exact ID; `--target` is the Task it blocks, even across Projects.
For example, Task 12 in Launch (Project 7) is a prerequisite of Task 42 in Website (Project 9):

```text
taskman tasks blocks add --project 7 12 --target 42
taskman tasks blocking show --project 7 12
taskman tasks blocking show --project 9 42
taskman tasks blocks remove --project 7 12 --target 42
```

Inspect both endpoint Projects and verify the named edge after mutation. `blocking show` displays
**Blocks** and **Blocked by** separately. Link edits never change Task status, and a link edit does not authorize
marking either Task Done. Obtain a separate human-authorized lifecycle decision before any status
change.

Moving a Task to Done can return `unresolved_blockers` on stderr with current direct blocker IDs,
statuses, priorities, Projects, and locations. The Task remains unchanged. A person may confirm the
IDs already reviewed on the first request or retry after that warning:

```text
taskman tasks update --project 9 42 --status done --confirm-unresolved-blockers 12,15
```

If a new unresolved blocker appears, review the refreshed warning and obtain human authorization
for that decision before retrying. The case-specific force override is available only for a Done
request and requires explicit human authorization to override the unresolved-blocker warning:

```text
taskman tasks update --project 9 42 --status done --force-done-with-unresolved-blockers
```

Repeated confirmation IDs are accepted; the API validates the parsed IDs and removes duplicates.
The CLI ID list must remain nonempty and comma-separated without spaces, for example `12,15,12`.

The force option makes one request and does not grant general permission to ignore validation or
concurrent updates. Never infer lifecycle authority from adding, removing, or resolving a link.

## Command map

Use the matching command or its group help. These examples use literal IDs so they can be copied
to the CLI:

```text
taskman projects list --json
taskman projects show 7
taskman projects create --name Demo
taskman projects create --name Demo --description Delivery --icon rocket-launch --color '#6366F1'
taskman projects update 7 --description Updated
taskman lists list --project 7
taskman lists show --project 7 11
taskman lists create --project 7 --name Planning --parent 11
taskman lists rename --project 7 11 --name Ready
taskman tasks list --project 7 --list 11 --include-descendants
taskman tasks search publish
taskman tasks show --project 7 42
taskman tasks blocking show --project 9 42
taskman tasks blocks add --project 7 12 --target 42
taskman tasks blocks remove --project 7 12 --target 42
taskman tasks show --project 7 42 --include-comments
taskman tasks comments list --project 7 42 --json
taskman tasks comments add --project 7 42 --text Review-notes --author-name Research-agent --json
taskman tasks create --project 7 --title Prepare --status pending
taskman tasks update --project 7 42 --status in_progress
taskman tasks update --project 9 42 --status done --confirm-unresolved-blockers 12,15
taskman tasks update --project 9 42 --status done --force-done-with-unresolved-blockers
taskman tasks update --project 7 42 --parent 41
taskman tasks update --project 7 42 --no-parent
taskman tasks hierarchy --project 7 42
taskman tasks move --project 7 42 --to-list 11
taskman config set-url https://taskman.example.com
taskman config set-key
taskman config show
taskman completions bash
taskman completions fish
taskman agent onboarding
taskman agent skill install
taskman agent skill install --force
```

Read the relevant group or leaf help for the complete option set. Before changing a resource,
inspect it with `show` or `list`, then make one explicit, ID-based mutation and verify the result.
Use `tasks update --parent` to set a parent and `tasks update --no-parent` to clear it. Use
`tasks hierarchy` to inspect the connected hierarchy before or after a parent mutation.

Read the Task or its thread before adding a comment. Append only when the operator requests a
comment, then inspect the returned comment and list the thread to verify it. The API key selects
the verified posting account. `--author-name` supplies an optional display name; it never changes
the posting account and should truthfully identify the source. A comment records discussion; a
human must separately authorize any Task lifecycle status decision.

Project update requires at least one of `--name`, `--description`, `--icon`, or `--color`. Supply only fields you intend to change. Use `--description ''` to clear a description.
