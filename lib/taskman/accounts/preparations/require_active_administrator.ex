defmodule Taskman.Accounts.Preparations.RequireActiveAdministrator do
  @moduledoc false

  use Ash.Resource.Preparation

  alias Taskman.Accounts.Administration

  @impl true
  def prepare(query, _opts, %{actor: actor}) do
    Ash.Query.before_action(query, fn query ->
      if Administration.persisted_active_administrator?(actor) do
        query
      else
        Ash.Query.add_error(query, field: :id, message: "is not permitted")
      end
    end)
  end
end
