# Staged full RemNote export

The full mode is a reviewable staging converter. It combines the structured
`.rem` archive with RemNote's native Markdown ZIP: the native archive supplies
document and folder path evidence, while the structured archive supplies rich
text, source identity, hierarchy, portals, timestamps, and asset references.
It does not infer a complete migration when required evidence is missing.

Python 3.9 or newer is sufficient; the converter uses only the standard
library.

```sh
python3 Remnote2Obsidian/remnote_export.py \
  --input /path/to/export.rem \
  --markdown-export /path/to/native-markdown.zip \
  --output /path/to/new-reading-vault \
  --config /path/to/full-config.json \
  --full
```

The output is a staging vault. Keep it separate from an existing Obsidian
vault until the manifest reports `complete_full_migration` and its downstream
media validation also passes. An incomplete run exits with status 2 but leaves
the draft and ledger available for review.

## Boundary and path policy

Every native Markdown file must match exactly one structured record through
its normalized ownership-title path. A timestamp may disambiguate otherwise
identical empty native files, but an ambiguous non-empty match fails before
conversion. The raw `n` field is not treated as a document flag. A live SDK
classification snapshot can cross-check document and folder semantics; it
does not silently create new boundaries.

Output lives below `Sources/RemNote/`. By default, every path component keeps a readable
normalized title plus a deterministic Rem-ID hash suffix. A reviewed
`document_plan` may replace those destinations with unique safe paths. Characters that
break Obsidian wikilink or anchor parsing, including `#`, `[`, `]`, and `^`, are
replaced. Components are bounded by UTF-8 byte length. The original title is
retained in note content and source metadata.

Reruns require either an empty output or a prior converter manifest. Before
writing, the converter verifies every existing manifest-owned note against its
recorded hash. It refuses to overwrite a modified generated note. After a
successful rewrite it removes only unchanged Markdown files that the prior
manifest owned and the new run no longer produces; unrelated files are left
alone.

## Private evidence configuration

Configuration can retain reviewed IDs and evidence outside the repository:

```json
{
  "mode": "full",
  "knowledgebase_id": "known-knowledge-base-id",
  "split_candidates": {
    "reviewed-document-id": "Reason this existing document boundary needs later structural review."
  },
  "exclude_subtree_roots": {
    "excluded-branch-id": "User-approved scope reason."
  },
  "exclude_source_ids": {
    "excluded-source-id": "User-approved exact-source reason."
  },
  "document_plan": {
    "retained-root-or-section-id": {
      "path": "Sources/RemNote/Reviewed topic.md",
      "evidence": "Reviewed structural grouping or split decision."
    }
  },
  "reference_metadata": {
    "reviewed_root_ids": ["reviewed-generated-link-index-id"],
    "link_type_id": "reviewed-link-type-id",
    "evidence": "Reviewed exact typed-link metadata fields."
  },
  "asset_omissions": {
    "https://example.invalid/unrecoverable.png": "Reviewed upstream failure; retain an explicit marker and original URL."
  },
  "snapshot_contract": "/private/path/remnote-migration-snapshot.json",
  "portal_evidence": {
    "evidence": "Explicit user-approved portal rendering policy.",
    "source_comparison_receipt": "/optional/private/reviewed-drift-report.json"
  }
}
```

`split_candidates` only records explicitly reviewed document IDs. Full mode
never chooses extra splits from a guessed size threshold. The manifest reports
the candidate's existing descendant document boundaries and file size so a
later structural migration can be reviewed independently.

`exclude_subtree_roots` follows only the raw ownership-child graph. It never
follows portal membership to unrelated sources. `exclude_source_ids` removes
only the named identity. Both policies apply to canonical notes, portal copies,
references, assets, the source map, and downstream retrieval. Every excluded
record remains in the ledger with its rule and reason. An exact exclusion does
not implicitly exclude children; a retained child without a rendered owner is
an error.

`document_plan` is an optional full replacement for the native output-boundary
map. Every key must be an exported non-portal record, and every entry needs a
reviewed evidence string and a unique safe Markdown path below
`Sources/RemNote/`. Omit a nested native boundary to merge it into a retained
ancestor document; add a reviewed section ID to split it into its own file.
The original native-boundary evidence remains unchanged in the manifest.
Omitting `document_plan` preserves the native-boundary layout.

`knowledgebase_id` is required when a snapshot is supplied and the `.rem`
payload itself has no knowledge-base ID. It is copied into the private
manifest; do not put a personal ID in public configuration or tests.

`reference_metadata` materializes only references under the named reviewed
roots whose exact typed-link metadata contains one unambiguous safe HTTP(S)
URL. The external link is rendered before the generated metadata subtree is
excluded. The manifest verifies every retained owner/target edge, while portal
copies may create additional appearances. Missing or ambiguous retained edges
are errors.

`asset_omissions` is an explicit exception map for reviewed unrecoverable image
URLs. A matching image renders as
`[Image unavailable: label](original-url)`, never as a dangling local embed.
The manifest records the reason and every affected occurrence. Unsafe or unused
exception entries fail closed.

## Snapshot contract

