defmodule TaskmanWeb.TaskParentPicker do
  alias Taskman.Projects.Project
  alias Taskman.Tasks
  alias Taskman.Tasks.{Task, TaskWithLocation}

  defstruct mode: nil,
            current_task: nil,
            query: "",
            selected_parent: nil,
            options: [],
            options_open?: false,
            active_option_id: nil,
            error: nil

  @type mode :: :create | :edit | nil

  @type t :: %__MODULE__{
          mode: mode(),
          current_task: Task.t() | nil,
          query: String.t(),
          selected_parent: Task.t() | nil,
          options: [TaskWithLocation.t()],
          options_open?: boolean(),
          active_option_id: String.t() | nil,
          error: String.t() | nil
        }

  @spec empty() :: t()
  def empty, do: %__MODULE__{}

  @spec open_create(t(), Project.t(), Task.t() | TaskWithLocation.t() | nil) :: t()
  def open_create(%__MODULE__{}, %Project{} = _project, selected_parent) do
    selected_parent = normalize_parent(selected_parent)

    %__MODULE__{
      mode: :create,
      selected_parent: selected_parent,
      query: parent_query(selected_parent)
    }
  end

  @spec open_edit(t(), Project.t(), Task.t()) :: t()
  def open_edit(%__MODULE__{}, %Project{} = project, %Task{} = task) do
    selected_parent =
      if is_integer(task.parent_task_id) do
        Tasks.get_task_for_project(project, task.parent_task_id)
      end

    %__MODULE__{
      mode: :edit,
      current_task: task,
      selected_parent: selected_parent,
      query: parent_query(selected_parent)
    }
  end

  @spec open_options(t(), Project.t()) :: t()
  def open_options(
        %__MODULE__{current_task: current_task, selected_parent: selected_parent} = state,
        %Project{} = project
      ) do
    %{
      state
      | query: "",
        options: search_candidates(project, current_task, "", selected_parent),
        options_open?: true,
        active_option_id: nil
    }
  end

  @spec toggle_options(t(), Project.t()) :: t()
  def toggle_options(%__MODULE__{options_open?: true} = state, %Project{}),
    do: close_options(state)

  def toggle_options(%__MODULE__{} = state, %Project{} = project),
    do: open_options(state, project)

  @spec close_options(t()) :: t()
  def close_options(%__MODULE__{} = state) do
    %{
      state
      | query: parent_query(state.selected_parent),
        options_open?: false,
        active_option_id: nil
    }
  end

  @spec search(t(), Project.t(), String.t()) :: t()
  def search(
        %__MODULE__{current_task: current_task} = state,
        %Project{} = project,
        query
      )
      when is_binary(query) do
    active_option_id = if state.query == query, do: state.active_option_id

    %{
      state
      | query: query,
        options: search_candidates(project, current_task, query, state.selected_parent),
        options_open?: true,
        active_option_id: active_option_id
    }
  end

  @spec select_draft(t(), Project.t(), pos_integer() | String.t() | nil) :: t()
  def select_draft(%__MODULE__{} = state, %Project{} = project, parent_id)
      when is_integer(parent_id) or is_binary(parent_id) or is_nil(parent_id) do
    case normalize_parent_id(parent_id) do
      :clear ->
        clear_draft(state)

      {:ok, parent_id} ->
        case find_parent(state, project, parent_id) do
          %Task{} = parent ->
            error = if same_parent?(state.selected_parent, parent), do: state.error, else: nil

            %{
              state
              | query: parent_query(parent),
                selected_parent: parent,
                options_open?: false,
                active_option_id: nil,
                error: error
            }

          nil ->
            %{state | options_open?: false, active_option_id: nil}
        end

      :invalid ->
        %{state | options_open?: false, active_option_id: nil}
    end
  end

  @spec clear_draft(t()) :: t()
  def clear_draft(%__MODULE__{} = state) do
    error = if is_nil(state.selected_parent), do: state.error, else: nil

    %{
      state
      | query: "",
        selected_parent: nil,
        options_open?: false,
        active_option_id: nil,
        error: error
    }
  end

  @spec reject_draft(t(), String.t()) :: t()
  def reject_draft(%__MODULE__{} = state, message) when is_binary(message) do
    %{state | options_open?: false, active_option_id: nil, error: message}
  end

  @spec selected_parent(t()) :: Task.t() | nil
  def selected_parent(%__MODULE__{selected_parent: selected_parent}), do: selected_parent

  @spec active_option_id(t()) :: String.t() | nil
  def active_option_id(%__MODULE__{active_option_id: active_option_id}), do: active_option_id

  @spec keydown(t(), String.t()) ::
          {:move, t()} | {:select, pos_integer() | nil} | {:close, t()} | :ignore
  def keydown(%__MODULE__{options_open?: true} = state, key)
      when key in ["ArrowDown", "ArrowUp"] do
    {:move, move_active(state, key)}
  end

  def keydown(%__MODULE__{options_open?: true} = state, "Escape") do
    {:close, close_options(state)}
  end

  def keydown(%__MODULE__{options_open?: true, active_option_id: active_option_id}, "Enter")
      when is_binary(active_option_id) do
    case active_option_id do
      "task-parent-clear" -> {:select, nil}
      "task-parent-option-" <> id -> parse_active_id(id)
      _ -> :ignore
    end
  end

  def keydown(%__MODULE__{}, _key), do: :ignore

  @spec save_edit(t(), Project.t(), Task.t()) ::
          {:ok, t(), Task.t()}
          | {:error, t(), :not_found | Ecto.Changeset.t() | term()}
  def save_edit(
        %__MODULE__{selected_parent: selected_parent} = state,
        %Project{} = project,
        %Task{} = task
      ) do
    case Tasks.update_task(project, task, %{}, parent: selected_parent) do
      {:ok, %Task{} = updated_task} ->
        refreshed_parent =
          if is_integer(updated_task.parent_task_id) do
            Tasks.get_task_for_project(project, updated_task.parent_task_id)
          end

        refreshed_state = %{
          state
          | mode: :edit,
            current_task: updated_task,
            query: parent_query(refreshed_parent),
            selected_parent: refreshed_parent,
            options: [],
            options_open?: false,
            error: nil
        }

        {:ok, refreshed_state, updated_task}

      {:error, :not_found} ->
        {:error, reject_draft(state, "That parent Task is no longer available."), :not_found}

      {:error, %Ecto.Changeset{} = changeset} ->
        message = changeset_error_message(changeset)
        {:error, reject_draft(state, message), changeset}

      {:error, reason} ->
        {:error, reject_draft(state, "Couldn’t save the parent. Please try again."), reason}
    end
  end

  defp search_candidates(project, current_task, query, selected_parent) do
    options = Tasks.search_parent_candidates(project, current_task, query)

    selected_options =
      case selected_parent do
        %Task{id: selected_id} when is_integer(selected_id) ->
          Tasks.search_parent_candidates(
            project,
            current_task,
            Integer.to_string(selected_id),
            limit: 1
          )

        _ ->
          []
      end

    (selected_options ++ options)
    |> Enum.uniq_by(& &1.task.id)
    |> Enum.take(20)
  end

  defp move_active(%__MODULE__{} = state, key) do
    ids = option_ids(state)

    case ids do
      [] ->
        %{state | active_option_id: nil}

      ids ->
        current_index = Enum.find_index(ids, &(&1 == state.active_option_id))

        next_index =
          case {key, current_index} do
            {"ArrowDown", nil} -> 0
            {"ArrowUp", nil} -> length(ids) - 1
            {"ArrowDown", index} -> min(index + 1, length(ids) - 1)
            {"ArrowUp", index} -> max(index - 1, 0)
          end

        %{state | active_option_id: Enum.at(ids, next_index)}
    end
  end

  defp option_ids(%__MODULE__{} = state) do
    no_parent_ids = if show_no_parent?(state), do: ["task-parent-clear"], else: []
    no_parent_ids ++ Enum.map(state.options, &option_id/1)
  end

  defp show_no_parent?(%__MODULE__{mode: :edit}), do: true
  defp show_no_parent?(%__MODULE__{selected_parent: %Task{}}), do: true
  defp show_no_parent?(%__MODULE__{}), do: false

  defp option_id(%TaskWithLocation{task: %Task{id: id}}), do: "task-parent-option-#{id}"

  defp parse_active_id(id) do
    case Integer.parse(id) do
      {parent_id, ""} when parent_id > 0 -> {:select, parent_id}
      _ -> :ignore
    end
  end

  defp normalize_parent(nil), do: nil
  defp normalize_parent(%Task{} = parent), do: parent
  defp normalize_parent(%TaskWithLocation{task: %Task{} = parent}), do: parent
  defp normalize_parent(_parent), do: nil

  defp parent_query(nil), do: ""
  defp parent_query(%Task{title: title}) when is_binary(title), do: title
  defp parent_query(%Task{}), do: ""

  defp normalize_parent_id(nil), do: :clear
  defp normalize_parent_id(""), do: :clear
  defp normalize_parent_id(id) when is_integer(id) and id > 0, do: {:ok, id}

  defp normalize_parent_id(id) when is_binary(id) do
    case Integer.parse(id) do
      {parsed, ""} when parsed > 0 -> {:ok, parsed}
      _ -> :invalid
    end
  end

  defp normalize_parent_id(_id), do: :invalid

  defp find_parent(%__MODULE__{options: options, selected_parent: selected_parent}, project, id) do
    option_parent =
      Enum.find_value(options, fn
        %TaskWithLocation{task: %Task{id: ^id} = parent} -> parent
        _ -> nil
      end)

    cond do
      option_parent -> option_parent
      match?(%Task{id: ^id}, selected_parent) -> selected_parent
      true -> Tasks.get_task_for_project(project, id)
    end
  end

  defp same_parent?(%Task{id: left_id}, %Task{id: right_id}), do: left_id == right_id
  defp same_parent?(_, _), do: false

  defp changeset_error_message(%Ecto.Changeset{} = changeset) do
    if cycle_error?(changeset) do
      "That parent would create a cycle."
    else
      "Couldn’t save the parent. Please try again."
    end
  end

  defp cycle_error?(%Ecto.Changeset{errors: errors}) do
    Enum.any?(errors, fn
      {:parent_task_id, {message, _opts}} when is_binary(message) ->
        String.contains?(String.downcase(message), "cycle")

      _ ->
        false
    end)
  end
end
