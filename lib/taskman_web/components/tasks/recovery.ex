defmodule TaskmanWeb.Tasks.Recovery do
  @moduledoc "Renders the non-actionable Task recovery operation surface."

  use TaskmanWeb, :html

  alias TaskmanWeb.Tasks.Detail

  attr :view, :map, required: true

  def shell(assigns) do
    ~H"""
    <section
      id="task-recovery"
      aria-labelledby="task-recovery-title"
      data-copy-value={@view.copy_value}
    >
      <header class="border-b border-slate-700 px-6 py-5 sm:px-7">
        <h2 id="task-recovery-title" class="text-xl font-semibold text-orange-300">
          {recovery_title(@view.reason)}
        </h2>
        <p id="task-recovery-explanation" class="mt-2 text-sm leading-6 text-slate-300">
          {recovery_explanation(@view.reason)}
        </p>
      </header>

      <div>
        <Detail.detail
          task={@view.editing.selected_task}
          task_autosave={@view.editing.autosave}
          parent_picker={@view.parent_picker}
          cancel="#"
          task_hierarchy={@view.editing.hierarchy}
          task_path={fn _task -> "#" end}
          task_move={@view.task_move}
          recovery?={true}
        />

        <footer class="flex flex-wrap items-center justify-end gap-3 border-t border-slate-700 px-6 py-5 sm:px-7">
          <div
            id="task-recovery-copy-controls"
            phx-hook=".CopyRecovery"
            phx-update="ignore"
            class="mr-auto flex items-center gap-3"
          >
            <button
              id="task-recovery-copy"
              type="button"
              class="rounded-xl border border-slate-600 px-4 py-2.5 text-sm font-semibold text-slate-200 transition hover:border-slate-500 hover:bg-slate-800"
            >
              Copy input
            </button>
            <span id="task-recovery-copy-status" aria-live="polite" class="text-sm text-slate-300"></span>
          </div>
          <button
            :if={@view.reason != :task_not_found}
            id="task-recovery-resume"
            type="button"
            phx-click="resume_task_recovery"
            phx-value-recovery_id={@view.id}
            class="rounded-xl bg-indigo-500 px-4 py-2.5 text-sm font-semibold text-white"
          >
            Try again
          </button>
          <button
            id="task-recovery-discard"
            type="button"
            phx-click="discard_task_recovery"
            phx-value-recovery_id={@view.id}
            class="rounded-xl border border-rose-500/40 bg-rose-950/20 px-4 py-2.5 text-sm font-semibold text-rose-200 transition hover:border-rose-400/60 hover:bg-rose-950/35 focus:outline-none focus:ring-2 focus:ring-rose-400/50"
          >
            Discard input
          </button>
        </footer>
      </div>
    </section>

    <script :type={Phoenix.LiveView.ColocatedHook} name=".CopyRecovery">
      export default {
        mounted() {
          this.button = this.el.querySelector("#task-recovery-copy")
          this.status = this.el.querySelector("#task-recovery-copy-status")

          this.copy = async () => {
            const shell = this.el.closest("#task-recovery")

            try {
              await navigator.clipboard.writeText(shell?.dataset.copyValue || "")
              this.status.textContent = "Copied."
            } catch (_error) {
              this.status.textContent = "Copy failed. Try again."
            }
          }

          this.button.addEventListener("click", this.copy)
        },

        destroyed() {
          this.button.removeEventListener("click", this.copy)
        }
      }
    </script>
    """
  end

  defp recovery_title(:task_not_found), do: "This task is no longer available"
  defp recovery_title(:project_not_found), do: "This project is no longer available"
  defp recovery_title(:destination_not_found), do: "This task’s location is no longer available"
  defp recovery_title(_reason), do: "Couldn’t open this task"

  defp recovery_explanation(:task_not_found) do
    "Copy your unsaved input or discard it. This recovery state is temporary. " <>
      "Reloading, reconnecting, or leaving Taskman may lose it."
  end

  defp recovery_explanation(_reason) do
    "Copy your unsaved input, try again, or discard it. This recovery state is temporary. " <>
      "Reloading, reconnecting, or leaving Taskman may lose it."
  end
end
