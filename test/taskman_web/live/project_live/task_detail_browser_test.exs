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
          "task-status",
          "task-priority",
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
