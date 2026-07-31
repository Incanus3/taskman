defmodule TaskmanWeb.TaskForm do
  use TaskmanWeb, :html

  import Phoenix.Component, except: [form: 1]

  alias TaskmanWeb.TaskComponents

  attr :form, Phoenix.HTML.Form, required: true
  attr :mode, :atom, values: [:new, :edit], required: true
  attr :change, :string, required: true
  attr :submit, :string, default: nil
  attr :cancel, :string, required: true

  def form(assigns) do
    ~H"""
    <Phoenix.Component.form
      for={@form}
      id="task-form"
      phx-change={@change}
      phx-submit={@submit}
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
        autocomplete="off"
        class="w-full rounded-xl border border-slate-700 bg-slate-950 px-3.5 py-3 text-sm text-slate-100 shadow-sm shadow-black/20 outline-none transition placeholder:text-slate-500 focus:border-indigo-400 focus:ring-4 focus:ring-indigo-400/15"
        error_class="border-rose-400 focus:border-rose-400 focus:ring-rose-400/15"
      />
      <div :if={@mode == :edit} class="mt-4 space-y-4">
        <.input
          field={@form[:description]}
          id="task-description"
          type="textarea"
          label="Description"
          rows="6"
          class="w-full rounded-xl border border-slate-700 bg-slate-950 px-3.5 py-3 text-sm text-slate-100 outline-none transition focus:border-indigo-400 focus:ring-4 focus:ring-indigo-400/15"
          error_class="border-rose-400 focus:border-rose-400 focus:ring-rose-400/15"
        />
        <.input
          field={@form[:status]}
          id="task-status"
          type="select"
          label="Status"
          options={TaskComponents.status_options()}
        />
        <.input
          field={@form[:priority]}
          id="task-priority"
          type="select"
          label="Priority"
          options={TaskComponents.priority_options()}
        />
        <.input
          field={@form[:due_at]}
          id="task-due-at"
          type="datetime-local"
          label="Due date and time"
          step="60"
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
          type="submit"
          phx-disable-with="Creating…"
          class="rounded-xl bg-indigo-500 px-4 py-2.5 text-sm font-semibold text-white shadow-sm shadow-indigo-950/30 transition hover:bg-indigo-400 disabled:cursor-wait disabled:opacity-60"
        >
          Create task
        </button>
      </div>
    </Phoenix.Component.form>
    """
  end
end
