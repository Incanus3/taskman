defmodule TaskmanWeb.FormPickersTest do
  use ExUnit.Case, async: true
  use Phoenix.Component

  import Phoenix.LiveViewTest
  alias TaskmanWeb.{DateTimePicker, SelectPicker}

  test "date controls derive unique field and ARIA references from caller IDs" do
    form =
      to_form(%{"starts_at" => "2026-10-28T17:45", "ends_at" => "2026-10-29T18:30"}, as: :meeting)

    for {key, id, label} <- [
          {:starts_at, "meeting-start", "Starts"},
          {:ends_at, "meeting-end", "Ends"}
        ] do
      document =
        render_component(&DateTimePicker.date_time_picker/1,
          field: form[key],
          id: id,
          label: label,
          trigger_label: "Choose " <> label,
          changed_message: label <> " changed elsewhere",
          context_id: "meeting-41"
        )
        |> LazyHTML.from_fragment()

      refute Enum.empty?(
               LazyHTML.query(
                 document,
                 "##{id}[name='meeting[#{key}]'][aria-describedby='#{id}-format']"
               )
             )

      refute Enum.empty?(
               LazyHTML.query(
                 document,
                 "##{id}-picker[data-input-id='#{id}'][data-context-id='meeting-41'][data-changed-message='#{label} changed elsewhere']"
               )
             )

      refute Enum.empty?(
               LazyHTML.query(
                 document,
                 "##{id}-trigger[aria-controls='#{id}-popup'][aria-label='Choose #{label}']"
               )
             )

      refute Enum.empty?(
               LazyHTML.query(document, "##{id}-popup[role='dialog'][aria-label='#{label}']")
             )
    end
  end

  test "date controls offer generic defaults and readonly text without an active popup" do
    form = to_form(%{"starts_at" => "28.10."}, as: :meeting)

    document =
      render_component(&DateTimePicker.date_time_picker/1,
        field: form[:starts_at],
        id: "meeting-start",
        label: "Starts"
      )
      |> LazyHTML.from_fragment()

    refute Enum.empty?(
             LazyHTML.query(document, "#meeting-start-trigger[aria-label='Choose date and time']")
           )

    refute Enum.empty?(
             LazyHTML.query(
               document,
               "#meeting-start-picker[data-changed-message='Date and time changed. Reopen the picker to edit the latest value.']"
             )
           )

    document =
      render_component(&DateTimePicker.date_time_picker/1,
        field: form[:starts_at],
        id: "meeting-start",
        label: "Starts",
        readonly: true
      )
      |> LazyHTML.from_fragment()

    refute Enum.empty?(LazyHTML.query(document, "#meeting-start[readonly][value='28.10.']"))
    assert Enum.empty?(LazyHTML.query(document, "[phx-hook]"))
  end

  test "select controls use caller fields, context and disabled native fallbacks" do
    form = to_form(%{"room" => "blue", "host" => "red"}, as: :meeting)

    for {key, id} <- [{:room, "meeting-room"}, {:host, "meeting-host"}] do
      document =
        render_component(&SelectPicker.select_picker/1,
          field: form[key],
          id: id,
          label: "Choose #{key}",
          options: [{"Blue", "blue"}, {"Red", "red"}],
          context_id: "meeting-41"
        )
        |> LazyHTML.from_fragment()

      refute Enum.empty?(
               LazyHTML.query(
                 document,
                 "##{id}-picker[data-input-id='#{id}'][data-context-id='meeting-41']"
               )
             )

      refute Enum.empty?(
               LazyHTML.query(
                 document,
                 "##{id}-trigger[aria-controls='#{id}-options'][aria-labelledby='#{id}-label']"
               )
             )

      refute Enum.empty?(
               LazyHTML.query(
                 document,
                 "##{id}[name='meeting[#{key}]'] option[selected][value='#{form[key].value}']"
               )
             )
    end

    document =
      render_component(&SelectPicker.select_picker/1,
        field: form[:room],
        id: "meeting-room",
        label: "Room",
        options: [{"Blue", "blue"}],
        disabled: true
      )
      |> LazyHTML.from_fragment()

    refute Enum.empty?(
             LazyHTML.query(document, "#meeting-room[disabled] option[selected][value='blue']")
           )

    assert Enum.empty?(LazyHTML.query(document, "[phx-hook]"))
  end
end
