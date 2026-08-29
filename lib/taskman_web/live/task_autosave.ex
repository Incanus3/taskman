defmodule TaskmanWeb.TaskAutosave do
  alias Taskman.Projects.Project
  alias Taskman.Tasks
  alias Taskman.Tasks.Task

  @editable_fields ~w(title description status priority due_at)
  @debounced_fields ~w(title description)

  defstruct form: nil,
            draft: %{},
            dirty_fields: MapSet.new(),
            revisions: %{},
            sequence: 0,
            save_failed?: false,
            saved?: false,
            save_state: :idle

  @type save_state :: :idle | :saving | :saved | :not_saved | :failed

  @type t :: %__MODULE__{
          form: Phoenix.HTML.Form.t() | nil,
          draft: map(),
          dirty_fields: MapSet.t(String.t()),
          revisions: %{optional(String.t()) => non_neg_integer()},
          sequence: non_neg_integer(),
          save_failed?: boolean(),
          saved?: boolean(),
          save_state: save_state()
        }

  def empty, do: %__MODULE__{}

  def load(%__MODULE__{sequence: sequence}, %Task{} = task, opts) do
    saved? = Keyword.fetch!(opts, :saved?)

    %__MODULE__{
      sequence: sequence,
      form: task |> Tasks.change_task() |> Phoenix.Component.to_form(),
      saved?: saved?,
      save_state: if(saved?, do: :saved, else: :idle)
    }
  end

  def clear(%__MODULE__{sequence: sequence}), do: %__MODULE__{sequence: sequence}

  def editable_fields, do: @editable_fields

  def change(
        %__MODULE__{} = autosave,
        %Project{} = project,
        %Task{} = task,
        task_params,
        field
      )
      when field in @editable_fields do
    autosave =
      autosave
      |> Map.put(:draft, task_params)
      |> Map.put(:save_failed?, false)
      |> Map.update!(:dirty_fields, &MapSet.put(&1, field))
      |> put_form(task)

    if field in @debounced_fields do
      schedule_field(autosave, task, field)
    else
      persist_field(autosave, project, task, field)
    end
  end

  def change(%__MODULE__{} = autosave, _project, %Task{} = task, _task_params, _field) do
    {:ignored, autosave, task}
  end

  def handle_scheduled_save(
        %__MODULE__{} = autosave,
        %Project{} = project,
        %Task{} = task,
        task_id,
        field,
        revision
      ) do
    if task.id == task_id and not is_nil(revision) and
         Map.get(autosave.revisions, field) == revision do
      persist_field(autosave, project, task, field)
    else
      {:ignored, autosave, task}
    end
  end

  def flush(%__MODULE__{} = autosave, %Project{} = project, %Task{} = task) do
    {result, autosave, task, save_failed?} =
      Enum.reduce_while(autosave.dirty_fields, {:ok, autosave, task, false}, fn field,
                                                                                {:ok, autosave,
                                                                                 task,
                                                                                 save_failed?} ->
        autosave = %{autosave | save_failed?: false}

        case persist_field(autosave, project, task, field) do
          {:ok, autosave, task} ->
            {:cont, {:ok, autosave, task, save_failed? || autosave.save_failed?}}

          {:not_found, autosave} ->
            {:halt, {:not_found, autosave, task, save_failed?}}
        end
      end)

    case result do
      :not_found ->
        {:not_found, autosave}

      :ok ->
        autosave =
          autosave
          |> Map.put(:save_failed?, save_failed?)
          |> refresh_save_state()

        if save_failed? do
          {:error, autosave, task}
        else
          {:ok, autosave, task}
        end
    end
  end

  def message(%__MODULE__{save_state: :idle}), do: "Autosaves changes"
  def message(%__MODULE__{save_state: :saving}), do: "Saving…"
  def message(%__MODULE__{save_state: :saved}), do: "Saved"
  def message(%__MODULE__{save_state: :not_saved}), do: "Not saved"
  def message(%__MODULE__{save_state: :failed}), do: "Couldn’t save changes"

  defp put_form(%__MODULE__{} = autosave, %Task{} = task) do
    form =
      task
      |> Tasks.change_task(autosave.draft)
      |> Map.put(:action, :validate)
      |> Phoenix.Component.to_form()

    %{autosave | form: form}
    |> refresh_save_state()
  end

  defp schedule_field(%__MODULE__{} = autosave, %Task{} = task, field) do
    value = Map.get(autosave.draft, field)
    field_changeset = Tasks.change_task(task, %{field => value})

    if field_changeset.valid? do
      revision = autosave.sequence + 1

      autosave =
        autosave
        |> Map.put(:sequence, revision)
        |> Map.update!(:revisions, &Map.put(&1, field, revision))
        |> refresh_save_state()

      {:schedule, autosave, task, autosave_delay_ms(),
       {:autosave_task_field, task.id, field, revision}}
    else
      {:ok, refresh_save_state(autosave), task}
    end
  end

  defp autosave_delay_ms do
    Application.get_env(:taskman, :task_autosave_delay_ms, 300)
  end

  defp persist_field(%__MODULE__{} = autosave, %Project{} = project, %Task{} = task, field) do
    value = Map.get(autosave.draft, field)
    field_changeset = Tasks.change_task(task, %{field => value})

    if field_changeset.valid? do
      case Tasks.update_task(project, task, %{field => value}) do
        {:ok, updated_task} ->
          autosave =
            autosave
            |> Map.update!(:dirty_fields, &MapSet.delete(&1, field))
            |> Map.put(:save_failed?, false)
            |> Map.put(:saved?, true)
            |> put_form(updated_task)

          {:ok, autosave, updated_task}

        {:error, %Ecto.Changeset{}} ->
          autosave =
            autosave
            |> Map.put(:save_failed?, true)
            |> refresh_save_state()

          {:ok, autosave, task}

        {:error, :not_found} ->
          {:not_found, clear(autosave)}
      end
    else
      {:ok, refresh_save_state(autosave), task}
    end
  end

  defp refresh_save_state(%__MODULE__{} = autosave) do
    save_state =
      cond do
        autosave.save_failed? -> :failed
        autosave.form && !autosave.form.source.valid? -> :not_saved
        MapSet.size(autosave.dirty_fields) > 0 -> :saving
        autosave.saved? -> :saved
        true -> :idle
      end

    %{autosave | save_state: save_state}
  end
end
