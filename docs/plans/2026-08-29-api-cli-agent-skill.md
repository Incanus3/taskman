# API, CLI, and Agent Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a versioned local JSON API, an installable `taskman` escript with complete
Project/List/Task parity, Bash and Fish completions, agent onboarding, and a safely installable
version-matched agent skill.

**Architecture:** Phoenix JSON controllers and LiveView remain equal adapters over the existing
public contexts. The escript starts no Taskman server services; it parses a declarative command
registry, calls `/api/v1` through Req, and renders either human-readable output or one JSON
envelope. Help and static shell completions share the registry, while onboarding and skill
installation are offline CLI commands.

**Tech Stack:** Elixir 1.17+, Phoenix 1.8, Ecto/PostgreSQL, Req 0.5, Jason, Mix escript,
`OptionParser`, ExUnit, `Req.Test`, Bandit, Bash, Fish.

**Spec:** `docs/specs/2026-08-29-api-cli-agent-skill-design.md`

**Status:** Draft for review

## Global Constraints

- Read the complete specification before executing any task.
- Keep the API under `/api/v1`, local-only, and unauthenticated.
- Controllers call only public `Taskman.Projects`, `Taskman.Lists`, and `Taskman.Tasks` APIs; they
  never call `Taskman.Repo` or construct Ecto queries.
- The CLI calls the HTTP API and never opens the database or duplicates domain validation.
- Configure the escript as `escript: [main_module: Taskman.CLI, app: nil]`.
- Use Req; do not introduce HTTPoison, Tesla, or `:httpc`.
- Preserve the exact routes, JSON fields, error codes, command paths, options, underscore lifecycle
  tokens, global precedence, and numeric exit statuses from the specification.
- Human output goes to stdout on success; JSON mode writes exactly one envelope to stdout;
  diagnostics and all error envelopes go to stderr.
- Bash and Fish completion generation must use the same declarative registry as help and must not
  call the backend.
- The skill installs only below `~/.agents/skills/taskman-cli/`; tests inject a temporary skills
  root and never mutate the real user directory.
- Use `start_supervised!/1` for test processes and no `Process.sleep/1`.
- Use `but` for every version-control mutation. Each task begins from a clean task boundary and
  ends with its focused tests passing before committing.
- Do not add deletion, relationships, Agent Session commands, authentication, remote access,
  name-based resource resolution, `taskman serve`, native packaging, or dynamic resource
  completion.

## File and Responsibility Map

### API boundary

- `lib/taskman_web/controllers/api/params.ex` — parse positive IDs and request/query primitives.
- `lib/taskman_web/controllers/api/representation.ex` — serialize Projects, Lists, Tasks,
  locations, and changeset fields into the public JSON contract.
- `lib/taskman_web/controllers/api/fallback_controller.ex` — translate context and transport
  failures into the shared error envelope and status.
- `lib/taskman_web/controllers/api/project_controller.ex` — Project list/show/create.
- `lib/taskman_web/controllers/api/list_controller.ex` — List list/show/create/rename.
- `lib/taskman_web/controllers/api/task_controller.ex` — Task list/show/create/update/move.
- `lib/taskman_web/controllers/error_json.ex` — shared 400/404/500 envelopes for errors raised
  outside controller fallback.
- `lib/taskman_web/router.ex` — exact `/api/v1` routes.

### CLI core

- `lib/taskman/cli.ex` — escript `main/1`, version, IO, and process exit boundary.
- `lib/taskman/cli/result.ex` — testable command result carrying status/stdout/stderr.
- `lib/taskman/cli/command.ex` — declarative leaf-command struct.
- `lib/taskman/cli/option.ex` — command/global option metadata.
- `lib/taskman/cli/argument.ex` — positional argument metadata.
- `lib/taskman/cli/invocation.ex` — successfully parsed command invocation.
- `lib/taskman/cli/registry.ex` — the one command/global-option registry.
- `lib/taskman/cli/parser.ex` — global extraction, command matching, option parsing, and constraints.
- `lib/taskman/cli/help.ex` — top-level, group, and leaf help rendered from the registry.
- `lib/taskman/cli/runner.ex` — parse/dispatch/error orchestration.
- `lib/taskman/cli/client.ex` — Req transport and API/connection/response classification.
- `lib/taskman/cli/output.ex` — JSON and readable rendering.
- `lib/taskman/cli/commands/projects.ex` — Project request construction.
- `lib/taskman/cli/commands/lists.ex` — List request construction.
- `lib/taskman/cli/commands/tasks.ex` — Task request construction.
- `lib/taskman/cli/completions.ex` — Bash/Fish generation from registry metadata.
- `lib/taskman/cli/onboarding.ex` — offline, versioned onboarding content.

### Agent skill installation

- `priv/taskman_cli_skill/SKILL.md` — bundled `taskman-cli` agent guidance.
- `lib/taskman/cli/skill/bundle.ex` — compile-time bundled files and version metadata.
- `lib/taskman/cli/skill/file_system.ex` — filesystem behavior.
- `lib/taskman/cli/skill/local_file_system.ex` — production filesystem adapter.
- `lib/taskman/cli/skill/installer.ex` — stage, recognize, replace, roll back, and clean up.
- `test/support/fake_skill_file_system.ex` — deterministic rename-failure adapter.

### Delivery and verification

- `mix.exs` — escript configuration.
- `.gitignore` — ignore the generated root `/taskman` escript.
- `.github/workflows/elixir.yml` — provide Bash and Fish for completion parsing.
- `README.md` — installed CLI entry point and help/onboarding discovery.
- `docs/development.md` — later-slice API/CLI/help/completion/skill parity gate.
- `docs/planning/roadmap.md` — mark Slice 4 complete only after final acceptance.

## Delivery Graph

The repository-local Beads feature is `tas-yty`. Its child tasks correspond directly to the
implementation tasks below:

