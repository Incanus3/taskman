defmodule TaskmanWeb.API.FallbackController do
  use TaskmanWeb, :controller

  alias TaskmanWeb.API.Representation

  def call(conn, {:error, :not_found}),
    do: error(conn, 404, "not_found", "Resource not found")

  def call(conn, {:error, :invalid_request}),
    do: error(conn, 400, "invalid_request", "Invalid request")

  def call(conn, {:error, :unchanged_location}),
    do: error(conn, 409, "unchanged_location", "Task is already at that location")

  def call(conn, {:error, :internal_error}),
    do: error(conn, 500, "internal_error", "Internal Server Error")

  def call(conn, {:error, %Ecto.Changeset{} = changeset}) do
    conn
    |> put_status(:unprocessable_entity)
    |> json(%{
      error: %{
        code: "validation_failed",
        message: "Validation failed",
        fields: Representation.validation_fields(changeset)
      }
    })
  end

  defp error(conn, status, code, message) do
    conn
    |> put_status(status)
    |> json(%{error: %{code: code, message: message}})
  end
end
