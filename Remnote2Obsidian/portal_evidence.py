"""Pure, fail-closed interpretation of a RemNote live portal snapshot.

This module does not render Markdown and does not mutate the snapshot contract.  It
turns already-admitted portal IDs into evidence plans that a renderer can consume.
The rendering policy is an explicit migration decision: ordinary portals preserve
eligible descendants, while automatic views render the matched bullet plus a link
to its canonical source path.  Collapse remains presentation evidence and never
decides content inclusion.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Set
from typing import Any


PORTAL_EVIDENCE_SCHEMA = "pkmigrator-portal-evidence/v1"
SNAPSHOT_SCHEMA = "remnote-migration-snapshot/v1"
PORTAL_RENDER_POLICY = "pkmigrator-portal-render-policy/v1"
ORDINARY_CONTENT_POLICY = "ordinary-full-eligible-subtree"
AUTOMATIC_CONTENT_POLICY = "automatic-match-with-canonical-source-link"
TABLE_ROW_CONTENT_POLICY = "physically-owned-table-row-subtree"
TABLE_SCHEMA_CONTENT_POLICY = "portal-owned-table-schema"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RESOLVED_VISIBILITY_STATES = {"none", "hidden", "included", "root"}
_PORTAL_SCOPE_POLICY_FIELDS = ("exclude_subtree_roots", "exclude_source_ids", "document_plan")
_ARTIFACT_DIGEST_CONTRACT = "sha256-json-sort-keys-utf8-v1"


class PortalEvidenceError(ValueError):
    """Raised when source/snapshot binding is invalid before planning begins."""


def _require_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise PortalEvidenceError(f"{label} must be a lowercase SHA-256 hex digest")
    return value


def _stable_id_digest(ids: Iterable[str]) -> str:
    value = "\0".join(sorted(ids)).encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def portal_scope_policy_sha256(scope_policy: Mapping[str, Any]) -> str:
    """Digest only the policy fields that determine portal admission and links."""
    subset: dict[str, Any] = {"portal_render_policy_id": PORTAL_RENDER_POLICY}
    for field in _PORTAL_SCOPE_POLICY_FIELDS:
        value = scope_policy.get(field)
        if not isinstance(value, Mapping):
            raise PortalEvidenceError(f"scope policy field {field!r} must be an object")
        subset[field] = value
    encoded = json.dumps(
        subset,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def portal_evidence_payload_sha256(artifact: Mapping[str, Any]) -> str:
    """Digest an evidence artifact excluding its self-describing digest block."""
    payload = {key: value for key, value in artifact.items() if key != "artifact_digest"}
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_portal_evidence_artifact(
    artifact: Mapping[str, Any],
    *,
    expected_knowledgebase_id: str,
    expected_snapshot_payload_sha256: str,
    expected_raw_export_sha256: str,
    expected_scope_policy_sha256: str,
    expected_source_comparison_receipt_sha256: str,
    expected_raw_record_ids: Iterable[str],
    expected_admitted_portal_ids: Iterable[str],
    expected_excluded_source_ids: Iterable[str],
    require_complete: bool = True,
) -> None:
    """Validate payload integrity and every binding before installing plans."""
    if artifact.get("schema_version") != PORTAL_EVIDENCE_SCHEMA:
        raise PortalEvidenceError("unsupported portal evidence schema")
    digest = artifact.get("artifact_digest")
    if not isinstance(digest, Mapping) or digest.get("contract") != _ARTIFACT_DIGEST_CONTRACT:
        raise PortalEvidenceError("portal evidence artifact digest contract is missing or unsupported")
    expected_payload_sha256 = _require_sha256(digest.get("payload_sha256"), "portal evidence payload")
    if portal_evidence_payload_sha256(artifact) != expected_payload_sha256:
        raise PortalEvidenceError("portal evidence artifact payload digest mismatch")

    binding = artifact.get("binding")
    if not isinstance(binding, Mapping):
        raise PortalEvidenceError("portal evidence binding is missing")
    raw_ids = set(expected_raw_record_ids)
    admitted = set(expected_admitted_portal_ids)
    excluded = set(expected_excluded_source_ids)
    expected_bindings = {
        "knowledgebase_id": expected_knowledgebase_id,
        "snapshot_payload_sha256": _require_sha256(expected_snapshot_payload_sha256, "expected snapshot payload"),
        "raw_export_sha256": _require_sha256(expected_raw_export_sha256, "expected raw export"),
        "scope_policy_sha256": _require_sha256(expected_scope_policy_sha256, "expected scope policy"),
        "source_comparison_receipt_sha256": _require_sha256(
            expected_source_comparison_receipt_sha256,
            "expected source comparison receipt",
        ),
        "raw_record_id_set_sha256": _stable_id_digest(raw_ids),
        "admitted_portal_id_set_sha256": _stable_id_digest(admitted),
        "excluded_source_id_set_sha256": _stable_id_digest(excluded),
    }
    for field, value in expected_bindings.items():
        if binding.get(field) != value:
            raise PortalEvidenceError(f"portal evidence binding mismatch: {field}")
    if binding.get("scope_policy_digest_contract") != "canonical-json-selected-portal-fields-v1":
        raise PortalEvidenceError("portal evidence scope-policy digest contract is unsupported")

    policy = artifact.get("render_policy")
    if not isinstance(policy, Mapping) or policy.get("policy_id") != PORTAL_RENDER_POLICY:
        raise PortalEvidenceError("portal evidence render policy is missing or unsupported")
    plans = artifact.get("plans")
    coverage = artifact.get("coverage")
    if not isinstance(plans, Mapping) or set(plans) != admitted:
        raise PortalEvidenceError("portal evidence plan IDs do not match admitted portal IDs")
    if not isinstance(coverage, Mapping):
        raise PortalEvidenceError("portal evidence coverage is missing")
    if coverage.get("expected_admitted_portal_count") != len(admitted) or coverage.get("planned_portal_count") != len(plans):
        raise PortalEvidenceError("portal evidence coverage counts do not match plan IDs")
    if require_complete:
        unresolved = coverage.get("unresolved_portal_ids")
        if coverage.get("complete") is not True or unresolved != []:
            raise PortalEvidenceError("portal evidence is incomplete")
        if any(not isinstance(plan, Mapping) or plan.get("complete") is not True for plan in plans.values()):
            raise PortalEvidenceError("portal evidence contains an incomplete plan")


def _ancestor_path(
    anchor_id: str,
    records: Mapping[str, Mapping[str, Any]],
) -> tuple[list[str], str | None]:
    """Return the known top-down canonical path ending at ``anchor_id``."""
    path: list[str] = []
    seen: set[str] = set()
    current: str | None = anchor_id
    while current is not None:
        if current in seen:
            return list(reversed(path)), f"canonical-parent-cycle:{current}"
        seen.add(current)
        record = records.get(current)
        if record is None:
            return list(reversed(path)), f"canonical-parent-missing:{current}"
        path.append(current)
        parent = record.get("parent")
        if parent is not None and not isinstance(parent, str):
            return list(reversed(path)), f"canonical-parent-invalid:{current}"
        current = parent
    return list(reversed(path)), None


def _portal_type(portal: Mapping[str, Any]) -> int | None:
    value = portal.get("portal_type")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _rich_has_content(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return any(_rich_has_content(item) for item in value)
    if isinstance(value, Mapping):
        text = value.get("text")
        return isinstance(text, str) and bool(text.strip())
    return False


def _rich_reference_ids(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, list):
        for item in value:
            found.update(_rich_reference_ids(item))
    elif isinstance(value, Mapping):
        target = value.get("q")
        if isinstance(target, str):
            found.add(target)
        for item in value.values():
            found.update(_rich_reference_ids(item))
    return found


def derive_portal_evidence(
    snapshot_contract: Mapping[str, Any],
    raw_records_by_id: Mapping[str, Mapping[str, Any]],
    admitted_portal_ids: Iterable[str],
    excluded_source_ids: Set[str] | set[str],
    *,
    raw_export_sha256: str,
    scope_policy_sha256: str,
    source_comparison_receipt_sha256: str,
    expected_knowledgebase_id: str | None = None,
) -> dict[str, Any]:
    """Derive immutable portal evidence plans for exactly the admitted portal set.

    The caller supplies portal reachability after scope and document-plan processing.
    Every supplied portal receives one plan, including unresolved plans.  Automatic
    result plans identify only their matched anchors.  The caller must render the
    canonical source path/link recorded on each appearance and must not recursively
    copy an automatic result subtree.  The caller must validate the source-comparison
    receipt before passing its digest to this pure adapter.
    """
    if snapshot_contract.get("schema_version") != SNAPSHOT_SCHEMA:
        raise PortalEvidenceError("unsupported snapshot schema")
    capture = snapshot_contract.get("capture")
    portals = snapshot_contract.get("portals")
    snapshot_records = snapshot_contract.get("records")
    if not isinstance(capture, Mapping) or not isinstance(portals, Mapping):
        raise PortalEvidenceError("snapshot is missing capture or portal evidence")
    if not isinstance(snapshot_records, Mapping):
        raise PortalEvidenceError("snapshot is missing its bulk record inventory")

    snapshot_sha256 = _require_sha256(capture.get("payload_sha256"), "snapshot payload")
    raw_sha256 = _require_sha256(raw_export_sha256, "raw export")
    policy_sha256 = _require_sha256(scope_policy_sha256, "scope policy")
    comparison_sha256 = _require_sha256(source_comparison_receipt_sha256, "source comparison receipt")
    knowledgebase_id = capture.get("knowledgebase_id")
    if not isinstance(knowledgebase_id, str) or not knowledgebase_id:
        raise PortalEvidenceError("snapshot knowledge-base identity is missing")
    if expected_knowledgebase_id is not None and knowledgebase_id != expected_knowledgebase_id:
        raise PortalEvidenceError("snapshot knowledge-base identity does not match the raw export")
    if capture.get("knowledgebase_consistent") is not True:
        raise PortalEvidenceError("snapshot changed knowledge bases during capture")

    raw_ids = set(raw_records_by_id)
    snapshot_ids = set(snapshot_records)
    if raw_ids != snapshot_ids:
        raise PortalEvidenceError("raw export and snapshot bulk record ID sets differ")

    admitted = sorted(set(admitted_portal_ids))
    excluded = set(excluded_source_ids)
    children_by_parent: dict[str | None, list[str]] = {}
    for rem_id, record in raw_records_by_id.items():
        parent_id = record.get("parent")
        children_by_parent.setdefault(parent_id if isinstance(parent_id, str) else None, []).append(rem_id)
    inbound_references: dict[str, set[str]] = {}
    for rem_id, record in raw_records_by_id.items():
        for target_id in _rich_reference_ids(record.get("key")) | _rich_reference_ids(record.get("value")):
            inbound_references.setdefault(target_id, set()).add(rem_id)
    classifications = snapshot_contract.get("classifications")
    document_and_folder = classifications.get("document_and_folder") if isinstance(classifications, Mapping) else None
    document_states = document_and_folder.get("states") if isinstance(document_and_folder, Mapping) else {}
    if not isinstance(document_states, Mapping):
        document_states = {}
    diagnostics: list[dict[str, Any]] = []
    plans: dict[str, dict[str, Any]] = {}

    def issue(
        portal_id: str,
        code: str,
        message: str,
        *,
        severity: str = "error",
        context_portal_id: str | None = None,
        rem_id: str | None = None,
    ) -> None:
        item: dict[str, Any] = {
            "severity": severity,
            "code": code,
            "message": message,
            "portal_id": portal_id,
        }
        if context_portal_id is not None:
            item["context_portal_id"] = context_portal_id
        if rem_id is not None:
            item["rem_id"] = rem_id
        diagnostics.append(item)

    for portal_id in admitted:
        portal = portals.get(portal_id)
        raw_portal = raw_records_by_id.get(portal_id)
        if not isinstance(portal, Mapping) or not isinstance(raw_portal, Mapping):
            issue(portal_id, "admitted-portal-missing", "Admitted portal is absent from live or raw evidence.")
            plans[portal_id] = {"kind": "unresolved", "complete": False, "appearances": []}
            continue
        if raw_portal.get("type") != 6:
            issue(portal_id, "admitted-record-not-portal", "Admitted record is not raw Rem type 6.")
            plans[portal_id] = {"kind": "unresolved", "complete": False, "appearances": []}
            continue

        membership = portal.get("membership")
        positions = portal.get("positions")
        visibility = portal.get("visibility")
        if not all(isinstance(value, Mapping) for value in (membership, positions, visibility)):
            issue(portal_id, "portal-evidence-shape", "Portal membership/position/visibility evidence is malformed.")
            plans[portal_id] = {"kind": "unresolved", "complete": False, "appearances": []}
            continue
        member_ids = membership.get("member_ids")
        if membership.get("complete") is not True or not isinstance(member_ids, list) or not all(isinstance(i, str) for i in member_ids):
            issue(portal_id, "portal-membership-incomplete", "Portal direct membership is incomplete or malformed.")
            plans[portal_id] = {"kind": "unresolved", "complete": False, "appearances": []}
            continue
        if len(member_ids) != len(set(member_ids)):
            issue(portal_id, "portal-membership-duplicate", "Portal direct membership contains duplicate IDs.")
            plans[portal_id] = {"kind": "unresolved", "complete": False, "appearances": []}
            continue

        position_states = positions.get("states") if isinstance(positions.get("states"), Mapping) else {}
        visibility_states = visibility.get("states") if isinstance(visibility.get("states"), Mapping) else {}
        portal_type = _portal_type(portal)
        plan: dict[str, Any] = {
            "complete": True,
            "complete_semantics": "admitted-plan-evidence-under-render-policy",
            "source_membership_ids": list(member_ids),
            "appearances": [],
            "omissions": [],
            "visibility_contexts": {},
        }

        def appearance(anchor_id: str, context_id: str, order: int, *, content_policy: str) -> dict[str, Any] | None:
            path, path_error = _ancestor_path(anchor_id, raw_records_by_id)
            if path_error:
                issue(portal_id, path_error.split(":", 1)[0], path_error, context_portal_id=context_id, rem_id=anchor_id)
                plan["complete"] = False
                return None
            state_map = portals[context_id].get("visibility", {}).get("states", {})
            state = state_map.get(anchor_id)
            if state not in _RESOLVED_VISIBILITY_STATES:
                issue(
                    portal_id,
                    "anchor-visibility-unresolved",
                    f"Anchor visibility state is {state!r}.",
                    context_portal_id=context_id,
                    rem_id=anchor_id,
                )
                plan["complete"] = False
                return None
            collapsed_map = portals[context_id].get("collapsed", {}).get("states", {})
            collapsed_value = collapsed_map.get(anchor_id)
            return {
                "anchor_id": anchor_id,
                "context_portal_id": context_id,
                "order": order,
                "canonical_ancestry_ids": path[:-1],
                "canonical_source_path_ids": path,
                "visibility_state": state,
                "collapsed": collapsed_value if isinstance(collapsed_value, bool) else None,
                "collapsed_observation_complete": isinstance(collapsed_value, bool),
                "content_policy": content_policy,
            }

        def ordered_children(rem_id: str) -> list[str] | None:
            raw_children = children_by_parent.get(rem_id, [])
            snapshot_record = snapshot_records.get(rem_id)
            child_ids = snapshot_record.get("child_ids") if isinstance(snapshot_record, Mapping) else None
            if (
                not isinstance(child_ids, list)
                or not all(isinstance(item, str) for item in child_ids)
                or len(child_ids) != len(set(child_ids))
                or set(child_ids) != set(raw_children)
            ):
                issue(
                    portal_id,
                    "table-child-order-unresolved",
                    "Snapshot child order is missing, duplicated, or inconsistent with raw parent ownership.",
                    rem_id=rem_id,
                )
                plan["complete"] = False
                return None
            return list(child_ids)

        def require_non_document(rem_id: str) -> bool:
            state = document_states.get(rem_id)
            if not isinstance(state, Mapping) or not isinstance(state.get("is_document"), bool) or not isinstance(state.get("is_folder"), bool):
                issue(portal_id, "table-boundary-unresolved", "Table-owned record lacks document/folder classification.", rem_id=rem_id)
                plan["complete"] = False
                return False
            if state.get("is_document") or state.get("is_folder"):
                issue(portal_id, "table-boundary-record", "Table-owned traversal reached a document or folder boundary.", rem_id=rem_id)
                plan["complete"] = False
                return False
            return True

        if portal_type == 0:
            plan["kind"] = "ordinary-content"
            plan["cycle_guard_ids"] = [portal_id]
            retained = [member for member in member_ids if member not in excluded]
            for member in member_ids:
                if member in excluded:
                    plan["omissions"].append({"context_portal_id": portal_id, "anchor_ids": [member], "reason": "excluded-source"})
            if len(retained) > 1:
                visible_positions: dict[str, int] = {}
                for member in retained:
                    value = position_states.get(member, {}).get("visible_position") if isinstance(position_states.get(member), Mapping) else None
                    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                        break
                    visible_positions[member] = value
                ordered = sorted(retained, key=lambda item: visible_positions.get(item, -1))
                if len(visible_positions) != len(retained) or len(set(visible_positions.values())) != len(retained) or ordered != retained:
                    issue(portal_id, "ordinary-order-unresolved", "Multiple retained ordinary members lack SDK/visible-position order concordance.")
                    plan["complete"] = False
                else:
                    plan["order_basis"] = "sdk-return-order-concordant-visible-position"
            else:
                plan["order_basis"] = "trivial-zero-or-one-retained-member"
            eligible_ids: set[str] = set()
            for member in retained:
                if visibility_states.get(member) == "hidden":
                    continue
                pending = [member]
                while pending:
                    rem_id = pending.pop()
                    if rem_id in eligible_ids or rem_id in excluded or rem_id == portal_id:
                        continue
                    eligible_ids.add(rem_id)
                    state = visibility_states.get(rem_id)
                    if state not in {"none", "hidden", "included"}:
                        issue(
                            portal_id,
                            "ordinary-descendant-visibility-unresolved",
                            f"Eligible ordinary descendant visibility state is {state!r}.",
                            rem_id=rem_id,
                        )
                        plan["complete"] = False
                    pending.extend(children_by_parent.get(rem_id, ()))
            plan["eligible_source_record_count"] = len(eligible_ids)
            for order, member in enumerate(retained):
                if visibility_states.get(member) == "hidden":
                    plan["omissions"].append({"context_portal_id": portal_id, "anchor_ids": [member], "reason": "hidden-anchor"})
                    continue
                item = appearance(member, portal_id, order, content_policy=ORDINARY_CONTENT_POLICY)
                if item is not None:
                    plan["appearances"].append(item)
            plan["visibility_contexts"][portal_id] = dict(visibility_states)

        elif portal_type == 4:
            context_members = [
                member
                for member in member_ids
                if (
                    isinstance(portals.get(member), Mapping)
                    and _portal_type(portals[member]) == 0
                    and isinstance(raw_records_by_id.get(member), Mapping)
                    and raw_records_by_id[member].get("parent") == portal_id
                )
            ]
            if member_ids and len(context_members) == len(member_ids):
                plan["kind"] = "contextual-search"
                context_positions: dict[str, int] = {}
                for context_id in context_members:
                    value = position_states.get(context_id, {}).get("visible_position") if isinstance(position_states.get(context_id), Mapping) else None
                    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                        issue(portal_id, "context-order-unresolved", "Context visible position is missing or invalid.", context_portal_id=context_id)
                        plan["complete"] = False
                    else:
                        context_positions[context_id] = value
                if len(set(context_positions.values())) != len(context_positions):
                    issue(portal_id, "context-order-duplicate", "Context visible positions are not unique.")
                    plan["complete"] = False
                ordered_contexts = sorted(context_members, key=lambda item: context_positions.get(item, -1))
                plan["ordered_context_portal_ids"] = ordered_contexts
                plan["order_basis"] = "outer-context-visible-position"
                result_order = 0
                for context_id in ordered_contexts:
                    child = portals[context_id]
                    child_membership = child.get("membership", {})
                    child_members = child_membership.get("member_ids")
                    if child_membership.get("complete") is not True or not isinstance(child_members, list) or not all(isinstance(i, str) for i in child_members):
                        issue(portal_id, "context-membership-incomplete", "Nested context membership is incomplete.", context_portal_id=context_id)
                        plan["complete"] = False
                        continue
                    if len(child_members) != len(set(child_members)):
                        issue(portal_id, "context-membership-duplicate", "Nested context membership contains duplicate IDs.", context_portal_id=context_id)
                        plan["complete"] = False
                        continue
                    retained = [member for member in child_members if member not in excluded]
                    removed = [member for member in child_members if member in excluded]
                    if retained and removed:
                        issue(portal_id, "context-scope-mixed", "Nested context mixes retained and excluded anchors.", context_portal_id=context_id)
                        plan["complete"] = False
                        continue
                    if not retained:
                        plan["omissions"].append({
                            "context_portal_id": context_id,
                            "anchor_ids": list(child_members),
                            "reason": "empty-context" if not child_members else "excluded-source",
                        })
                        continue
                    if len(retained) != 1:
                        issue(portal_id, "context-multiple-retained-anchors", "Nested context has multiple retained anchors.", context_portal_id=context_id)
                        plan["complete"] = False
                        continue
                    child_visibility = child.get("visibility", {}).get("states", {})
                    if child_visibility.get(retained[0]) == "hidden":
                        plan["omissions"].append({
                            "context_portal_id": context_id,
                            "anchor_ids": retained,
                            "reason": "hidden-anchor",
                        })
                        continue
                    outer_state = visibility_states.get(context_id)
                    if outer_state not in {"none", "included", "hidden"}:
                        issue(portal_id, "context-visibility-unresolved", f"Outer context visibility state is {outer_state!r}.", context_portal_id=context_id)
                        plan["complete"] = False
                        continue
                    if outer_state == "hidden":
                        plan["omissions"].append({"context_portal_id": context_id, "anchor_ids": retained, "reason": "hidden-context"})
                        continue
                    item = appearance(
                        retained[0],
                        context_id,
                        result_order,
                        content_policy=AUTOMATIC_CONTENT_POLICY,
                    )
                    if item is not None:
                        item["outer_visible_position"] = context_positions.get(context_id)
                        plan["appearances"].append(item)
                        result_order += 1
                    plan["visibility_contexts"][context_id] = dict(child.get("visibility", {}).get("states", {}))
            else:
                automatic = portal.get("automatic_view")
                if isinstance(automatic, Mapping) and automatic.get("root_result_complete") is True:
                    roots = automatic.get("root_result_ids")
                    if not isinstance(roots, list) or not all(isinstance(i, str) for i in roots):
                        issue(portal_id, "flat-result-shape", "Flat result roots are malformed.")
                        plan["kind"] = "unresolved-search"
                        plan["complete"] = False
                    else:
                        plan["kind"] = "flat-search"
                        plan["order_basis"] = "root-visible-position"
                        if len(roots) != len(set(roots)):
                            issue(portal_id, "flat-result-duplicate", "Flat result roots contain duplicate IDs.")
                            plan["complete"] = False
                        if any(root not in member_ids for root in roots):
                            issue(portal_id, "flat-result-not-member", "A flat result root is absent from direct membership.")
                            plan["complete"] = False
                        root_positions: dict[str, int] = {}
                        for root in roots:
                            state = visibility_states.get(root)
                            value = position_states.get(root, {}).get("visible_position") if isinstance(position_states.get(root), Mapping) else None
                            if state != "root":
                                issue(portal_id, "flat-result-not-root", f"Flat result visibility state is {state!r}.", rem_id=root)
                                plan["complete"] = False
                            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                                issue(portal_id, "flat-result-position-unresolved", "Flat result visible position is missing or invalid.", rem_id=root)
                                plan["complete"] = False
                            else:
                                root_positions[root] = value
                        if len(set(root_positions.values())) != len(root_positions):
                            issue(portal_id, "flat-result-position-duplicate", "Flat result visible positions are not unique.")
                            plan["complete"] = False
                        if len(root_positions) == len(roots) and sorted(roots, key=root_positions.get) != roots:
                            issue(portal_id, "flat-result-order-mismatch", "Flat result order disagrees with visible sibling positions.")
                            plan["complete"] = False
                        retained = [root for root in roots if root not in excluded]
                        for root in roots:
                            if root in excluded:
                                plan["omissions"].append({"context_portal_id": portal_id, "anchor_ids": [root], "reason": "excluded-source"})
                        for order, root in enumerate(retained):
                            if visibility_states.get(root) == "hidden":
                                plan["omissions"].append({"context_portal_id": portal_id, "anchor_ids": [root], "reason": "hidden-anchor"})
                                continue
                            item = appearance(root, portal_id, order, content_policy=AUTOMATIC_CONTENT_POLICY)
                            if item is not None:
                                raw_root = raw_records_by_id.get(root)
                                if isinstance(raw_root, Mapping) and raw_root.get("type") == 1:
                                    owned_ids: list[str] = []
                                    hidden_ids: list[str] = []
                                    descendant_states: dict[str, str] = {}

                                    def collect_owned_table_descendants(parent_id: str) -> None:
                                        child_ids = ordered_children(parent_id)
                                        if child_ids is None:
                                            return
                                        for child_id in child_ids:
                                            child_record = raw_records_by_id.get(child_id)
                                            if not isinstance(child_record, Mapping):
                                                issue(portal_id, "table-descendant-missing", "Owned table descendant is absent from raw records.", rem_id=child_id)
                                                plan["complete"] = False
                                                continue
                                            if child_id in excluded:
                                                plan["omissions"].append({
                                                    "context_portal_id": portal_id,
                                                    "anchor_ids": [child_id],
                                                    "reason": "excluded-source",
                                                })
                                                continue
                                            if child_record.get("type") == 6:
                                                issue(portal_id, "nested-table-portal-unresolved", "Owned table row contains a nested portal boundary.", rem_id=child_id)
                                                plan["complete"] = False
                                                continue
                                            if not require_non_document(child_id):
                                                continue
                                            state = visibility_states.get(child_id)
                                            if state not in {"none", "included", "hidden"}:
                                                issue(portal_id, "table-descendant-visibility-unresolved", f"Owned table descendant visibility state is {state!r}.", rem_id=child_id)
                                                plan["complete"] = False
                                                continue
                                            descendant_states[child_id] = state
                                            if state == "hidden":
                                                hidden_ids.append(child_id)
                                                continue
                                            owned_ids.append(child_id)
                                            collect_owned_table_descendants(child_id)

                                    collect_owned_table_descendants(root)
                                    item["owned_table_content_policy"] = TABLE_ROW_CONTENT_POLICY
                                    item["owned_table_descendant_ids"] = owned_ids
                                    item["hidden_table_descendant_ids"] = hidden_ids
                                    item["owned_table_descendant_visibility_states"] = descendant_states
                                plan["appearances"].append(item)

                        plan["table_schema_roots"] = []
                        plan["omitted_empty_table_schema_wrappers"] = []
                        plan["table_schema_label_ids"] = []
                        plan["hidden_table_schema_record_ids"] = []
                        plan["table_schema_visibility_states"] = {}
                        raw_visibility = raw_portal.get("ph") if isinstance(raw_portal.get("ph"), Mapping) else {}

                        def schema_visibility(rem_id: str) -> tuple[Any, Any, bool]:
                            sdk_state = visibility_states.get(rem_id)
                            raw_entry = raw_visibility.get(rem_id)
                            raw_state = raw_entry.get("h") if isinstance(raw_entry, Mapping) else None
                            explicit_hidden = sdk_state == "hidden" or raw_state == "h"
                            plan["table_schema_visibility_states"][rem_id] = {
                                "sdk_state": sdk_state,
                                "raw_ph_state": raw_state,
                                "explicit_hidden": explicit_hidden,
                            }
                            if sdk_state in {"none", "included"} and raw_state == "h":
                                issue(portal_id, "table-schema-visibility-conflict", "SDK and raw portal visibility disagree for table schema content.", rem_id=rem_id)
                                plan["complete"] = False
                            return sdk_state, raw_state, explicit_hidden
                        table_roots = [
                            root for root in roots
                            if isinstance(raw_records_by_id.get(root), Mapping)
                            and raw_records_by_id[root].get("type") == 1
                        ]
                        if table_roots:
                            portal_child_ids = ordered_children(portal_id)
                            wrappers: list[str] = []
                            if portal_child_ids is not None:
                                wrappers = [
                                    child_id for child_id in portal_child_ids
                                    if isinstance(raw_records_by_id.get(child_id), Mapping)
                                    and isinstance(raw_records_by_id[child_id].get("apu"), Mapping)
                                    and "g" in raw_records_by_id[child_id]["apu"]
                                ]
                            if not wrappers:
                                issue(portal_id, "table-schema-wrapper-missing", "Table result rows have no evidenced portal-owned schema wrapper.")
                                plan["complete"] = False
                            covered_labels: list[str] = []
                            for wrapper_id in wrappers:
                                wrapper = raw_records_by_id[wrapper_id]
                                wrapper_visibility, _, wrapper_hidden = schema_visibility(wrapper_id)
                                if wrapper_hidden:
                                    hidden_children = children_by_parent.get(wrapper_id, [])
                                    hidden_ids = [wrapper_id] + [child_id for child_id in hidden_children if child_id not in roots]
                                    for hidden_id in hidden_ids[1:]:
                                        schema_visibility(hidden_id)
                                    plan["hidden_table_schema_record_ids"].extend(hidden_ids)
                                    plan["omissions"].append({
                                        "context_portal_id": portal_id,
                                        "anchor_ids": hidden_ids,
                                        "reason": "hidden-table-schema-wrapper",
                                    })
                                    continue
                                if wrapper_visibility not in {None, "none", "included"}:
                                    issue(portal_id, "table-schema-wrapper-visibility-unresolved", f"Schema wrapper visibility state is {wrapper_visibility!r}.", rem_id=wrapper_id)
                                    plan["complete"] = False
                                    continue
                                if wrapper_id in excluded or not require_non_document(wrapper_id):
                                    issue(portal_id, "table-schema-wrapper-excluded", "Table schema wrapper is unavailable under the admitted scope.", rem_id=wrapper_id)
                                    plan["complete"] = False
                                    continue
                                wrapper_children = ordered_children(wrapper_id)
                                if wrapper_children is None:
                                    continue
                                label_ids = [
                                    child_id for child_id in wrapper_children
                                    if isinstance(raw_records_by_id.get(child_id), Mapping)
                                    and (
                                        (
                                            isinstance(raw_records_by_id[child_id].get("apu"), Mapping)
                                            and "y" in raw_records_by_id[child_id]["apu"]
                                        )
                                        or raw_records_by_id[child_id].get("opfl") == "label"
                                    )
                                ]
                                unaccounted = [child_id for child_id in wrapper_children if child_id not in roots and child_id not in label_ids]
                                if unaccounted:
                                    issue(portal_id, "table-schema-child-unresolved", "Schema wrapper has children that are neither result rows nor labels.", rem_id=wrapper_id)
                                    plan["complete"] = False
                                    continue
                                valid_labels: list[str] = []
                                for label_id in label_ids:
                                    label_visibility, _, label_hidden = schema_visibility(label_id)
                                    if label_hidden:
                                        plan["hidden_table_schema_record_ids"].append(label_id)
                                        plan["omissions"].append({
                                            "context_portal_id": portal_id,
                                            "anchor_ids": [label_id],
                                            "reason": "hidden-table-schema-label",
                                        })
                                        continue
                                    if label_visibility not in {None, "none", "included"}:
                                        issue(portal_id, "table-schema-label-visibility-unresolved", f"Schema label visibility state is {label_visibility!r}.", rem_id=label_id)
                                        plan["complete"] = False
                                        continue
                                    if label_id in excluded or not require_non_document(label_id):
                                        issue(portal_id, "table-schema-label-unavailable", "Schema label is unavailable under the admitted scope.", rem_id=label_id)
                                        plan["complete"] = False
                                        continue
                                    if not _rich_has_content(raw_records_by_id[label_id].get("key")) and not _rich_has_content(raw_records_by_id[label_id].get("value")):
                                        issue(portal_id, "table-schema-label-empty", "Schema label has no authored content.", rem_id=label_id)
                                        plan["complete"] = False
                                        continue
                                    valid_labels.append(label_id)
                                covered_labels.extend(valid_labels)
                                evidence = {
                                    "wrapper_id": wrapper_id,
                                    "ordered_label_ids": valid_labels,
                                    "visibility_state": plan["table_schema_visibility_states"][wrapper_id],
                                    "label_visibility_states": {
                                        label_id: plan["table_schema_visibility_states"][label_id]
                                        for label_id in valid_labels
                                    },
                                    "content_policy": TABLE_SCHEMA_CONTENT_POLICY,
                                    "raw_markers": {
                                        "apu_group": True,
                                        "has_property_storage": isinstance(wrapper.get("ps"), Mapping),
                                        "has_table_child_order": isinstance(wrapper.get("tco"), Mapping),
                                        "tagged_powerup_ids": sorted(wrapper.get("tp", {})) if isinstance(wrapper.get("tp"), Mapping) else [],
                                    },
                                }
                                wrapper_has_content = _rich_has_content(wrapper.get("key")) or _rich_has_content(wrapper.get("value"))
                                inbound = sorted(inbound_references.get(wrapper_id, set()))
                                if wrapper_has_content:
                                    evidence["inbound_reference_ids"] = inbound
                                    plan["table_schema_roots"].append(evidence)
                                elif inbound:
                                    issue(portal_id, "empty-table-schema-wrapper-referenced", "Empty schema wrapper has inbound rich-text references.", rem_id=wrapper_id)
                                    plan["complete"] = False
                                else:
                                    plan["omitted_empty_table_schema_wrappers"].append({
                                        "wrapper_id": wrapper_id,
                                        "promoted_label_ids": valid_labels,
                                        "reason": "empty-unreferenced-table-schema-wrapper",
                                        "content_policy": TABLE_SCHEMA_CONTENT_POLICY,
                                        "visibility_state": plan["table_schema_visibility_states"][wrapper_id],
                                        "label_visibility_states": {
                                            label_id: plan["table_schema_visibility_states"][label_id]
                                            for label_id in valid_labels
                                        },
                                        "raw_markers": evidence["raw_markers"],
                                    })
                            if len(covered_labels) != len(set(covered_labels)):
                                issue(portal_id, "table-schema-label-duplicate", "A table schema label is owned by multiple wrappers.")
                                plan["complete"] = False
                            plan["table_schema_label_ids"] = covered_labels
                        plan["table_schema_complete"] = plan["complete"]
                        plan["visibility_contexts"][portal_id] = dict(visibility_states)
                else:
                    issue(portal_id, "search-result-shape-unresolved", "Search portal is neither contextual nor a complete flat result set.")
                    plan["kind"] = "unresolved-search"
                    plan["complete"] = False
        else:
            issue(portal_id, "portal-type-unresolved", f"Unsupported portal type {portal_type!r}.")
            plan["kind"] = "unresolved"
            plan["complete"] = False

        plans[portal_id] = plan

    unresolved = sorted(portal_id for portal_id, plan in plans.items() if plan.get("complete") is not True)
    result = {
        "schema_version": PORTAL_EVIDENCE_SCHEMA,
        "binding": {
            "knowledgebase_id": knowledgebase_id,
            "snapshot_payload_sha256": snapshot_sha256,
            "raw_export_sha256": raw_sha256,
            "scope_policy_sha256": policy_sha256,
            "scope_policy_digest_contract": "canonical-json-selected-portal-fields-v1",
            "source_comparison_receipt_sha256": comparison_sha256,
            "raw_record_id_set_sha256": _stable_id_digest(raw_ids),
            "admitted_portal_id_set_sha256": _stable_id_digest(admitted),
            "excluded_source_id_set_sha256": _stable_id_digest(excluded),
        },
        "render_policy": {
            "policy_id": PORTAL_RENDER_POLICY,
            "provenance": "explicit-user-choice",
            "ordinary": {
                "content_policy": ORDINARY_CONTENT_POLICY,
                "include_collapsed_descendants": True,
                "exclude_explicitly_hidden": True,
                "cycle_guard": "do-not-reenter-an-active-portal-or-source-record",
            },
            "automatic": {
                "content_policy": AUTOMATIC_CONTENT_POLICY,
                "render": "matched-bullet-and-canonical-source-path-link",
                "recurse_into_canonical_subtree": False,
                "exclude_explicitly_hidden": True,
                "owned_table_rows": {
                    "content_policy": TABLE_ROW_CONTENT_POLICY,
                    "render": "full-physically-owned-descendant-subtree",
                    "visibility": "exclude-hidden-descendants-and-their-subtrees",
                },
                "owned_table_schema": {
                    "content_policy": TABLE_SCHEMA_CONTENT_POLICY,
                    "render": "one-schema-section-per-owning-portal",
                    "empty_wrapper": "omit-wrapper-and-promote-ordered-labels",
                },
            },
        },
        "coverage": {
            "scope": "caller-supplied-admitted-portals-under-render-policy",
            "expected_admitted_portal_count": len(admitted),
            "planned_portal_count": len(plans),
            "complete_portal_count": len(plans) - len(unresolved),
            "unresolved_portal_ids": unresolved,
            "complete": not unresolved,
            "complete_semantics": "all-admitted-portals-have-renderable-evidence-plans; not global snapshot completeness",
        },
        "plans": plans,
        "diagnostics": diagnostics,
        "limitations": [
            "Collapse is captured as presentation evidence and never hides ordinary portal descendants.",
            "Automatic-result match-only rendering is an explicit user policy, not an inference from collapse.",
            "The adapter does not mutate capture validation flags or converter_projection.",
            "The caller must validate the bound source-comparison receipt before invoking this pure adapter.",
        ],
    }
    result["artifact_digest"] = {
        "contract": _ARTIFACT_DIGEST_CONTRACT,
        "payload_sha256": portal_evidence_payload_sha256(result),
    }
    return result
