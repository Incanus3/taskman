defmodule TaskmanWeb.GlobalTaskSearchBrowserTest do
  use TaskmanWeb.ConnCase, async: false

  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.Accounts
  alias Wallaby.{Browser, Query}

  @moduletag :browser

  setup do
    password = "browser-test-password"
    email = "browser-search-#{Ecto.UUID.generate()}@example.com"
    assert {:ok, _user} = Accounts.bootstrap_admin(email, password)
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Toolbar search target"})

    server =
      start_supervised!(
        {Bandit, plug: TaskmanWeb.Endpoint, port: 0, ip: {127, 0, 0, 1}, startup_log: false}
      )

    assert {:ok, {_address, port}} = ThousandIsland.listener_info(server)
    base_url = "http://127.0.0.1:#{port}"
    {:ok, _started} = Application.ensure_all_started(:wallaby)

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

    Browser.find(session, Query.css("#global-task-search-input"))
    Browser.visit(session, "#{base_url}/projects/#{project.id}")
    Browser.find(session, Query.css("[data-phx-main].phx-connected #main-panel"))

    # An inert pointer target below both search surfaces avoids covered headings or Task links.
    Browser.execute_script(session, ~S"""
    const outside = document.createElement('div');
    outside.id = 'search-outside-area';
    outside.style = 'position:fixed;bottom:0;right:0;width:40px;height:40px;z-index:100';
    document.body.appendChild(outside);
    """)

    {:ok, session: session, task: task, project: project}
  end

  @tag :mounted_search
  test "Task modals retain search nodes and navbar height while blocking native interaction", %{
    session: session,
    task: task
  } do
    for width <- [1440, 390] do
      Browser.resize_window(session, width, 800)

      height =
        js(
          session,
          """
          window.retainedSearch = document.getElementById('global-task-search');
          window.retainedSearchInput = document.getElementById('global-task-search-input');
          window.retainedSearchTrigger = document.getElementById('global-task-search-mobile-trigger');
          return document.getElementById('authenticated-navigation').getBoundingClientRect().height;
          """,
          []
        )

      for opener <- ["#add-task", "#open-task-#{task.id}"] do
        Browser.click(session, Query.css(opener))
        Browser.find(session, Query.css("#task-modal #task-form"))

        assert js(
                 session,
                 "return document.getElementById('authenticated-navigation').getBoundingClientRect().height",
                 []
               ) == height

        assert js(
                 session,
                 """
                 return window.retainedSearch === document.getElementById('global-task-search') &&
                   window.retainedSearchInput === document.getElementById('global-task-search-input') &&
                   window.retainedSearchTrigger === document.getElementById('global-task-search-mobile-trigger');
                 """,
                 []
               )

        Browser.find(session, Query.css("#global-task-search[inert]", visible: :any))

        control_id =
          if width == 1440,
            do: "global-task-search-input",
            else: "global-task-search-mobile-trigger"

        assert js(
                 session,
                 """
                 const search = document.getElementById('global-task-search');
                 const control = document.getElementById(arguments[0]);
                 control.focus();
                 const bounds = control.getBoundingClientRect();
                 const pointerTarget = document.elementFromPoint(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
                 return !search.contains(document.activeElement) && !search.contains(pointerTarget);
                 """,
                 [control_id]
               )

        # The search controls lie between Administration and Account settings in tab order.
        js(session, "document.getElementById('account-administration-link').focus()", [])
        Browser.send_keys(session, [:tab])
        Browser.find(session, Query.css("#account-settings-link:focus"))

        Browser.click(session, Query.css("#task-modal-close"))
        Browser.find(session, Query.css("#global-task-search:not([inert])", visible: :any))

        assert js(
                 session,
                 "return document.getElementById('authenticated-navigation').getBoundingClientRect().height",
                 []
               ) == height

        assert js(
                 session,
                 "return window.retainedSearch === document.getElementById('global-task-search') && window.retainedSearchInput === document.getElementById('global-task-search-input') && window.retainedSearchTrigger === document.getElementById('global-task-search-mobile-trigger')",
                 []
               )
      end

      prefix = if width == 1440, do: "global-task-search", else: "global-task-search-mobile"

      if width == 390 do
        Browser.click(session, Query.css("#global-task-search-mobile-trigger"))
      end

      Browser.fill_in(session, Query.css("##{prefix}-input"), with: "Toolbar search")
      Browser.find(session, Query.css("##{prefix}-option-#{task.id}"))
      Browser.send_keys(session, [:escape])
      Browser.find(session, Query.css("##{prefix}-suggestions", count: 0))
    end
  end

  test "keyboard selection visibly follows arrow navigation on desktop and mobile", %{
    session: session,
    project: project,
    task: task
  } do
    other = task_fixture(project, %{title: "Toolbar search second target"})

    for {width, input_id, results_id} <- [
          {1440, "global-task-search-input", "global-task-search-results"},
          {390, "global-task-search-mobile-input", "global-task-search-mobile-results"}
        ] do
      Browser.resize_window(session, width, 800)

      if width == 390 do
        Browser.click(session, Query.css("#global-task-search-mobile-trigger"))
      end

      Browser.fill_in(session, Query.css("##{input_id}"), with: "Toolbar search")
      Browser.find(session, Query.css("##{results_id} [role='option']", count: 2))

      [first, second] =
        js(
          session,
          "return Array.from(document.getElementById(arguments[0]).children, row => row.id)",
          [results_id]
        )

      prefix = String.replace_suffix(results_id, "results", "option")

      assert Enum.sort([first, second]) ==
               Enum.sort(["#{prefix}-#{task.id}", "#{prefix}-#{other.id}"])

      first_idle = background(session, first)
      second_idle = background(session, second)

      Browser.send_keys(session, [:down_arrow])
      Browser.find(session, Query.css("##{first}[aria-selected='true']"))
      first_active = background(session, first)
      refute first_active == first_idle
      assert background(session, second) == second_idle
      Browser.find(session, Query.css("##{input_id}:focus[aria-activedescendant='#{first}']"))

      Browser.send_keys(session, [:down_arrow])
      Browser.find(session, Query.css("##{second}[aria-selected='true']"))
      refute background(session, second) == second_idle
      assert background(session, first) == first_idle

      Browser.send_keys(session, [:up_arrow])
      Browser.find(session, Query.css("##{first}[aria-selected='true']"))
      assert background(session, first) == first_active
      assert background(session, second) == second_idle
      Browser.find(session, Query.css("##{input_id}:focus[aria-activedescendant='#{first}']"))

      Browser.send_keys(session, [:escape])

      if width == 390 do
        Browser.find(session, Query.css("#global-task-search-mobile-trigger:focus"))
      end
    end
  end

  test "arrow navigation keeps overflowing results visible without moving input focus", %{
    session: session,
    project: project
  } do
    for index <- 1..20 do
      task_fixture(project, %{title: "Overflow search target #{index}"})
    end

    project_url = Browser.current_url(session)

    for {width, input_id, results_id, panel_id} <- [
          {1440, "global-task-search-input", "global-task-search-results",
           "global-task-search-suggestions"},
          {390, "global-task-search-mobile-input", "global-task-search-mobile-results",
           "global-task-search-mobile-suggestions"}
        ] do
      Browser.resize_window(session, width, 800)

      if width == 390 do
        Browser.click(session, Query.css("#global-task-search-mobile-trigger"))
      end

      Browser.fill_in(session, Query.css("##{input_id}"), with: "Overflow search")
      Browser.find(session, Query.css("##{results_id} [role='option']", count: 20))

      rows =
        js(
          session,
          "return Array.from(document.getElementById(arguments[0]).children, row => row.id)",
          [
            results_id
          ]
        )

      assert js(
               session,
               "const panel = document.getElementById(arguments[0]); return panel.scrollHeight > panel.clientHeight",
               [panel_id]
             )

      for row <- rows do
        Browser.send_keys(session, [:down_arrow])
        assert_selected_visible(session, input_id, panel_id, row)
      end

      assert js(session, "return document.getElementById(arguments[0]).scrollTop", [panel_id]) > 0

      for row <- rows |> Enum.reverse() |> Enum.drop(1) do
        Browser.send_keys(session, [:up_arrow])
        assert_selected_visible(session, input_id, panel_id, row)
      end

      # Wrap upward to the last row, then open that selected Task.
      Browser.send_keys(session, [:up_arrow])
      selected = List.last(rows)
      assert_selected_visible(session, input_id, panel_id, selected)
      Browser.send_keys(session, [:enter])
      Browser.find(session, Query.css("#task-modal"))
      task_id = selected |> String.split("-") |> List.last()
      assert Browser.current_url(session) =~ "/tasks/#{task_id}"

      Browser.visit(session, project_url)
      Browser.find(session, Query.css("[data-phx-main].phx-connected #main-panel"))
    end
  end

  for {surface, width, prefix} <- [
        {:desktop, 1440, "global-task-search"},
        {:mobile, 390, "global-task-search-mobile"}
      ] do
    test "Escape dismisses #{surface} search from a Tab-focused result", %{
      session: session,
      task: task
    } do
      Browser.resize_window(session, unquote(width), 800)
      prefix = unquote(prefix)

      if unquote(surface) == :mobile do
        Browser.click(session, Query.css("#global-task-search-mobile-trigger"))
      end

      Browser.fill_in(session, Query.css("##{prefix}-input"), with: "Toolbar search")
      Browser.find(session, Query.css("##{prefix}-option-#{task.id}"))
      Browser.send_keys(session, [:tab])
      Browser.find(session, Query.css("##{prefix}-option-#{task.id}:focus"))
      Browser.send_keys(session, [:escape])
      assert Browser.has?(session, Query.css("##{prefix}-suggestions", count: 0))

      if unquote(surface) == :mobile do
        assert Browser.has?(session, Query.css("#global-task-search-mobile-panel", count: 0))
        Browser.find(session, Query.css("#global-task-search-mobile-trigger:focus"))
        Browser.click(session, Query.css("#global-task-search-mobile-trigger"))
      end

      # Enter on a focused result still uses native button activation.
      Browser.fill_in(session, Query.css("##{prefix}-input"), with: "Toolbar search")
      Browser.find(session, Query.css("##{prefix}-option-#{task.id}"))
      Browser.send_keys(session, [:tab])
      Browser.find(session, Query.css("##{prefix}-option-#{task.id}:focus"))
      Browser.send_keys(session, [:enter])
      Browser.find(session, Query.css("#task-modal"))
      assert Browser.current_url(session) =~ "/tasks/#{task.id}"
    end

    @tag :capture_log
    test "Escape dismisses #{surface} search from the real error retry control", %{
      session: session
    } do
      # The connected Project LiveView already subscribes to workspace changes.
      # Reuse its component injection seam without a production test route.
      [{view_pid, _metadata}] = Registry.lookup(Taskman.PubSub, "workspace:changes")

      Phoenix.LiveView.send_update(view_pid, TaskmanWeb.GlobalTaskSearch,
        id: "global-task-search",
        search_tasks: fn _query -> raise "search unavailable" end
      )

      _ = :sys.get_state(view_pid)
      Browser.resize_window(session, unquote(width), 800)
      prefix = unquote(prefix)

      if unquote(surface) == :mobile do
        Browser.click(session, Query.css("#global-task-search-mobile-trigger"))
      end

      Browser.fill_in(session, Query.css("##{prefix}-input"), with: "Toolbar search")
      Browser.find(session, Query.css("##{prefix}-error[role='alert'] ##{prefix}-retry"))
      Browser.send_keys(session, [:tab])
      Browser.find(session, Query.css("##{prefix}-retry:focus"))

      Browser.send_keys(session, [:escape])
      assert Browser.has?(session, Query.css("##{prefix}-suggestions", count: 0))

      if unquote(surface) == :mobile do
        assert Browser.has?(session, Query.css("#global-task-search-mobile-panel", count: 0))
        Browser.find(session, Query.css("#global-task-search-mobile-trigger:focus"))
      end
    end
  end

  for {surface, width, prefix} <- [
        {:desktop, 1440, "global-task-search"},
        {:mobile, 390, "global-task-search-mobile"}
      ],
      activation <- [:keyboard, :pointer] do
    @tag :capture_log
    @tag :retry_visibility
    test "successful #{surface} Retry keeps results visible after #{activation} activation", %{
      session: session,
      task: task
    } do
      [{view_pid, _metadata}] = Registry.lookup(Taskman.PubSub, "workspace:changes")

      availability = start_supervised!({Agent, fn -> false end})

      Phoenix.LiveView.send_update(view_pid, TaskmanWeb.GlobalTaskSearch,
        id: "global-task-search",
        search_tasks: fn query ->
          if Agent.get(availability, & &1) do
            Taskman.Tasks.search_tasks(query)
          else
            raise "search unavailable"
          end
        end
      )

      _ = :sys.get_state(view_pid)
      Browser.resize_window(session, unquote(width), 800)
      prefix = unquote(prefix)

      if unquote(surface) == :mobile do
        Browser.click(session, Query.css("#global-task-search-mobile-trigger"))
      end

      Browser.fill_in(session, Query.css("##{prefix}-input"), with: "Toolbar search")
      Browser.find(session, Query.css("##{prefix}-error[role='alert'] ##{prefix}-retry"))
      Browser.send_keys(session, [:tab])
      Browser.find(session, Query.css("##{prefix}-retry:focus"))

      Agent.update(availability, fn _ -> true end)
      Browser.find(session, Query.css("##{prefix}-retry:focus"))
      project_url = Browser.current_url(session)

      if unquote(activation) == :keyboard do
        Browser.send_keys(session, [:enter])
      else
        Browser.click(session, Query.css("##{prefix}-retry"))
      end

      Browser.find(session, Query.css("##{prefix}-option-#{task.id}"))
      assert Browser.current_url(session) == project_url
      Browser.find(session, Query.css("##{prefix}-input:focus[value='Toolbar search']"))
    end
  end

  for {surface, width, prefix} <- [
        {:desktop, 1440, "global-task-search"},
        {:mobile, 390, "global-task-search-mobile"}
      ] do
    @tag :capture_log
    test "a repeated #{surface} Retry failure preserves error and query with input focus", %{
      session: session
    } do
      [{view_pid, _metadata}] = Registry.lookup(Taskman.PubSub, "workspace:changes")

      Phoenix.LiveView.send_update(view_pid, TaskmanWeb.GlobalTaskSearch,
        id: "global-task-search",
        search_tasks: fn _query -> raise "search unavailable" end
      )

      _ = :sys.get_state(view_pid)
      Browser.resize_window(session, unquote(width), 800)
      prefix = unquote(prefix)

      if unquote(surface) == :mobile do
        Browser.click(session, Query.css("#global-task-search-mobile-trigger"))
      end

      Browser.fill_in(session, Query.css("##{prefix}-input"), with: "Toolbar search")
      Browser.find(session, Query.css("##{prefix}-error[role='alert'] ##{prefix}-retry"))
      Browser.send_keys(session, [:tab])
      Browser.find(session, Query.css("##{prefix}-retry:focus"))
      Browser.send_keys(session, [:enter])
      Browser.find(session, Query.css("##{prefix}-input:focus[value='Toolbar search']"))
      Browser.find(session, Query.css("##{prefix}-error[role='alert']"))
      Browser.send_keys(session, [:escape])
      assert Browser.has?(session, Query.css("##{prefix}-suggestions", count: 0))

      if unquote(surface) == :mobile do
        assert Browser.has?(session, Query.css("#global-task-search-mobile-panel", count: 0))
        Browser.find(session, Query.css("#global-task-search-mobile-trigger:focus"))
      end
    end
  end

  test "Escape on the narrow search close button restores trigger focus", %{session: session} do
    Browser.resize_window(session, 390, 800)
    Browser.click(session, Query.css("#global-task-search-mobile-trigger"))
    Browser.find(session, Query.css("#global-task-search-mobile-input:focus"))
    Browser.send_keys(session, [:tab])
    Browser.find(session, Query.css("#global-task-search-mobile-close:focus"))
    Browser.send_keys(session, [:escape])
    assert Browser.has?(session, Query.css("#global-task-search-mobile-panel", count: 0))
    Browser.find(session, Query.css("#global-task-search-mobile-trigger:focus"))
  end

  test "desktop suggestions close when focus leaves and results remain clickable", %{
    session: session,
    task: task
  } do
    assert Browser.has?(session, Query.css("#global-task-search-suggestions", count: 0))
    Browser.fill_in(session, Query.css("#global-task-search-input"), with: "Toolbar search")
    Browser.find(session, Query.css("#global-task-search-option-#{task.id}"))

    # Keyboard traversal can focus a result without closing the search.
    Browser.send_keys(session, [:tab])
    Browser.find(session, Query.css("#global-task-search-option-#{task.id}:focus"))
    Browser.send_keys(session, [:shift, :tab, :null])
    Browser.send_keys(session, [:shift, :tab, :null])
    assert Browser.has?(session, Query.css("#global-task-search-suggestions", count: 0))

    Browser.fill_in(session, Query.css("#global-task-search-input"), with: "Toolbar search")
    Browser.click(session, Query.css("#global-task-search-option-#{task.id}"))
    Browser.find(session, Query.css("#task-modal"))
  end

  test "outside clicks dismiss the mobile panel and suggestions after widening", %{
    session: session,
    task: task
  } do
    Browser.resize_window(session, 1023, 800)
    Browser.find(session, Query.css("#project-sidebar-toggle"))
    Browser.click(session, Query.css("#global-task-search-mobile-trigger"))

    Browser.fill_in(session, Query.css("#global-task-search-mobile-input"),
      with: "Toolbar search"
    )

    Browser.find(session, Query.css("#global-task-search-mobile-option-#{task.id}"))
    Browser.click(session, Query.css("#search-outside-area"))
    assert Browser.has?(session, Query.css("#global-task-search-mobile-panel", count: 0))

    refute js(session, "return document.activeElement.id", []) ==
             "global-task-search-mobile-trigger"

    Browser.click(session, Query.css("#global-task-search-mobile-trigger"))
    Browser.find(session, Query.css("#global-task-search-mobile-prompt"))
    Browser.resize_window(session, 1024, 800)
    Browser.find(session, Query.css("#global-task-search-input"))
    assert Browser.has?(session, Query.css("#project-sidebar-toggle", count: 0))
    Browser.click(session, Query.css("#search-outside-area"))

    assert Browser.has?(
             session,
             Query.css("#global-task-search-mobile-panel", count: 0, visible: :any)
           )

    assert Browser.has?(session, Query.css("#global-task-search-suggestions", count: 0))
    Browser.click(session, Query.css("#global-task-search-input"))
    Browser.find(session, Query.css("#global-task-search-prompt"))
    Browser.click(session, Query.css("#search-outside-area"))
    assert Browser.has?(session, Query.css("#global-task-search-suggestions", count: 0))
  end

  defp assert_selected_visible(session, input_id, panel_id, row_id) do
    Browser.find(session, Query.css("##{input_id}:focus[aria-activedescendant='#{row_id}']"))
    owner = self()
    ref = make_ref()

    Browser.execute_script_async(
      session,
      """
      const [inputId, panelId, rowId, done] = arguments;
      const deadline = performance.now() + 1000;
      const check = () => {
        const input = document.getElementById(inputId);
        const panel = document.getElementById(panelId);
        const row = document.getElementById(rowId);
        const bounds = panel.getBoundingClientRect();
        const result = row.getBoundingClientRect();
        const visible = result.top >= bounds.top + panel.clientTop &&
          result.bottom <= bounds.top + panel.clientTop + panel.clientHeight &&
          result.top >= 0 && result.bottom <= window.innerHeight;
        const state = {visible, focused: document.activeElement === input,
          scrollTop: panel.scrollTop, rowTop: result.top, rowBottom: result.bottom,
          panelTop: bounds.top, panelBottom: bounds.bottom};
        if (visible || performance.now() >= deadline) done(state);
        else requestAnimationFrame(check);
      };
      check();
      """,
      [input_id, panel_id, row_id],
      fn result -> send(owner, {ref, result}) end
    )

    assert_receive {^ref, state}, 2000
    assert state["visible"], "Selected result is outside the scroll panel: #{inspect(state)}"
    assert state["focused"]
  end

  defp background(session, id) do
    js(
      session,
      """
      const row = document.getElementById(arguments[0]);
      // Finish the color transition before comparing its settled appearance.
      row.getAnimations().forEach(animation => animation.finish());
      return getComputedStyle(row).backgroundColor;
      """,
      [id]
    )
  end

  defp js(session, script, args) do
    owner = self()
    ref = make_ref()
    Browser.execute_script(session, script, args, fn result -> send(owner, {ref, result}) end)
    assert_receive {^ref, result}
    result
  end
end
