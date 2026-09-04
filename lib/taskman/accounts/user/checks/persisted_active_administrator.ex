defmodule Taskman.Accounts.User.Checks.PersistedActiveAdministrator do
  @moduledoc false

  use Ash.Policy.SimpleCheck

  alias Taskman.Accounts.Administration

  @impl true
  def match?(actor, _context, _opts), do: Administration.persisted_active_administrator?(actor)

  @impl true
  def describe(_opts), do: "actor is a persisted active administrator"
end
