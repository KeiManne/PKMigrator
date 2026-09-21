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
`http://localhost:8080`, and install the development plugin. Open the Omnibar with
**Ctrl/Cmd+K**, then run the exact command **Open PKMigrator snapshot**. This opens the exporter in
a popup; it remains available as the **PKMigrator Read-Only Snapshot** right-sidebar widget too.
The plugin does not open or read the knowledge base merely because it was activated. Use a
signed-in normal browser window or the RemNote desktop app that can reach localhost. If an embedded
browser blocks localhost, switch to one of those supported RemNote clients; do not route the
development server through a public host.

RemNote's official [quick-start guide](https://plugins.remnote.com/getting-started/quick_start_guide)
documents **Develop from localhost** as the development-install route. `npm run build` also creates
`PluginZip.zip`, but **Upload plugin** submits that ZIP for RemNote review and marketplace hosting;
it is not a manual local-install control. Even an
[unlisted plugin](https://plugins.remnote.com/advanced/unlisted_plugins) must be submitted and
approved. Do not use **Upload plugin** merely to run this private exporter.

Use **Calibration** first. Both modes take one bulk read-only inventory of the knowledge base.
Calibration restricts the expensive portal-context and per-Rem method probes to the selected portal
IDs. Compare ordinary and search result membership/order and hidden/included/none states with the
live UI, then select only the validation checkboxes that the comparison establishes. **Complete
migration** probes every portal, gives selected portals priority, and exposes both probe limits in
the UI. Progress reports portal count and probes used. **Cancel** stops after the pending SDK call
and prevents download.

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
  "portalProbeSeedsByPortal": {"portal-id": ["raw-pd-ph-pe-candidate-id"]},
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

`portalProbeSeedsByPortal` adds bounded raw-export candidates to a portal's live method probes. It
is intended for IDs found in raw `pd`, `ph`, or `pe`, including `d:false` entries that are probe
candidates rather than members. Seeds are tried before discovered candidates and may be resolved
with read-only `findOne()`. They never establish a complete candidate universe. Resolution and
missing IDs are recorded under `portals.<id>.visibility.probe_seeds`. A seed absent from SDK
direct-member/context discovery is listed in `supplemental_ids` and forces visibility completeness
false even when its diagnostic getter call succeeds.

Each portal contains direct members in SDK return order, the document/portal context array, the
typed-but-undocumented hidden getter's runtime-validated `hidden | included | none` result,
collapse, ordinary/visible positions, and search/backlink metadata. `none` means no local explicit
override; it does not prove global visibility through hidden ancestors. Collapse never hides
content in converter projection.

`converter_projection.portal_snapshots` is emitted only after the operator validates return order
for that portal type. `visibility_overrides` requires complete probes, valid runtime values, and
operator validation of getter semantics. It projects `hidden` as `hidden` and `included` as
`visible`; `none` stays in the full contract. Ordinary portal membership projection is independent
of optional context, collapse, and position diagnostics. Search portal projection additionally
requires successful context discovery because a failed call cannot establish that nested result
contexts are absent.

SDK Rem objects returned by portal methods are retained in `runtime_returned_records`, including
objects absent from the bulk inventory. Runtime response objects take precedence over same-ID bulk
objects for context probes. Lookup failures are recorded and do not abort the remaining capture.

Search portals can contain nested result-context portals whose local visibility differs from the
outer search context. The exporter detects type-6 objects in direct members/context arrays and bulk
portal children. It records IDs and discovery routes in `nested_contexts`. When any are detected,
the outer portal is marked `detected-unresolved`, its visibility is incomplete, its automatic-view
results are labelled `direct-members-unmapped-nested-contexts`, and no flat outer membership or
visibility projection is emitted. Nested contexts can be probed as separately selected portal IDs;
the snapshot does not infer a rendered source-result mapping before live calibration.
If `allRemInDocumentOrPortal()` fails for a search portal, `nested_contexts.status` is
`discovery-incomplete`, the direct-member result interpretation remains diagnostic, and flat
membership and visibility projections are suppressed. The same context-call failure remains an
optional diagnostic failure for ordinary portals.

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