| Plan task | Beads issue | Dependency gate |
| --- | --- | --- |
| 1. API contract and Projects | `tas-yty.1` | Ready |
| 2. Lists API | `tas-yty.2` | `tas-yty.1` |
| 3. Tasks API | `tas-yty.3` | `tas-yty.2` |
| 4. CLI core and escript | `tas-yty.4` | Ready |
| 5. Req client and Projects CLI | `tas-yty.5` | `tas-yty.1`, `tas-yty.4` |
| 6. Lists and Tasks CLI | `tas-yty.6` | `tas-yty.3`, `tas-yty.5` |
| 7. Completions and onboarding | `tas-yty.7` | `tas-yty.4` |
| 8. Bundled skill and installer | `tas-yty.8` | `tas-yty.7` |
| 9. Integrated delivery gate | `tas-yty.9` | `tas-yty.3`, `tas-yty.6`, `tas-yty.7`, `tas-yty.8` |

---

### Task 1: Shared JSON Contract and Project Endpoints

**Files:**

- Create: `lib/taskman_web/controllers/api/params.ex`
- Create: `lib/taskman_web/controllers/api/representation.ex`
- Create: `lib/taskman_web/controllers/api/fallback_controller.ex`
- Create: `lib/taskman_web/controllers/api/project_controller.ex`
- Modify: `lib/taskman_web/controllers/error_json.ex`
- Modify: `lib/taskman_web/router.ex`
- Create: `test/taskman_web/controllers/api/project_controller_test.exs`
- Modify: `test/taskman_web/controllers/error_json_test.exs`

**Interfaces:**

- Consumes: `Projects.list_projects/0`, `Projects.get_project/1`,
  `Projects.create_project/1`.
- Produces:
  - `TaskmanWeb.API.Params.positive_id/1 ::
    {:ok, pos_integer()} | {:error, :invalid_request}`
  - `TaskmanWeb.API.Representation.project/1 :: map()`
  - `TaskmanWeb.API.Representation.validation_fields/1 :: map()`
  - fallback results for `:invalid_request`, `:not_found`, `:unchanged_location`,
    `%Ecto.Changeset{}`, and `:internal_error`.

- [ ] **Step 1: Write failing Project API and error-envelope tests**

Use `TaskmanWeb.ConnCase` and `Taskman.ProjectsFixtures`. Cover:

```elixir
test "GET /api/v1/projects returns ordered project data", %{conn: conn} do
  first = project_fixture(%{name: "First"})
  second = project_fixture(%{name: "Second"})

  conn = get(conn, "/api/v1/projects")

  assert %{
           "data" => [
             %{"id" => first.id, "name" => "First", "primary_directory" => _},
             %{"id" => second.id, "name" => "Second", "primary_directory" => _}
           ]
         } = json_response(conn, 200)
end

test "POST /api/v1/projects returns 201 and normalized data", %{conn: conn} do
  conn =
    post(conn, "/api/v1/projects", %{
      "project" => %{"name" => "CLI", "primary_directory" => File.cwd!()}
    })

  assert %{"data" => %{"id" => id, "name" => "CLI"}} = json_response(conn, 201)
  assert is_integer(id)
end

test "POST /api/v1/projects returns field errors", %{conn: conn} do
  conn = post(conn, "/api/v1/projects", %{"project" => %{"name" => ""}})

  assert %{
           "error" => %{
             "code" => "validation_failed",
             "fields" => %{"name" => [_], "primary_directory" => [_]}
           }
         } = json_response(conn, 422)
end

test "malformed and missing project ids use stable errors", %{conn: conn} do
  assert %{"error" => %{"code" => "invalid_request"}} =
           conn |> get("/api/v1/projects/not-an-id") |> json_response(400)

  assert %{"error" => %{"code" => "not_found"}} =
           build_conn() |> get("/api/v1/projects/999999999") |> json_response(404)
end
```

Update `error_json_test.exs` to assert:

```elixir
assert TaskmanWeb.ErrorJSON.render("500.json", %{}) ==
         %{error: %{code: "internal_error", message: "Internal Server Error"}}
```

- [ ] **Step 2: Run the focused tests and confirm the red state**

Run:

```sh
mix test test/taskman_web/controllers/api/project_controller_test.exs \
  test/taskman_web/controllers/error_json_test.exs
```

Expected: compilation or routing failures because the API modules and `/api/v1/projects` routes do
not exist.

- [ ] **Step 3: Implement parsing, representation, fallback, and Project actions**

Implement the public shapes:

```elixir
def positive_id(value) when is_integer(value) and value > 0, do: {:ok, value}

def positive_id(value) when is_binary(value) do
  case Integer.parse(value) do
    {id, ""} when id > 0 -> {:ok, id}
    _invalid -> {:error, :invalid_request}
  end
end

def positive_id(_value), do: {:error, :invalid_request}
```

```elixir
def project(project) do
  %{
    id: project.id,
    name: project.name,
    primary_directory: project.primary_directory
  }
end

def validation_fields(changeset) do
  Ecto.Changeset.traverse_errors(changeset, fn {message, options} ->
    Enum.reduce(options, message, fn {key, value}, rendered ->
      String.replace(rendered, "%{#{key}}", to_string(value))
    end)
  end)
end
```

Implement fallback responses with `put_status/2` and `json/2`:

```elixir
def call(conn, {:error, :not_found}),
  do: error(conn, 404, "not_found", "Resource not found")

def call(conn, {:error, :invalid_request}),
  do: error(conn, 400, "invalid_request", "Invalid request")

def call(conn, {:error, %Ecto.Changeset{} = changeset}) do
  conn
  |> put_status(:unprocessable_entity)
  |> json(%{
    error: %{
      code: "validation_failed",
      message: "Validation failed",
      fields: Representation.validation_fields(changeset)
    }
  })
end
```

Create the exact routes:

```elixir
scope "/api/v1", TaskmanWeb.API do
  pipe_through :api

  get "/projects", ProjectController, :index
  post "/projects", ProjectController, :create
  get "/projects/:project_id", ProjectController, :show
end
```

Every controller uses:

```elixir
use TaskmanWeb, :controller
action_fallback TaskmanWeb.API.FallbackController
```

- [ ] **Step 4: Run Project API tests green**

Run:

```sh
mix test test/taskman_web/controllers/api/project_controller_test.exs \
  test/taskman_web/controllers/error_json_test.exs
```

Expected: all tests pass with 200/201/400/404/422 responses and the exact envelopes.

- [ ] **Step 5: Run existing Project context and browser tests**

