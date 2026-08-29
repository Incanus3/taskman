defmodule TaskmanWeb.TaskMove do
  alias Taskman.Lists
  alias Taskman.Lists.TaskList
  alias Taskman.Projects.Project
  alias Taskman.Tasks
  alias Taskman.Tasks.{Task, TaskWithLocation}

  defstruct active_task: nil,
            query: "",
            destination: nil,
            options: [],
            options_open?: false,
            error: nil

  @type origin :: :row | :detail
  @type destination_option :: %{
          id: pos_integer(),
          value: String.t(),
          label: String.t(),
          current?: boolean()
        }
  @type active_task :: %{
          task_id: pos_integer(),
          origin: origin(),
          current_destination: String.t(),
          task_with_location: TaskWithLocation.t() | nil
        }
  @type t :: %__MODULE__{
          active_task: active_task() | nil,
          query: String.t(),
          destination: String.t() | nil,
          options: [destination_option()],
          options_open?: boolean(),
          error: String.t() | nil
        }

  @spec empty() :: t()
  def empty, do: %__MODULE__{}

  @spec clear(t()) :: t()
  def clear(%__MODULE__{}), do: empty()

  @spec active?(t()) :: boolean()
  def active?(%__MODULE__{active_task: active_task}), do: not is_nil(active_task)

  @spec active_for?(t(), pos_integer(), origin()) :: boolean()
  def active_for?(
        %__MODULE__{active_task: %{task_id: task_id, origin: origin}},
        task_id,
        origin
      ),
      do: true

  def active_for?(%__MODULE__{}, _task_id, _origin), do: false

  @spec current_destination(t()) :: String.t() | nil
  def current_destination(%__MODULE__{
        active_task: %{current_destination: current_destination}
      }),
      do: current_destination

  def current_destination(%__MODULE__{}), do: nil

  @spec put_error(t(), String.t()) :: t()
  def put_error(%__MODULE__{} = state, message) when is_binary(message),
    do: %{state | error: message}

  @spec open(t(), Project.t(), Task.t(), origin()) :: t()
  def open(%__MODULE__{}, %Project{} = project, %Task{} = task, origin)
      when origin in [:row, :detail] do
    task_lists = Lists.list_lists_for_project(project)

    active_task = %{
      task_id: task.id,
      origin: origin,
      current_destination: task_destination(task),
      task_with_location: if(origin == :row, do: task_with_location(task_lists, task), else: nil)
    }

    %__MODULE__{
      active_task: active_task,
      options: destination_options(project, task_lists, task)
    }
  end

  @spec open_destinations(t()) :: t()
  def open_destinations(%__MODULE__{} = state), do: %{state | options_open?: true}

  @spec select_destination(t(), String.t()) :: t()
  def select_destination(%__MODULE__{} = state, destination) when is_binary(destination) do
    query =
      Enum.find_value(state.options, state.query, fn option ->
        if option.value == destination, do: option.label
      end)

    %{
      state
      | query: query,
        destination: destination,
        options_open?: false,
        error: nil
    }
  end

  @spec search(t(), Project.t(), String.t()) ::
          {:ok, t(), Task.t()} | {:error, t(), :task_not_found}
  def search(%__MODULE__{} = state, %Project{} = project, query) when is_binary(query) do
    destination = if query == state.query, do: state.destination, else: nil

    state = %{
      state
      | query: query,
        destination: destination,
        options_open?: true
    }

    refresh(state, project)
  end

  @spec refresh(t(), Project.t()) ::
          {:ok, t(), Task.t()} | {:error, t(), :task_not_found}
  def refresh(
        %__MODULE__{active_task: %{task_id: task_id} = active_task} = state,
        %Project{} = project
      ) do
    case Tasks.get_task_for_project(project, task_id) do
      nil ->
        {:error, clear(state), :task_not_found}

      %Task{} = persisted_task ->
        task_lists = Lists.list_lists_for_project(project)
        options = destination_options(project, task_lists, persisted_task)
        destination = retain_destination(options, state.destination)

        active_task = %{
          active_task
          | current_destination: task_destination(persisted_task),
            task_with_location:
              if(active_task.origin == :row,
                do: task_with_location(task_lists, persisted_task),
                else: nil
              )
        }

        refreshed = %{
          state
          | active_task: active_task,
            destination: destination,
            options: filtered_options(options, state.query),
            error: nil
        }

        {:ok, refreshed, persisted_task}
    end
  end

  @spec submit(t(), Project.t()) ::
          {:ok, t(), Task.t()}
          | {:error, t(),
             :task_not_found | :destination_not_found | :unchanged_location | :move_failed}
  def submit(
        %__MODULE__{active_task: %{task_id: task_id}} = state,
        %Project{} = project
      ) do
    case Tasks.get_task_for_project(project, task_id) do
      nil ->
        {:error, put_error(state, "This Task is no longer available."), :task_not_found}

      %Task{} = task ->
        submit_persisted_task(state, project, task)
    end
  end

  defp submit_persisted_task(state, project, task) do
    case resolve_destination(project, state.destination) do
      {:error, :destination_not_found} ->
        {:error, put_error(state, "That destination is no longer available."),
         :destination_not_found}

      {:ok, destination} ->
        case Tasks.move_task(project, task, destination) do
          {:ok, moved_task} ->
            {:ok, clear(state), moved_task}

          {:error, :unchanged_location} ->
            case refresh(state, project) do
              {:ok, refreshed, _persisted_task} ->
                {:error, put_error(refreshed, "This Task is already in that location."),
                 :unchanged_location}

              {:error, _cleared, :task_not_found} ->
                {:error, put_error(state, "This Task is no longer available."), :task_not_found}
            end

          {:error, :not_found} ->
            {:error, put_error(state, "That destination is no longer available."),
             :destination_not_found}

          {:error, _reason} ->
            {:error, put_error(state, "Couldn’t move this Task. Please try again."), :move_failed}
        end
    end
  end

  defp resolve_destination(_project, "project"), do: {:ok, nil}

  defp resolve_destination(project, "list:" <> list_id) do
    case Lists.get_list_for_project(project, list_id) do
      %TaskList{} = destination -> {:ok, destination}
      nil -> {:error, :destination_not_found}
    end
  end

  defp resolve_destination(_project, _destination),
    do: {:error, :destination_not_found}

  defp task_destination(%Task{list_id: nil}), do: "project"
  defp task_destination(%Task{list_id: list_id}), do: "list:#{list_id}"

  defp task_with_location(task_lists, task) do
    task_list = Enum.find(task_lists, &(&1.id == task.list_id))
    %TaskWithLocation{task: task, location_path: Lists.path_for(task_lists, task_list)}
  end

  defp destination_options(project, task_lists, task) do
    [
      %{
        id: project.id,
        value: "project",
        label: "Project · #{project.name}",
        current?: is_nil(task.list_id)
      }
      | Enum.map(ordered_task_lists(task_lists), fn task_list ->
          %{
            id: task_list.id,
            value: "list:#{task_list.id}",
            label:
              task_lists
              |> Lists.path_for(task_list)
              |> Enum.map_join(" / ", & &1.name),
            current?: task.list_id == task_list.id
          }
        end)
    ]
  end

  defp retain_destination(options, destination) do
    if Enum.any?(options, &(&1.value == destination)), do: destination, else: nil
  end

  defp filtered_options(options, query) do
    normalized_query = String.downcase(query)

    Enum.filter(options, fn option ->
      option.label
      |> String.downcase()
      |> String.contains?(normalized_query)
    end)
  end

  defp ordered_task_lists(task_lists) do
    children_by_parent = Enum.group_by(task_lists, & &1.parent_list_id)

    children_by_parent
    |> Map.get(nil, [])
    |> Enum.flat_map(&task_list_preorder(&1, children_by_parent))
  end

  defp task_list_preorder(task_list, children_by_parent) do
    [
      task_list
      | Enum.flat_map(
          Map.get(children_by_parent, task_list.id, []),
          &task_list_preorder(&1, children_by_parent)
        )
    ]
  end
end
