defmodule TaskmanWeb.ProjectLive.AdditionalStatesBrowserTest do
  use TaskmanWeb.ConnCase, async: false
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures
  alias Taskman.{Accounts, Tasks}
  alias Wallaby.{Browser, Query}
  @moduletag :browser

  setup do
    email = "states-browser-#{Ecto.UUID.generate()}@example.com"
    {:ok, _} = Accounts.bootstrap_admin(email, "browser-test-password")
    project = project_fixture(%{})
    task = task_fixture(project)

    server =
      start_supervised!(
        {Bandit, plug: TaskmanWeb.Endpoint, port: 0, ip: {127, 0, 0, 1}, startup_log: false}
      )

    {:ok, {_, port}} = ThousandIsland.listener_info(server)
    {:ok, _} = Application.ensure_all_started(:wallaby)

    {:ok, session} =
      Wallaby.start_session(
        headless: false,
        binary: System.find_executable("chromium"),
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

    %{session: session, base: base, project: project, task: task}
  end

  test "reveal round trip retains native value, popup and keyboard focus without autosave", c do
    s = c.session
    Browser.visit(s, c.base <> "/projects/#{c.project.id}/tasks/#{c.task.id}")
    Browser.find(s, Query.css("#task-status-trigger"))

    assert js(
             s,
             "window.statusChanges = []; document.addEventListener('change', e => {if (e.target.id === 'task-status') statusChanges.push(e.target.value)}); return [...document.querySelector('#task-status').options].map(o => o.value)"
           ) ==
             ~w(icebox pending in_progress in_review done not_planned will_not_do)

    Browser.click(s, Query.css("#task-status-trigger"))
    assert js(s, "return !document.getElementById('task-status-option-not_planned')")
    Browser.find(s, Query.css("#task-status-reveal"))

    assert js(
             s,
             "const p = document.querySelector('#task-status-popup').getBoundingClientRect(); const a = document.querySelector('#task-status-reveal').getBoundingClientRect(); return a.top >= p.top && a.bottom <= p.bottom"
           )

    Browser.send_keys(s, [:end, :down_arrow])
    assert js(s, "return document.activeElement.id") == "task-status-reveal"
    Browser.send_keys(s, [:up_arrow])

    assert js(
             s,
             "return document.querySelector('#task-status-options').getAttribute('aria-activedescendant')"
           ) == "task-status-option-will_not_do"

    Browser.send_keys(s, [:down_arrow, :enter])
    Browser.find(s, Query.css("#task-status-option-not_planned"))

    assert js(
             s,
             "return {open: document.querySelector('#task-status-popup').matches(':popover-open'), focus: document.activeElement.id, active: document.querySelector('#task-status-options').getAttribute('aria-activedescendant'), value: document.querySelector('#task-status').value, changes: statusChanges, action: !!document.querySelector('#task-status-reveal'), values: [...document.querySelector('#task-status-options').children].map(o => o.dataset.value)}"
           ) ==
             %{
               "open" => true,
               "focus" => "task-status-options",
               "active" => "task-status-option-not_planned",
               "value" => "pending",
               "changes" => [],
               "action" => false,
               "values" => ~w(icebox pending in_progress in_review done not_planned will_not_do)
             }

    persisted = Tasks.get_task_for_project(c.project, c.task.id)
    assert persisted.status == :pending
    assert persisted.lock_version == c.task.lock_version
    Browser.send_keys(s, [:escape])

    assert js(
             s,
             "return !document.querySelector('#task-status-popup').matches(':popover-open') && !!document.querySelector('#task-modal')"
           )

    Browser.click(s, Query.css("#task-status-trigger"))
    Browser.send_keys(s, [:tab])

    assert js(
             s,
             "return !document.querySelector('#task-status-popup').matches(':popover-open') && !!document.querySelector('#task-modal')"
           )

    Browser.click(s, Query.css("#task-modal-close"))
    Browser.find(s, Query.css("#task-modal", count: 0))
    Browser.click(s, Query.css("#open-task-#{c.task.id}"))
    Browser.click(s, Query.css("#task-status-trigger"))
    Browser.click(s, Query.css("#task-status-reveal"))
    Browser.find(s, Query.css("#task-status-option-not_planned"))

    assert js(
             s,
             "return document.querySelector('#task-status-popup').matches(':popover-open') && document.querySelector('#task-status').value === 'pending' && document.activeElement.id === 'task-status-options'"
           )

    Browser.click(s, Query.css("#task-status-option-not_planned"))
    Browser.find(s, Query.css("#task-status-save-status[data-state='saved']"))
    assert Tasks.get_task_for_project(c.project, c.task.id).status == :not_planned
    assert js(s, "return statusChanges") == ["not_planned"]
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
