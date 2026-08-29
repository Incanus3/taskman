defmodule Taskman.CLI.OutputTest do
  use ExUnit.Case, async: true

  alias Taskman.CLI.Output

  test "JSON success output is one API data envelope and a trailing newline" do
    data = [%{"id" => 1, "name" => "One", "primary_directory" => "/tmp"}]

    assert Output.success({:projects, :list}, data, true) ==
             Jason.encode!(%{data: data}) <> "\n"
  end

  test "readable Project collections retain identifying fields" do
    data = [%{"id" => 1, "name" => "One", "primary_directory" => "/tmp"}]

    assert Output.success({:projects, :list}, data, false) ==
             "ID\tNAME\tPRIMARY DIRECTORY\n1\tOne\t/tmp\n"
  end

  test "readable Project members use one labelled line per public field" do
    data = %{"id" => 1, "name" => "One", "primary_directory" => "/tmp"}

    assert Output.success({:projects, :show}, data, false) ==
             "ID: 1\nNAME: One\nPRIMARY DIRECTORY: /tmp\n"
  end

  test "JSON errors preserve the API envelope and trailing newline" do
    envelope = %{error: %{code: "not_found", message: "Resource not found"}}

    assert Output.error(envelope, true) == Jason.encode!(envelope) <> "\n"
  end

  test "readable errors contain a concise diagnostic" do
    assert Output.error(%{"error" => %{"code" => "not_found", "message" => "Missing"}}, false) ==
             "Error: Missing (not_found)\n"
  end
end
