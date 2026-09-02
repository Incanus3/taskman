defmodule Taskman.Repo do
  use AshPostgres.Repo,
    otp_app: :taskman,
    warn_on_missing_ash_functions?: false

  def installed_extensions, do: ["uuid-ossp", "citext"]

  def min_pg_version, do: %Version{major: 15, minor: 0, patch: 0}
end
