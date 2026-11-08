defmodule TaskmanWeb.ProjectLive.DescriptionEditorBrowserTest do
  use TaskmanWeb.ConnCase, async: false

  import Taskman.ListsFixtures
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.{Accounts, Tasks}
  alias Wallaby.{Browser, Query}

  @moduletag :browser

  # Headless Chromium with DISPLAY unset avoids desktop Ctrl+Z remapping.
  # Delayed delivery uses only the public WebSocket.send platform boundary:
  # ordinary Leaf content_changed frames wait until departure begins; the real flush proceeds.
  setup do
    previous = Application.get_env(:taskman, :task_autosave_delay_ms)
    Application.put_env(:taskman, :task_autosave_delay_ms, 60_000)

    on_exit(fn ->
      if previous,
        do: Application.put_env(:taskman, :task_autosave_delay_ms, previous),
        else: Application.delete_env(:taskman, :task_autosave_delay_ms)
    end)

    email = "markdown-browser-#{Ecto.UUID.generate()}@example.com"
    {:ok, _} = Accounts.bootstrap_admin(email, "browser-test-password")
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Editor acceptance", description: ""})
    other = task_fixture(project, %{title: "Other task"})

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
      """
      document.querySelector('input[name="user[email]"]').value = arguments[0];
      document.querySelector('input[name="user[password]"]').value = 'browser-test-password';
      document.querySelector('form[action="/auth/user/password/sign_in"]').submit();
      """,
      [email]
    )

    Browser.find(session, Query.css("#main-panel"))
    %{session: session, base: base, project: project, task: task, other: other}
  end

  test "creation captures typed final input even when its ordinary change frame has not arrived",
       c do
    Browser.visit(c.session, c.base <> "/projects/#{c.project.id}/tasks/new")
    ready(c.session)
    Browser.fill_in(c.session, Query.css("#task-title"), with: "Created Markdown task")
    hold_changes(c.session)
    type_description(c.session, "Final character!")
    assert held_count(c.session) > 0
    depart(c.session, "document.querySelector('#create-task').click()")
    wait(c.session, "return !location.pathname.endsWith('/new')")
    Browser.find(c.session, Query.css("#task-modal", count: 0))

    created =
      Enum.find(Tasks.list_tasks_for_project(c.project), &(&1.title == "Created Markdown task"))

    assert created.description == "Final character!"
    Browser.click(c.session, Query.css("#open-task-#{created.id}"))
    ready(c.session)
    Browser.find(c.session, Query.css("#task-description [contenteditable='true']"))
  end

  for {departure, comment?} <- [{:task_link, true}, {:back, false}, {:back, true}] do
    test "#{departure} captures final input with comment draft #{comment?}", c do
      Browser.visit(c.session, c.base <> "/projects/#{c.project.id}")
      Browser.click(c.session, Query.css("#open-task-#{c.task.id}"))
      ready(c.session)

      if unquote(comment?) do
        Browser.fill_in(c.session, Query.css("#task-comment-text"), with: "Unposted comment")
      end

      hold_changes(c.session)
      type_description(c.session, "Final character!")
      assert held_count(c.session) > 0

      case unquote(departure) do
        :task_link ->
          depart(c.session, "document.querySelector('#open-task-#{c.other.id}').click()")

        :back ->
          depart(c.session, "history.back()")
      end

      if unquote(comment?) do
        Browser.find(c.session, Query.css("#task-comment-departure"))

        assert js(c.session, "return document.querySelector('#task-comment-text').value") ==
                 "Unposted comment"

        Browser.click(c.session, Query.css("#task-comment-departure-discard"))
      end

      if unquote(departure) == :task_link do
        Browser.find(c.session, Query.css("#task-title[value='Other task']"))
        depart(c.session, "document.querySelector('#task-modal-close').click()")
      end

      Browser.find(c.session, Query.css("#task-modal", count: 0))
      assert Tasks.get_task_for_project(c.project, c.task.id).description == "Final character!"

      if unquote(departure) == :back && !unquote(comment?) do
        # A native clean-comment Back retains the browser's Forward destination.
        depart(c.session, "history.forward()")
      else
        Browser.click(c.session, Query.css("#open-task-#{c.task.id}"))
      end

      ready(c.session)

      Browser.find(
        c.session,
        Query.css("#task-description [contenteditable='true']", text: "Final character!")
      )
    end
  end

  test "late ordinary changes from a closed editor cannot change a reopened Task or new draft",
       c do
    visit(c, c.task)
    old_id = editor_id(c.session)
    hold_changes(c.session)
    type_description(c.session, "Old delayed input")
    assert held_count(c.session) > 0
    Browser.send_keys(c.session, [:control, "a", :null, "Final character!"])
    assert held_count(c.session) > 0
    depart(c.session, "document.querySelector('#task-modal-close').click()")
    wait(c.session, "return !document.querySelector('#task-modal')")
    Browser.click(c.session, Query.css("#open-task-#{c.task.id}"))
    ready(c.session)
    refute editor_id(c.session) == old_id
    release_changes(c.session)
    Browser.find(c.session, Query.css("[data-editor-visual]", text: "Final character!"))
    assert Tasks.get_task_for_project(c.project, c.task.id).description == "Final character!"

    hold_changes(c.session)
    Browser.click(c.session, Query.css("[data-editor-visual]"))
    Browser.send_keys(c.session, [:end, "?"])
    depart(c.session, "document.querySelector('#task-modal-close').click()")
    Browser.find(c.session, Query.css("#task-modal", count: 0))
    Browser.click(c.session, Query.css("#add-task"))
    ready(c.session)
    release_changes(c.session)

    assert js(
             c.session,
             "return document.querySelector('[data-editor-visual]').textContent.trim()"
           ) == ""
  end

  test "untouched noncanonical Markdown remains byte identical after focus and Close", c do
    source = "# Notes\n\n-   item\n"

    {:ok, original} = update_task(c.project, c.task, %{description: source})

    visit(c, original)
    Browser.click(c.session, Query.css("[data-editor-visual]"))
    depart(c.session, "document.querySelector('#task-modal-close').click()")
    Browser.find(c.session, Query.css("#task-modal", count: 0))
    current = Tasks.get_task_for_project(c.project, original.id)
    assert current.description == source
    assert current.updated_at == original.updated_at
  end

  test "toolbar acts on a keyboard selection and ordinary patches retain caret and undo", c do
    visit(c, c.task)
    mode(c.session, "visual")
    type_description(c.session, "Selected words")
    Browser.send_keys(c.session, [:home, :shift, :end, :null])
    assert js(c.session, "return getSelection().toString()") == "Selected words"

    Browser.click(
      c.session,
      Query.css("[data-toolbar-action='bold'][title='Bold']")
    )

    Browser.find(
      c.session,
      Query.css("[data-editor-visual] strong, [data-editor-visual] b", text: "Selected words")
    )

    id = editor_id(c.session)
    Browser.click(c.session, Query.css("[data-editor-visual]"))
    Browser.send_keys(c.session, [:end, "!"])
    before_patch = caret(c.session)
    Application.put_env(:taskman, :task_autosave_delay_ms, 0)

    {:ok, _} = update_task(c.project, c.task, %{title: "Unrelated title"})

    Browser.find(c.session, Query.css("#task-title[value='Unrelated title']"))
    assert editor_id(c.session) == id
    assert caret(c.session) == before_patch
    Browser.send_keys(c.session, [:control, "z", :null])

    wait(
      c.session,
      "return document.querySelector('[data-editor-visual]').textContent.trim() === 'Selected words'"
    )

    depart(c.session, "document.querySelector('#task-modal-close').click()")
    Browser.find(c.session, Query.css("#task-modal", count: 0))
    assert Tasks.get_task_for_project(c.project, c.task.id).description == "**Selected words**"
  end

  test "source editing round trips nested lists checkboxes links fenced code and literal punctuation",
       c do
    visit(c, c.task)
    mode(c.session, "markdown")

    source =
      "# Notes\n\n**Bold** and *italic* and `literal`\n\n- outer\n  - inner\n\n- [ ] todo\n- [x] done\n\n[Safe link](https://example.test/guide)\n\n```elixir\nvalue = [1, 2]\n```\n\nLiteral snake_case & punctuation!"

    Browser.fill_in(c.session, Query.css("[data-markdown-wrapper] textarea"), with: source)
    mode(c.session, "hybrid")
    assert_common_formats(c.session)

    Browser.click(
      c.session,
      Query.css("[data-editor-visual] li[data-checked='false'] .leaf-task-box")
    )

    Browser.find(
      c.session,
      Query.css("[data-editor-visual] li[data-checked='true']", text: "todo")
    )

    c.session
    |> Browser.find(Query.css("[data-editor-visual] li[data-checked='true']", text: "todo"))
    |> Browser.click(Query.css(".leaf-task-box"))

    Browser.click(c.session, Query.css("[data-editor-visual] > p:last-child"))
    Browser.send_keys(c.session, [:end, "!"])
    depart(c.session, "document.querySelector('#task-modal-close').click()")
    Browser.find(c.session, Query.css("#task-modal", count: 0))
    markdown = Tasks.get_task_for_project(c.project, c.task.id).description
    assert markdown =~ "- outer\n  - inner"
    assert markdown =~ "- [ ] todo"
    assert markdown =~ "- [x] done"
    assert markdown =~ "[Safe link](https://example.test/guide)"
    assert markdown =~ "```elixir\nvalue = [1, 2]\n```"
    assert markdown =~ "snake_case & punctuation!!"
    Browser.click(c.session, Query.css("#open-task-#{c.task.id}"))
    ready(c.session)
    assert_common_formats(c.session)
  end

  test "table and strikethrough rendering stay literal and only strikethrough source escaping is accepted",
       c do
    source = "~~removed~~\n\n| A | B |\n| --- | --- |\n| one | two |\n\nOther"

    {:ok, task} = update_task(c.project, c.task, %{description: source})

    visit(c, task)
    Browser.find(c.session, Query.css("[data-editor-visual]", text: "~~removed~~"))

    assert js(
             c.session,
             "return document.querySelectorAll('[data-editor-visual] table, [data-editor-visual] del, [data-editor-visual] strike').length"
           ) == 0

    Browser.click(c.session, Query.css("[data-editor-visual] > p:last-child"))
    Browser.send_keys(c.session, [:end, "!"])
    depart(c.session, "document.querySelector('#task-modal-close').click()")
    Browser.find(c.session, Query.css("#task-modal", count: 0))

    assert Tasks.get_task_for_project(c.project, c.task.id).description ==
             "\\~\\~removed\\~\\~\n\n| A | B |\n| --- | --- |\n| one | two |\n\nOther!"

    Browser.click(c.session, Query.css("#open-task-#{c.task.id}"))
    ready(c.session)
    Browser.find(c.session, Query.css("[data-editor-visual]", text: "removed"))
  end

  test "external location loss captures unsent final input into sanitized read-only recovery",
       c do
    lost = list_fixture(c.project)

    source =
      "[unsafe](javascript:window.markdownProbe=1)\n\n<img src=x onerror=window.markdownProbe=2>\n\n<script>window.markdownProbe=3</script>\n\n- [ ] retained checkbox\n\nFinal character"

    target = task_fixture(c.project, lost, %{title: "Recovery acceptance", description: source})

    Browser.visit(
      c.session,
      c.base <> "/projects/#{c.project.id}/lists/#{lost.id}/tasks/#{target.id}"
    )

    ready(c.session)
    assert_safe(c.session)
    hold_changes(c.session)
    Browser.click(c.session, Query.css("[data-editor-visual] > p:last-child"))
    Browser.send_keys(c.session, [:end, "!"])
    assert held_count(c.session) > 0

    js(
      c.session,
      "document.querySelector('[data-editor-visual] > p:last-child').dataset.clientOnly = 'never-recover'"
    )

    Taskman.Repo.delete!(target)
    Taskman.Repo.delete!(lost)
    js(c.session, "window.descriptionHold = false")
    :ok = Taskman.ChangeNotifications.publish_list(lost, :updated, [:name])
    Browser.find(c.session, Query.css("#task-recovery"))
    ready(c.session)

    Browser.find(
      c.session,
      Query.css("#task-recovery [data-editor-visual][contenteditable='false']",
        text: "Final character!"
      )
    )

    assert_safe(c.session)

    assert js(
             c.session,
             "return document.querySelectorAll('#task-recovery [data-client-only]').length"
           ) == 0

    before =
      js(
        c.session,
        "const e=document.querySelector('[data-editor-visual]').cloneNode(true); e.querySelectorAll('.leaf-source-marker').forEach(n=>n.remove()); return e.textContent"
      )

    Browser.click(c.session, Query.css("[data-editor-visual]"))
    Browser.send_keys(c.session, ["Should not edit"])

    assert js(
             c.session,
             "const e=document.querySelector('[data-editor-visual]').cloneNode(true); e.querySelectorAll('.leaf-source-marker').forEach(n=>n.remove()); return e.textContent"
           ) ==
             before

    # Leaf replaces native checkboxes with its clickable hybrid task boxes.
    checked =
      js(
        c.session,
        "return document.querySelector('[data-editor-visual] li[data-checked]')?.dataset.checked"
      )

    assert checked == "false"
    Browser.click(c.session, Query.css("[data-editor-visual] .leaf-task-box"))

    assert js(
             c.session,
             "return document.querySelector('[data-editor-visual] li[data-checked]')?.dataset.checked"
           ) == checked

    mode(c.session, "markdown")
    Browser.find(c.session, Query.css("[data-markdown-wrapper] textarea[readonly]"))

    source =
      js(c.session, "return document.querySelector('[data-markdown-wrapper] textarea').value")

    Browser.click(c.session, Query.css("[data-markdown-wrapper] textarea[readonly]"))
    Browser.send_keys(c.session, ["Should not edit"])

    assert js(
             c.session,
             "return document.querySelector('[data-markdown-wrapper] textarea').value"
           ) == source

    mode(c.session, "hybrid")
    Browser.find(c.session, Query.css("[data-editor-visual][contenteditable='false']"))
  end

  for departure <- [:close, :back] do
    test "an unanswered #{departure} flush retains the origin and draft until retry",
         c do
      target =
        if unquote(departure) == :back,
          do: task_fixture(c.project, %{title: "Capture history child"}, parent: c.task),
          else: c.task

      Browser.visit(c.session, c.base <> "/projects/#{c.project.id}")
      Browser.click(c.session, Query.css("#open-task-#{c.task.id}"))
      ready(c.session)

      if unquote(departure) == :back do
        # Keep a preceding same-LiveView entry and a byte-exact query destination.
        js(
          c.session,
          "history.replaceState(history.state, '', location.pathname + '?probe=retained%20query')"
        )

        Browser.click(c.session, Query.css("#task-hierarchy-link-#{target.id}"))
        Browser.find(c.session, Query.css("#task-detail-discussion[data-task-id='#{target.id}']"))
        ready(c.session)
      end

      origin = js(c.session, "return location.pathname + location.search")
      initial_id = editor_id(c.session)
      hold_changes(c.session)
      type_description(c.session, "Final character!")
      assert held_count(c.session) > 0

      js(
        c.session,
        if(unquote(departure) == :close,
          do:
            "window.descriptionDropFlush = true; document.querySelector('#task-modal-close').click()",
          else: "window.descriptionDropFlush = true; history.back()"
        )
      )

      Browser.find(c.session, Query.css("#task-description[inert]"))

      wait(
        c.session,
        "return !!document.querySelector('#description-capture-error')",
        System.monotonic_time(:millisecond) + 8_000
      )

      Browser.find(
        c.session,
        Query.css("#task-description:not([inert]) [contenteditable='true']",
          text: "Final character!"
        )
      )

      wait(c.session, "return location.pathname + location.search === #{Jason.encode!(origin)}")
      assert editor_id(c.session) == initial_id
      assert Tasks.get_task_for_project(c.project, target.id).description == ""

      if unquote(departure) == :back do
        Browser.find(
          c.session,
          Query.css("#task-comment-departure-guard[data-description-capture-pending='true']",
            visible: :any
          )
        )

        js(c.session, "history.back()")
        wait(c.session, "return location.pathname + location.search === #{Jason.encode!(origin)}")
        assert editor_id(c.session) == initial_id
      end

      if unquote(departure) == :close do
        chrome_height = js(c.session, "return outerHeight - innerHeight")
        Browser.resize_window(c.session, 320, 720 + chrome_height)
        settle_layout(c.session)

        if js(c.session, "return document.querySelector('#task-detail-content').inert") do
          Browser.click(c.session, Query.css("#task-hierarchy-toggle"))
        end

        assert js(
                 c.session,
                 "return {viewport:innerWidth,scrollWidth:document.documentElement.scrollWidth}"
               ) == %{"viewport" => 320, "scrollWidth" => 320}
      end

      js(c.session, "window.descriptionDropFlush = false; window.descriptionHold = false")

      js(
        c.session,
        "document.querySelector('#retry-description-capture').scrollIntoView({block:'center'})"
      )

      settle_layout(c.session)
      Browser.take_screenshot(c.session, name: "description-retry-#{unquote(departure)}")

      assert js(
               c.session,
               "const e=document.querySelector('#retry-description-capture'); const r=e.getBoundingClientRect(); return e.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2))"
             )

      Browser.click(c.session, Query.css("#retry-description-capture"))

      if unquote(departure) == :back do
        wait(
          c.session,
          "return document.querySelector('#task-detail-discussion')?.dataset.taskId === '#{c.task.id}'"
        )
      else
        Browser.find(c.session, Query.css("#task-modal", count: 0))
      end

      assert Tasks.get_task_for_project(c.project, target.id).description == "Final character!"

      expected =
        if unquote(departure) == :back,
          do: "/projects/#{c.project.id}/tasks/#{c.task.id}?probe=retained%20query",
          else: "/projects/#{c.project.id}"

      assert js(c.session, "return location.pathname + location.search") == expected
    end
  end

  test "native clipboard paste preserves plain text and sanitized rich formatting through reload",
       c do
    visit(c, c.task)
    type_description(c.session, "Start ")
    clipboard(c.session, "Plain literal snake_case!", nil)
    Browser.send_keys(c.session, [:control, "v", :null])
    Browser.find(c.session, Query.css("[data-editor-visual]", text: "Plain literal snake_case!"))
    Browser.send_keys(c.session, [:enter])

    clipboard(
      c.session,
      "Rich bold and italic",
      "<p><strong>Rich bold</strong> and <em>italic</em></p><script>window.markdownProbe=7</script><img src=x onerror=window.markdownProbe=8>"
    )

    Browser.send_keys(c.session, [:control, "v", :null])

    Browser.find(
      c.session,
      Query.css("[data-editor-visual] strong, [data-editor-visual] b", text: "Rich bold")
    )

    assert_safe(c.session)
    depart(c.session, "document.querySelector('#task-modal-close').click()")
    Browser.find(c.session, Query.css("#task-modal", count: 0))
    markdown = Tasks.get_task_for_project(c.project, c.task.id).description
    assert markdown =~ "Plain literal snake_case!"
    assert markdown =~ "**Rich bold**"
    assert markdown =~ "*italic*"
    Browser.click(c.session, Query.css("#open-task-#{c.task.id}"))
    ready(c.session)
    Browser.find(c.session, Query.css("[data-editor-visual] strong", text: "Rich bold"))
    assert_safe(c.session)
  end

  test "desktop and true 320 CSS pixel toolbars keep source and formatting reachable without overflow",
       c do
    visit(c, c.task)

    for {width, height} <- [{1440, 900}, {320, 720}] do
      chrome_height = js(c.session, "return outerHeight - innerHeight")
      Browser.resize_window(c.session, width, height + chrome_height)
      wait(c.session, "return innerWidth === #{width} && innerHeight === #{height}")

      settle_layout(c.session)

      if width == 320 &&
           js(c.session, "return document.querySelector('#task-detail-content').inert") do
        Browser.click(c.session, Query.css("#task-hierarchy-toggle"))
        Browser.find(c.session, Query.css("#task-detail-content:not([inert])"))
      end

      assert js(
               c.session,
               "return {viewport:innerWidth,scrollWidth:document.documentElement.scrollWidth}"
             ) == %{"viewport" => width, "scrollWidth" => width}

      mode(c.session, "markdown")
      Browser.find(c.session, Query.css("[data-markdown-wrapper] textarea"))
      mode(c.session, "hybrid")
      type_description(c.session, "Reachable")
      Browser.send_keys(c.session, [:home, :shift, :end, :null])
      Browser.click(c.session, Query.css("[data-toolbar-action='bold'][title='Bold']"))

      Browser.find(
        c.session,
        Query.css("[data-editor-visual] strong, [data-editor-visual] b", text: "Reachable")
      )

      assert js(
               c.session,
               "return {viewport:innerWidth,scrollWidth:document.documentElement.scrollWidth}"
             ) == %{"viewport" => width, "scrollWidth" => width}

      Browser.take_screenshot(c.session, name: "description-editor-#{width}")

      if width == 320 do
        Browser.click(c.session, Query.css("[data-mobile-tools-menu] summary"))
        settle_layout(c.session)

        for action <- [
              "heading1",
              "strike",
              "code",
              "orderedList",
              "taskList",
              "blockquote",
              "codeBlock",
              "table"
            ] do
          assert js(
                   c.session,
                   """
                   const button = document.querySelector('[data-mobile-tools-menu] [data-toolbar-action="' + arguments[0] + '"]');
                   button.focus(); button.scrollIntoView({block:'nearest'});
                   const r = button.getBoundingClientRect();
                   return r.left >= 0 && r.right <= innerWidth && r.top >= 0 && r.bottom <= innerHeight && button.contains(document.elementFromPoint(r.x+r.width/2, r.y+r.height/2));
                   """,
                   [action]
                 ),
                 "unreachable toolbar action: #{action}"
        end

        Browser.take_screenshot(c.session, name: "description-tools-320")
        Browser.click(c.session, Query.css("[data-mobile-tools-menu] summary"))

        assert js(
                 c.session,
                 "return {viewport:innerWidth,scrollWidth:document.documentElement.scrollWidth}"
               ) == %{"viewport" => width, "scrollWidth" => width}
      end

      # Reset through actual editing before checking the next viewport.
      Browser.click(c.session, Query.css("[data-editor-visual]"))
      Browser.send_keys(c.session, [:control, "a", :null, :backspace])
    end
  end

  defp settle_layout(session) do
    js(
      session,
      "window.descriptionLayoutReady=false; requestAnimationFrame(()=>requestAnimationFrame(()=>window.descriptionLayoutReady=true))"
    )

    wait(session, "return window.descriptionLayoutReady")
  end

  defp clipboard(session, plain, html) do
    js(
      session,
      """
      window.descriptionClipboardReady = false;
      window.descriptionClipboardError = null;
      const data = {'text/plain': new Blob([arguments[0]], {type:'text/plain'})};
      if (arguments[1]) data['text/html'] = new Blob([arguments[1]], {type:'text/html'});
      navigator.clipboard.write([new ClipboardItem(data)]).then(
        () => window.descriptionClipboardReady = true,
        error => window.descriptionClipboardError = error.message);
      """,
      [plain, html]
    )

    wait(session, "return window.descriptionClipboardReady || window.descriptionClipboardError")
    assert js(session, "return window.descriptionClipboardError") == nil
  end

  defp assert_safe(session) do
    assert js(session, "return window.markdownProbe || 0") == 0

    assert js(
             session,
             "return [...document.querySelector('[data-editor-visual]').querySelectorAll('*')].every(e => e.tagName !== 'SCRIPT' && [...e.attributes].every(a => !a.name.startsWith('on') && !(/^(href|src)$/i.test(a.name) && /^javascript:/i.test(a.value))))"
           )
  end

  defp assert_common_formats(session) do
    for selector <- [
          "h1",
          "strong",
          "em",
          "code",
          "ul ul li",
          "li[data-checked='false'] .leaf-task-box",
          "li[data-checked='true'] .leaf-task-box",
          "a[href='https://example.test/guide']",
          "pre code"
        ] do
      Browser.find(
        session,
        Query.css("[data-editor-visual] " <> selector, minimum: 1, visible: :any)
      )
    end
  end

  defp mode(session, value) do
    settle_layout(session)

    inline? =
      js(
        session,
        "const e=document.querySelector('[data-mode-switcher=inline] [data-mode-tab=' + arguments[0] + ']'); return !!e && e.checkVisibility({checkVisibilityCSS: true})",
        [value]
      )

    if inline? do
      Browser.click(
        session,
        Query.css("[data-mode-switcher='inline'] [data-mode-tab='#{value}']")
      )
    else
      Browser.click(session, Query.css("summary[title='More editor options']"))

      Browser.click(session, Query.css("[data-mode-tab='#{value}']"))
    end
  end

  defp visit(c, task) do
    Browser.visit(c.session, c.base <> "/projects/#{c.project.id}/tasks/#{task.id}")
    ready(c.session)
  end

  defp ready(session),
    do: Browser.find(session, Query.css("[data-editor-id][data-leaf-mount-state='ready']"))

  defp type_description(session, value) do
    Browser.click(session, Query.css("[data-editor-visual]"))
    Browser.send_keys(session, [value])
  end

  defp editor_id(session),
    do: js(session, "return document.querySelector('[data-editor-id]').dataset.editorId")

  defp hold_changes(session) do
    js(session, """
    if (!window.descriptionOriginalSend) {
      window.descriptionOriginalSend = WebSocket.prototype.send;
      window.descriptionHeld = [];
      WebSocket.prototype.send = function(data) {
        if (window.descriptionHold && typeof data === 'string' && (data.includes('"content_changed"') || (window.descriptionDropFlush && data.includes('"flushed"')))) {
          window.descriptionHeld.push([this, data]); return;
        }
        return window.descriptionOriginalSend.call(this, data);
      };
    }
    window.descriptionHold = true;
    """)
  end

  defp depart(session, script), do: js(session, "window.descriptionHold = false; " <> script)

  defp held_count(session), do: js(session, "return window.descriptionHeld.length")

  defp release_changes(session) do
    js(session, """
    window.descriptionHold = false;
    for (const [socket, data] of window.descriptionHeld.splice(0).slice(0, 1)) {
      if (socket.readyState === WebSocket.OPEN) window.descriptionOriginalSend.call(socket, data);
    }
    """)
  end

  defp caret(session),
    do:
      js(
        session,
        "const s=getSelection(); return {focus:s.focusNode?.textContent,offset:s.focusOffset,active:document.activeElement.id}"
      )

  defp wait(session, script),
    do: wait(session, script, System.monotonic_time(:millisecond) + 5_000)

  defp wait(session, script, deadline) do
    cond do
      js(session, script) ->
        :ok

      System.monotonic_time(:millisecond) < deadline ->
        wait(session, script, deadline)

      true ->
        flunk(
          "browser condition did not become true: #{script}; #{inspect(js(session, "return {path:location.pathname,held:window.descriptionHeld?.length,hold:window.descriptionHold,title:document.querySelector('#task-title')?.value,guard:document.querySelector('#task-comment-departure-guard')?.dataset, error:document.querySelector('#description-capture-error')?.textContent, body:document.querySelector('#task-modal')?.innerText.slice(-600)}"))}"
        )
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
