# RemNote to Obsidian

Convert a RemNote knowledge base into organised Markdown files for Obsidian,
with portal content copied into the notes where it appears.

This fork focuses on a local, one-time migration. It preserves source wording,
uses document and topic boundaries for files, and records each original Rem's
identity so duplicated portal passages remain traceable to the same source.

## What it does

- Combines a structured `.rem` export with RemNote's native Markdown ZIP.
- Copies ordinary portal bullets and eligible descendants, including collapsed
  content, while respecting explicitly hidden bullets.
- Captures live search, tag and backlink results using a read-only local plugin.
  Automatic lists can render each matched bullet with a link to its source.
- Supports reviewed document merges, section splits and content exclusions.
- Converts images, highlights, math, code and ordinary question/answer text.
  Images are downloaded separately and validated before use.
- Produces a source map, per-record accounting and explicit unresolved issues.
  A partial conversion is marked incomplete rather than silently accepted.

PDF binaries and flashcard scheduling/review history are outside the migration
scope. This is an actively developed staging exporter, not a single-click import.

## Start here

1. Export your knowledge base from RemNote in both structured (`.rem`) and
   Markdown (`.zip`) formats. Keep the original files unchanged.
2. Follow the [snapshot plugin guide](Remnote2Obsidian/remnote-snapshot/README.md)
   when live portal/query evidence is needed.
3. Follow the [full export guide](Remnote2Obsidian/FULL_EXPORT.md) to configure
   and run a staged conversion. Use the [bounded pilot](Remnote2Obsidian/PILOT.md)
   to test a small selection first.
4. Review the ledger, verify links and local media, then open the staged folder
   as a separate Obsidian vault before moving accepted files into your vault.

```sh
python3 Remnote2Obsidian/remnote_export.py \
  --input /path/to/export.rem \
  --markdown-export /path/to/native-markdown.zip \
  --config /path/to/private-config.json \
  --output /path/to/staged-vault \
  --full
```

The converter uses Python's standard library. The optional snapshot plugin has
its own Node.js setup instructions. Inputs, private configuration, snapshots,
generated notes and migration reports should remain outside this repository.
The converter does not upload notes; downloading linked media is a separate
step. Obsidian Sync or other folder-sync software can upload files after you
place them in a synchronized vault, so choose the destination accordingly.

## Development

```sh
cd Remnote2Obsidian
python3 -m unittest discover -p 'test_*.py'
```

Plugin tests and build commands are in its
[README](Remnote2Obsidian/remnote-snapshot/README.md).

## Origin and legacy tools

Forked from [Anwesh Gangula's PKMigrator](https://github.com/AnweshGangula/PKMigrator).
The [MIT license](LICENSE) and original attribution are retained.

The inherited `Remnote2Org`, `Obsidian2Org`, `Roam2Obsidian`, `RoamMD2Org-Roam`
and older `Remnote2Obsidian.py` converters remain for historical compatibility.
The supported development path in this fork is `Remnote2Obsidian/remnote_export.py`.
