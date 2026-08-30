defmodule TaskmanWeb.TaskParentPickerComponent do
  use TaskmanWeb, :html

  alias Taskman.Tasks.{Task, TaskWithLocation}
  alias TaskmanWeb.TaskParentPicker

  attr :picker, TaskParentPicker, required: true

  def parent_picker(assigns) do
    assigns = assign(assigns, :picker_state, assigns.picker)

    ~H"""
    <div id="task-parent-picker" class="relative">
      <div class="relative">
        <.input
          id="task-parent-search"
          type="search"
          name="parent_query"
          label="Parent Task"
          value={display_query(@picker_state)}
          role="combobox"
          aria-controls="task-parent-results"
          aria-expanded={to_string(@picker_state.options_open?)}
          aria-activedescendant={@picker_state.active_option_id}
          aria-autocomplete="list"
          aria-invalid={@picker_state.error && "true"}
          aria-describedby={@picker_state.error && "task-parent-error"}
          autocomplete="off"
          phx-click="open_task_parent_options"
          phx-keydown="task_parent_keydown"
          phx-keyup="search_task_parents"
          phx-hook=".TaskParentPickerKeyboard"
          class={[
            "w-full rounded-xl border border-slate-700 bg-slate-950 px-3.5 py-3 pr-10 text-sm text-slate-100 shadow-sm shadow-black/20 outline-none transition placeholder:text-slate-500 focus:border-indigo-400 focus:ring-4 focus:ring-indigo-400/15",
            @picker_state.error &&
              "border-rose-400 focus:border-rose-400 focus:ring-rose-400/15"
          ]}
          placeholder="Search parent Tasks"
        />
        <button
          type="button"
          aria-label="Open parent Task options"
          phx-click="open_task_parent_options"
          class="absolute right-2 top-1/2 grid size-8 -translate-y-1/2 place-items-center rounded-lg text-slate-400 transition hover:bg-slate-800 hover:text-white focus:outline-none focus:ring-2 focus:ring-indigo-400/50"
        >
          <.icon
            name="hero-chevron-down"
            class={["size-4 transition", @picker_state.options_open? && "rotate-180"]}
          />
        </button>
      </div>

      <div
        :if={@picker_state.selected_parent}
        id="task-parent-selected"
        class="mt-2 rounded-lg border border-indigo-400/30 bg-indigo-400/10 px-3 py-2 text-sm text-indigo-100"
      >
        <span class="font-medium">{@picker_state.selected_parent.title}</span>
        <span class="ml-1 text-xs text-indigo-200/70">Task #{@picker_state.selected_parent.id}</span>
      </div>

      <button
        :if={show_no_parent?(@picker_state) and not @picker_state.options_open?}
        id="task-parent-clear"
        type="button"
        phx-click="clear_task_parent"
        class="mt-2 rounded-lg px-2 py-1 text-xs font-semibold text-slate-400 transition hover:bg-slate-800 hover:text-white focus:outline-none focus:ring-2 focus:ring-indigo-400/50"
      >
        No parent
      </button>

      <div
        :if={@picker_state.options_open?}
        id="task-parent-results"
        role="listbox"
        aria-label="Parent Task options"
        class="mt-2 max-h-60 space-y-1 overflow-y-auto rounded-xl border border-slate-700 bg-slate-950 p-1 shadow-xl shadow-black/30"
      >
        <button
          :if={show_no_parent?(@picker_state)}
          id="task-parent-clear"
          type="button"
          role="option"
          aria-selected={to_string(is_nil(@picker_state.selected_parent))}
          data-active={to_string(@picker_state.active_option_id == "task-parent-clear")}
          tabindex="-1"
          phx-click="clear_task_parent"
          class="flex w-full items-center justify-between gap-3 rounded-lg px-3 py-2 text-left text-sm text-slate-200 transition hover:bg-slate-800 focus:outline-none focus:ring-2 focus:ring-indigo-400/50"
        >
          <span class="font-medium">No parent</span>
          <span class="text-xs text-slate-500">Make this a root Task</span>
        </button>

        <p
          :if={@picker_state.options == []}
          id="task-parent-no-results"
          class="rounded-lg px-3 py-2 text-sm text-slate-400"
        >
          No parent Tasks match your search.
        </p>

        <button
          :for={option <- @picker_state.options}
          id={option_id(option)}
          type="button"
          role="option"
          aria-label={option_label(option)}
          aria-selected={to_string(selected?(@picker_state, option))}
          data-active={to_string(@picker_state.active_option_id == option_id(option))}
          data-title={option.task.title}
          tabindex="-1"
          phx-click="select_task_parent"
          phx-value-parent-id={option.task.id}
          class={[
            "flex w-full items-start justify-between gap-3 rounded-lg px-3 py-2 text-left text-sm transition focus:outline-none focus:ring-2 focus:ring-indigo-400/50",
            selected?(@picker_state, option) && "bg-indigo-400/15 text-indigo-100",
            !selected?(@picker_state, option) && "text-slate-200 hover:bg-slate-800"
          ]}
        >
          <span class="min-w-0">
            <span class="block truncate font-medium">{option.task.title}</span>
            <span class="mt-0.5 block truncate text-xs text-slate-400">
              Task #{option.task.id} · {location_label(option.location_path)}
            </span>
          </span>
          <.icon
            :if={selected?(@picker_state, option)}
            name="hero-check"
            class="mt-0.5 size-4 shrink-0 text-indigo-300"
          />
        </button>
      </div>

      <p
        :if={@picker_state.error}
        id="task-parent-error"
        role="alert"
        class="mt-2 text-sm text-rose-300"
      >
        {@picker_state.error}
      </p>
    </div>

    <script :type={Phoenix.LiveView.ColocatedHook} name=".TaskParentPickerKeyboard">
      export default {
        mounted() {
          this.handledKeys = ["ArrowDown", "ArrowUp", "Enter", "Escape"]

          this.onKeydown = event => {
            if (!this.handledKeys.includes(event.key)) return

            event.preventDefault()
            event.stopPropagation()

            if (event.key === "Enter") {
              const activeId = this.el.getAttribute("aria-activedescendant")
              const activeOption = activeId && document.getElementById(activeId)
              this.el.value =
                activeId === "task-parent-clear" ? "" : activeOption?.dataset.title || this.el.value
            }

            this.pushEvent("task_parent_keydown", {key: event.key})
          }

          this.onKeyup = event => {
            if (this.handledKeys.includes(event.key)) event.stopPropagation()
          }

          this.el.addEventListener("keydown", this.onKeydown)
          this.el.addEventListener("keyup", this.onKeyup)
        },

        destroyed() {
          this.el.removeEventListener("keydown", this.onKeydown)
          this.el.removeEventListener("keyup", this.onKeyup)
        }
      }
    </script>
    """
  end

  defp display_query(%TaskParentPicker{query: query, selected_parent: selected_parent}) do
    if query == "" and selected_parent, do: selected_parent.title || "", else: query
  end

  defp show_no_parent?(%TaskParentPicker{mode: :edit}), do: true
  defp show_no_parent?(%TaskParentPicker{selected_parent: %Task{}}), do: true
  defp show_no_parent?(%TaskParentPicker{}), do: false

  defp selected?(
         %TaskParentPicker{selected_parent: %Task{id: selected_id}},
         %TaskWithLocation{task: %Task{id: option_id}}
       ),
       do: selected_id == option_id

  defp selected?(%TaskParentPicker{}, %TaskWithLocation{}), do: false

  defp option_id(%TaskWithLocation{task: %Task{id: id}}), do: "task-parent-option-#{id}"

  defp option_label(%TaskWithLocation{task: %Task{title: title, id: id}, location_path: path}) do
    "#{title} · Task ##{id} · #{location_label(path)}"
  end

  defp location_label([]), do: "Project"
  defp location_label(path), do: Enum.map_join(path, " / ", & &1.name)
end
