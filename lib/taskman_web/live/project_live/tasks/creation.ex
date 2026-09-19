defmodule TaskmanWeb.ProjectLive.Tasks.Creation do
  import Phoenix.Component, only: [assign: 3, to_form: 1]
  import Phoenix.LiveView, only: [push_patch: 2]

  alias Taskman.Lists
  alias Taskman.Lists.TaskList
  alias Taskman.Projects.Project
  alias Taskman.Tasks
  alias Taskman.Tasks.Task
  alias TaskmanWeb.ProjectLive.Paths
  alias TaskmanWeb.ProjectLive.Tasks.Listing
  alias TaskmanWeb.ProjectLive.Tasks.ParentPicker

  @events ["validate_task", "save_task"]

  defmodule State do
    alias Taskman.Lists.TaskList

    defstruct form: nil, enabled?: false, location: nil

    @type t :: %__MODULE__{
            form: Phoenix.HTML.Form.t() | nil,
            enabled?: boolean(),
            location: TaskList.t() | nil
          }

    @spec empty() :: t()
    def empty, do: %__MODULE__{}

    @spec open(t(), Phoenix.HTML.Form.t(), TaskList.t() | nil) :: t()
    def open(%__MODULE__{} = state, form, location) do
      %{state | form: form, enabled?: form.source.valid?, location: location}
    end

    @spec validate(t(), Phoenix.HTML.Form.t()) :: t()
    def validate(%__MODULE__{} = state, form), do: open(state, form, state.location)

    @spec refresh_location(t(), [TaskList.t()]) :: t()
    def refresh_location(%__MODULE__{location: %TaskList{id: list_id}} = state, task_lists) do
      case Enum.find(task_lists, &(&1.id == list_id)) do
        %TaskList{} = task_list -> %{state | location: task_list}
        nil -> state
      end
    end

    def refresh_location(%__MODULE__{} = state, _task_lists), do: state

    @spec clear(t()) :: t()
    def clear(%__MODULE__{}), do: empty()
  end

  @spec events() :: [String.t()]
  def events, do: @events

  @spec apply_route(Phoenix.LiveView.Socket.t(), map()) :: Phoenix.LiveView.Socket.t()
  def apply_route(socket, params) do
    project = socket.assigns.workspace.selected_project
    selected_list = socket.assigns.workspace.selected_list
    changeset = Tasks.change_task(project)

    {location, parent_picker} = task_create_state(project, selected_list, params)
    creation = State.open(socket.assigns.creation, to_form(changeset), location)

    socket
    |> assign(:creation, creation)
    |> assign(:task_parent_picker, parent_picker)
  end

  @spec handle_event(String.t(), map(), Phoenix.LiveView.Socket.t()) ::
          {:noreply, Phoenix.LiveView.Socket.t()}
  def handle_event("validate_task", %{"task" => task_params}, socket) do
    changeset =
      socket.assigns.workspace.selected_project
      |> Tasks.change_task(task_params)
      |> Map.put(:action, :validate)

    creation = State.validate(socket.assigns.creation, to_form(changeset))
    {:noreply, assign(socket, :creation, creation)}
  end

  def handle_event("save_task", %{"task" => task_params}, socket) do
    case Tasks.create_task(
           socket.assigns.workspace.selected_project,
           socket.assigns.creation.location,
           task_params,
           parent: ParentPicker.selected_parent(socket.assigns.task_parent_picker)
         ) do
      {:ok, _task} ->
        socket = Listing.refresh(socket)

        {:noreply,
         push_patch(socket,
           to:
             Paths.browse_path(
               socket.assigns.workspace.selected_project,
               socket.assigns.workspace.selected_list,
               socket.assigns.workspace.include_children?
             )
         )}

      {:error, :not_found} ->
        {:noreply,
         assign(
           socket,
           :task_parent_picker,
           ParentPicker.reject_draft(
             socket.assigns.task_parent_picker,
             "That parent Task is no longer available."
           )
         )}

      {:error, changeset} ->
        {:noreply,
         assign(socket, :creation, State.validate(socket.assigns.creation, to_form(changeset)))}
    end
  end

  @spec refresh_location(Phoenix.LiveView.Socket.t(), [TaskList.t()]) ::
          Phoenix.LiveView.Socket.t()
  def refresh_location(socket, task_lists) do
    assign(socket, :creation, State.refresh_location(socket.assigns.creation, task_lists))
  end

  @spec clear(Phoenix.LiveView.Socket.t()) :: Phoenix.LiveView.Socket.t()
  def clear(socket), do: assign(socket, :creation, State.clear(socket.assigns.creation))

  @spec location_copy(Project.t(), State.t()) :: String.t()
  def location_copy(%Project{name: project_name}, %State{location: nil}) do
    "Create this Task in Project #{project_name}."
  end

  def location_copy(%Project{}, %State{location: %TaskList{name: list_name}}) do
    "Create this Task in List #{list_name}."
  end

  defp task_create_state(project, selected_list, params) do
    case Map.fetch(params, "parent_task_id") do
      :error ->
        {selected_list, ParentPicker.open_create(ParentPicker.empty(), project, nil)}

      {:ok, parent_task_id} ->
        case task_create_parent(project, parent_task_id) do
          {:ok, parent, location} ->
            {location, ParentPicker.open_create(ParentPicker.empty(), project, parent)}

          :error ->
            {selected_list,
             ParentPicker.empty()
             |> ParentPicker.open_create(project, nil)
             |> ParentPicker.reject_draft("That parent Task is no longer available.")}
        end
    end
  end

  defp task_create_parent(project, parent_task_id) do
    with %Task{} = parent <- Tasks.get_task_for_project(project, parent_task_id),
         {:ok, location} <- task_location(project, parent) do
      {:ok, parent, location}
    else
      _not_found -> :error
    end
  end

  defp task_location(_project, %Task{list_id: nil}), do: {:ok, nil}

  defp task_location(project, %Task{list_id: list_id}) do
    case Lists.get_list_for_project(project, list_id) do
      %TaskList{} = task_list -> {:ok, task_list}
      nil -> :error
    end
  end
end
