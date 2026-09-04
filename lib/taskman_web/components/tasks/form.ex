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
  attr :parent_picker, ParentPicker, required: true
  attr :conflicts, :map, default: %{}

  def form(assigns) do
    ~H"""
    <Phoenix.Component.form
      for={@form}
      id="task-form"
      phx-change={@change}
      phx-submit={@submit}
      class={[
        @mode == :new && "[&>.fieldset:first-child]:mt-4"
      ]}
    >
      <button
        :if={@mode == :edit}
        id="submit-task-edit"
        type="submit"
        class="sr-only pointer-events-none"
        tabindex="-1"
        aria-hidden="true"
      ></button>
      <.input
        field={@form[:title]}
        id="task-title"
        type="text"
        label="Task title"
        phx-hook=".TaskTitleFocus"
        autocomplete="off"
        class="w-full rounded-xl border border-slate-700 bg-slate-950 px-3.5 py-3 text-sm text-slate-100 shadow-sm shadow-black/20 outline-none transition placeholder:text-slate-500 focus:border-indigo-400 focus:ring-4 focus:ring-indigo-400/15"
        error_class="border-rose-400 focus:border-rose-400 focus:ring-rose-400/15"
      />
      <.conflict_notice
        :if={Map.has_key?(@conflicts, "title")}
        field="title"
        value={Map.fetch!(@conflicts, "title")}
      />
      <script :type={Phoenix.LiveView.ColocatedHook} name=".TaskTitleFocus">
        export default {
          mounted() {
            // LiveView retries JS.focus after two frames; wait one more so caret placement wins.
            window.requestAnimationFrame(() => {
              window.requestAnimationFrame(() => {
                window.requestAnimationFrame(() => {
                  this.el.focus()
                  const end = this.el.value.length
                  this.el.setSelectionRange(end, end)
                })
              })
            })
          }
        }
      </script>
      <div class="mt-4">
        <ParentPickerComponent.parent_picker picker={@parent_picker} />
      </div>
      <div
        :if={@parent_picker.error}
        id="task-parent-focus"
        phx-mounted={JS.focus(to: "#task-parent-trigger")}
      >
      </div>
      <div class="mt-4 space-y-4">
        <.input
          field={@form[:description]}
          id="task-description"
          type="textarea"
          label="Description"
          rows="6"
          class="w-full rounded-xl border border-slate-700 bg-slate-950 px-3.5 py-3 text-sm text-slate-100 outline-none transition focus:border-indigo-400 focus:ring-4 focus:ring-indigo-400/15"
          error_class="border-rose-400 focus:border-rose-400 focus:ring-rose-400/15"
        />
        <.conflict_notice
          :if={Map.has_key?(@conflicts, "description")}
          field="description"
          value={Map.fetch!(@conflicts, "description")}
        />
        <.input
          field={@form[:status]}
          id="task-status"
          type="select"
          label="Status"
          options={Table.status_options()}
        />
        <.conflict_notice
          :if={Map.has_key?(@conflicts, "status")}
          field="status"
          value={Map.fetch!(@conflicts, "status")}
        />
        <.input
          field={@form[:priority]}
          id="task-priority"
          type="select"
          label="Priority"
          options={Table.priority_options()}
        />
        <.conflict_notice
          :if={Map.has_key?(@conflicts, "priority")}
          field="priority"
          value={Map.fetch!(@conflicts, "priority")}
        />
        <.input
          field={@form[:due_at]}
          id="task-due-at"
          type="datetime-local"
          label="Due date and time"
          step="60"
          value={@form[:due_at].value || ""}
        />
        <.conflict_notice
          :if={Map.has_key?(@conflicts, "due_at")}
          field="due_at"
          value={Map.fetch!(@conflicts, "due_at")}
        />
      </div>
      <div :if={@mode == :new} class="mt-6 flex justify-end gap-3">
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
    </Phoenix.Component.form>
    """
  end

  attr :field, :string, required: true
  attr :value, :any, required: true

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
      <div class="mt-2 flex flex-wrap gap-2">
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
end
