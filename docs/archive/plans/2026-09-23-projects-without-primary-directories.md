# Projects Without Primary Directories Implementation Plan

Historical implementation plan. The workstream was locally and independently verified, then
accepted by the operator on 2026-09-23. This plan preserves execution provenance, not remaining
work. Current behavior belongs to the
[accepted design](../../specs/2026-09-10-projects-without-primary-directories-design.md),
[product specification](../../product/mvp-spec.md), and implemented code.

**Goal:** Make Projects machine-independent by removing their stored directory from persistence, browser, API, CLI, bundled skill, and current product guidance.

**Architecture:** Keep `Taskman.Projects` as the creation boundary and `Taskman.Projects.Project` as the Ecto schema. A generated forward migration removes the database column; `down/0` refuses a misleading rollback. `TaskmanWeb.ProjectLive.Workspace`, the existing navigation component, API representation, and CLI registry retain their current ownership while adopting the name-only contract.

**Tech stack:** Elixir, Ecto/PostgreSQL, Phoenix LiveView, Req-backed Taskman CLI, ExUnit.

**Approved specification:** [Projects without primary directories](../../specs/2026-09-10-projects-without-primary-directories-design.md). Read it in full before implementing this plan.

## Global constraints

- Project data keeps ID, name, and timestamps. Do not introduce any replacement checkout, machine, or agent-provider model.
- API creation accepts `{"project":{"name":"Taskman"}}`; success is `201`, while a missing or blank name keeps `422 validation_failed`. Extra request keys remain ignored by the changeset.
- CLI creation is `taskman projects create --name NAME`; readable and JSON output use the new Project representation.
- Generate the migration with `mix ecto.gen.migration drop_primary_directory_from_projects`. Keep the historical migration untouched. `down/0` raises `Ecto.MigrationError` with the exact message in the specification.
- Keep tests about current supported behavior. A success test may assert no error; do not add assertions about the absence of removed features or fields. See [development guidance](../../guides/development.md#verification-expectations).
- Read `mix help TASK` before using a Mix task with unfamiliar options. Use `br` for Beads mutations and GitButler for version-control writes. Commit each verified bounded task locally; pushing, merge, and deployment remain separate approvals.

## File ownership map

| Responsibility | Existing owner and planned changes |
| --- | --- |
| Project domain and migration | `lib/taskman/projects/project.ex`, `lib/taskman/projects.ex`, `lib/taskman/change_notifications.ex`, generated `priv/repo/migrations/*_drop_primary_directory_from_projects.exs` |
| Browser creation and navigation | `lib/taskman_web/live/project_live/workspace.ex`, `lib/taskman_web/live/project_live.html.heex`, `lib/taskman_web/components/workspace_navigation.ex` |
| API | `lib/taskman_web/controllers/api/representation.ex`; keep `project_controller.ex` transport behavior |
| CLI and bundled skill | `lib/taskman/cli/registry.ex`, `commands/projects.ex`, `client.ex`, `presentation/output.ex`, `onboarding.ex`, `priv/taskman_cli_skill/SKILL.md` |
| Test data | `test/support/fixtures/projects_fixtures.ex`, `priv/repo/seeds.exs`, and the existing direct Project call sites listed in Task 1 |
| Current documentation | `docs/product/{mvp-spec,domain,agent-sessions}.md`, `docs/planning/roadmap.md`, `README.md`, `docs/README.md`, and status notes in the two older specifications |

## Review focus

These inputs or failure boundaries deserve explicit coverage in the owning tasks:

1. Whitespace-only Project names return a name validation error after trimming (Task 1; Task 3 at the API boundary).
2. Existing rows with arbitrary historical directory values retain their ID and name through the forward migration (Task 6 isolated migration drill).
3. An API request with an unrelated extra key still creates a Project from its name (Task 3).
4. The CLI accepts the new two-field Project response and rejects a malformed response (Task 4).
5. A failed notification publication does not turn a persisted Project creation into a failed command (Task 1).

## Beads and dependencies

| Plan task | Beads issue | Depends on |
| --- | --- | --- |
| 1. Domain and migration | `tas-project-domain-migration-prq8` | none |
| 2. Browser | `tas-project-browser-navigation-yolc` | Task 1 |
| 3. API | `tas-project-api-representation-mtjg` | Task 1 |
| 4. CLI and skill | `tas-project-cli-skill-pxjb` | Task 3 |
| 5. Product docs | `tas-project-product-docs-2v0h` | Task 1 |
| 6. Integrated verification | `tas-project-integration-verification-jcr7` | Tasks 2, 4, and 5; Task 3 transitively through Task 4 |

Tasks 2, 3, and 5 have separate file ownership after Task 1. Coordinate shared working-tree and test-database use if they are delegated concurrently.

## Task 1: Project persistence, notification, and shared data

**Files:**

- Modify: `lib/taskman/projects/project.ex`, `lib/taskman/projects.ex`, `lib/taskman/change_notifications.ex`
- Create with Mix: `priv/repo/migrations/*_drop_primary_directory_from_projects.exs`
- Modify: `test/taskman/projects/actions_test.exs`, `test/taskman/change_notifications_test.exs`, `test/support/fixtures/projects_fixtures.ex`, `priv/repo/seeds.exs`, `test/taskman/repo/seeds_test.exs`, `test/taskman/repo/compatibility_test.exs`, `test/taskman/accounts/user/account_deletion_test.exs`

**Interfaces:** `Projects.create_project/1` and `Projects.change_project/2` take name-only maps; successful creation returns `{:ok, %Project{}}` and publishes a Project-created event with `fields: [:name]`. `project_fixture/1` still accepts overrides, now defaulting only `name`.

- [ ] **Step 1: Write red domain tests.** Replace the old directory-normalization cases with a trimmed name-only success and blank/whitespace name errors. Keep the existing publication-failure test, using name-only input. Assert the created event fields are `[:name]`.

  ```elixir
  assert {:ok, project} = Projects.create_project(%{name: "  Taskman  "})
  assert project.name == "Taskman"
  assert {:error, changeset} = Projects.create_project(%{name: "   "})
  assert %{name: [_]} = errors_on(changeset)
  ```

- [ ] **Step 2: Confirm the red test fails for the intended reason.** Run `mix test test/taskman/projects/actions_test.exs`; the name-only success must fail because the current schema requires a directory. Do not add an assertion about the old field's absence.

- [ ] **Step 3: Generate and implement the migration and domain change.** Read `mix help ecto.gen.migration`, run the specified generator, and edit only the generated file. Use the exact rollback message from the approved specification.

  ```elixir
  def up do
    alter table(:projects) do
      remove :primary_directory
    end
  end

  def down do
    raise Ecto.MigrationError,
          "Cannot roll back Project directory removal; restore the matching pre-migration database backup with the prior application release."
  end
  ```

  In `Project`, cast and require `[:name]` and keep name trimming. Remove the directory normalization/validation functions from `Projects`; publish `[:name]` on creation and remove `:primary_directory` from notification field ordering. Keep `Repo.insert/1` result behavior when publication fails.

- [ ] **Step 4: Update shared callers and run focused tests.** Make the fixture default `%{name: "Project #{unique}"}` and seed Projects with names. Convert the direct Project creations in the listed seed, compatibility, account-deletion, and notification tests to name-only input. Run:

  ```text
  mix test test/taskman/projects/actions_test.exs test/taskman/change_notifications_test.exs test/taskman/repo/seeds_test.exs test/taskman/repo/compatibility_test.exs test/taskman/accounts/user/account_deletion_test.exs
  ```

  Expect all selected tests to pass after migration. Review the generated migration file and confirm the historical `20260719082112_create_projects.exs` was not edited.

- [ ] **Step 5: Commit the verified domain slice.** Use `but diff` to review the selected files and `but commit -b projects-without-primary-directories -m "Remove Project directory from domain and persistence" <file IDs>` with IDs copied from the fresh diff.

## Task 2: Browser Project creation and navigation

**Files:**

- Modify: `lib/taskman_web/live/project_live.html.heex`, `lib/taskman_web/components/workspace_navigation.ex`
- Review: `lib/taskman_web/live/project_live/workspace.ex` (its existing `validate_project` and `save_project` events already pass form maps to `Projects`)
- Modify: `test/taskman_web/live/project_live/project_live_test.exs`, `test/taskman_web/components/workspace_navigation_test.exs`, `test/taskman_web/live/project_live/lists_test.exs`, `external_updates_test.exs`, `workspace_updates_test.exs`, `test/taskman_web/authenticated_hosted_access_test.exs`

**Interfaces:** The form submits `%{"project" => %{"name" => name}}` to `Workspace.handle_event/3`, which keeps its existing patch to `/projects/:id`. Project tree rows retain `#select-project-ID` and `#add-list-project-ID` controls.

- [ ] **Step 1: Write browser contract tests.** In the LiveView creation test, submit `%{name: "Taskman"}` and assert the Project name, selected URL, and existing Task panel. Add a blank-name form test that asserts the name error. In the navigation component test, assert the Project selection link and Add List control are present; remove tests dedicated to the retired directory tooltip/hook.

  ```elixir
  view |> form("#project-form", project: %{name: "Taskman"}) |> render_submit()
  assert [project] = Taskman.Projects.list_projects()
  assert_patch(view, ~p"/projects/#{project.id}")
  assert has_element?(view, "#add-list-project-#{project.id}")
  ```

- [ ] **Step 2: Run the browser baseline.** Run `mix test test/taskman_web/live/project_live/project_live_test.exs test/taskman_web/components/workspace_navigation_test.exs`. Name-only submission may already pass after Task 1 because the form handler delegates to `Projects`. Do not invent an absence assertion to force a red test; record the baseline and inspect the markup change directly in Step 3.

- [ ] **Step 3: Update templates and navigation.** Keep the name input and Create Project button in `project_live.html.heex`. In `workspace_navigation.ex`, keep the selection link and list actions but remove its directory-dependent `phx-hook`, `aria-describedby`, tooltip markup, and colocated `ProjectDirectoryPopover` script. Keep List navigation and Task behavior intact. Change `Workspace` only if focused tests expose an actual gap.

  ```heex
  <.input
    field={@workspace.project_form[:name]}
    type="text"
    label="Name"
    autocomplete="off"
    class="w-full rounded-xl border border-slate-700 bg-slate-900 px-3 py-2.5 text-sm text-white outline-none transition placeholder:text-slate-500 focus:border-indigo-400 focus:ring-4 focus:ring-indigo-400/10"
    error_class="border-rose-400 focus:border-rose-400 focus:ring-rose-400/10"
  />
  ```

  The Project selection link retains `id={selection_link_id(node)}`, `patch={selection_path(node, @include_children?)}`, and `aria-label={"Select #{node_label(node)}"}`; its directory-specific attributes and nested tooltip are deleted.

- [ ] **Step 4: Update neighboring LiveView tests and verify.** Change direct Project form submissions and external Project creations in the listed tests to name-only inputs. Run the six listed browser test files with `mix test`; expect all to pass. Inspect the resulting navigation markup for the Project name, selection link, and Add List action.

- [ ] **Step 5: Commit the verified browser slice.** Review `but diff`, then commit the relevant file IDs with message `Make Project creation and navigation name-only`.

## Task 3: Project API representation

**Files:**

- Modify: `lib/taskman_web/controllers/api/representation.ex`, `test/taskman_web/controllers/api/project_controller_test.exs`
- Review: `lib/taskman_web/controllers/api/project_controller.ex`, `fallback_controller.ex`

**Interfaces:** `Representation.project/1` returns `%{id: project.id, name: project.name}`. The controller retains list/show envelopes, `201` creation, `422 validation_failed` for bad names, and permissive changeset handling of extra keys.

- [ ] **Step 1: Write API contract tests.** Assert the list, show, and create responses contain the documented ID and name values. Send name-only, missing-name, and whitespace-name requests. Send a request with an unrelated extra key and assert successful creation from the supplied name. Avoid asserting that the retired key is missing.

  ```elixir
  conn = post(conn, "/api/v1/projects", %{"project" => %{"name" => "CLI"}})
  assert %{"data" => %{"id" => id, "name" => "CLI"}} = json_response(conn, 201)
  assert is_integer(id)
  ```

- [ ] **Step 2: Run the API baseline.** Run `mix test test/taskman_web/controllers/api/project_controller_test.exs`. Name-only creation may already pass after Task 1. Do not assert the old field's absence merely to force a red test; inspect the exact representation implementation in Step 3.

- [ ] **Step 3: Change the representation and preserve controller semantics.** Return only `id` and `name` from `Representation.project/1`. The controller should require no new parsing rule; do not add strict unknown-key rejection.

  ```elixir
  def project(project), do: %{id: project.id, name: project.name}
  ```

- [ ] **Step 4: Run the focused API test and commit.** Expect the Project controller tests to pass, review `but diff`, and commit the relevant file IDs with message `Expose name-only Project API responses`.

## Task 4: CLI parity and bundled skill

**Files:**

- Modify: `lib/taskman/cli/registry.ex`, `commands/projects.ex`, `client.ex`, `presentation/output.ex`, `onboarding.ex`, `priv/taskman_cli_skill/SKILL.md`
- Modify: `test/taskman/cli/commands/projects_test.exs`, `client_test.exs`, `presentation/output_test.exs`, `presentation/help_test.exs`, `presentation/completions_test.exs`, `end_to_end_test.exs`, `onboarding_test.exs`, `skill/bundle_test.exs`
- Review: `lib/taskman/cli/execution/parser.ex`, `presentation/completions.ex` (both derive behavior from the registry)

**Interfaces:** Registry `projects create` requires `--name NAME`; the command sends `%{"project" => %{"name" => name}}`; `Client` accepts Project objects with integer ID and binary name; readable output shows `ID` and `NAME`, and JSON mirrors the API data envelope.

- [ ] **Step 1: Write red CLI tests.** Use Req.Test to run `projects create --name CLI --json` and assert the exact request body and response envelope. Assert readable list/detail output for ID and name. Add help and Bash/Fish completion assertions for the supported `--name` option; retain malformed-response tests with a truly malformed member such as `%{}`.

  ```elixir
  assert conn |> Req.Test.raw_body() |> Jason.decode!() ==
           %{"project" => %{"name" => "CLI"}}
  assert result.stdout == "ID\tNAME\n7\tCLI\n"
  ```

- [ ] **Step 2: Confirm the red CLI test fails.** Run `mix test test/taskman/cli/commands/projects_test.exs`; the registry should reject the name-only invocation before implementation.

- [ ] **Step 3: Update CLI contracts.** Delete `:directory` from the Project create registry options and examples; change the command body to name-only. In `Client`, require and validate `id` and `name`; in `Output`, use two Project fields and two-column rows. Change onboarding and bundled-skill examples and the skill's Project record guidance to use returned ID and name. Let parser, help, and completion generators consume the revised registry.

  ```elixir
  usage: "taskman projects create --name NAME",
  options: [option(:name, "--name", :string, "NAME", "Project name.", required?: true)],
  examples: ["taskman projects create --name CLI"]

  body = %{"project" => %{"name" => Map.fetch!(invocation.options, :name)}}

  required_keys?(project, ~w(id name)) and
    positive_integer?(project["id"]) and is_binary(project["name"])

  @project_fields [{:id, "ID"}, {:name, "NAME"}]
  ```

- [ ] **Step 4: Run focused CLI and skill tests.** Run `mix test` with the eight listed test files. Inspect the local CLI help and generate Bash and Fish completions through `Taskman.CLI.run/1`; confirm their name-option coverage with the tests. Expect readable and JSON output tests to pass.

  ```text
  MIX_ENV=test mix run -e 'IO.write(Taskman.CLI.run(["projects", "create", "--help"]).stdout)'
  MIX_ENV=test mix run -e 'IO.write(Taskman.CLI.run(["completions", "bash"]).stdout)'
  MIX_ENV=test mix run -e 'IO.write(Taskman.CLI.run(["completions", "fish"]).stdout)'
  ```

- [ ] **Step 5: Commit the verified CLI slice.** Review `but diff`, then commit the relevant file IDs with message `Update Project CLI and skill for name-only creation`.

## Task 5: Canonical product and planning documentation

**Files:**

- Modify: `docs/product/mvp-spec.md`, `docs/product/domain.md`, `docs/product/agent-sessions.md`, `docs/planning/roadmap.md`, `docs/README.md`, `README.md`
- Modify status notes only: `docs/specs/2026-07-18-projects-basic-tasks-first-slice-design.md`, `docs/specs/2026-08-29-api-cli-agent-skill-design.md`
- Review: `docs/guides/deployment.md`; update only if its backup/rollback instructions fail to make this migration's limit clear

**Interfaces:** Current product documents define a Project by ID/name and its Lists/Tasks. Agent Sessions and provider choice are deferred to a separate accepted design; historical specifications remain available with explicit supersession notes.

- [ ] **Step 1: Update canonical product copy.** In `mvp-spec.md`, replace the local-directory core journey and Auggie launch promises with Project/Task work and an explicit deferred Agent Session integration statement. In `domain.md`, define Project without a directory and mark Agent Session/provider rules as deferred. Mark `agent-sessions.md` superseded at its top, linking the approved specification, while retaining its historical text for provenance.

  ```markdown
  **Status:** Superseded. Agent Session implementation is deferred to a separate accepted design; the current Project model has no directory. See [Projects without primary directories](../specs/2026-09-10-projects-without-primary-directories-design.md).
  ```

- [ ] **Step 2: Update roadmap and entry points.** Replace current Project directory and Auggie launch requirements in `roadmap.md` with the name-only Project outcome and a future agent-integration design gate. Refresh root `README.md` and `docs/README.md` links/copy so they do not present the old model as current. Add short supersession status notes to the two older specifications; leave archived plans and research untouched.

- [ ] **Step 3: Verify documentation authority.** Read the [documentation guide](../../guides/documentation.md) again before editing. Inspect links and whitespace. Search current product, roadmap, root README, and bundled skill for obsolete current-model language; distinguish historical provenance from current guidance. Review the deployment runbook's backup and rollback text against the forward-only migration contract. Commit the reviewed documentation file IDs with message `Update Project and agent guidance for hosted model`.

## Task 6: Integrated verification and migration drill

**Files:** No new production owner. Update only the tests or documentation that fail a concrete check in Tasks 1–5; keep findings in their canonical owner and Beads evidence.

**Interfaces:** The combined browser, API, CLI, seed, and migration contract must work together. Deployment remains separately authorized.

- [ ] **Step 1: Run the integrated gate.** Run `mix precommit`. Fix failures tied to this change, including remaining direct Project constructors in `test/taskman_web/live/project_live/`, `test/taskman/cli/`, and repository compatibility tests. Search implementation-facing files for stale Project-directory references and leaked planning terms. Existing unrelated historical files may retain provenance.

  ```text
  rg -n 'primary_directory|--directory|ProjectDirectoryPopover' lib assets priv/repo/seeds.exs priv/taskman_cli_skill test
  mix precommit
  ```

- [ ] **Step 2: Exercise the forward migration with a legacy row in an isolated test database.** Read `mix help ecto.create`, `mix help ecto.migrate`, and `mix help ecto.drop` before use. Use `MIX_ENV=test MIX_TEST_PARTITION=_project_directory_transition` for every command, so the ordinary test database is untouched. Create that database, migrate only through `20260904065131`, insert a row with a directory using `Ecto.Adapters.SQL.query!/3` from `mix run -e`, then apply the new migration. Read the row through `Projects.list_projects/0` and create another Project by name. Record the observed IDs and names in the Beads verification issue.

  ```text
  MIX_ENV=test MIX_TEST_PARTITION=_project_directory_transition mix ecto.create
  MIX_ENV=test MIX_TEST_PARTITION=_project_directory_transition mix ecto.migrate --to 20260904065131
  MIX_ENV=test MIX_TEST_PARTITION=_project_directory_transition mix run -e 'Ecto.Adapters.SQL.query!(Taskman.Repo, "INSERT INTO projects (id, name, primary_directory, inserted_at, updated_at) VALUES ($1, $2, $3, NOW(), NOW())", [4242, "Existing", "/legacy/checkout"])'
  MIX_ENV=test MIX_TEST_PARTITION=_project_directory_transition mix ecto.migrate
  MIX_ENV=test MIX_TEST_PARTITION=_project_directory_transition mix run -e 'alias Taskman.Projects; [%{id: 4242, name: "Existing"}] = Projects.list_projects(); {:ok, %{name: "New"}} = Projects.create_project(%{name: "New"})'
  ```

- [ ] **Step 3: Verify the rollback refusal and clean up the isolated database.** Read `mix help ecto.rollback`, then run rollback with `--step 1` against the same isolated test database. Expect a nonzero exit and the exact `Ecto.MigrationError` message from the specification. Drop only the database created for this drill with the same `MIX_TEST_PARTITION` value. Confirm the runbook requires a verified pre-migration backup and prior release for actual rollback; do not deploy.

  ```text
  MIX_ENV=test MIX_TEST_PARTITION=_project_directory_transition mix ecto.rollback --step 1
  MIX_ENV=test MIX_TEST_PARTITION=_project_directory_transition mix ecto.drop
  ```

- [ ] **Step 4: Independent focused verification and evidence.** Have a separate verifier inspect the implemented migration, name-only API/CLI contract, current-behavior tests, and relevant documentation directly; reproduce focused checks where feasible and report concrete risks. Keep the review scoped to this work and its relevant interactions. Check local Markdown links and whitespace; inspect `taskman projects create --help` and Bash/Fish completions; record `mix precommit` result, migration drill, rollback refusal, verifier findings, and unresolved uncertainty in Beads. Review final `but diff` and commit any verified correction with a scoped message. The workstream handoff remains active until the operator separately confirms completion and acknowledges its rulings; publication and merge are separate decisions.

## Plan completion gate

The six Beads issues linked to this plan were closed after implementation and verification.
The operator accepted the completed workstream and its two implementation rulings on 2026-09-23.
Publication and deployment were separate decisions.
