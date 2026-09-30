defmodule TaskmanWeb.ProjectLive.Tasks.Editing do
  import Phoenix.Component, only: [assign: 3]
  import Phoenix.LiveView, only: [push_patch: 2]

  alias Taskman.Projects
  alias Taskman.Projects.Project
  alias Taskman.Tasks
  alias Taskman.Tasks.Task
  alias TaskmanWeb.ProjectLive.Paths

  alias TaskmanWeb.ProjectLive.Tasks.{
    Autosave,
    Creation,
    Hierarchy,
    Listing,
    Move,
    ParentPicker,
    RelatedTasksPicker
  }

  @events ~w(toggle_task_hierarchy_node autosave_task submit_task_edit resolve_task_conflict open_related_picker close_related_picker select_related_project search_related_tasks add_related_task remove_related_task keep_current_task_status confirm_task_done)

  defmodule State do
    alias Taskman.Tasks.Task
    alias Taskman.Tasks.Hierarchy, as: TaskHierarchy
    alias TaskmanWeb.ProjectLive.Tasks.{Autosave, Hierarchy, RelatedTasksPicker}

    defstruct selected_task: nil,
              not_found?: false,
              detail_open?: false,
              autosave: Autosave.empty(),
              hierarchy: Hierarchy.empty(),
              related_tasks: %{blocks: [], blocked_by: []},
              related_picker: RelatedTasksPicker.empty()

    @type t :: %__MODULE__{
            selected_task: Task.t() | nil,
            not_found?: boolean(),
            detail_open?: boolean(),
            autosave: Autosave.t(),
            hierarchy: Hierarchy.t(),
            related_tasks: map(),
            related_picker: RelatedTasksPicker.t()
          }

    @spec empty() :: t()
    def empty, do: %__MODULE__{}

    @spec open(t(), Task.t(), Autosave.t(), TaskHierarchy.t()) :: t()
    @spec open(t(), Task.t(), Autosave.t(), TaskHierarchy.t(), map()) :: t()
    def open(
          %__MODULE__{} = state,
          %Task{} = task,
          %Autosave{} = autosave,
          hierarchy,
          related_tasks \\ %{blocks: [], blocked_by: []}
        ) do
      %{
        state
        | selected_task: task,
          not_found?: false,
          detail_open?: true,
          autosave: autosave,
          hierarchy: Hierarchy.load(state.hierarchy, hierarchy),
          related_tasks: related_tasks,
          related_picker:
            if(state.selected_task && state.selected_task.id == task.id,
              do: state.related_picker,
              else: RelatedTasksPicker.empty()
            )
      }
    end

    @spec not_found(t()) :: t()
    def not_found(%__MODULE__{} = state), do: %{clear(state) | not_found?: true}

    @spec put_autosave(t(), Autosave.t()) :: t()
    def put_autosave(%__MODULE__{} = state, %Autosave{} = autosave),
      do: %{state | autosave: autosave}

    @spec put_hierarchy(t(), Hierarchy.t()) :: t()
    def put_hierarchy(%__MODULE__{} = state, %Hierarchy{} = hierarchy),
      do: %{state | hierarchy: hierarchy}

    @spec clear_transient(t()) :: t()
    def clear_transient(%__MODULE__{} = state) do
      %{
        state
        | selected_task: nil,
          not_found?: false,
          autosave: Autosave.clear(state.autosave),
          related_tasks: %{blocks: [], blocked_by: []},
          related_picker: RelatedTasksPicker.empty()
      }
    end

    @spec clear(t()) :: t()
    def clear(%__MODULE__{} = state) do
      %{clear_transient(state) | detail_open?: false, hierarchy: Hierarchy.clear(state.hierarchy)}
    end
  end

  @spec events() :: [String.t()]
  def events, do: @events

  @doc "Loads detail for the scoped Task resolved by the route coordinator."
  @spec apply_route(Phoenix.LiveView.Socket.t(), Project.t(), Task.t() | nil) ::
          Phoenix.LiveView.Socket.t()
  def apply_route(socket, project, %Task{} = task) do
    case {Tasks.get_task_hierarchy(project, task), Tasks.list_blocking(project, task)} do
      {{:ok, hierarchy}, {:ok, related_tasks}} ->
        autosave = route_autosave(socket.assigns.editing, task)

        assign(
          socket,
          :editing,
          State.open(socket.assigns.editing, task, autosave, hierarchy, related_tasks)
        )

      _not_found ->
        task_not_found_modal_state(socket)
    end
  end

  def apply_route(socket, _project, nil), do: task_not_found_modal_state(socket)

  defp route_autosave(
         %State{selected_task: %Task{id: task_id}, autosave: current},
         %Task{
           id: task_id
         } = task
       ) do
    current
    |> Autosave.load(task, saved?: current.saved?)
    |> Map.put(:field_states, current.field_states)
  end

  defp route_autosave(%State{autosave: current}, %Task{} = task),
    do: Autosave.load(current, task, saved?: false)

  @doc "Restores captured detail input against fresh scoped Task authority without writing."
  @spec restore(Phoenix.LiveView.Socket.t(), State.t(), ParentPicker.t()) ::
          {:ok, Phoenix.LiveView.Socket.t()}
          | {:error, :task_not_found, Phoenix.LiveView.Socket.t()}
          | {:error, Phoenix.LiveView.Socket.t()}
  def restore(
        %{assigns: %{workspace: %{selected_project: %Project{} = project}}} = socket,
        %State{selected_task: %Task{id: task_id}, autosave: %Autosave{} = captured_autosave} =
          captured,
        %ParentPicker{} = captured_picker
      ) do
    case Tasks.get_task_for_project(project, task_id) do
      %Task{} = task ->
        case {Tasks.get_task_hierarchy(project, task), Tasks.list_blocking(project, task)} do
          {{:ok, hierarchy}, {:ok, related_tasks}} ->
            sequence = max(captured_autosave.sequence, socket.assigns.editing.autosave.sequence)
            autosave = Autosave.resume(%{captured_autosave | sequence: sequence}, task)
            editing = State.open(captured, task, autosave, hierarchy, related_tasks)
            picker = ParentPicker.reconcile(captured_picker, project, task)

            socket =
              socket
              |> assign(:editing, editing)
              |> assign(:task_parent_picker, picker)

            case Autosave.restart(socket.assigns.editing.autosave, project, task) do
              {:ok, autosave, task, schedules} ->
                socket = sync_autosave(socket, autosave, task)

                {:ok,
                 Enum.reduce(schedules, socket, fn {delay_ms, message}, socket ->
                   execute_task_autosave_schedule(socket, delay_ms, message)
                 end)}

              {:not_found, autosave} ->
                {:error, apply_task_autosave_result(socket, {:not_found, autosave})}
            end

          _not_found ->
            {:error, :task_not_found, socket}
        end

      nil ->
        {:error, :task_not_found, socket}
    end
  end

  def restore(socket, %State{}, %ParentPicker{}), do: {:error, socket}

  @doc "Clears detail data while retaining the autosave timer sequence."
  @spec clear(Phoenix.LiveView.Socket.t()) :: Phoenix.LiveView.Socket.t()
  def clear(socket), do: assign(socket, :editing, State.clear(socket.assigns.editing))

  @spec handle_event(String.t(), map(), Phoenix.LiveView.Socket.t()) ::
          {:noreply, Phoenix.LiveView.Socket.t()}
  def handle_event("toggle_task_hierarchy_node", %{"task-id" => task_id}, socket) do
    case parse_navigation_identity(task_id) do
      {:ok, task_id} ->
        {:noreply,
         update_state(socket, &State.put_hierarchy(&1, Hierarchy.toggle(&1.hierarchy, task_id)))}

      :error ->
        {:noreply, socket}
    end
  end

  def handle_event("toggle_task_hierarchy_node", _params, socket), do: {:noreply, socket}

  def handle_event("open_related_picker", %{"direction" => direction}, socket)
      when direction in ["blocks", "blocked-by"] do
    with %Project{} = project <- socket.assigns.workspace.selected_project,
         %Task{} = task <- socket.assigns.editing.selected_task do
      direction = if(direction == "blocks", do: :blocks, else: :blocked_by)
      picker = RelatedTasksPicker.open(direction, project, task, Projects.list_projects())
      {:noreply, update_state(socket, &%{&1 | related_picker: picker})}
    else
      _ -> {:noreply, socket}
    end
  end

  def handle_event("open_related_picker", _params, socket), do: {:noreply, socket}

  def handle_event("close_related_picker", _params, socket),
    do: {:noreply, update_state(socket, &%{&1 | related_picker: RelatedTasksPicker.empty()})}

  def handle_event(
        "select_related_project",
        %{"related_project" => %{"project_id" => id}},
        socket
      ) do
    picker =
      RelatedTasksPicker.select_project(
        socket.assigns.editing.related_picker,
        id,
        socket.assigns.editing.selected_task
      )

    {:noreply, update_state(socket, &%{&1 | related_picker: picker})}
  end

  def handle_event("select_related_project", _params, socket), do: {:noreply, socket}

  def handle_event("search_related_tasks", %{"related_search" => %{"query" => query}}, socket) do
    picker =
      RelatedTasksPicker.search(
        socket.assigns.editing.related_picker,
        query,
        socket.assigns.editing.selected_task
      )

    {:noreply, update_state(socket, &%{&1 | related_picker: picker})}
  end

  def handle_event("search_related_tasks", _params, socket), do: {:noreply, socket}

  def handle_event("add_related_task", %{"task-id" => id}, socket) do
    picker = socket.assigns.editing.related_picker

    case RelatedTasksPicker.candidate(picker, id) do
      %{id: candidate_id} -> mutate_related_task(socket, :add, picker.direction, candidate_id)
      _ -> {:noreply, related_error(socket, "This Task is no longer available. Search again.")}
    end
  end

  def handle_event("add_related_task", _params, socket), do: {:noreply, socket}

  def handle_event("remove_related_task", %{"direction" => direction, "task-id" => id}, socket)
      when direction in ["blocks", "blocked-by"] do
    case parse_navigation_identity(id) do
      {:ok, target_id} ->
        direction = if(direction == "blocks", do: :blocks, else: :blocked_by)
        mutate_related_task(socket, :remove, direction, target_id)

      :error ->
        {:noreply, related_error(socket, "This relationship is no longer available.")}
    end
  end

  def handle_event("remove_related_task", _params, socket), do: {:noreply, socket}

  def handle_event("keep_current_task_status", _params, socket) do
    case socket.assigns.editing.selected_task do
      %Task{} = task ->
        autosave = Autosave.cancel_done(socket.assigns.editing.autosave, task)
        {:noreply, update_state(socket, &State.put_autosave(&1, autosave))}

      _ ->
        {:noreply, socket}
    end
  end

  def handle_event("confirm_task_done", _params, socket) do
    case {socket.assigns.workspace.selected_project, socket.assigns.editing.selected_task} do
      {%Project{} = project, %Task{} = task} ->
        result = Autosave.confirm_done(socket.assigns.editing.autosave, project, task)
        {:noreply, apply_task_autosave_result(socket, result)}

      _ ->
        {:noreply, socket}
    end
  end

  def handle_event(
        "autosave_task",
        %{"_target" => ["task", field], "task" => task_params},
        socket
      ) do
    result =
      Autosave.change(
        socket.assigns.editing.autosave,
        socket.assigns.workspace.selected_project,
        socket.assigns.editing.selected_task,
        task_params,
        field
      )

    {:noreply, apply_task_autosave_result(socket, result)}
  end

  def handle_event("submit_task_edit", _params, socket) do
    case flush(socket) do
      {:ok, socket} -> {:noreply, socket}
      {:error, socket} -> {:noreply, socket}
    end
  end

  def handle_event(
        "resolve_task_conflict",
        %{"field" => field, "resolution" => resolution},
        %{
          assigns: %{
            workspace: %{selected_project: %Project{} = project},
            editing: %{selected_task: %Task{} = task, autosave: %Autosave{} = autosave}
          }
        } = socket
      )
      when is_binary(field) and is_binary(resolution) do
    case task_conflict_resolution(resolution) do
      nil ->
        {:noreply, socket}

      resolution ->
        result = Autosave.resolve_conflict(autosave, project, task, field, resolution)
        {:noreply, apply_task_autosave_result(socket, result)}
    end
  end

  def handle_event("resolve_task_conflict", _params, socket), do: {:noreply, socket}

  @spec handle_autosave_info(tuple(), Phoenix.LiveView.Socket.t()) ::
          {:noreply, Phoenix.LiveView.Socket.t()}
  def handle_autosave_info(
        {:autosave_task_field, task_id, field, revision},
        %{
          assigns: %{
            workspace: %{selected_project: %Project{} = project},
            editing: %{selected_task: %Task{} = task}
          }
        } = socket
      ) do
    result =
      Autosave.handle_scheduled_save(
        socket.assigns.editing.autosave,
        project,
        task,
        task_id,
        field,
        revision
      )

    {:noreply, apply_task_autosave_result(socket, result)}
  end

  def handle_autosave_info({:autosave_task_field, _task_id, _field, _revision}, socket) do
    {:noreply, socket}
  end

  defp apply_task_autosave_result(socket, {:ok, autosave, task}),
    do: sync_autosave(socket, autosave, task)

  defp apply_task_autosave_result(socket, {:ignored, autosave, task}),
    do: sync_autosave(socket, autosave, task)

  defp apply_task_autosave_result(socket, {:conflict, autosave, task}),
    do: sync_autosave(socket, autosave, task)

  defp apply_task_autosave_result(socket, {:error, autosave, task}),
    do: sync_autosave(socket, autosave, task)

  defp apply_task_autosave_result(
         socket,
         {:schedule, autosave, task, delay_ms, message}
       ) do
    socket
    |> sync_autosave(autosave, task)
    |> execute_task_autosave_schedule(delay_ms, message)
  end

  defp apply_task_autosave_result(socket, {:not_found, autosave}) do
    socket
    |> update_state(&State.put_autosave(&1, autosave))
    |> update_state(&%{&1 | selected_task: nil})
    |> update_state(&%{&1 | not_found?: true})
  end

  @doc "Synchronizes a persisted Task without discarding pending ordinary-field edits."
  @spec sync_persisted_task(Phoenix.LiveView.Socket.t(), Task.t()) :: Phoenix.LiveView.Socket.t()
  def sync_persisted_task(socket, %Task{} = task) do
    sync_autosave(socket, Autosave.reconcile(socket.assigns.editing.autosave, task), task)
  end

  @doc "Reloads a successfully moved selected Task and marks its flushed detail as saved."
  @spec refresh_after_move(Phoenix.LiveView.Socket.t(), pos_integer()) ::
          Phoenix.LiveView.Socket.t()
  def refresh_after_move(socket, task_id) do
    case socket.assigns.editing.selected_task do
      %Task{id: ^task_id} ->
        case Tasks.get_task_for_project(socket.assigns.workspace.selected_project, task_id) do
          %Task{} = task ->
            update_state(socket, fn editing ->
              autosave = Autosave.load(editing.autosave, task, saved?: true)

              %{
                editing
                | selected_task: task,
                  autosave: %{autosave | field_states: editing.autosave.field_states}
              }
            end)

          nil ->
            socket
        end

      _not_selected ->
        socket
    end
  end

  defp sync_autosave(socket, autosave, task) do
    title_changed? =
      case socket.assigns.editing.selected_task do
        %Task{title: title} -> title != task.title
        _ -> false
      end

    socket = update_state(socket, &State.put_autosave(&1, autosave))

    socket =
      if socket.assigns.editing.selected_task != task do
        socket
        |> update_state(&%{&1 | selected_task: task})
        |> Listing.refresh()
      else
        socket
      end

    socket =
      case {
        socket.assigns.live_action,
        socket.assigns.workspace.selected_project,
        socket.assigns.task_parent_picker
      } do
        {:show_task, %Project{} = project, %ParentPicker{} = picker} ->
          assign(socket, :task_parent_picker, ParentPicker.reconcile(picker, project, task))

        _ ->
          socket
      end

    if title_changed? do
      reload_task_hierarchy(socket, socket.assigns.workspace.selected_project, task)
    else
      socket
    end
  end

  defp execute_task_autosave_schedule(socket, 0, message) do
    send(self(), message)
    socket
  end

  defp execute_task_autosave_schedule(socket, delay_ms, message) do
    Process.send_after(self(), message, delay_ms)
    socket
  end

  @doc "Flushes pending edits and returns whether route navigation may proceed.

  Departure callers use `discard_done?: true` to discard a warned Done draft before saving.

  Ordinary navigation may leave behind invalid Task input as before. Comment departure actions
  require `allow_invalid?: false` so discarding a comment cannot silently discard Task input.
  "
  @spec flush(Phoenix.LiveView.Socket.t()) ::
          {:ok, Phoenix.LiveView.Socket.t()} | {:error, Phoenix.LiveView.Socket.t()}
  @spec flush(Phoenix.LiveView.Socket.t(), keyword()) ::
          {:ok, Phoenix.LiveView.Socket.t()} | {:error, Phoenix.LiveView.Socket.t()}
  def flush(socket, opts \\ [])

  def flush(
        %{
          assigns: %{
            workspace: %{selected_project: %Project{} = project},
            editing: %{selected_task: %Task{} = task}
          }
        } = socket,
        opts
      ) do
    autosave = socket.assigns.editing.autosave

    autosave =
      if Keyword.get(opts, :discard_done?, false) and not is_nil(autosave.done_warning) do
        Autosave.cancel_done(autosave, task)
      else
        autosave
      end

    case Autosave.flush(autosave, project, task) do
      {:ok, autosave, task} ->
        {:ok, sync_autosave(socket, autosave, task)}

      {:error, autosave, task} ->
        socket = sync_autosave(socket, autosave, task)

        if Keyword.get(opts, :allow_invalid?, true) and not autosave.save_failed? and
             autosave.conflicts == %{} do
          {:ok, socket}
        else
          {:error, socket}
        end

      {:not_found, autosave} ->
        {:ok, apply_task_autosave_result(socket, {:not_found, autosave})}
    end
  end

  def flush(socket, _opts), do: {:ok, socket}

  @doc "Restores the selected detail route when flushing blocked navigation."
  @spec restore_failed_route(Phoenix.LiveView.Socket.t(), map()) :: Phoenix.LiveView.Socket.t()
  def restore_failed_route(
        %{
          assigns: %{
            workspace: %{
              selected_project: %Project{} = project,
              selected_list: selected_list,
              include_children?: include_children?
            },
            editing: %{selected_task: %Task{} = task}
          }
        } = socket,
        params
      ) do
    if Paths.selected_task_route?(params, project, selected_list, task, include_children?) do
      socket
    else
      push_patch(socket,
        to: Paths.task_detail_path(project, selected_list, task, include_children?),
        replace: true
      )
    end
  end

  @doc "Refreshes hierarchy for an open detail, preserving disclosure state."
  @spec reload_hierarchy(Phoenix.LiveView.Socket.t()) :: Phoenix.LiveView.Socket.t()
  def reload_hierarchy(
        %{
          assigns: %{
            live_action: :show_task,
            workspace: %{selected_project: %Project{} = project},
            editing: %{selected_task: %Task{} = selected_task}
          }
        } = socket
      ),
      do: reload_task_hierarchy(socket, project, selected_task)

  def reload_hierarchy(socket), do: socket

  @doc "Refetches relationships for the selected detail from its scoped Project."
  @spec reload_related_tasks(Phoenix.LiveView.Socket.t()) :: Phoenix.LiveView.Socket.t()
  def reload_related_tasks(
        %{
          assigns: %{
            live_action: :show_task,
            workspace: %{selected_project: %Project{} = project},
            editing: %{selected_task: %Task{} = selected_task}
          }
        } = socket
      ) do
    case Tasks.list_blocking(project, selected_task) do
      {:ok, related_tasks} ->
        update_state(socket, &%{&1 | related_tasks: related_tasks})

      {:error, :not_found} ->
        task_not_found_modal_state(socket)
    end
  end

  def reload_related_tasks(socket), do: socket

  defp reload_task_hierarchy(socket, %Project{} = project, %Task{} = task) do
    case Tasks.get_task_hierarchy(project, task) do
      {:ok, hierarchy} ->
        update_state(socket, &State.put_hierarchy(&1, Hierarchy.load(&1.hierarchy, hierarchy)))

      {:error, :not_found} ->
        task_not_found_modal_state(socket)
    end
  end

  @doc """
  Reconciles open detail while retaining its draft and reports the persisted Task on success.

  Missing or inactive detail returns `:unchanged`; external picker synchronization belongs to
  the notification coordinator.
  """
  @spec reconcile(Phoenix.LiveView.Socket.t(), Taskman.ChangeNotifications.Event.t()) ::
          {Phoenix.LiveView.Socket.t(), :unchanged | {:task_reconciled, Task.t()}}
  def reconcile(
        %{
          assigns: %{
            live_action: :show_task,
            workspace: %{selected_project: %Project{} = project},
            editing: %{
              selected_task: %Task{id: task_id},
              autosave: %Autosave{} = autosave
            }
          }
        } = socket,
        _event
      ) do
    case Tasks.get_task_for_project(project, task_id) do
      %Task{} = persisted_task ->
        socket =
          socket
          |> update_state(&%{&1 | selected_task: persisted_task})
          |> update_state(&State.put_autosave(&1, Autosave.reconcile(autosave, persisted_task)))

        {socket, {:task_reconciled, persisted_task}}

      nil ->
        {socket, :unchanged}
    end
  end

  def reconcile(socket, _event), do: {socket, :unchanged}

  defp mutate_related_task(socket, action, direction, target_id)
       when action in [:add, :remove] and direction in [:blocks, :blocked_by] do
    selected = socket.assigns.editing.selected_task
    selected_project = socket.assigns.workspace.selected_project

    with %Task{} = target <- Tasks.get_task(target_id),
         {blocker_project, blocker, blocked} <-
           ordered_endpoints(direction, selected_project, selected, target),
         %Project{} <- blocker_project do
      result =
        case action do
          :add -> Tasks.add_block(blocker_project, blocker, blocked)
          :remove -> Tasks.remove_block(blocker_project, blocker, blocked)
        end

      case result do
        {:ok, _edge} ->
          socket =
            socket
            |> update_state(&%{&1 | related_picker: RelatedTasksPicker.empty()})
            |> reload_related_tasks()

          {:noreply, socket}

        {:error, reason} ->
          {:noreply, related_error(socket, related_error_message(reason))}
      end
    else
      _ -> {:noreply, related_error(socket, "This Task is no longer available. Search again.")}
    end
  end

  defp ordered_endpoints(:blocks, project, selected, target),
    do: {project, selected, target}

  defp ordered_endpoints(:blocked_by, _project, selected, target),
    do: {Projects.get_project(target.project_id), target, selected}

  defp related_error(socket, message) do
    update_state(socket, fn state ->
      %{state | related_picker: RelatedTasksPicker.reject(state.related_picker, message)}
    end)
  end

  defp related_error_message(%Ecto.Changeset{errors: errors}) do
    case Keyword.get(errors, :target_task_id) do
      {message, _} -> "Couldn’t change the relationship: #{message}."
      nil -> "Couldn’t change the relationship. Please try again."
    end
  end

  defp related_error_message(:not_found), do: "This relationship is no longer available."
  defp related_error_message(_), do: "Couldn’t change the relationship. Please try again."

  defp task_conflict_resolution("use_latest"), do: :use_latest
  defp task_conflict_resolution("keep_mine"), do: :keep_mine
  defp task_conflict_resolution(_resolution), do: nil

  defp update_state(socket, transition),
    do: assign(socket, :editing, transition.(socket.assigns.editing))

  defp parse_navigation_identity(id) do
    case Integer.parse(to_string(id)) do
      {id, ""} when id > 0 -> {:ok, id}
      _ -> :error
    end
  end

  defp task_not_found_modal_state(socket) do
    socket
    |> update_state(&State.not_found/1)
    |> Creation.clear()
    |> assign(:task_parent_picker, ParentPicker.empty())
    |> assign(:task_move, Move.clear(socket.assigns.task_move))
  end
end
