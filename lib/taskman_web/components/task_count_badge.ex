defmodule TaskmanWeb.TaskCountBadge do
  use TaskmanWeb, :html

  @doc "Renders matching task counts, optionally with the broader eligible population."
  attr :id, :string, required: true
  attr :visible, :integer, required: true
  attr :eligible, :integer, default: nil
  attr :class, :string, default: nil

  def badge(assigns) do
    description =
      if is_nil(assigns.eligible) do
        "#{assigns.visible} tasks match the current filters."
      else
        "#{assigns.visible} tasks match the current filters / #{assigns.eligible} eligible tasks. " <>
          "Eligible tasks include all unclosed tasks plus selected closed statuses."
      end

    assigns = assign(assigns, :description, description)

    ~H"""
    <span
      id={@id}
      data-tooltip={if(is_nil(@eligible), do: "Matching tasks", else: "Matching / eligible tasks")}
      aria-describedby={"#{@id}-description"}
      class={[
        "inline-flex shrink-0 items-center rounded-md bg-white/7 font-medium tabular-nums text-slate-400",
        if(is_nil(@eligible), do: "px-2 py-0.5 text-xs", else: "p-2 text-sm"),
        @class
      ]}
    >
      <span id={"#{@id}-value"} aria-hidden="true">
        {@visible}
        <%= if !is_nil(@eligible) do %>
          / {@eligible}
        <% end %>
      </span>
      <span id={"#{@id}-description"} class="sr-only">{@description}</span>
    </span>
    """
  end
end
