#!/usr/bin/env python3
"""RemNote export converter with bounded pilot and staged full-export modes.

The pilot remains deliberately bounded.  Full-export mode uses the supplied native
Markdown ZIP only as structural evidence for real document/file boundaries, renders
the structured ``rem.json`` data, and emits an explicit ledger for every raw record.
Unsupported automatic views remain unresolved rather than being inferred from stale
``searchResults`` cache data.
"""

from __future__ import annotations

import argparse
import datetime as datetime_module
import hashlib
import json
import re
import sys
import unicodedata
import urllib.parse
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


CONVERTER_VERSION = "0.2.0-full-staging"
DEFAULT_MAX_OCCURRENCES = 2_000
DEFAULT_MAX_DEPTH = 50
DEFAULT_FULL_MAX_OCCURRENCES = 500_000
DEFAULT_FULL_MAX_DEPTH = 200
MAX_JSON_BYTES = 256 * 1024 * 1024
WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


class ExportError(ValueError):
    """Raised for invalid or unsafe input, before any conversion is attempted."""


def _json_bytes(path: Path) -> tuple[bytes, str]:
    raw = path.read_bytes()
    fingerprint = hashlib.sha256(raw).hexdigest()
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            candidates = []
            for info in archive.infolist():
                member = PurePosixPath(info.filename)
                if member.is_absolute() or ".." in member.parts:
                    raise ExportError(f"unsafe ZIP member path: {info.filename!r}")
                if info.is_dir():
                    continue
                if info.file_size > MAX_JSON_BYTES:
                    raise ExportError(f"ZIP member is too large: {info.filename!r}")
                if member.name == "rem.json":
                    candidates.append(info)
            if len(candidates) != 1:
                raise ExportError("archive must contain exactly one rem.json member")
            return archive.read(candidates[0]), fingerprint
    if len(raw) > MAX_JSON_BYTES:
        raise ExportError("JSON input is too large for the pilot")
    return raw, fingerprint


