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

Output lives below `Sources/RemNote/`. Every path component keeps a readable
normalized title plus a deterministic Rem-ID hash suffix. Characters that
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
  "snapshot_contract": "/private/path/remnote-migration-snapshot.json"
}
```

`split_candidates` only records explicitly reviewed document IDs. Full mode
never chooses extra splits from a guessed size threshold. The manifest reports
the candidate's existing descendant document boundaries and file size so a
later structural migration can be reviewed independently.

`knowledgebase_id` is required when a snapshot is supplied and the `.rem`
payload itself has no knowledge-base ID. It is copied into the private
manifest; do not put a personal ID in public configuration or tests.

## Snapshot contract

`--snapshot-contract` accepts `remnote-migration-snapshot/v1`. Intake requires
a known knowledge-base ID that exactly matches the snapshot. Record IDs and
parent IDs are always strict drift gates. Ordered child IDs become a strict
gate after SDK child order is calibrated against the export's parent plus
fractional-order traversal; an uncalibrated order blocks admission. Rich-text
fingerprints use canonical object-key order, preserved array order, and a
versioned FNV-1a calculation over JavaScript UTF-16 code units. They become a
hard content drift gate only after
the snapshot says raw `key`/`value` and SDK `text`/`backText` equivalence was
calibrated. An uncalibrated comparison leaves an error issue, so the run cannot
claim completion. A structurally changed or different knowledge base is
rejected rather than combined with older exported note bodies.

The converter reads only these projected fields:

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

Snapshot `capture.mode` must be `complete`, the knowledge-base identity must be
stable through the capture, and expected, processed, and supplied portal sets
must exactly cover every portal in the export. A calibration or selected-portal
capture may still supply useful evidence for an inspectable draft, but
`snapshot_scope_incomplete` prevents admission.

Live `document_and_folder` classifications are checked against every native
Markdown boundary. Missing, conflicting, or SDK-only boundaries remain errors.
Records outside those boundaries are excluded as system definitions only when
that same record has a positive result from the SDK powerup, powerup enum,
powerup property-list item, powerup slot, or powerup property methods. A title
or ancestor classification is never inherited.

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
placeholder and an unresolved issue.

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
python3 -m unittest -v test_remnote_export.py test_pilot_support.py
```

The converter tests cover full boundary reconciliation, Obsidian-safe paths,
record accounting, rich text, PDF annotations, reference resolution, snapshot
identity and drift rejection, and safe manifest-owned reruns in addition to the
bounded portal pilot cases.