Run:

```sh
mix test test/taskman/projects_test.exs test/taskman_web/live/project_live_test.exs
```

Expected: existing Project behavior remains green.

- [ ] **Step 6: Commit the Project API boundary**

Run:

```sh
but diff
but commit -b api-cli-agent-skill -m "Add versioned Project JSON API"
```

Expected: one focused commit containing only Task 1 files.

---

### Task 2: List API Parity

**Files:**

- Create: `lib/taskman_web/controllers/api/list_controller.ex`
- Modify: `lib/taskman_web/controllers/api/representation.ex`
- Modify: `lib/taskman_web/router.ex`
- Create: `test/taskman_web/controllers/api/list_controller_test.exs`

**Interfaces:**

- Consumes Task 1 `Params`, `Representation`, and `FallbackController`.
- Consumes `Lists.list_lists_for_project/1`, `Lists.get_list_for_project/2`,
  `Lists.create_list/3`, `Lists.rename_list/3`, and `Lists.path_for/2`.
- Produces `Representation.task_list/2 :: map()` and all four List routes.

- [ ] **Step 1: Write failing List endpoint tests**

Cover stable tree order and paths, root/child creation, show, rename, invalid parent ID,
cross-Project lookup, duplicate sibling validation, and malformed IDs. Representative assertions:

```elixir
test "GET lists returns parent ids and root-to-node paths", %{conn: conn} do
  project = project_fixture(%{})
  root = list_fixture(project, %{name: "Planning"})
  child = list_fixture(project, root, %{name: "Launch"})

  conn = get(conn, "/api/v1/projects/#{project.id}/lists")

  assert %{
           "data" => [
             %{"id" => root.id, "parent_list_id" => nil, "path" => ["Planning"]},
             %{
               "id" => child.id,
               "parent_list_id" => root.id,
               "path" => ["Planning", "Launch"]
             }
           ]
         } = json_response(conn, 200)
end

test "POST child list rejects a parent from another Project", %{conn: conn} do
  project = project_fixture(%{})
  other = project_fixture(%{})
  parent = list_fixture(other)

  conn =
    post(conn, "/api/v1/projects/#{project.id}/lists", %{
      "list" => %{"name" => "Invalid", "parent_list_id" => parent.id}
    })

  assert %{"error" => %{"code" => "not_found"}} = json_response(conn, 404)
end
```

- [ ] **Step 2: Run the List controller test red**

Run:

```sh
mix test test/taskman_web/controllers/api/list_controller_test.exs
```

Expected: route/module failures.

- [ ] **Step 3: Implement List representation and actions**

Serialize with the Project's complete ordered List collection:

```elixir
def task_list(task_list, project_lists) do
  %{
    id: task_list.id,
    project_id: task_list.project_id,
    parent_list_id: task_list.parent_list_id,
    name: task_list.name,
    path: project_lists |> Lists.path_for(task_list) |> Enum.map(& &1.name)
  }
end
```

Resolve optional parents without casting ownership fields:

```elixir
defp resolve_parent(_project, nil), do: {:ok, nil}

defp resolve_parent(project, parent_id) do
  with {:ok, id} <- Params.positive_id(parent_id),
       %TaskList{} = parent <- Lists.get_list_for_project(project, id) do
    {:ok, parent}
  else
    nil -> {:error, :not_found}
    {:error, _reason} = error -> error
  end
end
```

Add:

```elixir
get "/projects/:project_id/lists", ListController, :index
post "/projects/:project_id/lists", ListController, :create
get "/projects/:project_id/lists/:list_id", ListController, :show
patch "/projects/:project_id/lists/:list_id", ListController, :update
```

- [ ] **Step 4: Run List API and context tests**

Run:

```sh
mix test test/taskman_web/controllers/api/list_controller_test.exs \
  test/taskman/lists_test.exs
```

Expected: List API contract and existing List invariants pass.

- [ ] **Step 5: Commit List API parity**

Run:

```sh
but diff
but commit -b api-cli-agent-skill -m "Add nested List JSON API"
```

---

### Task 3: Task API Parity

**Files:**

- Create: `lib/taskman_web/controllers/api/task_controller.ex`
- Modify: `lib/taskman_web/controllers/api/representation.ex`
- Modify: `lib/taskman_web/router.ex`
- Create: `test/taskman_web/controllers/api/task_controller_test.exs`

**Interfaces:**

- Consumes Tasks and Lists public APIs plus Task 1 transport helpers.
- Produces `Representation.task/2 :: map()` and Task list/create/show/update/move routes.
- Task JSON lifecycle tokens are atoms encoded by Jason as the exact underscore strings.

- [ ] **Step 1: Write failing Task endpoint tests**

Cover:

- direct Project and direct List queries;
- `include_descendants=true` with complete location paths;
- show inside the correct Project and cross-Project 404;
- create at Project root and in a List with every editable field;
- create with a foreign List 404;
- update each editable field and clear `due_at` with JSON `null`;
- underscore lifecycle values;
- move to List and Project root;
- unchanged move 409;
- malformed Project/List/Task IDs 400;
- changeset failures 422.

Representative list and move assertions:

```elixir
test "GET tasks includes descendant locations", %{conn: conn} do
  project = project_fixture(%{})
  root = list_fixture(project, %{name: "Planning"})
  child = list_fixture(project, root, %{name: "Launch"})
  task = task_fixture(project, child, %{title: "Copy"})

  conn =
    get(
      conn,
      "/api/v1/projects/#{project.id}/tasks?list_id=#{root.id}&include_descendants=true"
    )

  assert %{
           "data" => [
             %{
               "id" => task.id,
               "location" => %{
                 "kind" => "list",
                 "list_id" => child.id,
                 "path" => ["Planning", "Launch"]
               }
             }
           ]
         } = json_response(conn, 200)
end

test "POST move reports unchanged location as 409", %{conn: conn} do
  project = project_fixture(%{})
  task = task_fixture(project)

  conn =
    post(conn, "/api/v1/projects/#{project.id}/tasks/#{task.id}/move", %{
      "destination" => %{"list_id" => nil}
    })

  assert %{"error" => %{"code" => "unchanged_location"}} = json_response(conn, 409)
end
```

