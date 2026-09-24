defmodule Taskman.Repo.Migrations.DropPrimaryDirectoryFromProjects do
  use Ecto.Migration

  def up do
    alter table(:projects) do
      remove :primary_directory
    end
  end

  def down do
    raise Ecto.MigrationError,
          "Cannot roll back Project directory removal; restore the matching pre-migration database backup with the prior application release."
  end
end
