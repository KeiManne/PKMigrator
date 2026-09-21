import unittest

from portal_evidence import (
    PortalEvidenceError,
    derive_portal_evidence,
    portal_scope_policy_sha256,
    validate_portal_evidence_artifact,
)


def record(rem_id, parent=None, *, rem_type=0, **fields):
    value = {"_id": rem_id, "parent": parent}
    if rem_type:
        value["type"] = rem_type
    value.update(fields)
    return value


def portal(portal_id, portal_type, members, *, positions=None, states=None, collapsed=None):
    positions = positions or {item: index for index, item in enumerate(members)}
    states = states or {item: "none" for item in members}
    collapsed = collapsed or {item: True for item in members}
    return {
        "portal_id": portal_id,
        "portal_type": portal_type,
        "membership": {"complete": True, "member_ids": list(members)},
        "positions": {
            "states": {
                item: {"position": value, "visible_position": value}
                for item, value in positions.items()
            }
        },
        "visibility": {"states": dict(states)},
        "collapsed": {"states": dict(collapsed)},
        "automatic_view": None,
    }


def contract(records, portals):
    children = {item: [] for item in records}
    for item, value in records.items():
        parent = value.get("parent")
        if parent in children:
            children[parent].append(item)
    return {
        "schema_version": "remnote-migration-snapshot/v1",
        "capture": {
            "payload_sha256": "a" * 64,
            "knowledgebase_id": "kb",
            "knowledgebase_consistent": True,
        },
        "records": {item: {"id": item, "child_ids": children[item]} for item in records},
        "portals": portals,
        "classifications": {
            "document_and_folder": {
                "states": {
                    item: {"is_document": False, "is_folder": False}
                    for item in records
                }
            }
        },
        "converter_projection": {"portal_snapshots": {}, "visibility_overrides": {}},
    }


