defmodule Taskman.Accounts.RateLimitBackend do
  @moduledoc false

  use Hammer, backend: :ets
end
