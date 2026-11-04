defmodule TaskmanWeb.FormPickersBrowserTest do
  use ExUnit.Case, async: false
  alias Wallaby.{Browser, Query}
  @moduletag :browser

  setup do
    server =
      start_supervised!(
        {Bandit,
         plug: TaskmanWeb.FormPickerProbe, port: 0, ip: {127, 0, 0, 1}, startup_log: false}
      )

    {:ok, {_, port}} = ThousandIsland.listener_info(server)
    {:ok, _} = Application.ensure_all_started(:wallaby)

    {:ok, session} =
      Wallaby.start_session(binary: System.find_executable("chromium"), window_size: [1440, 900])

    on_exit(fn -> Wallaby.end_session(session) end)
    Browser.visit(session, "http://127.0.0.1:#{port}/")
    Browser.find(session, Query.css("#meeting-start"))

    # LiveView mounts these hooks on its static-page view. Replace only server delivery;
    # DOM events, popovers, focus and calendar library run in Chromium.
    assert js(session, ~S"""
           window.probeHooks = {};
           window.probeEvents = []; window.probePushes = [];
           document.querySelector('#meeting').addEventListener('input', e => probeEvents.push(['input', e.target.id]));
           document.querySelector('#meeting').addEventListener('change', e => probeEvents.push(['change', e.target.id]));
           for (const el of document.querySelectorAll('[phx-hook]')) {
             const hook = window.liveSocket.main.getHook(el);
             hook.pushEvent = (event, payload) => {
               probePushes.push([event, payload]);
               // An already queued validation patch can precede the reveal reply.
               probeHooks[el.dataset.inputId].updated();
               el.dataset.visibleValues = 'null';
               el.dataset.action = 'null';
               probeHooks[el.dataset.inputId].updated();
             };
             if (!hook.mounted) return false;
             probeHooks[el.dataset.inputId] = hook;
           }
           return Object.keys(probeHooks).length === 4;
           """)

    %{session: session}
  end

  test "generic reveal is a separate action that retains value, open state and isolation", %{
    session: s
  } do
    click(s, "meeting-room-trigger")
    refute js(s, "return !!document.getElementById('meeting-room-option-North%20room%20%2F%202')")
    click(s, "meeting-room-reveal")
    assert js(s, "return probePushes") == [["reveal_rooms", %{}]]
    assert values(s) == ["28.10.2026 17:45", "29.10.2026 18:30", "blue", "red"]
    assert events(s) == []

    assert js(
             s,
             "return document.querySelector('#meeting-room-popup').matches(':popover-open') && document.activeElement.id === 'meeting-room-options'"
           )

    assert js(
             s,
             "return document.querySelector('#meeting-room-options').getAttribute('aria-activedescendant')"
           ) == "meeting-room-option-North%20room%20%2F%202"

    click(s, "meeting-room-option-North%20room%20%2F%202")
    assert events(s) == [["change", "meeting-room"]]
    click(s, "meeting-host-trigger")
    click(s, "meeting-host-option-blue")
    assert values(s) == ["28.10.2026 17:45", "29.10.2026 18:30", "North room / 2", "blue"]
    assert events(s) == [["change", "meeting-room"], ["change", "meeting-host"]]
  end

  test "unrelated date and select instances isolate values, events, notices and focus", %{
    session: s
  } do
    assert js(s, ~S"""
           const ids = [...document.querySelectorAll('[id]')].map(e => e.id);
           return new Set(ids).size === ids.length &&
             [...document.querySelectorAll('[aria-controls], [aria-describedby], [aria-labelledby]')].every(e =>
               ['aria-controls', 'aria-describedby', 'aria-labelledby'].every(attr =>
                 !e.hasAttribute(attr) || e.getAttribute(attr).split(' ').every(id => document.getElementById(id))));
           """)

    click(s, "meeting-start-trigger")
    minute(s, "meeting-start", "46")
    click(s, "meeting-start-apply")
    assert values(s) == ["28.10.2026 17:46", "29.10.2026 18:30", "blue", "red"]
    assert events(s) == [["input", "meeting-start"]]
    assert js(s, "return document.activeElement.id") == "meeting-start-trigger"

    click(s, "meeting-end-trigger")
    minute(s, "meeting-end", "31")
    click(s, "meeting-end-cancel")
    assert values(s) == ["28.10.2026 17:46", "29.10.2026 18:30", "blue", "red"]
    assert events(s) == [["input", "meeting-start"]]
    assert js(s, "return document.activeElement.id") == "meeting-end-trigger"

    click(s, "meeting-end-trigger")
    minute(s, "meeting-end", "32")
    click(s, "meeting-end-apply")
    assert values(s) == ["28.10.2026 17:46", "29.10.2026 18:32", "blue", "red"]
    assert events(s) == [["input", "meeting-start"], ["input", "meeting-end"]]

    click(s, "meeting-room-trigger")
    click(s, "meeting-room-option-red")
    assert values(s) == ["28.10.2026 17:46", "29.10.2026 18:32", "red", "red"]
    click(s, "meeting-host-trigger")
    click(s, "meeting-host-option-blue")
    assert values(s) == ["28.10.2026 17:46", "29.10.2026 18:32", "red", "blue"]

    assert events(s) == [
             ["input", "meeting-start"],
             ["input", "meeting-end"],
             ["change", "meeting-room"],
             ["change", "meeting-host"]
           ]

    # Opening another control dismisses without applying private drafts.
    click(s, "meeting-start-trigger")
    minute(s, "meeting-start", "59")
    # Direct clicks isolate coordination from the fixture's overlapping popup geometry.
    for {next, previous} <- [
          {"meeting-room", "meeting-start"},
          {"meeting-host", "meeting-room"},
          {"meeting-end", "meeting-host"}
        ] do
      assert js(
               s,
               "document.getElementById(arguments[0]+'-trigger').click(); return document.getElementById(arguments[1]+'-trigger').getAttribute('aria-expanded') === 'false'",
               [next, previous]
             )
    end

    assert length(events(s)) == 4

    assert js(s, ~S"""
           const hook = probeHooks['meeting-end'];
           hook.el.dataset.value = '2027-01-02T03:04';
           hook.updated();
           return !hook.opened && document.querySelector('#meeting-end-picker-notice').textContent === 'End changed elsewhere' &&
             document.querySelector('#meeting-start-picker-notice').textContent === '';
           """)

    click(s, "meeting-start-trigger")

    assert js(s, ~S"""
           const hook = probeHooks['meeting-start'];
           hook.el.dataset.contextId = 'meeting-42';
           hook.updated();
           return !hook.opened && document.querySelector('#meeting-start-picker-notice').textContent === '';
           """)

    assert values(s) == ["28.10.2026 17:46", "29.10.2026 18:32", "red", "blue"]
    assert length(events(s)) == 4

    click(s, "meeting-room-trigger")

    assert js(s, ~S"""
           const hook = probeHooks['meeting-room'];
           hook.el.dataset.contextId = 'meeting-42';
           hook.updated();
           return !hook.opened && document.querySelector('#meeting-host').value === 'blue';
           """)

    # Teardown removes listeners; remounting must not duplicate form events.
    js(
      s,
      "Object.values(probeHooks).forEach(h => h.destroyed()); Object.values(probeHooks).forEach(h => h.mounted())"
    )

    click(s, "meeting-start-trigger")
    minute(s, "meeting-start", "47")
    click(s, "meeting-start-apply")
    assert List.last(events(s)) == ["input", "meeting-start"]
    assert length(events(s)) == 5
    js(s, "Object.values(probeHooks).forEach(h => h.destroyed())")
  end

  test "select values containing whitespace keep a single resolvable active descendant", %{
    session: s
  } do
    click(s, "meeting-room-trigger")
    Browser.send_keys(s, [:end, :down_arrow, :space])
    Browser.send_keys(s, [:end])

    assert js(s, ~S"""
           const panel = document.querySelector('#meeting-room-options');
           const id = panel.getAttribute('aria-activedescendant');
           return id.split(/\s+/).length === 1 && document.getElementById(id)?.getAttribute('role') === 'option';
           """)

    Browser.send_keys(s, [:enter])
    assert Enum.at(values(s), 2) == "North room / 2"
    assert Enum.at(values(s), 3) == "red"
    assert events(s) == [["change", "meeting-room"]]
    js(s, "Object.values(probeHooks).forEach(h => h.destroyed())")
  end

  defp click(session, id), do: Browser.click(session, Query.css(~s([id="#{id}"])))

  defp minute(session, id, minute) do
    js(
      session,
      "const e=document.querySelector('#'+arguments[0]+'-popup [data-vc-time-input=minute] input'); e.value=arguments[1]; e.dispatchEvent(new Event('change',{bubbles:true}))",
      [id, minute]
    )
  end

  defp values(session),
    do:
      js(
        session,
        "return ['meeting-start','meeting-end','meeting-room','meeting-host'].map(id=>document.getElementById(id).value)"
      )

  defp events(session), do: js(session, "return window.probeEvents")

  defp js(session, script, args \\ []) do
    ref = make_ref()
    owner = self()
    Browser.execute_script(session, script, args, fn result -> send(owner, {ref, result}) end)

    receive do
      {^ref, result} -> result
    after
      5_000 -> flunk("browser script did not return")
    end
  end
end