def load_export(path: Path) -> tuple[dict[str, Any], str]:
    raw, fingerprint = _json_bytes(path)
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExportError(f"invalid UTF-8 JSON export: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("docs"), list):
        raise ExportError("export must be an object containing a docs array")
    return payload, fingerprint


def _plain_boundary_text(
    value: Any,
    index: dict[str, dict[str, Any]] | None = None,
    seen_references: set[str] | None = None,
) -> str:
    """Extract only the text needed to match native Markdown path segments."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_plain_boundary_text(item, index, seen_references) for item in value)
    if isinstance(value, dict):
        if value.get("i") == "q" and value.get("_id"):
            target_id = str(value["_id"])
            seen = seen_references or set()
            if index is None or target_id not in index or target_id in seen:
                return f"(({target_id}))"
            return _plain_boundary_text(index[target_id].get("key"), index, seen | {target_id})
        return str(value.get("text") or value.get("alt") or value.get("title") or "")
    return str(value)


def _native_filename_title(title: str) -> str:
    title = unicodedata.normalize("NFKC", title).strip()
    title = re.sub(r'[\\/:*?"<>|]', "_", title)
    return title or "Untitled"


def _native_segment_matches(segment: str, title: str) -> bool:
    """Match RemNote's native filename escaping and observed middle truncation."""
    segment = unicodedata.normalize("NFKC", segment).strip()
    candidate = _native_filename_title(title)
    if segment == candidate:
        return True
    if segment.count("...") == 1:
        prefix, suffix = segment.split("...", 1)
        return candidate.startswith(prefix) and candidate.endswith(suffix) and len(candidate) >= len(prefix) + len(suffix)
    return False


def safe_full_component(title: str, rem_id: str, *, max_bytes: int = 120) -> str:
    """Return an Obsidian-link-safe, byte-bounded component with stable identity."""
    title = unicodedata.normalize("NFKC", title).replace("\x00", "")
    title = re.sub(r'[<>:"/\\|?*#\[\]^\x00-\x1f]', "_", title)
    title = re.sub(r"\s+", " ", title).strip(" .") or "Untitled"
    if title.upper() in WINDOWS_RESERVED:
        title = "_" + title
    suffix = "--" + hashlib.sha256(rem_id.encode("utf-8")).hexdigest()[:10]
    budget = max(1, max_bytes - len(suffix.encode("utf-8")))
    encoded = title.encode("utf-8")
    if len(encoded) > budget:
        encoded = encoded[:budget]
        while encoded:
            try:
                title = encoded.decode("utf-8").rstrip(" .")
                break
            except UnicodeDecodeError:
                encoded = encoded[:-1]
        title = title or "Untitled"
    return title + suffix


def load_markdown_boundaries(payload: dict[str, Any], archive_path: Path) -> tuple[dict[str, str], dict[str, Any]]:
    """Map every native Markdown member to one raw Rem using path/timestamp evidence."""
    if not zipfile.is_zipfile(archive_path):
        raise ExportError("full mode requires a native Markdown ZIP")
    index = {
        raw["_id"]: raw
        for raw in payload.get("docs", [])
        if isinstance(raw, dict) and isinstance(raw.get("_id"), str)
    }
    path_cache: dict[str, tuple[str, ...]] = {}
    id_path_cache: dict[str, tuple[str, ...]] = {}

    def ownership_titles(rem_id: str) -> tuple[str, ...]:
        if rem_id in path_cache:
            return path_cache[rem_id]
        path: list[str] = []
        current = rem_id
        seen: set[str] = set()
        while current in index and current not in seen:
            seen.add(current)
            path.append(_plain_boundary_text(index[current].get("key"), index, {current}).strip())
            parent = index[current].get("parent")
            if not isinstance(parent, str):
                break
            current = parent
        result = tuple(reversed(path))
        path_cache[rem_id] = result
        return result

    def ownership_ids(rem_id: str) -> tuple[str, ...]:
        if rem_id in id_path_cache:
            return id_path_cache[rem_id]
        path: list[str] = []
        current = rem_id
        seen: set[str] = set()
        while current in index and current not in seen:
            seen.add(current)
            path.append(current)
            parent = index[current].get("parent")
            if not isinstance(parent, str):
                break
            current = parent
        result = tuple(reversed(path))
        id_path_cache[rem_id] = result
        return result

    by_depth: dict[int, list[str]] = defaultdict(list)
    for rem_id in index:
        by_depth[len(ownership_titles(rem_id))].append(rem_id)

    file_map: dict[str, str] = {}
    matches: list[dict[str, Any]] = []
    empty_files = 0
    with zipfile.ZipFile(archive_path) as archive:
        infos = [info for info in archive.infolist() if not info.is_dir() and PurePosixPath(info.filename).suffix.lower() == ".md"]
        for info in infos:
            member = PurePosixPath(info.filename)
            if member.is_absolute() or ".." in member.parts:
                raise ExportError(f"unsafe Markdown ZIP member path: {info.filename!r}")
            if info.file_size == 0:
                empty_files += 1
            member_without_suffix = member.with_suffix("")
            native_parts = tuple(member_without_suffix.parts)
            candidates = [
                rem_id
                for rem_id in by_depth[len(native_parts)]
                if all(_native_segment_matches(segment, title) for segment, title in zip(native_parts, ownership_titles(rem_id)))
            ]
            method = "ownership_path"
            timestamp_delta_ms: int | None = None
            if len(candidates) > 1:
                zip_timestamp = int(
                    datetime_module.datetime(*info.date_time, tzinfo=datetime_module.timezone.utc).timestamp() * 1000
                )

                def timestamp_delta(rem_id: str) -> int:
                    values = [
                        index[rem_id].get(field)
                        for field in ("docUpdated", "u", "m", "createdAt")
                        if isinstance(index[rem_id].get(field), (int, float))
                    ]
                    return min((abs(int(value) - zip_timestamp) for value in values), default=10**30)

                ranked = sorted((timestamp_delta(rem_id), rem_id) for rem_id in candidates)
                if len(ranked) > 1 and ranked[0][0] < ranked[1][0] and ranked[0][0] <= 2_000:
                    timestamp_delta_ms, chosen = ranked[0]
                    candidates = [chosen]
                    method = "ownership_path_and_timestamp"
            if len(candidates) != 1:
                raise ExportError(
                    f"native Markdown boundary {info.filename!r} matched {len(candidates)} raw records; "
                    "refusing to guess"
                )
            rem_id = candidates[0]
            source_ids = ownership_ids(rem_id)
            source_titles = ownership_titles(rem_id)
            safe_parts = [safe_full_component(title, source_id) for source_id, title in zip(source_ids, source_titles)]
            safe_parts[-1] += ".md"
            relative = str(PurePosixPath("Sources", "RemNote", *safe_parts))
            if rem_id in file_map:
                raise ExportError(f"raw record {rem_id!r} matched multiple native Markdown files")
            file_map[rem_id] = relative
            matches.append({
                "rem_id": rem_id,
                "native_path": info.filename,
                "output_path": relative,
                "method": method,
                "timestamp_delta_ms": timestamp_delta_ms,
                "native_size": info.file_size,
            })
    normalized_paths: dict[str, str] = {}
    for path in file_map.values():
        folded = unicodedata.normalize("NFKC", path).casefold()
        if folded in normalized_paths:
            raise ExportError(f"case-insensitive Markdown path collision: {path!r} and {normalized_paths[folded]!r}")
        normalized_paths[folded] = path
    evidence = {
        "archive_sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
        "markdown_files": len(matches),
        "empty_files": empty_files,
        "match_methods": dict(sorted(Counter(item["method"] for item in matches).items())),
        "matches": matches,
    }
    return file_map, evidence


def _fnv1a64_javascript(value: str) -> str:
    """Match the snapshot plugin's FNV-1a over JavaScript UTF-16 code units."""
    digest = 0xCBF29CE484222325
    encoded = value.encode("utf-16-le", "surrogatepass")
    for offset in range(0, len(encoded), 2):
        digest ^= int.from_bytes(encoded[offset:offset + 2], "little")
        digest = (digest * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return f"{digest:016x}"


def _canonical_javascript_json_value(value: Any) -> Any:
    if isinstance(value, list):
        return [_canonical_javascript_json_value(item) for item in value]
    if isinstance(value, dict):
        def utf16_units(key: str) -> tuple[int, ...]:
            encoded = key.encode("utf-16-be", "surrogatepass")
            return tuple(int.from_bytes(encoded[offset:offset + 2], "big") for offset in range(0, len(encoded), 2))

        return {
            key: _canonical_javascript_json_value(value[key])
            for key in sorted(value, key=utf16_units)
        }
    return value


def _snapshot_rich_fingerprint(raw: dict[str, Any]) -> str:
    serialized = json.dumps(
        _canonical_javascript_json_value([raw.get("key"), raw.get("value")]),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return "fnv1a64-canonical-richtext-v1:" + _fnv1a64_javascript(serialized)


def _validate_snapshot_drift(contract: dict[str, Any], payload: dict[str, Any]) -> None:
    records = contract.get("records")
    if not isinstance(records, dict):
        raise ExportError("snapshot contract needs a complete records object for export drift validation")
    exported = {
        raw["_id"]: raw
        for raw in payload.get("docs", [])
        if isinstance(raw, dict) and isinstance(raw.get("_id"), str)
    }
    snapshot_ids = set(records)
    export_ids = set(exported)
    if snapshot_ids != export_ids:
        raise ExportError(
            "snapshot/export record identities drifted "
            f"(missing from snapshot: {len(export_ids - snapshot_ids)}, new in snapshot: {len(snapshot_ids - export_ids)})"
        )
    comparison = contract.get("capture", {}).get("export_comparison")
    if (
        not isinstance(comparison, dict)
        or comparison.get("rich_text_algorithm") != "fnv1a64-canonical-richtext-v1"
        or comparison.get("structural_fields") != ["id", "parent_id", "child_ids"]
        or comparison.get("child_order_raw_basis")
        != "Compare SDK children array order with raw siblings sorted by fractional f, using raw record ordinal as the tie-break."
        or comparison.get("raw_input_fields") != ["key", "value"]
        or comparison.get("sdk_input_fields") != ["text", "backText"]
    ):
        raise ExportError("snapshot contract lacks the supported export-comparable rich-text algorithm")
    compare_rich_text = comparison.get("calibrated_equivalent") is True
    compare_child_order = comparison.get("child_order_calibrated") is True
    ordinal = {rem_id: position for position, rem_id in enumerate(exported)}
    exported_children: dict[str, list[str]] = defaultdict(list)
    for rem_id, raw in exported.items():
        parent = raw.get("parent")
        if isinstance(parent, str) and parent in exported:
            exported_children[parent].append(rem_id)
    for parent, child_ids in exported_children.items():
        child_ids.sort(key=lambda rem_id: (str(exported[rem_id].get("f", "~")), ordinal[rem_id]))
    mismatches: Counter[str] = Counter()
    for rem_id, raw in exported.items():
        observed = records.get(rem_id)
        if not isinstance(observed, dict) or observed.get("id") != rem_id:
            mismatches["invalid_record"] += 1
            continue
        raw_parent = raw.get("parent") if isinstance(raw.get("parent"), str) else None
        if observed.get("parent_id") != raw_parent:
            mismatches["parent"] += 1
        if compare_child_order and observed.get("child_ids") != exported_children.get(rem_id, []):
            mismatches["child_order"] += 1
        if compare_rich_text and observed.get("export_comparable_rich_text_fingerprint") != _snapshot_rich_fingerprint(raw):
            mismatches["rich_text"] += 1
    if mismatches:
        summary = ", ".join(f"{name}={count}" for name, count in sorted(mismatches.items()))
        raise ExportError(f"snapshot/export record drift detected ({summary})")


def load_snapshot_contract(
    path: Path,
    payload: dict[str, Any],
    expected_knowledgebase_id: str | None = None,
) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    try:
        contract = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExportError(f"invalid snapshot contract JSON: {exc}") from exc
    if not isinstance(contract, dict) or contract.get("schema_version") != "remnote-migration-snapshot/v1":
        raise ExportError("snapshot contract must use schema_version remnote-migration-snapshot/v1")
    capture = contract.get("capture")
    projection = contract.get("converter_projection")
    if not isinstance(capture, dict) or not isinstance(projection, dict):
        raise ExportError("snapshot contract needs capture and converter_projection objects")
    payload_kb = payload.get("knowledgebaseId")
    if (
        isinstance(payload_kb, str)
        and payload_kb.strip()
        and isinstance(expected_knowledgebase_id, str)
        and expected_knowledgebase_id.strip()
        and payload_kb != expected_knowledgebase_id
    ):
        raise ExportError("configured knowledge-base identity does not match the export")
    source_kb = payload_kb or expected_knowledgebase_id
    captured_kb = capture.get("knowledgebase_id")
    if not isinstance(source_kb, str) or not source_kb.strip():
        raise ExportError("snapshot intake requires a known export knowledge-base ID or config.knowledgebase_id")
    if not isinstance(captured_kb, str) or not captured_kb.strip():
        raise ExportError("snapshot capture has no knowledge-base identity")
    if source_kb != captured_kb:
        raise ExportError("snapshot contract knowledge-base identity does not match the export")
    _validate_snapshot_drift(contract, payload)
    return contract, hashlib.sha256(raw).hexdigest()


def safe_stem(title: str, rem_id: str) -> str:
    """Return a deterministic, portable, non-traversing file stem."""
    title = unicodedata.normalize("NFKC", title).replace("\x00", "")
    title = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", title)
    title = re.sub(r"\s+", " ", title).strip(" .")
    if not title:
        title = "Untitled"
    if title.upper() in WINDOWS_RESERVED:
        title = "_" + title
    suffix = hashlib.sha256(rem_id.encode("utf-8")).hexdigest()[:10]
    return f"{title[:80].rstrip(' .')}--{suffix}"


def stable_anchor(rem_id: str, context: str) -> str:
    digest = hashlib.sha256(f"{rem_id}\0{context}".encode("utf-8")).hexdigest()[:16]
    return f"rem-{digest}"


def _escape_markdown(text: str) -> str:
    text = text.replace("\\", "\\\\")
    return re.sub(r"([`*_{}\[\]<>])", r"\\\1", text)


def _inline_code(text: str) -> str:
    longest = max((len(x) for x in re.findall(r"`+", text)), default=0)
    fence = "`" * (longest + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def _url(url: str) -> str:
    return urllib.parse.quote(url, safe=":/?#[]@!$&'()*+,;=%")


@dataclass(frozen=True)
class RenderContext:
    file: str
    root_id: str
    portal_id: str | None
    source_path: tuple[str, ...]
    appearance_path: tuple[str, ...]


class Converter:
    def __init__(
        self,
        payload: dict[str, Any],
        fingerprint: str,
        roots: Iterable[str],
        *,
        max_occurrences: int = DEFAULT_MAX_OCCURRENCES,
        max_depth: int = DEFAULT_MAX_DEPTH,
        portal_snapshots: dict[str, Any] | None = None,
        visibility_overrides: dict[str, Any] | None = None,
        file_map: dict[str, str] | None = None,
        mode: str = "pilot",
        boundary_evidence: dict[str, Any] | None = None,
        snapshot_contract: dict[str, Any] | None = None,
        snapshot_contract_sha256: str | None = None,
        knowledgebase_id: str | None = None,
        split_candidates: dict[str, Any] | None = None,
    ) -> None:
        if max_occurrences < 1 or max_depth < 1:
            raise ExportError("max_occurrences and max_depth must be positive")
        if mode not in {"pilot", "full"}:
            raise ExportError("mode must be pilot or full")
        if mode == "full" and not file_map:
            raise ExportError("full mode requires evidenced Markdown document boundaries")
        self.payload = payload
        self.fingerprint = fingerprint
        self.mode = mode
        self.full_mode = mode == "full"
        self.requested_file_map = dict(file_map or {})
        self.roots = list(self.requested_file_map) if self.full_mode else list(dict.fromkeys(str(x) for x in roots))
        if not self.roots:
            raise ExportError("at least one explicit root ID is required")
        self.max_occurrences = max_occurrences
        self.max_depth = max_depth
        self.portal_snapshots = portal_snapshots or {}
        self.visibility_overrides = visibility_overrides or {}
        self.boundary_evidence = boundary_evidence or {}
        self.snapshot_contract = snapshot_contract
        self.snapshot_contract_sha256 = snapshot_contract_sha256
        self.knowledgebase_id = knowledgebase_id or payload.get("knowledgebaseId")
        self.split_candidates = split_candidates or {}
        self.index: dict[str, dict[str, Any]] = {}
        self.children: dict[str, list[str]] = defaultdict(list)
        self.files: dict[str, str] = {}
        self.canonical: dict[str, dict[str, str]] = {}
        self.source_map: dict[str, dict[str, Any]] = {}
        self.assets: dict[str, dict[str, Any]] = {}
        self.issues: list[dict[str, Any]] = []
        self._issue_keys: set[tuple[Any, ...]] = set()
        self._occurrences = 0
        self._budget_reported = False
        self._referenced_canonical: set[str] = set()
        self._prepare_index()
        if self.full_mode:
            for rem_id in self.split_candidates:
                if rem_id not in self.files:
                    self.issue(
                        "unknown_split_candidate",
                        "error",
                        "Configured split-review candidate is not an evidenced document boundary",
                        rem_id=rem_id,
                    )
        if self.full_mode and self.snapshot_contract is not None:
            capture = self.snapshot_contract.get("capture", {})
            if capture.get("complete") is not True:
                self.issue(
                    "snapshot_capture_incomplete",
                    "error",
                    "Snapshot contract does not attest a complete capture; only individually projected evidence is applied",
                    details={"errors": capture.get("errors", [])},
                )
            comparison = capture.get("export_comparison", {})
            if comparison.get("calibrated_equivalent") is not True:
                self.issue(
                    "snapshot_rich_text_comparison_uncalibrated",
                    "error",
                    "Snapshot structure matched the export, but raw key/value versus SDK text/backText equivalence was not calibrated; content drift remains unresolved",
                    details={"algorithm": comparison.get("rich_text_algorithm")},
                )
            if comparison.get("child_order_calibrated") is not True:
                self.issue(
                    "snapshot_child_order_comparison_uncalibrated",
                    "error",
                    "Snapshot child order was not calibrated against the export's parent/fractional-order traversal; sibling-order drift remains unresolved",
                )
            self._validate_snapshot_scope()
            self._validate_snapshot_boundaries()

    def _snapshot_classification(self, name: str) -> dict[str, Any]:
        if not self.snapshot_contract:
            return {}
        classifications = self.snapshot_contract.get("classifications")
        if not isinstance(classifications, dict):
            return {}
        value = classifications.get(name)
        return value if isinstance(value, dict) else {}

    def _is_evidenced_system_definition(self, rem_id: str) -> bool:
        classification = self._snapshot_classification("system_definition")
        states = classification.get("states")
        state = states.get(rem_id) if isinstance(states, dict) else None
        if not isinstance(state, dict):
            return False
        fields = (
            "is_powerup",
            "is_powerup_enum",
            "is_powerup_property_list_item",
            "is_powerup_slot",
            "is_powerup_property",
        )
        return any(state.get(field) is True for field in fields)

    def _validate_snapshot_boundaries(self) -> None:
        classification = self._snapshot_classification("document_and_folder")
        states = classification.get("states")
        if not isinstance(states, dict):
            self.issue(
                "snapshot_boundary_classification_missing",
                "error",
                "Snapshot does not contain document/folder classification states",
            )
            return
        missing_boundaries = sorted(set(self.files) - set(states))
        conflicts = sorted(
            rem_id
            for rem_id in self.files
            if isinstance(states.get(rem_id), dict)
            and states[rem_id].get("is_document") is not True
            and states[rem_id].get("is_folder") is not True
        )
        snapshot_only_boundaries = sorted(
            rem_id
            for rem_id, state in states.items()
            if rem_id in self.index
            and rem_id not in self.files
            and isinstance(state, dict)
            and (state.get("is_document") is True or state.get("is_folder") is True)
        )
        if missing_boundaries:
            self.issue(
                "snapshot_boundary_classification_incomplete",
                "error",
                "Snapshot did not classify every native Markdown boundary",
                details={"missing_count": len(missing_boundaries)},
            )
        if conflicts:
            self.issue(
                "document_boundary_classification_conflict",
                "error",
                "Live SDK classification disagrees with native Markdown boundary evidence",
                details={"conflict_count": len(conflicts)},
            )
        if snapshot_only_boundaries:
            self.issue(
                "snapshot_boundary_missing_from_native_export",
                "error",
                "Live SDK reports document/folder records with no matched native Markdown file",
                details={"record_count": len(snapshot_only_boundaries)},
            )

    def _validate_snapshot_scope(self) -> None:
        if not self.snapshot_contract:
            return
        capture = self.snapshot_contract.get("capture", {})
        scope = capture.get("scope")
        portals = self.snapshot_contract.get("portals")
        exported_portals = {rem_id for rem_id, raw in self.index.items() if raw.get("type") == 6}
        captured_portals = set(portals) if isinstance(portals, dict) else set()
        scope_complete = (
            capture.get("mode") == "complete"
            and capture.get("knowledgebase_consistent") is True
            and capture.get("knowledgebase_id_at_end") == capture.get("knowledgebase_id")
            and isinstance(scope, dict)
            and scope.get("expected_portal_count") == len(exported_portals)
            and scope.get("processed_portal_count") == len(exported_portals)
            and scope.get("missing_requested_portal_ids") == []
            and captured_portals == exported_portals
        )
        if not scope_complete:
            self.issue(
                "snapshot_scope_incomplete",
                "error",
                "Snapshot is not a complete, knowledge-base-consistent capture of every exported portal; partial evidence may inform the draft but cannot pass admission",
                details={
                    "capture_mode": capture.get("mode"),
                    "exported_portal_count": len(exported_portals),
                    "captured_portal_count": len(captured_portals),
                    "scope": scope,
                },
            )

    def _prepare_index(self) -> None:
        for ordinal, raw in enumerate(self.payload["docs"]):
            if not isinstance(raw, dict) or not isinstance(raw.get("_id"), str):
                self.issue("invalid_record", "error", "Record has no string _id", details={"ordinal": ordinal})
                continue
            rem_id = raw["_id"]
            if rem_id in self.index:
                self.issue("duplicate_id", "error", "Duplicate Rem ID", rem_id=rem_id)
                continue
            self.index[rem_id] = raw
        order = {rid: i for i, rid in enumerate(self.index)}
        for rem_id, raw in self.index.items():
            parent = raw.get("parent")
            if isinstance(parent, str):
                self.children[parent].append(rem_id)
        for parent, ids in self.children.items():
            ids.sort(key=lambda rid: (str(self.index[rid].get("f", "~")), order[rid]))
        for root in self.roots:
            if root not in self.index:
                self.issue("missing_root", "error", "Requested root is absent from export", rem_id=root)
                continue
            if self.index[root].get("type") == 6:
                self.issue("portal_root_unsupported", "error", "A pilot root must be a source/document Rem, not a portal", rem_id=root, portal_id=root)
                continue
            if self.full_mode:
                relative = PurePosixPath(self.requested_file_map[root])
                if relative.is_absolute() or ".." in relative.parts or relative.suffix.lower() != ".md":
                    raise ExportError(f"unsafe full-export output path for {root!r}")
                self.files[root] = str(relative)
            else:
                title = self.plain_rich(self.index[root].get("key")) or "Untitled"
                self.files[root] = f"notes/{safe_stem(title, root)}.md"
        # Every explicit root owns its own canonical location, even when roots overlap.
        for root in self.roots:
            if root in self.files:
                context = f"canonical:{root}:{root}"
                self.canonical[root] = {"file": self.files[root], "anchor": stable_anchor(root, context)}
        # Other canonical locations are based only on ordinary ownership, never
        # portal/query-helper ownership or the contents of another selected root.
        for root in self.roots:
            if root in self.files:
                self._assign_canonical(root, root, (), set())

    def _assign_canonical(self, rem_id: str, root: str, path: tuple[str, ...], seen: set[str]) -> None:
        if rem_id in seen or len(path) > self.max_depth:
            return
        if self.index[rem_id].get("type") == 6:
            return
        if rem_id in self.files and rem_id != root:
            return
        if rem_id not in self.canonical:
            context = f"canonical:{root}:{'/'.join(path + (rem_id,))}"
            self.canonical[rem_id] = {"file": self.files[root], "anchor": stable_anchor(rem_id, context)}
        next_seen = seen | {rem_id}
        for child in self.children.get(rem_id, []):
            self._assign_canonical(child, root, path + (rem_id,), next_seen)

    def issue(
        self,
        code: str,
        severity: str,
        message: str,
        *,
        rem_id: str | None = None,
        portal_id: str | None = None,
        path: Iterable[str] = (),
        details: dict[str, Any] | None = None,
    ) -> None:
        key = (code, rem_id, portal_id, tuple(path), json.dumps(details or {}, sort_keys=True))
        if key in self._issue_keys:
            return
        self._issue_keys.add(key)
        self.issues.append({
            "code": code,
            "severity": severity,
            "message": message,
            "rem_id": rem_id,
            "portal_id": portal_id,
            "path": list(path),
            "details": details or {},
        })

    def plain_rich(self, value: Any) -> str:
        return self._plain_rich(value, set())

    def _plain_rich(self, value: Any, seen_references: set[str]) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if not isinstance(value, list):
            value = [value]
        result = []
        for part in value:
            if isinstance(part, str):
                result.append(part)
            elif isinstance(part, dict):
                kind = part.get("i")
                if kind == "q" and part.get("_id"):
                    target_id = str(part["_id"])
                    target = self.index.get(target_id)
                    if not target:
                        fallback = self._plain_rich(part.get("textOfDeletedRem"), seen_references).strip()
                        result.append(f"{fallback} (({target_id}))" if fallback else f"(({target_id}))")
                    elif target_id in seen_references:
                        result.append(f"(({target_id}))")
                    else:
                        result.append(self._plain_rich(target.get("key"), seen_references | {target_id}))
                elif kind == "i":
                    result.append(str(part.get("alt") or part.get("title") or "[image]"))
                elif kind == "o":
                    result.append(str(part.get("text", "")))
                else:
                    result.append(str(part.get("text", "")))
            else:
                result.append(str(part))
        return "".join(result)

    def render_rich(self, value: Any, ctx: RenderContext, owner_id: str) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return _escape_markdown(value)
        if not isinstance(value, list):
            value = [value]
        rendered = []

        def append_block(block: str) -> None:
            if rendered and not rendered[-1].endswith("\n"):
                rendered.append("\n")
            rendered.extend((block, "\n"))

        for part in value:
            if isinstance(part, str):
                rendered.append(_escape_markdown(part))
                continue
            if not isinstance(part, dict):
                rendered.append(_escape_markdown(str(part)))
                continue
            kind = part.get("i")
            text = str(part.get("text", ""))
            if kind == "q" and part.get("_id"):
                target_id = str(part["_id"])
                fallback_label = self._plain_rich(part.get("textOfDeletedRem"), set()).strip()
                label = self.plain_rich(self.index.get(target_id, {}).get("key")) or fallback_label or target_id
                target = self.canonical.get(target_id)
                if target:
                    self._referenced_canonical.add(target_id)
                    rendered.append(f"[[{target['file']}#^{target['anchor']}|{_escape_markdown(label)}]]")
                elif self.full_mode and target_id in self.index:
                    rendered.append(_escape_markdown(label))
                    evidenced_system = self._is_evidenced_system_definition(target_id)
                    self.issue(
                        "system_reference_label_only" if evidenced_system else "outside_document_reference_label_only",
                        "warning",
                        (
                            "Reference target is an SDK-classified system definition without a canonical note; its visible label was preserved"
                            if evidenced_system
                            else "Reference target is exported outside every evidenced document boundary; its visible label was preserved without inventing a link"
                        ),
                        rem_id=target_id,
                        details={"target_id": target_id},
                    )
                else:
                    unresolved = f"{fallback_label} (({target_id}))" if fallback_label else f"(({target_id}))"
                    rendered.append(_escape_markdown(unresolved))
                    self.issue(
                        "unresolved_reference",
                        "warning",
                        "Reference target has no canonical output location; any exported deleted-reference label was retained with the unresolved ID",
                        rem_id=owner_id,
                        path=ctx.appearance_path,
                        details={"target_id": target_id, "fallback_label_preserved": bool(fallback_label)},
                    )
                continue
            if kind == "i":
                source = part.get("url")
                if not isinstance(source, str) or not source:
                    rendered.append("[image unavailable]")
                    self.issue("image_without_url", "warning", "Image object has no URL", rem_id=owner_id, path=ctx.appearance_path)
                    continue
                parsed = urllib.parse.urlparse(source)
                if parsed.scheme not in {"http", "https"}:
                    self.issue(
                        "unsupported_asset_scheme",
                        "error" if self.full_mode else "warning",
                        "Image source is not an HTTP(S) asset and requires explicit recovery evidence",
                        rem_id=owner_id,
                        details={"source": source, "scheme": parsed.scheme or None},
                    )
                suffix = Path(parsed.path).suffix.lower()
                if not re.fullmatch(r"\.[a-z0-9]{1,8}", suffix):
                    suffix = ".bin"
                    self.issue(
                        "asset_extension_unresolved",
                        "error" if self.full_mode else "warning",
                        "Image source has no evidenced image suffix and requires content validation",
                        rem_id=owner_id,
                        details={"source": source},
                    )
                relative = f"Attachments/RemNote/{hashlib.sha256(source.encode()).hexdigest()[:24]}{suffix}"
                asset = self.assets.setdefault(source, {"relative_path": relative, "occurrences": []})
                asset["occurrences"].append({"rem_id": owner_id, "file": ctx.file, "portal_id": ctx.portal_id})
                rendered.append(f"![[{relative}]]")
                continue
            if kind == "o" or part.get("code") is True:
                language = re.sub(r"[^A-Za-z0-9_+.-]", "", str(part.get("language", "")))
                longest = max((len(x) for x in re.findall(r"`+", text)), default=0)
                fence = "`" * max(3, longest + 1)
                append_block(f"{fence}{language}\n{text}\n{fence}")
                continue
            if kind == "x" and part.get("block"):
                append_block(f"$$\n{text}\n$$")
                continue
            if kind not in {"m", "u", "x", "s", None}:
                self.issue("unsupported_rich_text", "warning", "Unknown rich-text object was reduced to text", rem_id=owner_id, path=ctx.appearance_path, details={"kind": kind})
            if kind == "x" or part.get("x"):
                segment = f"${text}$"
            elif part.get("q"):
                segment = _inline_code(text)
            elif part.get("url"):
                segment = f"[{_escape_markdown(text or str(part['url']))}]({_url(str(part['url']))})"
            else:
                segment = _escape_markdown(text)
            if part.get("b"):
                segment = f"**{segment}**"
            if part.get("l"):
                segment = f"*{segment}*"
            if part.get("h") not in (None, False, 0):
                segment = f"=={segment}=="
            rendered.append(segment)
        return "".join(rendered).rstrip("\n")

    def _source_entry(self, rem_id: str) -> dict[str, Any]:
        if rem_id not in self.source_map:
            raw = self.index[rem_id]
            plain_key = self.plain_rich(raw.get("key"))
            plain_value = self.plain_rich(raw.get("value"))
            self.source_map[rem_id] = {
                "rem_id": rem_id,
                "canonical": self.canonical.get(rem_id),
                "canonical_origin": self._canonical_origin(rem_id),
                "occurrences": [],
                "plain_original_text": plain_key + ((" — " + plain_value) if plain_value else ""),
                "timestamps": {key: raw.get(key) for key in ("createdAt", "m", "u")},
                "original_parent": raw.get("parent"),
            }
        return self.source_map[rem_id]

    def _canonical_origin(self, rem_id: str) -> dict[str, Any]:
        """Describe source ownership even when its canonical file is outside the pilot."""
        path = []
        current = rem_id
        seen = set()
        document_id = None
        while current in self.index and current not in seen and len(path) <= self.max_depth:
            seen.add(current)
            path.append(current)
            raw = self.index[current]
            if self.full_mode and current in self.files:
                document_id = current
                break
            if not self.full_mode and (raw.get("n") == 1 or raw.get("forceIsFolder") is True):
                document_id = current
            parent = raw.get("parent")
            if not isinstance(parent, str):
                break
            current = parent
        document = self.index.get(document_id, {}) if document_id else {}
        return {
            "document_id": document_id,
            "document_title": self.plain_rich(document.get("key")) if document_id else None,
            "ownership_path": list(reversed(path)),
        }

    def _record_occurrence(self, rem_id: str, ctx: RenderContext, anchor: str, kind: str, text: str) -> bool:
        if self._occurrences >= self.max_occurrences:
            if not self._budget_reported:
                self._budget_reported = True
                self.issue("occurrence_limit", "error", "Conversion occurrence limit reached; output is intentionally incomplete", rem_id=rem_id, portal_id=ctx.portal_id, path=ctx.appearance_path, details={"max_occurrences": self.max_occurrences})
            return False
        self._occurrences += 1
        self._source_entry(rem_id)["occurrences"].append({
            "file": ctx.file,
            "anchor": anchor,
            "kind": kind,
            "portal_id": ctx.portal_id,
            "portal_context": list(ctx.appearance_path) if ctx.portal_id else None,
            "path": list(ctx.appearance_path),
            "visible": True,
            "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        })
        return True

    def _is_search_portal(self, raw: dict[str, Any]) -> bool:
        return raw.get("portalType") == 4

    def _snapshot_targets(self, portal_id: str) -> list[str] | None:
        snapshot = self.portal_snapshots.get(portal_id)
        if snapshot is None:
            return None
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("evidence"), str) or not snapshot["evidence"].strip() or not isinstance(snapshot.get("members"), list):
            self.issue("invalid_portal_snapshot", "error", "Portal snapshot needs non-empty evidence and a members array", portal_id=portal_id)
            return None
        return [str(x) for x in snapshot["members"]]

    def _portal_targets(self, portal_id: str, raw: dict[str, Any], ctx: RenderContext) -> list[str]:
        snapshot = self._snapshot_targets(portal_id)
        if snapshot is not None:
            return snapshot
        if self._is_search_portal(raw):
            self.issue("unsupported_required_view", "error", "Search/automatic portal needs an explicitly evidenced snapshot", portal_id=portal_id, path=ctx.appearance_path, details={"cached_result_count": len(raw.get("searchResults") or [])})
            return []
        if "portalType" in raw:
            self.issue("unsupported_portal_type", "error", "Portal enum value is not supported by this pilot", portal_id=portal_id, path=ctx.appearance_path, details={"portal_type": raw.get("portalType")})
            return []
        if "embeddedSearchId" in raw or "searchResults" in raw:
            self.issue("ambiguous_portal_type", "error", "Portal has search fields without the verified search portal enum", portal_id=portal_id, path=ctx.appearance_path)
            return []
        pd = raw.get("pd")
        if not isinstance(pd, dict):
            self.issue("portal_without_members", "error", "Ordinary portal has no supported pd membership map", portal_id=portal_id, path=ctx.appearance_path)
            return []
        targets = []
        string_ordered = []
        boolean_roots = []
        for target_id, state in pd.items():
            included = state.get("d") if isinstance(state, dict) else state
            if isinstance(included, str):
                string_ordered.append((included, str(target_id)))
            elif included is True:
                boolean_roots.append(str(target_id))
            elif included not in (False, None):
                self.issue("unknown_portal_membership", "error", "Unknown portal membership value", portal_id=portal_id, path=ctx.appearance_path, details={"target_id": target_id, "value_type": type(included).__name__})
        targets.extend(target for _, target in sorted(string_ordered))
        # The export supplies no ordering key for boolean roots. Retain JSON order so
        # the draft is inspectable, but require an evidenced snapshot when it matters.
        targets.extend(boolean_roots)
        if boolean_roots and len(boolean_roots) + len(string_ordered) > 1:
            self.issue("portal_boolean_order_uncertain", "error", "Boolean-included portal roots have no evidenced order relative to other roots; provide a portal snapshot", portal_id=portal_id, path=ctx.appearance_path, details={"boolean_root_count": len(boolean_roots), "ordered_root_count": len(string_ordered)})
        return targets

    def _visibility(self, portal_id: str, target_id: str, raw_portal: dict[str, Any], ctx: RenderContext) -> bool:
        override_config = self.visibility_overrides.get(portal_id)
        overrides: dict[str, Any] = {}
        if override_config is not None:
            valid = (
                isinstance(override_config, dict)
                and isinstance(override_config.get("evidence"), str)
                and bool(override_config["evidence"].strip())
                and isinstance(override_config.get("states"), dict)
            )
            if not valid:
                self.issue("invalid_visibility_override", "error", "Visibility overrides need non-empty evidence and a states object", portal_id=portal_id, path=ctx.appearance_path)
            else:
                overrides = override_config["states"]
        if target_id in overrides:
            value = overrides[target_id]
            if value in (True, "visible", "show"):
                return True
            if value in (False, "hidden", "hide"):
                return False
            self.issue("invalid_visibility_override", "error", "Visibility override must be visible/hidden or boolean", rem_id=target_id, portal_id=portal_id, path=ctx.appearance_path)
        if isinstance(raw_portal.get("ph"), dict) and target_id in raw_portal["ph"]:
            state = raw_portal["ph"][target_id]
            hidden = state.get("h") if isinstance(state, dict) else state
            if hidden == "h":
                return False
            self.issue("ambiguous_portal_visibility", "error", "Portal visibility state is not an evidenced explicit-hidden value", rem_id=target_id, portal_id=portal_id, path=ctx.appearance_path, details={"observed_state": hidden})
        return True

    def _cycle_line(self, rem_id: str, level: int, ctx: RenderContext) -> str:
        target = self.canonical.get(rem_id)
        label = self.plain_rich(self.index.get(rem_id, {}).get("key")) or rem_id
        if target:
            body = f"[[{target['file']}#^{target['anchor']}|{_escape_markdown(label)}]] (cycle stopped)"
        else:
            body = f"{_escape_markdown(label)} (cycle stopped)"
        return f"{'  ' * level}- {body}"

    def _render_node(self, rem_id: str, level: int, ctx: RenderContext, kind: str) -> list[str]:
        if self._budget_reported:
            return []
        if len(ctx.source_path) >= self.max_depth:
            self.issue("depth_limit", "error", "Conversion traversal depth limit reached", rem_id=rem_id, portal_id=ctx.portal_id, path=ctx.appearance_path, details={"max_depth": self.max_depth})
            return []
        if rem_id not in self.index:
            self.issue("missing_target", "error", "Portal or ownership target is absent", rem_id=rem_id, portal_id=ctx.portal_id, path=ctx.appearance_path)
            return []
        if rem_id in ctx.source_path:
            self.issue("cycle", "warning", "Recursive branch stopped at a per-path cycle", rem_id=rem_id, portal_id=ctx.portal_id, path=ctx.appearance_path)
            return [self._cycle_line(rem_id, level, ctx)]
        raw = self.index[rem_id]
        next_ctx = RenderContext(ctx.file, ctx.root_id, ctx.portal_id, ctx.source_path + (rem_id,), ctx.appearance_path + (rem_id,))
        if ctx.portal_id and not self._visibility(ctx.portal_id, rem_id, self.index[ctx.portal_id], ctx):
            return []
        if raw.get("type") == 6:
            return self._render_portal(rem_id, raw, level, next_ctx)
        if rem_id in self.files and rem_id != ctx.root_id and ctx.portal_id is None:
            target = self.canonical[rem_id]
            self._referenced_canonical.add(rem_id)
            label = self.plain_rich(raw.get("key")) or "Untitled"
            text = f"[[{target['file']}#^{target['anchor']}|{_escape_markdown(label)}]]"
            anchor = stable_anchor(rem_id, f"document-link:{ctx.root_id}:{'/'.join(next_ctx.appearance_path)}")
            if not self._record_occurrence(rem_id, next_ctx, anchor, "document-link", text):
                return []
            return [f"{'  ' * level}- {text} ^{anchor}"]
        text = self.render_rich(raw.get("key"), next_ctx, rem_id)
        value = self.render_rich(raw.get("value"), next_ctx, rem_id)
        if value:
            text = f"{text} — {value}" if text else value
        context = f"{kind}:{ctx.root_id}:{ctx.portal_id or ''}:{'/'.join(next_ctx.appearance_path)}"
        if kind == "canonical" and self.canonical.get(rem_id, {}).get("file") == ctx.file:
            anchor = self.canonical[rem_id]["anchor"]
        else:
            anchor = stable_anchor(rem_id, context)
        if not self._record_occurrence(rem_id, next_ctx, anchor, kind, text):
            return []
        prefix = "  " * level
        if "\n" in text:
            parts = text.splitlines()
            lines = [f"{prefix}- {parts[0] or '  '}"]
            lines.extend(f"{prefix}  {part}" for part in parts[1:])
            lines.append(f"{prefix}  ^{anchor}")
        else:
            lines = [f"{prefix}- {text or '[empty]'} ^{anchor}"]
        for child in self.children.get(rem_id, []):
            lines.extend(self._render_node(child, level + 1, next_ctx, "portal-copy" if ctx.portal_id else "canonical"))
        return lines

    def _render_portal(self, portal_id: str, raw: dict[str, Any], level: int, ctx: RenderContext) -> list[str]:
        lines = []
        targets = self._portal_targets(portal_id, raw, ctx)
        for target_id in targets:
            portal_ctx = RenderContext(ctx.file, ctx.root_id, portal_id, ctx.source_path, ctx.appearance_path + (f"portal:{portal_id}",))
            lines.extend(self._render_node(target_id, level, portal_ctx, "portal-copy"))
        return lines

    def _nearest_full_boundary(self, rem_id: str) -> str | None:
        current = rem_id
        seen: set[str] = set()
        while current in self.index and current not in seen:
            seen.add(current)
            if current in self.files:
                return current
            parent = self.index[current].get("parent")
            if not isinstance(parent, str):
                break
            current = parent
        return None

    def _has_portal_ancestor(self, rem_id: str) -> bool:
        current = self.index.get(rem_id, {}).get("parent")
        seen: set[str] = set()
        while isinstance(current, str) and current in self.index and current not in seen:
            seen.add(current)
            if self.index[current].get("type") == 6:
                return True
            if current in self.files:
                return False
            current = self.index[current].get("parent")
        return False

    def _build_full_record_ledger(self) -> dict[str, dict[str, Any]]:
        portal_errors: set[str] = {
            issue["portal_id"]
            for issue in self.issues
            if issue["severity"] == "error" and isinstance(issue.get("portal_id"), str)
        }
        ledger: dict[str, dict[str, Any]] = {}
        for rem_id, raw in self.index.items():
            owner = self._nearest_full_boundary(rem_id)
            source = self.source_map.get(rem_id)
            entry: dict[str, Any] = {
                "rem_id": rem_id,
                "document_id": owner,
                "canonical": source.get("canonical") if source else None,
                "occurrence_count": len(source.get("occurrences", [])) if source else 0,
            }
            if raw.get("type") == 6:
                if owner is None:
                    if self._is_evidenced_system_definition(rem_id):
                        entry.update(
                            disposition="excluded_system_definition",
                            reason="Per-record live SDK methods positively classified this exported record as a RemNote system definition.",
                        )
                    else:
                        entry.update(
                            disposition="unresolved_outside_document_boundary",
                            reason="Portal/query record lies outside every evidenced native Markdown document boundary; no system/content classification was guessed.",
                        )
                elif rem_id in portal_errors:
                    entry.update(
                        disposition="unresolved_required_view" if self._is_search_portal(raw) else "unresolved_portal",
                        reason="Portal output has one or more structured error issues; cached search results were not trusted.",
                    )
                else:
                    entry.update(
                        disposition="expanded_portal",
                        reason="Portal definition was represented by physical copied source occurrences.",
                    )
            elif source and source["occurrences"]:
                canonical_rendered = any(item["kind"] == "canonical" for item in source["occurrences"])
                source_title = self.plain_rich(raw.get("key")).strip().lower()
                if source_title.endswith(".pdf"):
                    disposition = "included_pdf_text_container"
                    reason = "Readable text and annotations were included; the PDF binary is absent from the structured export and was not copied."
                elif rem_id in self.files:
                    disposition = "included_document"
                    reason = "Native Markdown file boundary and structured source content were both preserved."
                elif canonical_rendered:
                    disposition = "included_source"
                    reason = "Structured source text was rendered at its canonical ownership location."
                else:
                    disposition = "included_portal_only"
                    reason = "Source is outside native document ownership but has one or more explicit portal appearances."
                entry.update(disposition=disposition, reason=reason)
            elif owner is None:
                if self._is_evidenced_system_definition(rem_id):
                    entry.update(
                        disposition="excluded_system_definition",
                        reason="Per-record live SDK methods positively classified this exported record as a RemNote system definition.",
                    )
                else:
                    entry.update(
                        disposition="unresolved_outside_document_boundary",
                        reason="Record lies outside every evidenced native Markdown document boundary; native-export absence alone is not proof that it is system metadata.",
                    )
            elif self._has_portal_ancestor(rem_id):
                content_bearing = bool(raw.get("key")) or bool(raw.get("value"))
                entry.update(
                    disposition="unresolved_portal_descendant",
                    reason=(
                        "Content-bearing record is owned below a portal but was not rendered; portal ancestry is not proof that it is implementation metadata."
                        if content_bearing
                        else "Empty portal-owned wrapper was not rendered and needs positive structural evidence before exclusion."
                    ),
                    content_bearing=content_bearing,
                )
            else:
                entry.update(
                    disposition="unresolved_missing_output",
                    reason="Record is inside an evidenced document boundary but has no rendered occurrence.",
                )
                self.issue(
                    "unexplained_missing_record",
                    "error",
                    "Record inside an evidenced document boundary has no output occurrence",
                    rem_id=rem_id,
                    details={"document_id": owner},
                )
            ledger[rem_id] = entry
        outside_count = sum(
            item["disposition"] == "unresolved_outside_document_boundary"
            for item in ledger.values()
        )
        if outside_count:
            self.issue(
                "unclassified_outside_document_boundary",
                "error",
                "Exported records outside native Markdown boundaries need explicit source/system classification",
                details={"record_count": outside_count},
            )
        portal_descendants = [
            item
            for item in ledger.values()
            if item["disposition"] == "unresolved_portal_descendant"
        ]
        if portal_descendants:
            content_count = sum(item["content_bearing"] for item in portal_descendants)
            self.issue(
                "unresolved_portal_descendants",
                "error",
                "Portal-owned records without rendered occurrences need supported membership/context evidence; none were assumed to be disposable implementation metadata",
                details={
                    "record_count": len(portal_descendants),
                    "content_bearing_count": content_count,
                    "empty_wrapper_count": len(portal_descendants) - content_count,
                },
            )
        return ledger

    def _full_split_review(self, written_files: list[dict[str, Any]]) -> dict[str, Any]:
        candidates = []
        for root, evidence in self.split_candidates.items():
            if root not in self.files:
                continue
            descendants = []
            for candidate in self.roots:
                if candidate == root:
                    continue
                current = self.index[candidate].get("parent")
                seen: set[str] = set()
                while isinstance(current, str) and current in self.index and current not in seen:
                    seen.add(current)
                    if current == root:
                        descendants.append({"rem_id": candidate, "path": self.files[candidate]})
                        break
                    current = self.index[current].get("parent")
            candidates.append({
                "rem_id": root,
                "path": self.files[root],
                "evidence": evidence,
                "existing_descendant_document_boundaries": sorted(descendants, key=lambda item: item["path"]),
            })
        return {
            "policy": "Native Markdown document boundaries are applied. Additional splits require explicitly configured, evidenced candidate IDs and are never chosen by a size threshold.",
            "plan_candidates": candidates,
            "file_size_review": sorted(
                ({"path": item["path"], "bytes": item["bytes"]} for item in written_files),
                key=lambda item: (-item["bytes"], item["path"]),
            ),
        }

    def _preflight_existing_output(self, output: Path) -> dict[str, Any] | None:
        if not output.exists():
            return None
        manifest_path = output / "manifest.json"
        if not manifest_path.exists():
            if any(output.iterdir()):
                raise ExportError("output directory is non-empty and has no converter manifest")
            return None
        try:
            prior = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ExportError(f"existing output manifest is invalid: {exc}") from exc
        if not isinstance(prior, dict) or not isinstance(prior.get("files"), list):
            raise ExportError("existing output manifest does not identify converter-owned files")
        for item in prior["files"]:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not isinstance(item.get("sha256"), str):
                raise ExportError("existing output manifest contains an invalid file entry")
            relative = PurePosixPath(item["path"])
            if relative.is_absolute() or ".." in relative.parts:
                raise ExportError("existing output manifest contains an unsafe file path")
            destination = output / relative
            if destination.exists() and hashlib.sha256(destination.read_bytes()).hexdigest() != item["sha256"]:
                raise ExportError(f"refusing to overwrite modified generated file: {item['path']}")
        return prior

    def _remove_stale_generated_files(
        self,
        output: Path,
        prior: dict[str, Any] | None,
        current_paths: set[str],
    ) -> None:
        if prior is None:
            return
        prior_paths = {item["path"] for item in prior["files"]}
        for relative in sorted(prior_paths - current_paths):
            destination = output / PurePosixPath(relative)
            if destination.exists():
                destination.unlink()
        prior_snapshot = prior.get("snapshot_contract")
        if self.snapshot_contract is None and isinstance(prior_snapshot, dict):
            sidecar = prior_snapshot.get("sidecar")
            expected_sha = prior_snapshot.get("sha256")
            if isinstance(sidecar, str) and isinstance(expected_sha, str):
                relative = PurePosixPath(sidecar)
                if relative.is_absolute() or ".." in relative.parts:
                    raise ExportError("existing output manifest contains an unsafe snapshot sidecar path")
                destination = output / relative
                if destination.exists():
                    if hashlib.sha256(destination.read_bytes()).hexdigest() != expected_sha:
                        raise ExportError("refusing to remove a modified generated snapshot sidecar")
                    destination.unlink()

    def convert(self, output: Path) -> dict[str, Any]:
        prior_manifest = self._preflight_existing_output(output)
        output.mkdir(parents=True, exist_ok=True)
        written_files = []
        for root in self.roots:
            if root not in self.files or self._budget_reported:
                continue
            relative = self.files[root]
            destination = output / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            raw = self.index[root]
            title = self.render_rich(raw.get("key"), RenderContext(relative, root, None, (), (root,)), root) or "Untitled"
            root_value = self.render_rich(raw.get("value"), RenderContext(relative, root, None, (), (root,)), root)
            anchor = self.canonical[root]["anchor"]
            root_ctx = RenderContext(relative, root, None, (root,), (root,))
            occurrence_text = f"{title} — {root_value}" if root_value else title
            if not self._record_occurrence(root, root_ctx, anchor, "canonical", occurrence_text):
                continue
            lines = [f"# {title}", ""]
            if root_value:
                if "\n" in root_value:
                    lines.extend(["—", "", root_value, ""])
                else:
                    lines.extend([f"— {root_value}", ""])
            lines.extend([f"^{anchor}", ""])
            for child in self.children.get(root, []):
                lines.extend(self._render_node(child, 0, root_ctx, "canonical"))
            body = "\n".join(lines).rstrip() + "\n"
            destination.write_text(body, encoding="utf-8")
            written_files.append({"root_id": root, "path": relative, "sha256": hashlib.sha256(body.encode()).hexdigest(), "bytes": len(body.encode("utf-8"))})
        self._remove_stale_generated_files(
            output,
            prior_manifest,
            {item["path"] for item in written_files},
        )
        rendered_targets = {
            (occurrence["file"], occurrence["anchor"])
            for entry in self.source_map.values()
            for occurrence in entry["occurrences"]
        }
        for rem_id, entry in self.source_map.items():
            canonical = entry["canonical"]
            if canonical and (canonical["file"], canonical["anchor"]) not in rendered_targets:
                self.issue("canonical_not_rendered", "error", "Manifest canonical target was not rendered", rem_id=rem_id, details=canonical)
                entry["canonical"] = None
        for rem_id in sorted(self._referenced_canonical):
            canonical = self.canonical[rem_id]
            if (canonical["file"], canonical["anchor"]) not in rendered_targets:
                self.issue("dangling_canonical_reference", "error", "Rendered reference points to a canonical target stopped by a pilot limit", rem_id=rem_id, details=canonical)
        record_ledger = self._build_full_record_ledger() if self.full_mode else None
        disposition_counts = Counter(item["disposition"] for item in record_ledger.values()) if record_ledger else Counter()
        manifest = {
            "schema_version": 2 if self.full_mode else 1,
            "converter_version": CONVERTER_VERSION,
            "mode": self.mode,
            "status": (
                "incomplete_full_migration"
                if self.full_mode and any(x["severity"] == "error" for x in self.issues)
                else "complete_full_migration"
                if self.full_mode
                else "incomplete"
                if any(x["severity"] == "error" for x in self.issues)
                else "complete_for_requested_pilot"
            ),
            "export": {
                "sha256": self.fingerprint,
                "exportVersion": self.payload.get("exportVersion"),
                "exportDate": self.payload.get("exportDate"),
                "knowledgebaseId": self.knowledgebase_id,
                "knowledgebase_identity_status": "known" if isinstance(self.knowledgebase_id, str) and bool(self.knowledgebase_id.strip()) else "unknown",
            },
            "configuration": {
                "roots": self.roots,
                "max_occurrences": self.max_occurrences,
                "max_depth": self.max_depth,
                "portal_snapshots": self.portal_snapshots,
                "visibility_overrides": self.visibility_overrides,
                "split_candidates": self.split_candidates,
            },
            "files": written_files,
            "source_map": self.source_map,
            "assets": self.assets,
            "issues": self.issues,
            "counts": {
                "raw_records": len(self.payload.get("docs", [])),
                "indexed_records": len(self.index),
                "occurrences": self._occurrences,
                "sources": len(self.source_map),
                "assets": len(self.assets),
                "issues": len(self.issues),
                "record_dispositions": dict(sorted(disposition_counts.items())),
            },
        }
        if self.full_mode:
            manifest["document_boundaries"] = self.boundary_evidence
            manifest["record_ledger"] = record_ledger
            manifest["omissions"] = {
                "pdf_binaries": "Not present in the .rem archive and intentionally not copied; readable source text remains included.",
                "flashcard_review": "Scheduling, review history, and flashcard mechanics are omitted; ordinary front/back text remains included.",
                "search_cache": "Cached searchResults are retained only as issue evidence and never treated as an authoritative view snapshot.",
            }
            manifest["split_review"] = self._full_split_review(written_files)
            manifest["snapshot_contract"] = (
                {
                    "schema_version": self.snapshot_contract.get("schema_version"),
                    "sha256": self.snapshot_contract_sha256,
                    "sidecar": "snapshot-contract.json",
                    "capture": self.snapshot_contract.get("capture"),
                }
                if self.snapshot_contract
                else {"status": "not_supplied"}
            )
        (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return manifest


def _parse_roots(values: list[str] | None) -> list[str]:
    result = []
    for value in values or []:
        result.extend(x.strip() for x in value.split(",") if x.strip())
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="RemNote .rem archive or rem.json")
    parser.add_argument("--output", required=True, type=Path, help="isolated staging output directory")
    parser.add_argument("--roots", action="append", help="explicit root ID(s), comma-separated or repeated")
    parser.add_argument("--config", type=Path, help="JSON config with roots, limits, evidence overrides, and full-export review candidates")
    parser.add_argument("--full", action="store_true", help="stage the full export using native Markdown document boundaries")
    parser.add_argument("--markdown-export", type=Path, help="native Markdown ZIP required by --full")
    parser.add_argument("--snapshot-contract", type=Path, help="optional remnote-migration-snapshot/v1 JSON")
    parser.add_argument("--max-occurrences", type=int, help=f"hard output cap (default {DEFAULT_MAX_OCCURRENCES})")
    parser.add_argument("--max-depth", type=int, help=f"hard traversal cap (default {DEFAULT_MAX_DEPTH})")
    args = parser.parse_args(argv)
    config: dict[str, Any] = {}
    if args.config:
        try:
            config = json.loads(args.config.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            parser.error(f"could not read config: {exc}")
        if not isinstance(config, dict):
            parser.error("config must be a JSON object")
    full_mode = args.full or config.get("mode") == "full"
    roots = _parse_roots(args.roots) or [str(x) for x in config.get("roots", [])]
    try:
        payload, fingerprint = load_export(args.input)
        file_map = None
        boundary_evidence = None
        markdown_export = args.markdown_export or (Path(config["markdown_export"]) if config.get("markdown_export") else None)
        if full_mode:
            if markdown_export is None:
                raise ExportError("--full requires --markdown-export or config.markdown_export")
            file_map, boundary_evidence = load_markdown_boundaries(payload, markdown_export)
        contract = None
        contract_sha256 = None
        snapshot_path = args.snapshot_contract or (Path(config["snapshot_contract"]) if config.get("snapshot_contract") else None)
        portal_snapshots = dict(config.get("portal_snapshots") or {})
        visibility_overrides = dict(config.get("visibility_overrides") or {})
        split_candidates = config.get("split_candidates") or {}
        if not isinstance(split_candidates, dict) or any(
            not isinstance(rem_id, str) or not isinstance(evidence, str) or not evidence.strip()
            for rem_id, evidence in split_candidates.items()
        ):
            raise ExportError("config.split_candidates must map Rem IDs to non-empty evidence strings")
        configured_kb_id = config.get("knowledgebase_id")
        if configured_kb_id is not None and (not isinstance(configured_kb_id, str) or not configured_kb_id.strip()):
            raise ExportError("config.knowledgebase_id must be a non-empty string")
        payload_kb_id = payload.get("knowledgebaseId")
        if (
            isinstance(payload_kb_id, str)
            and payload_kb_id.strip()
            and configured_kb_id is not None
            and payload_kb_id != configured_kb_id
        ):
            raise ExportError("config.knowledgebase_id does not match the export")
        if snapshot_path:
            contract, contract_sha256 = load_snapshot_contract(snapshot_path, payload, configured_kb_id)
            projection = contract["converter_projection"]
            for field, destination in (
                ("portal_snapshots", portal_snapshots),
                ("visibility_overrides", visibility_overrides),
            ):
                supplied = projection.get(field, {})
                if not isinstance(supplied, dict):
                    raise ExportError(f"snapshot converter_projection.{field} must be an object")
                for key, value in supplied.items():
                    if key in destination and destination[key] != value:
                        raise ExportError(f"conflicting {field} evidence for portal {key!r}")
                    destination[key] = value
        default_occurrences = DEFAULT_FULL_MAX_OCCURRENCES if full_mode else DEFAULT_MAX_OCCURRENCES
        default_depth = DEFAULT_FULL_MAX_DEPTH if full_mode else DEFAULT_MAX_DEPTH
        converter = Converter(
            payload,
            fingerprint,
            roots,
            max_occurrences=args.max_occurrences or int(config.get("max_occurrences", default_occurrences)),
            max_depth=args.max_depth or int(config.get("max_depth", default_depth)),
            portal_snapshots=portal_snapshots,
            visibility_overrides=visibility_overrides,
            file_map=file_map,
            mode="full" if full_mode else "pilot",
            boundary_evidence=boundary_evidence,
            snapshot_contract=contract,
            snapshot_contract_sha256=contract_sha256,
            knowledgebase_id=configured_kb_id,
            split_candidates=split_candidates,
        )
        manifest = converter.convert(args.output)
        if full_mode and contract is not None and snapshot_path is not None:
            (args.output / "snapshot-contract.json").write_bytes(snapshot_path.read_bytes())
    except (OSError, ExportError, ValueError) as exc:
        print(f"remnote_export: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"manifest": str(args.output / 'manifest.json'), "status": manifest["status"], "counts": manifest["counts"]}, sort_keys=True))
    return 2 if manifest["status"].startswith("incomplete") else 0


if __name__ == "__main__":
    raise SystemExit(main())
