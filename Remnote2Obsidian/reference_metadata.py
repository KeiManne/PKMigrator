"""Conservative extraction of external-link metadata from reviewed Rem trees."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Optional, Sequence
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ReferenceMetadata:
    url: str
    label: str
    provenance: Mapping[str, Any]


@dataclass(frozen=True)
class ReferenceMetadataIndex:
    entries: Mapping[str, ReferenceMetadata]
    unresolved: Mapping[str, str]

    def resolve_reference(self, rem_id: str) -> Optional[ReferenceMetadata]:
        return self.entries.get(rem_id)


def _path(value: Any, keys: Sequence[str]) -> Any:
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _plain(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_plain(item) for item in value)
    if isinstance(value, dict):
        return _plain(value.get("text") or value.get("textOfDeletedRem"))
    return ""


def _safe_http_url(value: Any) -> Optional[str]:
    if not isinstance(value, str) or value != value.strip():
        return None
    if "\\" in value or any(character.isspace() or ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        return None
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        parsed.port
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not hostname or parsed.username or parsed.password:
        return None
    return value


def extract_reference_metadata(
    records: Iterable[Mapping[str, Any]],
    reviewed_root_ids: Iterable[str],
    link_type_id: str,
) -> ReferenceMetadataIndex:
    """Extract typed link targets below explicitly reviewed metadata roots.

    A record is admitted only when its ``tp`` map positively names
    ``link_type_id`` and the two observed URL metadata locations agree. Records
    that do not satisfy the contract remain in ``unresolved``; this function
    never decides that a source record may be excluded.
    """

    by_id = {record.get("_id"): record for record in records if isinstance(record.get("_id"), str)}
    children: dict[Optional[str], list[str]] = {}
    for rem_id, record in by_id.items():
        children.setdefault(record.get("parent"), []).append(rem_id)

    roots = tuple(dict.fromkeys(reviewed_root_ids))
    visited: dict[str, str] = {}
    stack = [(root_id, root_id) for root_id in roots if root_id in by_id]
    while stack:
        rem_id, root_id = stack.pop()
        previous_root = visited.get(rem_id)
        if previous_root is not None:
            continue
        visited[rem_id] = root_id
        stack.extend((child_id, root_id) for child_id in children.get(rem_id, ()))

    entries: dict[str, ReferenceMetadata] = {}
    unresolved: dict[str, str] = {}
    url_paths = (("crt", "b", "u", "s"), ("ps", "b_u", "v", "s"))
    label_paths = (("crt", "b", "t", "s"), ("ps", "b_t", "v", "s"))
    for rem_id, root_id in visited.items():
        record = by_id[rem_id]
        if rem_id == root_id:
            continue
        if link_type_id not in (record.get("tp") or {}):
            unresolved[rem_id] = "not-typed-link-metadata"
            continue

        raw_urls = [(".".join(path), _path(record, path)) for path in url_paths]
        present_urls = [(path, value) for path, value in raw_urls if value is not None]
        safe_urls = [(path, _safe_http_url(value)) for path, value in present_urls]
        if not present_urls:
            unresolved[rem_id] = "missing-url-metadata"
            continue
        if any(value is None for _, value in safe_urls):
            unresolved[rem_id] = "unsafe-url-metadata"
            continue
        unique_urls = {value for _, value in safe_urls}
        if len(unique_urls) != 1:
            unresolved[rem_id] = "ambiguous-url-metadata"
            continue
        url = unique_urls.pop()

        labels = [(".".join(path), _path(record, path)) for path in label_paths]
        labels = [(path, value.strip()) for path, value in labels if isinstance(value, str) and value.strip()]
        if len({value for _, value in labels}) > 1:
            unresolved[rem_id] = "ambiguous-label-metadata"
            continue
        if labels:
            label_field, label = labels[0]
        else:
            label_field, label = "key", _plain(record.get("key")).strip()
        if not label:
            unresolved[rem_id] = "missing-display-label"
            continue

        entries[rem_id] = ReferenceMetadata(
            url=url,
            label=label,
            provenance=MappingProxyType({
                "reviewed_root_id": root_id,
                "type_field": f"tp.{link_type_id}",
                "url_fields": tuple(path for path, _ in safe_urls),
                "label_field": label_field,
            }),
        )

    for root_id in roots:
        if root_id not in by_id:
            unresolved[root_id] = "missing-reviewed-root"
    return ReferenceMetadataIndex(
        entries=MappingProxyType(entries),
        unresolved=MappingProxyType(unresolved),
    )