`--snapshot-contract` accepts `remnote-migration-snapshot/v1`. Intake requires
a known knowledge-base ID that exactly matches the snapshot. The converter
recomputes a public `pkmigrator-source-comparison/v1` receipt on
every portal-evidenced run. It requires exact record IDs, parents, created
timestamps, versioned rich-text fingerprints, complete child membership, and
matching order wherever raw fractional order is unambiguous. Complete,
parent-consistent SDK `child_ids` resolve tied or missing raw order. This strict
comparison runs even when the immutable capture's original calibration flags
are false. An optional private reviewed receipt is checked against the exact
export and snapshot hashes and recorded as supporting provenance; it is never
the authority for admission. A structurally changed or different knowledge
base is rejected rather than combined with older exported note bodies.

Legacy pilot runs may still read these projected fields:

```json
{
  "converter_projection": {
    "portal_snapshots": {
      "portal-id": {
        "evidence": "Verified capture method and time",
        "members": ["ordered-member-id"]
      }
    },
    "visibility_overrides": {
      "portal-id": {
        "evidence": "Verified capture method and time",
        "states": {"member-id": "hidden"}
      }
    }
  }
}
```

The full contract is copied verbatim to `snapshot-contract.json` and identified
by SHA-256 in the manifest. Portal membership, portal-local visibility, and
collapse remain separate. Collapse never suppresses content. Cached
`searchResults` are never accepted as authoritative view membership.

Full scoped runs derive `pkmigrator-portal-evidence/v1` plans from the immutable
snapshot for exactly the portals reachable under the reviewed scope and
document plan. The artifact binds the snapshot payload, raw export, selected
scope fields, strict source-comparison receipt, raw ID set, effective excluded
ID set, and admitted portal ID set. Its self-digest and all bindings are
validated immediately before rendering.

Ordinary portal plans copy the full eligible source subtree, including
collapsed descendants, while omitting explicitly hidden branches. Automatic
search and backlink plans follow the explicit match-only policy: render the
matched bullet and its canonical source path/link without recursively copying
an external source subtree. A flat table result owned by that same portal also
preserves its authored row/cell subtree in calibrated child order. Nested
portal boundaries and per-path cycles remain guarded. Every admitted portal
must have a complete plan with zero adapter diagnostics; cached
`searchResults` are never treated as membership evidence.

Live `document_and_folder` classifications are checked against every original
native Markdown boundary. The explicit full `document_plan` may preserve a
reviewed native boundary despite an SDK false result or merge an SDK-only
boundary into its retained ancestor; the manifest retains the disagreement as
a warning. Any conflict without a reviewed scope/output disposition remains an
error.
A record is excluded as a system definition only when that same record has a
positive result from the SDK powerup, powerup enum,
powerup property-list item, powerup slot, or powerup property methods. A title
or ancestor classification is never inherited. Positive system evidence takes
precedence even when the native export produced a tiny Markdown file for that
record. Its children remain independent retained records and must still have a
rendered owner.

## Output and completeness ledger

`manifest.json` uses schema version 2. Stable integration fields include
`export`, `files`, `source_map`, `assets`, `issues`, and `record_ledger`.
`source_map` keeps one canonical source identity and every rendered canonical,
document-link, and portal-copy appearance. Asset source strings map to
deterministic `Attachments/RemNote/` paths; media retrieval and validation are
separate stages.

The record ledger accounts for every indexed raw record with a disposition and
reason. Records outside evidenced native document boundaries remain
`unresolved_outside_document_boundary` until source/system classification is
supported by explicit evidence. Native Markdown absence alone is not treated
as proof that a record is disposable system metadata. References to such
records keep their readable exported label as plain text without inventing a
canonical link. References to IDs absent from the export retain their ID
placeholder and an unresolved issue. If the export includes a deleted-reference
label, that label is preserved alongside the ID in both Markdown and searchable
source text.

These contracts are emitted by converter version `0.4.0-evidenced-staging`.
Downstream tools should continue to key compatibility from the manifest schema
and status fields, while retaining the converter version for audit provenance.

Unrendered records owned below a portal remain `unresolved_portal_descendant`,
including empty wrappers. Portal ancestry does not prove that a record is
disposable query metadata: table rows and cells can contain source text there.
Explicit automatic match-only omissions and explicitly hidden portal branches
receive separate ledger dispositions. Empty structural wrappers require
positive evidence before omission.

`complete_full_migration` is emitted only when the converter has zero error
issues. `incomplete_full_migration` covers unresolved automatic views, unknown
portal visibility or order, missing targets, unclassified outside-boundary
records, traversal limits, unsafe media sources, incomplete snapshot evidence,
and any unexplained missing output. Downstream projection must reject the
incomplete status.

PDF binaries and flashcard scheduling/review machinery are omitted. Readable
PDF container text, annotations, and ordinary front/back question-and-answer
text remain in the staged notes and manifest.

## Tests

All committed fixtures are synthetic:

```sh
cd Remnote2Obsidian
python3 -m unittest -v \
  test_remnote_export.py test_reference_metadata.py \
  test_portal_evidence.py test_pilot_support.py
```

The converter tests cover full boundary reconciliation, Obsidian-safe paths,
scope propagation through portal copies and assets, reviewed document-plan
splits and merges, exact system-definition precedence, retained-child
accounting, rich text, PDF annotations, reference resolution, snapshot identity
and drift rejection, and safe manifest-owned reruns in addition to the bounded
portal pilot cases.