- [ ] **Step 2: Run Task API tests red**

Run:

```sh
mix test test/taskman_web/controllers/api/task_controller_test.exs
```

Expected: missing route/module failures.

- [ ] **Step 3: Implement Task location representation and request resolution**

```elixir
def task(task, project_lists) do
  path =
    case task.list_id do
      nil -> []
      list_id ->
        owner = Enum.find(project_lists, &(&1.id == list_id))
        project_lists |> Lists.path_for(owner) |> Enum.map(& &1.name)
    end

  %{
    id: task.id,
    project_id: task.project_id,
    list_id: task.list_id,
    title: task.title,
    description: task.description,
    status: task.status,
    priority: task.priority,
    due_at: task.due_at,
    location: %{
      kind: if(is_nil(task.list_id), do: "project", else: "list"),
      list_id: task.list_id,
      path: path
    }
  }
end
```

Do not require an Ecto preload for `task.list`; find the owner in `project_lists` and pass that
struct plus the complete list collection to `Lists.path_for/2`. Keep a private controller helper:

```elixir
defp task_data(project, task) do
  project_lists = Lists.list_lists_for_project(project)
  Representation.task(task, project_lists)
end
```

For create, pop `list_id` before calling the context:

```elixir
{list_id, task_attrs} = Map.pop(task_params, "list_id")

with {:ok, location} <- resolve_location(project, list_id),
     {:ok, task} <- Tasks.create_task(project, location, task_attrs) do
  conn |> put_status(:created) |> json(%{data: task_data(project, task)})
end
```

Add the exact Task routes from the specification.

- [ ] **Step 4: Run Task API and domain tests**

Run:

```sh
mix test test/taskman_web/controllers/api/task_controller_test.exs \
  test/taskman/tasks_test.exs test/taskman/lists_test.exs
```

Expected: all API and existing context tests pass.

- [ ] **Step 5: Run every API controller test**

Run:

```sh
mix test test/taskman_web/controllers/api
```

Expected: Project, List, Task, and shared error contracts pass together.

- [ ] **Step 6: Commit Task API parity**

Run:

```sh
but diff
but commit -b api-cli-agent-skill -m "Add Task JSON API parity"
```

---

### Task 4: CLI Command Registry, Parser, Help, and Escript Boundary

**Files:**

- Create: `lib/taskman/cli.ex`
- Create: `lib/taskman/cli/result.ex`
- Create: `lib/taskman/cli/command.ex`
- Create: `lib/taskman/cli/option.ex`
- Create: `lib/taskman/cli/argument.ex`
- Create: `lib/taskman/cli/invocation.ex`
- Create: `lib/taskman/cli/registry.ex`
- Create: `lib/taskman/cli/parser.ex`
- Create: `lib/taskman/cli/help.ex`
- Create: `lib/taskman/cli/runner.ex`
- Modify: `mix.exs`
- Modify: `.gitignore`
- Create: `test/taskman/cli/parser_test.exs`
- Create: `test/taskman/cli/help_test.exs`
- Create: `test/taskman/cli_test.exs`

**Interfaces:**

- Produces:
  - `Taskman.CLI.run/2 :: %Taskman.CLI.Result{}`
  - `Taskman.CLI.version/0 :: String.t()`
  - `Registry.commands/0 :: [Command.t()]`
  - `Registry.global_options/0 :: [Option.t()]`
  - `Registry.find/1 :: {:ok, Command.t()} | {:group, [String.t()]} | :error`
  - `Parser.parse/2 :: {:ok, Invocation.t()} | {:help, path} | :version |
    {:error, message, usage_path}`
  - `Help.render/1 :: String.t()`.

- [ ] **Step 1: Write command-registry and parser tests**

Assert all exact paths from the spec:

```elixir
assert Enum.map(Registry.commands(), & &1.path) == [
         ~w(projects list),
         ~w(projects show),
         ~w(projects create),
         ~w(lists list),
         ~w(lists show),
         ~w(lists create),
         ~w(lists rename),
         ~w(tasks list),
         ~w(tasks show),
         ~w(tasks create),
         ~w(tasks update),
         ~w(tasks move),
         ~w(completions bash),
         ~w(completions fish),
         ~w(agent onboarding),
         ~w(agent skill install)
       ]
```

Add parser tests for:

```elixir
assert {:ok, %Invocation{
         command: %{path: ["tasks", "update"]},
         arguments: %{task_id: 42},
         options: %{project: 7, status: "in_progress", clear_due_at: true},
         globals: %{api_url: "http://localhost:4010", json: true}
       }} =
         Parser.parse(
           ~w(tasks update --project 7 42 --status in_progress --clear-due-at
              --json --api-url http://localhost:4010),
           %{}
         )
```

Cover global options before and after paths, `TASKMAN_API_URL` precedence, missing operands,
unknown commands/options, invalid integer IDs, invalid enum values, zero update options, both
due-date options, and move destination exactly-one enforcement.

- [ ] **Step 2: Write failing help and CLI boundary tests**

Assert top-level help lists every group/utility and every global option; group help lists every
child; leaf help contains usage, required arguments, options, output note, exit statuses, and at
least one example.

```elixir
result = Taskman.CLI.run(["tasks", "move", "--help"])

assert result.status == 0
assert result.stderr == ""
assert result.stdout =~ "taskman tasks move --project PROJECT_ID TASK_ID"
assert result.stdout =~ "--to-list LIST_ID"
assert result.stdout =~ "--to-project-root"
```

Assert `Taskman.CLI.version/0 == Mix.Project.config()[:version]` to catch version drift.

- [ ] **Step 3: Run CLI core tests red**

Run:

```sh
mix test test/taskman/cli/parser_test.exs test/taskman/cli/help_test.exs \
  test/taskman/cli_test.exs
```

Expected: missing CLI modules.

- [ ] **Step 4: Implement focused data structs**

Use one module per file. Required fields:

```elixir
defstruct [:path, :summary, :usage, :handler, arguments: [], options: [], constraints: [], examples: []]
```

```elixir
defstruct [:name, :long, :type, :value_name, :help, values: [], required?: false]
```

```elixir
defstruct [:name, :value_name, :type, :help]
```

