defmodule Taskman.CLI.EndToEndTest do
  use TaskmanWeb.ConnCase, async: false

  @moduletag :tmp_dir

  import Phoenix.LiveViewTest
  import Taskman.AccountsFixtures
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  setup %{tmp_dir: tmp_dir} do
    user = user_fixture()
    now = DateTime.utc_now()
    expires_at = DateTime.add(now, 365 * 86_400, :second)

    assert {:ok, %{plaintext: api_key}} =
             Taskman.Accounts.create_api_key(
               user,
               %{
                 name: "CLI end-to-end tests",
                 expires_at: expires_at
               },
               now: now
             )

    {:ok, api_key: api_key, config_root: Path.join(tmp_dir, "xdg")}
  end

  test "creates and inspects a Project through the loopback API", %{
    api_key: api_key,
    config_root: config_root
  } do
    server =
      start_supervised!(
        {Bandit,
         plug: TaskmanWeb.Endpoint, scheme: :http, port: 0, ip: {127, 0, 0, 1}, startup_log: false}
      )

    assert {:ok, {{127, 0, 0, 1}, port}} = ThousandIsland.listener_info(server)
    api_url = "http://127.0.0.1:#{port}"
    write_config(config_root, api_url, api_key)

    Taskman.CLI.Fixtures.write_hosts!(Path.join(config_root, "missing"), %{
      format: :v2,
      default_host: "personal",
      hosts: %{"personal" => %{api_url: api_url, api_key: nil}}
    })

    missing = cli_run(["projects", "list", "--json"], Path.join(config_root, "missing"))
    assert missing.status == 7
    assert %{"error" => %{"code" => "authentication_required"}} = Jason.decode!(missing.stderr)

    rejected_root = Path.join(config_root, "rejected")

    write_config(
      rejected_root,
      api_url,
      "tm_b1BWqBOKyDOvG9UmpFc40u2BTKuySXY5HGGKYZGrIf23jgINXtEy2AqT94Y6W7pu_bGXTfd"
    )

    rejected = cli_run(["projects", "list", "--json"], rejected_root)
    assert rejected.status == 7
    assert %{"error" => %{"code" => "unauthorized"}} = Jason.decode!(rejected.stderr)

    create =
      cli_run(
        [
          "projects",
          "create",
          "--name",
          "HTTP smoke",
          "--json"
        ],
        config_root
      )

    assert create.status == 0
    assert create.stderr == ""
    assert String.ends_with?(create.stdout, "\n")

    assert %{
             "data" => %{
               "id" => id,
               "name" => "HTTP smoke",
               "description" => "",
               "icon" => "briefcase",
               "color" => "#6366F1"
             }
           } = Jason.decode!(create.stdout)

    show =
      cli_run(
        [
          "projects",
          "show",
          Integer.to_string(id),
          "--json"
        ],
        config_root
      )

    assert show.status == 0
    assert show.stderr == ""
    assert String.ends_with?(show.stdout, "\n")

    assert Jason.decode!(show.stdout) == %{
             "data" => %{
               "id" => id,
               "name" => "HTTP smoke",
               "description" => "",
               "icon" => "briefcase",
               "color" => "#6366F1"
             }
           }

    update =
      cli_run(
        [
          "projects",
          "update",
          Integer.to_string(id),
          "--description",
          "Delivery",
          "--icon",
          "beaker",
          "--color",
          "#aabbcc",
          "--json"
        ],
        config_root
      )

    assert update.status == 0

    assert %{
             "data" => %{
               "id" => ^id,
               "description" => "Delivery",
               "icon" => "beaker",
               "color" => "#AABBCC"
             }
           } =
             Jason.decode!(update.stdout)

    clear =
      cli_run(
        ["projects", "update", Integer.to_string(id), "--description", "", "--json"],
        config_root
      )

    assert clear.status == 0
    assert Jason.decode!(clear.stdout)["data"]["description"] == ""
  end

  test "updates Task parentage and inspects hierarchy through the loopback API",
       %{api_key: api_key, config_root: config_root} do
    server =
      start_supervised!(
        {Bandit,
         plug: TaskmanWeb.Endpoint, scheme: :http, port: 0, ip: {127, 0, 0, 1}, startup_log: false}
      )

    assert {:ok, {{127, 0, 0, 1}, port}} = ThousandIsland.listener_info(server)
    api_url = "http://127.0.0.1:#{port}"
    write_config(config_root, api_url, api_key)

    project =
      cli_run(
        [
          "projects",
          "create",
          "--name",
          "Hierarchy smoke",
          "--json"
        ],
        config_root
      )

    assert %{"data" => %{"id" => project_id}} = Jason.decode!(project.stdout)

    parent =
      cli_run(
        [
          "tasks",
          "create",
          "--project",
          Integer.to_string(project_id),
          "--title",
          "Parent",
          "--json"
        ],
        config_root
      )

    assert %{"data" => %{"id" => parent_id, "parent_task_id" => nil}} =
             Jason.decode!(parent.stdout)

    child =
      cli_run(
        [
          "tasks",
          "create",
          "--project",
          Integer.to_string(project_id),
          "--title",
          "Child",
          "--parent",
          Integer.to_string(parent_id),
          "--json"
        ],
        config_root
      )

    assert %{"data" => %{"id" => child_id, "parent_task_id" => ^parent_id}} =
             Jason.decode!(child.stdout)

    hierarchy =
      cli_run(
        [
          "tasks",
          "hierarchy",
          "--project",
          Integer.to_string(project_id),
          Integer.to_string(child_id),
          "--json"
        ],
        config_root
      )

    assert hierarchy.status == 0

    assert %{
             "data" => %{
               "selected_task_id" => ^child_id,
               "root" => %{
                 "task" => %{"id" => ^parent_id, "parent_task_id" => nil},
                 "children" => [%{"task" => %{"id" => ^child_id, "parent_task_id" => ^parent_id}}]
               }
             }
           } = Jason.decode!(hierarchy.stdout)

    clear_parent =
      cli_run(
        [
          "tasks",
          "update",
          "--project",
          Integer.to_string(project_id),
          Integer.to_string(child_id),
          "--no-parent",
          "--json"
        ],
        config_root
      )

    assert %{"data" => %{"parent_task_id" => nil}} = Jason.decode!(clear_parent.stdout)
  end

  test "CLI writes refresh connected Project views through the loopback API",
       %{conn: conn, api_key: api_key, config_root: config_root} do
    server =
      start_supervised!(
        {Bandit,
         plug: TaskmanWeb.Endpoint, scheme: :http, port: 0, ip: {127, 0, 0, 1}, startup_log: false}
      )

    assert {:ok, {{127, 0, 0, 1}, port}} = ThousandIsland.listener_info(server)
    api_url = "http://127.0.0.1:#{port}"
    write_config(config_root, api_url, api_key)
    project = project_fixture(%{name: "Browser Project"})
    parent = task_fixture(project, %{title: "CLI parent"})
    project_path = ~p"/projects/#{project.id}"

    conn = log_in_user(conn, user_fixture())
    {:ok, project_view, _html} = live(conn, project_path)

    project_create =
      cli_run(
        [
          "projects",
          "create",
          "--name",
          "CLI Project",
          "--json"
        ],
        config_root
      )

    assert project_create.status == 0
    assert project_create.stderr == ""
    assert String.ends_with?(project_create.stdout, "\n")

    assert %{"data" => %{"id" => created_project_id, "name" => "CLI Project"}} =
             Jason.decode!(project_create.stdout)

    sync_view(project_view)
    project_view |> element("#project-selector-toggle") |> render_click()
    assert has_element?(project_view, "#select-project-#{created_project_id}", "CLI Project")
    refute_patched(project_view, project_path)

    list_create =
      cli_run(
        [
          "lists",
          "create",
          "--project",
          Integer.to_string(project.id),
          "--name",
          "CLI List",
          "--json"
        ],
        config_root
      )

    assert list_create.status == 0
    assert list_create.stderr == ""
    assert String.ends_with?(list_create.stdout, "\n")

    assert %{
             "data" => %{
               "id" => list_id,
               "project_id" => project_id,
               "parent_list_id" => nil,
               "name" => "CLI List",
               "path" => ["CLI List"]
             }
           } = Jason.decode!(list_create.stdout)

    assert project_id == project.id

    sync_view(project_view)
    assert has_element?(project_view, "#list-#{list_id}", "CLI List")
    refute_patched(project_view, project_path)

    list_rename =
      cli_run(
        [
          "lists",
          "rename",
          "--project",
          Integer.to_string(project.id),
          Integer.to_string(list_id),
          "--name",
          "CLI Renamed List",
          "--json"
        ],
        config_root
      )

    assert list_rename.status == 0
    assert list_rename.stderr == ""
    assert String.ends_with?(list_rename.stdout, "\n")

    assert %{"data" => %{"id" => ^list_id, "name" => "CLI Renamed List"}} =
             Jason.decode!(list_rename.stdout)

    sync_view(project_view)
    assert has_element?(project_view, "#list-#{list_id}", "CLI Renamed List")
    refute_patched(project_view, project_path)

    list_path = ~p"/projects/#{project.id}/lists/#{list_id}"
    {:ok, list_view, _html} = live(conn, list_path)

    task_create =
      cli_run(
        [
          "tasks",
          "create",
          "--project",
          Integer.to_string(project.id),
          "--list",
          Integer.to_string(list_id),
          "--title",
          "CLI Task",
          "--json"
        ],
        config_root
      )

    assert task_create.status == 0
    assert task_create.stderr == ""
    assert String.ends_with?(task_create.stdout, "\n")

    assert %{
             "data" => %{
               "id" => task_id,
               "project_id" => ^project_id,
               "list_id" => ^list_id,
               "parent_task_id" => nil,
               "title" => "CLI Task",
               "location" => %{"kind" => "list", "list_id" => ^list_id}
             }
           } = Jason.decode!(task_create.stdout)

    sync_view(list_view)
    assert has_element?(list_view, "#task-#{task_id}", "CLI Task")
    refute_patched(list_view, list_path)

    detail_path = ~p"/projects/#{project.id}/lists/#{list_id}/tasks/#{task_id}"
    {:ok, detail, _html} = live(conn, detail_path)

    task_update =
      cli_run(
        [
          "tasks",
          "update",
          "--project",
          Integer.to_string(project.id),
          Integer.to_string(task_id),
          "--title",
          "CLI Updated Task",
          "--json"
        ],
        config_root
      )

    assert task_update.status == 0
    assert task_update.stderr == ""
    assert String.ends_with?(task_update.stdout, "\n")

    assert %{"data" => %{"id" => ^task_id, "title" => "CLI Updated Task"}} =
             Jason.decode!(task_update.stdout)

    sync_view(detail)
    assert has_element?(detail, "#task-modal")
    assert has_element?(detail, "#task-title[value='CLI Updated Task']")
    refute_patched(detail, detail_path)

    parent_change =
      cli_run(
        [
          "tasks",
          "update",
          "--project",
          Integer.to_string(project.id),
          Integer.to_string(task_id),
          "--parent",
          Integer.to_string(parent.id),
          "--json"
        ],
        config_root
      )

    assert parent_change.status == 0
    assert parent_change.stderr == ""
    assert String.ends_with?(parent_change.stdout, "\n")

    assert %{"data" => %{"id" => ^task_id, "parent_task_id" => parent_id}} =
             Jason.decode!(parent_change.stdout)

    assert parent_id == parent.id

    sync_view(detail)
    assert has_element?(detail, "#task-modal")
    assert has_element?(detail, "#task-parent-trigger", "CLI parent")
    assert has_element?(detail, "#task-hierarchy-node-#{parent.id}")
    assert has_element?(detail, "#task-hierarchy-link-#{task_id}[aria-current='true']")
    refute_patched(detail, detail_path)

    task_move =
      cli_run(
        [
          "tasks",
          "move",
          "--project",
          Integer.to_string(project.id),
          Integer.to_string(task_id),
          "--to-project-root",
          "--json"
        ],
        config_root
      )

    assert task_move.status == 0
    assert task_move.stderr == ""
    assert String.ends_with?(task_move.stdout, "\n")

    assert %{
             "data" => %{
               "id" => ^task_id,
               "project_id" => ^project_id,
               "list_id" => nil,
               "parent_task_id" => ^parent_id,
               "location" => %{"kind" => "project", "list_id" => nil, "path" => []}
             }
           } = Jason.decode!(task_move.stdout)

    sync_view(detail)
    assert has_element?(detail, "#task-modal")
    assert has_element?(detail, "#task-title[value='CLI Updated Task']")
    assert has_element?(detail, "#task-hierarchy-node-#{parent.id}")
    refute has_element?(detail, "#task-#{task_id}")
    refute_patched(detail, detail_path)
  end

  test "named hosts bind shared clone context and recover dangling references through loopback",
       %{api_key: api_key, config_root: root, tmp_dir: tmp_dir} do
    alias Taskman.CLI.{Config, Fixtures}

    server =
      start_supervised!(
        {Bandit,
         plug: TaskmanWeb.Endpoint, scheme: :http, port: 0, ip: {127, 0, 0, 1}, startup_log: false},
        id: :context_loopback
      )

    assert {:ok, {{127, 0, 0, 1}, port}} = ThousandIsland.listener_info(server)
    repo = Fixtures.git_repo!(tmp_dir, "bound clone")
    linked = Fixtures.linked_worktree!(repo, Path.join(tmp_dir, "linked tree"))
    opts = [config_root: root, cwd: repo.cwd, env: %{}, terminal: Taskman.FakeTerminal]
    run = fn args -> Taskman.CLI.run(args ++ ["--json"], opts) end

    data = fn args ->
      result = run.(args)
      assert result.status == 0, result.stderr
      assert result.stderr == ""
      assert String.ends_with?(result.stdout, "\n")
      Jason.decode!(result.stdout)["data"]
    end

    for host <- ~w(personal work) do
      assert data.(~w(config hosts add) ++ [host, "--url", "http://127.0.0.1:#{port}"]) == %{
               "saved" => true
             }

      Taskman.FakeTerminal.set_responses([], [api_key])
      assert data.(~w(config hosts set-key) ++ [host]) == %{"saved" => true}
    end

    assert data.(~w(config default-host personal)) == %{"saved" => true}

    assert {:ok, %{default_host: "personal", hosts: %{"personal" => %{api_key: ^api_key}}}} =
             Config.load(opts)

    project = data.(~w(projects create --name Repository))
    other = data.(~w(projects create --name Elsewhere))
    project_id = project["id"]
    other_id = other["id"]

    assert data.(~w(repo bind --host personal --project) ++ [to_string(project_id)]) ==
             %{"bound" => true, "host" => "personal", "project_id" => project_id}

    binding_path = Path.join([repo.common_dir, "taskman", "binding.json"])

    assert Jason.decode!(File.read!(binding_path)) == %{
             "version" => 1,
             "host" => "personal",
             "project_id" => project_id
           }

    task = data.(~w(tasks create --title Context-acceptance))
    task_id = task["id"]
    assert task["project_id"] == project_id
    assert Taskman.Tasks.get_task(task_id).project_id == project_id
    assert data.(~w(tasks show) ++ [to_string(task_id)])["project_id"] == project_id

    # The bound host wins over environment selection; injection labels only the key.
    injected =
      Keyword.merge(opts,
        cwd: linked.cwd,
        env: %{
          "TASKMAN_HOST" => "work",
          "TASKMAN_API_KEY_HOST" => "personal",
          "TASKMAN_API_KEY" => api_key
        }
      )

    shown = Taskman.CLI.run(~w(config show --json), injected)

    assert Jason.decode!(shown.stdout)["data"]["sources"] == %{
             "host" => "repository",
             "project" => "repository",
             "key" => "environment"
           }

    linked_show = Taskman.CLI.run(["tasks", "show", to_string(task_id), "--json"], injected)
    assert linked_show.status == 0
    assert Jason.decode!(linked_show.stdout)["data"]["id"] == task_id

    assert data.(~w(repo show)) == %{
             "common_git_dir" => repo.common_dir,
             "binding" => %{"host" => "personal", "project_id" => project_id},
             "host_registered" => true
           }

    override =
      data.(
        ~w(tasks create --host work --project) ++
          [to_string(other_id), "--title", "Context-elsewhere"]
      )

    assert override["project_id"] == other_id
    assert Taskman.Tasks.get_task(override["id"]).project_id == other_id
    assert Enum.map(data.(~w(tasks search Context)), & &1["id"]) == [task_id]

    assert Enum.sort(
             Enum.map(data.(~w(tasks search Context --host work --all-projects)), & &1["id"])
           ) == Enum.sort([task_id, override["id"]])

    assert Enum.map(
             data.(~w(tasks search Context --host work --project) ++ [to_string(other_id)]),
             & &1["id"]
           ) == [override["id"]]

    for args <- [
          ~w(tasks list --host personal),
          ["tasks", "list", "--project", to_string(project_id)],
          ["tasks", "search", "Context", "--all-projects", "--project", to_string(project_id)]
        ] do
      result = run.(args)
      assert result.status == 2
      assert result.stdout == ""
      assert Jason.decode!(result.stderr)["error"]["code"] == "invalid_invocation"
    end

    assert Jason.decode!(File.read!(binding_path))["project_id"] == project_id

    assert data.(~w(config hosts remove personal)) == %{"removed" => true}
    assert {:ok, %{default_host: nil}} = Config.load(opts)
    assert data.(~w(repo show))["host_registered"] == false
    refused = run.(~w(tasks list))
    assert refused.status == 2
    assert Jason.decode!(refused.stderr)["error"]["code"] == "invalid_configuration"
    # Complete scope overrides bypass the dangling binding.
    assert Enum.map(
             data.(~w(tasks list --host work --project) ++ [to_string(other_id)]),
             & &1["id"]
           ) == [override["id"]]

    stop_supervised!(:context_loopback)

    offline =
      Keyword.merge(opts,
        env: %{"TASKMAN_API_URL" => "retired", "TASKMAN_API_KEY_HOST" => "orphan"}
      )

    unbound = Taskman.CLI.run(~w(repo unbind --json), offline)
    assert unbound.status == 0
    assert Jason.decode!(unbound.stdout) == %{"data" => %{"unbound" => true}}
    refute File.exists?(binding_path)

    assert data.(~w(repo show)) == %{
             "common_git_dir" => repo.common_dir,
             "binding" => nil,
             "host_registered" => nil
           }
  end

  defp sync_view(view), do: _ = :sys.get_state(view.pid)

  defp cli_run(args, config_root),
    do:
      Taskman.CLI.run(args,
        config_root: config_root,
        env: %{},
        git_runner: &Taskman.CLI.Fixtures.outside_git/3
      )

  defp write_config(config_root, api_url, api_key) do
    Taskman.CLI.Fixtures.write_hosts!(config_root, %{
      format: :v2,
      default_host: "personal",
      hosts: %{"personal" => %{api_url: api_url, api_key: api_key}}
    })
  end
end
