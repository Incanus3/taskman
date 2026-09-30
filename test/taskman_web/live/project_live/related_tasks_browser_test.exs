defmodule TaskmanWeb.ProjectLive.RelatedTasksBrowserTest do
  use TaskmanWeb.ConnCase, async: false

  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Wallaby.{Browser, Query}

  @moduletag :browser

  test "relationship dropdown floats without moving groups and dismisses within Task detail" do
    password = "browser-test-password"
    email = "related-browser-#{Ecto.UUID.generate()}@example.com"
    assert {:ok, _user} = Taskman.Accounts.bootstrap_admin(email, password)
    project = project_fixture(%{})
    task = task_fixture(project, %{})
    candidate = task_fixture(project, %{title: "Related candidate"})

    server =
      start_supervised!(
        {Bandit, plug: TaskmanWeb.Endpoint, port: 0, ip: {127, 0, 0, 1}, startup_log: false}
      )

    assert {:ok, {_address, port}} = ThousandIsland.listener_info(server)
    base_url = "http://127.0.0.1:#{port}"
    {:ok, _} = Application.ensure_all_started(:wallaby)

    {:ok, session} =
      Wallaby.start_session(
        binary: System.find_executable("chromium"),
        window_size: [width: 1440, height: 800]
      )

    on_exit(fn -> Wallaby.end_session(session) end)
    Browser.visit(session, base_url <> "/sign-in")
    Browser.find(session, Query.css("[data-phx-main].phx-connected"))

    Browser.execute_script(
      session,
      ~S"""
      document.querySelector('input[name="user[email]"]').value = arguments[0];
      document.querySelector('input[name="user[password]"]').value = arguments[1];
      document.querySelector('form[action="/auth/user/password/sign_in"]').submit();
      """,
      [email, password]
    )

    Browser.find(session, Query.css("#application-home-link"))
    Browser.visit(session, "#{base_url}/projects/#{project.id}/tasks/#{task.id}")
    Browser.find(session, Query.css("[data-phx-main].phx-connected #related-tasks"))

    for width <- [1440, 390], direction <- ["blocked-by", "blocks"] do
      Browser.resize_window(session, width, 800)

      height =
        js(session, "return document.getElementById('related-tasks').offsetHeight")

      Browser.click(session, Query.css("#related-add-#{direction}"))
      Browser.find(session, Query.css("#related-picker-search"))

      assert js(
               session,
               ~S"""
               const panel = document.getElementById('related-picker');
               const bounds = panel.getBoundingClientRect();
               return document.getElementById('related-tasks').offsetHeight === arguments[0] &&
                 bounds.width > 0 && bounds.height > 0 &&
                 bounds.left >= 11 && bounds.right <= innerWidth - 11 &&
                 bounds.top >= 11 && bounds.bottom <= innerHeight - 11;
               """,
               [height]
             )

      Browser.find(session, Query.css("#related-picker-search:focus"))
      Browser.fill_in(session, Query.css("#related-picker-search"), with: "no matching candidate")
      Browser.find(session, Query.css("#related-picker-empty"))
      Browser.fill_in(session, Query.css("#related-picker-search"), with: "Related candidate")
      Browser.find(session, Query.css("#related-candidate-#{candidate.id}"))

      Browser.execute_script(session, "window.relatedSearchDocument = arguments[0]", [email])
      Browser.send_keys(session, [:enter])
      Browser.find(session, Query.css("#related-picker-search:focus:not([readonly])"))
      Browser.fill_in(session, Query.css("#related-picker-search"), with: "no matching candidate")
      Browser.find(session, Query.css("#related-picker-empty"))
      assert js(session, "return window.relatedSearchDocument") == email
      Browser.find(session, Query.css("#task-modal"))
      Browser.fill_in(session, Query.css("#related-picker-search"), with: "Related candidate")
      Browser.find(session, Query.css("#related-candidate-#{candidate.id}"))

      assert js(
               session,
               ~S"""
               const panel = document.getElementById('related-picker');
               const bounds = panel.getBoundingClientRect();
               const anchor = document.getElementById(arguments[0]).getBoundingClientRect();
               return panel.matches(':popover-open') && bounds.left >= 11 &&
                 bounds.right <= innerWidth - 11 && bounds.top >= 11 && bounds.bottom <= innerHeight - 11 &&
                 Math.min(Math.abs(bounds.top - anchor.bottom), Math.abs(bounds.bottom - anchor.top)) <= 9;
               """,
               ["related-add-#{direction}"]
             )

      if direction == "blocked-by" do
        Browser.execute_script(session, "document.getElementById('related-add-blocks').focus()")
        Browser.send_keys(session, [:enter])
        Browser.find(session, Query.css("#related-picker[data-anchor='related-add-blocks']"))
        Browser.find(session, Query.css("#related-picker-search:focus"))

        Browser.execute_script(
          session,
          "document.getElementById('related-add-blocked-by').focus()"
        )

        Browser.send_keys(session, [:enter])
        Browser.find(session, Query.css("#related-picker[data-anchor='related-add-blocked-by']"))
        Browser.find(session, Query.css("#related-picker-search:focus"))
      end

      Browser.send_keys(session, [:escape])
      assert Browser.has?(session, Query.css("#related-picker", count: 0, visible: :any))
      Browser.find(session, Query.css("#related-add-#{direction}:focus"))
      Browser.find(session, Query.css("#task-modal"))

      Browser.click(session, Query.css("#related-add-#{direction}"))
      Browser.find(session, Query.css("#related-picker-search:focus"))
      Browser.click(session, Query.css("#task-title"))
      assert Browser.has?(session, Query.css("#related-picker", count: 0, visible: :any))
      Browser.find(session, Query.css("#task-title:focus"))
      Browser.find(session, Query.css("#task-modal"))
    end
  end

  defp js(session, script, args \\ []) do
    owner = self()
    ref = make_ref()
    Browser.execute_script(session, script, args, fn result -> send(owner, {ref, result}) end)
    assert_receive {^ref, result}
    result
  end
end
