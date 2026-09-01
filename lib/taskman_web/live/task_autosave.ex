defmodule TaskmanWeb.TaskAutosave do
  alias Taskman.Projects.Project
  alias Taskman.Tasks
  alias Taskman.Tasks.Conflict
  alias Taskman.Tasks.Task

  @editable_fields ~w(title description status priority due_at)
  @debounced_fields ~w(title description)

  defstruct form: nil,
            baseline: nil,
            draft: %{},
            dirty_fields: MapSet.new(),
            revisions: %{},
            conflicts: %{},
            sequence: 0,
            save_failed?: false,
            saved?: false,
            save_state: :idle

  @type save_state :: :idle | :saving | :saved | :not_saved | :failed | :conflicted

  @type t :: %__MODULE__{
          form: Phoenix.HTML.Form.t() | nil,
          baseline: Task.t() | nil,
          draft: map(),
          dirty_fields: MapSet.t(String.t()),
          revisions: %{optional(String.t()) => non_neg_integer()},
          conflicts: %{optional(String.t()) => term()},
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
      baseline: task,
      saved?: saved?,
      save_state: if(saved?, do: :saved, else: :idle)
    }
  end

  def clear(%__MODULE__{sequence: sequence}), do: %__MODULE__{sequence: sequence}

  def editable_fields, do: @editable_fields

  @spec reconcile(t(), Task.t()) :: t()
  def reconcile(%__MODULE__{} = autosave, %Task{} = persisted_task) do
    baseline = autosave.baseline || persisted_task
    draft = Map.take(autosave.draft, MapSet.to_list(autosave.dirty_fields))

    {conflicts, revisions} =
      Enum.reduce(autosave.dirty_fields, {autosave.conflicts, autosave.revisions}, fn field,
                                                                                      {conflicts,
                                                                                       revisions} ->
        field_atom = editable_field_atom(field)

        if Map.get(baseline, field_atom) != Map.get(persisted_task, field_atom) do
          {Map.put(conflicts, field, Map.get(persisted_task, field_atom)),
           Map.delete(revisions, field)}
        else
          {conflicts, revisions}
        end
      end)

    autosave
    |> Map.put(:baseline, persisted_task)
    |> Map.put(:draft, draft)
    |> Map.put(:conflicts, conflicts)
    |> Map.put(:revisions, revisions)
    |> put_form(persisted_task)
  end

  @spec resolve_conflict(t(), Project.t(), Task.t(), String.t(), :use_latest | :keep_mine) ::
          {:ok, t(), Task.t()}
          | {:conflict, t(), Task.t()}
          | {:not_found, t()}
          | {:error, t(), Task.t()}
          | {:ignored, t(), Task.t()}
  def resolve_conflict(
        %__MODULE__{} = autosave,
        %Project{} = project,
        %Task{} = persisted_task,
        field,
        resolution
      )
      when field in @editable_fields and resolution in [:use_latest, :keep_mine] do
    if Map.has_key?(autosave.conflicts, field) do
      case resolution do
        :use_latest ->
          canonical_task = autosave.baseline || persisted_task

          {:ok, clear_field(autosave, field, canonical_task), canonical_task}

        :keep_mine ->
          retry_conflicted_field(autosave, project, persisted_task, field)
      end
    else
      {:ignored, autosave, persisted_task}
    end
  end

  def resolve_conflict(%__MODULE__{} = autosave, _project, %Task{} = task, _field, _resolution),
    do: {:ignored, autosave, task}

  @spec conflict_value(t(), String.t()) :: term() | nil
  def conflict_value(%__MODULE__{} = autosave, field) when field in @editable_fields,
    do: Map.get(autosave.conflicts, field)

  def conflict_value(%__MODULE__{}, _field), do: nil

  def change(
        %__MODULE__{} = autosave,
        %Project{} = project,
        %Task{} = task,
        task_params,
        field
      )
      when field in @editable_fields do
    draft =
      case Map.fetch(task_params, field) do
        {:ok, value} -> Map.put(autosave.draft, field, value)
        :error -> autosave.draft
      end

    autosave =
      autosave
      |> Map.put(:draft, draft)
      |> Map.put(:save_failed?, false)
      |> Map.update!(:dirty_fields, &MapSet.put(&1, field))
      |> put_form(autosave.baseline || task)

    if Map.has_key?(autosave.conflicts, field) do
      autosave = Map.update!(autosave, :revisions, &Map.delete(&1, field))
      {:ok, refresh_save_state(autosave), task}
    else
      if field in @debounced_fields do
        schedule_field(autosave, task, field)
      else
        persist_field(autosave, project, task, field)
      end
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
    if autosave.conflicts != %{} do
      {:error, refresh_save_state(autosave), task}
    else
      {result, autosave, task, save_failed?} =
        Enum.reduce_while(autosave.dirty_fields, {:ok, autosave, task, false}, fn field,
                                                                                  {:ok, autosave,
                                                                                   task,
                                                                                   save_failed?} ->
          autosave = %{autosave | save_failed?: false}

          case persist_field(autosave, project, task, field) do
            {:ok, autosave, task} ->
              {:cont, {:ok, autosave, task, save_failed? || autosave.save_failed?}}

            {:conflict, autosave, task} ->
              {:halt, {:conflict, autosave, task, save_failed?}}

            {:not_found, autosave} ->
              {:halt, {:not_found, autosave, task, save_failed?}}
          end
        end)

      case result do
        :not_found ->
          {:not_found, autosave}

        :conflict ->
          {:error, refresh_save_state(autosave), task}

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
  end

  def message(%__MODULE__{save_state: :idle}), do: "Autosaves changes"
  def message(%__MODULE__{save_state: :saving}), do: "Saving…"
  def message(%__MODULE__{save_state: :saved}), do: "Saved"
  def message(%__MODULE__{save_state: :not_saved}), do: "Not saved"
  def message(%__MODULE__{save_state: :failed}), do: "Couldn’t save changes"
  def message(%__MODULE__{save_state: :conflicted}), do: "Resolve conflicting changes"

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
      case Tasks.update_task(project, task, %{editable_field_atom(field) => value}) do
        {:ok, updated_task} ->
          autosave =
            autosave
            |> clear_field(field, updated_task)

          {:ok, autosave, updated_task}

        {:error, %Conflict{task: current_task}} ->
          {:conflict, reconcile(autosave, current_task), current_task}

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
        autosave.conflicts != %{} -> :conflicted
        autosave.save_failed? -> :failed
        autosave.form && !autosave.form.source.valid? -> :not_saved
        MapSet.size(autosave.dirty_fields) > 0 -> :saving
        autosave.saved? -> :saved
        true -> :idle
      end

    %{autosave | save_state: save_state}
  end

  defp retry_conflicted_field(%__MODULE__{} = autosave, %Project{} = project, task, field) do
    canonical_task = autosave.baseline || task
    value = Map.get(autosave.draft, field)

    case Tasks.update_task(project, canonical_task, %{editable_field_atom(field) => value}) do
      {:ok, updated_task} ->
        {:ok, clear_field(autosave, field, updated_task), updated_task}

      {:error, %Conflict{task: current_task}} ->
        {:conflict, reconcile(autosave, current_task), current_task}

      {:error, %Ecto.Changeset{}} ->
        autosave =
          autosave
          |> Map.put(:save_failed?, true)
          |> refresh_save_state()

        {:error, autosave, canonical_task}

      {:error, :not_found} ->
        {:not_found, clear(autosave)}
    end
  end

  defp clear_field(%__MODULE__{} = autosave, field, %Task{} = task) do
    autosave
    |> Map.update!(:draft, &Map.delete(&1, field))
    |> Map.update!(:dirty_fields, &MapSet.delete(&1, field))
    |> Map.update!(:revisions, &Map.delete(&1, field))
    |> Map.update!(:conflicts, &Map.delete(&1, field))
    |> Map.put(:save_failed?, false)
    |> Map.put(:saved?, true)
    |> reconcile(task)
  end

  defp editable_field_atom("title"), do: :title
  defp editable_field_atom("description"), do: :description
  defp editable_field_atom("status"), do: :status
  defp editable_field_atom("priority"), do: :priority
  defp editable_field_atom("due_at"), do: :due_at
end