```elixir
defstruct [:command, arguments: %{}, options: %{}, globals: %{}]
```

```elixir
defstruct status: 0, stdout: "", stderr: ""
```

- [ ] **Step 5: Implement the exact registry**

Define shared status and priority values once:

```elixir
@statuses ~w(icebox pending in_progress in_review done will_not_do)
@priorities ~w(none low medium high urgent)
```

Each command record contains its full usage and examples. Express constraints as data:

```elixir
constraints: [
  {:at_least_one, ~w(title description status priority due_at clear_due_at)a},
  {:mutually_exclusive, [:due_at, :clear_due_at]}
]
```

```elixir
constraints: [
  {:exactly_one, [:to_list, :to_project_root]}
]
```

- [ ] **Step 6: Implement parser, help, and testable main boundary**

Keep the escript version explicit and test it against the Mix project version so a release cannot
silently ship mismatched CLI and bundled-skill metadata:

```elixir
@version "0.1.0"
def version, do: @version
```

Extract globals wherever they appear, with precedence:

```elixir
cli_api_url || Map.get(env, "TASKMAN_API_URL") || "http://localhost:4000"
```

Return results rather than writing from `Runner`. Keep IO and process exit in `main/1`:

```elixir
def main(args) do
  result = run(args)
  if result.stdout != "", do: IO.write(:stdio, result.stdout)
  if result.stderr != "", do: IO.write(:stderr, result.stderr)
  System.halt(result.status)
end
```

At this task, parsed API command handlers return a controlled `internal_error` result saying the
handler is not installed; later tasks replace that branch. Help, version, and invalid-invocation
paths are fully operational.

Configure:

```elixir
escript: [main_module: Taskman.CLI, app: nil]
```

Add `/taskman` to `.gitignore`.

- [ ] **Step 7: Run CLI core tests green**

Run:

```sh
mix test test/taskman/cli/parser_test.exs test/taskman/cli/help_test.exs \
  test/taskman/cli_test.exs
```

Expected: parser, help, version, stdout/stderr, and status tests pass.

- [ ] **Step 8: Build and invoke the escript**

Run:

```sh
mix escript.build
./taskman --help
./taskman --version
```

Expected: build succeeds without `DATABASE_URL` or `SECRET_KEY_BASE`; help and version exit 0; no
Phoenix endpoint or Repo startup is attempted.

- [ ] **Step 9: Commit CLI core**

Run:

```sh
but diff
but commit -b api-cli-agent-skill -m "Establish taskman CLI command registry"
```

---

### Task 5: Req Client, Output Contract, and Project Commands

**Files:**

- Create: `lib/taskman/cli/client.ex`
- Create: `lib/taskman/cli/output.ex`
- Create: `lib/taskman/cli/commands/projects.ex`
- Modify: `lib/taskman/cli/runner.ex`
- Create: `test/taskman/cli/client_test.exs`
- Create: `test/taskman/cli/output_test.exs`
- Create: `test/taskman/cli/commands/projects_test.exs`

**Interfaces:**

- Produces:
  - `Client.request(method, path, request_options, runtime_options) ::
    {:ok, data} | {:error, status, error_envelope}`
  - `Output.success(command, data, json?) :: String.t()`
  - `Output.error(error_envelope, json?) :: String.t()`
  - `Projects.execute(action, invocation, runtime_options) :: Result.t()`.
- `runtime_options[:req_options]` injects `plug: {Req.Test, stub}` in tests.

- [ ] **Step 1: Write Req transport classification tests**

Use `Req.Test` with `setup {Req.Test, :verify_on_exit!}`. Cover:

```elixir
Req.Test.expect(TaskmanCLIClient, fn conn ->
  Req.Test.json(conn, %{data: [%{id: 1, name: "One", primary_directory: "/tmp"}]})
end)

assert {:ok, [%{"id" => 1, "name" => "One"}]} =
         Client.request(:get, "/api/v1/projects", [], req_options: [
           plug: {Req.Test, TaskmanCLIClient}
         ])
```

Also cover API 400/404/409/422 → exit 3, connection refusal/timeout → exit 4, API 500,
non-JSON, and missing `data`/`error` envelope → exit 5.

- [ ] **Step 2: Write output and Project command tests**

Assert JSON output is exactly one Jason envelope plus a trailing newline. Assert readable Project
list/show/create includes IDs, names, and primary directories. Assert errors go only to stderr.
Inspect request method/path/body through `Req.Test.raw_body/1`.

```elixir
result =
  Taskman.CLI.run(
    ["projects", "create", "--name", "CLI", "--directory", File.cwd!(), "--json"],
    req_options: [plug: {Req.Test, ProjectCommands}]
  )

assert result.status == 0
assert result.stderr == ""
assert %{"data" => %{"name" => "CLI"}} = Jason.decode!(result.stdout)
```

- [ ] **Step 3: Run transport/output/Project tests red**

Run:

```sh
mix test test/taskman/cli/client_test.exs test/taskman/cli/output_test.exs \
  test/taskman/cli/commands/projects_test.exs
```

Expected: missing modules and handler dispatch.

- [ ] **Step 4: Implement Req transport and stable error mapping**

Start only Req's runtime dependencies:

```elixir
{:ok, _started} = Application.ensure_all_started(:req)
```

Build requests with:

```elixir
Req.new(
  [base_url: api_url, receive_timeout: 15_000, retry: false] ++ req_options
)
|> Req.request(method: method, url: path, json: json_body, params: query)
```

Omit `:json` and `:params` when absent. Validate every successful response has only the expected
top-level `data` contract and every failed response has an `error.code` and `error.message`.

- [ ] **Step 5: Implement readable/JSON output and Project handlers**

Dispatch:

```elixir
{:projects, :list} -> Projects.execute(:list, invocation, runtime_options)
{:projects, :show} -> Projects.execute(:show, invocation, runtime_options)
{:projects, :create} -> Projects.execute(:create, invocation, runtime_options)
```

Project create sends:

```elixir
%{
  "project" => %{
    "name" => invocation.options.name,
    "primary_directory" => invocation.options.directory
  }
}
```

Readable collection output starts with `ID\tNAME\tPRIMARY DIRECTORY`; member output uses one
`Label: value` line per public field.

