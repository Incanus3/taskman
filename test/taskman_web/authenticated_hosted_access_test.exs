defmodule TaskmanWeb.AuthenticatedHostedAccessTest do
  use TaskmanWeb.ConnCase, async: false

  @moduletag :tmp_dir

  import Phoenix.LiveViewTest
  import Swoosh.TestAssertions
  import Taskman.ListsFixtures
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.Accounts
  alias Taskman.Accounts.User
  alias Taskman.{Lists, Projects, Tasks}

  setup :set_swoosh_global

  test "hosted operation examples keep the release private behind Caddy" do
    root = File.cwd!()
    service_path = Path.join(root, "ops/systemd/taskman.service")
    caddyfile_path = Path.join(root, "ops/caddy/Caddyfile")
    deployment_path = Path.join(root, "docs/deployment.md")

    assert File.exists?(service_path), "expected the versioned-release systemd example"
    assert File.exists?(caddyfile_path), "expected the loopback Caddy example"
    assert File.exists?(deployment_path), "expected the hosted deployment runbook"

    service = File.read!(service_path)
    caddyfile = File.read!(caddyfile_path)
    deployment = File.read!(deployment_path)

    assert service =~ "User=taskman"
    assert service =~ "Group=taskman"
    assert service =~ "EnvironmentFile=/etc/taskman/taskman.env"
    assert service =~ "Environment=RELEASE_TMP=/var/lib/taskman"
    assert service =~ "ExecStartPre=/opt/taskman/current/bin/migrate"
    assert service =~ "ExecStart=/opt/taskman/current/bin/server"
    assert service =~ "KillSignal=SIGTERM"
    assert service =~ "Restart=on-failure"

    assert caddyfile =~ "taskman.example.com {"
    assert caddyfile =~ "reverse_proxy 127.0.0.1:4000"

    assert deployment =~
             "caddy validate --config /etc/caddy/Caddyfile\nsystemctl enable --now caddy.service"

    assert deployment =~
             ~r/On later\s+Caddyfile changes, validate first and then `systemctl reload caddy\.service`\./
  end

  test "an invited user accesses shared work through LiveView and CLI until disabled", %{
    conn: conn,
    tmp_dir: tmp_dir
  } do
    administrator = password_administrator()
    shared_project = project_fixture(%{name: "Shared acceptance Project"})
    shared_list = list_fixture(shared_project, %{name: "Shared acceptance List"})
    shared_task = task_fixture(shared_project, shared_list, %{title: "Shared acceptance Task"})
    email = "hosted-user-#{System.unique_integer([:positive])}@example.com"

    assert {:ok, %User{status: :pending}} = Accounts.invite_user(administrator, %{email: email})
    setup_token = receive_setup_token()

    assert {:ok, %User{status: :active, confirmed_at: %DateTime{}} = user} =
             Accounts.complete_setup(setup_token, %{
               password: "hosted-password",
               password_confirmation: "hosted-password"
             })

    signed_in =
      post(conn, "/auth/user/password/sign_in", %{
        "user" => %{"email" => email, "password" => "hosted-password"}
      })

    assert redirected_to(signed_in) == "/"
    assert is_binary(Plug.Conn.get_session(signed_in, :user_token))

    assert {:ok, workspace, _html} = live(signed_in, "/projects/#{shared_project.id}")
    assert has_element?(workspace, "#authenticated-navigation")
    assert has_element?(workspace, "#project-#{shared_project.id}", shared_project.name)

    assert {:ok, settings, _html} = live(signed_in, "/account/settings")

    settings
    |> form("#api-key-form", %{"api_key" => %{"name" => "Hosted acceptance"}})
    |> render_submit()

    plaintext_key = api_key_plaintext(settings)
    assert String.starts_with?(plaintext_key, "tm_")

    server =
      start_supervised!(
        {Bandit,
         plug: TaskmanWeb.Endpoint, scheme: :http, port: 0, ip: {127, 0, 0, 1}, startup_log: false}
      )

    assert {:ok, {{127, 0, 0, 1}, port}} = ThousandIsland.listener_info(server)
    config_root = Path.join(tmp_dir, "xdg")
    write_config(config_root, "http://127.0.0.1:#{port}", plaintext_key)

    read_shared_project =
      cli_run(["projects", "show", Integer.to_string(shared_project.id), "--json"], config_root)

    assert read_shared_project.status == 0

    assert %{"data" => %{"id" => shared_project_id, "name" => "Shared acceptance Project"}} =
             Jason.decode!(read_shared_project.stdout)

    assert shared_project_id == shared_project.id

    read_shared_task =
      cli_run(
        [
          "tasks",
          "show",
          "--project",
          Integer.to_string(shared_project.id),
          Integer.to_string(shared_task.id),
          "--json"
        ],
        config_root
      )

    assert read_shared_task.status == 0

    assert %{"data" => %{"id" => shared_task_id, "list_id" => shared_list_id}} =
             Jason.decode!(read_shared_task.stdout)

    assert shared_task_id == shared_task.id
    assert shared_list_id == shared_list.id

    created_project =
      cli_run(
        [
          "projects",
          "create",
          "--name",
          "CLI acceptance Project",
          "--directory",
          File.cwd!(),
          "--json"
        ],
        config_root
      )

    assert created_project.status == 0
    assert %{"data" => %{"id" => created_project_id}} = Jason.decode!(created_project.stdout)

    created_task =
      cli_run(
        [
          "tasks",
          "create",
          "--project",
          Integer.to_string(created_project_id),
          "--title",
          "CLI acceptance Task",
          "--json"
        ],
        config_root
      )

    assert created_task.status == 0

    assert %{"data" => %{"project_id" => ^created_project_id}} =
             Jason.decode!(created_task.stdout)

    socket_id = Plug.Conn.get_session(signed_in, :live_socket_id)
    TaskmanWeb.Endpoint.subscribe(socket_id)

    assert {:ok, %User{status: :disabled}} = Accounts.disable_user(administrator, user)
    assert_receive %Phoenix.Socket.Broadcast{topic: ^socket_id, event: "disconnect"}

    assert {:error, {:redirect, %{to: "/sign-in" <> _}}} = live(signed_in, "/")

    disabled_api =
      conn
      |> TaskmanWeb.ConnCase.put_api_key(plaintext_key)
      |> get("/api/v1/projects")

    assert disabled_api.status == 401
    assert %{"error" => %{"code" => "unauthorized"}} = Jason.decode!(disabled_api.resp_body)

    disabled_cli = cli_run(["projects", "list", "--json"], config_root)
    assert disabled_cli.status == 7
    assert %{"error" => %{"code" => "unauthorized"}} = Jason.decode!(disabled_cli.stderr)

    assert %{id: ^shared_project_id} = Projects.get_project(shared_project.id)
    assert %{id: ^shared_list_id} = Lists.get_list_for_project(shared_project, shared_list.id)
    assert %{id: ^shared_task_id} = Tasks.get_task_for_project(shared_project, shared_task.id)
    assert %{id: ^created_project_id} = Projects.get_project(created_project_id)
  end

  defp password_administrator do
    {:ok, administrator} =
      Accounts.bootstrap_admin(
        "hosted-admin-#{System.unique_integer([:positive])}@example.com",
        "administrator-password"
      )

    administrator
  end

  defp receive_setup_token do
    assert_receive {:email, email}

    [_, token] = Regex.run(~r{https://[^\s<]+/setup/([^\s<]+)}, email.text_body)
    URI.decode(token)
  end

  defp api_key_plaintext(view) do
    [plaintext] =
      view
      |> render()
      |> LazyHTML.from_fragment()
      |> LazyHTML.query("#api-key-plaintext-value")
      |> LazyHTML.attribute("value")

    plaintext
  end

  defp cli_run(args, config_root), do: Taskman.CLI.run(args, config_root: config_root, env: %{})

  defp write_config(config_root, api_url, api_key) do
    path = Path.join([config_root, "taskman", "config.json"])
    File.mkdir_p!(Path.dirname(path))
    :ok = File.chmod(Path.dirname(path), 0o700)
    File.write!(path, Jason.encode!(%{"api_url" => api_url, "api_key" => api_key}))
    :ok = File.chmod(path, 0o600)
  end
end
