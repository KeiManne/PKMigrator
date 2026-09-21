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

from portal_evidence import (
    AUTOMATIC_CONTENT_POLICY,
    ORDINARY_CONTENT_POLICY,
    PORTAL_EVIDENCE_SCHEMA,
    PORTAL_RENDER_POLICY,
    PortalEvidenceError,
    TABLE_ROW_CONTENT_POLICY,
    TABLE_SCHEMA_CONTENT_POLICY,
    derive_portal_evidence,
    portal_scope_policy_sha256,
    validate_portal_evidence_artifact,
)
from reference_metadata import ReferenceMetadataIndex, extract_reference_metadata


CONVERTER_VERSION = "0.4.0-evidenced-staging"
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


def load_source_comparison_receipt(
    path: Path,
    *,
    raw_export_sha256: str,
    snapshot_file_sha256: str,
    record_count: int,
) -> tuple[dict[str, Any], str]:
    """Load the private strict comparison receipt used to admit live portal evidence."""
    raw = path.read_bytes()
    try:
        receipt = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExportError(f"invalid source-comparison receipt: {exc}") from exc
    if not isinstance(receipt, dict) or receipt.get("report_schema") != "remnote-private-drift-full/v1":
        raise ExportError("unsupported source-comparison receipt schema")
    inputs = receipt.get("inputs")
    strict = receipt.get("strict_comparison")
    decision = receipt.get("decision")
    admission = receipt.get("capture_admission")
    if not all(isinstance(value, dict) for value in (inputs, strict, decision, admission)):
        raise ExportError("source-comparison receipt is missing required evidence sections")
    if (
        inputs.get("export_sha256") != raw_export_sha256
        or inputs.get("snapshot_sha256") != snapshot_file_sha256
        or inputs.get("export_records") != record_count
        or inputs.get("snapshot_records") != record_count
    ):
        raise ExportError("source-comparison receipt does not match the supplied export and snapshot")
    empty_mismatch_fields = (
        "identity_export_only",
        "identity_snapshot_only",
        "parent_mismatches",
        "created_at_mismatches",
        "rich_v2_mismatches",
    )
    if (
        decision.get("source_pair_exact") is not True
        or decision.get("fresh_export_required") is not False
        or decision.get("fresh_snapshot_required_for_source_drift") is not False
        or strict.get("helper_passed") is not True
        or strict.get("structural_and_content_exact") is not True
        or strict.get("helper_error") is not None
        or strict.get("child_mismatches") != {}
        or any(strict.get(field) != [] for field in empty_mismatch_fields)
        or admission.get("structural_pair_admissible") is not True
        or admission.get("knowledgebase_consistent") is not True
    ):
        raise ExportError("source-comparison receipt does not prove exact source identity, hierarchy, and rich text")
    return receipt, hashlib.sha256(raw).hexdigest()


