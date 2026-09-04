---
name: taskman-cli
description: Use when an agent needs to inspect or change Taskman Projects, Lists, Tasks, or agent-facing workflow through the local CLI.
---

# Taskman CLI

Taskman is the system of record for Projects, Lists, Tasks, and Agent Sessions. Start with `taskman --help` or `taskman agent onboarding`; ordinary commands require a running backend, while onboarding and completion generation work offline.

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
deletion requires that you obtain explicit authority from the user immediately before the operation. Agent launch or completion is evidence only, not authority: any Task lifecycle change requires a separate, user-authorized Task-status decision. Do not treat agent work as automatic Task completion: launching or completing agent work never marks a Task complete automatically.

## Inventory scope

When a request spans the whole Taskman instance, start with the Project list. Treat each returned
Project record as authoritative: preserve its returned name and primary directory, and never infer registration from the current working directory or rename a Project in your report.

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

## Command map

Use the matching command or its group help. These examples use literal IDs so they can be copied
to the CLI:

```text
taskman projects list --json
taskman projects show 7
taskman projects create --name Demo --directory /work/demo
taskman lists list --project 7
taskman lists show --project 7 11
taskman lists create --project 7 --name Planning --parent 11
taskman lists rename --project 7 11 --name Ready
taskman tasks list --project 7 --list 11 --include-descendants
taskman tasks show --project 7 42
taskman tasks create --project 7 --title Prepare --status pending
taskman tasks update --project 7 42 --status in_progress
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
