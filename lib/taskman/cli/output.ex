defmodule Taskman.CLI.Output do
  @moduledoc "Render one CLI result as readable text or an API-compatible JSON envelope."

  @project_fields [
    {:id, "ID"},
    {:name, "NAME"},
    {:primary_directory, "PRIMARY DIRECTORY"}
  ]

  @doc "Render successful command data. JSON mode always preserves the API data envelope."
  @spec success(term(), term(), boolean()) :: String.t()
  def success(_command, data, true), do: Jason.encode!(%{data: data}) <> "\n"

  def success(command, data, false) when is_list(data),
    do: readable_collection(command, data)

  def success(command, data, false) when is_map(data) do
    if hierarchy_command?(command), do: readable_hierarchy(data), else: readable_member(data)
  end

  def success(_command, data, false), do: to_string(data) <> "\n"

  @doc "Render an API or locally generated error envelope."
  @spec error(map(), boolean()) :: String.t()
  def error(envelope, true), do: Jason.encode!(envelope) <> "\n"

  def error(envelope, false) when is_map(envelope) do
    error_data = Map.get(envelope, "error") || Map.get(envelope, :error) || %{}
    code = Map.get(error_data, "code") || Map.get(error_data, :code) || "error"
    message = Map.get(error_data, "message") || Map.get(error_data, :message) || "Unknown error"
    fields = Map.get(error_data, "fields") || Map.get(error_data, :fields)

    ["Error: #{message} (#{code})\n", render_fields(fields)]
    |> IO.iodata_to_binary()
  end

  def error(_envelope, false), do: "Error: Unknown error (error)\n"

  defp readable_collection(command, rows) do
    case resource(command) do
      :projects ->
        ["ID\tNAME\tPRIMARY DIRECTORY\n", Enum.map(rows, &project_row/1)]
        |> IO.iodata_to_binary()

      :lists ->
        ["ID\tNAME\tPARENT\tPATH\n", Enum.map(rows, &list_row/1)]
        |> IO.iodata_to_binary()

      :tasks ->
        ["ID\tTITLE\tPARENT\tSTATUS\tPRIORITY\tLOCATION\tDUE\n", Enum.map(rows, &task_row/1)]
        |> IO.iodata_to_binary()

      _other ->
        rows
        |> Enum.map(&readable_member/1)
        |> IO.iodata_to_binary()
    end
  end

  defp project_row(project) do
    [
      value(project, :id),
      "\t",
      value(project, :name),
      "\t",
      value(project, :primary_directory),
      "\n"
    ]
  end

  defp list_row(task_list) do
    [
      value(task_list, :id),
      "\t",
      value(task_list, :name),
      "\t",
      value(task_list, :parent_list_id),
      "\t",
      value(task_list, :path),
      "\n"
    ]
  end

  defp task_row(task) do
    [
      value(task, :id),
      "\t",
      value(task, :title),
      "\t",
      value(task, :parent_task_id),
      "\t",
      value(task, :status),
      "\t",
      value(task, :priority),
      "\t",
      format_task_location(fetch_value(task, :location)),
      "\t",
      value(task, :due_at),
      "\n"
    ]
  end

  defp format_task_location(location) when is_map(location) do
    path = fetch_value(location, :path)

    case path do
      [] -> "Project"
      path when is_list(path) -> Enum.map_join(path, " / ", &format_value/1)
      _other -> format_value(path)
    end
  end

  defp format_task_location(nil), do: "—"
  defp format_task_location(location), do: format_value(location)

  defp readable_member(project) when is_map(project) do
    fields =
      if project_fields?(project) do
        @project_fields
      else
        project
        |> Map.keys()
        |> Enum.sort_by(&to_string/1)
        |> Enum.map(fn key -> {key, String.upcase(String.replace(to_string(key), "_", " "))} end)
      end

    Enum.map(fields, fn {key, label} -> [label, ": ", value(project, key), "\n"] end)
    |> IO.iodata_to_binary()
  end

  defp readable_hierarchy(hierarchy) do
    selected_task_id = fetch_value(hierarchy, :selected_task_id)
    root = fetch_value(hierarchy, :root)

    [hierarchy_root_line(root, selected_task_id), hierarchy_children(root, selected_task_id, "")]
    |> IO.iodata_to_binary()
  end

  defp hierarchy_root_line(node, selected_task_id) do
    [hierarchy_task_label(fetch_value(node, :task), selected_task_id), "\n"]
  end

  defp hierarchy_children(node, selected_task_id, prefix) do
    node
    |> fetch_value(:children)
    |> Enum.with_index()
    |> Enum.map(fn {child, index} ->
      last? = index == length(fetch_value(node, :children)) - 1
      connector = if last?, do: "└─ ", else: "├─ "
      child_prefix = prefix <> if(last?, do: "   ", else: "│  ")

      [
        prefix,
        connector,
        hierarchy_task_label(fetch_value(child, :task), selected_task_id),
        "\n",
        hierarchy_children(child, selected_task_id, child_prefix)
      ]
    end)
  end

  defp hierarchy_task_label(task, selected_task_id) do
    selected = if fetch_value(task, :id) == selected_task_id, do: "  [selected]", else: ""
    [value(task, :id), "  ", value(task, :title), selected]
  end

  defp project_fields?(project) do
    Enum.all?(@project_fields, fn {key, _label} -> has_value?(project, key) end)
  end

  defp resource({resource, _action}) when is_atom(resource), do: resource
  defp resource(:list), do: :projects
  defp resource(%Taskman.CLI.Command{path: ["projects" | _rest]}), do: :projects
  defp resource(%Taskman.CLI.Command{path: ["lists" | _rest]}), do: :lists
  defp resource(%Taskman.CLI.Command{path: ["tasks" | _rest]}), do: :tasks
  defp resource(%Taskman.CLI.Command{path: [resource | _rest]}), do: resource
  defp resource(["projects" | _rest]), do: :projects
  defp resource(["lists" | _rest]), do: :lists
  defp resource(["tasks" | _rest]), do: :tasks
  defp resource([resource | _rest]) when is_atom(resource), do: resource
  defp resource([resource | _rest]) when is_binary(resource), do: resource

  defp resource(command) when is_binary(command) do
    case String.split(command, ~r/\s+/, trim: true) do
      ["projects" | _rest] -> :projects
      ["lists" | _rest] -> :lists
      ["tasks" | _rest] -> :tasks
      [resource | _rest] -> resource
      _empty -> nil
    end
  end

  defp resource(_command), do: nil

  defp hierarchy_command?({:tasks, :hierarchy}), do: true
  defp hierarchy_command?(%Taskman.CLI.Command{handler: {:tasks, :hierarchy}}), do: true
  defp hierarchy_command?(["tasks", "hierarchy"]), do: true
  defp hierarchy_command?("tasks hierarchy"), do: true
  defp hierarchy_command?(_command), do: false

  defp has_value?(map, key), do: Map.has_key?(map, key) or Map.has_key?(map, Atom.to_string(key))

  defp value(map, key) do
    map
    |> fetch_value(key)
    |> format_value()
  end

  defp fetch_value(map, key) do
    case Map.fetch(map, key) do
      {:ok, value} -> value
      :error -> Map.get(map, Atom.to_string(key))
    end
  end

  defp format_value(nil), do: "—"
  defp format_value(value) when is_binary(value), do: value
  defp format_value(value) when is_boolean(value), do: to_string(value)
  defp format_value(value) when is_atom(value), do: Atom.to_string(value)
  defp format_value(value) when is_number(value), do: to_string(value)
  defp format_value(value) when is_list(value), do: Enum.map_join(value, " / ", &format_value/1)
  defp format_value(value) when is_map(value), do: Jason.encode!(value)
  defp format_value(value), do: inspect(value)

  defp render_fields(fields) when is_map(fields) do
    fields
    |> Enum.sort_by(fn {key, _messages} -> to_string(key) end)
    |> Enum.map(fn {key, messages} ->
      [String.upcase(to_string(key)), ": ", Enum.join(List.wrap(messages), ", "), "\n"]
    end)
  end

  defp render_fields(_fields), do: []
end
