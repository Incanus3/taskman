defmodule TaskmanWeb.ProjectLive.ProjectEdit do
  @moduledoc """
  Form state for creating and editing a Project. Persistence remains with the caller.
  """

  alias Taskman.Projects
  alias Taskman.Projects.Project

  defstruct mode: nil, project: nil, form: nil, changeset: nil, dirty?: false, available?: true

  @type t :: %__MODULE__{}

  @spec empty() :: t()
  def empty, do: %__MODULE__{}

  @spec open_new() :: t()
  def open_new do
    project = %Project{}
    changeset = Projects.change_project(project)

    %__MODULE__{
      mode: :new,
      project: project,
      changeset: changeset,
      form: project_form(changeset, %{"color" => editable_color(project.color)})
    }
  end

  @spec open_edit(Project.t()) :: t()
  def open_edit(%Project{} = project) do
    changeset = Projects.change_project(project)

    %__MODULE__{
      mode: :edit,
      project: project,
      changeset: changeset,
      form: project_form(changeset, %{"color" => editable_color(project.color)})
    }
  end

  @spec title(t()) :: String.t() | nil
  def title(%__MODULE__{mode: :new}), do: "New Project"
  def title(%__MODULE__{mode: :edit, available?: false}), do: "Project unavailable"
  def title(%__MODULE__{mode: :edit}), do: "Edit Project"
  def title(%__MODULE__{}), do: nil

  @spec submit_label(t()) :: String.t() | nil
  def submit_label(%__MODULE__{mode: :new}), do: "Create Project"
  def submit_label(%__MODULE__{mode: :edit, available?: true}), do: "Save Changes"
  def submit_label(%__MODULE__{}), do: nil

  @spec canonical_color(term()) :: {:ok, String.t()} | {:error, :invalid_color}
  def canonical_color(<<digits::binary-size(6)>>) do
    if String.match?(digits, ~r/\A[0-9A-Fa-f]{6}\z/),
      do: {:ok, "#" <> String.upcase(digits)},
      else: {:error, :invalid_color}
  end

  def canonical_color(_digits), do: {:error, :invalid_color}

  @spec target(t()) :: {:ok, :new | :edit, Project.t()} | :error
  def target(%__MODULE__{mode: :new, project: %Project{} = project}),
    do: {:ok, :new, project}

  def target(%__MODULE__{mode: :edit, available?: true, project: %Project{} = project}),
    do: {:ok, :edit, project}

  def target(%__MODULE__{}), do: :error

  @spec validate(t(), map()) :: {:ok, t()} | {:error, :not_found}
  def validate(%__MODULE__{} = edit, attrs) when is_map(attrs) do
    case target(edit) do
      {:ok, _mode, project} ->
        attrs = stringify_keys(attrs)
        changeset = Projects.change_project(project, canonical_attrs(attrs))
        form = project_form(changeset, attrs)
        {:ok, %{edit | changeset: changeset, form: form, dirty?: true}}

      :error ->
        {:error, :not_found}
    end
  end

  @spec put_error(t(), Ecto.Changeset.t()) :: t()
  def put_error(%__MODULE__{} = edit, %Ecto.Changeset{} = changeset) do
    params = if edit.form, do: edit.form.params || %{}, else: %{}
    %{edit | changeset: changeset, form: project_form(changeset, params), dirty?: true}
  end

  @spec reconcile(t(), [Project.t()]) :: t()
  def reconcile(%__MODULE__{mode: :edit, project: %Project{id: id}} = edit, projects)
      when is_list(projects) do
    case Enum.find(projects, &(&1.id == id)) do
      %Project{} = project when edit.dirty? ->
        %{edit | project: project, available?: true}

      %Project{} = project ->
        changeset = Projects.change_project(project)

        %{
          edit
          | project: project,
            changeset: changeset,
            form: project_form(changeset, %{"color" => editable_color(project.color)}),
            available?: true
        }

      nil ->
        %{edit | available?: false}
    end
  end

  def reconcile(%__MODULE__{} = edit, _projects), do: edit

  defp project_form(%Ecto.Changeset{} = changeset, params) do
    display_changeset = %{changeset | changes: %{}}

    display_changeset
    |> Map.put(:action, :validate)
    |> Phoenix.Component.to_form(as: :project)
    |> Map.update!(:params, &Map.merge(&1 || %{}, params))
  end

  defp canonical_attrs(attrs) do
    case Map.fetch(attrs, "color") do
      {:ok, digits} ->
        color =
          case canonical_color(digits) do
            {:ok, canonical} -> canonical
            {:error, :invalid_color} when is_binary(digits) -> "#" <> digits
            {:error, :invalid_color} -> digits
          end

        Map.put(attrs, "color", color)

      :error ->
        attrs
    end
  end

  defp editable_color("#" <> digits), do: digits
  defp editable_color(color), do: color

  defp stringify_keys(attrs) do
    Map.new(attrs, fn
      {key, value} when is_atom(key) -> {Atom.to_string(key), value}
      pair -> pair
    end)
  end
end
