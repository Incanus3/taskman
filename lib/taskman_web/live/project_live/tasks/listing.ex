defmodule TaskmanWeb.ProjectLive.Tasks.Listing do
  import Phoenix.Component, only: [assign: 3, to_form: 2]
  import Phoenix.LiveView, only: [stream: 4]

  alias Taskman.Projects.Project
  alias Taskman.Tasks
  alias Taskman.Tasks.Task

  @events [
    "toggle_task_status_filter",
    "close_task_status_filter",
    "filter_task_statuses",
    "restore_task_statuses",
    "sort_tasks"
  ]

  @sort_fields %{
    "id" => :id,
    "title" => :title,
    "location" => :location,
    "status" => :status,
    "priority" => :priority
  }

  defmodule State do
    import Phoenix.Component, only: [to_form: 2]

    alias Taskman.Tasks.Task

    defstruct visible_statuses: [],
              status_filter_form: nil,
              filter_open?: false,
              sort: nil,
              tasks_empty?: true,
              tasks_filtered_empty?: false

    @type t :: %__MODULE__{
            visible_statuses: [Task.status()],
            status_filter_form: Phoenix.HTML.Form.t(),
            filter_open?: boolean(),
            sort: {atom(), :asc | :desc} | nil,
            tasks_empty?: boolean(),
            tasks_filtered_empty?: boolean()
          }

    @spec new([Task.status()]) :: t()
    def new(visible_statuses) do
      %__MODULE__{
        visible_statuses: visible_statuses,
        status_filter_form: status_filter_form(visible_statuses)
      }
    end

    @spec toggle_filter(t()) :: t()
    def toggle_filter(%__MODULE__{} = state), do: %{state | filter_open?: !state.filter_open?}

    @spec close_filter(t()) :: t()
    def close_filter(%__MODULE__{} = state), do: %{state | filter_open?: false}

    @spec apply_statuses(t(), [term()]) :: t()
    def apply_statuses(%__MODULE__{} = state, statuses) when is_list(statuses) do
      visible_statuses = normalize_statuses(statuses)

      %{
        state
        | visible_statuses: visible_statuses,
          status_filter_form: status_filter_form(visible_statuses)
      }
    end

    @spec sort_by(t(), atom()) :: t()
    def sort_by(%__MODULE__{sort: {field, direction}} = state, field),
      do: %{state | sort: {field, reverse_sort_direction(direction)}}

    def sort_by(%__MODULE__{} = state, field) when field in [:status, :priority],
      do: %{state | sort: {field, :desc}}

    def sort_by(%__MODULE__{} = state, field), do: %{state | sort: {field, :asc}}

    @spec available_sort(t(), boolean()) :: t()
    def available_sort(%__MODULE__{sort: {:location, _direction}} = state, false),
      do: %{state | sort: nil}

    def available_sort(%__MODULE__{} = state, _include_children?), do: state

    @spec put_results(t(), list(), boolean()) :: t()
    def put_results(%__MODULE__{} = state, tasks, tasks_filtered_empty?) do
      %{
        state
        | tasks_empty?: tasks == [],
          tasks_filtered_empty?: tasks_filtered_empty?
      }
    end

    @spec clear_results(t()) :: t()
    def clear_results(%__MODULE__{} = state), do: put_results(state, [], false)

    defp normalize_statuses(statuses) do
      Task.statuses()
      |> Enum.filter(fn status -> Atom.to_string(status) in statuses end)
    end

    defp status_filter_form(statuses) do
      to_form(%{"statuses" => Enum.map(statuses, &Atom.to_string/1)}, as: :status_filter)
    end

    defp reverse_sort_direction(:asc), do: :desc
    defp reverse_sort_direction(:desc), do: :asc
  end

  @spec events() :: [String.t()]
  def events, do: @events

  @spec handle_event(String.t(), map(), Phoenix.LiveView.Socket.t()) ::
          {:noreply, Phoenix.LiveView.Socket.t()}
  def handle_event("toggle_task_status_filter", _params, socket) do
    {:noreply, update_state(socket, &State.toggle_filter/1)}
  end

  def handle_event("close_task_status_filter", _params, socket) do
    {:noreply, update_state(socket, &State.close_filter/1)}
  end

  def handle_event("filter_task_statuses", %{"status_filter" => params}, socket)
      when is_map(params) do
    state = State.apply_statuses(socket.assigns.listing, Map.get(params, "statuses", []))
    {:noreply, socket |> assign(:listing, state) |> refresh()}
  end

  def handle_event("filter_task_statuses", _params, socket), do: {:noreply, socket}

  def handle_event("restore_task_statuses", %{"statuses" => statuses}, socket)
      when is_list(statuses) do
    state = State.apply_statuses(socket.assigns.listing, statuses)
    {:noreply, socket |> assign(:listing, state) |> refresh()}
  end

  def handle_event("restore_task_statuses", _params, socket), do: {:noreply, socket}

  def handle_event("sort_tasks", %{"field" => field}, socket) do
    case Map.fetch(@sort_fields, field) do
      {:ok, sort_field} ->
        {:noreply, socket |> update_state(&State.sort_by(&1, sort_field)) |> refresh()}

      :error ->
        {:noreply, socket}
    end
  end

  def handle_event("sort_tasks", _params, socket), do: {:noreply, socket}

  @spec refresh(Phoenix.LiveView.Socket.t()) :: Phoenix.LiveView.Socket.t()
  def refresh(
        %{assigns: %{workspace: %{selected_project: %Project{} = project} = workspace}} = socket
      ) do
    tasks =
      list_tasks_for_location(
        project,
        workspace.selected_list,
        workspace.include_children?,
        socket.assigns.listing.visible_statuses,
        socket.assigns.listing.sort
      )

    filtered_empty? =
      tasks_filtered_empty?(
        tasks,
        project,
        workspace.selected_list,
        workspace.include_children?,
        socket.assigns.listing.visible_statuses
      )

    socket
    |> update_state(&State.put_results(&1, tasks, filtered_empty?))
    |> stream(:tasks, tasks, reset: true)
  end

  def refresh(socket), do: socket

  @spec clear(Phoenix.LiveView.Socket.t()) :: Phoenix.LiveView.Socket.t()
  def clear(socket) do
    socket
    |> update_state(&State.clear_results/1)
    |> stream(:tasks, [], reset: true)
  end

  @spec list_tasks_for_location(Project.t(), term(), boolean(), [Task.status()], term()) :: list()
  def list_tasks_for_location(project, task_list, include_children?, visible_statuses, sort) do
    case Tasks.list_tasks_for_location(
           project,
           task_list,
           include_descendants: include_children?,
           statuses: visible_statuses,
           sort: sort
         ) do
      {:ok, tasks} -> tasks
      {:error, :not_found} -> []
    end
  end

  defp tasks_filtered_empty?([], %Project{} = project, task_list, include_children?, statuses) do
    statuses != Task.statuses() and
      list_tasks_for_location(project, task_list, include_children?, Task.statuses(), nil) != []
  end

  defp tasks_filtered_empty?(_tasks, _project, _task_list, _include_children?, _statuses),
    do: false

  defp update_state(socket, transition),
    do: assign(socket, :listing, transition.(socket.assigns.listing))
end
