defmodule TaskmanWeb.DescriptionEditor do
  @moduledoc "Shared Markdown editor; Taskman owns the form and persistence feedback."
  use TaskmanWeb, :html

  attr :field, Phoenix.HTML.FormField, required: true
  attr :id, :string, required: true
  attr :editor_id, :string, required: true
  attr :initial_value, :string, required: true
  attr :readonly?, :boolean, default: false
  attr :save_state, :atom, default: :idle

  attr :capture, :map, default: nil

  def editor(assigns) do
    ~H"""
    <div class="fieldset mb-4">
      <div
        inert={@capture && !@capture.timed_out?}
        id={@id}
        data-description-editor
        data-description-readonly={to_string(@readonly?)}
        phx-hook="DescriptionEditor"
      >
        <label id={"#{@id}-label"} for={"#{@editor_id}-visual"} class="label mb-1">
          Description
        </label>
        <div
          :if={@capture && @capture.timed_out?}
          id="description-capture-error"
          role="alert"
          class="mb-3 rounded-lg border border-rose-400/30 bg-rose-400/10 p-3 text-sm text-rose-100"
        >
          <p>Couldn’t capture the description. Reconnect and retry before continuing.</p>
          <button
            id="retry-description-capture"
            type="button"
            phx-click="retry_description_capture"
            class="mt-2 rounded-lg border border-rose-200/30 px-3 py-1.5 text-sm font-semibold transition hover:bg-rose-100/10 focus:outline-none focus:ring-2 focus:ring-rose-300/50"
          >
            Retry
          </button>
        </div>
        <div :if={!@readonly?} id={"#{@editor_id}-sync"} phx-update="ignore">
          <input
            type="hidden"
            id={"#{@id}-input"}
            name={@field.name}
            value={@field.value}
            data-leaf-sync-input={@editor_id}
          />
        </div>
        <Leaf.leaf_editor
          id={@editor_id}
          content={@initial_value}
          readonly={@readonly?}
          mode={:hybrid}
          preset={:advanced}
          deny={[:html_mode]}
          toolbar={[]}
          debounce={0}
          sync_input_name={@field.name}
          bundle_check={false}
          height="212px"
          class="task-description-leaf"
        />
        <p :for={{message, _opts} <- @field.errors} class="mt-2 text-sm text-rose-300" role="alert">
          {message}
        </p>
      </div>
    </div>
    """
  end
end
