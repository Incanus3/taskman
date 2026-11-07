# Markdown task descriptions with Leaf

**Date:** 2026-11-06  
**Status:** Implemented and operator accepted for initial delivery, including D1–D12. Known Leaf
defects remain deferred follow-ups; the same-LiveView navigation boundary is accepted.

Task descriptions should support formatted Markdown editing without a separate preview pane.
Use Leaf's hybrid editor and formatting toolbar first. Keep ProseMark as the likely replacement
if Leaf does not meet the editing and content-preservation requirements below.

## Decisions for review

| ID | Status | Choice | Cost or constraint | Contract |
|---|---|---|---|---|
| D1 | Accepted | Try Leaf first; retain ProseMark as the most likely fallback | Leaf's custom browser editing and serialization need direct verification | [Choice](#why-leaf-is-the-first-choice) |
| D2 | Accepted | Keep descriptions always editable, as today | No separate reading surface | [Editing](#hybrid-editing-and-toolbar-controls-work-together) |
| D3 | Accepted | Store Markdown in the existing description string | Existing descriptions containing Markdown syntax acquire formatting | [Storage](#markdown-remains-the-description-value) |
| D4 | Accepted | Use one project-owned Leaf integration for creation, detail and recovery | Must bridge editor state into current form and navigation contracts | [Integration](#leaf-is-an-editor-not-a-second-persistence-workflow) |
| D5 | Accepted | Start with ordinary Markdown formatting, links, code, tables and task-list items; omit uploads and custom embeds | Advanced Leaf features remain unavailable | [Editing](#hybrid-editing-and-toolbar-controls-work-together) |
| D6 | Accepted | Trial the unpatched release with its known table/strikethrough rendering limitation; fix and contribute upstream as follow-up | Markdown rendering can show literal syntax until the correction is adopted | [Published-package finding](#published-leaf-does-not-enable-tables-or-strikethrough) |
| D7 | Accepted | Temporarily accept strikethrough source escaping on unrelated edits and track an upstream fix | Intended strikethrough formatting can become literal Markdown even though words survive | [Source conversion](#integration-trial-exposes-source-and-undo-preservation-failures) |
| D8 | Accepted | Remount Leaf on intentional canonical content replacement | Discards the predecessor and all earlier undo history; may reset focus/selection at replacement | [Replacement](#intentional-replacement-starts-a-fresh-editor-history) |
| D9 | Accepted | Keep the initial capture guarantee within the current LiveView and accept the existing full-document exit boundary | A full-document exit can lose unsent description text; no additional description unload guard is provided | [Evidence and boundary](#chromium-acceptance-and-limits) |
| D10 | Accepted | Temporarily accept the reproduced raw audio/video source loss and track a later correction | Editing another paragraph can erase existing media URLs | [Source defect and follow-up](#existing-media-and-fenced-code-indentation-require-a-decision) |
| D11 | Accepted | Temporarily accept the reproduced first-line fenced-code indentation loss and track a later correction | An unrelated edit can change code structure | [Source defect and follow-up](#existing-media-and-fenced-code-indentation-require-a-decision) |
| D12 | Accepted | Use working native manual resizing with an initial height of roughly ten lines | Automatic growth is disabled; Leaf keeps the initial height as its minimum and each mode has its own size | [Sizing](#manual-resizing-uses-leafs-supported-fixed-height-mode) |

Design decisions D1–D12 are accepted. The initial unpatched trial retains the exact defects
recorded in D6–D7 and D10–D11; other preservation requirements remain in force. D8 establishes
fresh replacement history. D9 limits capture guarantees to in-app, same-LiveView transitions;
full-document exits retain the existing behavior. These are accepted trial trade-offs, not fixes.
The operator selected follow-up tracking and later attempted upstream contributions for the two
additional source defects. Publication, dependency changes and deployment retain separate gates.

## Existing behavior and boundaries

Inspected baseline: `3f69aa815cf7f29e0325627fab80b5fd9594bab0`, with no uncommitted changes at
the start of design work. This is source inspection, not a running-browser acceptance result.

- `lib/taskman_web/components/tasks/form.ex` renders the same description textarea for creation
  and detail, and a read-only version in recovery. Both normal forms use `task[description]`.
- `ProjectLive.Tasks.Autosave` owns description drafts, field-specific save scheduling, baseline
  comparison and conflict resolution. It already distinguishes local draft from persisted text.
- `ProjectLive.Tasks.Editing` consumes targeted `autosave_task` events and flushes server-side
  drafts before leaving a Task. `ProjectLive.Reconciliation` routes scheduled saves and external
  updates; Leaf messages require an explicit routing integration.
- Creation writes only on Create task. A description change must not create a Task early.
- Existing descriptions are non-null strings; clearing persists `""`. API and CLI consumers
  already exchange the description string.
- Assets use app.js/app.css with locked npm packages, LiveView hooks, Tailwind and daisyUI.

Inherited authorities are the [editing/autosave contract](2026-07-31-task-editing-lifecycle-design.md),
[creation contract](2026-08-03-full-task-creation-form-design.md),
[cooperative update contract](2026-09-01-live-task-updates-design.md), and
[missing-location recovery contract](2026-09-18-missing-list-recovery-design.md).
This proposal replaces their plain-text description control where applicable; persistence,
authority, conflict and recovery guarantees remain requirements.

## Why Leaf is the first choice

The operator selected hybrid editing: Markdown syntax is editable where the cursor is active,
while other content appears formatted. Toolbar controls must also serve people who do not write
Markdown directly.

| Approach | Fit | Trade-off and disposition |
|---|---|---|
| Leaf | Supplies hybrid mode, toolbar and a LiveView component | Selected for trial; custom editing and Markdown conversion require verification |
| ProseMark | Keeps a CodeMirror Markdown document with hidden syntax | Likely fallback; toolbar UI and additional formatting actions belong to Taskman |
| Milkdown | Structured rich-text editor with Markdown input shortcuts | No built-in equivalent of the selected hybrid interaction was found |
| Vditor | Supplies an instant-rendering mode and toolbar | Operator declined its experience |

Research evidence checked on 2026-11-06:

- [Leaf README](https://github.com/alexdont/leaf): hybrid mode, toolbar configuration, form
  synchronization, saved-state controls, navigation flushing and local bundle import.
- [Leaf source](https://github.com/alexdont/leaf/blob/main/priv/static/assets/leaf.js): editable
  HTML is serialized to Markdown; `sync_input_name` updates a hidden input during visual edits.
- [Leaf changelog](https://github.com/alexdont/leaf/blob/main/CHANGELOG.md): versions 0.10.0 and
  0.10.1 contain fixes for text loss, escaping, parent rerenders and HTML sanitization. These are
  reported fixes, not proof that Taskman's use cases are correct.
- [ProseMark](https://github.com/jsimonrichard/ProseMark) and its
  [formatting commands](https://github.com/jsimonrichard/ProseMark/blob/main/packages/core/lib/markdownFormattingKeymap.ts):
  hybrid source-based foundation with reusable inline-formatting actions, but no supplied toolbar.

Inspect the exact published Leaf package before pinning it; upstream main is not a release
guarantee. Pin a reviewed release including the documented sanitization fixes, initially evaluate
0.10.1, and import its matching JavaScript from the Mix dependency rather than a CDN or copied file.
Do not silently move to a different release or fork to resolve a blocker.
Use the existing app.js/app.css bundles. Disable Leaf's optional inline bundle-check script to
respect the repository's prohibition on raw inline scripts in templates.

## Published Leaf does not enable tables or strikethrough

An isolated runtime check of the published Hex package `leaf` 0.10.1, resolving `mdex` 0.14.6,
established that `Leaf.Markdown.markdown_to_html/1` returns literal Markdown for both of these inputs:

| Input | Observed renderer output |
|---|---|
| `~~removed~~` | `<p>~~removed~~</p>` |
| A two-column pipe table with a separator row | A paragraph containing pipe characters and line breaks, with no table element |

The package's `lib/leaf/markdown.ex:44` passes render and sanitization options to MDEx, but does
not enable `extension: [table: true, strikethrough: true]`. MDEx defaults both extensions to
false. Running MDEx with the same options plus these two flags produced `<del>removed</del>`
and a `<table>` respectively. The renderer is used for initial content and `:set_content`, so
toolbar availability alone does not establish reload correctness.

The [public demo](https://sasha.don.ee/demo/leaf) was then checked in Orca's browser. Its server
and JavaScript bundle both reported Leaf 0.8.0, so this is separate evidence from the 0.10.1
package check. Selecting text and clicking the strikethrough toolbar button created a rendered
`<strike>` element successfully. The failure is narrower than unavailable formatting controls:
the browser's editing path can produce formatting that the Markdown rendering path does not
restore.

Demo reproduction: switch to Markdown mode, enter `~~Leaf probe~~` followed by a blank line and
the two-column table above, then switch back to Hybrid. The visible editor contained
`<p>~~Leaf probe~~</p>` and a paragraph with pipe characters and `<br>` elements, with no
strikethrough or table elements. This verifies a mode round-trip in the demo; it does not
establish a save/reload result in Taskman or browser behavior of the published 0.10.1 package.

Provenance: [Leaf release metadata](https://hex.pm/api/packages/leaf/releases/0.10.1),
[published package](https://repo.hex.pm/tarballs/leaf-0.10.1.tar), outer archive SHA-256
`b6c2538cc167e8031acf0e25d2fe15ad6884d35726d99db98419f083858a1a3b` matching release metadata.
The experiment installed dependencies into a temporary Mix installation, not the repository.

Reproduce independently with:

```sh
MIX_INSTALL_DIR="$(mktemp -d)" elixir -e '
  Mix.install([{:leaf, "0.10.1"}, {:mdex, "0.14.6"}])
  IO.inspect(Leaf.Markdown.markdown_to_html("~~removed~~"))
  IO.inspect(Leaf.Markdown.markdown_to_html("| A | B |\n| --- | --- |\n| one | two |"))
'
```

Accepted D6 keeps the unpatched release for the initial trial. The operator accepts literal table
and strikethrough syntax from the affected Markdown-rendering paths for starters; this is not a
blocker and does not remove their toolbar controls. The exception covers presentation, not lost
text or silently corrupted source.

Follow-up: add focused renderer regression tests, enable the missing MDEx extensions, verify
browser mode and save/reload round-trips, and contribute the correction upstream. Then adopt a
reviewed, pinned upstream version in Taskman. Avoid a private dependency fork during the initial
trial. Re-evaluate if the correction requires broader editing changes than the reproduced options
defect. ProseMark remains the fallback for other material Leaf shortcomings, with its toolbar cost.

## Integration trial exposes source and undo preservation failures

Independent review of the shared integration found two material failures in unpatched Leaf
0.10.1. Controlled probes using the installed, unchanged JavaScript bundle and its actual mounted
hook reproduced both in Chromium 156.0.8078.79 on Linux. A coordinator rerun confirmed the same
results. These are package-level fixtures, not complete Taskman browser acceptance.

| Scenario | Observed result | Contract affected |
|---|---|---|
| Initialize `~~removed~~` followed by a blank line and `Other`; change only the second paragraph to `Other!` | `content_changed` emits `\~\~removed\~\~` followed by a blank line and `Other!`, with `dirty: true` | Unrelated edits must preserve formatting source; D6 accepts literal presentation only |
| Initialize `Original`; deliver public `set_content` for `Latest`; type `!`; press Ctrl+Z | Editor restores `Original` and emits it with `dirty: true`, rather than returning to `Latest` | Intentional replacement must establish the new document's undo baseline |

The first failure follows the renderer/serializer mismatch: `lib/leaf/markdown.ex:44` leaves
strikethrough literal, while `priv/static/assets/leaf.js:925` serializes text nodes through
`escapeInlineMarkdown`, whose `:1814` escapes every `~~`. The emitted source now represents literal
tildes; a corrected renderer cannot recover the original formatting. Taskman's ordinary dirty
snapshot path accepts this value for autosave. D7 explicitly accepts this additional temporary
formatting-source defect; D6 alone does not cover it.

The second failure comes from the shipped `set_content` handler calling `_historyReset` at
`priv/static/assets/leaf.js:18024` before replacing the visual DOM at `:18029`.
`_historyReset` immediately captures the current document at `:13457`, recording the predecessor
as the new undo baseline. Both `Latest!` and the subsequently restored `Original` are dirty
relative to `Latest`; Taskman's normal autosave can therefore overwrite the chosen replacement.
The initial Taskman adapter used the then-required public command; it did not call private
methods. D8 replaces that path with a remount.

To reproduce at the package boundary, mount the shipped `LeafHooks.Leaf` on synthetic hybrid
editor DOM with captured `pushEventTo` callbacks. For the first case, initialize visual HTML as
`<p>~~removed~~</p><p>Other</p>`, edit only the second paragraph, and dispatch its input event.
For the second case, initialize `<p>Original</p>`, deliver
`{action: "set_content", content: "Latest", html: "<p>Latest</p>"}` through the callback registered
for the public `leaf-command:<editor_id>` event, edit `Latest` to `Latest!`, dispatch input, and
send Ctrl+Z. Inspect emitted Markdown and dirty flags. The probe controls DOM events and does not
substitute the serializer. Full application typing, toolbar and persistence acceptance remain
required after correction.

D7 accepts the reproduced strikethrough conversion as a temporary bug, not as correct Markdown
normalization. Track it relative to this repository's registered Project as Task 83, with exact
marker `Source: Incanus3/taskman:task-description-markdown:strikethrough-preservation`. The follow-up
must preserve deliberately literal escaped tildes and verify unrelated edits plus mode and
save/reload round-trips. Keep the existing table-rendering correction and upstream contribution
commitment. D10–D11 are separate accepted source exceptions; D7 does not cover other conversions.

## Intentional replacement starts a fresh editor history

D8 selects a fresh Leaf identity for Use latest and clean incoming description replacement.
Mount it from the canonical replacement Markdown, retaining the current Project/Task context and
advancing the identity generation. Reset identity-local edit/capture state and reject old editor
messages. Do not invoke the faulty public `:set_content` path for these replacements.

The operator accepts discarding the replaced document and all earlier undo history. After
`Original` is replaced by `Latest`, typing `!` and pressing Undo once must return to `Latest`.
Normal autosave then persists that restored value. Ordinary saves, unrelated parent rerenders
and save acknowledgements must keep the current editor mounted, preserving text, caret and undo.
A remount may reset focus/selection only at intentional replacement.

Keeping `:set_content` would retain the reproduced incorrect undo baseline. A dependency history
patch could fix the underlying order, but would introduce initial fork/pin maintenance. Remounting
uses supported component lifecycle behavior and confines the workaround to Taskman's replacement
boundary. Verify with real browser keyboard typing and Undo, alongside stale-identity and exact
replacement-source checks. The underlying history correction remains agreed upstream follow-up;
after a reviewed upstream correction, removal of the workaround must retain these acceptance cases.

## Existing media and fenced-code indentation require a decision

Final integration review reproduced two more destructive conversions in unchanged Leaf 0.10.1.
D10–D11 accept these exact defects temporarily for the initial trial. They remain source-loss
bugs, separate from D6's presentation limitation and D7's strikethrough exception. Current
creation/autosave routing accepts these dirty snapshots; passing the ordinary browser matrix
does not establish preservation for these inputs.

| Existing Markdown, followed by a blank line and `Other` | Edit only `Other` to `Other!` | Consequence |
|---|---|---|
| `<video controls src="https://example.test/clip.mp4"></video>` | Emitted Markdown is exactly `Other!`, `dirty: true` | Existing media URL/source disappears |
| `<audio controls><source src="https://example.test/clip.ogg" type="audio/ogg"></audio>` | Emitted Markdown is exactly `Other!`, `dirty: true` | Existing audio/source URL disappears |
| `"```python\n    first()\n    second()\n```\n\nOther"` | Emits `"```python\nfirst()\n    second()\n```\n\nOther!"`, `dirty: true` | First code line loses its four-space indentation while the second retains it |

The installed renderer retains these safe media elements and both code indents. Its media
allow-list is in `deps/leaf/lib/leaf/sanitizer.ex:16–24`. The browser serializer has no media cases
and falls back to descendant text at `deps/leaf/priv/static/assets/leaf.js:1180–1181`; fenced code
uses `inner.trim()` at `:1026–1035`. This is lost source/meaning, not equivalent Markdown spelling.
A single indented `return result` line has the same code defect; a spaced inline-code comparison
retained its spaces. The finding concerns fenced code, not all code formatting.

Runtime evidence: published Leaf 0.10.1 with MDEx 0.14.6, unchanged mounted hook in Chromium
156.0.8078.79 on Linux. Independent review reproduced both media cases and fenced-code examples;
a coordinator rerun confirmed video and two equally indented code lines. These are package-boundary
reproductions plus inspected application persistence wiring, not additional full-app keyboard/DB
runs. Untouched descriptions remain protected by the existing clean-normalization guard; an
actual unrelated edit triggers the destructive conversion.

Renderer verification from a compiled test checkout:

```sh
elixir -pa '_build/test/lib/*/ebin' -e '
Application.ensure_all_started(:mdex)
IO.puts(Leaf.Markdown.markdown_to_html("<video controls src=\"https://example.test/clip.mp4\"></video>\n\nOther"))
IO.puts(Leaf.Markdown.markdown_to_html("```python\n    first()\n    second()\n```\n\nOther"))
'
```

For browser reproduction, mount the shipped `LeafHooks.Leaf` with this initial Markdown and the
renderer-produced HTML, capture its public `pushEventTo` output, change only the final paragraph,
and dispatch its input event. The video renderer produces
`<p><video controls="" src="https://example.test/clip.mp4"></video></p><p>Other</p>`; the code
renderer produces `<pre><code class="language-python">    first()\n    second()\n</code></pre><p>Other</p>`.
The emitted `content_changed` payloads match the table. No substitute serializer or private
editing method is needed.

Deferred corrections must prove unrelated-edit/save/reload retention, preserve
intentional edits and continue sanitizing all rendering boundaries. Do not attempt blind source
repair that cannot distinguish intended whitespace/tag changes from serializer loss. Leaf's public
`preserve_tags` is not an approved drop-in fix: it renders atomic component previews and exposes
raw-source editing, which can change D5's excluded embed/editing behavior. A dependency correction,
changed pin/fork, source-only fallback or further accepted defect still requires an explicit choice.

Track the accepted follow-ups relative to this repository's registered Project:

- Media source preservation: Task 84, exact marker
  `Source: Incanus3/taskman:task-description-markdown:media-preservation`.
- Fenced-code indentation: Task 85, exact marker
  `Source: Incanus3/taskman:task-description-markdown:code-indentation`.

Both are deferred and do not block the initial trial. Try to contribute focused corrections
upstream, then adopt reviewed pinned fixes. Strikethrough remains separately tracked as 83; the
full-document exit boundary is accepted without adding a new unload workflow.

## Typed fences are treated as literal text in hybrid mode

In Leaf 0.10.1, typing three opening backticks in an empty hybrid paragraph and pressing Enter
leaves paragraphs and emits escaped backticks with `dirty: true`, instead of starting a code
block. The installed unchanged hook reproduced this in Chromium using browser `insertText` and
`insertParagraph` editing commands; this is not independent real-keypress coverage. Ordinary text
passes through `escapeInlineMarkdown`, which escapes backticks before a fenced block exists.
The public toolbar Code block action creates `<pre>` and emits a proper fenced block; source mode
is another way to enter fences. This entry issue is distinct from the existing-code indentation
loss in D11. Deferred Task 86 owns its correction and real-keyboard/mode/save/reload regressions.

## Hybrid editing and toolbar controls work together

Use hybrid mode initially in Task creation and existing Task detail. Leave the description ready
to edit with no Edit button, Write/Preview tabs or global Save action on existing Tasks.

Provide visible controls for headings, bold, italic, strikethrough, inline and fenced code, links,
ordered/unordered lists, task-list items, quotes and tables, using Leaf's supported controls.
Source-only editing can remain available as an escape hatch. Raw HTML editing, media insertion,
uploads, custom component tags, wiki-link resolution and collaborative rooms are out of scope.
Do not deny links or images by filtering stored content merely to hide insertion affordances;
unsupported existing content must not be silently rewritten because a toolbar button is absent,
apart from the exact temporary raw-media defect accepted in D10.

Follow Taskman's theme and bounded editor panel. Controls must remain reachable at narrow widths,
with an accessible description label, keyboard-operable toolbar and visible focus. Recovery
renders the captured description read-only and retains the existing recovery actions. Read-only
covers native typing, source-text editing and hybrid checkbox interactions while preserving text
selection and Copy. Leaf 0.10.1 emits a boolean `data-readonly` attribute but its hook expects the
string `"true"`; Taskman's wrapper supplies an explicit string and normalizes it immediately before
the shipped public `mounted` and `updated` hook callbacks. The adapter preserves the remaining
callbacks and uses no private Leaf methods or dependency patch.

Paragraph menus may extend below the text area so every action remains reachable. The body
wrapper permits overlay overflow; the hybrid and source scroll surfaces retain rounded content
clipping. When the editor is narrower than the menu's normal width plus horizontal offset,
the menu aligns with the editor's left edge to avoid adding a horizontal scrollbar. This keeps
Leaf's existing menu handlers and desktop placement without a separate popup implementation.
The description boundary stops the paragraph handle's click from reaching LiveView's window
click-away handler: Leaf stops its mousedown, and LiveView navigation intercepts the preceding
Task-link click before clearing its outside-modal origin. Leaf still opens the menu on mouseup;
ordinary backdrop clicks retain their existing departure behavior.

## Manual resizing uses Leaf's supported fixed-height mode

The editor starts at `212px`, approximately ten plain-text lines at the current hybrid typography
(18px line height plus 32px vertical padding). Hybrid/visual and source views expose their native
vertical resize grip. Dragging sets a height retained through typing and ordinary server patches;
double-clicking the corner fits content with the initial height as the minimum. Each mode keeps
its own native size. This does not introduce shared sizing state or change recovery editability.

Leaf 0.10.1's `height="auto"` path disables manual resizing but still advertises its grip tooltip.
Its double-click handler parses `data-height` as a number, so `"auto"` produces NaN. The unchanged
hook reproduced a visible tooltip with `resize: none` and unchanged auto height. Automatic textarea
growth also resets height on input and mode changes; simply enabling CSS resize would overwrite
manual choices. Combining auto-growth capped around fifteen lines with manual overrides would
require additional sizing state across modes. D12 selects the operator's simpler fixed-height
fallback using Leaf's public configuration, with no private hook calls or dependency patch.

## Markdown remains the description value

Persist Markdown as the existing description string; no HTML column, schema migration or alternate
API representation. CLI/API values remain plain strings containing Markdown. Existing text is
interpreted as Markdown without a bulk rewrite. Opening, focusing or leaving an untouched
description must not write a normalized version back.

After actual edits, Markdown spelling may normalize, for example list spacing or emphasis markers.
That trade-off is acceptable only when meaning and formatting survive, except for D6's presentation
limitation and the exact source defects in D7 and D10–D11. Validate common literal characters,
URLs, nested lists, checkboxes, tables and code. Unsupported constructs and raw HTML must not
silently disappear on an unrelated edit outside those explicit temporary exceptions. Other
reproducible destructive conversions remain trial failures.

Render only as Markdown data. Never compile descriptions as HEEx or execute embedded components.
Use a sanitizing allow-list at each HTML rendering boundary, including editor initialization,
updates and read-only recovery; ignore client-supplied HTML for persistence. Leaf's MDEx dependency
does not by itself prove that every rendering path applies the required policy.

## Leaf is an editor, not a second persistence workflow

Add a small project-owned description component/adapter so Leaf-specific IDs, messages, rendering
options and hook registration are localized. Reuse existing Tasks context operations and
Autosave state rather than Leaf.Collab or a second save queue. Keep recovery and conflict controls
outside the editor's ignored DOM.

Relevant Leaf contracts are `{:leaf_changed, %{editor_id: ..., markdown: ..., html: ...}}`,
`send_update(Leaf, id: ..., action: :set_content, content: ...)`,
`action: :mark_saved`, and correlated `action: :flush, ref: ...` with
`{:leaf_flushed, %{ref: ..., markdown: ...}}`. Identity must distinguish Task instances and creation
drafts; reject messages from a replaced editor. The hidden form value is `task[description]`.

`Tasks.Description` owns one pending correlated capture, with purposes `{:submit, params}`,
`{:departure, destination}`, `{:route, params, uri}`, `{:reconcile, event}` or
`{:recover, previous_workspace}`. Creation merges captured Markdown over stale hidden form data;
Cancel discards its draft. Completion requires matching active editor/ref and current comment
metadata. Leaf's flushed reply has no dirty flag, so retain the corresponding ordinary change
metadata; ignore clean normalization and do not use `:mark_saved` to acknowledge persistence of
an older value while newer text may exist.

The existing CommentDeparture hook reads the composer's current DOM value in the capture phase
of the public Leaf flush event, including a composer hidden by the Sessions tab. It sends
`capture_task_departure_comment` with `editor_id`, `ref` and exact `draft` text, explicitly empty
when no composer exists. Accept only the first matching snapshot for the active capture. Ingest
a departure's initial comment once; never replay it after waiting or retrying over newer input.
When authority disappears, `Autosave.capture_description/3` records the draft without persistence
or scheduling; recovery resumes only after fresh authority checks.

Avoid Leaf's default additional 400 ms client debounce: the existing server save debounce remains
the persistence schedule. Even a zero-delay client timer does not prove navigation safety.
The implementation must capture the current editor Markdown before creation submit and before
Task departure, and establish ordering with route changes. Use supported Leaf synchronization and
flush APIs; do not assume a hidden input emits a form change event or that server flushing sees
unsent browser input. If supported APIs cannot meet this contract, report the concrete blocker
before maintaining a private fork or weakening the navigation guarantee.

Departure ordering also involves `ProjectLive` route application and the comment-departure
coordinator in `assets/js/project_live_hooks.js`; compose description flushing with that existing
confirmation flow rather than adding a competing navigation interceptor. Recovery currently
captures server-owned state and clears Editing immediately, so browser-side input must be captured
before the editor is replaced on external location loss as well as on voluntary departure.

A no-comment Back or Forward with no pending capture uses the existing server route-capture path,
so successful navigation preserves native history and its Forward destination. Actual comment
confirmation and active or timed-out description capture keep using the existing browser departure
guard. Repeated navigation cannot replace the pending capture's destination.

An unanswered capture times out after five seconds, retains the mounted editor and draft, and
requires explicit retry. For a native history route, restore the origin through the existing
failed-route behavior; that same-origin acknowledgement must retain the pending editor without
flushing older server text. Retry starts a fresh correlated capture with the current browser
comment snapshot and uses the existing departure continuation for the retained destination,
including its original query. This adds no history interceptor or persistence queue.

Unrelated form updates and save acknowledgements must preserve local text, selection and undo.
An incoming persisted description may replace a clean editor; a dirty description enters the
existing conflict flow. Use latest remounts the editor with the canonical value and fresh history
under D8; Keep mine sends the current local Markdown through the existing guarded update. Failed saves retain the draft.
Recovery captures the latest draft, blocks editing in the recovery surface, and resumes through
the existing authority checks.

## Verification and fallback criteria

Verify server-owned behavior with focused LiveView tests: creation submission and validation,
description autosave, empty values, stale editor messages, conflicts, external updates and recovery.
Use real-browser tests for editor typing/toolbar actions, selection/undo preservation, immediate
submit/departure and remount behavior; LiveView tests alone cannot establish these properties.

Browser acceptance must cover:

1. Type Markdown, leave the active line and return: rendering and markers behave as expected.
2. Select text and use toolbar controls without losing the selection or submitting the form.
3. Round-trip nested lists, checkboxes, tables, links, fenced code and literal Markdown punctuation
   through edit, save and reload; paste plain and formatted text and verify the result. Treat
   D6's literal table/strikethrough rendering and the exact source defects in D7 and D10–D11 as
   accepted temporary bugs; preserve all other source content. Retain the reproductions and
   follow-up verification requirements without patching the initial dependency.
4. Type a final character and immediately create, close, navigate to another Task or use Back;
   the value follows the existing creation/departure rules with no dropped character.
5. Apply unrelated updates, save acknowledgements and incoming changes without cursor resets,
   draft loss or loss of conflict choices. Type a final character immediately before external
   location loss, then capture and resume recovery without losing that character. Verify
   departures both with and without a pending comment draft.
6. Verify unsafe HTML and URL inputs do not execute in edit or recovery views.
7. Inspect actual desktop and 320 CSS px layouts; no page overflow or inaccessible toolbar actions.

### Chromium acceptance and limits

The completed integration passed `mix precommit` (1,529 tests, 3 existing executable skips and
87 separate browser exclusions). The editor regression gate passed 26 browser cases across the
editor, paragraph-menu, resize, capture and replacement suites; the preceding hook refactor passed
34 cases covering departure, editor/capture, comments and table behavior. Scoped independent reviews
approved the hook refactor, manual-resize correction and paragraph-menu corrections. The
[native resize test](../../test/taskman_web/live/project_live/description_resize_browser_test.exs)
uses actual WebDriver pointer drag and double-click actions in both hybrid and source views,
checks roughly ten initial lines, and retains the chosen size through typing and a server patch.
Both native grips were visible in inspected synthetic screenshots. The
[paragraph menu tests](../../test/taskman_web/live/project_live/description_block_menu_browser_test.exs)
enter through Task-list links, open the six-dot control with real pointer actions, hit-test Duplicate
and Delete beyond the short editor's bottom edge, verify exact saved Markdown, and reject horizontal
panel overflow at both measured viewport widths. Existing and new Task menus remain open after
pointer release, while genuine backdrop clicks still dismiss their modals. This is Chromium evidence;
other engines and real network reconnect behavior remain unverified.

Application checks on 2026-11-06 passed in headless Chromium and ChromeDriver `156.0.8078.79`
on Linux, with `DISPLAY` unset for native keyboard consistency. The
[editor browser suite](../../test/taskman_web/live/project_live/description_editor_browser_test.exs)
exercises the production Leaf hook and public toolbar, using WebDriver typing/selection, native
Clipboard API writes followed by Ctrl+V, and the application's persistence and navigation paths.

Verified behavior includes creation and immediate Task-link/Back departure with and without a
comment draft; delayed old-editor frame injection with current UI retention; byte-identical untouched source; ordinary
formatting/source-mode round-trips for nested lists, checkboxes, links and fenced code; native plain
and rich paste; caret/undo preservation; hostile HTML/URL sanitization; and sanitized, selectable
read-only recovery after location loss. Table source remains intact while the exact D7 strikethrough
escaping remains characterized. Both Close and same-LiveView Back timeout/retry retain the current
editor/draft; repeated pending history requests do not replace the destination, and retry preserves
its query. Capture failure feedback and Retry remain reachable separately from field status.

The [replacement keyboard test](../../test/taskman_web/live/project_live/description_replacement_browser_test.exs)
retains the canonical replacement → edit → Undo proof. The
[capture browser tests](../../test/taskman_web/live/project_live/description_capture_browser_test.exs)
retain immediate Close/inert-before-flush and current unsent comment coverage. The complete
[comment-departure suite](../../test/taskman_web/live/project_live/comment_departure_browser_test.exs)
and the existing narrow recovery case also pass with the integrated editor.

Measured CSS viewports are `1440 × 900` and `320 × 720`, with document scroll widths exactly
`1440` and `320` respectively. Actual menu/source-mode actions, mobile toolbar hit bounds and
Retry pointer activation pass; desktop, narrow editor/menu and narrow failure screenshots were
inspected. These results do not establish other browser engines. Stale editor/ref rejection is
proved by the server identity tests; the delayed-frame browser case currently lacks a deterministic
server-processing barrier and is not standalone proof of rejection after delivery.

The browser navigation guarantee established here is for transitions within the current LiveView,
including Back and Forward among its history entries. Full-document unload remains outside that
proof: a Back entry predating the mounted LiveView can unload the document and discard unsent
browser state. Existing comment-draft unload warnings remain best-effort; this integration does
not add a separate description unload workflow. D9 accepts this boundary for the initial trial;
it is separate from the formatting/source defects and does not establish a full-document guarantee.

Unanswered-flush checks may withhold public WebSocket frames while retaining the real editor,
serializer and flush hook. This establishes bounded timeout and retry behavior, not actual network
disconnection/reconnection or cross-browser correctness.

Run focused checks, assets build and `mix precommit` for implementation. Record actual browsers,
checks and unverified limits with the canonical evidence and tracker records. Do not claim
cross-browser correctness from Chromium or jsdom alone.

Apart from the exact temporary defects in D6–D7 and D10–D11, use ProseMark as the next evaluation candidate
if Leaf cannot preserve supported content, drops input, disrupts normal cursor/undo behavior,
or requires invasive private patches to meet these
contracts. Report findings before switching; building a ProseMark toolbar is a separate scope
decision. Do not implement two editors or a generalized editor framework preemptively.

## File ownership and continuation

Expected implementation boundaries: a new description integration under
`lib/taskman_web/components/`; `components/tasks/form.ex`; creation/editing/reconciliation/recovery
adapters under `live/project_live/`; an isolated JavaScript adapter and app.js registration;
app.css theme adjustments; Mix dependency/lock files; focused LiveView/browser tests. Add a shared
sanitized Markdown renderer only where an actual display consumer needs it.

At delivery, update current product documentation and mark superseded plain-text-control guidance
in the older specifications. Do not alter unrelated descriptions, comments or lifecycle behavior.

Initial delivery is complete. The following deferred Tasks are relative to this repository's
registered Project; their exact source markers identify the retained follow-up owners:

| Task | Deferred correction | Exact source marker |
|---|---|---|
| 83 | Table/strikethrough Markdown rendering and strikethrough source preservation | `Source: Incanus3/taskman:task-description-markdown:strikethrough-preservation` |
| 84 | Existing audio/video source preservation | `Source: Incanus3/taskman:task-description-markdown:media-preservation` |
| 85 | First-line fenced-code indentation preservation | `Source: Incanus3/taskman:task-description-markdown:code-indentation` |
| 86 | Typed opening code fences in hybrid mode | `Source: Incanus3/taskman:task-description-markdown:fence-typing` |
| 87 | Upstream replacement-history baseline correction | `Source: Incanus3/taskman:task-description-markdown:replacement-history` |

Prepare focused regressions, try to contribute fixes upstream, and adopt reviewed pinned
corrections before removing exceptions or the remount workaround. Preserve unrelated-edit,
mode and save/reload checks plus real-keyboard fence entry and replacement → edit → Undo cases.
Do not switch editors or change the dependency pin without a separate decision. Publication,
merge and deployment remain separate actions from initial-delivery acceptance.
