# PKMigrator RemNote snapshot

This RemNote plugin uses the official SDK to download local JSON evidence needed for migration. It
does nothing until **Download read-only snapshot** is pressed. It has no mutation or upload path.

## Run from localhost

```sh
cd Remnote2Obsidian/remnote-snapshot
npm install
npm run check-types
npm test
npm run dev
```

In RemNote, open **Settings → Plugins → Build → Develop from localhost**, enter
`http://localhost:8080`, install the development plugin, and open the
**PKMigrator Read-Only Snapshot** sidebar widget. `npm run build` also produces `PluginZip.zip` for
a local manual install. Localhost avoids publishing or uploading the plugin.

Use **Calibration** first. It reads only the portal IDs in the settings file. Compare ordinary and
search result membership/order and hidden/included/none states with the live UI, then select only
the validation checkboxes that the comparison establishes. **Complete migration** reads every
portal, gives selected portals priority, and exposes both probe limits in the UI. Progress reports
portal count and probes used. **Cancel** stops after the pending SDK call and prevents download.

The single file input accepts the converter's `document-candidates.json` (`records[].rem_id`) or:

```json
{
  "schema_version": "remnote-migration-capture-settings/v1",
  "mode": "calibration",
  "priorityPortalIds": ["portal-id"],
  "classificationRecordIds": ["document-candidate-id"],
  "systemDefinitionRecordIds": ["possible-system-definition-id"],
  "detailRecordIds": ["rich-record-id"],
  "expectedHiddenByPortal": {"portal-id": ["known-hidden-id"]},
  "maxContextProbes": 10000,
  "maxProbesPerPortal": 10000,
  "ordinaryOrderValidated": false,
  "searchOrderValidated": false,
  "visibilitySemanticsValidated": false,
  "richTextFingerprintCalibrated": false,
  "childOrderCalibrated": false
}
```

Arrays can be empty. Calibration requires at least one portal ID. Complete-mode defaults are
250,000 total probes and 100,000 per portal; both are configurable positive safe integers. A limit
never silently drops evidence: affected components and migration completeness become false.

## Contract

The result uses `schema_version: "remnote-migration-snapshot/v1"`. `capture.complete` is the
compatibility alias for `capture.migration_complete`. `capture.diagnostics_complete` separately
covers portal context, collapse, and position probes; those display diagnostics do not invalidate
complete membership/order/visibility evidence.

Each portal contains direct members in SDK return order, the document/portal context array, the
typed-but-undocumented hidden getter's runtime-validated `hidden | included | none` result,
collapse, ordinary/visible positions, and search/backlink metadata. `none` means no local explicit
override; it does not prove global visibility through hidden ancestors. Collapse never hides
content in converter projection.

`converter_projection.portal_snapshots` is emitted only after the operator validates return order
for that portal type. `visibility_overrides` requires complete probes, valid runtime values, and
operator validation of getter semantics. It projects `hidden` as `hidden` and `included` as
`visible`; `none` stays in the full contract. Membership projection is independent of visibility
completeness.

The exporter records the knowledge-base ID at start, every 25 portals, and end. A switch marks the
capture incomplete and removes all converter projections. The SDK does not provide a transaction;
timestamps, hierarchy, record count, selected rich text, and fingerprints provide drift evidence.

`classifications.document_and_folder` calls `isDocument()` and `isFolder()` for top-level records
and explicit candidates. `classifications.system_definition` calls all five powerup predicates for
explicit candidates only. A child is never inferred to be a system definition from its ancestor or
title.

Each record carries `export_comparable_rich_text_fingerprint`. The algorithm canonicalizes
`[text ?? null, backText ?? null]` by recursively sorting object keys with explicit JavaScript
UTF-16 code-unit order, preserving array order, JSON-serializing, and applying FNV-1a 64 over
JavaScript UTF-16 code units. Compare against raw `[key ?? null, value ?? null]` only when
`capture.export_comparison.calibrated_equivalent` is true after a real sample comparison.
Likewise, compare each ordered `child_ids` array with raw sibling order (`f`, with raw ordinal as
tie-break) only when `capture.export_comparison.child_order_calibrated` is true. A changed sibling
order is migration drift even when IDs and parents are unchanged.
