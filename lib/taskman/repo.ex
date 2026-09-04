defmodule Taskman.Repo do
  use AshPostgres.Repo, otp_app: :taskman

  def installed_extensions, do: ["ash-functions", "uuid-ossp", "citext"]

  def min_pg_version, do: %Version{major: 15, minor: 0, patch: 0}
end
