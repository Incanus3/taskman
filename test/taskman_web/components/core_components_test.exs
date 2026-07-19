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

  defp shown_modal(assigns) do
    ~H"""
    <CoreComponents.modal id="task-modal" show on_cancel={JS.push("cancel-task")}>
      <button id="first-modal-control" type="button">First control</button>
    </CoreComponents.modal>
    """
  end
end
