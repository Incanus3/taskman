defmodule TaskmanWeb.ProjectLive.Tasks.Listing do
  import Phoenix.Component, only: [assign: 3]
  import Phoenix.LiveView, only: [stream: 4]

  alias Taskman.Projects.Project
  alias Taskman.Tasks
  alias Taskman.Tasks.{ListingProjection, Task}
  alias TaskmanWeb.ProjectLive.{TaskTablePreferences, Workspace}
  alias TaskmanWeb.ProjectLive.Tasks.Move

  @events [
    "toggle_task_status_filter",
    "close_task_status_filter",
    "filter_task_statuses",
    "sort_tasks",
    "toggle_task_sort",
    "close_task_sort",
    "toggle_task_subtasks"
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

    defstruct location_key: nil,
              expanded_task_ids: MapSet.new(),
              expandable_root_ids: MapSet.new(),
              rendered_task_ids: MapSet.new(),
              task_counts: %{},
              visible_statuses: [],
              status_filter_form: nil,
              filter_open?: false,
              sort: nil,
              sort_open?: false,
              tasks_empty?: true,
              tasks_filtered_empty?: false

    @type t :: %__MODULE__{
            location_key: {pos_integer(), pos_integer() | nil} | nil,
            expanded_task_ids: MapSet.t(pos_integer()),
            expandable_root_ids: MapSet.t(pos_integer()),
            rendered_task_ids: MapSet.t(pos_integer()),
            task_counts: Taskman.Tasks.Counts.t(),
            visible_statuses: [Task.status()],
            status_filter_form: Phoenix.HTML.Form.t(),
            filter_open?: boolean(),
            sort: {atom(), :asc | :desc} | nil,
            sort_open?: boolean(),
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
      visible_statuses = TaskTablePreferences.normalize_statuses(statuses)

      %{
        state
        | visible_statuses: visible_statuses,
          status_filter_form: status_filter_form(visible_statuses)
      }
    end

    @spec sort_by(t(), atom()) :: t()
    def sort_by(%__MODULE__{sort: {field, direction}} = state, field),
      do: %{state | sort: {field, reverse_sort_direction(direction)}, sort_open?: false}

    def sort_by(%__MODULE__{} = state, field) when field in [:status, :priority],
      do: %{state | sort: {field, :desc}, sort_open?: false}

    def sort_by(%__MODULE__{} = state, field),
      do: %{state | sort: {field, :asc}, sort_open?: false}

    @spec available_sort(t(), boolean()) :: t()
    def available_sort(%__MODULE__{sort: {:location, _direction}} = state, false),
      do: %{state | sort: nil}

    def available_sort(%__MODULE__{} = state, _include_children?), do: state

    @spec for_location(t(), {pos_integer(), pos_integer() | nil}) :: t()
    def for_location(%__MODULE__{location_key: key} = state, key), do: state

    def for_location(%__MODULE__{} = state, key),
      do: %{clear_results(state) | location_key: key}

    @spec put_projection(t(), ListingProjection.t()) :: t()
    def put_projection(%__MODULE__{} = state, %ListingProjection{} = projection) do
      %{
        state
        | expanded_task_ids: projection.expanded_task_ids,
          expandable_root_ids: projection.expandable_root_ids,
          rendered_task_ids: MapSet.new(projection.rows, & &1.task_with_location.task.id),
          tasks_empty?: projection.matching_empty?,
          tasks_filtered_empty?: projection.filtered_empty?
      }
    end

    @spec clear_results(t()) :: t()
    def clear_results(%__MODULE__{} = state) do
      %{put_projection(state, %ListingProjection{}) | location_key: nil}
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

    {:noreply,
     socket
     |> assign(:listing, state)
     |> refresh(close_hidden_move?: true)
     |> TaskTablePreferences.changed()}
  end

  def handle_event("filter_task_statuses", _params, socket), do: {:noreply, socket}

  def handle_event("toggle_task_sort", _params, socket) do
    {:noreply, update_state(socket, &%{&1 | sort_open?: !&1.sort_open?})}
  end

  def handle_event("close_task_sort", _params, socket) do
    {:noreply, update_state(socket, &%{&1 | sort_open?: false})}
  end

  def handle_event("sort_tasks", %{"field" => field}, socket) do
    case Map.fetch(@sort_fields, field) do
      {:ok, sort_field} ->
        {:noreply, socket |> update_state(&State.sort_by(&1, sort_field)) |> refresh_table()}

      :error ->
        {:noreply, socket}
    end
  end

  def handle_event("sort_tasks", _params, socket), do: {:noreply, socket}

  def handle_event("toggle_task_subtasks", %{"task-id" => value}, socket)
      when is_binary(value) do
    with true <- value =~ ~r/\A[0-9]+\z/,
         {id, ""} when id > 0 <- Integer.parse(value),
         true <- MapSet.member?(socket.assigns.listing.expandable_root_ids, id) do
      {:noreply, refresh_table(socket, close_hidden_move?: true, toggle_task_id: id)}
    else
      _invalid -> {:noreply, socket}
    end
  end

  def handle_event("toggle_task_subtasks", _params, socket), do: {:noreply, socket}

  @spec refresh(Phoenix.LiveView.Socket.t(), keyword()) :: Phoenix.LiveView.Socket.t()
  def refresh(socket, opts \\ []), do: socket |> refresh_table(opts) |> refresh_counts()

  @doc "Refreshes projected rows without recomputing counts or navigation."
  @spec refresh_table(Phoenix.LiveView.Socket.t(), keyword()) :: Phoenix.LiveView.Socket.t()
  def refresh_table(socket, opts \\ [])

  def refresh_table(%{assigns: %{workspace: %{location_not_found?: true}}} = socket, opts) do
    socket
    |> update_state(&State.clear_results/1)
    |> stream(:tasks, [], reset: true)
    |> close_hidden_move(opts)
  end

  def refresh_table(
        %{assigns: %{workspace: %{selected_project: %Project{} = project} = workspace}} = socket,
        opts
      ) do
    key = {project.id, workspace.selected_list && workspace.selected_list.id}
    state = State.for_location(socket.assigns.listing, key)

    projection =
      case Tasks.list_task_rows_for_location(project, workspace.selected_list,
             include_descendants: workspace.include_children?,
             statuses: state.visible_statuses,
             sort: state.sort,
             expanded_task_ids: state.expanded_task_ids,
             toggle_task_id: Keyword.get(opts, :toggle_task_id)
           ) do
        {:ok, projection} -> projection
        {:error, :not_found} -> %ListingProjection{}
      end

    socket
    |> assign(:listing, State.put_projection(state, projection))
    |> stream(:tasks, projection.rows, reset: true)
    |> close_hidden_move(opts)
  end

  def refresh_table(socket, _opts), do: socket

  @spec row_visible?(Phoenix.LiveView.Socket.t(), pos_integer()) :: boolean()
  def row_visible?(socket, task_id) do
    not socket.assigns.workspace.location_not_found? and
      MapSet.member?(socket.assigns.listing.rendered_task_ids, task_id)
  end

  defp close_hidden_move(socket, opts) do
    case {Keyword.get(opts, :close_hidden_move?, false), socket.assigns.task_move} do
      {true, %Move{active_task: %{origin: :row, task_id: id}}} ->
        if row_visible?(socket, id),
          do: socket,
          else: assign(socket, :task_move, Move.clear(socket.assigns.task_move))

      _other ->
        socket
    end
  end

  @spec clear(Phoenix.LiveView.Socket.t()) :: Phoenix.LiveView.Socket.t()
  def clear(socket) do
    socket
    |> update_state(&State.clear_results/1)
    |> stream(:tasks, [], reset: true)
    |> refresh_counts()
  end

  @doc "Refreshes location counts and re-renders the active navigation rows."
  @spec refresh_counts(Phoenix.LiveView.Socket.t()) :: Phoenix.LiveView.Socket.t()
  def refresh_counts(socket) do
    counts =
      case socket.assigns.workspace.selected_project do
        %Project{} = project ->
          Tasks.count_tasks_by_location(
            project,
            socket.assigns.listing.visible_statuses,
            socket.assigns.workspace.include_children?
          )

        nil ->
          %{}
      end

    socket
    |> update_state(&%{&1 | task_counts: counts})
    |> Workspace.refresh()
  end

  defp update_state(socket, transition),
    do: assign(socket, :listing, transition.(socket.assigns.listing))
end
