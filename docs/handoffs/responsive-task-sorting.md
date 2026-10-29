# Responsive task sorting

Status: active. Updated: 2026-10-29.

Resume: `$resume responsive-task-sorting`

The Task table header now appears from 1280 CSS pixels. Below that, a Sort button beside
Statuses controls the existing listing sort, with the same fields, initial directions and
repeated-selection reversal as the column headers. Both toolbar controls use icons only below
768 pixels. Location is offered when child Lists are included. The canonical behavior is in
[the MVP specification](../product/mvp-spec.md).

Implementation and scoped independent review are complete. The review's Escape-focus gap was
reproduced with a failing browser test and corrected: Escape from a focused option now restores
focus to Sort. The initial menu regression also failed without the feature.

Verification: all 19 Task table LiveView tests and all 5 Task table browser tests passed. The
browser test measured 320, 390, 639, 640, 767, 768, 1279 and 1280 CSS pixels, including narrow-screen
sorting, focus restoration and no horizontal overflow. The final `mix precommit --max-cases 8`
passed with 1,394 tests, 3 skipped and 53 browser tests excluded.

## Remaining sequence

Operator visual acceptance and explicit completion confirmation remain pending. Pushing
or merging requires separate authorization.

## Rulings

None. The menu reuses existing listing sorting semantics and the requested hierarchy-menu
presentation; no new sorting policy or persistence was introduced.
