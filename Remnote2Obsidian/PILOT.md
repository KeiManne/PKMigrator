# Bounded RemNote export pilot

`remnote_export.py` is a reproducible proof for reviewing a few selected RemNote
documents and their portals in Obsidian. It is intentionally not a whole-vault
importer. The work remains covered by PKMigrator's root MIT license and attribution.

The pilot reads `rem.json` directly from a `.rem` ZIP (or accepts that JSON file),
indexes records by ID, derives ordinary child order from `parent` plus `f`, and
writes one Markdown file per explicitly requested non-portal root. Supported
ordinary portals become physical nested bullet copies. Every copy has its own
stable block anchor while retaining the original Rem ID in `manifest.json`.

## Run a pilot

Python 3.9 or newer is sufficient; there are no third-party dependencies.

```sh
python3 Remnote2Obsidian/remnote_export.py \
  --input /path/to/export.rem \
  --output /path/to/isolated-preview \
  --roots example-document-id,another-document-id \
  --max-occurrences 500 \
  --max-depth 40
```

Roots are mandatory, deduplicated, and never inferred. The default hard cap is
2,000 rendered occurrences across the run, including portal copies. The default
depth cap is 50. Reaching either cap leaves a reviewable partial draft, records an
error issue, marks the manifest `incomplete`, and makes the command exit with
status 2. A portal ID cannot be used as a root; it is rejected cleanly in the
manifest because it has no canonical document body.

The output directory should be disposable staging. The pilot writes into `notes/`,
creates `manifest.json`, and only describes planned asset paths. It does not
download media or modify an existing vault.

## Configuration file

`--config` accepts JSON. Command-line roots and limit options take precedence.
This complete synthetic example also documents the supported schema:

```json
{
  "roots": ["example-document-id"],
  "max_occurrences": 500,
  "max_depth": 40,
  "portal_snapshots": {
    "example-search-portal-id": {
      "evidence": "Manually checked in the source application on YYYY-MM-DD",
      "members": ["first-result-id", "second-result-id"]
    }
  },
  "visibility_overrides": {
    "example-portal-id": {
      "evidence": "Manually checked in the source application on YYYY-MM-DD",
      "states": {
        "example-descendant-id": "hidden",
        "another-descendant-id": "visible"
      }
    }
  }
}
```

A snapshot must include a non-empty `evidence` value and an ordered `members`
array. It replaces file-derived membership for that one portal. This is the only
way the pilot renders a search/automatic portal: exported `searchResults` are
treated as cache evidence, not as an authoritative result set.

Visibility overrides require non-empty `evidence`; their `states` values accept
booleans or `visible`/`hidden` (`show`/`hide` are also accepted). Use them only for
a case whose state has been checked. In the export
contract currently supported without an override, `ph.<id>.h == "h"` means that
ID is explicitly hidden in that portal and is omitted from that copy. Other `ph`
states are unresolved and make the pilot incomplete. `pe` and source `ic` values
describe collapse/expansion; they never suppress descendants.

## Portal contract and intentional limits

`type: 6` marks a portal record; the verified search enum is specifically
`portalType: 4`. Other explicit enum values are unsupported rather than guessed.
An ordinary portal's `pd` map supplies directly
included roots: `.d` string values are sorted as fractional order keys; `.d: true`
also includes a root; false/null entries are not included. A boolean root has no
evidenced order relative to any other root in this export. The pilot retains JSON
order for inspection but reports `portal_boolean_order_uncertain` as an error
unless an ordered snapshot is configured.

Cycles stop only the recursive branch that encountered the repeated source ID.
The output contains a link to the source's canonical pilot block when one exists,
and the issue includes the traversal path. The same source remains free to appear
through a different portal or path.

The rich-text renderer supports strings, combined bold/italic marks, highlights,
inline code, fenced code (`i: "m", code: true` with an optional `language`),
inline or block LaTeX (`i: "x"` with an optional `block`), ordinary links,
references, images, and front/back text rendered with an em dash. Those shapes
were checked against the supplied raw export rather than inferred from the old
converter. Flashcard scheduling and review behavior are not emitted. PDF binaries
are not copied. Unknown rich-text objects retain any recoverable `text` and
produce an issue.

## Manifest contract

Stable integration fields are:

```json
{
  "status": "complete_for_requested_pilot",
  "source_map": {
    "example-rem-id": {
      "rem_id": "example-rem-id",
      "canonical": {
        "file": "notes/Example--0123456789.md",
        "anchor": "rem-0123456789abcdef"
      },
      "canonical_origin": {
        "document_id": "example-document-id",
        "document_title": "Example",
        "ownership_path": ["example-document-id", "example-rem-id"]
      },
      "occurrences": [
        {
          "file": "notes/Example--0123456789.md",
          "anchor": "rem-0123456789abcdef",
          "kind": "canonical",
          "portal_id": null,
          "portal_context": null,
          "path": ["example-document-id", "example-rem-id"],
          "visible": true,
          "content_sha256": "..."
        }
      ],
      "plain_original_text": "Readable source text",
      "timestamps": {"createdAt": 0, "m": 0, "u": 0},
      "original_parent": "example-document-id"
    }
  },
  "assets": {
    "https://assets.example.test/image.png": {
      "relative_path": "Attachments/RemNote/0123456789abcdef01234567.png",
      "occurrences": [
        {"rem_id": "example-rem-id", "file": "notes/Example--0123456789.md", "portal_id": null}
      ]
    }
  },
  "issues": []
}
```

`canonical` is null when the original ownership location lies outside the selected
pilot roots. `canonical_origin` still records its source hierarchy, without
inventing an output location. `occurrences` contains every rendered canonical and
portal appearance, allowing retrieval to index original text once and map a hit
back to all readable copies. Asset URLs map deterministically to local relative
paths; a separate validated downloader can populate those paths and report media
failures.

`status` only means that every required case encountered in the requested bounded
pilot was supported. It is not a whole-export completeness claim. Search views,
ambiguous visibility/order, missing IDs, invalid records, and traversal limits are
never silently accepted.

The manifest's `configuration` preserves the complete applied portal snapshots
and visibility overrides, including their evidence strings, so an accepted run can
be reproduced and audited. `export.knowledgebase_identity_status` is `known` only
when the raw export supplies a non-empty `knowledgebaseId`; otherwise it explicitly
reports `unknown` rather than inventing an identity.

## Media and canonical search support

`pilot_support.py` performs the bounded follow-up steps against a completed pilot
manifest. Download media into the isolated reading output, then validate every
declared local asset:

```sh
python3 Remnote2Obsidian/pilot_support.py download-assets \
  --manifest /tmp/remnote-reading/manifest.json \
  --output-root /tmp/remnote-reading \
  --report /tmp/remnote-media-download.json \
  --max-assets 12 \
  --max-bytes 12582912 \
  --timeout 10 \
  --retries 1

python3 Remnote2Obsidian/pilot_support.py validate-assets \
  --manifest /tmp/remnote-reading/manifest.json \
  --output-root /tmp/remnote-reading \
  --report /tmp/remnote-media-validation.json \
  --max-bytes 12582912
```

The downloader accepts HTTP(S) only, verifies both image content type and raster
signature, enforces the per-asset byte and request bounds, checks the deterministic
URL-hash filename, and refuses any destination that escapes the output root. Every
failure is recorded in the JSON report; an incomplete result exits with status 2.

Build the search projection in a separate directory outside the reading output:

```sh
python3 Remnote2Obsidian/pilot_support.py build-records \
  --manifest /tmp/remnote-reading/manifest.json \
  --output /tmp/remnote-search-projection \
  --reading-vault-root /tmp/remnote-reading
```

The projection contains exactly one Markdown record per original Rem ID. Canonical
and portal copies remain appearances in that record's metadata, with a readable
source citation and visible document membership for scoped gather. Index the
projection, not the expanded reading copies, so repeated portal appearances do not
become independent evidence. The command refuses incomplete converter manifests,
non-empty projection destinations, and destinations inside the reading vault.

The Python helper `propagate_privacy()` calculates which duplicate appearances and
derived fixtures depend on a marked-private source. It is an advisory test and
review aid only; it does not implement production retrieval filtering, access
control, or later privacy-change invalidation.

## Tests

All fixtures are synthetic and contain no private export content or IDs:

```sh
cd Remnote2Obsidian
python3 -m unittest -v test_remnote_export.py test_pilot_support.py
```

The suite covers repeated, hidden, collapsed, cyclic, search-snapshot and bounded
portals, as well as rich formatting, escaping, safe paths, stable anchors, archive
loading, asset mapping, and structured failures. It also covers canonical search
deduplication, document-scoped visible membership, advisory privacy propagation,
bounded media validation, unsafe destinations, and explicit missing assets.
