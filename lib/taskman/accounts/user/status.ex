defmodule Taskman.Accounts.User.Status do
  @moduledoc "Supported account lifecycle states."

  use Ash.Type.Enum, values: [:pending, :active, :disabled]
end
