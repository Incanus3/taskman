defmodule TaskmanWeb.ProjectLive.TaskDetailBrowserTest do
  use TaskmanWeb.ConnCase, async: false

  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures
  import Taskman.ListsFixtures

  alias Taskman.{Accounts, Tasks}
  alias Wallaby.{Browser, Query}

  @moduletag :browser

  setup do
    email = "detail-browser-#{Ecto.UUID.generate()}@example.com"
    {:ok, _} = Accounts.bootstrap_admin(email, "browser-test-password")
    project = project_fixture(%{})
    list = list_fixture(project)
    root = task_fixture(project, list, %{title: "Connected work breakdown"})

    second =
      task_fixture(
        project,
        %{title: String.duplicate("Wrapped title ", 9) |> String.slice(0, 120)},
        parent: root
      )

    third = task_fixture(project, %{title: String.duplicate("X", 64)}, parent: second)

    selected =
      task_fixture(
        project,
        %{title: String.duplicate("Selected fourth level work ", 6) |> String.slice(0, 120)},
        parent: third
      )

    siblings =
      for n <- 1..18,
          do:
            task_fixture(
              project,
              %{
                title: "Sibling #{n}: " <> String.duplicate("readable work ", 5),
                status:
                  Enum.at(
                    [:icebox, :pending, :in_progress, :in_review, :done, :will_not_do],
                    rem(n, 6)
                  )
              },
              parent: root
            )

    optional = List.last(siblings)
    optional_child = task_fixture(project, %{title: "Optional branch child"}, parent: optional)

    server =
      start_supervised!(
        {Bandit, plug: TaskmanWeb.Endpoint, port: 0, ip: {127, 0, 0, 1}, startup_log: false}
      )

    {:ok, {_, port}} = ThousandIsland.listener_info(server)
    {:ok, _} = Application.ensure_all_started(:wallaby)

    {:ok, session} =
      Wallaby.start_session(binary: System.find_executable("chromium"), window_size: [1440, 900])

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

    %{
      session: session,
      base: base,
      project: project,
      list: list,
      root: root,
      second: second,
      third: third,
      selected: selected,
      optional: optional,
      optional_child: optional_child
    }
  end

  # A real text field must retain partial edits through patches and send day-first values to the UI boundary.
  test "due text typing uses the chosen format and preserves invalid drafts", c do
    visit(c, c.selected)
    Browser.fill_in(c.session, Query.css("#task-due-at"), with: "28.10.2026 17:45")
    Browser.find(c.session, Query.css("#task-due-at-save-status[data-state='saved']"))
    assert Tasks.get_task_for_project(c.project, c.selected.id).due_at == ~N[2026-10-28 17:45:00]

    assert js(c.session, "return document.querySelector('#task-due-at').value") ==
             "28.10.2026 17:45"

    Browser.find(c.session, Query.css("#task-due-at-trigger:not([hidden])"))
    Browser.fill_in(c.session, Query.css("#task-due-at"), with: "31.02.2026 17:45")
    Browser.find(c.session, Query.css("#task-due-at-save-status[data-state='not_saved']"))
    assert Tasks.get_task_for_project(c.project, c.selected.id).due_at == ~N[2026-10-28 17:45:00]

    assert js(c.session, "return document.querySelector('#task-due-at').value") ==
             "31.02.2026 17:45"

    Browser.fill_in(c.session, Query.css("#task-due-at"), with: "28.10.")
    Browser.find(c.session, Query.css("#task-due-at-picker[data-value='28.10.']"))

    {:ok, _} =
      Tasks.update_task(c.project, Tasks.get_task_for_project(c.project, c.selected.id), %{
        title: "Patched title"
      })

    Browser.find(c.session, Query.css("#task-title[value='Patched title']"))
    assert js(c.session, "return document.querySelector('#task-due-at').value") == "28.10."
    Browser.fill_in(c.session, Query.css("#task-due-at"), with: "29.02.2028 09:30")
    Browser.find(c.session, Query.css("#task-due-at-save-status[data-state='saved']"))
    Browser.click(c.session, Query.css("#task-due-at-trigger"))
    Browser.find(c.session, Query.css("#task-due-at-popup:popover-open"))
    Browser.click(c.session, Query.css("#task-due-at-cancel"))

    assert js(c.session, "return document.querySelector('#task-due-at').value") ==
             "29.02.2028 09:30"

    Browser.click(c.session, Query.css("#task-due-at-trigger"))

    js(
      c.session,
      "const m=document.querySelector('[data-vc-time-input=minute] input');m.value='31';m.dispatchEvent(new Event('change',{bubbles:true}))"
    )

    Browser.click(c.session, Query.css("#task-due-at-apply"))
    Browser.find(c.session, Query.css("#task-due-at-picker[data-value='2028-02-29T09:31']"))
    assert Tasks.get_task_for_project(c.project, c.selected.id).due_at == ~N[2028-02-29 09:31:00]
  end

  # Draft inputs must never reach the enclosing LiveView form before Apply.
  test "due popup isolates draft events and applies one local datetime", c do
    visit(c, c.selected)
    Browser.find(c.session, Query.css("#task-due-at-trigger"))

    js(
      c.session,
      "window.__dueInputs=0; window.__draftLeaks=0; document.querySelector('#task-due-at').addEventListener('input',()=>window.__dueInputs++); document.querySelector('#task-form').addEventListener('input',e=>{if(e.target.closest('#task-due-at-popup'))window.__draftLeaks++}); document.querySelector('#task-form').addEventListener('change',e=>{if(e.target.closest('#task-due-at-popup'))window.__draftLeaks++})"
    )

    Browser.click(c.session, Query.css("#task-due-at-trigger"))
    Browser.find(c.session, Query.css("#task-due-at-apply[disabled]"))

    assert js(
             c.session,
             "return document.querySelector('[data-vc-time-input=hour] input').value + ':' + document.querySelector('[data-vc-time-input=minute] input').value"
           ) == "00:00"

    Browser.click(
      c.session,
      Query.css("#task-due-at-popup [data-vc-date-btn]:not([disabled])", at: 10)
    )

    js(
      c.session,
      "const e=document.querySelector('[data-vc-time-input=hour] input');e.value='09';e.dispatchEvent(new Event('change',{bubbles:true}));const m=document.querySelector('[data-vc-time-input=minute] input');m.value='30';m.dispatchEvent(new Event('change',{bubbles:true}))"
    )

    assert js(
             c.session,
             "return [window.__dueInputs,window.__draftLeaks,document.querySelector('#task-due-at').value]"
           ) == [0, 0, ""]

    Browser.click(c.session, Query.css("#task-due-at-cancel"))
    assert Tasks.get_task_for_project(c.project, c.selected.id).due_at == nil
    assert js(c.session, "return document.activeElement.id") == "task-due-at-trigger"

    # Pre-1970 dates and retained seconds must not be normalized by an unchanged Apply.
    {:ok, _} = Tasks.update_task(c.project, c.selected, %{due_at: ~N[1960-05-06 09:30:42]})
    Browser.find(c.session, Query.css("#task-due-at-picker[data-value='1960-05-06T09:30:42']"))
    Browser.click(c.session, Query.css("#task-due-at-trigger"))
    Browser.click(c.session, Query.css("#task-due-at-apply:not([disabled])"))
    assert js(c.session, "return window.__dueInputs") == 0
    assert Tasks.get_task_for_project(c.project, c.selected.id).due_at == ~N[1960-05-06 09:30:42]

    Browser.click(c.session, Query.css("#task-due-at-trigger"))

    js(
      c.session,
      "const m=document.querySelector('[data-vc-time-input=minute] input');m.value='31';m.dispatchEvent(new Event('change',{bubbles:true}))"
    )

    js(
      c.session,
      "const m=document.querySelector('[data-vc-time-input=minute] input');m.value='99';m.dispatchEvent(new Event('input',{bubbles:true}))"
    )

    Browser.find(c.session, Query.css("#task-due-at-apply[disabled]"))

    js(
      c.session,
      "document.querySelector('[data-vc-time-input=minute] input').dispatchEvent(new Event('change',{bubbles:true}))"
    )

    Browser.find(c.session, Query.css("#task-due-at-apply[disabled]"))

    js(
      c.session,
      "const m=document.querySelector('[data-vc-time-input=minute] input');m.value='31';m.dispatchEvent(new Event('change',{bubbles:true}))"
    )

    Browser.click(c.session, Query.css("#task-due-at-apply:not([disabled])"))
    Browser.find(c.session, Query.css("#task-due-at-save-status[data-state='saved']"))
    assert Tasks.get_task_for_project(c.project, c.selected.id).due_at == ~N[1960-05-06 09:31:00]
    assert js(c.session, "return window.__dueInputs") == 1

    Browser.click(c.session, Query.css("#task-due-at-trigger"))
    Browser.click(c.session, Query.css("#task-due-at-clear"))

    assert js(c.session, "return document.querySelector('#task-due-at').value") ==
             "06.05.1960 09:31"

    Browser.send_keys(c.session, [:escape])
    Browser.find(c.session, Query.css("#task-due-at-trigger[aria-expanded='false']"))
    Browser.click(c.session, Query.css("#task-due-at-trigger"))
    Browser.click(c.session, Query.css("#task-due-at-clear"))
    Browser.click(c.session, Query.css("#task-due-at-apply"))
    Browser.find(c.session, Query.css("#task-due-at-save-status[data-state='saved']"))
    assert Tasks.get_task_for_project(c.project, c.selected.id).due_at == nil
    assert js(c.session, "return [window.__dueInputs,window.__draftLeaks]") == [2, 0]
  end

  # Browser patches must preserve both private popup drafts and focused manual edits.
  test "due picker follows authoritative updates and stays reachable in the top layer", c do
    visit(c, c.selected)
    {:ok, _} = Tasks.update_task(c.project, c.selected, %{due_at: ~N[2030-05-06 09:30:00]})
    Browser.find(c.session, Query.css("#task-due-at-picker[data-value='2030-05-06T09:30']"))
    Browser.click(c.session, Query.css("#task-due-at-trigger"))

    js(
      c.session,
      "const m=document.querySelector('[data-vc-time-input=minute] input');m.value='45';m.dispatchEvent(new Event('change',{bubbles:true}))"
    )

    {:ok, _} =
      Tasks.update_task(c.project, Tasks.get_task_for_project(c.project, c.selected.id), %{
        title: "Unrelated patch"
      })

    Browser.find(c.session, Query.css("#task-title[value='Unrelated patch']"))

    assert js(
             c.session,
             "return document.querySelector('#task-due-at-popup').matches(':popover-open') && document.querySelector('[data-vc-time-input=minute] input').value==='45'"
           )

    {:ok, _} =
      Tasks.update_task(c.project, Tasks.get_task_for_project(c.project, c.selected.id), %{
        due_at: ~N[2031-06-07 10:20:00]
      })

    Browser.find(c.session, Query.css("#task-due-at-trigger[aria-expanded='false']"))
    Browser.find(c.session, Query.css("#task-due-at-picker-notice", text: "Reopen"))
    assert Tasks.get_task_for_project(c.project, c.selected.id).due_at == ~N[2031-06-07 10:20:00]

    # Real text typing retains the chosen display and its normal form event.
    Browser.fill_in(c.session, Query.css("#task-due-at"), with: "08.07.2032 11:21")
    Browser.find(c.session, Query.css("#task-due-at-save-status[data-state='saved']"))
    # An uncommitted text edit also survives an unrelated patch while focused.
    js(
      c.session,
      "const e=document.querySelector('#task-due-at');e.focus();e.value='08.07.2032 11:22'"
    )

    {:ok, _} =
      Tasks.update_task(c.project, Tasks.get_task_for_project(c.project, c.selected.id), %{
        title: "Native typing patch"
      })

    Browser.find(c.session, Query.css("#task-title[value='Native typing patch']"))

    assert js(c.session, "return document.querySelector('#task-due-at').value") ==
             "08.07.2032 11:22"

    js(
      c.session,
      "document.querySelector('#task-due-at').dispatchEvent(new Event('input',{bubbles:true}))"
    )

    Browser.find(c.session, Query.css("#task-due-at-picker[data-value='2032-07-08T11:22']"))
    assert Tasks.get_task_for_project(c.project, c.selected.id).due_at == ~N[2032-07-08 11:22:00]

    for {width, height} <- [{1440, 900}, {320, 720}, {320, 400}] do
      resize(c.session, width, height)

      if width == 320 &&
           js(
             c.session,
             "return document.querySelector('#task-detail-layout').dataset.hierarchyExpanded==='true'"
           ),
         do: Browser.click(c.session, Query.css("#task-hierarchy-toggle"))

      js(
        c.session,
        "document.querySelector('#task-due-at-trigger').scrollIntoView({block:'end'})"
      )

      Browser.click(c.session, Query.css("#task-due-at-trigger"))

      bounds =
        js(
          c.session,
          "const p=document.querySelector('#task-due-at-popup'),r=p.getBoundingClientRect(),b=document.querySelector('#task-due-at-apply').getBoundingClientRect();return {width:innerWidth,height:innerHeight,page:document.documentElement.scrollWidth,left:r.left,right:r.right,top:r.top,bottom:r.bottom,open:p.matches(':popover-open'),footer:document.elementFromPoint(b.x+b.width/2,b.y+b.height/2).id}"
        )

      assert bounds["width"] == width && bounds["height"] == height
      assert bounds["page"] <= width + 1
      assert bounds["open"] && bounds["footer"] == "task-due-at-apply"
      assert bounds["left"] >= 0 && bounds["right"] <= width
      assert bounds["top"] >= 0 && bounds["bottom"] <= height

      if directory = System.get_env("TASKMAN_DUE_SCREENSHOTS") do
        File.mkdir_p!(directory)

        File.write!(
          Path.join(directory, "due-#{width}-#{height}.png"),
          c.session.driver.take_screenshot(c.session)
        )

        File.write!(Path.join(directory, "due-#{width}-#{height}.json"), Jason.encode!(bounds))
        css = "file://" <> Path.expand("priv/static/assets/css/app.css")

        html =
          js(
            c.session,
            "const html=document.documentElement.cloneNode(true);html.querySelectorAll('script').forEach(e=>e.remove());html.querySelectorAll('link[rel=stylesheet]').forEach(e=>e.href=arguments[0]);return '<!doctype html>'+html.outerHTML",
            [css]
          )

        File.write!(Path.join(directory, "due-#{width}-#{height}.html"), html)
      end

      # Time Enter cannot submit; footer Tab/focus and Escape stay inside this dialog.
      js(c.session, "document.querySelector('[data-vc-time-input=minute] input').focus()")
      Browser.send_keys(c.session, [:enter])
      Browser.find(c.session, Query.css("#task-due-at-trigger[aria-expanded='true']"))
      js(c.session, "document.querySelector('#task-due-at-clear').focus()")
      Browser.send_keys(c.session, [:tab])
      assert js(c.session, "return document.activeElement.id") == "task-due-at-cancel"
      Browser.send_keys(c.session, [:escape])
      Browser.find(c.session, Query.css("#task-due-at-trigger[aria-expanded='false']"))
    end

    resize(c.session, 1440, 900)
    Browser.click(c.session, Query.css("#task-hierarchy-toggle"))
    Browser.click(c.session, Query.css("#task-due-at-trigger"))
    Browser.click(c.session, Query.css("#task-title"))
    assert js(c.session, "return document.activeElement.id") == "task-title"
    Browser.find(c.session, Query.css("#task-due-at-trigger[aria-expanded='false']"))
    Browser.click(c.session, Query.css("#task-due-at-trigger"))
    js(c.session, "document.querySelector('#task-status-trigger').focus()")
    Browser.send_keys(c.session, [:enter])
    Browser.find(c.session, Query.css("#task-due-at-trigger[aria-expanded='false']"))
    Browser.send_keys(c.session, [:escape])
    Browser.click(c.session, Query.css("#task-due-at-trigger"))
    Browser.click(c.session, Query.css("#task-hierarchy-link-#{c.third.id}"))
    Browser.find(c.session, Query.css("#task-due-at-trigger[aria-expanded='false']"))

    assert js(
             c.session,
             "return document.querySelectorAll('#task-due-at-popup [data-vc=calendar]').length"
           ) == 0

    Browser.click(c.session, Query.css("#task-hierarchy-link-#{c.selected.id}"))
    Browser.click(c.session, Query.css("#task-due-at-trigger"))
    js(c.session, "window.liveSocket.disconnect()")

    Browser.find(
      c.session,
      Query.css("#task-due-at-trigger[disabled][aria-expanded='false']", visible: :any)
    )

    js(c.session, "window.liveSocket.connect()")
    Browser.find(c.session, Query.css("#task-due-at-trigger:not([disabled])"))
    Browser.click(c.session, Query.css("#task-due-at-trigger"))
    Browser.click(c.session, Query.css("#task-due-at-cancel"))

    {:ok, _} =
      Tasks.update_task(c.project, Tasks.get_task_for_project(c.project, c.selected.id), %{
        due_at: ~N[0999-05-06 09:30:00]
      })

    Browser.find(c.session, Query.css("#task-due-at-picker[data-value='0999-05-06T09:30']"))
    Browser.find(c.session, Query.css("#task-due-at-trigger[hidden]", visible: :any))

    assert js(
             c.session,
             "const e=document.querySelector('#task-due-at');return {value:e.value,visible:e.getClientRects().length>0,enhanced:!document.querySelector('#task-due-at-trigger').hidden}"
           ) == %{"value" => "06.05.0999 09:30", "visible" => true, "enhanced" => false}

    assert Tasks.get_task_for_project(c.project, c.selected.id).due_at == ~N[0999-05-06 09:30:00]

    # A rejected server form value stays editable without opening the calendar.
    js(
      c.session,
      "const e=document.querySelector('#task-due-at');document.querySelector('#task-due-at-picker').dataset.value='2030-02-31T09:30';e.value='2030-02-31T09:30';e.dispatchEvent(new Event('input'))"
    )

    Browser.find(c.session, Query.css("#task-due-at-trigger[hidden]", visible: :any))

    refute js(
             c.session,
             "return !document.querySelector('#task-due-at-trigger').hidden"
           )

    # Creation shares the same bridge and still waits for explicit Create task.
    Browser.visit(c.session, c.base <> "/projects/#{c.project.id}/tasks/new")
    Browser.find(c.session, Query.css("#task-due-at-trigger"))
    Browser.click(c.session, Query.css("#task-due-at-trigger"))

    Browser.click(
      c.session,
      Query.css("#task-due-at-popup [data-vc-date-btn]:not([disabled])", at: 10)
    )

    Browser.click(c.session, Query.css("#task-due-at-apply:not([disabled])"))
    assert js(c.session, "return document.querySelector('#task-due-at').value.endsWith(' 00:00')")
    Browser.find(c.session, Query.css("#task-form"))
  end

  # Native options cannot prove browser keyboard handling, top-layer placement or patch sync.
  test "field picker keyboard saves only a choice and follows server updates", c do
    visit(c, c.selected)
    Browser.find(c.session, Query.css("#task-status-trigger"))

    # Opening a server-selected last option must reveal the whole active row immediately.
    {:ok, _} = Tasks.update_task(c.project, c.selected, %{status: :will_not_do})
    Browser.find(c.session, Query.css("#task-status-trigger", text: "Will Not Do"))
    Browser.click(c.session, Query.css("#task-status-trigger"))

    active =
      js(
        c.session,
        "const p=document.querySelector('#task-status-options'),a=document.getElementById(p.getAttribute('aria-activedescendant')),r=a.getBoundingClientRect(),b=p.getBoundingClientRect();return {id:a.id,top:r.top,bottom:r.bottom,panelTop:b.top,panelBottom:b.bottom}"
      )

    assert active["id"] == "task-status-option-will_not_do"
    assert active["top"] >= active["panelTop"]
    assert active["bottom"] <= active["panelBottom"]
    Browser.send_keys(c.session, [:escape])

    {:ok, _} =
      Tasks.update_task(c.project, Tasks.get_task_for_project(c.project, c.selected.id), %{
        status: :pending
      })

    Browser.find(c.session, Query.css("#task-status-trigger", text: "Pending"))

    js(
      c.session,
      "window.__pickerChanges=0; document.querySelector('#task-status').addEventListener('change',()=>window.__pickerChanges++); document.querySelector('#task-status-trigger').focus()"
    )

    Browser.send_keys(c.session, [:down_arrow])
    Browser.find(c.session, Query.css("#task-status-trigger[aria-expanded='true']"))
    Browser.send_keys(c.session, [:down_arrow])
    assert js(c.session, "return window.__pickerChanges") == 0
    Browser.send_keys(c.session, [:escape])
    Browser.find(c.session, Query.css("#task-status-trigger[aria-expanded='false']"))
    assert js(c.session, "return document.activeElement.id") == "task-status-trigger"
    Browser.find(c.session, Query.css("#task-detail-layout"))

    Browser.send_keys(c.session, [:space])
    Browser.send_keys(c.session, ["i"])
    Browser.send_keys(c.session, [:enter])
    Browser.find(c.session, Query.css("#task-status-save-status[data-state='saved']"))
    assert Tasks.get_task_for_project(c.project, c.selected.id).status == :icebox
    assert js(c.session, "return window.__pickerChanges") == 1

    {:ok, _} =
      Tasks.update_task(c.project, Tasks.get_task_for_project(c.project, c.selected.id), %{
        status: :in_review
      })

    Browser.find(c.session, Query.css("#task-status-picker[data-value='in_review']"))
    Browser.find(c.session, Query.css("#task-status-trigger", text: "In Review"))
    Browser.click(c.session, Query.css("#task-status-trigger"))
    Browser.find(c.session, Query.css("#task-status-option-in_review[aria-selected='true']"))
    Browser.click(c.session, Query.css("#task-priority-trigger"))
    Browser.find(c.session, Query.css("#task-status-trigger[aria-expanded='false']"))
    Browser.send_keys(c.session, [:end, :enter])
    Browser.find(c.session, Query.css("#task-priority-save-status[data-state='saved']"))
    assert Tasks.get_task_for_project(c.project, c.selected.id).priority == :urgent

    Browser.click(c.session, Query.css("#task-priority-trigger"))
    Browser.click(c.session, Query.css("#task-priority-option-high"))
    Browser.find(c.session, Query.css("#task-priority-save-status[data-state='saved']"))
    assert Tasks.get_task_for_project(c.project, c.selected.id).priority == :high
    assert js(c.session, "return document.activeElement.id") == "task-priority-trigger"

    Browser.click(c.session, Query.css("#task-status-trigger"))
    Browser.click(c.session, Query.css("#task-hierarchy-link-#{c.third.id}"))

    Browser.find(
      c.session,
      Query.css("#task-status-trigger[aria-expanded='false']", text: "Pending")
    )

    Browser.click(c.session, Query.css("#task-hierarchy-link-#{c.selected.id}"))
    Browser.find(c.session, Query.css("#task-status-trigger", text: "In Review"))

    # Rechoosing the current value is idempotent, and outside clicks keep their target focus.
    Browser.click(c.session, Query.css("#task-status-trigger"))
    Browser.send_keys(c.session, [:enter])
    assert js(c.session, "return window.__pickerChanges") == 1
    Browser.click(c.session, Query.css("#task-status-trigger"))
    Browser.click(c.session, Query.css("#task-title"))
    Browser.find(c.session, Query.css("#task-status-trigger[aria-expanded='false']"))
    assert js(c.session, "return document.activeElement.id") == "task-title"

    assert js(
             c.session,
             "return getComputedStyle(document.querySelector('#task-status')).display === 'none' || document.querySelector('#task-status').getClientRects().length === 0"
           )

    {:ok, _} = Tasks.add_block(c.project, c.root, c.selected)
    Browser.click(c.session, Query.css("#task-status-trigger"))
    Browser.send_keys(c.session, ["d", :enter])
    Browser.find(c.session, Query.css("#task-done-warning"))
    Browser.find(c.session, Query.css("#task-status-trigger", text: "Done"))
    Browser.click(c.session, Query.css("#keep-current-task-status"))
    Browser.find(c.session, Query.css("#task-status-trigger", text: "In Review"))
    assert Tasks.get_task_for_project(c.project, c.selected.id).status == :in_review

    for width <- [1440, 320] do
      resize(c.session, width, 720)

      if width == 320 do
        Browser.click(c.session, Query.css("#task-hierarchy-toggle"))
        wait(c.session, &(!&1["expanded"]))
      end

      js(
        c.session,
        "document.querySelector('#task-priority-trigger').scrollIntoView({block:'end'})"
      )

      Browser.click(c.session, Query.css("#task-priority-trigger"))

      bounds =
        js(
          c.session,
          "const p=document.querySelector('#task-priority-options'), r=p.getBoundingClientRect(); return {open:p.matches(':popover-open'),left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:innerWidth,height:innerHeight,page:document.documentElement.scrollWidth,hit:p.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2))}"
        )

      assert bounds["open"] && bounds["hit"]
      assert bounds["width"] == width
      assert bounds["page"] <= width + 1
      assert bounds["left"] >= 0 && bounds["right"] <= width
      assert bounds["top"] >= 0 && bounds["bottom"] <= bounds["height"]
      Browser.send_keys(c.session, [:tab])
      Browser.find(c.session, Query.css("#task-priority-trigger[aria-expanded='false']"))
    end

    Browser.click(c.session, Query.css("#task-status-trigger"))
    js(c.session, "window.liveSocket.disconnect()")
    Browser.find(c.session, Query.css("#task-status-trigger[disabled][aria-expanded='false']"))

    assert js(
             c.session,
             "return document.querySelector('#task-status-options').matches(':popover-open')"
           ) == false
  end

  # Catches squeezed text, page overflow, obscured controls and incorrect responsive composition.
  test "hierarchy and discussion remain reachable across viewport boundaries", c do
    visit(c, c.selected)

    for {width, height} <- [
          {1440, 900},
          {1280, 900},
          {1279, 900},
          {1024, 768},
          {1023, 768},
          {390, 844},
          {320, 720},
          {390, 400}
        ] do
      resize(c.session, width, height)

      s =
        wait(
          c.session,
          &(&1["viewport"] == width && &1["height"] == height && &1["closeHit"] && &1["toggleHit"] &&
              &1["selectedVisible"])
        )

      assert s["pageWidth"] <= width + 1
      assert s["panelBottom"] <= s["dialogBottom"] + 1
      assert s["scrollTopEdge"] >= s["headingBottom"] - 1
      assert s["scrollBottomEdge"] <= s["panelBottom"] + 1
      assert s["textWidth"] >= 180
      assert s["treeWidth"] <= s["treeClientWidth"] + 1
      assert s["overlay"] == width < 1024
      assert s["discussionBeside"] == width >= 1280
      js(c.session, "document.querySelector('#task-hierarchy-scroll').scrollTop = 200")
      after_scroll = state(c.session)
      assert after_scroll["headingTop"] == s["headingTop"]
      assert after_scroll["toggleHit"]
    end
  end

  test "closed narrow editor contains its long parent control and keeps fields reachable", c do
    resize(c.session, 320, 720)
    visit(c, c.selected)
    Browser.click(c.session, Query.css("#task-hierarchy-toggle"))
    wait(c.session, &(!&1["expanded"] && &1["panelWidth"] == 48))

    for id <- [
          "task-location-task-label",
          "move-task-detail-button-#{c.selected.id}",
          "task-title",
          "task-parent-trigger",
          "task-status-trigger",
          "task-priority-trigger",
          "task-due-at",
          "task-comment-text"
        ] do
      bounds =
        js(
          c.session,
          "const e=document.getElementById(arguments[0]); e.focus(); e.scrollIntoView({block:'center'}); const r=e.getBoundingClientRect(), content=document.querySelector('#task-detail-content').getBoundingClientRect(); return {left:r.left,right:r.right,contentLeft:content.left,contentRight:content.right,hit:e.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2))}",
          [id]
        )

      assert bounds["left"] >= bounds["contentLeft"]
      assert bounds["right"] <= bounds["contentRight"]
      assert bounds["hit"]
    end

    assert js(
             c.session,
             "const e=document.querySelector('#task-detail-content'); return e.scrollWidth <= e.clientWidth + 1"
           )
  end

  # Catches inert/focus state becoming stale after media changes or LiveView patches.
  test "overlay focus enters and returns safely through opening navigation and resizing", c do
    resize(c.session, 390, 844)
    visit(c, c.selected)
    wait(c.session, &(&1["focus"] == "task-hierarchy-toggle" && &1["editorInert"]))
    assert_overlay_tabs(c.session)
    js(c.session, "document.querySelector('#task-hierarchy-toggle').focus()")
    Browser.send_keys(c.session, [:escape])

    wait(
      c.session,
      &(!&1["expanded"] && !&1["editorInert"] && &1["focus"] == "task-hierarchy-toggle")
    )

    Browser.send_keys(c.session, [:enter])
    wait(c.session, &(&1["expanded"] && &1["editorInert"]))
    Browser.click(c.session, Query.css("#task-hierarchy-link-#{c.third.id}"))
    Browser.find(c.session, Query.css("#task-hierarchy-link-#{c.third.id}[aria-current='true']"))
    assert state(c.session)["expanded"]
    assert_overlay_tabs(c.session)

    resize(c.session, 1024, 768)
    wait(c.session, &(!&1["editorInert"] && !&1["overlay"]))
    js(c.session, "document.querySelector('#task-title').focus()")
    resize(c.session, 1023, 768)
    wait(c.session, &(&1["editorInert"] && &1["focus"] == "task-hierarchy-toggle"))
    js(c.session, "document.querySelector('#task-hierarchy-link-#{c.root.id}').focus()")
    resize(c.session, 1024, 768)
    wait(c.session, &(!&1["editorInert"] && &1["focus"] == "task-hierarchy-link-#{c.root.id}"))
    resize(c.session, 1023, 768)
    wait(c.session, &(&1["editorInert"] && &1["focus"] == "task-hierarchy-link-#{c.root.id}"))
    Browser.click(c.session, Query.css("#task-hierarchy-toggle"))
    wait(c.session, &(!&1["editorInert"] && &1["focus"] == "task-hierarchy-toggle"))
    js(c.session, "document.querySelector('#task-title').focus()")
    {:ok, _} = Tasks.update_task(c.project, c.third, %{status: :in_review})
    wait(c.session, &(&1["statuses"]["#{c.third.id}"] == "In Review"))
    assert state(c.session)["focus"] == "task-title"
  end

  # Catches resetting useful hierarchy scroll, losing optional branches, or failing to reveal a new selection.
  test "navigation reveals selection while patches preserve reading position and backdrop", c do
    visit(c, c.selected)
    Browser.click(c.session, Query.css("#task-hierarchy-disclosure-#{c.optional.id}"))
    Browser.find(c.session, Query.css("#task-hierarchy-link-#{c.optional_child.id}"))
    Browser.click(c.session, Query.css("#task-hierarchy-link-#{c.optional_child.id}"))

    Browser.find(
      c.session,
      Query.css("#task-hierarchy-link-#{c.optional_child.id}[aria-current='true']")
    )

    wait(c.session, & &1["selectedVisible"])
    assert state(c.session)["expanded"]
    js(c.session, "document.querySelector('#task-hierarchy-scroll').scrollTop = 100")
    before_patch = state(c.session)["scrollTop"]

    {:ok, _} =
      Tasks.update_task(c.project, c.second, %{
        title: "Updated wrapping ancestor " <> String.duplicate("work ", 20),
        status: :done
      })

    wait(c.session, &(&1["statuses"]["#{c.second.id}"] == "Done"))
    assert abs(state(c.session)["scrollTop"] - before_patch) <= 2
    # Navigation to an offscreen ancestor must reveal it without collapsing the optional branch.
    js(
      c.session,
      "document.querySelector('#task-hierarchy-scroll').scrollTop = 100000; document.querySelector('#task-hierarchy-link-#{c.selected.id}').click()"
    )

    Browser.find(
      c.session,
      Query.css("#task-hierarchy-link-#{c.selected.id}[aria-current='true']")
    )

    wait(c.session, & &1["selectedVisible"])

    Browser.find(
      c.session,
      Query.css("#task-hierarchy-link-#{c.optional_child.id}", visible: :any)
    )

    Browser.find(
      c.session,
      Query.css("#select-list-#{c.list.id}[aria-current='page']", visible: :any)
    )

    assert js(
             c.session,
             "return document.querySelector('#include-child-lists').getAttribute('aria-pressed')"
           ) == "true"

    assert js(
             c.session,
             "return document.querySelector('#task-location-task-label').textContent.trim()"
           ) == "Task ##{c.selected.id}"

    {:ok, _} =
      Tasks.update_task(c.project, c.selected, %{title: "Reparented selected work"},
        parent: c.optional
      )

    Browser.find(
      c.session,
      Query.css(
        "#task-hierarchy-node-#{c.optional.id} #task-hierarchy-link-#{c.selected.id}[aria-current='true']"
      )
    )

    Browser.find(c.session, Query.css("#task-hierarchy-expanded-marker-#{c.optional.id}"))
    wait(c.session, & &1["selectedVisible"])
    Taskman.Repo.delete!(c.optional_child)

    Browser.visit(
      c.session,
      c.base <> "/projects/#{c.project.id}/lists/#{c.list.id}/tasks/#{c.optional_child.id}"
    )

    Browser.find(c.session, Query.css("#task-not-found"))
    Browser.find(c.session, Query.css("#close-task-not-found"))
  end

  test "narrow recovery keeps retained input accessible with an open hierarchy preference", c do
    previous = Application.get_env(:taskman, :task_autosave_delay_ms)
    Application.put_env(:taskman, :task_autosave_delay_ms, 60_000)

    on_exit(fn ->
      case previous do
        nil -> Application.delete_env(:taskman, :task_autosave_delay_ms)
        value -> Application.put_env(:taskman, :task_autosave_delay_ms, value)
      end
    end)

    lost = list_fixture(c.project)
    target = task_fixture(c.project, lost, %{title: "Recovery target"}, parent: c.root)
    independent = task_fixture(c.project, %{title: "Independent work"})
    resize(c.session, 390, 844)

    Browser.visit(
      c.session,
      c.base <> "/projects/#{c.project.id}/lists/#{lost.id}/tasks/#{target.id}"
    )

    Browser.find(c.session, Query.css("#task-hierarchy-toggle[aria-expanded='true']"))
    Browser.click(c.session, Query.css("#task-hierarchy-toggle"))
    wait(c.session, &(!&1["expanded"] && &1["panelWidth"] == 48))
    Browser.fill_in(c.session, Query.css("#task-title"), with: "Retained recovery title")

    Browser.fill_in(c.session, Query.css("#task-description"),
      with: "Retained recovery description"
    )

    Browser.find(c.session, Query.css("#task-title-save-status[data-state='saving']"))
    Browser.find(c.session, Query.css("#task-description-save-status[data-state='saving']"))
    Browser.click(c.session, Query.css("#task-hierarchy-toggle"))
    Browser.find(c.session, Query.css("#task-detail-content[inert]"))
    Taskman.Repo.delete!(target)
    Taskman.Repo.delete!(lost)
    :ok = Taskman.ChangeNotifications.publish_list(lost, :updated, [:name])
    Browser.find(c.session, Query.css("#task-recovery #task-title[readonly]"))
    Browser.find(c.session, Query.css("#task-recovery #task-description[readonly]"))
    Browser.find(c.session, Query.css("#task-recovery #task-status[disabled]"))
    Browser.find(c.session, Query.css("#task-recovery #task-priority[disabled]"))
    Browser.find(c.session, Query.css("#task-recovery #task-due-at[readonly]"))

    assert js(
             c.session,
             "return document.querySelectorAll('[id^=task-status-options]:popover-open, [id^=task-priority-options]:popover-open, #task-due-at-popup:popover-open').length"
           ) == 0

    Browser.find(c.session, Query.css("#task-recovery #task-hierarchy", count: 0))

    refute js(c.session, "return document.querySelector('#task-detail-content').inert")

    for {id, value} <- [
          {"task-title", "Retained recovery title"},
          {"task-description", "Retained recovery description"}
        ] do
      Browser.click(c.session, Query.css("##{id}"))
      Browser.send_keys(c.session, [:control, "a", :null])

      assert js(
               c.session,
               "const e=document.getElementById(arguments[0]); return {focus:document.activeElement.id,selected:e.value.slice(e.selectionStart,e.selectionEnd),readonly:e.readOnly}",
               [id]
             ) == %{"focus" => id, "selected" => value, "readonly" => true}
    end

    js(
      c.session,
      "Object.defineProperty(navigator, 'clipboard', {configurable:true, value:{writeText:value=>{window.__recoveryCopied=value;return Promise.resolve()}}})"
    )

    js(c.session, "document.querySelector('#task-recovery-copy').focus()")
    Browser.send_keys(c.session, [:enter])
    Browser.find(c.session, Query.css("#task-recovery-copy-status", text: "Copied."))
    copied = js(c.session, "return window.__recoveryCopied")
    assert copied =~ "Retained recovery title"
    assert copied =~ "Retained recovery description"

    # A confirmed missing Task offers Copy/Discard; retryable recovery is covered below the browser.
    Browser.find(c.session, Query.css("#task-recovery-resume", count: 0))
    js(c.session, "document.querySelector('#task-recovery-discard').focus()")
    Browser.send_keys(c.session, [:enter])
    Browser.find(c.session, Query.css("#task-recovery", count: 0))
    Browser.click(c.session, Query.css("#open-task-#{independent.id}"))
    Browser.find(c.session, Query.css("#task-hierarchy-toggle[aria-expanded='true']"))
    Browser.find(c.session, Query.css("#task-hierarchy-empty"))
    assert state(c.session)["editorInert"]
    Browser.click(c.session, Query.css("#task-hierarchy-toggle"))
    wait(c.session, &(!&1["expanded"] && !&1["editorInert"]))
  end

  defp resize(session, width, height) do
    chrome_height = js(session, "return outerHeight - innerHeight")
    Browser.resize_window(session, width, height + chrome_height)
  end

  defp visit(c, task) do
    Browser.visit(
      c.session,
      c.base <>
        "/projects/#{c.project.id}/lists/#{c.list.id}/tasks/#{task.id}?include_children=true&statuses=icebox,pending,in_progress,in_review"
    )

    Browser.find(c.session, Query.css("#task-hierarchy-link-#{task.id}[aria-current='true']"))
    Browser.find(c.session, Query.css("#task-hierarchy-toggle[aria-expanded='true']"))
  end

  defp assert_overlay_tabs(session) do
    js(session, "document.querySelector('#task-hierarchy-toggle').focus()")

    for _ <- 1..12 do
      Browser.send_keys(session, [:tab])

      assert js(
               session,
               "return !!document.activeElement.closest('#task-dialog-header, #task-hierarchy')"
             )
    end

    js(session, "document.querySelector('#task-modal-close').focus()")
    assert state(session)["focus"] == "task-modal-close"
  end

  defp state(session) do
    js(session, ~S"""
    const q = s => document.querySelector(s), rect = s => q(s).getBoundingClientRect();
    const hit = s => { const e=q(s), r=e.getBoundingClientRect(); return e.contains(document.elementFromPoint(r.x+r.width/2, r.y+r.height/2)); };
    const scroll=q('#task-hierarchy-scroll'), selected=q('#task-hierarchy a[aria-current="true"]');
    const sr=scroll.getBoundingClientRect(), r=selected.getBoundingClientRect();
    return {viewport:innerWidth, height:innerHeight, pageWidth:document.documentElement.scrollWidth,
      dialogBottom:rect('#task-modal-content').bottom, panelBottom:rect('#task-hierarchy').bottom,
      panelWidth:rect('#task-hierarchy').width, dialogWidth:rect('#task-modal-content').width,
      headingTop:rect('.task-hierarchy-header').top, headingBottom:rect('.task-hierarchy-header').bottom,
      scrollTopEdge:sr.top, scrollBottomEdge:sr.bottom, treeWidth:scroll.scrollWidth, treeClientWidth:scroll.clientWidth,
      scrollTop:scroll.scrollTop, textWidth:selected.querySelector('.task-hierarchy-title').getBoundingClientRect().width,
      selectedVisible:r.top >= sr.top-1 && r.bottom <= sr.bottom+1,
      closeHit:hit('#task-modal-close'), toggleHit:hit('#task-hierarchy-toggle'),
      overlay:getComputedStyle(q('#task-hierarchy-overlay')).display !== 'none',
      expanded:q('#task-detail-layout').dataset.hierarchyExpanded === 'true', editorInert:q('#task-detail-content').inert,
      discussionBeside:rect('#task-detail-discussion').left >= rect('#task-detail-main').right-1,
      statuses:Object.fromEntries([...document.querySelectorAll('[id^="task-hierarchy-status-"]')].map(e => [e.id.replace('task-hierarchy-status-', ''), e.textContent.trim()])),
      focus:document.activeElement.id};
    """)
  end

  defp wait(session, predicate),
    do: wait(session, predicate, System.monotonic_time(:millisecond) + 5_000)

  defp wait(session, predicate, deadline) do
    snapshot = state(session)

    cond do
      predicate.(snapshot) -> snapshot
      System.monotonic_time(:millisecond) < deadline -> wait(session, predicate, deadline)
      true -> flunk("browser state: #{inspect(snapshot)}")
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
