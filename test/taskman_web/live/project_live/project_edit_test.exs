defmodule TaskmanWeb.ProjectLive.ProjectEditTest do
  use ExUnit.Case, async: true

  alias Taskman.Projects
  alias Taskman.Projects.Project
  alias TaskmanWeb.ProjectLive.ProjectEdit

  test "closed, create, and edit modes expose the right target and form" do
    assert :error = ProjectEdit.target(ProjectEdit.empty())
    assert ProjectEdit.title(ProjectEdit.empty()) == nil

    new = ProjectEdit.open_new()
    assert {:ok, :new, %Project{}} = ProjectEdit.target(new)
    assert ProjectEdit.title(new) == "New Project"
    assert ProjectEdit.submit_label(new) == "Create Project"
    assert new.form[:color].value == "6366F1"

    project = %Project{id: 4, name: "Alpha", color: "#12ABEF"}
    edit = ProjectEdit.open_edit(project)
    assert {:ok, :edit, ^project} = ProjectEdit.target(edit)
    assert ProjectEdit.title(edit) == "Edit Project"
    assert ProjectEdit.submit_label(edit) == "Save Changes"
    assert edit.form[:color].value == "12ABEF"
  end

  test "converts exactly six editable hex digits to canonical color" do
    assert {:ok, "#12ABEF"} = ProjectEdit.canonical_color("12abef")
    assert {:error, :invalid_color} = ProjectEdit.canonical_color("12abe")
    assert {:error, :invalid_color} = ProjectEdit.canonical_color("12abeg")
    assert {:error, :invalid_color} = ProjectEdit.canonical_color("#12abef")
  end

  test "valid custom color and preset values remain editable as six digits" do
    new = ProjectEdit.open_new()
    assert {:ok, custom} = ProjectEdit.validate(new, %{"name" => "Alpha", "color" => "12abef"})
    assert custom.form[:color].value == "12abef"
    assert Ecto.Changeset.get_field(custom.changeset, :color) == "#12ABEF"

    assert {:ok, preset} = ProjectEdit.validate(custom, %{"name" => "Alpha", "color" => "6366F1"})
    assert preset.form[:color].value == "6366F1"
  end

  test "invalid and incomplete colors retain every entered parameter and show a field error" do
    attrs = %{
      "name" => "  Mine  ",
      "description" => "  Draft  ",
      "icon" => "rocket-launch",
      "color" => "12zz"
    }

    assert {:ok, edit} = ProjectEdit.validate(ProjectEdit.open_new(), attrs)
    assert edit.form[:name].value == "  Mine  "
    assert edit.form[:description].value == "  Draft  "
    assert edit.form[:icon].value == "rocket-launch"
    assert edit.form[:color].value == "12zz"
    refute edit.form.source.valid?
    assert Keyword.has_key?(edit.form.source.errors, :color)

    assert {:ok, incomplete} = ProjectEdit.validate(edit, Map.put(attrs, "color", "12A"))
    assert incomplete.form[:color].value == "12A"
    assert Keyword.has_key?(incomplete.form.source.errors, :color)
  end

  test "put_error retains editable digits and server validation errors" do
    edit = ProjectEdit.open_new()
    assert {:ok, edit} = ProjectEdit.validate(edit, %{"name" => "Mine", "color" => "12ABEF"})
    error = Projects.change_project(%Project{}, %{"name" => "", "color" => "#12ABEF"})
    edit = ProjectEdit.put_error(edit, error)
    assert edit.form[:name].value == "Mine"
    assert edit.form[:color].value == "12ABEF"
    assert Keyword.has_key?(edit.form.source.errors, :name)
  end

  test "reconciliation refreshes pristine edit and preserves dirty edit parameters" do
    initial = %Project{id: 4, name: "Alpha", color: "#12ABEF"}
    latest = %Project{id: 4, name: "Latest", color: "#ABCDEF"}

    pristine = initial |> ProjectEdit.open_edit() |> ProjectEdit.reconcile([latest])
    assert {:ok, :edit, ^latest} = ProjectEdit.target(pristine)
    assert pristine.form[:name].value == "Latest"
    assert pristine.form[:color].value == "ABCDEF"

    {:ok, dirty} =
      ProjectEdit.validate(ProjectEdit.open_edit(initial), %{
        "name" => "Mine",
        "color" => "12abef"
      })

    dirty = ProjectEdit.reconcile(dirty, [latest])
    assert {:ok, :edit, ^latest} = ProjectEdit.target(dirty)
    assert dirty.form[:name].value == "Mine"
    assert dirty.form[:color].value == "12abef"
  end

  test "a vanished edit target cannot be submitted" do
    edit = ProjectEdit.open_edit(%Project{id: 4, name: "Alpha"})
    missing = ProjectEdit.reconcile(edit, [])
    assert :error = ProjectEdit.target(missing)
    assert {:error, :not_found} = ProjectEdit.validate(missing, %{"name" => "Mine"})
    assert ProjectEdit.title(missing) == "Project unavailable"
  end
end
