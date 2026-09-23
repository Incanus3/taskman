defmodule TaskmanWeb.ProjectLive.Tasks.Creation do
  import Phoenix.Component, only: [assign: 3, to_form: 1]
  import Phoenix.LiveView, only: [push_patch: 2]

  alias Taskman.Lists
  alias Taskman.Lists.TaskList
  alias Taskman.Projects.Project
  alias Taskman.Tasks
  alias Taskman.Tasks.Task
  alias TaskmanWeb.ProjectLive.Paths
  alias TaskmanWeb.ProjectLive.Tasks.{Listing, LocationScope, Messages, ParentPicker}

  @events ["validate_task", "save_task"]

  defmodule State do
    defstruct form: nil,
              enabled?: false,
              location: nil,
              location_label: nil,
              location_options: [],
              location_error: nil

    @type t :: %__MODULE__{
            form: Phoenix.HTML.Form.t() | nil,
            enabled?: boolean(),
            location: String.t() | nil,
            location_label: String.t() | nil,
            location_options: [{String.t(), String.t()}],
            location_error: String.t() | nil
          }

    @spec empty() :: t()
    def empty, do: %__MODULE__{}

    @spec open(t(), Phoenix.HTML.Form.t(), String.t(), String.t(), [{String.t(), String.t()}]) ::
            t()
    def open(%__MODULE__{} = state, form, location, location_label, location_options) do
      state
      |> Map.put(:form, form)
      |> Map.put(:location, location)
      |> Map.put(:location_label, location_label)
      |> refresh_locations(location_options)
    end

    @spec validate(t(), Phoenix.HTML.Form.t()) :: t()
    def validate(%__MODULE__{} = state, form), do: state |> Map.put(:form, form) |> with_enabled()

    @spec choose_location(t(), String.t(), [{String.t(), String.t()}]) :: t()
    def choose_location(%__MODULE__{} = state, location, location_options)
        when is_binary(location) do
      case option_label(location_options, location) do
        nil ->
          state
          |> Map.put(:location, location)
          |> Map.put(:location_options, location_options)
          |> Map.put(:location_error, unavailable_location_error())
          |> with_enabled()

        label ->
          state
          |> Map.put(:location, location)
          |> Map.put(:location_label, label)
          |> Map.put(:location_options, location_options)
          |> Map.put(:location_error, nil)
          |> with_enabled()
      end
    end

    @spec refresh_locations(t(), [{String.t(), String.t()}]) :: t()
    def refresh_locations(%__MODULE__{} = state, location_options) do
      case option_label(location_options, state.location) do
        nil when is_nil(state.location) ->
          state
          |> Map.put(:location_options, location_options)
          |> Map.put(:location_label, nil)
          |> Map.put(:location_error, unavailable_location_error())
          |> with_enabled()

        nil ->
          state
          |> Map.put(:location_options, location_options)
          |> Map.put(:location_error, unavailable_location_error())
          |> with_enabled()

        label ->
          state
          |> Map.put(:location_options, location_options)
          |> Map.put(:location_label, label)
          |> Map.put(:location_error, nil)
          |> with_enabled()
      end
    end

    @spec clear(t()) :: t()
    def clear(%__MODULE__{}), do: empty()

    defp option_label(options, location) do
      case Enum.find(options, fn {_label, key} -> key == location end) do
        {label, _key} -> label
        nil -> nil
      end
    end

    defp with_enabled(%__MODULE__{} = state) do
      %{state | enabled?: state.form.source.valid? and is_nil(state.location_error)}
    end

    defp unavailable_location_error,
      do: "This List is no longer available. Choose another location."
  end

  @spec events() :: [String.t()]
  def events, do: @events

  @spec apply_route(Phoenix.LiveView.Socket.t(), map()) :: Phoenix.LiveView.Socket.t()
  def apply_route(socket, params) do
    project = socket.assigns.workspace.selected_project
    selected_list = socket.assigns.workspace.selected_list
    changeset = Tasks.change_task(project)

    {location, parent_picker} = task_create_state(project, selected_list, params)
    location_options = location_options(project)
    location_key = location_key(location)
    location_label = location_label(location_options, location_key)

    creation =
      State.open(
        socket.assigns.creation,
        to_form(changeset),
        location_key,
        location_label,
        location_options
      )

    socket
    |> assign(:creation, creation)
    |> assign(:task_parent_picker, parent_picker)
  end

  @spec handle_event(String.t(), map(), Phoenix.LiveView.Socket.t()) ::
          {:noreply, Phoenix.LiveView.Socket.t()}
  def handle_event("validate_task", %{"task" => task_params} = params, socket) do
    changeset =
      socket.assigns.workspace.selected_project
      |> Tasks.change_task(task_params)
      |> Map.put(:action, :validate)

    location = Map.get(params, "location") || socket.assigns.creation.location || "project"

    creation =
      socket.assigns.creation
      |> State.validate(to_form(changeset))
      |> State.choose_location(
        location,
        location_options(socket.assigns.workspace.selected_project)
      )

    {:noreply, assign(socket, :creation, creation)}
  end

  def handle_event("save_task", %{"task" => task_params} = params, socket) do
    project = socket.assigns.workspace.selected_project
    location = Map.get(params, "location") || socket.assigns.creation.location || "project"

    case resolve_location(project, location) do
      {:ok, destination} ->
        create_task(socket, project, destination, task_params, location)

      :error ->
        {:noreply, assign_invalid_location(socket, task_params, location)}
    end
  end

  @spec refresh_locations(Phoenix.LiveView.Socket.t(), [TaskList.t()]) ::
          Phoenix.LiveView.Socket.t()
  def refresh_locations(socket, task_lists) do
    project = socket.assigns.workspace.selected_project

    if socket.assigns.live_action == :new_task and socket.assigns.creation.form do
      assign(
        socket,
        :creation,
        State.refresh_locations(socket.assigns.creation, location_options(project, task_lists))
      )
    else
      socket
    end
  end

  @spec cancel_path(map()) :: String.t()
  def cancel_path(workspace) do
    destination = if workspace.location_not_found?, do: nil, else: workspace.selected_list
    Paths.browse_path(workspace.selected_project, destination, workspace.include_children?)
  end

  @spec post_create_path(Project.t(), map(), TaskList.t() | nil, [TaskList.t()]) :: String.t()
  def post_create_path(project, workspace, destination, task_lists) do
    backdrop = LocationScope.backdrop(workspace, destination, task_lists)
    Paths.browse_path(project, backdrop, workspace.include_children?)
  end

  @spec clear(Phoenix.LiveView.Socket.t()) :: Phoenix.LiveView.Socket.t()
  def clear(socket), do: assign(socket, :creation, State.clear(socket.assigns.creation))

  defp create_task(socket, project, destination, task_params, location) do
    case Tasks.create_task(project, destination, task_params,
           parent: ParentPicker.selected_parent(socket.assigns.task_parent_picker)
         ) do
      {:ok, _task} ->
        socket = Listing.refresh(socket)

        {:noreply,
         push_patch(socket,
           to:
             post_create_path(
               project,
               socket.assigns.workspace,
               destination,
               Lists.list_lists_for_project(project)
             )
         )}

      {:error, :not_found} ->
        {:noreply,
         assign(
           socket,
           :task_parent_picker,
           ParentPicker.reject_draft(
             socket.assigns.task_parent_picker,
             Messages.parent_unavailable()
           )
         )}

      {:error, changeset} ->
        {:noreply,
         assign(
           socket,
           :creation,
           socket.assigns.creation
           |> State.validate(to_form(changeset))
           |> State.choose_location(location, location_options(project))
         )}
    end
  end

  defp assign_invalid_location(socket, task_params, location) do
    changeset =
      socket.assigns.workspace.selected_project
      |> Tasks.change_task(task_params)
      |> Map.put(:action, :validate)

    assign(
      socket,
      :creation,
      socket.assigns.creation
      |> State.validate(to_form(changeset))
      |> State.choose_location(
        location,
        location_options(socket.assigns.workspace.selected_project)
      )
    )
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
             |> ParentPicker.reject_draft(Messages.parent_unavailable())}
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

  defp location_options(project, task_lists \\ nil)

  defp location_options(%Project{} = project, nil),
    do: location_options(project, Lists.list_lists_for_project(project))

  defp location_options(%Project{name: name}, task_lists) do
    [{"Project #{name}", "project"}] ++
      Enum.map(Lists.tree_order(task_lists), fn task_list ->
        {"List #{task_list.name}", location_key(task_list)}
      end)
  end

  defp location_key(nil), do: "project"
  defp location_key(%TaskList{id: id}), do: "list:#{id}"

  defp location_label(options, key) do
    case Enum.find(options, fn {_label, option_key} -> option_key == key end) do
      {label, _key} -> label
      nil -> nil
    end
  end

  defp resolve_location(_project, "project"), do: {:ok, nil}

  defp resolve_location(project, "list:" <> id) do
    case Integer.parse(id) do
      {value, ""} when value > 0 ->
        if Integer.to_string(value) == id do
          case Lists.get_list_for_project(project, value) do
            %TaskList{} = task_list -> {:ok, task_list}
            nil -> :error
          end
        else
          :error
        end

      _ ->
        :error
    end
  end

  defp resolve_location(_project, _location), do: :error
end