- [ ] **Step 6: Run CLI Project tests green**

Run:

```sh
mix test test/taskman/cli/client_test.exs test/taskman/cli/output_test.exs \
  test/taskman/cli/commands/projects_test.exs
```

Expected: all transport classifications, output channels, statuses, and Project requests pass.

- [ ] **Step 7: Commit the HTTP client and Project commands**

Run:

```sh
but diff
but commit -b api-cli-agent-skill -m "Add Req-backed Project CLI commands"
```

---

### Task 6: List and Task CLI Commands

**Files:**

- Create: `lib/taskman/cli/commands/lists.ex`
- Create: `lib/taskman/cli/commands/tasks.ex`
- Modify: `lib/taskman/cli/runner.ex`
- Modify: `lib/taskman/cli/output.ex`
- Create: `test/taskman/cli/commands/lists_test.exs`
- Create: `test/taskman/cli/commands/tasks_test.exs`

**Interfaces:**

- Consumes Task 5 `Client` and `Output`.
- Produces every List and Task command in the approved registry.

- [ ] **Step 1: Write failing List command tests**

For list/show/create/rename, assert exact methods, paths, and JSON:

```elixir
assert_request(:post, "/api/v1/projects/7/lists", %{
  "list" => %{"name" => "Launch", "parent_list_id" => 11}
})
```

Assert readable List output includes ID, name, parent ID, and slash-joined full path; JSON output
preserves the API envelope.

- [ ] **Step 2: Write failing Task command tests**

Cover all commands and option mappings:

```elixir
assert_request(
  :get,
  "/api/v1/projects/7/tasks",
  query: %{"list_id" => 11, "include_descendants" => true}
)

assert_request(:patch, "/api/v1/projects/7/tasks/42", %{
  "task" => %{
    "status" => "in_review",
    "priority" => "urgent",
    "due_at" => nil
  }
})

assert_request(:post, "/api/v1/projects/7/tasks/42/move", %{
  "destination" => %{"list_id" => nil}
})
```

Assert `--description ""` remains an explicit empty string, absent options are omitted, and
`--clear-due-at` alone sends `due_at: nil`.

- [ ] **Step 3: Run List/Task command tests red**

Run:

```sh
mix test test/taskman/cli/commands/lists_test.exs \
  test/taskman/cli/commands/tasks_test.exs
```

Expected: missing command modules and dispatch.

- [ ] **Step 4: Implement List handlers**

Map:

```elixir
:list -> {:get, "/api/v1/projects/#{project_id}/lists"}
:show -> {:get, "/api/v1/projects/#{project_id}/lists/#{list_id}"}
:create -> {:post, "/api/v1/projects/#{project_id}/lists"}
:rename -> {:patch, "/api/v1/projects/#{project_id}/lists/#{list_id}"}
```

Create includes `"parent_list_id"` only when `--parent` is supplied; the API defaults absence to
root.

- [ ] **Step 5: Implement Task handlers**

Create/update bodies use a whitelist map built from present options:

```elixir
@task_fields ~w(title description status priority due_at)a

task =
  Enum.reduce(@task_fields, %{}, fn field, body ->
    if Map.has_key?(invocation.options, field) do
      Map.put(body, Atom.to_string(field), Map.fetch!(invocation.options, field))
    else
      body
    end
  end)
```

Replace `due_at` with `nil` when `clear_due_at` is true. Add `list_id` only for create. Move maps
`to_project_root: true` to `nil` and `to_list: id` to that positive ID.

- [ ] **Step 6: Extend readable rendering**

List rows:

```text
ID    NAME    PARENT    PATH
```

Task rows:

```text
ID    TITLE    STATUS    PRIORITY    LOCATION    DUE
```

Use `Project` for a root Task and join List paths with ` / `. Use `—` for nil parent/due values.
Member output includes every public response field.

- [ ] **Step 7: Run all CLI command tests**

Run:

```sh
mix test test/taskman/cli
```

Expected: parser, help, transport, output, Project, List, and Task tests pass together.

- [ ] **Step 8: Commit List and Task CLI parity**

Run:

```sh
but diff
but commit -b api-cli-agent-skill -m "Add List and Task CLI commands"
```

---

### Task 7: Bash/Fish Completions and Agent Onboarding

**Files:**

- Create: `lib/taskman/cli/completions.ex`
- Create: `lib/taskman/cli/onboarding.ex`
- Modify: `lib/taskman/cli/runner.ex`
- Modify: `.github/workflows/elixir.yml`
- Create: `test/taskman/cli/completions_test.exs`
- Create: `test/taskman/cli/onboarding_test.exs`

**Interfaces:**

- Produces:
  - `Completions.bash/0 :: String.t()`
  - `Completions.fish/0 :: String.t()`
  - `Onboarding.text/0 :: String.t()`.
- Consumes only `Registry`; neither module accepts an HTTP client.

- [ ] **Step 1: Write failing completion coverage tests**

For every command, option, and fixed value:

```elixir
for command <- Registry.commands() do
  path = Enum.join(command.path, " ")
  assert Completions.bash() =~ path
  assert Completions.fish() =~ path
end

for value <- ~w(icebox pending in_progress in_review done will_not_do) do
  assert Completions.bash() =~ value
  assert Completions.fish() =~ value
end
```

Assert output does not contain the API URL and generation succeeds with no `req_options`.
Write each generated script to a per-test path under `tmp_dir` and run:

```elixir
assert {_output, 0} = System.cmd("bash", ["-n", bash_path], stderr_to_stdout: true)
assert {_output, 0} = System.cmd("fish", ["-n", fish_path], stderr_to_stdout: true)
```

- [ ] **Step 2: Write failing onboarding tests**

Assert the offline text contains:

- Taskman purpose, managed data, and current operations;
- running-backend requirement and `mix phx.server`;
- `mix do escript.build + escript.install --force`;
- `~/.mix/escripts/taskman` and `PATH`;
- default `http://localhost:4000`, `TASKMAN_API_URL`, and `--api-url`;
- representative Project/List/Task commands with `--json`;
- both exact completion installation command pairs;
- `taskman agent skill install`;
- `taskman --help`.

