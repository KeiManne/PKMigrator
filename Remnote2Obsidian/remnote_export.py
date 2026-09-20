#!/usr/bin/env python3
"""Bounded RemNote export proof-of-concept.

This module deliberately implements a reviewable pilot, not a whole-vault import.
It reads the structured ``rem.json`` member of a .rem archive, writes one note per
explicit root, expands supported portals as real nested bullets, and emits a
machine-readable identity/appearance manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
import urllib.parse
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


CONVERTER_VERSION = "0.1.0-pilot"
DEFAULT_MAX_OCCURRENCES = 2_000
DEFAULT_MAX_DEPTH = 50
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
    ) -> None:
        if max_occurrences < 1 or max_depth < 1:
            raise ExportError("max_occurrences and max_depth must be positive")
        self.payload = payload
        self.fingerprint = fingerprint
        self.roots = list(dict.fromkeys(str(x) for x in roots))
        if not self.roots:
            raise ExportError("at least one explicit root ID is required")
        self.max_occurrences = max_occurrences
        self.max_depth = max_depth
        self.portal_snapshots = portal_snapshots or {}
        self.visibility_overrides = visibility_overrides or {}
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
                    if not target or target_id in seen_references:
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
                label = self.plain_rich(self.index.get(target_id, {}).get("key")) or target_id
                target = self.canonical.get(target_id)
                if target:
                    self._referenced_canonical.add(target_id)
                    rendered.append(f"[[{target['file']}#^{target['anchor']}|{_escape_markdown(label)}]]")
                else:
                    rendered.append(_escape_markdown(f"(({target_id}))"))
                    self.issue("unresolved_reference", "warning", "Reference has no canonical pilot location", rem_id=owner_id, path=ctx.appearance_path, details={"target_id": target_id})
                continue
            if kind == "i":
                source = part.get("url")
                if not isinstance(source, str) or not source:
                    rendered.append("[image unavailable]")
                    self.issue("image_without_url", "warning", "Image object has no URL", rem_id=owner_id, path=ctx.appearance_path)
                    continue
                parsed = urllib.parse.urlparse(source)
                suffix = Path(parsed.path).suffix.lower()
                if not re.fullmatch(r"\.[a-z0-9]{1,8}", suffix):
                    suffix = ".bin"
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
            if raw.get("n") == 1 or raw.get("forceIsFolder") is True:
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
                self.issue("occurrence_limit", "error", "Pilot occurrence limit reached; output is intentionally incomplete", rem_id=rem_id, portal_id=ctx.portal_id, path=ctx.appearance_path, details={"max_occurrences": self.max_occurrences})
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
            self.issue("depth_limit", "error", "Pilot traversal depth limit reached", rem_id=rem_id, portal_id=ctx.portal_id, path=ctx.appearance_path, details={"max_depth": self.max_depth})
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

    def convert(self, output: Path) -> dict[str, Any]:
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
            written_files.append({"root_id": root, "path": relative, "sha256": hashlib.sha256(body.encode()).hexdigest()})
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
        manifest = {
            "schema_version": 1,
            "converter_version": CONVERTER_VERSION,
            "status": "incomplete" if any(x["severity"] == "error" for x in self.issues) else "complete_for_requested_pilot",
            "export": {
                "sha256": self.fingerprint,
                "exportVersion": self.payload.get("exportVersion"),
                "exportDate": self.payload.get("exportDate"),
                "knowledgebaseId": self.payload.get("knowledgebaseId"),
                "knowledgebase_identity_status": "known" if isinstance(self.payload.get("knowledgebaseId"), str) and bool(self.payload["knowledgebaseId"].strip()) else "unknown",
            },
            "configuration": {
                "roots": self.roots,
                "max_occurrences": self.max_occurrences,
                "max_depth": self.max_depth,
                "portal_snapshots": self.portal_snapshots,
                "visibility_overrides": self.visibility_overrides,
            },
            "files": written_files,
            "source_map": self.source_map,
            "assets": self.assets,
            "issues": self.issues,
            "counts": {"occurrences": self._occurrences, "sources": len(self.source_map), "assets": len(self.assets), "issues": len(self.issues)},
        }
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
    parser.add_argument("--output", required=True, type=Path, help="isolated pilot output directory")
    parser.add_argument("--roots", action="append", help="explicit root ID(s), comma-separated or repeated")
    parser.add_argument("--config", type=Path, help="JSON config with roots, limits, portal_snapshots and visibility_overrides")
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
    roots = _parse_roots(args.roots) or [str(x) for x in config.get("roots", [])]
    try:
        payload, fingerprint = load_export(args.input)
        converter = Converter(
            payload,
            fingerprint,
            roots,
            max_occurrences=args.max_occurrences or int(config.get("max_occurrences", DEFAULT_MAX_OCCURRENCES)),
            max_depth=args.max_depth or int(config.get("max_depth", DEFAULT_MAX_DEPTH)),
            portal_snapshots=config.get("portal_snapshots"),
            visibility_overrides=config.get("visibility_overrides"),
        )
        manifest = converter.convert(args.output)
    except (OSError, ExportError, ValueError) as exc:
        print(f"remnote_export: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"manifest": str(args.output / 'manifest.json'), "status": manifest["status"], "counts": manifest["counts"]}, sort_keys=True))
    return 2 if manifest["status"] == "incomplete" else 0


if __name__ == "__main__":
    raise SystemExit(main())
