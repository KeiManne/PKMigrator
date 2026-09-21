# RemNote to Markdown for Obsidian

The current exporter is **`remnote_export.py`**. It converts structured RemNote
data into document/topic Markdown files, copies supported portal content,
preserves source identity and reports incomplete evidence explicitly.

- [Full migration](FULL_EXPORT.md): native document boundaries, reviewed scope
  and organisation, source accounting and media reconciliation.
- [Small pilot](PILOT.md): selected roots with strict size and recursion limits.
- [Read-only snapshot plugin](remnote-snapshot/README.md): local capture of live
  portal and automatic-view evidence.

Keep personal exports and generated vaults outside this public repository.

## Legacy converter

`Remnote2Obsidian.py` is the inherited converter from PKMigrator. It predates the
staged migration workflow and does not provide its portal or reconciliation
guarantees. Its original entrypoint is retained for compatibility; use the
guides above for new migrations.