class PortalEvidenceTests(unittest.TestCase):
    def derive(self, records, portals, admitted, excluded=()):
        return derive_portal_evidence(
            contract(records, portals),
            records,
            admitted,
            set(excluded),
            raw_export_sha256="b" * 64,
            scope_policy_sha256="c" * 64,
            source_comparison_receipt_sha256="d" * 64,
            expected_knowledgebase_id="kb",
        )

    def validate(self, artifact, records, admitted, excluded=()):
        validate_portal_evidence_artifact(
            artifact,
            expected_knowledgebase_id="kb",
            expected_snapshot_payload_sha256="a" * 64,
            expected_raw_export_sha256="b" * 64,
            expected_scope_policy_sha256="c" * 64,
            expected_source_comparison_receipt_sha256="d" * 64,
            expected_raw_record_ids=records,
            expected_admitted_portal_ids=admitted,
            expected_excluded_source_ids=excluded,
        )

    def test_contextual_search_orders_contexts_and_preserves_anchor_evidence(self):
        records = {
            "doc": record("doc"),
            "branch": record("branch", "doc"),
            "first": record("first", "branch"),
            "second": record("second", "doc"),
            "outer": record("outer", "doc", rem_type=6),
            "ctx-a": record("ctx-a", "outer", rem_type=6),
            "ctx-b": record("ctx-b", "outer", rem_type=6),
        }
        outer = portal(
            "outer", 4, ["ctx-a", "ctx-b"],
            positions={"ctx-a": 1, "ctx-b": 0},
        )
        first = portal("ctx-a", 0, ["first"])
        second = portal("ctx-b", 0, ["second"])
        result = self.derive(records, {"outer": outer, "ctx-a": first, "ctx-b": second}, ["outer"])

        plan = result["plans"]["outer"]
        self.assertTrue(plan["complete"])
        self.assertEqual(plan["ordered_context_portal_ids"], ["ctx-b", "ctx-a"])
        self.assertEqual([item["anchor_id"] for item in plan["appearances"]], ["second", "first"])
        self.assertEqual(plan["appearances"][1]["canonical_ancestry_ids"], ["doc", "branch"])
        self.assertEqual(plan["appearances"][0]["content_policy"], "automatic-match-with-canonical-source-link")
        self.assertEqual(plan["appearances"][0]["canonical_source_path_ids"], ["doc", "second"])
        self.assertEqual(result["render_policy"]["provenance"], "explicit-user-choice")
        self.assertNotIn("descendant_ids", plan["appearances"][0])

    def test_self_containing_context_is_an_anchor_plan_without_recursive_flattening(self):
        records = {
            "doc": record("doc"),
            "outer": record("outer", "doc", rem_type=6),
            "ctx": record("ctx", "outer", rem_type=6),
        }
        outer = portal("outer", 4, ["ctx"])
        context = portal("ctx", 0, ["doc"])
        result = self.derive(records, {"outer": outer, "ctx": context}, ["outer"])

        appearance = result["plans"]["outer"]["appearances"][0]
        self.assertEqual(appearance["anchor_id"], "doc")
        self.assertEqual(appearance["canonical_ancestry_ids"], [])
        self.assertEqual(appearance["content_policy"], "automatic-match-with-canonical-source-link")

    def test_context_scope_omissions_and_multiple_retained_anchors_fail_closed(self):
        records = {
            "doc": record("doc"),
            "outer": record("outer", "doc", rem_type=6),
            "empty": record("empty", "outer", rem_type=6),
            "excluded": record("excluded", "outer", rem_type=6),
            "multi": record("multi", "outer", rem_type=6),
            "drop": record("drop"),
            "one": record("one"),
            "two": record("two"),
        }
        outer = portal("outer", 4, ["empty", "excluded", "multi"])
        portals = {
            "outer": outer,
            "empty": portal("empty", 0, []),
            "excluded": portal("excluded", 0, ["drop"]),
            "multi": portal("multi", 0, ["one", "two"]),
        }
        result = self.derive(records, portals, ["outer"], excluded={"drop"})

        plan = result["plans"]["outer"]
        self.assertFalse(plan["complete"])
        self.assertEqual({item["reason"] for item in plan["omissions"]}, {"empty-context", "excluded-source"})
        self.assertIn("context-multiple-retained-anchors", {item["code"] for item in result["diagnostics"]})

    def test_contextual_hidden_anchor_is_omitted_without_blocking_other_results(self):
        records = {
            "doc": record("doc"),
            "outer": record("outer", "doc", rem_type=6),
            "context": record("context", "outer", rem_type=6),
            "hidden": record("hidden"),
        }
        outer = portal("outer", 4, ["context"])
        context = portal("context", 0, ["hidden"], states={"hidden": "hidden"})
        result = self.derive(records, {"outer": outer, "context": context}, ["outer"])

        plan = result["plans"]["outer"]
        self.assertTrue(plan["complete"])
        self.assertEqual(plan["appearances"], [])
        self.assertEqual(plan["omissions"], [{
            "context_portal_id": "context",
            "anchor_ids": ["hidden"],
            "reason": "hidden-anchor",
        }])

    def test_ordinary_multi_member_order_requires_concordant_visible_positions(self):
        records = {
            "doc": record("doc"),
            "good": record("good", "doc", rem_type=6),
            "bad": record("bad", "doc", rem_type=6),
            "one": record("one"),
            "two": record("two"),
        }
        good = portal("good", 0, ["one", "two"], positions={"one": 3, "two": 8})
        bad = portal("bad", 0, ["one", "two"], positions={"one": 8, "two": 3})
        result = self.derive(records, {"good": good, "bad": bad}, ["good", "bad"])

        self.assertTrue(result["plans"]["good"]["complete"])
        self.assertEqual(result["plans"]["good"]["order_basis"], "sdk-return-order-concordant-visible-position")
        self.assertFalse(result["plans"]["bad"]["complete"])
        self.assertIn("bad", result["coverage"]["unresolved_portal_ids"])

    def test_flat_search_keeps_scope_and_uses_explicit_match_only_policy(self):
        records = {
            "doc": record("doc"),
            "search": record("search", "doc", rem_type=6),
            "keep": record("keep"),
            "drop": record("drop"),
        }
        search = portal("search", 4, ["keep", "drop"], positions={"drop": 0, "keep": 1})
        search["visibility"]["states"] = {"keep": "root", "drop": "root"}
        search["automatic_view"] = {
            "root_result_complete": True,
            "root_result_ids": ["drop", "keep"],
        }
        result = self.derive(records, {"search": search}, ["search"], excluded={"drop"})

        plan = result["plans"]["search"]
        self.assertTrue(plan["complete"])
        self.assertEqual([item["anchor_id"] for item in plan["appearances"]], ["keep"])
        self.assertEqual(plan["appearances"][0]["content_policy"], "automatic-match-with-canonical-source-link")

    def test_flat_search_requires_root_state_unique_position_and_order(self):
        records = {
            "doc": record("doc"),
            "search": record("search", "doc", rem_type=6),
            "one": record("one"),
            "two": record("two"),
        }
        search = portal("search", 4, ["one", "two"], positions={"one": 1, "two": 0})
        search["visibility"]["states"] = {"one": "root", "two": "root"}
        search["automatic_view"] = {
            "root_result_complete": True,
            "root_result_ids": ["one", "two"],
        }
        result = self.derive(records, {"search": search}, ["search"])

        self.assertFalse(result["plans"]["search"]["complete"])
        self.assertIn("flat-result-order-mismatch", {item["code"] for item in result["diagnostics"]})

    def test_flat_search_emits_each_owned_table_row_as_a_separate_match(self):
        records = {
            "doc": record("doc"),
            "search": record("search", "doc", rem_type=6),
            "row-one": record("row-one"),
            "row-two": record("row-two"),
            "cell-one": record("cell-one", "row-one"),
            "cell-two": record("cell-two", "row-two"),
        }
        search = portal(
            "search", 4, ["row-one", "cell-one", "row-two", "cell-two"],
            positions={"row-one": 0, "cell-one": 0, "row-two": 1, "cell-two": 0},
            states={"row-one": "root", "cell-one": "none", "row-two": "root", "cell-two": "none"},
        )
        search["automatic_view"] = {
            "root_result_complete": True,
            "root_result_ids": ["row-one", "row-two"],
        }
        result = self.derive(records, {"search": search}, ["search"])

        plan = result["plans"]["search"]
        self.assertTrue(plan["complete"])
        self.assertEqual([item["anchor_id"] for item in plan["appearances"]], ["row-one", "row-two"])
        self.assertTrue(all(item["content_policy"] == "automatic-match-with-canonical-source-link" for item in plan["appearances"]))

    def test_flat_table_binds_owned_cells_schema_labels_and_empty_wrapper_omission(self):
        records = {
            "doc": record("doc"),
            "search": record("search", "doc", rem_type=6),
            "schema": record("schema", "search", key=["Column"], apu={"g": {"v": True}}, ps={}, tco={}),
            "label": record("label", "schema", key=["Owner"], apu={"y": {"v": True}}, opfl="label"),
            "row": record("row", "schema", rem_type=1, key=["Match"]),
            "cell": record("cell", "row", rem_type=2, key=["Value"]),
            "note": record("note", "cell", key=["Detail"]),
            "empty-schema": record("empty-schema", "search", key=[], apu={"g": {"v": True}}, ps={}, tco={}),
            "promoted-label": record("promoted-label", "empty-schema", key=["Status"], apu={"y": {"v": True}}, opfl="label"),
        }
        search = portal(
            "search", 4, ["row", "cell", "note"],
            positions={"row": 0, "cell": 0, "note": 0},
            states={"row": "root", "cell": "none", "note": "none"},
        )
        search["automatic_view"] = {"root_result_complete": True, "root_result_ids": ["row"]}

        result = self.derive(records, {"search": search}, ["search"])
        plan = result["plans"]["search"]

        self.assertTrue(plan["complete"])
        appearance = plan["appearances"][0]
        self.assertEqual(appearance["owned_table_content_policy"], "physically-owned-table-row-subtree")
        self.assertEqual(appearance["owned_table_descendant_ids"], ["cell", "note"])
        self.assertEqual(appearance["owned_table_descendant_visibility_states"], {"cell": "none", "note": "none"})
        self.assertEqual(plan["table_schema_roots"][0]["wrapper_id"], "schema")
        self.assertEqual(plan["table_schema_roots"][0]["ordered_label_ids"], ["label"])
        self.assertEqual(plan["omitted_empty_table_schema_wrappers"][0]["wrapper_id"], "empty-schema")
        self.assertEqual(plan["omitted_empty_table_schema_wrappers"][0]["promoted_label_ids"], ["promoted-label"])
        self.assertEqual(plan["table_schema_label_ids"], ["label", "promoted-label"])

    def test_hidden_owned_table_cell_and_its_subtree_are_bound_as_omitted(self):
        records = {
            "doc": record("doc"),
            "search": record("search", "doc", rem_type=6, ph={"label": {"h": "h"}}),
            "schema": record("schema", "search", key=["Column"], apu={"g": {"v": True}}, ps={}, tco={}),
            "label": record("label", "schema", key=["Owner"], apu={"y": {"v": True}}, opfl="label"),
            "row": record("row", "schema", rem_type=1),
            "cell": record("cell", "row", rem_type=2),
            "child": record("child", "cell"),
        }
        search = portal(
            "search", 4, ["row", "cell", "child"],
            positions={"row": 0, "cell": 0, "child": 0},
            states={"row": "root", "cell": "hidden", "child": "none"},
        )
        search["automatic_view"] = {"root_result_complete": True, "root_result_ids": ["row"]}

        plan = self.derive(records, {"search": search}, ["search"])["plans"]["search"]

        self.assertTrue(plan["complete"])
        appearance = plan["appearances"][0]
        self.assertEqual(appearance["owned_table_descendant_ids"], [])
        self.assertEqual(appearance["hidden_table_descendant_ids"], ["cell"])
        self.assertNotIn("child", appearance["owned_table_descendant_visibility_states"])
        self.assertEqual(plan["hidden_table_schema_record_ids"], ["label"])
        self.assertEqual(plan["table_schema_roots"][0]["ordered_label_ids"], [])
        self.assertEqual(plan["table_schema_visibility_states"]["label"], {
            "sdk_state": None,
            "raw_ph_state": "h",
            "explicit_hidden": True,
        })
        self.assertIn("hidden-table-schema-label", {item["reason"] for item in plan["omissions"]})

    def test_contextual_shape_requires_context_records_to_be_owned_by_outer_portal(self):
        records = {
            "doc": record("doc"),
            "outer": record("outer", "doc", rem_type=6),
            "ordinary": record("ordinary", "doc", rem_type=6),
            "anchor": record("anchor"),
        }
        outer = portal("outer", 4, ["ordinary"])
        ordinary = portal("ordinary", 0, ["anchor"])
        result = self.derive(records, {"outer": outer, "ordinary": ordinary}, ["outer"])

        self.assertFalse(result["plans"]["outer"]["complete"])
        self.assertEqual(result["plans"]["outer"]["kind"], "unresolved-search")

    def test_duplicate_membership_and_unrecognized_visibility_fail_closed(self):
        records = {
            "doc": record("doc"),
            "duplicate": record("duplicate", "doc", rem_type=6),
            "state": record("state", "doc", rem_type=6),
            "anchor": record("anchor"),
        }
        duplicate = portal("duplicate", 0, ["anchor", "anchor"])
        state = portal("state", 0, ["anchor"], states={"anchor": "future-state"})
        result = self.derive(records, {"duplicate": duplicate, "state": state}, ["duplicate", "state"])

        self.assertFalse(result["plans"]["duplicate"]["complete"])
        self.assertFalse(result["plans"]["state"]["complete"])
        self.assertTrue(
            {"portal-membership-duplicate", "anchor-visibility-unresolved"}.issubset(
                {item["code"] for item in result["diagnostics"]}
            )
        )

    def test_hidden_anchor_is_omitted_while_collapse_does_not_hide_ordinary_descendants(self):
        records = {
            "doc": record("doc"),
            "ordinary": record("ordinary", "doc", rem_type=6),
            "hidden": record("hidden"),
            "visible": record("visible"),
        }
        ordinary = portal(
            "ordinary", 0, ["hidden", "visible"],
            states={"hidden": "hidden", "visible": "none"},
            collapsed={"hidden": True, "visible": True},
        )
        result = self.derive(records, {"ordinary": ordinary}, ["ordinary"])

        plan = result["plans"]["ordinary"]
        self.assertTrue(plan["complete"])
        self.assertEqual([item["anchor_id"] for item in plan["appearances"]], ["visible"])
        self.assertEqual(plan["appearances"][0]["content_policy"], "ordinary-full-eligible-subtree")
        self.assertEqual(plan["omissions"], [{
            "context_portal_id": "ordinary",
            "anchor_ids": ["hidden"],
            "reason": "hidden-anchor",
        }])
        self.assertTrue(result["render_policy"]["ordinary"]["include_collapsed_descendants"])

    def test_ordinary_self_containment_records_cycle_guard_and_keeps_descendants(self):
        records = {
            "doc": record("doc"),
            "child": record("child", "doc"),
            "ordinary": record("ordinary", "doc", rem_type=6),
        }
        ordinary = portal(
            "ordinary", 0, ["doc"],
            states={"doc": "none", "child": "none"},
            collapsed={"doc": True},
        )
        result = self.derive(records, {"ordinary": ordinary}, ["ordinary"])

        plan = result["plans"]["ordinary"]
        self.assertTrue(plan["complete"])
        self.assertEqual(plan["cycle_guard_ids"], ["ordinary"])
        self.assertEqual(plan["eligible_source_record_count"], 2)

    def test_ordinary_descendant_with_missing_visibility_fails_closed(self):
        records = {
            "doc": record("doc"),
            "child": record("child", "doc"),
            "ordinary": record("ordinary", rem_type=6),
        }
        ordinary = portal("ordinary", 0, ["doc"])
        result = self.derive(records, {"ordinary": ordinary}, ["ordinary"])

        self.assertFalse(result["plans"]["ordinary"]["complete"])
        self.assertIn("ordinary-descendant-visibility-unresolved", {item["code"] for item in result["diagnostics"]})

    def test_missing_collapse_observation_does_not_block_content_plan(self):
        records = {
            "doc": record("doc"),
            "ordinary": record("ordinary", rem_type=6),
        }
        ordinary = portal("ordinary", 0, ["doc"])
        ordinary["collapsed"]["states"] = {}
        result = self.derive(records, {"ordinary": ordinary}, ["ordinary"])

        plan = result["plans"]["ordinary"]
        self.assertTrue(plan["complete"])
        self.assertIsNone(plan["appearances"][0]["collapsed"])
        self.assertFalse(plan["appearances"][0]["collapsed_observation_complete"])

    def test_binding_rejects_id_set_or_knowledgebase_drift(self):
        records = {"portal": record("portal", rem_type=6)}
        portals = {"portal": portal("portal", 0, [])}
        snapshot = contract(records, portals)
        with self.assertRaisesRegex(PortalEvidenceError, "identity"):
            derive_portal_evidence(
                snapshot, records, ["portal"], set(),
                raw_export_sha256="b" * 64,
                scope_policy_sha256="c" * 64,
                source_comparison_receipt_sha256="d" * 64,
                expected_knowledgebase_id="other",
            )
        with self.assertRaisesRegex(PortalEvidenceError, "ID sets differ"):
            derive_portal_evidence(
                snapshot, {}, ["portal"], set(),
                raw_export_sha256="b" * 64,
                scope_policy_sha256="c" * 64,
                source_comparison_receipt_sha256="d" * 64,
                expected_knowledgebase_id="kb",
            )

    def test_scope_digest_tracks_only_portal_relevant_policy_fields(self):
        policy = {
            "exclude_subtree_roots": {"excluded": "reason"},
            "exclude_source_ids": {},
            "document_plan": {"doc": {"path": "Folder/Doc.md"}},
            "asset_omissions": {"asset": "unrelated"},
        }
        digest = portal_scope_policy_sha256(policy)
        changed_unrelated = dict(policy, asset_omissions={"other": "still unrelated"})
        changed_relevant = dict(policy, document_plan={"doc": {"path": "Elsewhere/Doc.md"}})

        self.assertEqual(digest, portal_scope_policy_sha256(changed_unrelated))
        self.assertNotEqual(digest, portal_scope_policy_sha256(changed_relevant))

    def test_artifact_digest_and_bindings_reject_plan_mutation(self):
        records = {
            "portal": record("portal", rem_type=6),
            "anchor": record("anchor"),
        }
        artifact = self.derive(records, {"portal": portal("portal", 0, ["anchor"])}, ["portal"])
        self.validate(artifact, records, ["portal"])

        artifact["plans"]["portal"]["appearances"][0]["order"] = 99
        with self.assertRaisesRegex(PortalEvidenceError, "payload digest mismatch"):
            self.validate(artifact, records, ["portal"])


if __name__ == "__main__":
    unittest.main()
