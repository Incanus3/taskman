defmodule TaskmanWeb.CoreComponentsTest do
  use ExUnit.Case, async: true

  use Phoenix.Component

  import Phoenix.LiveViewTest

  alias Phoenix.LiveView.JS
  alias TaskmanWeb.CoreComponents

  test "shown modal removes its hidden attribute on mount" do
    html = render_component(&shown_modal/1, %{})

    assert html =~
             ~s(&quot;remove_attr&quot;,{&quot;to&quot;:&quot;#task-modal&quot;,&quot;attr&quot;:&quot;hidden&quot;})
  end

  test "modal keeps the compact default and exposes an opt-in wide size" do
    html = render_component(&sized_modals/1, %{})
    document = LazyHTML.from_fragment(html)

    default_modal = LazyHTML.query(document, "#default-modal-content[data-size='default']")
    wide_modal = LazyHTML.query(document, "#wide-modal-content[data-size='wide']")

    assert Enum.count(default_modal) == 1
    assert Enum.count(wide_modal) == 1
  end

  test "modal uses an Escape override without running its dismissal transition" do
    html = render_component(&modal_with_escape_override/1, %{})

    [escape_actions] =
      html
      |> LazyHTML.from_fragment()
      |> LazyHTML.query("#task-modal-content")
      |> LazyHTML.attribute("phx-window-keydown")

    assert escape_actions =~ "cancel-move-task"
    refute escape_actions =~ "cancel-task"
    refute escape_actions =~ ~s("hide")
    refute escape_actions =~ "pop_focus"
  end

  defp shown_modal(assigns) do
    ~H"""
    <CoreComponents.modal id="task-modal" show on_cancel={JS.push("cancel-task")}>
      <button id="first-modal-control" type="button">First control</button>
    </CoreComponents.modal>
    """
  end

  defp sized_modals(assigns) do
    ~H"""
    <CoreComponents.modal id="default-modal" on_cancel={JS.push("cancel-default")}>
      <h2 id="default-modal-title">Default modal</h2>
    </CoreComponents.modal>
    <CoreComponents.modal id="wide-modal" size={:wide} on_cancel={JS.push("cancel-wide")}>
      <h2 id="wide-modal-title">Wide modal</h2>
    </CoreComponents.modal>
    """
  end

  defp modal_with_escape_override(assigns) do
    ~H"""
    <CoreComponents.modal
      id="task-modal"
      on_cancel={JS.push("cancel-task")}
      on_escape={JS.push("cancel-move-task")}
    >
      <h2 id="task-modal-title">Task</h2>
    </CoreComponents.modal>
    """
  end
end
