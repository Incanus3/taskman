defmodule Taskman.Repo do
  use Ecto.Repo,
    otp_app: :taskman,
    adapter: Ecto.Adapters.Postgres
end
