defmodule Taskman.ReleaseConfigurationTest do
  use ExUnit.Case, async: true

  @release_env_path Path.expand("../../rel/env.sh.eex", __DIR__)
  @vm_args_path Path.expand("../../rel/vm.args.eex", __DIR__)
  @remote_vm_args_path Path.expand("../../rel/remote.vm.args.eex", __DIR__)

  test "rendered release environment exports the private node identity" do
    rendered = EEx.eval_file(@release_env_path, assigns: [release: %{name: :taskman}])

    probe =
      rendered <>
        "\nsh -c 'printf \"%s\\n\" \"$RELEASE_DISTRIBUTION\" \"$RELEASE_NODE\"'\n"

    assert {output, 0} =
             System.cmd("sh", ["-c", probe],
               env: [{"RELEASE_DISTRIBUTION", nil}, {"RELEASE_NODE", nil}],
               stderr_to_stdout: true
             )

    assert output == "name\ntaskman@127.0.0.1\n"
  end

  test "release VM arguments keep distribution on the fixed loopback boundary" do
    assert effective_vm_args(@vm_args_path) == [
             "-kernel",
             "inet_dist_use_interface",
             "{127,0,0,1}",
             "-start_epmd",
             "false",
             "-erl_epmd_port",
             "6789"
           ]
  end

  test "remote VM arguments connect to the fixed port without listening" do
    assert effective_vm_args(@remote_vm_args_path) == [
             "-start_epmd",
             "false",
             "-erl_epmd_port",
             "6789",
             "-dist_listen",
             "false"
           ]
  end

  defp effective_vm_args(path) do
    path
    |> File.read!()
    |> String.split("\n")
    |> Enum.reject(fn line ->
      String.trim(line) == "" or String.starts_with?(String.trim(line), "#")
    end)
    |> Enum.flat_map(&String.split/1)
  end
end
