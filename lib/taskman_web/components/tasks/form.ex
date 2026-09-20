defmodule TaskmanWeb.Tasks.Form do
  use TaskmanWeb, :html

  import Phoenix.Component, except: [form: 1]

  alias TaskmanWeb.ProjectLive.Tasks.ParentPicker
  alias TaskmanWeb.Tasks.ParentPicker, as: ParentPickerComponent
  alias TaskmanWeb.Tasks.Table

  attr :form, Phoenix.HTML.Form, required: true
  attr :mode, :atom, values: [:new, :edit], required: true
  attr :change, :string, required: true
  attr :submit, :string, default: nil
  attr :cancel, :string, required: true
  attr :create_enabled?, :boolean, default: false
  attr :location, :string, default: nil
  attr :location_label, :string, default: nil
  attr :location_options, :list, default: []
  attr :location_error, :string, default: nil
  attr :parent_picker, ParentPicker, required: true
  attr :conflicts, :map, default: %{}
  attr :field_states, :map, default: %{}
  attr :recovery?, :boolean, default: false

  def form(assigns) do
    ~H"""
    <Phoenix.Component.form
      :if={!@recovery?}
      for={@form}
      id="task-form"
      phx-change={@change}
      phx-submit={@submit}
      class={[
        @mode == :new && "[&>.fieldset:first-child]:mt-4"
      ]}
    >
      <.fields {assigns} />
    </Phoenix.Component.form>
    <div :if={@recovery?} id="task-recovery-fields">
      <.fields {assigns} />
    </div>
    """
  end

  attr :form, Phoenix.HTML.Form, required: true
  attr :mode, :atom, values: [:new, :edit], required: true
  attr :cancel, :string, required: true
  attr :create_enabled?, :boolean, default: false
  attr :location, :string, default: nil
  attr :location_label, :string, default: nil
  attr :location_options, :list, default: []
  attr :location_error, :string, default: nil
  attr :parent_picker, ParentPicker, required: true
  attr :conflicts, :map, default: %{}
  attr :field_states, :map, default: %{}
  attr :recovery?, :boolean, default: false

  defp fields(assigns) do
    ~H"""
    <button
      :if={@mode == :edit && !@recovery?}
      id="submit-task-edit"
      type="submit"
      class="sr-only pointer-events-none"
      tabindex="-1"
      aria-hidden="true"
    ></button>
    <div :if={@mode == :new} class="fieldset mb-4">
      <label for="task-location">
        <span class="label mb-1">Location</span>
        <select
          id="task-location"
          name="location"
          value={@location}
          aria-invalid={to_string(not is_nil(@location_error))}
          aria-describedby={@location_error && "task-location-error"}
          class="w-full select"
        >
          <option :if={@location_error} value={@location} selected disabled>
            {@location_label}
          </option>
          <option
            :for={{label, key} <- @location_options}
            value={key}
            selected={key == @location}
          >
            {label}
          </option>
        </select>
      </label>
      <.inline_error
        :if={@location_error}
        id="task-location-error"
        role="alert"
        class="mt-2 text-rose-300"
      >
        {@location_error}
      </.inline_error>
    </div>
    <.lifecycle_field
      field="title"
      states={@field_states}
      conflicts={@conflicts}
      recovery?={@recovery?}
    >
      <.input
        field={@form[:title]}
        id="task-title"
        type="text"
        label="Task title"
        phx-hook={!@recovery? && "TaskmanWeb.Tasks.Form.TaskTitleFocus"}
        readonly={@recovery?}
        autocomplete="off"
        class="w-full rounded-xl border border-slate-700 bg-slate-950 px-3.5 py-3 text-sm text-slate-100 shadow-sm shadow-black/20 outline-none transition placeholder:text-slate-500 focus:border-indigo-400 focus:ring-4 focus:ring-indigo-400/15"
        error_class="border-rose-400 focus:border-rose-400 focus:ring-rose-400/15"
      />
    </.lifecycle_field>
    <.conflict_notice
      :if={Map.has_key?(@conflicts, "title")}
      field="title"
      value={Map.fetch!(@conflicts, "title")}
      recovery?={@recovery?}
    />
    <script :type={Phoenix.LiveView.ColocatedHook} name=".TaskTitleFocus">
      export default {
        mounted() {
          this.modal = this.el.closest("[data-modal-root]")
          this.focusAtEnd = () => {
            this.el.focus()
            const end = this.el.value.length
            this.el.setSelectionRange(end, end)
          }

          if (this.modal) {
            this.modal.addEventListener("phx:show-end", this.focusAtEnd, { once: true })
          } else {
            this.focusAtEnd()
          }
        },

        destroyed() {
          this.modal?.removeEventListener("phx:show-end", this.focusAtEnd)
        }
      }
    </script>
    <div class="mt-4">
      <ParentPickerComponent.parent_picker picker={@parent_picker} recovery?={@recovery?} />
    </div>
    <div
      :if={@parent_picker.error}
      id="task-parent-focus"
      phx-mounted={JS.focus(to: "#task-parent-trigger")}
    >
    </div>
    <div class="mt-4 space-y-4">
      <.lifecycle_field
        field="description"
        states={@field_states}
        conflicts={@conflicts}
        recovery?={@recovery?}
      >
        <.input
          field={@form[:description]}
          id="task-description"
          type="textarea"
          label="Description"
          rows="6"
          readonly={@recovery?}
          class="w-full rounded-xl border border-slate-700 bg-slate-950 px-3.5 py-3 text-sm text-slate-100 outline-none transition focus:border-indigo-400 focus:ring-4 focus:ring-indigo-400/15"
          error_class="border-rose-400 focus:border-rose-400 focus:ring-rose-400/15"
        />
      </.lifecycle_field>
      <.conflict_notice
        :if={Map.has_key?(@conflicts, "description")}
        field="description"
        value={Map.fetch!(@conflicts, "description")}
        recovery?={@recovery?}
      />
      <.lifecycle_field
        field="status"
        states={@field_states}
        conflicts={@conflicts}
        recovery?={@recovery?}
      >
        <.input
          field={@form[:status]}
          id="task-status"
          type="select"
          label="Status"
          options={select_options(Table.status_options(), @form[:status].value, @recovery?)}
          disabled={@recovery?}
        />
      </.lifecycle_field>
      <.conflict_notice
        :if={Map.has_key?(@conflicts, "status")}
        field="status"
        value={Map.fetch!(@conflicts, "status")}
        recovery?={@recovery?}
      />
      <.lifecycle_field
        field="priority"
        states={@field_states}
        conflicts={@conflicts}
        recovery?={@recovery?}
      >
        <.input
          field={@form[:priority]}
          id="task-priority"
          type="select"
          label="Priority"
          options={select_options(Table.priority_options(), @form[:priority].value, @recovery?)}
          disabled={@recovery?}
        />
      </.lifecycle_field>
      <.conflict_notice
        :if={Map.has_key?(@conflicts, "priority")}
        field="priority"
        value={Map.fetch!(@conflicts, "priority")}
        recovery?={@recovery?}
      />
      <.lifecycle_field
        field="due_at"
        states={@field_states}
        conflicts={@conflicts}
        recovery?={@recovery?}
      >
        <.input
          field={@form[:due_at]}
          id="task-due-at"
          type="datetime-local"
          label="Due date and time"
          step="60"
          value={@form[:due_at].value || ""}
          readonly={@recovery?}
        />
      </.lifecycle_field>
      <.conflict_notice
        :if={Map.has_key?(@conflicts, "due_at")}
        field="due_at"
        value={Map.fetch!(@conflicts, "due_at")}
        recovery?={@recovery?}
      />
    </div>
    <div :if={@mode == :new && !@recovery?} class="mt-6 flex justify-end gap-3">
      <.link
        id="cancel-task"
        patch={@cancel}
        class="rounded-xl px-4 py-2.5 text-sm font-semibold text-slate-300 transition hover:bg-slate-800 hover:text-white"
      >
        Cancel
      </.link>
      <button
        id="create-task"
        type="submit"
        disabled={!@create_enabled?}
        phx-disable-with="Creating…"
        class="rounded-xl bg-indigo-500 px-4 py-2.5 text-sm font-semibold text-white shadow-sm shadow-indigo-950/30 transition hover:bg-indigo-400 disabled:cursor-not-allowed disabled:opacity-60"
      >
        Create task
      </button>
    </div>
    """
  end

  attr :field, :string, required: true
  attr :states, :map, required: true
  attr :conflicts, :map, required: true
  attr :recovery?, :boolean, required: true
  slot :inner_block, required: true

  defp lifecycle_field(assigns) do
    assigns = assign(assigns, :field_id, String.replace(assigns.field, "_", "-"))

    ~H"""
    <div data-lifecycle-field={@field_id} class="relative">
      {render_slot(@inner_block)}
      <.field_status :if={!@recovery?} field={@field} states={@states} conflicts={@conflicts} />
    </div>
    """
  end

  attr :field, :string, required: true
  attr :states, :map, required: true
  attr :conflicts, :map, required: true

  defp field_status(assigns) do
    state =
      if Map.has_key?(assigns.conflicts, assigns.field),
        do: nil,
        else: Map.get(assigns.states, assigns.field)

    assigns = assign(assigns, :state, state)

    ~H"""
    <p
      id={"task-#{String.replace(@field, "_", "-")}-save-status"}
      aria-live="polite"
      data-state={@state}
      data-layout="label-end"
      class={[
        "absolute right-0 top-1 mb-1 text-right text-xs leading-[18px]",
        field_status_color(@state),
        !@state && "invisible"
      ]}
    >
      {@state && field_status_message(@state)}
    </p>
    """
  end

  defp field_status_message(:saving), do: "Saving…"
  defp field_status_message(:saved), do: "Saved"
  defp field_status_message(:not_saved), do: "Not saved"
  defp field_status_message(:failed), do: "Couldn’t save changes"

  defp field_status_color(:saving), do: "text-amber-300"
  defp field_status_color(:saved), do: "text-emerald-400"
  defp field_status_color(state) when state in [:not_saved, :failed], do: "text-rose-300"
  defp field_status_color(nil), do: "text-slate-400"

  attr :field, :string, required: true
  attr :value, :any, required: true
  attr :recovery?, :boolean, default: false

  defp conflict_notice(assigns) do
    ~H"""
    <div
      id={"task-#{@field}-conflict"}
      role="alert"
      class="mt-2 rounded-lg border border-amber-400/30 bg-amber-400/10 px-3 py-2.5 text-sm text-amber-100"
    >
      <p>
        This field changed elsewhere. Latest saved value: {format_conflict_value(@field, @value)}
      </p>
      <div :if={!@recovery?} class="mt-2 flex flex-wrap gap-2">
        <button
          id={"use-latest-#{@field}"}
          type="button"
          phx-click="resolve_task_conflict"
          phx-value-field={@field}
          phx-value-resolution="use_latest"
          class="rounded-lg border border-amber-200/30 px-2.5 py-1.5 text-xs font-semibold text-amber-50 transition hover:bg-amber-100/10 focus:outline-none focus:ring-2 focus:ring-amber-300/50"
        >
          Use latest
        </button>
        <button
          id={"keep-mine-#{@field}"}
          type="button"
          phx-click="resolve_task_conflict"
          phx-value-field={@field}
          phx-value-resolution="keep_mine"
          class="rounded-lg border border-amber-200/30 px-2.5 py-1.5 text-xs font-semibold text-amber-50 transition hover:bg-amber-100/10 focus:outline-none focus:ring-2 focus:ring-amber-300/50"
        >
          Keep mine
        </button>
      </div>
    </div>
    """
  end

  defp format_conflict_value("status", value),
    do: option_label(Table.status_options(), value)

  defp format_conflict_value("priority", value),
    do: option_label(Table.priority_options(), value)

  defp format_conflict_value("due_at", nil), do: "No due date"

  defp format_conflict_value("due_at", %NaiveDateTime{} = value),
    do: Calendar.strftime(value, "%Y-%m-%dT%H:%M")

  defp format_conflict_value(_field, nil), do: "(empty)"
  defp format_conflict_value(_field, ""), do: "(empty)"
  defp format_conflict_value(_field, value) when is_binary(value), do: value
  defp format_conflict_value(_field, value), do: to_string(value)

  defp option_label(options, value) do
    case Enum.find(options, fn {_label, option_value} -> option_value == value end) do
      {label, _value} -> label
      nil -> "(empty)"
    end
  end

  defp select_options(options, value, true) do
    if Enum.any?(options, fn {_label, option_value} ->
         to_string(option_value) == to_string(value)
       end) do
      options
    else
      [{"Captured value · #{value}", value} | options]
    end
  end

  defp select_options(options, _value, false), do: options
end
