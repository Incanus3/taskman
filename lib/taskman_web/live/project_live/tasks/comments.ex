defmodule TaskmanWeb.ProjectLive.Tasks.Comments do
  @moduledoc false

  import Phoenix.Component, only: [assign: 3]

  alias Taskman.Tasks
  alias Taskman.Tasks.Comment
  alias TaskmanWeb.ProjectLive.Tasks.Activity

  @events ~w(select_task_detail_tab validate_task_comment post_task_comment)

  def events, do: @events

  defmodule State do
    @moduledoc false

    defstruct task_id: nil,
              tab: :activity,
              form: nil,
              draft: "",
              posting?: false,
              error: nil

    def empty, do: %__MODULE__{}
  end

  def clear(socket) do
    socket
    |> assign(:comments, State.empty())
    |> Activity.clear()
  end

  def discard_draft(socket) do
    state = socket.assigns.comments

    assign(socket, :comments, %{
      state
      | draft: "",
        form: comment_form(%Comment{}, %{}),
        error: nil
    })
  end

  def set_draft(socket, text) do
    state = socket.assigns.comments
    assign(socket, :comments, %{state | draft: text})
  end

  def load(socket, _project, task) do
    if socket.assigns.comments.task_id == task.id do
      socket
    else
      socket
      |> assign(:comments, %State{task_id: task.id, form: comment_form(%Comment{}, %{})})
    end
  end

  def sync_timestamp(socket, task) do
    editing = socket.assigns.editing
    selected_task = %{editing.selected_task | updated_at: task.updated_at}
    assign(socket, :editing, %{editing | selected_task: selected_task})
  end

  def handle_event("select_task_detail_tab", %{"tab" => tab}, socket)
      when tab in ["activity", "sessions"] do
    {:noreply,
     assign(socket, :comments, %{socket.assigns.comments | tab: String.to_existing_atom(tab)})}
  end

  def handle_event("select_task_detail_tab", _params, socket), do: {:noreply, socket}

  def handle_event("validate_task_comment", %{"comment" => %{"text" => text}}, socket) do
    state = socket.assigns.comments
    changeset = %{Comment.changeset(%Comment{}, %{text: text}) | action: :validate}

    state = %{
      state
      | draft: text,
        form: Phoenix.Component.to_form(changeset, as: :comment),
        error: nil
    }

    {:noreply, assign(socket, :comments, state)}
  end

  def handle_event("post_task_comment", %{"comment" => %{"text" => text}}, socket) do
    case post(socket, text) do
      {:ok, socket} -> {:noreply, socket}
      {:error, socket} -> {:noreply, socket}
    end
  end

  def handle_event("post_task_comment", _params, socket), do: {:noreply, socket}

  def post(socket, text) do
    state = socket.assigns.comments
    project = socket.assigns.workspace.selected_project
    task = socket.assigns.editing.selected_task

    cond do
      state.posting? ->
        {:error, socket}

      is_nil(project) or is_nil(task) ->
        {:error,
         assign(socket, :comments, %{state | tab: :activity, error: error_message(:not_found)})}

      state.task_id != task.id ->
        {:error, socket}

      true ->
        post_comment(socket, state, project, task, text)
    end
  end

  defp refresh_timestamp(socket, project, task) do
    case Tasks.get_task_for_project(project, task.id) do
      nil -> socket
      persisted_task -> sync_timestamp(socket, persisted_task)
    end
  end

  defp comment_form(comment, attrs) do
    comment
    |> Comment.changeset(attrs)
    |> Phoenix.Component.to_form(as: :comment)
  end

  defp error_message(:not_found),
    do: "This Task is no longer available. Your comment was not posted."

  defp error_message(:authentication_required), do: "Sign in again to post your comment."

  defp error_message(_), do: "The comment could not be posted. Please try again."

  defp post_comment(socket, state, project, task, text) do
    posting_socket = assign(socket, :comments, %{state | posting?: true, draft: text})
    actor = socket.assigns.current_user

    case Tasks.create_comment(project, task, actor, %{text: text}) do
      {:ok, _comment} ->
        next = %{
          state
          | posting?: false,
            draft: "",
            form: comment_form(%Comment{}, %{}),
            error: nil
        }

        {:ok,
         posting_socket
         |> assign(:comments, next)
         |> Activity.reconcile(project, task)
         |> refresh_timestamp(project, task)}

      {:error, %Ecto.Changeset{} = changeset} ->
        next = %{
          state
          | posting?: false,
            draft: text,
            form: Phoenix.Component.to_form(%{changeset | action: :validate}, as: :comment),
            error: "Check the comment text and try again."
        }

        {:error, assign(posting_socket, :comments, %{next | tab: :activity})}

      {:error, reason} ->
        next = %{
          state
          | posting?: false,
            draft: text,
            error: error_message(reason)
        }

        {:error, assign(posting_socket, :comments, %{next | tab: :activity})}
    end
  end
end
