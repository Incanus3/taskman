defmodule TaskmanWeb.DescriptionEditorHelpers do
  @moduledoc false
  import Phoenix.LiveViewTest

  def change_description(view, markdown) do
    view
    |> with_target("[data-description-editor] [phx-hook='Leaf']")
    |> render_hook("content_changed", %{
      "markdown" => markdown,
      "html" => Leaf.Markdown.markdown_to_html(markdown),
      "dirty" => true
    })

    _ = :sys.get_state(view.pid)
    render(view)
  end

  # LiveViewTest runs no browser hooks. Complete the two public Leaf events in
  # their package order using the simulated browser's current Markdown.
  def complete_description_capture(view) do
    socket = :sys.get_state(view.pid).socket

    case Map.get(socket.assigns, :description, %{}) |> Map.get(:pending_capture) do
      %{ref: ref, timed_out?: false} ->
        {markdown, dirty?} = captured_value(socket)

        render_hook(view, "capture_task_departure_comment", %{
          "editor_id" => socket.assigns.description.editor_id,
          "ref" => ref,
          "draft" => socket.assigns.comments.draft
        })

        target = with_target(view, "[data-description-editor] [phx-hook='Leaf']")

        render_hook(target, "content_changed", %{
          "markdown" => markdown,
          "html" => Leaf.Markdown.markdown_to_html(markdown),
          "dirty" => dirty?
        })

        render_hook(target, "flushed", %{
          "ref" => ref,
          "markdown" => markdown,
          "html" => Leaf.Markdown.markdown_to_html(markdown)
        })

      _ ->
        :ok
    end

    view
  end

  def has_captured_element?(view, selector, text \\ nil) do
    render_captured(view)
    has_element?(view, selector, text)
  end

  def render_captured(view) do
    Phoenix.LiveViewTest.render(view)
    complete_description_capture(view)
    Phoenix.LiveViewTest.render(view)
  end

  def render_captured_click(view, params \\ %{}),
    do: captured_result(view, fn -> render_click(view, params) end)

  def render_captured_hook(view, event, params \\ %{}),
    do: captured_result(view, fn -> render_hook(view, event, params) end)

  def render_captured_patch(view, path),
    do: captured_result(view, fn -> render_patch(view, path) end)

  def render_captured_change(view, params \\ %{}),
    do: captured_result(view, fn -> render_change(view, params) end)

  def render_captured_submit(view, params \\ %{}),
    do: captured_result(view, fn -> render_submit(view, params) end)

  def render_captured_click(view, event, params),
    do: captured_result(view, fn -> render_click(view, event, params) end)

  def render_captured_change(view, event, params),
    do: captured_result(view, fn -> render_change(view, event, params) end)

  def render_captured_submit(view, event, params),
    do: captured_result(view, fn -> render_submit(view, event, params) end)

  defp captured_result(target, action) do
    result = action.()

    if is_binary(result) do
      view =
        case target do
          %Phoenix.LiveViewTest.Element{proxy: {_, _, pid} = proxy} ->
            {_, root} = Phoenix.LiveViewTest.ClientProxy.root_view(pid)

            %Phoenix.LiveViewTest.View{
              id: root.id,
              module: root.module,
              pid: root.pid,
              endpoint: root.endpoint,
              proxy: proxy
            }

          view ->
            view
        end

      complete_description_capture(view)
      Phoenix.LiveViewTest.render(view)
    else
      result
    end
  end

  def complete_socket_capture({:noreply, socket}) do
    case Map.get(socket.assigns, :description, %{}) |> Map.get(:pending_capture) do
      %{ref: ref} ->
        {markdown, dirty?} = captured_value(socket)
        id = socket.assigns.description.editor_id

        {:noreply, socket} =
          TaskmanWeb.ProjectLive.handle_event(
            "capture_task_departure_comment",
            %{"editor_id" => id, "ref" => ref, "draft" => socket.assigns.comments.draft},
            socket
          )

        {:noreply, socket} =
          TaskmanWeb.ProjectLive.handle_info(
            {:leaf_changed, %{editor_id: id, markdown: markdown, dirty: dirty?}},
            socket
          )

        TaskmanWeb.ProjectLive.handle_info(
          {:leaf_flushed, %{editor_id: id, ref: ref, markdown: markdown}},
          socket
        )

      _ ->
        {:noreply, socket}
    end
  end

  defp captured_value(socket) do
    description = socket.assigns.description

    value =
      case description.context do
        {:new, _} -> socket.assigns.creation.form[:description].value || ""
        {:edit, _, _} -> socket.assigns.editing.autosave.form[:description].value || ""
      end

    case description.pending_capture do
      %{metadata: %{markdown: markdown, dirty: dirty?}} -> {markdown, dirty?}
      _ -> {value, description.edited?}
    end
  end
end
