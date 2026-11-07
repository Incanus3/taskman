defmodule TaskmanWeb.Tasks.ActivityEntry do
  @moduledoc false
  use TaskmanWeb, :html

  alias Taskman.Tasks.Comment
  alias TaskmanWeb.Tasks.{Priority, Status}

  attr :id, :string, required: true
  attr :entry, :map, required: true
  attr :task_id, :integer, required: true

  def entry(%{entry: %{kind: :comment}} = assigns) do
    assigns = assign(assigns, :comment, assigns.entry.comment)

    ~H"""
    <article id={@id} class="rounded-xl border border-slate-700/70 bg-slate-900/65 p-4">
      <div class="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <strong class="min-w-0 break-words text-sm font-semibold text-slate-100">
          {comment_author(@comment)}
        </strong>
        <.time id={"#{@id}-time"} value={@entry.occurred_at} />
      </div>
      <p
        phx-no-format
        class="task-comment-text mt-2 whitespace-pre-wrap break-words text-sm leading-6 text-slate-200"
      >{@comment.text}</p>
    </article>
    """
  end

  def entry(%{entry: %{kind: :change}} = assigns) do
    assigns = assign(assigns, :parts, transition(assigns.entry.change, assigns.task_id))

    ~H"""
    <article id={@id} class="min-w-0 rounded-lg border-l-2 border-slate-700 px-3 py-2">
      <div class="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <strong class="min-w-0 break-words text-xs font-semibold text-slate-300">
          {@entry.change.actor.login}
        </strong>
        <.time id={"#{@id}-time"} value={@entry.occurred_at} />
      </div>
      <p class="mt-1 break-words text-sm leading-6 text-slate-300">
        <%= for part <- @parts do %>
          <.reference :if={is_map(part)} reference={part} />
          <%= if is_binary(part) do %>
            {part}
          <% end %>
        <% end %>
      </p>
    </article>
    """
  end

  attr :id, :string, required: true
  attr :value, :any, required: true

  defp time(assigns) do
    ~H"""
    <time
      id={@id}
      datetime={DateTime.to_iso8601(@value)}
      phx-hook=".LocalTime"
      class="text-xs text-slate-400"
    >
      {DateTime.to_iso8601(@value)}
    </time>
    <script :type={Phoenix.LiveView.ColocatedHook} name=".LocalTime">
      export default {
        mounted() { this.formatTime() },
        updated() { this.formatTime() },
        formatTime() {
          const value = new Date(this.el.dateTime)
          if (!Number.isNaN(value.valueOf())) {
            this.el.textContent = new Intl.DateTimeFormat(undefined, {
              dateStyle: "medium", timeStyle: "short"
            }).format(value)
          }
        }
      }
    </script>
    """
  end

  attr :reference, :map, required: true

  defp reference(assigns) do
    ~H"""
    <.link
      :if={@reference.available?}
      patch={~p"/projects/#{@reference.project_id}/tasks/#{@reference.id}"}
      class="font-medium text-indigo-300 underline decoration-indigo-400/30 underline-offset-2 transition hover:text-indigo-200"
    >{@reference.title}</.link>
    <span :if={!@reference.available?}>{@reference.title}</span>
    """
  end

  defp transition(%{type: "task_created"}, _task_id), do: ["Task created"]

  defp transition(%{type: "block_changed"} = change, task_id) do
    source? = change.context.blocking_task.id == task_id
    target = if source?, do: change.context.blocked_task, else: change.context.blocking_task
    label = if source?, do: "Blocks", else: "Blocked by"
    ["#{label} #{if(change.after, do: "added", else: "removed")}: ", target]
  end

  defp transition(%{field: "parent_task_id", task_id: task_id} = change, task_id),
    do: ["Parent changed from ", change.before || "None", " to ", change.after || "None"]

  defp transition(%{field: "parent_task_id"} = change, task_id) do
    action = if change.after && change.after.id == task_id, do: "added", else: "removed"
    ["Child #{action}: ", change.context.task]
  end

  defp transition(change, _task_id) do
    [
      "#{field_label(change.field)} changed from ",
      value(change.field, change.before),
      " to ",
      value(change.field, change.after)
    ]
  end

  defp field_label("title"), do: "Title"
  defp field_label("status"), do: "Status"
  defp field_label("priority"), do: "Priority"
  defp field_label("due_at"), do: "Due date"
  defp field_label("list_id"), do: "List"

  defp value(_field, nil), do: "None"
  defp value("priority", value), do: Priority.label(String.to_existing_atom(value))
  defp value("status", value), do: Status.label(String.to_existing_atom(value))

  defp value("list_id", %{project_name: project_name, path: []}),
    do: "#{project_name} (Project level)"

  defp value("list_id", %{project_name: project_name, path: path}),
    do: Enum.join([project_name | path], " / ")

  defp value("due_at", value) do
    case NaiveDateTime.from_iso8601(value) do
      {:ok, datetime} -> Calendar.strftime(datetime, "%b %-d, %Y at %H:%M")
      _ -> value
    end
  end

  defp value(_field, value), do: value

  defp comment_author(%Comment{author_name: nil, author_login: login}),
    do: login || "Deleted user"

  defp comment_author(%Comment{author_name: name, author_login: login}),
    do: "#{name} (#{login || "Deleted user"})"
end
