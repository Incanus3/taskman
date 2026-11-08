defmodule TaskmanWeb.ProjectLive.DescriptionReplacementBrowserTest do
  use TaskmanWeb.ConnCase, async: false

  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.{Accounts, Tasks}
  alias Wallaby.{Browser, Query}

  @moduletag :browser

  # Run headless with DISPLAY unset: X11 keyboard layouts can remap WebDriver's Ctrl+Z.

  setup do
    previous = Application.get_env(:taskman, :task_autosave_delay_ms)
    Application.put_env(:taskman, :task_autosave_delay_ms, 60_000)

    on_exit(fn ->
      if previous,
        do: Application.put_env(:taskman, :task_autosave_delay_ms, previous),
        else: Application.delete_env(:taskman, :task_autosave_delay_ms)
    end)

    email = "description-browser-#{Ecto.UUID.generate()}@example.com"
    {:ok, _} = Accounts.bootstrap_admin(email, "browser-test-password")
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Replacement undo", description: "Original"})

    server =
      start_supervised!(
        {Bandit, plug: TaskmanWeb.Endpoint, port: 0, ip: {127, 0, 0, 1}, startup_log: false}
      )

    {:ok, {_, port}} = ThousandIsland.listener_info(server)
    {:ok, _} = Application.ensure_all_started(:wallaby)

    {:ok, session} =
      Wallaby.start_session(
        binary: System.find_executable("chromium"),
        headless: true,
        window_size: [1440, 900]
      )

    on_exit(fn -> Wallaby.end_session(session) end)
    base = "http://127.0.0.1:#{port}"
    Browser.visit(session, base <> "/sign-in")
    Browser.find(session, Query.css("input[name='user[email]']"))

    js(
      session,
      "document.querySelector('input[name=\"user[email]\"]').value = arguments[0]; document.querySelector('input[name=\"user[password]\"]').value = 'browser-test-password'; document.querySelector('form[action=\"/auth/user/password/sign_in\"]').submit()",
      [email]
    )

    Browser.find(session, Query.css("#main-panel"))
    Browser.visit(session, base <> "/projects/#{project.id}/tasks/#{task.id}")
    Browser.find(session, Query.css("[data-editor-id][data-leaf-mount-state='ready']"))
    %{session: session, project: project, task: task}
  end

  test "canonical replacement gives keyboard undo a fresh baseline and preserves it through autosave",
       c do
    original_id = editor_id(c.session)
    Browser.click(c.session, Query.css("[data-editor-visual] > p:first-child"))
    Browser.send_keys(c.session, [:end, "?"])
    Browser.find(c.session, Query.css("#task-description-save-status[data-state='saving']"))
    assert Tasks.get_task_for_project(c.project, c.task.id).description == "Original"

    {:ok, _external} = update_task(c.project, c.task, %{description: "Latest"})

    Browser.find(c.session, Query.css("#use-latest-description"))
    Browser.click(c.session, Query.css("#use-latest-description"))
    Browser.find(c.session, Query.css("[data-editor-visual]", text: "Latest"))
    Browser.find(c.session, Query.css("[data-editor-id][data-leaf-mount-state='ready']"))

    # Use real WebDriver keyboard input and Leaf's Ctrl+Z handler. No DOM text assignment.
    Application.put_env(:taskman, :task_autosave_delay_ms, 0)
    Browser.click(c.session, Query.css("[data-editor-visual] > p:first-child"))
    Browser.send_keys(c.session, [:end, "!"])
    Browser.find(c.session, Query.css("#task-description-save-status[data-state='saved']"))
    assert editor_text(c.session) == "Latest!"
    assert Tasks.get_task_for_project(c.project, c.task.id).description == "Latest!"
    replaced_id = editor_id(c.session)

    {:ok, _updated} =
      update_task(
        c.project,
        Tasks.get_task_for_project(c.project, c.task.id),
        %{
          title: "Unrelated update"
        }
      )

    Browser.find(c.session, Query.css("#task-title[value='Unrelated update']"))
    assert editor_id(c.session) == replaced_id

    assert js(c.session, "return document.activeElement.closest('[data-editor-visual]') !== null")
    Browser.send_keys(c.session, [:control, "z", :null])
    assert_editor_text(c.session, "Latest")
    Browser.find(c.session, Query.css("#task-description-save-status[data-state='saved']"))
    assert Tasks.get_task_for_project(c.project, c.task.id).description == "Latest"
    assert editor_id(c.session) == replaced_id
    assert replaced_id != original_id

    # A clean incoming canonical update uses the same fresh-history boundary.
    {:ok, _updated} =
      update_task(
        c.project,
        Tasks.get_task_for_project(c.project, c.task.id),
        %{
          description: "Clean replacement"
        }
      )

    Browser.find(c.session, Query.css("[data-editor-visual]", text: "Clean replacement"))
    Browser.find(c.session, Query.css("[data-editor-id][data-leaf-mount-state='ready']"))
    clean_id = editor_id(c.session)
    assert clean_id != replaced_id
    Browser.click(c.session, Query.css("[data-editor-visual] > p:first-child"))
    Browser.send_keys(c.session, [:end, "!"])
    Browser.find(c.session, Query.css("#task-description-save-status[data-state='saved']"))
    assert Tasks.get_task_for_project(c.project, c.task.id).description == "Clean replacement!"
    Browser.send_keys(c.session, [:control, "z", :null])
    assert_editor_text(c.session, "Clean replacement")
    Browser.find(c.session, Query.css("#task-description-save-status[data-state='saved']"))
    assert Tasks.get_task_for_project(c.project, c.task.id).description == "Clean replacement"
    assert editor_id(c.session) == clean_id
  end

  defp editor_id(session),
    do: js(session, "return document.querySelector('[data-editor-id]').dataset.editorId")

  defp editor_text(session),
    do: js(session, "return document.querySelector('[data-editor-visual]').textContent.trim()")

  defp assert_editor_text(session, expected),
    do: assert_editor_text(session, expected, System.monotonic_time(:millisecond) + 5_000)

  defp assert_editor_text(session, expected, deadline) do
    actual = editor_text(session)

    cond do
      actual == expected ->
        :ok

      System.monotonic_time(:millisecond) < deadline ->
        assert_editor_text(session, expected, deadline)

      true ->
        flunk("expected editor #{inspect(expected)}, got #{inspect(actual)}")
    end
  end

  defp js(session, script, args \\ []) do
    ref = make_ref()
    owner = self()
    Browser.execute_script(session, script, args, fn result -> send(owner, {ref, result}) end)

    receive do
      {^ref, result} -> result
    after
      5_000 -> flunk("browser script did not return")
    end
  end
end
