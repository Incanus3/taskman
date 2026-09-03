defmodule Taskman.CLI.Skill.InstallerTest do
  use ExUnit.Case, async: true

  alias Taskman.CLI.Skill.{Bundle, Installer}
  alias Taskman.CLI.Skill.FakeSkillFileSystem

  @tag :tmp_dir
  test "installs the bundled skill and leaves no temporary siblings", %{tmp_dir: root} do
    assert {:ok, result} = Installer.install(skills_root: root)
    target = Path.join(root, "taskman-cli")

    assert result.action == :installed
    assert result.path == target
    assert result.skill == "taskman-cli"
    assert result.cli_version == Bundle.cli_version()
    assert File.regular?(Path.join(target, "SKILL.md"))
    assert File.regular?(Path.join(target, ".taskman-managed.json"))
    assert File.read!(Path.join(target, "SKILL.md")) == Bundle.files()["SKILL.md"]

    assert Jason.decode!(File.read!(Path.join(target, ".taskman-managed.json"))) == %{
             "installer" => "taskman",
             "skill" => "taskman-cli",
             "cli_version" => Bundle.cli_version()
           }

    assert temporary_siblings(root) == []
  end

  @tag :tmp_dir
  test "identical installation is current", %{tmp_dir: root} do
    assert {:ok, %{action: :installed}} = Installer.install(skills_root: root)
    assert {:ok, %{action: :current, path: target}} = Installer.install(skills_root: root)
    assert target == Path.join(root, "taskman-cli")
    assert temporary_siblings(root) == []
  end

  @tag :tmp_dir
  test "current cleanup preserves a near-prefix sibling", %{tmp_dir: root} do
    assert {:ok, %{action: :installed}} = Installer.install(skills_root: root)
    near_prefix = Path.join(root, ".taskman-cli.backup-user-notes")
    File.write!(near_prefix, "keep me")

    assert {:ok, %{action: :current}} = Installer.install(skills_root: root)
    assert File.read!(near_prefix) == "keep me"
  end

  @tag :tmp_dir
  test "current cleanup preserves exact-name siblings without ownership markers", %{tmp_dir: root} do
    assert {:ok, %{action: :installed}} = Installer.install(skills_root: root)
    stage = Path.join(root, ".taskman-cli.stage-123")
    backup = Path.join(root, ".taskman-cli.backup-456")
    File.mkdir_p!(stage)
    File.mkdir_p!(backup)
    File.write!(Path.join(stage, "personal.txt"), "keep stage")
    File.write!(Path.join(backup, "personal.txt"), "keep backup")

    assert {:ok, %{action: :current}} = Installer.install(skills_root: root)
    assert File.read!(Path.join(stage, "personal.txt")) == "keep stage"
    assert File.read!(Path.join(backup, "personal.txt")) == "keep backup"
  end

  @tag :tmp_dir
  test "current cleanup preserves a recognized stage from another invocation", %{tmp_dir: root} do
    assert {:ok, %{action: :installed}} = Installer.install(skills_root: root)
    target = Path.join(root, "taskman-cli")
    concurrent_stage = Path.join(root, ".taskman-cli.stage-789")
    File.cp_r!(target, concurrent_stage)

    assert {:ok, %{action: :current}} = Installer.install(skills_root: root)
    assert File.regular?(Path.join(concurrent_stage, ".taskman-managed.json"))
    assert File.regular?(Path.join(concurrent_stage, "SKILL.md"))
  end

  @tag :tmp_dir
  test "recognized older marker is updated", %{tmp_dir: root} do
    assert {:ok, %{action: :installed}} = Installer.install(skills_root: root)
    target = Path.join(root, "taskman-cli")

    File.write!(
      Path.join(target, ".taskman-managed.json"),
      Jason.encode!(%{
        "installer" => "taskman",
        "skill" => "taskman-cli",
        "cli_version" => "0.0.1"
      })
    )

    assert {:ok, %{action: :updated, path: ^target}} = Installer.install(skills_root: root)
    assert File.read!(Path.join(target, ".taskman-managed.json")) =~ Bundle.cli_version()
    assert temporary_siblings(root) == []
  end

  @tag :tmp_dir
  test "installer-owned stale content is atomically replaced with the exact current bundle", %{
    tmp_dir: root
  } do
    assert {:ok, %{action: :installed}} = Installer.install(skills_root: root)
    target = Path.join(root, "taskman-cli")
    skill_path = Path.join(target, "SKILL.md")
    File.write!(skill_path, "stale credential guidance")

    assert {:ok, %{action: :updated, path: ^target, cli_version: version}} =
             Installer.install(skills_root: root)

    assert version == Bundle.cli_version()
    assert File.read!(skill_path) == Bundle.files()["SKILL.md"]

    assert Jason.decode!(File.read!(Path.join(target, ".taskman-managed.json")))["cli_version"] ==
             Bundle.cli_version()

    assert temporary_siblings(root) == []
  end

  @tag :tmp_dir
  test "owned updates leave a complete old or new SKILL.md available to concurrent readers", %{
    tmp_dir: root
  } do
    assert {:ok, %{action: :installed}} = Installer.install(skills_root: root)
    target = Path.join(root, "taskman-cli")
    skill_path = Path.join(target, "SKILL.md")
    stale_contents = "stale credential guidance"
    File.write!(skill_path, stale_contents)

    FakeSkillFileSystem.observe_before_rename!(fn from, to ->
      if Path.basename(to) == "SKILL.md" and String.contains?(Path.basename(from), ".stage-") do
        assert File.read!(skill_path) == stale_contents
      end
    end)

    assert {:ok, %{action: :updated}} =
             Installer.install(skills_root: root, file_system: FakeSkillFileSystem)

    assert File.read!(skill_path) == Bundle.files()["SKILL.md"]
  end

  @tag :tmp_dir
  test "a marker-rename failure leaves complete content and a retry repairs exact ownership", %{
    tmp_dir: root
  } do
    assert {:ok, %{action: :installed}} = Installer.install(skills_root: root)
    target = Path.join(root, "taskman-cli")
    skill_path = Path.join(target, "SKILL.md")
    marker_path = Path.join(target, ".taskman-managed.json")
    stale_marker = File.read!(marker_path) |> String.replace(Bundle.cli_version(), "0.0.1")
    File.write!(marker_path, stale_marker)
    FakeSkillFileSystem.fail_next_rename!(:marker)

    assert {:error, :skill_install_failed, _message} =
             Installer.install(skills_root: root, file_system: FakeSkillFileSystem)

    assert File.read!(skill_path) == Bundle.files()["SKILL.md"]
    assert File.read!(marker_path) == stale_marker

    assert {:ok, %{action: :updated}} =
             Installer.install(skills_root: root, file_system: FakeSkillFileSystem)

    assert File.read!(skill_path) == Bundle.files()["SKILL.md"]

    assert Jason.decode!(File.read!(marker_path)) == %{
             "installer" => "taskman",
             "skill" => "taskman-cli",
             "cli_version" => Bundle.cli_version()
           }
  end

  @tag :tmp_dir
  test "refuses symlink target and installer-looking stage or backup siblings", %{tmp_dir: root} do
    target = Path.join(root, "taskman-cli")
    linked_directory = Path.join(root, "linked-directory")
    File.mkdir_p!(linked_directory)
    File.ln_s!(linked_directory, target)

    assert {:error, :skill_install_failed, target_message} = Installer.install(skills_root: root)
    assert target_message =~ "symlink"

    File.rm!(target)
    assert {:ok, %{action: :installed}} = Installer.install(skills_root: root)

    for {name, label} <- [
          {".taskman-cli.stage-101", "staging"},
          {".taskman-cli.backup-202", "backup"}
        ] do
      link = Path.join(root, name)
      File.ln_s!(linked_directory, link)

      assert {:error, :skill_install_failed, message} = Installer.install(skills_root: root)
      assert message =~ label
      assert message =~ "symlink"
      assert File.lstat!(link).type == :symlink
      File.rm!(link)
    end
  end

  @tag :tmp_dir
  test "unrecognized target is refused without changing its contents", %{tmp_dir: root} do
    target = Path.join(root, "taskman-cli")
    File.mkdir_p!(target)
    custom_path = Path.join(target, "custom.md")
    File.write!(custom_path, "keep me")

    assert {:error, :skill_install_failed, message} = Installer.install(skills_root: root)
    assert message =~ "unrecognized"
    assert File.read!(custom_path) == "keep me"
    assert temporary_siblings(root) == []
  end

  @tag :tmp_dir
  test "force replaces an unrecognized target", %{tmp_dir: root} do
    target = Path.join(root, "taskman-cli")
    File.mkdir_p!(target)
    File.write!(Path.join(target, "custom.md"), "replace me")

    assert {:ok, %{action: :updated, path: ^target}} =
             Installer.install(skills_root: root, force: true)

    refute File.exists?(Path.join(target, "custom.md"))
    assert File.regular?(Path.join(target, "SKILL.md"))
    assert temporary_siblings(root) == []
  end

  @tag :tmp_dir
  test "failed stage-to-target swap restores the previous complete target", %{tmp_dir: root} do
    assert {:ok, %{action: :installed}} = Installer.install(skills_root: root)
    target = Path.join(root, "taskman-cli")
    marker_path = Path.join(target, ".taskman-managed.json")
    old_skill = File.read!(Path.join(target, "SKILL.md"))
    old_marker = File.read!(marker_path)
    File.write!(marker_path, String.replace(old_marker, Bundle.cli_version(), "0.0.1"))
    FakeSkillFileSystem.fail_next_rename!()

    assert {:error, :skill_install_failed, _message} =
             Installer.install(skills_root: root, file_system: FakeSkillFileSystem)

    assert File.read!(Path.join(target, "SKILL.md")) == old_skill
    assert File.read!(marker_path) == String.replace(old_marker, Bundle.cli_version(), "0.0.1")
    assert temporary_siblings(root) == []
  end

  @tag :tmp_dir
  test "a transient restore failure still restores the previous target", %{tmp_dir: root} do
    assert {:ok, %{action: :installed}} = Installer.install(skills_root: root)
    target = Path.join(root, "taskman-cli")
    marker_path = Path.join(target, ".taskman-managed.json")
    old_skill = File.read!(Path.join(target, "SKILL.md"))
    old_marker = File.read!(marker_path)
    File.write!(marker_path, String.replace(old_marker, Bundle.cli_version(), "0.0.1"))
    FakeSkillFileSystem.fail_rename_sequence!([:stage_to_target, :restore])

    assert {:error, :skill_install_failed, _message} =
             Installer.install(skills_root: root, file_system: FakeSkillFileSystem)

    assert File.read!(Path.join(target, "SKILL.md")) == old_skill
    assert File.read!(marker_path) == String.replace(old_marker, Bundle.cli_version(), "0.0.1")
    assert temporary_siblings(root) == []
  end

  @tag :tmp_dir
  test "a failed owned update preserves its complete target for the next invocation", %{
    tmp_dir: root
  } do
    assert {:ok, %{action: :installed}} = Installer.install(skills_root: root)
    target = Path.join(root, "taskman-cli")
    marker_path = Path.join(target, ".taskman-managed.json")
    old_skill = File.read!(Path.join(target, "SKILL.md"))
    old_marker = File.read!(marker_path)
    File.write!(marker_path, String.replace(old_marker, Bundle.cli_version(), "0.0.1"))
    FakeSkillFileSystem.fail_next_rename!(:stage_to_target)

    assert {:error, :skill_install_failed, _message} =
             Installer.install(skills_root: root, file_system: FakeSkillFileSystem)

    assert File.read!(Path.join(target, "SKILL.md")) == old_skill
    assert File.read!(marker_path) == String.replace(old_marker, Bundle.cli_version(), "0.0.1")
    assert exact_backup_siblings(root) == []

    assert {:ok, %{action: :updated, path: ^target}} =
             Installer.install(skills_root: root, file_system: FakeSkillFileSystem)

    assert File.read!(Path.join(target, "SKILL.md")) == Bundle.files()["SKILL.md"]
    assert File.read!(Path.join(target, ".taskman-managed.json")) =~ Bundle.cli_version()
    assert exact_backup_siblings(root) == []
  end

  @tag :tmp_dir
  test "a missing target refuses ambiguous installer backups", %{tmp_dir: root} do
    assert {:ok, %{action: :installed}} = Installer.install(skills_root: root)
    target = Path.join(root, "taskman-cli")
    backup_one = Path.join(root, ".taskman-cli.backup-101")
    backup_two = Path.join(root, ".taskman-cli.backup-202")
    File.cp_r!(target, backup_one)
    File.cp_r!(target, backup_two)
    File.rm_rf!(target)

    assert {:error, :skill_install_failed, message} = Installer.install(skills_root: root)
    assert message =~ "multiple installer backups"
    refute File.exists?(target)
    assert File.exists?(backup_one)
    assert File.exists?(backup_two)
  end

  @tag :tmp_dir
  test "owned updates do not use the old backup cleanup path", %{tmp_dir: root} do
    assert {:ok, %{action: :installed}} = Installer.install(skills_root: root)
    target = Path.join(root, "taskman-cli")
    marker_path = Path.join(target, ".taskman-managed.json")
    marker = File.read!(marker_path)
    File.write!(marker_path, String.replace(marker, Bundle.cli_version(), "0.0.1"))
    FakeSkillFileSystem.fail_next_rm_rf!()

    assert {:ok, %{action: :updated}} =
             Installer.install(skills_root: root, file_system: FakeSkillFileSystem)

    assert temporary_siblings(root) == []
  end

  @tag :tmp_dir
  test "pre-swap write failure cleans the staged directory", %{tmp_dir: root} do
    FakeSkillFileSystem.fail_next_write!()

    assert {:error, :skill_install_failed, _message} =
             Installer.install(skills_root: root, file_system: FakeSkillFileSystem)

    assert temporary_siblings(root) == []
    refute File.exists?(Path.join(root, "taskman-cli"))
  end

  @tag :tmp_dir
  test "CLI renders installation success and status 6 failures in both modes", %{tmp_dir: root} do
    result = Taskman.CLI.run(["agent", "skill", "install"], skills_root: root)
    target = Path.join(root, "taskman-cli")

    assert result.status == 0
    assert result.stderr == ""
    assert result.stdout == "Installed taskman-cli at #{target}\n"

    custom_root = Path.join(root, "custom")
    custom_target = Path.join(custom_root, "taskman-cli")
    File.mkdir_p!(custom_target)
    File.write!(Path.join(custom_target, "custom.md"), "keep")

    failed =
      Taskman.CLI.run(
        ["agent", "skill", "install", "--json"],
        skills_root: custom_root
      )

    assert failed.status == 6
    assert failed.stdout == ""
    assert %{"error" => %{"code" => "skill_install_failed"}} = Jason.decode!(failed.stderr)
  end

  defp temporary_siblings(root) do
    root
    |> File.ls!()
    |> Enum.filter(&String.starts_with?(&1, ".taskman-cli."))
  end

  defp exact_backup_siblings(root) do
    root
    |> File.ls!()
    |> Enum.filter(&Regex.match?(~r/\A\.taskman-cli\.backup-[0-9]+\z/, &1))
  end
end
