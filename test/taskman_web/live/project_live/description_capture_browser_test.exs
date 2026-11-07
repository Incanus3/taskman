defmodule TaskmanWeb.ProjectLive.DescriptionCaptureBrowserTest do
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

  test "immediate Close captures typed Markdown after freezing the editor", c do
    id = js(c.session, "return document.querySelector('[data-editor-id]').dataset.editorId")

    js(
      c.session,
      """
        window.descriptionFlushes = [];
        window.addEventListener('phx:leaf-command:' + arguments[0], event => {
          if (event.detail.action !== 'flush') return;
          const editor = document.querySelector('[data-editor-id]');
          document.activeElement.blur();
          editor.querySelector('[data-editor-visual]').focus();
          window.descriptionFlushes.push({
            inert: document.querySelector('[data-description-editor]').inert,
            focused: editor.contains(document.activeElement),
            ref: event.detail.ref
          });
        });
      """,
      [id]
    )

    Browser.click(c.session, Query.css("[data-editor-visual] > p:first-child"))
    Browser.send_keys(c.session, [:end, "!"])
    js(c.session, "document.querySelector('#task-modal-close').click()")
    Browser.find(c.session, Query.css("#task-modal", count: 0))
    assert Tasks.get_task_for_project(c.project, c.task.id).description == "Original!"

    assert [%{"inert" => true, "focused" => false, "ref" => ref}] =
             js(c.session, "return window.descriptionFlushes")

    assert is_binary(ref)
    assert js(c.session, "return location.pathname") == "/projects/#{c.project.id}"
  end

  for initial <- ["", "  Original comment  "] do
    test "capture snapshots unsent comment after an initial #{inspect(initial)} request", c do
      initial = unquote(initial)
      current = "  Unsent browser comment!\nPreserve exact spacing.  "

      js(
        c.session,
        """
          const comment = document.querySelector('#task-comment-text');
          comment.value = arguments[0];
          comment.dispatchEvent(new Event('input', {bubbles: true}));
          window.commentBoundaryInjected = false;
          const injectCurrentComment = () => {
            if (!document.querySelector('[data-description-editor]')?.inert) return;
            document.querySelector('#task-comment-text').value = arguments[1];
            window.commentBoundaryInjected = true;
            document.removeEventListener('phx:update', injectCurrentComment);
          };
          document.addEventListener('phx:update', injectCurrentComment);
          document.querySelector('#task-modal-close').click();
        """,
        [initial, current]
      )

      # The public pending patch supplies B after the request, before the
      # Leaf flush. No input event sends B to ordinary validation.
      Browser.find(c.session, Query.css("#task-comment-departure"))
      assert js(c.session, "return window.commentBoundaryInjected")
      assert js(c.session, "return document.querySelector('#task-comment-text').value") == current

      assert js(c.session, "return location.pathname") ==
               "/projects/#{c.project.id}/tasks/#{c.task.id}"
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
