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

  test "fresh browser filters include analysis while explicit saved arrays preserve exclusion",
       c do
    s = c.session

    {:ok, _} = update_task(c.project, c.task, %{status: :in_analysis})

    url = c.base <> "/projects/#{c.project.id}"
    js(s, "localStorage.removeItem('taskman.task-table.visible-statuses')")
    Browser.visit(s, url)
    Browser.find(s, Query.css("#workspace-content[data-preferences-hydrated='true']"))
    Browser.find(s, Query.css("#open-task-#{c.task.id}"))
    Browser.click(s, Query.css("#task-status-filter-button"))
    Browser.find(s, Query.css("#task-status-filter-option-in_analysis:checked"))

    js(
      s,
      ~s|localStorage.setItem('taskman.task-table.visible-statuses', '["icebox","pending","in_progress","in_review"]')|
    )

    Browser.visit(s, url)
    Browser.find(s, Query.css("#workspace-content[data-preferences-hydrated='true']"))
    Browser.find(s, Query.css("#open-task-#{c.task.id}", count: 0))
    Browser.click(s, Query.css("#task-status-filter-button"))
    Browser.find(s, Query.css("#task-status-filter-option-in_analysis:not(:checked)"))

    assert js(s, "return JSON.parse(localStorage.getItem('taskman.task-table.visible-statuses'))") ==
             ~w(icebox pending in_progress in_review)
  end

  test "reveal round trip retains native value, popup and keyboard focus without autosave", c do
    s = c.session
    Browser.visit(s, c.base <> "/projects/#{c.project.id}/tasks/#{c.task.id}")
    Browser.find(s, Query.css("#task-status-trigger"))

    js(
      s,
      "window.statusChanges = []; document.addEventListener('change', e => {if (e.target.id === 'task-status') statusChanges.push(e.target.value)})"
    )

    Browser.click(s, Query.css("#task-status-trigger"))

    assert js(
             s,
             "return !document.getElementById('task-status-option-not_planned') && !document.getElementById('task-status-option-in_analysis')"
           )

    Browser.find(s, Query.css("#task-status-reveal"))

    assert js(
             s,
             "const p = document.querySelector('#task-status-popup'); p.scrollTop = 0; const a = document.querySelector('#task-status-reveal').getBoundingClientRect(); return p.scrollHeight > p.clientHeight && a.bottom > p.getBoundingClientRect().bottom"
           )

    assert js(
             s,
             "const p = document.querySelector('#task-status-popup'); p.scrollTop = p.scrollHeight; const a = document.querySelector('#task-status-reveal').getBoundingClientRect(); const r = p.getBoundingClientRect(); return p.scrollTop > 0 && a.top >= r.top && a.bottom <= r.bottom"
           )

    Browser.send_keys(s, [:end, :down_arrow])
    assert js(s, "return document.activeElement.id") == "task-status-reveal"
    Browser.send_keys(s, [:up_arrow])

    assert js(
             s,
             "return document.querySelector('#task-status-options').getAttribute('aria-activedescendant')"
           ) == "task-status-option-will_not_do"

    Browser.send_keys(s, [:down_arrow])

    js(
      s,
      "requestAnimationFrame(() => requestAnimationFrame(() => {window.revealFocusReady = true}))"
    )

    wait(s, "return window.revealFocusReady", true)
    Browser.send_keys(s, [:enter])
    Browser.find(s, Query.css("#task-status-option-not_planned"))

    assert js(
             s,
             "return {open: document.querySelector('#task-status-popup').matches(':popover-open'), focus: document.activeElement.id, active: document.querySelector('#task-status-options').getAttribute('aria-activedescendant'), value: document.querySelector('#task-status').value, changes: statusChanges, action: !!document.querySelector('#task-status-reveal')}"
           ) ==
             %{
               "open" => true,
               "focus" => "task-status-options",
               "active" => "task-status-option-in_analysis",
               "value" => "pending",
               "changes" => [],
               "action" => false
             }

    wait(
      s,
      "return document.querySelector('[role=tooltip]')?.textContent",
      "Work being investigated or clarified before implementation begins."
    )

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

  test "keyboard state descriptions wait, follow the active row and preserve Escape precedence",
       c do
    s = c.session
    Browser.visit(s, c.base <> "/projects/#{c.project.id}/tasks/#{c.task.id}")
    Browser.click(s, Query.css("#task-status-trigger"))
    Browser.click(s, Query.css("#task-status-reveal"))
    Browser.find(s, Query.css("#task-status-option-not_planned"))

    assert js(
             s,
             "return [...document.querySelectorAll('#task-status-options [role=option]')].every(o => {const d = document.getElementById(o.getAttribute('aria-describedby')); return d && d.textContent === o.dataset.tooltip && !!d.textContent})"
           )

    Browser.send_keys(s, [:home])
    assert js(s, "return !document.querySelector('[role=tooltip]')")

    wait(
      s,
      "return document.querySelector('[role=tooltip]')?.textContent",
      "Intentionally deferred work that is not currently ready to begin."
    )

    assert js(
             s,
             "const t = document.querySelector('[role=tooltip]'); const r = t.getBoundingClientRect(); return t.matches(':popover-open') && document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2) === t"
           )

    assert js(s, "return document.querySelector('#task-status').value") == "pending"
    assert js(s, "return document.activeElement.id") == "task-status-options"
    Browser.send_keys(s, [:down_arrow])
    assert js(s, "return !document.querySelector('[role=tooltip]')")

    wait(
      s,
      "return document.querySelector('[role=tooltip]')?.textContent",
      "Work that is ready to be started."
    )

    Browser.send_keys(s, [:escape])

    assert js(
             s,
             "return !document.querySelector('[role=tooltip]') && !document.querySelector('#task-status-popup').matches(':popover-open') && !!document.querySelector('#task-modal')"
           )

    Browser.send_keys(s, [:escape])
    Browser.find(s, Query.css("#task-modal", count: 0))
  end

  test "filter rows expose descriptions before visual timing and cancel stale focused sources",
       c do
    s = c.session
    task_fixture(c.project, %{status: :not_planned})
    Browser.visit(s, c.base <> "/projects/#{c.project.id}")
    Browser.click(s, Query.css("#task-status-filter-button"))

    assert js(
             s,
             "return [...document.querySelectorAll('#task-status-filter-menu input[type=checkbox]')].every(o => {const d = document.getElementById(o.getAttribute('aria-describedby')); return d && d.textContent === o.closest('label').dataset.tooltip && !!d.textContent})"
           )

    js(s, "document.querySelector('#task-status-filter-option-not_planned').focus()")
    assert js(s, "return !document.querySelector('[role=tooltip]')")

    wait(
      s,
      "return document.querySelector('[role=tooltip]')?.textContent",
      "Work we currently consider unnecessary, retained for reconsideration if the need arises."
    )

    js(
      s,
      "const t = document.querySelector('[role=tooltip]'); document.body.dispatchEvent(new PointerEvent('pointerover', {bubbles: true})); t.dispatchEvent(new PointerEvent('pointerover', {bubbles: true})); return t.isConnected"
    )

    assert js(s, "return !!document.querySelector('[role=tooltip]')")
    Browser.hover(s, Query.css("[role=tooltip]"))
    js(s, "window.descriptionStarted = performance.now()")

    wait(
      s,
      "return performance.now() - descriptionStarted >= 500 && !!document.querySelector('[role=tooltip]')",
      true
    )

    Browser.send_keys(s, [:escape])
    Browser.find(s, Query.css("#task-status-filter-menu", count: 0))
    assert js(s, "return !document.querySelector('[role=tooltip]')")
    Browser.click(s, Query.css("#task-status-filter-button"))

    js(
      s,
      "document.querySelector('#task-status-filter-option-pending').focus(); document.querySelector('#task-status-filter-option-in_progress').focus(); window.descriptionStarted = performance.now()"
    )

    wait(
      s,
      "return document.querySelector('[role=tooltip]')?.textContent",
      "Work actively being performed by a person, an external agent, or both."
    )

    assert js(s, "return performance.now() - descriptionStarted >= 440")

    js(
      s,
      "document.querySelector('#task-status-filter-menu').remove(); window.descriptionStarted = performance.now()"
    )

    wait(s, "return !document.querySelector('[role=tooltip]')", true)

    wait(
      s,
      "return performance.now() - descriptionStarted >= 500 && !document.querySelector('[role=tooltip]')",
      true
    )
  end

  test "badge descriptions preserve pointer passthrough, update text and add no tab stop", c do
    s = c.session
    Browser.visit(s, c.base <> "/projects/#{c.project.id}")
    badge = "#task-status-#{c.task.id}"
    Browser.find(s, Query.css(badge))

    assert js(
             s,
             "const b = document.querySelector(arguments[0]); const d = document.getElementById(b.getAttribute('aria-describedby')); return b.tabIndex === -1 && d?.textContent === 'Work that is ready to be started.'",
             [badge]
           )

    Browser.hover(s, Query.css(badge))
    assert js(s, "return !document.querySelector('[role=tooltip]')")

    wait(
      s,
      "return document.querySelector('[role=tooltip]')?.textContent",
      "Work that is ready to be started."
    )

    Browser.click(s, :left)
    Browser.find(s, Query.css("#task-modal"))
    assert js(s, "return !document.querySelector('[role=tooltip]')")
    Browser.click(s, Query.css("#task-modal-close"))
    Browser.hover(s, Query.css(badge))

    wait(
      s,
      "return document.querySelector('[role=tooltip]')?.textContent",
      "Work that is ready to be started."
    )

    {:ok, _} = update_task(c.project, c.task, %{status: :in_progress})

    wait(
      s,
      "return document.querySelector(arguments[0])?.dataset.tooltip",
      "Work actively being performed by a person, an external agent, or both.",
      [badge]
    )

    assert js(s, "return !document.querySelector('[role=tooltip]')")
    Browser.hover(s, Query.css("#main-panel"))
    Browser.hover(s, Query.css(badge))

    wait(
      s,
      "return document.querySelector('[role=tooltip]')?.textContent",
      "Work actively being performed by a person, an external agent, or both."
    )

    js(
      s,
      "document.dispatchEvent(new Event('scroll')); window.descriptionStarted = performance.now()"
    )

    wait(
      s,
      "return performance.now() - descriptionStarted >= 500 && !document.querySelector('[role=tooltip]')",
      true
    )

    js(s, "document.querySelector('#move-task-row-button-#{c.task.id}').focus()")
    assert js(s, "return document.querySelector('[role=tooltip]')?.textContent") == "Move Task"
  end

  test "a page patch cancels pending descriptions even when the source survives", c do
    s = c.session
    Browser.visit(s, c.base <> "/projects/#{c.project.id}")
    Browser.click(s, Query.css("#task-status-filter-button"))

    js(
      s,
      "document.querySelector('#task-status-filter-option-pending').focus(); document.dispatchEvent(new Event('phx:update')); window.descriptionStarted = performance.now()"
    )

    wait(
      s,
      "return performance.now() - descriptionStarted >= 500 && !document.querySelector('[role=tooltip]')",
      true
    )
  end

  test "descriptions remain above hierarchy filters and bounded at real CSS viewports", c do
    s = c.session
    task_fixture(c.project, %{status: :not_planned}, parent: c.task)
    Browser.visit(s, c.base <> "/projects/#{c.project.id}/tasks/#{c.task.id}")
    Browser.find(s, Query.css("#task-hierarchy-toggle[aria-expanded='true']"))
    Browser.click(s, Query.css("#task-hierarchy-status-filter-button"))
    Browser.find(s, Query.css("#task-hierarchy-status-filter-option-not_planned"))
    js(s, "document.querySelector('#task-hierarchy-status-filter-option-not_planned').focus()")

    wait(
      s,
      "return document.querySelector('[role=tooltip]')?.textContent",
      "Work we currently consider unnecessary, retained for reconsideration if the need arises."
    )

    assert js(
             s,
             "const t = document.querySelector('[role=tooltip]'); const r = t.getBoundingClientRect(); return document.querySelector('#task-hierarchy-status-filter-menu').matches(':popover-open') && document.elementFromPoint(r.x + r.width/2, r.y + r.height/2) === t"
           )

    capture(s, "hierarchy-description")
    Browser.send_keys(s, [:escape])
    Browser.find(s, Query.css("#task-hierarchy-status-filter-menu", count: 0))

    assert js(
             s,
             "return !document.querySelector('[role=tooltip][popover]') && !!document.querySelector('#task-modal')"
           )

    Browser.click(s, Query.css("#task-hierarchy-toggle"))

    for {width, height} <- [{1440, 900}, {390, 844}, {320, 400}] do
      assert %{status: 200} =
               Req.post!(s.session_url <> "/goog/cdp/execute",
                 json: %{
                   cmd: "Emulation.setDeviceMetricsOverride",
                   params: %{width: width, height: height, deviceScaleFactor: 1, mobile: false}
                 }
               )

      assert js(s, "return [innerWidth, innerHeight]") == [width, height]
      Browser.click(s, Query.css("#task-status-trigger"))
      Browser.send_keys(s, [:end, :up_arrow])

      wait(
        s,
        "return document.querySelector('[role=tooltip]')?.textContent",
        "Work we currently consider unnecessary, retained for reconsideration if the need arises."
      )

      assert js(
               s,
               "const t = document.querySelector('[role=tooltip]'); const r = t.getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth && r.top >= 0 && r.bottom <= innerHeight && document.documentElement.scrollWidth <= innerWidth && document.activeElement.id === 'task-status-options' && !t.querySelector('button, a, input, [tabindex]')"
             )

      IO.puts(
        "Description viewport: " <>
          inspect(
            js(
              s,
              "return {width: innerWidth, height: innerHeight, scrollWidth: document.documentElement.scrollWidth, hover: matchMedia('(hover: hover)').matches}"
            )
          )
      )

      capture(s, "picker-description-#{width}x#{height}")
      Browser.send_keys(s, [:escape])
    end

    js(s, "document.querySelector('#task-status-picker').hidden = true")

    assert js(
             s,
             "const s = document.querySelector('#task-status'); return s.getBoundingClientRect().width > 0"
           )

    capture(s, "native-fallback")
  end

  defp capture(session, name) do
    if directory = System.get_env("TASKMAN_DESCRIPTION_SCREENSHOTS") do
      File.mkdir_p!(directory)
      File.write!(Path.join(directory, name <> ".png"), session.driver.take_screenshot(session))
    end
  end

  defp wait(session, script, expected, args \\ []) do
    wait_until(session, script, expected, args, System.monotonic_time(:millisecond) + 6_000)
  end

  defp wait_until(session, script, expected, args, deadline) do
    actual = js(session, script, args)

    if actual != expected do
      if System.monotonic_time(:millisecond) >= deadline,
        do: flunk("Expected #{inspect(expected)}, got #{inspect(actual)} for #{script}")

      receive do
      after
        40 -> wait_until(session, script, expected, args, deadline)
      end
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