Also assert JSON mode decodes as `%{"data" => %{"onboarding" => text}}`.

- [ ] **Step 3: Run completion/onboarding tests red**

Run:

```sh
mix test test/taskman/cli/completions_test.exs test/taskman/cli/onboarding_test.exs
```

Expected: missing modules/dispatch.

- [ ] **Step 4: Generate Bash and Fish from the registry**

Bash registers `_taskman`; Fish emits `complete -c taskman` entries. Both branch on current command
words and offer only the registry's valid child paths/options/values. Do not execute `taskman`,
`curl`, or any backend lookup inside generated shell code.

Reject `--json` for completion commands in `Runner`:

```elixir
%Invocation{command: %{path: ["completions", _shell]}, globals: %{json: true}} ->
  Result.error(2, "invalid_invocation", "--json cannot be used with shell completions")
```

- [ ] **Step 5: Implement the exact onboarding content**

Keep the content as a module attribute so human and JSON modes share one string. It includes:

```text
mix do escript.build + escript.install --force
~/.mix/escripts/taskman
export PATH="$HOME/.mix/escripts:$PATH"
mix phx.server
taskman agent skill install
```

Include the Bash and Fish redirection examples verbatim from the specification.

- [ ] **Step 6: Add shell parsers to CI**

Change the Alpine package step to:

```yaml
- name: Install runtime packages
  run: apk add --no-cache bash fish git tar
```

- [ ] **Step 7: Run completion/onboarding tests and manual smoke**

Run:

```sh
mix test test/taskman/cli/completions_test.exs test/taskman/cli/onboarding_test.exs
mix escript.build
./taskman completions bash | bash -n
./taskman completions fish | fish -n
./taskman agent onboarding
```

Expected: tests pass, both parsers exit 0, and onboarding works with no backend.

- [ ] **Step 8: Commit completions and onboarding**

Run:

```sh
but diff
but commit -b api-cli-agent-skill -m "Add CLI completions and onboarding"
```

---

### Task 8: Bundled Agent Skill and Safe Installer

**Files:**

- Create: `priv/taskman_cli_skill/SKILL.md`
- Create: `lib/taskman/cli/skill/bundle.ex`
- Create: `lib/taskman/cli/skill/file_system.ex`
- Create: `lib/taskman/cli/skill/local_file_system.ex`
- Create: `lib/taskman/cli/skill/installer.ex`
- Modify: `lib/taskman/cli/runner.ex`
- Create: `test/support/fake_skill_file_system.ex`
- Create: `test/taskman/cli/skill/bundle_test.exs`
- Create: `test/taskman/cli/skill/installer_test.exs`
- Create: `test/taskman/cli/skill/skill_contract_test.exs`

**Interfaces:**

- Before editing `SKILL.md`, the implementer reads and follows the available `skill-creator` skill.
- Produces:
  - `Bundle.files/0 :: %{required(String.t()) => String.t()}`
  - `Bundle.cli_version/0 :: String.t()`
  - `Installer.install(keyword()) ::
    {:ok, %{action: :installed | :updated | :current, path: String.t(), skill: "taskman-cli",
    cli_version: String.t()}} | {:error, :skill_install_failed, String.t()}`.
- Options: `skills_root`, `force`, and `file_system`.

- [ ] **Step 1: Write the bundled-skill contract test**

Assert valid frontmatter and required operational sections:

```elixir
assert skill =~ "name: taskman-cli"
assert skill =~ "taskman --help"
assert skill =~ "taskman agent onboarding"
assert skill =~ "--json"
assert skill =~ "TASKMAN_API_URL"
assert skill =~ "connection_failed"
assert skill =~ "Do not treat agent work as automatic Task completion"
```

For every registry command path, require either a direct mention in the skill or an explicit
instruction to consult the corresponding group help. Validate all examples by passing their argv
through `Parser.parse/2`.

- [ ] **Step 2: Write installer tests**

Use an ExUnit `tmp_dir` as `skills_root`. Cover:

```elixir
assert {:ok, %{action: :installed, path: target}} =
         Installer.install(skills_root: root)

assert File.regular?(Path.join(target, "SKILL.md"))
assert File.regular?(Path.join(target, ".taskman-managed.json"))
assert Path.wildcard(Path.join(root, ".taskman-cli.*")) == []
```

Then cover:

- second identical install → `:current`;
- older recognized marker → `:updated`;
- unrecognized existing target → exit-6 error and unchanged contents;
- `force: true` replaces unrecognized target;
- injected failure on the stage-to-target rename restores the previous complete target;
- success leaves only `taskman-cli/`, with no staging or backup siblings.

- [ ] **Step 3: Run skill tests red**

Run:

```sh
mix test test/taskman/cli/skill
```

Expected: missing bundle/installer modules and skill source.

- [ ] **Step 4: Author the `taskman-cli` skill**

Keep the skill concise and operational. It must state:

- use Taskman as the system of record for Projects, Lists, Tasks, later relationships, and Agent
  Sessions;
- first run `taskman --help` or `taskman agent onboarding`;
- ordinary commands require the backend;
- prefer `--json` for automation and parse stdout only on status 0;
- distinguish statuses 2–6 and read stderr on failure;
- use exact ID operands and never guess by name;
- inspect before mutating and obtain explicit authority for consequential future deletion;
- launching or completing agent work never marks a Task complete automatically.

- [ ] **Step 5: Implement compile-time bundling and filesystem behavior**

Bundle the source without relying on an external runtime path:

```elixir
@skill_path Path.expand("../../../../../priv/taskman_cli_skill/SKILL.md", __DIR__)
@external_resource @skill_path
@skill File.read!(@skill_path)

def files, do: %{"SKILL.md" => @skill}
def cli_version, do: Taskman.CLI.version()
```

Define the filesystem behavior with `mkdir_p/1`, `write/2`, `read/1`, `exists?/1`, `rename/2`, and
`rm_rf/1`. Production delegates to `File`.

- [ ] **Step 6: Implement safe install/update/rollback**

Target:

```elixir
Path.join(skills_root, "taskman-cli")
```

Marker:

```elixir
%{
  "installer" => "taskman",
  "skill" => "taskman-cli",
  "cli_version" => Bundle.cli_version()
}
```

