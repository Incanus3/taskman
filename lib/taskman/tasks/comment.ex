defmodule Taskman.Tasks.Comment do
  use Ecto.Schema

  import Ecto.Changeset

  @type t :: %__MODULE__{
          id: pos_integer() | nil,
          task_id: pos_integer() | nil,
          actor_user_id: Ecto.UUID.t() | nil,
          author_name: String.t() | nil,
          author_login: String.t() | nil,
          text: String.t() | nil,
          created_at: DateTime.t() | nil
        }

  schema "task_comments" do
    belongs_to :task, Taskman.Tasks.Task
    field :actor_user_id, Ecto.UUID
    field :author_name, :string
    field :author_login, :string, virtual: true
    field :text, :string
    field :created_at, :utc_datetime, read_after_writes: true
  end

  def changeset(comment, attrs) do
    comment
    |> cast(attrs, [:text, :author_name])
    |> update_change(:text, &String.trim/1)
    |> update_change(:author_name, &String.trim/1)
    |> validate_required([:text])
    |> validate_supplied_name(attrs)
    |> validate_graphemes(:text, 10_000)
    |> validate_graphemes(:author_name, 80)
    |> check_constraint(:text, name: :task_comments_text_nonempty_check)
    |> foreign_key_constraint(:task_id)
    |> foreign_key_constraint(:actor_user_id)
  end

  defp validate_supplied_name(changeset, attrs) do
    if Map.has_key?(attrs, :author_name) or Map.has_key?(attrs, "author_name") do
      validate_required(changeset, [:author_name])
    else
      changeset
    end
  end

  defp validate_graphemes(changeset, field, max) do
    validate_change(changeset, field, fn ^field, value ->
      if is_binary(value) and String.length(value) > max do
        [{field, {"should be at most %{count} character(s)", [count: max]}}]
      else
        []
      end
    end)
  end
end
