defmodule TaskmanWeb.ProjectLive.ListEdit do
  alias Taskman.Lists
  alias Taskman.Lists.{NavigationNode, TaskList}
  alias Taskman.Projects.Project

  defstruct project: nil, action: nil, form: nil

  @type action :: {:new, TaskList.t() | nil} | {:rename, TaskList.t()}
  @type t :: %__MODULE__{
          project: Project.t() | nil,
          action: action() | nil,
          form: Phoenix.HTML.Form.t() | nil
        }

  @spec empty() :: t()
  def empty, do: %__MODULE__{}

  @spec clear(t()) :: t()
  def clear(%__MODULE__{}), do: empty()

  @spec reconcile(t(), [Project.t()], %{optional(pos_integer()) => [TaskList.t()]}) :: t()
  def reconcile(
        %__MODULE__{project: %Project{id: project_id}} = state,
        projects,
        lists_by_project
      )
      when is_list(projects) and is_map(lists_by_project) do
    case Enum.find(projects, &(&1.id == project_id)) do
      %Project{} = project ->
        task_lists = Map.get(lists_by_project, project_id, [])
        %{state | project: project, action: reconcile_action(state.action, task_lists)}

      nil ->
        state
    end
  end

  def reconcile(%__MODULE__{} = state, _projects, _lists_by_project), do: state

  @spec open_new(Project.t(), TaskList.t() | nil) :: t()
  def open_new(%Project{} = project, parent) when is_nil(parent) or is_struct(parent, TaskList) do
    task_list = %TaskList{project_id: project.id, parent_list_id: parent && parent.id}

    %__MODULE__{
      project: project,
      action: {:new, parent},
      form: list_form(task_list)
    }
  end

  @spec open_rename(Project.t(), TaskList.t()) :: t()
  def open_rename(%Project{} = project, %TaskList{} = task_list) do
    %__MODULE__{
      project: project,
      action: {:rename, task_list},
      form: list_form(task_list)
    }
  end

  @spec validate(t(), map()) :: {:ok, t()} | {:error, :not_found}
  def validate(%__MODULE__{} = state, attrs) when is_map(attrs) do
    case target(state) do
      {:ok, action, task_list} ->
        form =
          task_list
          |> Lists.change_list(attrs)
          |> Map.put(:action, :validate)
          |> Phoenix.Component.to_form(as: :list)

        {:ok, %{state | action: action, form: form}}

      :error ->
        {:error, :not_found}
    end
  end

  @spec target(t()) :: {:ok, action(), TaskList.t()} | :error
  def target(%__MODULE__{project: %Project{} = project, action: {:new, nil}}) do
    {:ok, {:new, nil}, %TaskList{project_id: project.id}}
  end

  def target(%__MODULE__{
        project: %Project{} = project,
        action: {:new, %TaskList{id: parent_id}}
      }) do
    case Lists.get_list_for_project(project, parent_id) do
      %TaskList{} = parent ->
        {:ok, {:new, parent}, %TaskList{project_id: project.id, parent_list_id: parent.id}}

      nil ->
        :error
    end
  end

  def target(%__MODULE__{
        project: %Project{} = project,
        action: {:rename, %TaskList{id: list_id}}
      }) do
    case Lists.get_list_for_project(project, list_id) do
      %TaskList{} = task_list -> {:ok, {:rename, task_list}, task_list}
      nil -> :error
    end
  end

  def target(%__MODULE__{}), do: :error

  @spec put_error(t(), Ecto.Changeset.t()) :: t()
  def put_error(%__MODULE__{} = state, %Ecto.Changeset{} = changeset) do
    form =
      changeset
      |> Map.put(:action, :validate)
      |> Phoenix.Component.to_form(as: :list)

    %{state | form: form}
  end

  @spec active_for?(t(), NavigationNode.t()) :: boolean()
  def active_for?(
        %__MODULE__{project: %Project{id: project_id}, action: {:new, nil}},
        %NavigationNode{kind: :project, project: %Project{id: project_id}}
      ),
      do: true

  def active_for?(
        %__MODULE__{
          project: %Project{id: project_id},
          action: {:new, %TaskList{id: parent_id}}
        },
        %NavigationNode{
          kind: :list,
          project: %Project{id: project_id},
          task_list: %TaskList{id: parent_id}
        }
      ),
      do: true

  def active_for?(
        %__MODULE__{
          project: %Project{id: project_id},
          action: {:rename, %TaskList{id: list_id}}
        },
        %NavigationNode{
          kind: :list,
          project: %Project{id: project_id},
          task_list: %TaskList{id: list_id}
        }
      ),
      do: true

  def active_for?(%__MODULE__{}, %NavigationNode{}), do: false

  defp reconcile_action({:new, nil} = action, _task_lists), do: action

  defp reconcile_action({:new, %TaskList{id: parent_id} = parent}, task_lists) do
    {:new, Enum.find(task_lists, &(&1.id == parent_id)) || parent}
  end

  defp reconcile_action({:rename, %TaskList{id: list_id} = task_list}, task_lists) do
    {:rename, Enum.find(task_lists, &(&1.id == list_id)) || task_list}
  end

  defp reconcile_action(action, _task_lists), do: action

  @spec form_id(t()) :: String.t() | nil
  def form_id(%__MODULE__{action: {:new, nil}}), do: "list-create-form-root"

  def form_id(%__MODULE__{action: {:new, %TaskList{id: id}}}),
    do: "list-create-form-#{id}"

  def form_id(%__MODULE__{action: {:rename, %TaskList{id: id}}}),
    do: "list-rename-form-#{id}"

  def form_id(%__MODULE__{}), do: nil

  @spec title(t()) :: String.t() | nil
  def title(%__MODULE__{action: {:new, nil}}), do: "New List"

  def title(%__MODULE__{action: {:new, %TaskList{name: name}}}),
    do: "New child List of #{name}"

  def title(%__MODULE__{action: {:rename, %TaskList{name: name}}}), do: "Rename #{name}"
  def title(%__MODULE__{}), do: nil

  defp list_form(task_list) do
    task_list
    |> Lists.change_list()
    |> Phoenix.Component.to_form(as: :list)
  end
end
