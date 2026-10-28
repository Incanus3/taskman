defmodule TaskmanWeb.FormPickerProbe do
  @moduledoc false
  use Phoenix.Component

  import Phoenix.LiveViewTest, only: [render_component: 2]
  import Plug.Conn, only: [put_resp_content_type: 2, send_resp: 3]
  alias TaskmanWeb.{DateTimePicker, SelectPicker}

  # A static, test-only page exercises the shipped components and registered hooks without
  # adding a route or test API to the application. Task browser tests own LiveView wiring.
  def init(opts), do: opts

  def call(conn, _opts) do
    conn = Plug.Static.call(conn, Plug.Static.init(at: "/", from: :taskman, gzip: true))

    if conn.halted do
      conn
    else
      conn
      |> put_resp_content_type("text/html")
      |> send_resp(200, render_component(&page/1, %{}))
    end
  end

  def page(assigns) do
    assigns =
      assign(
        assigns,
        :form,
        to_form(
          %{
            "starts_at" => "2026-10-28T17:45",
            "ends_at" => "2026-10-29T18:30",
            "room" => "blue",
            "host" => "red"
          },
          as: :meeting
        )
      )

    ~H"""
    <!DOCTYPE html>
    <html lang="en">
      <head>
        <meta charset="utf-8" />
        <meta name="csrf-token" content="test-only" />
        <link rel="stylesheet" href="/assets/css/app.css" />
        <script defer src="/assets/js/app.js">
        </script>
      </head>
      <body>
        <form id="meeting" class="mx-auto max-w-xl p-8">
          <DateTimePicker.date_time_picker
            field={@form[:starts_at]}
            id="meeting-start"
            label="Starts"
            changed_message="Start changed elsewhere"
            context_id="meeting-41"
          />
          <DateTimePicker.date_time_picker
            field={@form[:ends_at]}
            id="meeting-end"
            label="Ends"
            changed_message="End changed elsewhere"
            context_id="meeting-41"
          />
          <SelectPicker.select_picker
            field={@form[:room]}
            id="meeting-room"
            label="Room"
            options={[{"Blue", "blue"}, {"Red", "red"}, {"North room", "North room / 2"}]}
            context_id="meeting-41"
          />
          <SelectPicker.select_picker
            field={@form[:host]}
            id="meeting-host"
            label="Host"
            options={[{"Blue", "blue"}, {"Red", "red"}]}
            context_id="meeting-41"
          />
        </form>
      </body>
    </html>
    """
  end
end