Use unique sibling names based on `System.unique_integer([:positive, :monotonic])`. Always remove a
staging directory after a pre-swap error. On update:

1. rename target to backup;
2. rename stage to target;
3. if step 2 fails, rename backup back to target;
4. after success, remove backup;
5. return success only after no stage/backup siblings remain.

- [ ] **Step 7: Wire human/JSON output and exit status 6**

Human success:

```text
Installed taskman-cli at /home/user/.agents/skills/taskman-cli
```

JSON success:

```json
{"data":{"action":"installed","path":"/home/user/.agents/skills/taskman-cli","skill":"taskman-cli","cli_version":"0.1.0"}}
```

Failures use `skill_install_failed` on stderr and status 6.

- [ ] **Step 8: Run skill and complete CLI tests**

Run:

```sh
mix test test/taskman/cli/skill test/taskman/cli
```

Expected: skill validation, example parsing, installer safety, and all CLI behavior pass.

- [ ] **Step 9: Commit the bundled skill**

Run:

```sh
but diff
but commit -b api-cli-agent-skill -m "Bundle and safely install taskman CLI skill"
```

---

### Task 9: Real HTTP Smoke, Documentation, and Delivery Gate

**Files:**

- Create: `test/taskman/cli/end_to_end_test.exs`
- Modify: `README.md`
- Modify: `docs/development.md`
- Modify: `docs/planning/roadmap.md`
- Modify: `docs/handoffs/api-cli-agent-skill.md`
- Modify: `.beads/issues.jsonl` only through `br` commands

**Interfaces:**

- Consumes the complete API, CLI, completion, onboarding, and skill installer.
- Produces the final real-network evidence and durable delivery state.

- [ ] **Step 1: Write the real CLI-to-Phoenix smoke test**

Use `Taskman.DataCase, async: false`, put the sandbox in shared mode, and start a real ephemeral
Bandit listener:

```elixir
server =
  start_supervised!(
    {Bandit,
     plug: TaskmanWeb.Endpoint,
     scheme: :http,
     port: 0,
     ip: {127, 0, 0, 1},
     startup_log: false}
  )

assert {:ok, {{127, 0, 0, 1}, port}} = ThousandIsland.listener_info(server)
api_url = "http://127.0.0.1:#{port}"
```

Run one mutation and one read through the actual Req adapter:

```elixir
create =
  Taskman.CLI.run([
    "projects",
    "create",
    "--name",
    "HTTP smoke",
    "--directory",
    File.cwd!(),
    "--api-url",
    api_url,
    "--json"
  ])

assert create.status == 0
assert %{"data" => %{"id" => id}} = Jason.decode!(create.stdout)

show =
  Taskman.CLI.run([
    "projects",
    "show",
    Integer.to_string(id),
    "--api-url",
    api_url,
    "--json"
  ])

assert show.status == 0
assert %{"data" => %{"name" => "HTTP smoke"}} = Jason.decode!(show.stdout)
```

- [ ] **Step 2: Run the real HTTP smoke test**

Run:

```sh
mix test test/taskman/cli/end_to_end_test.exs
```

Expected: real loopback mutation/read passes with no Req test adapter.

- [ ] **Step 3: Update durable user and development documentation**

README documents:

```sh
mix do escript.build + escript.install --force
~/.mix/escripts/taskman agent onboarding
```

It states that `~/.mix/escripts` belongs on `PATH` for bare `taskman` invocation and points users to
`taskman --help`.

Add one development-guide rule: meaningful UI operations must ship with API, CLI, help,
Bash/Fish completion, skill, and focused verification parity unless the slice specification
records an explicit exception.

- [ ] **Step 4: Run focused scope and terminology checks**

Run:

```sh
rg -n "slice [0-9]|bead|ticket|milestone|phase" \
  lib test README.md --glob '*.ex' --glob '*.exs' --glob '*.md'
rg -n "HTTPoison|Tesla|:httpc|Taskman\\.Repo" lib/taskman/cli lib/taskman_web/controllers/api
```

Expected: no planning terminology in implementation-facing surfaces; no forbidden HTTP client; no
Repo access in CLI/controllers. Any legitimate domain prose match is inspected directly rather
than blindly changed.

- [ ] **Step 5: Run all focused tests**

Run:

```sh
mix test test/taskman_web/controllers/api test/taskman/cli
```

Expected: every API and CLI test passes.

- [ ] **Step 6: Build and manually inspect installed artifacts**

Use temporary homes so the real user paths remain untouched:

```sh
mix escript.build
./taskman --help
./taskman tasks update --help
./taskman completions bash | bash -n
./taskman completions fish | fish -n
./taskman agent onboarding
```

For skill installation, run the installer test rather than overriding `HOME` in production code:

```sh
mix test test/taskman/cli/skill/installer_test.exs
```

Expected: all commands exit 0, help is complete, completion syntax parses, onboarding contains
installation/PATH/backend guidance, and installer tests leave only the target directory.

- [ ] **Step 7: Run the complete repository gate**

Run:

```sh
mix precommit
```

Expected: compile with warnings as errors, unused dependency check, formatting, and the complete
test suite pass.

- [ ] **Step 8: Obtain independent implementation verification**

Dispatch a verifier distinct from all implementers. It reads the specification and plan, inspects
the diff directly, reruns the focused API/CLI tests and `mix precommit`, checks every acceptance
criterion, and records remaining uncertainty. Fix any verified defect through the responsible
implementation task before repeating the affected checks.

- [ ] **Step 9: Reconcile durable delivery state**

Only after all verification passes:

- mark Slice 4 complete in `docs/planning/roadmap.md`;
- update the roadmap status and next slice to Task relationships;
- add bounded verification evidence to the active Beads issues;
- close child issues and then the feature through `br close`;
- retire `docs/handoffs/api-cli-agent-skill.md` and remove its index entry because no work remains.

- [ ] **Step 10: Commit final verification and documentation**

Run:

```sh
but diff
but commit -b api-cli-agent-skill -m "Verify API CLI and agent skill delivery"
```

Expected: final commit contains the smoke test and durable documentation/tracker reconciliation;
no active handoff remains for completed work.