def create_source_comparison_receipt(
    payload: dict[str, Any],
    raw_export_sha256: str,
    snapshot_contract: dict[str, Any],
    snapshot_file_sha256: str,
    *,
    reviewed_receipt_sha256: str | None = None,
) -> tuple[dict[str, Any], str]:
    """Recompute the strict source comparison used for portal admission."""
    exported = {
        raw["_id"]: raw
        for raw in payload.get("docs", [])
        if isinstance(raw, dict) and isinstance(raw.get("_id"), str)
    }
    records = snapshot_contract.get("records")
    if not isinstance(records, dict) or set(records) != set(exported):
        raise ExportError("strict source comparison requires identical raw and snapshot record ID sets")
    mismatches: Counter[str] = Counter()
    for rem_id, raw in exported.items():
        observed = records.get(rem_id)
        if not isinstance(observed, dict) or observed.get("id") != rem_id:
            mismatches["invalid_record"] += 1
            continue
        raw_parent = raw.get("parent") if isinstance(raw.get("parent"), str) else None
        if observed.get("parent_id") != raw_parent:
            mismatches["parent"] += 1
        if observed.get("created_at") != raw.get("createdAt"):
            mismatches["created_at"] += 1
        if observed.get("export_comparable_rich_text_fingerprint") != _snapshot_rich_fingerprint(raw):
            mismatches["rich_text"] += 1
    _, child_mismatches = _derive_snapshot_children(exported, records)
    mismatches.update(child_mismatches)
    if mismatches:
        summary = ", ".join(f"{field}={count}" for field, count in sorted(mismatches.items()))
        raise ExportError(f"strict source comparison failed ({summary})")
    receipt = {
        "schema_version": "pkmigrator-source-comparison/v1",
        "inputs": {
            "raw_export_sha256": raw_export_sha256,
            "snapshot_file_sha256": snapshot_file_sha256,
            "raw_record_count": len(exported),
            "snapshot_record_count": len(records),
            "reviewed_receipt_sha256": reviewed_receipt_sha256,
        },
        "comparison": {
            "identity_exact": True,
            "parent_exact": True,
            "created_at_exact": True,
            "rich_text_fingerprint_exact": True,
            "child_membership_complete": True,
            "unambiguous_fractional_child_order_exact": True,
            "ambiguous_fractional_order_source": "complete parent-consistent SDK child_ids",
            "rich_text_algorithm": RICH_FINGERPRINT_ALGORITHM,
            "child_order_raw_basis": CHILD_ORDER_BASIS,
        },
        "status": "complete",
    }
    encoded = (json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    return receipt, hashlib.sha256(encoded).hexdigest()


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


RICH_FINGERPRINT_ALGORITHM = "fnv1a64-canonical-richtext-v2-media-url"
CHILD_ORDER_BASIS = (
    "Raw siblings use null-first fractional f; every SDK child array must have complete "
    "parent-consistent membership; SDK order resolves null/tied f."
)
REMOTE_MEDIA_PREFIX = "https://remnote-user-data.s3.amazonaws.com/"
LOCAL_MEDIA_PREFIX = "%LOCAL_FILE%"
CANONICAL_MEDIA_PREFIX = "%REMNOTE_ASSET%"


def _canonical_rich_text_value(value: Any) -> Any:
    if isinstance(value, list):
        return [_canonical_rich_text_value(item) for item in value]
    if isinstance(value, dict):
        normalized = {key: _canonical_rich_text_value(item) for key, item in value.items()}
        url = value.get("url")
        if value.get("i") == "i" and isinstance(url, str):
            for prefix in (REMOTE_MEDIA_PREFIX, LOCAL_MEDIA_PREFIX):
                if url.startswith(prefix):
                    normalized["url"] = CANONICAL_MEDIA_PREFIX + url[len(prefix):]
                    break
        return normalized
    return value


def _snapshot_rich_fingerprint(raw: dict[str, Any]) -> str:
    serialized = json.dumps(
        _canonical_javascript_json_value(
            _canonical_rich_text_value([raw.get("key"), raw.get("value")])
        ),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return RICH_FINGERPRINT_ALGORITHM + ":" + _fnv1a64_javascript(serialized)


def _empty_rich_record(record: dict[str, Any], *, raw: bool) -> bool:
    return (
        record.get("key" if raw else "text") == []
        and record.get("value" if raw else "back_text") is None
    )


def _generated_context_identity_exemptions(
    contract: dict[str, Any],
    exported: dict[str, dict[str, Any]],
    records: dict[str, dict[str, Any]],
) -> tuple[set[str], set[str]]:
    portals = contract.get("portals")
    runtime = contract.get("runtime_returned_records")
    if not isinstance(portals, dict) or not isinstance(runtime, dict):
        return set(), set()
    export_only = set(exported) - set(records)
    snapshot_only = set(records) - set(exported)
    exported_children = Counter(
        raw.get("parent") for raw in exported.values() if isinstance(raw.get("parent"), str)
    )
    snapshot_children = Counter(
        record.get("parent_id") for record in records.values()
        if isinstance(record, dict) and isinstance(record.get("parent_id"), str)
    )
    empty_fingerprint = _snapshot_rich_fingerprint({"key": [], "value": None})
    ignored_export: set[str] = set()
    ignored_snapshot: set[str] = set()
    for parent_id, portal in portals.items():
        if not isinstance(portal, dict) or portal.get("portal_type_name") != "search_portal":
            continue
        parent_raw = exported.get(parent_id)
        parent_sdk = records.get(parent_id)
        nested = portal.get("nested_contexts")
        detected = set(nested.get("detected_ids", [])) if isinstance(nested, dict) else set()
        if (
            not isinstance(parent_raw, dict)
            or not isinstance(parent_sdk, dict)
            or parent_raw.get("portalType") != 4
            or parent_sdk.get("parent_id") != (
                parent_raw.get("parent") if isinstance(parent_raw.get("parent"), str) else None
            )
            or parent_sdk.get("export_comparable_rich_text_fingerprint")
            != _snapshot_rich_fingerprint(parent_raw)
        ):
            continue
        old_ids = {
            rem_id for rem_id in export_only
            if exported[rem_id].get("type") == 6
            and exported[rem_id].get("parent") == parent_id
            and "embeddedSearchId" in exported[rem_id]
            and "searchResults" in exported[rem_id]
            and _empty_rich_record(exported[rem_id], raw=True)
            and exported_children[rem_id] == 0
        }
        new_ids = {
            rem_id for rem_id in snapshot_only
            if records[rem_id].get("type") == 6
            and records[rem_id].get("parent_id") == parent_id
            and rem_id in detected
            and isinstance(runtime.get(rem_id), dict)
            and runtime[rem_id].get("type") == 6
            and runtime[rem_id].get("parent_id") == parent_id
            and runtime[rem_id].get("child_ids") == []
            and _empty_rich_record(runtime[rem_id], raw=False)
            and snapshot_children[rem_id] == 0
            and records[rem_id].get("export_comparable_rich_text_fingerprint") == empty_fingerprint
        }
        if old_ids and len(old_ids) == len(new_ids):
            ignored_export.update(old_ids)
            ignored_snapshot.update(new_ids)
    return ignored_export, ignored_snapshot


def _derive_snapshot_children(
    exported: dict[str, dict[str, Any]],
    records: dict[str, dict[str, Any]],
    ignored_export: set[str] | None = None,
    ignored_snapshot: set[str] | None = None,
) -> tuple[dict[str, list[str]], Counter[str]]:
    ignored_export = ignored_export or set()
    ignored_snapshot = ignored_snapshot or set()
    ordinal = {rem_id: position for position, rem_id in enumerate(exported)}
    children: dict[str, list[str]] = defaultdict(list)
    for rem_id, raw in exported.items():
        if rem_id in ignored_export:
            continue
        parent = raw.get("parent")
        if isinstance(parent, str) and parent in exported:
            children[parent].append(rem_id)
    mismatches: Counter[str] = Counter()
    for parent in exported:
        if parent in ignored_export:
            continue
        child_ids = children.get(parent, [])
        raw_order = sorted(
            child_ids,
            key=lambda rem_id: (
                exported[rem_id].get("f") is not None,
                str(exported[rem_id].get("f") or ""),
                ordinal[rem_id],
            ),
        )
        observed = records.get(parent, {}).get("child_ids")
        sdk_order = (
            [rem_id for rem_id in observed if rem_id not in ignored_snapshot]
            if isinstance(observed, list) and all(isinstance(rem_id, str) for rem_id in observed)
            else []
        )
        membership_complete = Counter(sdk_order) == Counter(raw_order)
        parent_consistent = membership_complete and all(
            records.get(rem_id, {}).get("parent_id") == parent for rem_id in sdk_order
        )
        f_values = [exported[rem_id].get("f") for rem_id in child_ids]
        unambiguous = all(value is not None for value in f_values) and len(set(f_values)) == len(f_values)
        if not parent_consistent:
            children[parent] = raw_order
            mismatches["child_order_unresolved"] += 1
        elif unambiguous:
            children[parent] = raw_order
            if sdk_order != raw_order:
                mismatches["child_order"] += 1
        else:
            children[parent] = sdk_order
    return dict(children), mismatches


def _validate_snapshot_drift(contract: dict[str, Any], payload: dict[str, Any]) -> None:
    records = contract.get("records")
    if not isinstance(records, dict):
        raise ExportError("snapshot contract needs a complete records object for export drift validation")
    exported = {
        raw["_id"]: raw
        for raw in payload.get("docs", [])
        if isinstance(raw, dict) and isinstance(raw.get("_id"), str)
    }
    ignored_export, ignored_snapshot = _generated_context_identity_exemptions(contract, exported, records)
    snapshot_ids = set(records) - ignored_snapshot
    export_ids = set(exported) - ignored_export
    if snapshot_ids != export_ids:
        raise ExportError(
            "snapshot/export record identities drifted "
            f"(missing from snapshot: {len(export_ids - snapshot_ids)}, new in snapshot: {len(snapshot_ids - export_ids)})"
        )
    comparison = contract.get("capture", {}).get("export_comparison")
    if (
        not isinstance(comparison, dict)
        or comparison.get("rich_text_algorithm") != RICH_FINGERPRINT_ALGORITHM
        or comparison.get("structural_fields") != ["id", "parent_id", "child_ids"]
        or comparison.get("child_order_raw_basis") != CHILD_ORDER_BASIS
        or comparison.get("raw_input_fields") != ["key", "value"]
        or comparison.get("sdk_input_fields") != ["text", "backText"]
    ):
        raise ExportError("snapshot contract lacks the supported export-comparable rich-text algorithm")
    compare_rich_text = comparison.get("calibrated_equivalent") is True
    compare_child_order = comparison.get("child_order_calibrated") is True
    mismatches: Counter[str] = Counter()
    for rem_id in sorted(export_ids):
        raw = exported[rem_id]
        observed = records.get(rem_id)
        if not isinstance(observed, dict) or observed.get("id") != rem_id:
            mismatches["invalid_record"] += 1
            continue
        raw_parent = raw.get("parent") if isinstance(raw.get("parent"), str) else None
        if observed.get("parent_id") != raw_parent:
            mismatches["parent"] += 1
        if compare_rich_text and observed.get("export_comparable_rich_text_fingerprint") != _snapshot_rich_fingerprint(raw):
            mismatches["rich_text"] += 1
    if compare_child_order:
        _, child_mismatches = _derive_snapshot_children(
            exported, records, ignored_export, ignored_snapshot
        )
        mismatches.update(child_mismatches)
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
    # Markdown inline-link delimiters must be encoded even when they are valid
    # URL characters; otherwise a literal closing parenthesis can end the link.
    return urllib.parse.quote(url, safe=":/?#@!$&'*+,;=%")


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
        exclude_subtree_roots: dict[str, str] | None = None,
        exclude_source_ids: dict[str, str] | None = None,
        document_plan: dict[str, dict[str, str]] | None = None,
        reference_metadata: dict[str, Any] | None = None,
        asset_omissions: dict[str, str] | None = None,
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
        self.native_file_map = dict(file_map or {})
        if document_plan is not None and (
            not isinstance(document_plan, dict)
            or any(not isinstance(rem_id, str) or not isinstance(entry, dict) for rem_id, entry in document_plan.items())
        ):
            raise ExportError("document_plan must map Rem IDs to path/evidence objects")
        self.document_plan = document_plan
        self.requested_file_map = (
            {rem_id: entry.get("path") for rem_id, entry in document_plan.items()}
            if document_plan is not None
            else dict(self.native_file_map)
        )
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
        self.exclude_subtree_roots = dict(exclude_subtree_roots or {})
        self.exclude_source_ids = dict(exclude_source_ids or {})
        self.reference_metadata_config = reference_metadata
        self.asset_omissions = dict(asset_omissions or {})
        for source, reason in self.asset_omissions.items():
            if not isinstance(source, str) or not source or not isinstance(reason, str) or not reason.strip():
                raise ExportError("asset_omissions must map non-empty HTTP(S) URLs to non-empty reviewed reasons")
            parsed = urllib.parse.urlparse(source)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.username is not None
                or parsed.password is not None
                or any(character.isspace() or character == "\\" for character in source)
            ):
                raise ExportError(f"asset_omissions contains an unsafe URL: {source!r}")
        self.index: dict[str, dict[str, Any]] = {}
        self.ownership_children: dict[str, list[str]] = defaultdict(list)
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
        self._generated_context_export_ids: set[str] = set()
        self._generated_context_snapshot_ids: set[str] = set()
        self._scope_exclusions: dict[str, dict[str, Any]] = {}
        self._system_exclusions: dict[str, dict[str, Any]] = {}
        self.reference_metadata_index: ReferenceMetadataIndex | None = None
        self._reference_materializations: list[dict[str, Any]] = []
        self._materialized_reference_edges: set[tuple[str, str]] = set()
        self._asset_omission_occurrences: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.portal_evidence: dict[str, Any] | None = None
        self._portal_locations: dict[str, dict[str, str]] = {}
        self._root_portal_entry_ids: set[str] = set()
        self._active_portal_ids: set[str] = set()
        self._portal_canonical_owners: dict[str, str] = {}
        self._rendered_portal_canonical: set[str] = set()
        self._automatic_policy_omitted_ids: set[str] = set()
        self._automatic_row_cell_ids: set[str] = set()
        self._portal_hidden_omitted_ids: set[str] = set()
        self._empty_table_schema_wrapper_ids: set[str] = set()
        self.source_comparison_receipt: dict[str, Any] | None = None
        self.source_comparison_receipt_sha256: str | None = None
        self.scope_policy_sha256 = portal_scope_policy_sha256({
            "exclude_subtree_roots": self.exclude_subtree_roots,
            "exclude_source_ids": self.exclude_source_ids,
            "document_plan": self.document_plan or {},
        })
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

    def admitted_portal_locations(self) -> dict[str, dict[str, str]]:
        """Return type-6 ownership nodes reachable from retained output documents."""
        locations: dict[str, dict[str, str]] = {}

        root_entries: set[str] = set()

        def visit(rem_id: str, root_id: str, seen: set[str], beneath_portal: bool) -> None:
            if rem_id in seen or rem_id not in self.index:
                return
            decision = self._scope_exclusions.get(rem_id)
            if decision and decision.get("kind") == "subtree":
                return
            if self._is_excluded(rem_id):
                for child in self.ownership_children.get(rem_id, []):
                    visit(child, root_id, seen | {rem_id}, beneath_portal)
                return
            if rem_id in self.files and rem_id != root_id:
                return
            if self.index[rem_id].get("type") == 6:
                location = {"file": self.files[root_id], "root_id": root_id}
                previous = locations.get(rem_id)
                if previous is not None and previous != location:
                    raise ExportError(f"portal {rem_id!r} is reachable from multiple output documents")
                locations[rem_id] = location
                if not beneath_portal:
                    root_entries.add(rem_id)
                beneath_portal = True
            for child in self.ownership_children.get(rem_id, []):
                visit(child, root_id, seen | {rem_id}, beneath_portal)

        for root_id in self.roots:
            if root_id not in self.files:
                continue
            for child in self.ownership_children.get(root_id, []):
                visit(child, root_id, {root_id}, False)
        self._root_portal_entry_ids = root_entries
        return locations

    def install_portal_evidence(
        self,
        evidence: dict[str, Any],
        *,
        source_comparison_receipt: dict[str, Any],
        source_comparison_receipt_sha256: str,
    ) -> None:
        """Validate and install immutable, hash-bound portal rendering plans."""
        locations = self.admitted_portal_locations()
        canonical_receipt = (
            json.dumps(source_comparison_receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        if hashlib.sha256(canonical_receipt).hexdigest() != source_comparison_receipt_sha256:
            raise ExportError("source-comparison receipt object does not match its supplied digest")
        if self.snapshot_contract is None:
            raise ExportError("portal evidence requires the loaded snapshot contract")
        expected_evidence = derive_portal_evidence(
            self.snapshot_contract,
            self.index,
            locations,
            set(self._scope_exclusions) | set(self._system_exclusions),
            raw_export_sha256=self.fingerprint,
            scope_policy_sha256=self.scope_policy_sha256,
            source_comparison_receipt_sha256=source_comparison_receipt_sha256,
            expected_knowledgebase_id=self.knowledgebase_id,
        )
        if evidence != expected_evidence:
            raise ExportError("portal evidence payload differs from a fresh derivation over current inputs")
        validate_portal_evidence_artifact(
            evidence,
            expected_knowledgebase_id=self.knowledgebase_id,
            expected_snapshot_payload_sha256=self.snapshot_contract.get("capture", {}).get("payload_sha256"),
            expected_raw_export_sha256=self.fingerprint,
            expected_scope_policy_sha256=self.scope_policy_sha256,
            expected_source_comparison_receipt_sha256=source_comparison_receipt_sha256,
            expected_raw_record_ids=self.index,
            expected_admitted_portal_ids=locations,
            expected_excluded_source_ids=set(self._scope_exclusions) | set(self._system_exclusions),
        )
        plans = evidence.get("plans")
        coverage = evidence.get("coverage")
        binding = evidence.get("binding")
        policy = evidence.get("render_policy")
        if evidence.get("schema_version") != PORTAL_EVIDENCE_SCHEMA:
            raise ExportError("unsupported portal evidence schema")
        if not all(isinstance(value, dict) for value in (plans, coverage, binding, policy)):
            raise ExportError("portal evidence is missing required sections")
        if (
            policy.get("policy_id") != PORTAL_RENDER_POLICY
            or policy.get("provenance") != "explicit-user-choice"
            or coverage.get("complete") is not True
            or coverage.get("unresolved_portal_ids") != []
            or evidence.get("diagnostics") != []
        ):
            raise ExportError("portal evidence is incomplete or uses an unapproved rendering policy")
        if set(plans) != set(locations):
            raise ExportError("portal evidence coverage does not exactly match retained output portals")
        if (
            binding.get("raw_export_sha256") != self.fingerprint
            or binding.get("snapshot_payload_sha256") != self.snapshot_contract.get("capture", {}).get("payload_sha256")
            or binding.get("scope_policy_sha256") != self.scope_policy_sha256
            or binding.get("source_comparison_receipt_sha256") != source_comparison_receipt_sha256
            or binding.get("knowledgebase_id") != self.knowledgebase_id
        ):
            raise ExportError("portal evidence binding does not match converter inputs")
        for portal_id, plan in plans.items():
            if not isinstance(plan, dict) or plan.get("complete") is not True:
                raise ExportError(f"portal evidence plan is incomplete for {portal_id!r}")
            expected_policy = (
                ORDINARY_CONTENT_POLICY
                if plan.get("kind") == "ordinary-content"
                else AUTOMATIC_CONTENT_POLICY
                if plan.get("kind") in {"contextual-search", "flat-search"}
                else None
            )
            appearances = plan.get("appearances")
            if expected_policy is None or not isinstance(appearances, list):
                raise ExportError(f"portal evidence plan has unsupported kind for {portal_id!r}")
            if any(not isinstance(item, dict) or item.get("content_policy") != expected_policy for item in appearances):
                raise ExportError(f"portal evidence appearance policy mismatch for {portal_id!r}")
            if plan.get("kind") == "flat-search":
                if plan.get("table_schema_complete") is not True:
                    raise ExportError(f"flat portal table-schema evidence is incomplete for {portal_id!r}")
                for item in plan.get("appearances", []):
                    if "owned_table_descendant_ids" in item and item.get("owned_table_content_policy") != TABLE_ROW_CONTENT_POLICY:
                        raise ExportError(f"flat portal row content policy mismatch for {portal_id!r}")
                schema_items = [
                    *plan.get("table_schema_roots", []),
                    *plan.get("omitted_empty_table_schema_wrappers", []),
                ]
                if any(
                    not isinstance(item, dict)
                    or item.get("content_policy") != TABLE_SCHEMA_CONTENT_POLICY
                    for item in schema_items
                ):
                    raise ExportError(f"flat portal table-schema policy mismatch for {portal_id!r}")
                visibility_states = plan.get("table_schema_visibility_states")
                hidden_schema_ids = plan.get("hidden_table_schema_record_ids")
                if not isinstance(visibility_states, dict) or not isinstance(hidden_schema_ids, list):
                    raise ExportError(f"flat portal table-schema visibility evidence is missing for {portal_id!r}")
                rendered_schema_ids = {
                    item["wrapper_id"]
                    for item in schema_items
                } | {
                    rem_id
                    for item in schema_items
                    for rem_id in item.get("ordered_label_ids", item.get("promoted_label_ids", []))
                }
                for rem_id in rendered_schema_ids:
                    state = visibility_states.get(rem_id)
                    if (
                        not isinstance(state, dict)
                        or state.get("explicit_hidden") is not False
                        or state.get("sdk_state") not in {None, "none", "included"}
                        or state.get("raw_ph_state") == "h"
                    ):
                        raise ExportError(f"flat portal table-schema visibility is unresolved for {rem_id!r}")
        self.portal_evidence = evidence
        self._portal_locations = locations
        self.source_comparison_receipt = source_comparison_receipt
        self.source_comparison_receipt_sha256 = source_comparison_receipt_sha256
        self._active_portal_ids = self._derive_active_portal_ids()
        self._assign_portal_canonical_sources()
        self._adjudicate_snapshot_source_issues()

    def _assign_portal_canonical_sources(self) -> None:
        assert self.portal_evidence is not None

        def assign_one(rem_id: str, portal_id: str, location: dict[str, str], context: str) -> None:
            if rem_id not in self.canonical:
                self.canonical[rem_id] = {
                    "file": location["file"],
                    "anchor": stable_anchor(rem_id, context),
                }
                self._portal_canonical_owners[rem_id] = portal_id

        def assign_tree(rem_id: str, portal_id: str, location: dict[str, str], states: dict[str, Any], path: tuple[str, ...], seen: set[str]) -> None:
            if rem_id in seen or rem_id not in self.index:
                return
            state = states.get(rem_id)
            if state not in {"none", "included", "root", "hidden"}:
                raise ExportError(f"portal visibility evidence is unresolved for retained record {rem_id!r}")
            if state == "hidden":
                pending = [rem_id]
                while pending:
                    hidden_id = pending.pop()
                    if hidden_id in self._portal_hidden_omitted_ids or hidden_id not in self.index:
                        continue
                    if self.index[hidden_id].get("type") != 6 and hidden_id not in self.canonical:
                        self._portal_hidden_omitted_ids.add(hidden_id)
                    pending.extend(self.children.get(hidden_id, []))
                return
            decision = self._scope_exclusions.get(rem_id)
            if decision and decision.get("kind") == "subtree":
                return
            if self._is_excluded(rem_id):
                for child in self.children.get(rem_id, []):
                    assign_tree(child, portal_id, location, states, path + (rem_id,), seen | {rem_id})
                return
            if self.index[rem_id].get("type") == 6 or (rem_id in self.files and rem_id != location["root_id"]):
                return
            if rem_id not in self.canonical:
                context = f"portal-canonical:{portal_id}:{'/'.join(path + (rem_id,))}"
                self.canonical[rem_id] = {
                    "file": location["file"],
                    "anchor": stable_anchor(rem_id, context),
                }
                self._portal_canonical_owners[rem_id] = portal_id
            for child in self.children.get(rem_id, []):
                assign_tree(child, portal_id, location, states, path + (rem_id,), seen | {rem_id})

        for portal_id in sorted(self._active_portal_ids):
            plan = self.portal_evidence["plans"][portal_id]
            location = self._portal_locations[portal_id]
            for appearance in plan["appearances"]:
                context_id = appearance["context_portal_id"]
                states = plan.get("visibility_contexts", {}).get(context_id, {})
                anchor_id = appearance["anchor_id"]
                if plan["kind"] == "ordinary-content":
                    assign_tree(anchor_id, portal_id, location, states, (), set())
                elif anchor_id not in self.canonical:
                    context = f"portal-canonical:{portal_id}:automatic:{anchor_id}"
                    self.canonical[anchor_id] = {
                        "file": location["file"],
                        "anchor": stable_anchor(anchor_id, context),
                    }
                    self._portal_canonical_owners[anchor_id] = portal_id
                owned_ids = appearance.get("owned_table_descendant_ids", [])
                hidden_ids = appearance.get("hidden_table_descendant_ids", [])
                descendant_states = appearance.get("owned_table_descendant_visibility_states", {})
                if owned_ids or hidden_ids:
                    if appearance.get("owned_table_content_policy") != TABLE_ROW_CONTENT_POLICY:
                        raise ExportError(f"flat table row policy is missing for {anchor_id!r}")
                    if not isinstance(descendant_states, dict):
                        raise ExportError(f"flat table row visibility map is malformed for {anchor_id!r}")
                    for descendant_id in owned_ids:
                        if descendant_states.get(descendant_id) not in {"none", "included"}:
                            raise ExportError(f"flat table row has unresolved visible descendant {descendant_id!r}")
                        assign_one(
                            descendant_id,
                            portal_id,
                            location,
                            f"portal-canonical:{portal_id}:table-row:{anchor_id}:{descendant_id}",
                        )
                        self._automatic_row_cell_ids.add(descendant_id)
                    for descendant_id in hidden_ids:
                        if descendant_states.get(descendant_id) != "hidden":
                            raise ExportError(f"flat table row has unresolved hidden descendant {descendant_id!r}")
                        self._portal_hidden_omitted_ids.add(descendant_id)

                pending = list(self.children.get(anchor_id, []))
                seen: set[str] = set()
                while pending:
                    descendant_id = pending.pop()
                    if descendant_id in seen or descendant_id not in self.index:
                        continue
                    seen.add(descendant_id)
                    if self.index[descendant_id].get("type") == 6:
                        continue
                    if (
                        descendant_id not in self.canonical
                        and descendant_id not in self._automatic_row_cell_ids
                        and not self._is_excluded(descendant_id)
                    ):
                        self._automatic_policy_omitted_ids.add(descendant_id)
                    pending.extend(self.children.get(descendant_id, []))

            if plan["kind"] == "flat-search":
                self._portal_hidden_omitted_ids.update(plan.get("hidden_table_schema_record_ids", []))
                for schema in plan.get("table_schema_roots", []):
                    wrapper_id = schema["wrapper_id"]
                    assign_one(
                        wrapper_id,
                        portal_id,
                        location,
                        f"portal-canonical:{portal_id}:table-schema:{wrapper_id}",
                    )
                    for label_id in schema["ordered_label_ids"]:
                        assign_one(
                            label_id,
                            portal_id,
                            location,
                            f"portal-canonical:{portal_id}:table-schema-label:{wrapper_id}:{label_id}",
                        )
                for schema in plan.get("omitted_empty_table_schema_wrappers", []):
                    wrapper_id = schema["wrapper_id"]
                    self._empty_table_schema_wrapper_ids.add(wrapper_id)
                    for label_id in schema["promoted_label_ids"]:
                        assign_one(
                            label_id,
                            portal_id,
                            location,
                            f"portal-canonical:{portal_id}:promoted-table-schema-label:{wrapper_id}:{label_id}",
                        )

    def _derive_active_portal_ids(self) -> set[str]:
        """Find plans that can actually render under the approved recursion policy."""
        assert self.portal_evidence is not None
        active = set(self._root_portal_entry_ids)

        def nested_portals(rem_id: str, seen: set[str]) -> set[str]:
            if rem_id in seen or rem_id not in self.index:
                return set()
            decision = self._scope_exclusions.get(rem_id)
            if decision and decision.get("kind") == "subtree":
                return set()
            if self._is_excluded(rem_id):
                result: set[str] = set()
                for child in self.children.get(rem_id, []):
                    result.update(nested_portals(child, seen | {rem_id}))
                return result
            if self.index[rem_id].get("type") == 6:
                return {rem_id}
            result = set()
            for child in self.children.get(rem_id, []):
                result.update(nested_portals(child, seen | {rem_id}))
            return result

        pending = list(active)
        while pending:
            portal_id = pending.pop()
            plan = self.portal_evidence["plans"][portal_id]
            if plan.get("kind") != "ordinary-content":
                continue
            discovered: set[str] = set()
            for appearance in plan["appearances"]:
                for child in self.children.get(appearance["anchor_id"], []):
                    discovered.update(nested_portals(child, {appearance["anchor_id"]}))
            new_ids = discovered - active
            active.update(new_ids)
            pending.extend(new_ids)
        return active

    def _adjudicate_snapshot_source_issues(self) -> None:
        if self.source_comparison_receipt_sha256 is None:
            return
        codes = {
            "snapshot_capture_incomplete",
            "snapshot_rich_text_comparison_uncalibrated",
            "snapshot_child_order_comparison_uncalibrated",
            "snapshot_scope_incomplete",
        }
        for issue in self.issues:
            if issue["code"] in codes and issue["severity"] == "error":
                issue["severity"] = "warning"
                issue["details"] = {
                    **issue.get("details", {}),
                    "offline_source_comparison_receipt_sha256": self.source_comparison_receipt_sha256,
                    "adjudication": "Source bodies, identity, parents, created timestamps, and ordered children were independently compared; original capture flags remain unchanged.",
                }

    def _snapshot_classification(self, name: str) -> dict[str, Any]:
        if not self.snapshot_contract:
            return {}
        classifications = self.snapshot_contract.get("classifications")
        if not isinstance(classifications, dict):
            return {}
        value = classifications.get(name)
        return value if isinstance(value, dict) else {}

    def _is_evidenced_system_definition(self, rem_id: str) -> bool:
        return bool(self._system_definition_evidence(rem_id))

    def _system_definition_evidence(self, rem_id: str) -> dict[str, Any]:
        classification = self._snapshot_classification("system_definition")
        states = classification.get("states")
        state = states.get(rem_id) if isinstance(states, dict) else None
        if not isinstance(state, dict):
            return {}
        fields = (
            "is_powerup",
            "is_powerup_enum",
            "is_powerup_property_list_item",
            "is_powerup_slot",
            "is_powerup_property",
        )
        positive = [field for field in fields if state.get(field) is True]
        if not positive:
            return {}
        return {
            "positive_predicates": positive,
            "method": classification.get("method"),
        }

    def _is_excluded(self, rem_id: str) -> bool:
        return rem_id in self._scope_exclusions or rem_id in self._system_exclusions

    def _prepare_reference_metadata(self) -> None:
        config = self.reference_metadata_config
        if config is None:
            return
        if not isinstance(config, dict):
            raise ExportError("reference_metadata must be a path-independent configuration object")
        roots = config.get("reviewed_root_ids")
        link_type_id = config.get("link_type_id")
        evidence = config.get("evidence")
        if (
            not isinstance(roots, list)
            or not roots
            or any(not isinstance(rem_id, str) or not rem_id for rem_id in roots)
            or len(set(roots)) != len(roots)
        ):
            raise ExportError("reference_metadata.reviewed_root_ids must be a non-empty unique string list")
        if not isinstance(link_type_id, str) or not link_type_id:
            raise ExportError("reference_metadata.link_type_id must be a non-empty Rem ID")
        if not isinstance(evidence, str) or not evidence.strip():
            raise ExportError("reference_metadata.evidence must be a non-empty reviewed reason")
        unknown = sorted(set(roots) - set(self.index))
        if unknown:
            raise ExportError("reference_metadata contains unknown reviewed roots: " + ", ".join(unknown))
        self.reference_metadata_index = extract_reference_metadata(
            self.index.values(), roots, link_type_id
        )

    @staticmethod
    def _rich_reference_targets(value: Any) -> set[str]:
        targets: set[str] = set()

        def visit(item: Any) -> None:
            if isinstance(item, list):
                for child in item:
                    visit(child)
            elif isinstance(item, dict):
                if item.get("i") == "q" and item.get("_id"):
                    targets.add(str(item["_id"]))
                    return
                for child in item.values():
                    if isinstance(child, (list, dict)):
                        visit(child)

        visit(value)
        return targets

    def _reference_metadata_report(self) -> dict[str, Any]:
        if self.reference_metadata_index is None:
            return {"status": "not_configured"}
        entries = self.reference_metadata_index.entries
        unresolved = self.reference_metadata_index.unresolved
        eligible_owners = (set(self.canonical) | set(self.source_map)) - (
            set(self._scope_exclusions) | set(self._system_exclusions)
        )
        expected_edges: set[tuple[str, str]] = set()
        unresolved_edges: set[tuple[str, str]] = set()
        for owner_id in eligible_owners:
            raw = self.index[owner_id]
            targets = self._rich_reference_targets(raw.get("key")) | self._rich_reference_targets(raw.get("value"))
            for target_id in targets:
                if target_id in entries:
                    expected_edges.add((owner_id, target_id))
                elif target_id in unresolved:
                    unresolved_edges.add((owner_id, target_id))
        missing_edges = expected_edges - self._materialized_reference_edges
        if unresolved_edges:
            self.issue(
                "reference_metadata_unresolved_edges",
                "error",
                "Retained source references target reviewed metadata records that did not produce safe external links",
                details={
                    "edge_count": len(unresolved_edges),
                    "target_count": len({target for _, target in unresolved_edges}),
                },
            )
        if missing_edges:
            self.issue(
                "reference_metadata_materialization_incomplete",
                "error",
                "Expected retained external-reference edges were not materialized in rendered output",
                details={
                    "edge_count": len(missing_edges),
                    "target_count": len({target for _, target in missing_edges}),
                },
            )

        def edges(items: set[tuple[str, str]]) -> list[dict[str, str]]:
            return [
                {"owner_id": owner_id, "target_id": target_id}
                for owner_id, target_id in sorted(items)
            ]

        return {
            "status": "complete" if not unresolved_edges and not missing_edges else "incomplete",
            "configuration": self.reference_metadata_config,
            "targets": {
                rem_id: {
                    "url": item.url,
                    "label": item.label,
                    "provenance": dict(item.provenance),
                }
                for rem_id, item in sorted(entries.items())
            },
            "unresolved": dict(sorted(unresolved.items())),
            "materializations": self._reference_materializations,
            "expected_retained_edges": edges(expected_edges),
            "materialized_retained_edges": edges(expected_edges & self._materialized_reference_edges),
            "missing_retained_edges": edges(missing_edges),
            "unresolved_retained_edges": edges(unresolved_edges),
            "counts": {
                "resolved_targets": len(entries),
                "unresolved_reviewed_records": len(unresolved),
                "materialization_appearances": len(self._reference_materializations),
                "expected_retained_edges": len(expected_edges),
                "materialized_retained_edges": len(expected_edges & self._materialized_reference_edges),
                "missing_retained_edges": len(missing_edges),
                "unresolved_retained_edges": len(unresolved_edges),
            },
        }

    def _prepare_exclusions(self) -> None:
        for field, configured in (
            ("exclude_subtree_roots", self.exclude_subtree_roots),
            ("exclude_source_ids", self.exclude_source_ids),
        ):
            if any(
                not isinstance(rem_id, str)
                or not rem_id
                or not isinstance(reason, str)
                or not reason.strip()
                for rem_id, reason in configured.items()
            ):
                raise ExportError(f"{field} must map non-empty Rem IDs to non-empty reasons")
            unknown = sorted(set(configured) - set(self.index))
            if unknown:
                raise ExportError(f"{field} contains unknown Rem IDs: {', '.join(unknown)}")

        for root_id, reason in self.exclude_subtree_roots.items():
            pending = [root_id]
            seen: set[str] = set()
            while pending:
                rem_id = pending.pop()
                if rem_id in seen:
                    continue
                seen.add(rem_id)
                existing = self._scope_exclusions.get(rem_id)
                if existing and existing["rule_id"] != root_id:
                    raise ExportError(
                        f"exclude_subtree_roots overlap at {rem_id!r}: "
                        f"{existing['rule_id']!r} and {root_id!r}"
                    )
                self._scope_exclusions[rem_id] = {
                    "kind": "subtree",
                    "rule_id": root_id,
                    "reason": reason.strip(),
                    "rule_root": rem_id == root_id,
                }
                pending.extend(self.ownership_children.get(rem_id, []))

        overlap = sorted(set(self.exclude_source_ids) & set(self._scope_exclusions))
        if overlap:
            raise ExportError(
                "exclude_source_ids overlap excluded subtrees: " + ", ".join(overlap)
            )
        for rem_id, reason in self.exclude_source_ids.items():
            self._scope_exclusions[rem_id] = {
                "kind": "source",
                "rule_id": rem_id,
                "reason": reason.strip(),
                "rule_root": True,
            }

        for rem_id in self.index:
            evidence = self._system_definition_evidence(rem_id)
            if evidence:
                self._system_exclusions[rem_id] = evidence

    def _validate_document_plan(self) -> None:
        if self.document_plan is None:
            return
        if not self.full_mode:
            raise ExportError("document_plan is supported only in full mode")
        if not self.document_plan:
            raise ExportError("document_plan must contain at least one retained output boundary")
        normalized_paths: dict[str, str] = {}
        for rem_id, entry in self.document_plan.items():
            path = entry.get("path")
            evidence = entry.get("evidence")
            if rem_id not in self.index:
                raise ExportError(f"document_plan contains unknown Rem ID: {rem_id!r}")
            if self.index[rem_id].get("type") == 6:
                raise ExportError(f"document_plan boundary cannot be a portal: {rem_id!r}")
            if self._is_excluded(rem_id):
                raise ExportError(f"document_plan includes an excluded source/system Rem ID: {rem_id!r}")
            if not isinstance(path, str) or not path:
                raise ExportError(f"document_plan entry {rem_id!r} needs a non-empty path")
            if not isinstance(evidence, str) or not evidence.strip():
                raise ExportError(f"document_plan entry {rem_id!r} needs non-empty evidence")
            relative = PurePosixPath(path)
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or relative.suffix.lower() != ".md"
                or relative.parts[:2] != ("Sources", "RemNote")
            ):
                raise ExportError(f"unsafe document_plan path for {rem_id!r}: {path!r}")
            for component in relative.parts[2:]:
                stem = PurePosixPath(component).stem if component == relative.name else component
                if (
                    not component
                    or component != component.strip(" .")
                    or re.search(r'[<>:"\\|?*#\[\]^\x00-\x1f]', component)
                    or stem.upper() in WINDOWS_RESERVED
                    or len(component.encode("utf-8")) > 240
                ):
                    raise ExportError(f"unsafe document_plan path component for {rem_id!r}: {component!r}")
            folded = unicodedata.normalize("NFKC", str(relative)).casefold()
            if folded in normalized_paths:
                raise ExportError(
                    f"duplicate document_plan path: {path!r} and {normalized_paths[folded]!r}"
                )
            normalized_paths[folded] = path

    def _retained_child_has_owner(self, excluded_id: str, child_id: str) -> bool:
        if child_id in self.files:
            return True
        current = self.index.get(excluded_id, {}).get("parent")
        seen: set[str] = set()
        while isinstance(current, str) and current in self.index and current not in seen:
            seen.add(current)
            if self.index[current].get("type") == 6:
                return False
            if current in self.files:
                return True
            current = self.index[current].get("parent")
        return False

    def _validate_retained_children_of_exact_exclusions(self) -> None:
        exact_ids = set(self.exclude_source_ids) | set(self._system_exclusions)
        for rem_id in sorted(exact_ids):
            for child_id in self.ownership_children.get(rem_id, []):
                if self._is_excluded(child_id):
                    continue
                if self._retained_child_has_owner(rem_id, child_id):
                    continue
                self.issue(
                    "excluded_parent_retained_child_unowned",
                    "error",
                    "An exact source/system exclusion has a retained child without a rendered output owner",
                    rem_id=child_id,
                    details={"excluded_parent_id": rem_id},
                )

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
        missing_boundaries = sorted(set(self.native_file_map) - set(states))
        conflicts = sorted(
            rem_id
            for rem_id in self.native_file_map
            if isinstance(states.get(rem_id), dict)
            and states[rem_id].get("is_document") is not True
            and states[rem_id].get("is_folder") is not True
        )
        snapshot_only_boundaries = sorted(
            rem_id
            for rem_id, state in states.items()
            if rem_id in self.index
            and rem_id not in self.native_file_map
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
        reviewed_conflicts = [
            rem_id for rem_id in conflicts
            if self._is_excluded(rem_id) or (self.document_plan is not None and rem_id in self.document_plan)
        ]
        unresolved_conflicts = sorted(set(conflicts) - set(reviewed_conflicts))
        if reviewed_conflicts:
            self.issue(
                "document_boundary_classification_reviewed_override",
                "warning",
                "Native Markdown boundary evidence was retained or explicitly excluded by the reviewed document/scope plan despite a live SDK false classification",
                details={"record_count": len(reviewed_conflicts)},
            )
        if unresolved_conflicts:
            self.issue(
                "document_boundary_classification_conflict",
                "error",
                "Live SDK classification disagrees with native Markdown boundary evidence",
                details={"conflict_count": len(unresolved_conflicts)},
            )
        reviewed_snapshot_only = [
            rem_id for rem_id in snapshot_only_boundaries
            if self._is_excluded(rem_id)
            or (self.document_plan is not None and rem_id in self.document_plan)
            or (
                self.document_plan is not None
                and self._nearest_full_boundary(rem_id) is not None
            )
        ]
        unresolved_snapshot_only = sorted(set(snapshot_only_boundaries) - set(reviewed_snapshot_only))
        if reviewed_snapshot_only:
            self.issue(
                "snapshot_boundary_reviewed_disposition",
                "warning",
                "Live-only document/folder classification has an explicit reviewed output-boundary or scope disposition",
                details={"record_count": len(reviewed_snapshot_only)},
            )
        if unresolved_snapshot_only:
            self.issue(
                "snapshot_boundary_missing_from_native_export",
                "error",
                "Live SDK reports document/folder records with no matched native Markdown file",
                details={"record_count": len(unresolved_snapshot_only)},
            )

    def _validate_snapshot_scope(self) -> None:
        if not self.snapshot_contract:
            return
        capture = self.snapshot_contract.get("capture", {})
        scope = capture.get("scope")
        portals = self.snapshot_contract.get("portals")
        records = self.snapshot_contract.get("records")
        ignored_export, ignored_snapshot = (
            _generated_context_identity_exemptions(self.snapshot_contract, self.index, records)
            if isinstance(records, dict)
            else (set(), set())
        )
        exported_portals = {
            rem_id for rem_id, raw in self.index.items()
            if raw.get("type") == 6 and rem_id not in ignored_export
        }
        captured_portals = (
            set(portals) - ignored_snapshot if isinstance(portals, dict) else set()
        )
        expected_portal_count = scope.get("expected_portal_count") if isinstance(scope, dict) else None
        processed_portal_count = scope.get("processed_portal_count") if isinstance(scope, dict) else None
        scope_complete = (
            capture.get("mode") == "complete"
            and capture.get("knowledgebase_consistent") is True
            and capture.get("knowledgebase_id_at_end") == capture.get("knowledgebase_id")
            and isinstance(scope, dict)
            and isinstance(expected_portal_count, int)
            and expected_portal_count - len(ignored_snapshot) == len(exported_portals)
            and isinstance(processed_portal_count, int)
            and processed_portal_count - len(ignored_snapshot) == len(exported_portals)
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
                self.ownership_children[parent].append(rem_id)
        for parent, ids in self.ownership_children.items():
            ids.sort(key=lambda rid: (str(self.index[rid].get("f", "~")), order[rid]))
        if self.snapshot_contract is not None and isinstance(self.snapshot_contract.get("records"), dict):
            records = self.snapshot_contract["records"]
            ignored_export, ignored_snapshot = _generated_context_identity_exemptions(
                self.snapshot_contract, self.index, records
            )
            self._generated_context_export_ids = ignored_export
            self._generated_context_snapshot_ids = ignored_snapshot
            derived, order_mismatches = _derive_snapshot_children(
                self.index, records, ignored_export, ignored_snapshot
            )
            self.children.update(derived)
            if order_mismatches.get("child_order"):
                self.issue(
                    "snapshot_child_order_conflict",
                    "error",
                    "Live SDK child order disagrees with unambiguous raw fractional order",
                    details={"parent_count": order_mismatches["child_order"]},
                )
            if order_mismatches.get("child_order_unresolved"):
                self.issue(
                    "snapshot_child_order_unresolved",
                    "error",
                    "SDK child membership is incomplete or parent-inconsistent, so calibrated sibling order cannot be established",
                    details={"parent_count": order_mismatches["child_order_unresolved"]},
                )
        else:
            self.children.update({parent: list(ids) for parent, ids in self.ownership_children.items()})
        self._prepare_exclusions()
        self._prepare_reference_metadata()
        self._validate_document_plan()
        normalized_paths: dict[str, str] = {}
        for root in self.roots:
            if root not in self.index:
                self.issue("missing_root", "error", "Requested root is absent from export", rem_id=root)
                continue
            if self.index[root].get("type") == 6:
                self.issue("portal_root_unsupported", "error", "A pilot root must be a source/document Rem, not a portal", rem_id=root, portal_id=root)
                continue
            if self._is_excluded(root):
                continue
            if self.full_mode:
                relative = PurePosixPath(self.requested_file_map[root])
                if relative.is_absolute() or ".." in relative.parts or relative.suffix.lower() != ".md":
                    raise ExportError(f"unsafe full-export output path for {root!r}")
                folded = unicodedata.normalize("NFKC", str(relative)).casefold()
                if folded in normalized_paths:
                    raise ExportError(
                        f"duplicate full-export output path: {str(relative)!r} and {normalized_paths[folded]!r}"
                    )
                normalized_paths[folded] = str(relative)
                self.files[root] = str(relative)
            else:
                title = self.plain_rich(self.index[root].get("key")) or "Untitled"
                self.files[root] = f"notes/{safe_stem(title, root)}.md"
        if self.full_mode:
            self._validate_retained_children_of_exact_exclusions()
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
        if self._is_excluded(rem_id):
            decision = self._scope_exclusions.get(rem_id)
            if decision and decision["kind"] == "subtree":
                return
            next_seen = seen | {rem_id}
            for child in self.children.get(rem_id, []):
                self._assign_canonical(child, root, path + (rem_id,), next_seen)
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
                    metadata = (
                        self.reference_metadata_index.resolve_reference(target_id)
                        if self.reference_metadata_index is not None
                        else None
                    )
                    if metadata is not None:
                        result.append(f"{metadata.label} ({metadata.url})")
                        continue
                    if target_id in self._scope_exclusions:
                        result.append("[excluded source]")
                        continue
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
                metadata = (
                    self.reference_metadata_index.resolve_reference(target_id)
                    if self.reference_metadata_index is not None
                    else None
                )
                if metadata is not None:
                    rendered.append(f"[{_escape_markdown(metadata.label)}]({_url(metadata.url)})")
                    self._materialized_reference_edges.add((owner_id, target_id))
                    self._reference_materializations.append({
                        "owner_id": owner_id,
                        "target_id": target_id,
                        "file": ctx.file,
                        "portal_id": ctx.portal_id,
                        "path": list(ctx.appearance_path),
                        "provenance": dict(metadata.provenance),
                    })
                    continue
                fallback_label = self._plain_rich(part.get("textOfDeletedRem"), set()).strip()
                if target_id in self._scope_exclusions:
                    rendered.append("[excluded source]")
                    self.issue(
                        "excluded_reference",
                        "warning",
                        "Reference target was intentionally excluded; no target text or link was emitted",
                        rem_id=owner_id,
                        path=ctx.appearance_path,
                        details={"target_id": target_id},
                    )
                    continue
                label = self.plain_rich(self.index.get(target_id, {}).get("key")) or fallback_label or target_id
                if target_id in self._system_exclusions:
                    rendered.append(_escape_markdown(label))
                    self.issue(
                        "system_reference_label_only",
                        "warning",
                        "Reference target is an SDK-classified system definition without a canonical note; its visible label was preserved",
                        rem_id=target_id,
                        details={"target_id": target_id},
                    )
                    continue
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
                if source in self.asset_omissions:
                    label = str(part.get("alt") or part.get("title") or "image").strip() or "image"
                    rendered.append(f"[Image unavailable: {_escape_markdown(label)}]({_url(source)})")
                    self._asset_omission_occurrences[source].append({
                        "rem_id": owner_id,
                        "file": ctx.file,
                        "portal_id": ctx.portal_id,
                        "path": list(ctx.appearance_path),
                    })
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
        if self.portal_evidence is not None:
            plan = self.portal_evidence["plans"].get(portal_id)
            states = plan.get("visibility_contexts", {}).get(portal_id, {}) if isinstance(plan, dict) else {}
            state = states.get(target_id)
            if state == "hidden":
                return False
            if state in {"none", "included", "root"}:
                return True
            self.issue(
                "portal_evidence_visibility_missing",
                "error",
                "Installed portal evidence lacks a resolved visibility state for rendered content",
                rem_id=target_id,
                portal_id=portal_id,
                path=ctx.appearance_path,
                details={"state": state},
            )
            return False
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

    @staticmethod
    def _bullet_lines(text: str, level: int, anchor: str) -> list[str]:
        prefix = "  " * level
        if "\n" in text:
            parts = text.splitlines()
            lines = [f"{prefix}- {parts[0] or '  '}"]
            lines.extend(f"{prefix}  {part}" for part in parts[1:])
            lines.append(f"{prefix}  ^{anchor}")
            return lines
        return [f"{prefix}- {text or '[empty]'} ^{anchor}"]

    def _render_node(self, rem_id: str, level: int, ctx: RenderContext, kind: str) -> list[str]:
        if self._budget_reported:
            return []
        if len(ctx.source_path) >= self.max_depth:
            self.issue("depth_limit", "error", "Conversion traversal depth limit reached", rem_id=rem_id, portal_id=ctx.portal_id, path=ctx.appearance_path, details={"max_depth": self.max_depth})
            return []
        if rem_id not in self.index:
            self.issue("missing_target", "error", "Portal or ownership target is absent", rem_id=rem_id, portal_id=ctx.portal_id, path=ctx.appearance_path)
            return []
        if (
            any(component.startswith("automatic-row:") for component in ctx.appearance_path)
            and rem_id not in self._automatic_row_cell_ids
        ):
            return []
        early_exclusion = self._scope_exclusions.get(rem_id)
        if early_exclusion and early_exclusion["kind"] == "subtree":
            return []
        if rem_id in ctx.source_path:
            self.issue("cycle", "warning", "Recursive branch stopped at a per-path cycle", rem_id=rem_id, portal_id=ctx.portal_id, path=ctx.appearance_path)
            return [self._cycle_line(rem_id, level, ctx)]
        raw = self.index[rem_id]
        next_ctx = RenderContext(ctx.file, ctx.root_id, ctx.portal_id, ctx.source_path + (rem_id,), ctx.appearance_path + (rem_id,))
        if self._is_excluded(rem_id):
            lines: list[str] = []
            for child in self.children.get(rem_id, []):
                lines.extend(self._render_node(child, level, next_ctx, kind))
            return lines
        if ctx.portal_id and not self._visibility(ctx.portal_id, rem_id, self.index[ctx.portal_id], ctx):
            return []
        if raw.get("type") == 6 and any(
            component.startswith("automatic-row:") for component in ctx.appearance_path
        ):
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
        if (
            kind == "portal-copy"
            and self._portal_canonical_owners.get(rem_id) == ctx.portal_id
            and rem_id not in self._rendered_portal_canonical
        ):
            kind = "portal-source-canonical"
        if kind in {"canonical", "portal-source-canonical"} and self.canonical.get(rem_id, {}).get("file") == ctx.file:
            anchor = self.canonical[rem_id]["anchor"]
        else:
            anchor = stable_anchor(rem_id, context)
        if not self._record_occurrence(rem_id, next_ctx, anchor, kind, text):
            return []
        if kind == "portal-source-canonical":
            self._rendered_portal_canonical.add(rem_id)
        lines = self._bullet_lines(text, level, anchor)
        for child in self.children.get(rem_id, []):
            lines.extend(self._render_node(child, level + 1, next_ctx, "portal-copy" if ctx.portal_id else "canonical"))
        return lines

    def _render_portal(self, portal_id: str, raw: dict[str, Any], level: int, ctx: RenderContext) -> list[str]:
        if self.portal_evidence is not None:
            plan = self.portal_evidence["plans"][portal_id]
            lines: list[str] = []
            if plan["kind"] == "ordinary-content":
                for appearance in plan["appearances"]:
                    context_id = appearance["context_portal_id"]
                    portal_ctx = RenderContext(
                        ctx.file,
                        ctx.root_id,
                        context_id,
                        ctx.source_path,
                        ctx.appearance_path + (f"portal:{portal_id}",),
                    )
                    lines.extend(self._render_node(appearance["anchor_id"], level, portal_ctx, "portal-copy"))
                return lines
            for appearance in plan["appearances"]:
                lines.extend(self._render_automatic_match(portal_id, appearance, level, ctx))
            if plan["kind"] == "flat-search":
                lines.extend(self._render_table_schema(portal_id, plan, level, ctx))
            return lines
        lines = []
        targets = self._portal_targets(portal_id, raw, ctx)
        for target_id in targets:
            portal_ctx = RenderContext(ctx.file, ctx.root_id, portal_id, ctx.source_path, ctx.appearance_path + (f"portal:{portal_id}",))
            lines.extend(self._render_node(target_id, level, portal_ctx, "portal-copy"))
        return lines

    def _render_table_schema(
        self,
        portal_id: str,
        plan: dict[str, Any],
        level: int,
        ctx: RenderContext,
    ) -> list[str]:
        schema_roots = plan.get("table_schema_roots", [])
        omitted_wrappers = plan.get("omitted_empty_table_schema_wrappers", [])
        if not schema_roots and not omitted_wrappers:
            return []
        prefix = "  " * level
        lines = [f"{prefix}- **Columns**"]
        schema_ctx = RenderContext(
            ctx.file,
            ctx.root_id,
            portal_id,
            ctx.source_path,
            ctx.appearance_path + (f"portal:{portal_id}", "table-schema"),
        )
        for schema in schema_roots:
            wrapper_id = schema["wrapper_id"]
            lines.extend(self._render_evidenced_record(wrapper_id, level + 1, schema_ctx, portal_id))
            wrapper_ctx = RenderContext(
                schema_ctx.file,
                schema_ctx.root_id,
                schema_ctx.portal_id,
                schema_ctx.source_path + (wrapper_id,),
                schema_ctx.appearance_path + (wrapper_id,),
            )
            for label_id in schema["ordered_label_ids"]:
                lines.extend(self._render_evidenced_record(label_id, level + 2, wrapper_ctx, portal_id))
        for schema in omitted_wrappers:
            for label_id in schema["promoted_label_ids"]:
                lines.extend(self._render_evidenced_record(label_id, level + 1, schema_ctx, portal_id))
        return lines

    def _render_evidenced_record(
        self,
        rem_id: str,
        level: int,
        ctx: RenderContext,
        portal_id: str,
    ) -> list[str]:
        raw = self.index[rem_id]
        next_ctx = RenderContext(
            ctx.file,
            ctx.root_id,
            portal_id,
            ctx.source_path + (rem_id,),
            ctx.appearance_path + (rem_id,),
        )
        text = self.render_rich(raw.get("key"), next_ctx, rem_id)
        value = self.render_rich(raw.get("value"), next_ctx, rem_id)
        if value:
            text = f"{text} — {value}" if text else value
        canonical = self.canonical[rem_id]
        if self._portal_canonical_owners.get(rem_id) == portal_id and rem_id not in self._rendered_portal_canonical:
            anchor = canonical["anchor"]
            kind = "portal-source-canonical"
        else:
            anchor = stable_anchor(rem_id, f"portal-schema:{portal_id}:{'/'.join(next_ctx.appearance_path)}")
            kind = "portal-schema"
        if not self._record_occurrence(rem_id, next_ctx, anchor, kind, text):
            return []
        if kind == "portal-source-canonical":
            self._rendered_portal_canonical.add(rem_id)
        return self._bullet_lines(text, level, anchor)

    def _render_automatic_match(
        self,
        portal_id: str,
        appearance: dict[str, Any],
        level: int,
        ctx: RenderContext,
    ) -> list[str]:
        rem_id = appearance["anchor_id"]
        if rem_id not in self.index or self._is_excluded(rem_id):
            self.issue(
                "automatic_portal_anchor_unavailable",
                "error",
                "Evidenced automatic-view anchor is unavailable after scope processing",
                rem_id=rem_id,
                portal_id=portal_id,
                path=ctx.appearance_path,
            )
            return []
        next_ctx = RenderContext(
            ctx.file,
            ctx.root_id,
            portal_id,
            ctx.source_path + (rem_id,),
            ctx.appearance_path + (
                f"portal:{portal_id}",
                f"context:{appearance['context_portal_id']}",
                rem_id,
            ),
        )
        raw = self.index[rem_id]
        text = self.render_rich(raw.get("key"), next_ctx, rem_id)
        value = self.render_rich(raw.get("value"), next_ctx, rem_id)
        if value:
            text = f"{text} — {value}" if text else value
        canonical = self.canonical.get(rem_id)
        if canonical is None:
            self.issue(
                "automatic_portal_anchor_without_canonical",
                "error",
                "Automatic-view match has no canonical output target",
                rem_id=rem_id,
                portal_id=portal_id,
                path=next_ctx.appearance_path,
            )
            return []
        path_ids = appearance.get("canonical_source_path_ids")
        if not isinstance(path_ids, list) or not path_ids or path_ids[-1] != rem_id:
            self.issue(
                "automatic_portal_source_path_invalid",
                "error",
                "Automatic-view match lacks its evidenced canonical source path",
                rem_id=rem_id,
                portal_id=portal_id,
                path=next_ctx.appearance_path,
            )
            return []
        labels = [
            self.plain_rich(self.index[path_id].get("key")).strip()
            for path_id in path_ids[:-1]
            if path_id in self.index and not self._is_excluded(path_id)
        ]
        labels = [label for label in labels if label]
        final_label = self.plain_rich(raw.get("key")).strip() or rem_id
        source_link = f"[[{canonical['file']}#^{canonical['anchor']}|{_escape_markdown(final_label)}]]"
        source_path = " / ".join([*(_escape_markdown(label) for label in labels), source_link])
        occurrence_kind = "portal-match"
        if self._portal_canonical_owners.get(rem_id) == portal_id and rem_id not in self._rendered_portal_canonical:
            anchor = canonical["anchor"]
            occurrence_kind = "portal-source-canonical"
        else:
            anchor = stable_anchor(rem_id, f"portal-match:{portal_id}:{'/'.join(next_ctx.appearance_path)}")
            self._referenced_canonical.add(rem_id)
        occurrence_text = f"{text}\nSource: {source_path}"
        if not self._record_occurrence(rem_id, next_ctx, anchor, occurrence_kind, occurrence_text):
            return []
        if occurrence_kind == "portal-source-canonical":
            self._rendered_portal_canonical.add(rem_id)
        lines = self._bullet_lines(text, level, anchor)
        prefix = "  " * level
        lines.append(f"{prefix}  Source: {source_path}")
        if self.index[rem_id].get("type") == 1:
            row_ctx = RenderContext(
                next_ctx.file,
                next_ctx.root_id,
                next_ctx.portal_id,
                next_ctx.source_path,
                next_ctx.appearance_path + (f"automatic-row:{rem_id}",),
            )
            for child_id in self.children.get(rem_id, []):
                if child_id in self._automatic_row_cell_ids:
                    lines.extend(self._render_node(child_id, level + 1, row_ctx, "portal-copy"))
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
            if rem_id in self._system_exclusions:
                entry.update(
                    disposition="excluded_system_definition",
                    reason="Per-record live SDK methods positively classified this exact record as a RemNote system definition; classification was not propagated to children.",
                    system_definition_evidence=self._system_exclusions[rem_id],
                )
                if rem_id in self._scope_exclusions:
                    entry["scope_exclusion"] = self._scope_exclusions[rem_id]
            elif rem_id in self._scope_exclusions:
                entry.update(
                    disposition="excluded_by_scope",
                    reason="User-approved source scope excluded this identity from canonical output, portal copies, assets, and retrieval.",
                    scope_exclusion=self._scope_exclusions[rem_id],
                )
            elif rem_id in self._generated_context_export_ids:
                entry.update(
                    disposition="excluded_generated_search_context",
                    reason="Paired export/snapshot/runtime evidence identifies this empty type-6 record as a replaced generated search-context node.",
                )
            elif raw.get("type") == 6:
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
                    if self.portal_evidence is not None and rem_id not in self._active_portal_ids:
                        entry.update(
                            disposition="evidenced_automatic_context_portal",
                            reason="Portal is an evidenced nested automatic-view context; the approved match-only parent plan represents its result without recursively rendering the context wrapper.",
                        )
                    else:
                        entry.update(
                            disposition="expanded_portal",
                            reason="Portal definition was represented by physical copied source occurrences.",
                        )
            elif source and source["occurrences"]:
                canonical_rendered = any(
                    item["kind"] in {"canonical", "portal-source-canonical"}
                    for item in source["occurrences"]
                )
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
            elif rem_id in self._empty_table_schema_wrapper_ids:
                entry.update(
                    disposition="omitted_empty_table_schema_wrapper",
                    reason="Signed portal evidence proved this table-schema wrapper has no authored key/value, no inbound rich-text references, and only promoted authored label children.",
                )
            elif rem_id in self._portal_hidden_omitted_ids:
                entry.update(
                    disposition="omitted_explicitly_hidden_portal_content",
                    reason="Live portal visibility evidence explicitly marked this portal-local branch hidden; the source identity remains available wherever it has a separate canonical occurrence.",
                )
            elif rem_id in self._automatic_policy_omitted_ids:
                entry.update(
                    disposition="omitted_by_automatic_match_policy",
                    reason="The explicit user-approved automatic-view policy preserves the matched bullet and canonical source path/link without recursively copying its descendants.",
                )
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
        for section_name in (
            "snapshot_contract",
            "portal_evidence",
            "source_comparison_receipt",
        ):
            section = prior.get(section_name)
            if not isinstance(section, dict):
                continue
            sidecar = section.get("sidecar")
            expected_sha = section.get("sha256")
            if not isinstance(sidecar, str) or not isinstance(expected_sha, str):
                continue
            relative = PurePosixPath(sidecar)
            if relative.is_absolute() or ".." in relative.parts:
                raise ExportError(f"existing output manifest contains an unsafe {section_name} sidecar path")
            destination = output / relative
            if destination.exists() and hashlib.sha256(destination.read_bytes()).hexdigest() != expected_sha:
                raise ExportError(f"refusing to overwrite modified generated sidecar: {sidecar}")
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
        excluded_ids = set(self._scope_exclusions) | set(self._system_exclusions)
        leaked_sources = sorted(excluded_ids & set(self.source_map))
        leaked_asset_occurrences = sorted({
            occurrence.get("rem_id")
            for asset in self.assets.values()
            for occurrence in asset.get("occurrences", [])
            if isinstance(occurrence, dict) and occurrence.get("rem_id") in excluded_ids
        })
        if leaked_sources or leaked_asset_occurrences:
            self.issue(
                "scope_exclusion_leak",
                "error",
                "Excluded identities appeared in rendered source or asset output",
                details={
                    "source_count": len(leaked_sources),
                    "asset_occurrence_source_count": len(leaked_asset_occurrences),
                },
            )
        reference_metadata_report = self._reference_metadata_report()
        for source in sorted(set(self.asset_omissions) - set(self._asset_omission_occurrences)):
            self.issue(
                "configured_asset_omission_unused",
                "error",
                "Configured unavailable-asset evidence did not match any rendered image occurrence",
                details={"source": source},
            )
        asset_omission_report = {
            source: {
                "reason": self.asset_omissions[source],
                "occurrences": occurrences,
                "occurrence_count": len(occurrences),
                "rendering": "external-unavailable-marker",
            }
            for source, occurrences in sorted(self._asset_omission_occurrences.items())
        }
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
                "exclude_subtree_roots": self.exclude_subtree_roots,
                "exclude_source_ids": self.exclude_source_ids,
                "document_plan": self.document_plan,
                "reference_metadata": self.reference_metadata_config,
                "asset_omissions": self.asset_omissions,
            },
            "files": written_files,
            "source_map": self.source_map,
            "assets": self.assets,
            "reference_metadata": reference_metadata_report,
            "asset_omissions": asset_omission_report,
            "portal_evidence": (
                {
                    "schema_version": self.portal_evidence.get("schema_version"),
                    "sidecar": "portal-evidence.json",
                    "sha256": hashlib.sha256(
                        (json.dumps(self.portal_evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
                    ).hexdigest(),
                    "binding": self.portal_evidence.get("binding"),
                    "render_policy": self.portal_evidence.get("render_policy"),
                    "coverage": self.portal_evidence.get("coverage"),
                    "diagnostics": self.portal_evidence.get("diagnostics"),
                }
                if self.portal_evidence is not None
                else {"status": "not_supplied"}
            ),
            "source_comparison_receipt": (
                {
                    "schema_version": self.source_comparison_receipt.get("schema_version")
                    or self.source_comparison_receipt.get("report_schema"),
                    "sidecar": "source-comparison-receipt.json",
                    "sha256": self.source_comparison_receipt_sha256,
                    "decision": self.source_comparison_receipt.get("decision"),
                    "strict_comparison": self.source_comparison_receipt.get("strict_comparison"),
                }
                if self.source_comparison_receipt is not None
                else {"status": "not_supplied"}
            ),
            "issues": self.issues,
            "counts": {
                "raw_records": len(self.payload.get("docs", [])),
                "indexed_records": len(self.index),
                "occurrences": self._occurrences,
                "sources": len(self.source_map),
                "assets": len(self.assets),
                "asset_omissions": len(asset_omission_report),
                "asset_omission_occurrences": sum(
                    item["occurrence_count"] for item in asset_omission_report.values()
                ),
                "issues": len(self.issues),
                "scope_excluded_records": len(self._scope_exclusions),
                "system_definition_records": len(self._system_exclusions),
                "excluded_records": len(excluded_ids),
                "record_dispositions": dict(sorted(disposition_counts.items())),
            },
        }
        if self.full_mode:
            manifest["document_boundaries"] = self.boundary_evidence
            manifest["output_document_plan"] = {
                "mode": "explicit_full_map" if self.document_plan is not None else "native_boundaries",
                "boundary_count": len(self.files),
                "native_boundary_count": len(self.native_file_map),
                "added_boundary_ids": sorted(set(self.files) - set(self.native_file_map)),
                "removed_boundary_ids": sorted(set(self.native_file_map) - set(self.files)),
            }
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
        exclusion_fields: dict[str, dict[str, str]] = {}
        for field in ("exclude_subtree_roots", "exclude_source_ids"):
            value = config.get(field) or {}
            if not isinstance(value, dict) or any(
                not isinstance(rem_id, str)
                or not rem_id
                or not isinstance(reason, str)
                or not reason.strip()
                for rem_id, reason in value.items()
            ):
                raise ExportError(f"config.{field} must map non-empty Rem IDs to non-empty reasons")
            exclusion_fields[field] = value
        document_plan = config.get("document_plan") if "document_plan" in config else None
        if document_plan is not None and not isinstance(document_plan, dict):
            raise ExportError("config.document_plan must map Rem IDs to path/evidence objects")
        reference_metadata_config = config.get("reference_metadata") if "reference_metadata" in config else None
        if reference_metadata_config is not None and not isinstance(reference_metadata_config, dict):
            raise ExportError("config.reference_metadata must be an object")
        asset_omissions = config.get("asset_omissions") or {}
        if not isinstance(asset_omissions, dict):
            raise ExportError("config.asset_omissions must map asset URLs to reviewed reasons")
        portal_evidence_config = config.get("portal_evidence") if "portal_evidence" in config else None
        if portal_evidence_config is not None:
            if not isinstance(portal_evidence_config, dict):
                raise ExportError("config.portal_evidence must be an object")
            if (
                not isinstance(portal_evidence_config.get("evidence"), str)
                or not portal_evidence_config["evidence"].strip()
            ):
                raise ExportError("config.portal_evidence requires non-empty evidence")
            reviewed_receipt_path = portal_evidence_config.get("source_comparison_receipt")
            if reviewed_receipt_path is not None and (
                not isinstance(reviewed_receipt_path, str) or not reviewed_receipt_path
            ):
                raise ExportError("config.portal_evidence.source_comparison_receipt must be a non-empty path")
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
            exclude_subtree_roots=exclusion_fields["exclude_subtree_roots"],
            exclude_source_ids=exclusion_fields["exclude_source_ids"],
            document_plan=document_plan,
            reference_metadata=reference_metadata_config,
            asset_omissions=asset_omissions,
        )
        portal_evidence_result = None
        source_receipt = None
        source_receipt_sha256 = None
        if portal_evidence_config is not None:
            if not full_mode or contract is None or snapshot_path is None or contract_sha256 is None:
                raise ExportError("config.portal_evidence requires full mode and a validated snapshot contract")
            reviewed_receipt_sha256 = None
            if portal_evidence_config.get("source_comparison_receipt"):
                _, reviewed_receipt_sha256 = load_source_comparison_receipt(
                    Path(portal_evidence_config["source_comparison_receipt"]),
                    raw_export_sha256=fingerprint,
                    snapshot_file_sha256=contract_sha256,
                    record_count=len(payload["docs"]),
                )
            source_receipt, source_receipt_sha256 = create_source_comparison_receipt(
                payload,
                fingerprint,
                contract,
                contract_sha256,
                reviewed_receipt_sha256=reviewed_receipt_sha256,
            )
            locations = converter.admitted_portal_locations()
            portal_evidence_result = derive_portal_evidence(
                contract,
                converter.index,
                locations,
                set(converter._scope_exclusions) | set(converter._system_exclusions),
                raw_export_sha256=fingerprint,
                scope_policy_sha256=converter.scope_policy_sha256,
                source_comparison_receipt_sha256=source_receipt_sha256,
                expected_knowledgebase_id=converter.knowledgebase_id,
            )
            converter.install_portal_evidence(
                portal_evidence_result,
                source_comparison_receipt=source_receipt,
                source_comparison_receipt_sha256=source_receipt_sha256,
            )
        manifest = converter.convert(args.output)
        if full_mode and contract is not None and snapshot_path is not None:
            (args.output / "snapshot-contract.json").write_bytes(snapshot_path.read_bytes())
        if portal_evidence_result is not None and source_receipt is not None:
            (args.output / "portal-evidence.json").write_text(
                json.dumps(portal_evidence_result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            (args.output / "source-comparison-receipt.json").write_text(
                json.dumps(source_receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
    except (OSError, ExportError, ValueError) as exc:
        print(f"remnote_export: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"manifest": str(args.output / 'manifest.json'), "status": manifest["status"], "counts": manifest["counts"]}, sort_keys=True))
    return 2 if manifest["status"].startswith("incomplete") else 0


if __name__ == "__main__":
    raise SystemExit(main())
