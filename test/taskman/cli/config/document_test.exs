defmodule Taskman.CLI.Config.DocumentTest do
  use ExUnit.Case, async: true

  alias Taskman.CLI.Config.Document

  @key "tm_synthetic_payload_checksum"
  @url "https://taskman.example.com"

  test "optional host settings treat null as absence" do
    assert {:ok, %{format: :v2, default_host: nil, hosts: %{"personal" => %{api_key: nil}}}} =
             Document.decode(
               ~s({"version":2,"default_host":null,"hosts":{"personal":{"api_url":"https://taskman.example.com","api_key":null}}})
             )

    for optional <- [%{}, %{"default_host" => nil}] do
      for key <- [%{}, %{"api_key" => nil}] do
        input =
          Map.merge(
            %{"version" => 2, "hosts" => %{"personal" => Map.put(key, "api_url", @url)}},
            optional
          )

        assert {:ok, %{default_host: nil, hosts: %{"personal" => %{api_url: @url, api_key: nil}}}} =
                 decode(input)
      end
    end
  end

  test "legacy partial connections project to default without fresh localhost fallback" do
    rows = [
      {%{}, %{}, nil},
      {%{"api_url" => @url}, %{"default" => %{api_url: @url, api_key: nil}}, "default"},
      {%{"api_key" => @key}, %{"default" => %{api_url: "http://localhost:4000", api_key: @key}},
       "default"},
      {%{"api_url" => @url, "api_key" => @key}, %{"default" => %{api_url: @url, api_key: @key}},
       "default"},
      {%{"api_url" => nil, "api_key" => nil}, %{}, nil},
      {%{"api_url" => nil}, %{}, nil},
      {%{"api_key" => nil}, %{}, nil},
      {%{"api_url" => @url, "api_key" => nil}, %{"default" => %{api_url: @url, api_key: nil}},
       "default"},
      {%{"api_url" => nil, "api_key" => @key},
       %{"default" => %{api_url: "http://localhost:4000", api_key: @key}}, "default"}
    ]

    for {stored, hosts, default_host} <- rows do
      assert {:ok, %{format: :legacy, hosts: ^hosts, default_host: ^default_host}} =
               decode(stored)
    end
  end

  test "unsupported fields and invalid required values refuse" do
    invalid = [
      nil,
      [],
      true,
      2,
      %{"extra" => @key},
      %{"api_url" => ""},
      %{"api_key" => ""},
      %{"api_url" => 7},
      %{"api_key" => false},
      %{"version" => 1, "hosts" => %{}},
      %{"version" => 2.0, "hosts" => %{}},
      %{"version" => nil, "hosts" => %{}},
      %{"hosts" => %{}},
      %{"version" => 2},
      %{"version" => 2, "hosts" => nil},
      %{"version" => 2, "hosts" => []},
      %{"version" => 2, "hosts" => %{}, "api_url" => @url},
      %{"version" => 2, "hosts" => %{}, "api_key" => @key},
      %{"version" => 2, "hosts" => %{}, "extra" => @key},
      %{"version" => 2, "hosts" => %{}, "default_host" => "missing"},
      %{"version" => 2, "hosts" => %{}, "default_host" => false}
    ]

    invalid_hosts = [
      nil,
      [],
      %{},
      %{"api_url" => nil},
      %{"api_url" => ""},
      %{"api_url" => @url, "api_key" => ""},
      %{"api_url" => @url, "api_key" => 2},
      %{"api_url" => @url, "extra" => @key}
    ]

    invalid =
      invalid ++ Enum.map(invalid_hosts, &%{"version" => 2, "hosts" => %{"personal" => &1}})

    invalid =
      invalid ++
        Enum.map(
          ["", "Personal", "1host", "host.name", " host", "../host", String.duplicate("a", 65)],
          fn name ->
            %{"version" => 2, "hosts" => %{name => %{"api_url" => @url}}}
          end
        )

    for input <- invalid do
      assert {:error, :invalid_configuration, message} = decode(input)
      refute message =~ @key
    end

    assert {:error, :invalid_configuration, message} = Document.decode("not-json #{@key}")
    refute message =~ @key
  end

  test "v2 encoding preserves host settings and round trips legacy projections" do
    assert {:ok, document} = decode(%{"api_url" => @url, "api_key" => @key})
    encoded = Document.encode(document)

    assert encoded == %{
             "version" => 2,
             "default_host" => "default",
             "hosts" => %{"default" => %{"api_url" => @url, "api_key" => @key}}
           }

    assert {:ok,
            %{
              format: :v2,
              hosts: %{"default" => %{api_url: @url, api_key: @key}},
              default_host: "default"
            }} = decode(encoded)

    assert {:ok, document} = decode(%{"version" => 2, "hosts" => %{}})
    assert Document.encode(document) == %{"version" => 2, "hosts" => %{}}
  end

  test "host names are exact lowercase ASCII identifiers" do
    for name <- ["a", "default", "personal-host_2", String.duplicate("a", 64)] do
      assert :ok = Document.validate_host_name(name)
    end

    for name <- [nil, 7, "", "Upper", "host ", "á", "host/name", String.duplicate("a", 65)] do
      assert {:error, :invalid_configuration, _} = Document.validate_host_name(name)
    end
  end

  test "URLs and keys retain the constrained connection rules without secret diagnostics" do
    for url <- [@url, "https://[::1]:4001/", "http://localhost:4000"] do
      assert :ok = Document.validate_url(url)
    end

    for url <- [
          nil,
          "",
          " https://taskman.example",
          "https://taskman.example ",
          "https://user:#{@key}@taskman.example",
          "https://taskman.example/path",
          "https://taskman.example?key=#{@key}",
          "https://taskman.example#fragment",
          "https://taskman.example:0",
          "https://taskman.example:65536",
          "ftp://taskman.example"
        ] do
      assert {:error, :invalid_configuration, message} = Document.validate_url(url)
      refute message =~ @key
      assert {:error, :invalid_configuration, _} = decode(%{"api_url" => url || ""})
    end

    assert :ok = Document.validate_api_key(@key)

    for key <- [
          nil,
          "",
          "tm_missing",
          "tm_payload_",
          "tm_payload_checksum\n",
          "tm_payload checksum"
        ] do
      assert {:error, :invalid_configuration, message} = Document.validate_api_key(key)
      refute message =~ @key
      assert {:error, :invalid_configuration, _} = decode(%{"api_key" => key || ""})
    end
  end

  defp decode(input), do: input |> Jason.encode!() |> Document.decode()
end
