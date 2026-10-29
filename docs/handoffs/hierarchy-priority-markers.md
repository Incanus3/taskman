# Hierarchy priority markers

Status: active. Updated: 2026-10-29. Resume: `$resume hierarchy-priority-markers`.

## Outcome and authority

Every Task detail hierarchy row shows ID, status, then priority flag and label, including muted
None. The operator chose consistent metadata and direct implementation without a separate design.
The [layout specification](../specs/2026-10-28-task-detail-layout-design.md#titles-and-metadata-stay-readable)
and [product contract](../product/mvp-spec.md) own the behavior.

## Current checkpoint

Implementation includes shared table/hierarchy labels and colors, responsive metadata wrapping,
and priority-only external-update refreshes. Scoped independent source review found a refresh-filter
omission; the correction was independently rechecked. The regression test failed before the fix
and passed afterward. Chromium inspection at 1440 × 900 and 320 × 720 verified four-level
readability and no hierarchy/page horizontal overflow. Other engines, actual browser zoom and
assistive-technology sessions remain untested.

Final verification: `mix precommit --max-cases 8` passed with 1,381 tests, 3 existing skips
and 51 browser exclusions. The separate temporary Chromium check passed at both viewport sizes.
The asset build, Markdown file-link check, whitespace check and scoped privacy audit passed.

## Remaining sequence

1. Operator inspects the result and confirms completion or requests adjustments.
2. After confirmation, retire this handoff and update its index. Publication requires separate authorization.
