import json
import copy
import tempfile
import unittest
import zipfile
from pathlib import Path

from remnote_export import (
    CHILD_ORDER_BASIS,
    Converter,
    ExportError,
    RICH_FINGERPRINT_ALGORITHM,
    _snapshot_rich_fingerprint,
    create_source_comparison_receipt,
    load_export,
    load_markdown_boundaries,
    load_snapshot_contract,
    main,
    safe_full_component,
    safe_stem,
    stable_anchor,
)
from portal_evidence import derive_portal_evidence, portal_scope_policy_sha256


def rem(rem_id, key, parent=None, order="a0", **extra):
    value = {
        "_id": rem_id,
        "key": key if isinstance(key, list) else [key],
        "parent": parent,
        "f": order,
        "createdAt": 10,
        "m": 20,
        "u": 30,
    }
    value.update(extra)
    return value


def snapshot_contract(docs, *, child_ids=None, portals=None, runtime=None):
    child_ids = child_ids or {}
    records = {
        source["_id"]: {
            "id": source["_id"],
            "type": source.get("type", 0),
            "parent_id": source.get("parent"),
            "child_ids": child_ids.get(source["_id"], []),
            "text": source.get("key"),
            "back_text": source.get("value"),
            "created_at": source.get("createdAt"),
            "updated_at": source.get("u"),
            "export_comparable_rich_text_fingerprint": _snapshot_rich_fingerprint(source),
        }
        for source in docs
    }
    return {
        "schema_version": "remnote-migration-snapshot/v1",
        "capture": {
            "knowledgebase_id": "synthetic-kb",
            "complete": True,
            "payload_sha256": "a" * 64,
            "export_comparison": {
                "rich_text_algorithm": RICH_FINGERPRINT_ALGORITHM,
                "structural_fields": ["id", "parent_id", "child_ids"],
                "child_order_raw_basis": CHILD_ORDER_BASIS,
                "raw_input_fields": ["key", "value"],
                "sdk_input_fields": ["text", "backText"],
                "calibrated_equivalent": True,
                "child_order_calibrated": True,
            },
        },
        "records": records,
        "runtime_returned_records": runtime or {},
        "portals": portals or {},
        "converter_projection": {"portal_snapshots": {}, "visibility_overrides": {}},
    }


class ConverterTests(unittest.TestCase):
    def convert(self, docs, roots=("root",), **kwargs):
        payload = {
            "knowledgebaseId": "synthetic-kb",
            "exportDate": "2030-01-01T00:00:00Z",
            "exportVersion": 1,
            "docs": docs,
        }
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        output = Path(temporary.name)
        converter = Converter(payload, "f" * 64, roots, **kwargs)
        manifest = converter.convert(output)
        notes = {item["root_id"]: (output / item["path"]).read_text() for item in manifest["files"]}
        return manifest, notes, output

    def test_repeated_portal_appearances_are_nested_and_have_unique_anchors(self):
        docs = [
            rem("root", "Root", n=1),
            rem("source", "Source", "root", "a0"),
            rem("detail", "Detail", "source", "a0"),
            rem("portal-one", "", "root", "a1", type=6, pd={"source": {"d": True}}),
            rem("portal-two", "", "root", "a2", type=6, pd={"source": {"d": "a0"}}),
        ]
        manifest, notes, _ = self.convert(docs)
        body = notes["root"]
        self.assertEqual(body.count("- Source"), 3)
        self.assertEqual(body.count("  - Detail"), 3)
        occurrences = manifest["source_map"]["source"]["occurrences"]
        self.assertEqual([x["kind"] for x in occurrences], ["canonical", "portal-copy", "portal-copy"])
        self.assertEqual(len({x["anchor"] for x in occurrences}), 3)
        self.assertEqual(manifest["status"], "complete_for_requested_pilot")

    def test_visibility_override_hides_only_that_portal_and_collapse_does_not_hide(self):
        docs = [
            rem("root", "Root", n=1),
            rem("source", "Source", "root", "a0"),
            rem("shown", "Shown", "source", "a0", ic=True),
            rem("hidden", "Hidden", "source", "a1"),
            rem(
                "portal",
                "",
                "root",
                "a1",
                type=6,
                pd={"source": {"d": True}},
                ph={"hidden": {"h": "h"}},
            ),
        ]
        manifest, notes, _ = self.convert(docs)
        body = notes["root"]
        self.assertEqual(body.count("Shown"), 2, "collapsed ownership content must still be copied")
        self.assertEqual(body.count("Hidden"), 1, "the ordinary canonical occurrence remains visible")
        self.assertFalse(any(x["code"] == "ambiguous_portal_visibility" for x in manifest["issues"]))

    def test_ambiguous_visibility_and_search_views_are_explicit_failures(self):
        docs = [
            rem("root", "Root", n=1),
            rem("source", "Source"),
            rem("portal", "", "root", "a0", type=6, pd={"source": {"d": True}}, ph={"source": {"h": "r"}}),
            rem("search", "", "root", "a1", type=6, portalType=4, searchResults=["source"], pd={"source": {"d": True}}),
        ]
        manifest, _, _ = self.convert(docs)
        self.assertEqual(manifest["status"], "incomplete")
        codes = {x["code"] for x in manifest["issues"]}
        self.assertIn("ambiguous_portal_visibility", codes)
        self.assertIn("unsupported_required_view", codes)

    def test_evidenced_search_snapshot_renders_members(self):
        docs = [
            rem("root", "Root", n=1),
            rem("source", "Snapshot member"),
            rem("search", "", "root", type=6, portalType=4, searchResults=["stale"], pd={}),
        ]
        manifest, notes, _ = self.convert(
            docs,
            portal_snapshots={"search": {"evidence": "synthetic fixture", "members": ["source"]}},
        )
        self.assertIn("Snapshot member", notes["root"])
        self.assertEqual(manifest["status"], "complete_for_requested_pilot")
        self.assertEqual(manifest["configuration"]["portal_snapshots"]["search"]["evidence"], "synthetic fixture")

    def test_multiple_boolean_portal_roots_require_order_evidence(self):
        docs = [
            rem("root", "Root", n=1),
            rem("one", "One"),
            rem("two", "Two"),
            rem("portal", "", "root", type=6, pd={"one": {"d": True}, "two": {"d": True}}),
        ]
        manifest, notes, _ = self.convert(docs)
        self.assertIn("One", notes["root"])
        self.assertIn("Two", notes["root"])
        self.assertEqual(manifest["status"], "incomplete")
        self.assertIn("portal_boolean_order_uncertain", {x["code"] for x in manifest["issues"]})

    def test_mixed_string_and_boolean_portal_roots_require_order_evidence(self):
        docs = [
            rem("root", "Root", n=1),
            rem("one", "One"),
            rem("two", "Two"),
            rem("portal", "", "root", type=6, pd={"one": {"d": "a0"}, "two": {"d": True}}),
        ]
        manifest, _, _ = self.convert(docs)
        self.assertIn("portal_boolean_order_uncertain", {x["code"] for x in manifest["issues"]})

    def test_non_search_portal_enum_is_not_misclassified_or_rendered(self):
        docs = [
            rem("root", "Root", n=1),
            rem("source", "Source"),
            rem("portal", "", "root", type=6, portalType=2, pd={"source": {"d": True}}),
        ]
        manifest, notes, _ = self.convert(docs)
        self.assertNotIn("Source", notes["root"])
        codes = {x["code"] for x in manifest["issues"]}
        self.assertIn("unsupported_portal_type", codes)
        self.assertNotIn("unsupported_required_view", codes)

    def test_visibility_override_requires_evidence_and_can_resolve_unknown_state(self):
        docs = [
            rem("root", "Root", n=1),
            rem("source", "Source"),
            rem("portal", "", "root", type=6, pd={"source": {"d": True}}, ph={"source": {"h": "r"}}),
        ]
        manifest, notes, _ = self.convert(
            docs,
            visibility_overrides={"portal": {"evidence": "synthetic fixture", "states": {"source": "hidden"}}},
        )
        self.assertNotIn("Source", notes["root"])
        self.assertEqual(manifest["status"], "complete_for_requested_pilot")
        self.assertEqual(manifest["configuration"]["visibility_overrides"]["portal"]["evidence"], "synthetic fixture")

    def test_portal_root_is_rejected_without_canonical_lookup_failure(self):
        docs = [rem("source", "Source"), rem("portal", "", type=6, pd={"source": {"d": True}})]
        manifest, notes, _ = self.convert(docs, roots=("portal",))
        self.assertEqual(notes, {})
        self.assertEqual(manifest["status"], "incomplete")
        self.assertIn("portal_root_unsupported", {x["code"] for x in manifest["issues"]})

    def test_portal_cycle_stops_only_recursive_branch(self):
        docs = [
            rem("root", "Root", n=1),
            rem("source", "Source"),
            rem("nested", "", "source", type=6, pd={"source": {"d": True}}),
            rem("portal", "", "root", type=6, pd={"source": {"d": True}}),
        ]
        manifest, notes, _ = self.convert(docs)
        self.assertIn("cycle stopped", notes["root"])
        self.assertEqual([x["code"] for x in manifest["issues"]].count("cycle"), 1)
        self.assertEqual(manifest["status"], "complete_for_requested_pilot")

    def test_rich_text_front_back_assets_and_escaping(self):
        docs = [
            rem("root", "CON", n=1),
            rem(
                "rich",
                [
                    "literal *stars* ",
                    {"i": "m", "text": "both", "b": True, "l": True},
                    " ",
                    {"i": "m", "text": "mark", "h": 2},
                    " ",
                    {"i": "x", "text": "x^2"},
                    " ",
                    {"i": "m", "text": "a`b", "q": True},
                    " ",
                    {"i": "i", "url": "https://assets.example.test/image.png?token=one"},
                ],
                "root",
                value=[{"i": "m", "text": "answer", "b": True}],
            ),
        ]
        manifest, notes, _ = self.convert(docs)
        body = notes["root"]
        self.assertIn(r"literal \*stars\*", body)
        self.assertIn("***both***", body)
        self.assertIn("==mark==", body)
        self.assertIn("$x^2$", body)
        self.assertIn("``a`b``", body)
        self.assertIn(" — **answer**", body)
        asset = manifest["assets"]["https://assets.example.test/image.png?token=one"]
        self.assertRegex(asset["relative_path"], r"^Attachments/RemNote/[0-9a-f]{24}\.png$")
        self.assertEqual(len(asset["occurrences"]), 1)
        self.assertTrue(manifest["files"][0]["path"].startswith("notes/_CON--"))

    def test_actual_export_math_and_code_shapes(self):
        docs = [
            rem("root", "Root", n=1),
            rem("math", [{"i": "x", "text": r"x^2 + y^2"}], "root", "a0"),
            rem("block-math", [{"i": "x", "text": r"\sum_i x_i", "block": True}], "root", "a1"),
            rem("code", [{"i": "m", "text": "value = 1\nprint(value)", "code": True, "language": "python"}], "root", "a2"),
        ]
        _, notes, _ = self.convert(docs)
        body = notes["root"]
        self.assertIn("$x^2 + y^2$", body)
        self.assertIn("$$\n  \\sum_i x_i\n  $$", body)
        self.assertIn("- ```python\n  value = 1\n  print(value)\n  ```\n  ^rem-", body)

    def test_code_segments_are_separated_from_prose_and_each_other(self):
        docs = [
            rem("root", "Root", n=1),
            rem(
                "code",
                [
                    "Before",
                    {"i": "m", "text": "first()", "code": True, "language": "python"},
                    {"i": "m", "text": "second()", "code": True, "language": "python"},
                    "After",
                ],
                "root",
            ),
        ]
        _, notes, _ = self.convert(docs)
        body = notes["root"]
        self.assertIn("- Before\n  ```python\n  first()\n  ```\n  ```python\n  second()\n  ```\n  After\n  ^rem-", body)

    def test_root_value_is_rendered_and_in_search_text(self):
        docs = [
            rem(
                "root",
                "Root question",
                n=1,
                value=[{"i": "m", "text": "answer = 1", "code": True, "language": "python"}],
            )
        ]
        manifest, notes, _ = self.convert(docs)
        self.assertIn("—\n\n```python\nanswer = 1\n```", notes["root"])
        self.assertEqual(manifest["source_map"]["root"]["plain_original_text"], "Root question — answer = 1")
        occurrence = manifest["source_map"]["root"]["occurrences"][0]
        self.assertEqual(occurrence["kind"], "canonical")

    def test_overlapping_roots_keep_own_canonicals_and_parent_uses_link(self):
        docs = [
            rem("parent", "Parent", n=1),
            rem("child-doc", "Child document", "parent", n=1),
            rem("child-body", "Child body", "child-doc"),
        ]
        manifest, notes, _ = self.convert(docs, roots=("parent", "child-doc"))
        child_canonical = manifest["source_map"]["child-doc"]["canonical"]
        self.assertEqual(child_canonical["file"], manifest["files"][1]["path"])
        self.assertIn("[[" + child_canonical["file"] + "#^", notes["parent"])
        self.assertNotIn("Child body", notes["parent"])
        self.assertIn("Child body", notes["child-doc"])
        self.assertEqual(
            [x["kind"] for x in manifest["source_map"]["child-doc"]["occurrences"]],
            ["document-link", "canonical"],
        )

    def test_portal_owned_helpers_do_not_receive_fake_canonical_targets(self):
        docs = [
            rem("root", "Root", n=1),
            rem("source", "Source"),
            rem("portal", "", "root", type=6, pd={"source": {"d": True}}),
            rem("query-helper", "Internal query", "portal"),
        ]
        converter = Converter({"docs": docs}, "f" * 64, ["root"])
        self.assertNotIn("query-helper", converter.canonical)

    def test_plain_reference_expansion_is_cycle_safe(self):
        docs = [
            rem("root", "Root", n=1),
            rem("left", [{"i": "q", "_id": "right"}], "root"),
            rem("right", [{"i": "q", "_id": "left"}]),
        ]
        converter = Converter({"docs": docs}, "f" * 64, ["root"])
        self.assertIn("((right))", converter.plain_rich(converter.index["left"]["key"]))

    def test_occurrence_budget_is_hard_and_reported(self):
        docs = [rem("root", "Root", n=1)] + [rem(f"child-{i}", f"Child {i}", "root", f"a{i}") for i in range(5)]
        manifest, notes, _ = self.convert(docs, max_occurrences=3)
        self.assertEqual(manifest["status"], "incomplete")
        self.assertEqual(manifest["counts"]["occurrences"], 3)
        self.assertEqual(sum(x["code"] == "occurrence_limit" for x in manifest["issues"]), 1)
        self.assertNotIn("Child 4", notes["root"])

    def test_helpers_are_stable_and_safe(self):
        self.assertEqual(stable_anchor("alpha", "ctx"), stable_anchor("alpha", "ctx"))
        stem = safe_stem("../A:B? ", "alpha")
        self.assertNotIn("/", stem)
        self.assertNotIn(":", stem)
        self.assertLessEqual(len(stem), 92)

    def test_archive_cli_and_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            archive = base / "fixture.rem"
            payload = {"exportVersion": 1, "docs": [rem("root", "Root", n=1), rem("child", "Child", "root")]}
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("rem.json", json.dumps(payload))
            loaded, fingerprint = load_export(archive)
            self.assertEqual(loaded["docs"][0]["_id"], "root")
            self.assertEqual(len(fingerprint), 64)
            output = base / "out"
            self.assertEqual(main(["--input", str(archive), "--output", str(output), "--roots", "root"]), 0)
            manifest = json.loads((output / "manifest.json").read_text())
            self.assertEqual(manifest["source_map"]["child"]["timestamps"], {"createdAt": 10, "m": 20, "u": 30})

    def test_full_boundaries_use_native_paths_but_emit_safe_identity_paths(self):
        title = "Folder#[unsafe]^"
        child_title = "多" * 100 + "#[child]^"
        docs = [
            rem("root", title, n=1),
            rem("body", "Parent body", "root", "a0"),
            rem("child-doc", child_title, "root", "a1"),
            rem("child-body", "Child body", "child-doc", "a0"),
        ]
        payload = {"knowledgebaseId": "synthetic-kb", "docs": docs}
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            archive = base / "native.zip"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr(f"{title}.md", "- Parent body\n")
                handle.writestr(f"{title}/{child_title}.md", "- Child body\n")
            file_map, evidence = load_markdown_boundaries(payload, archive)
            self.assertEqual(len(file_map), 2)
            self.assertEqual(evidence["match_methods"], {"ownership_path": 2})
            for path in file_map.values():
                self.assertTrue(path.startswith("Sources/RemNote/"))
                self.assertNotRegex(path, r"[#\[\]^]")
                for component in Path(path).parts[2:]:
                    limit = 124 if component.endswith(".md") else 120
                    self.assertLessEqual(len(component.encode("utf-8")), limit)
            converter = Converter(
                payload,
                "f" * 64,
                (),
                file_map=file_map,
                mode="full",
                boundary_evidence=evidence,
            )
            output = base / "out"
            manifest = converter.convert(output)
            self.assertEqual(manifest["status"], "complete_full_migration")
            self.assertEqual(len(manifest["record_ledger"]), len(docs))
            parent = (output / file_map["root"]).read_text()
            child = (output / file_map["child-doc"]).read_text()
            self.assertIn(file_map["child-doc"], parent)
            self.assertIn("多" * 100, child)
            self.assertEqual(manifest["source_map"]["child-doc"]["plain_original_text"], child_title)
            self.assertEqual(manifest["record_ledger"]["child-doc"]["disposition"], "included_document")

    def test_full_ledger_keeps_portal_descendant_content_and_empty_wrappers_unresolved(self):
        docs = [
            rem("root", "Root"),
            rem("search", "", "root", "a0", type=6, portalType=4, searchResults=["missing"]),
            rem("helper", "query:", "search", "a0"),
            rem("empty-wrapper", [], "search", "a1"),
        ]
        payload = {"knowledgebaseId": "synthetic-kb", "docs": docs}
        converter = Converter(
            payload,
            "f" * 64,
            (),
            file_map={"root": "Sources/RemNote/Root--0000000000.md"},
            mode="full",
            boundary_evidence={"markdown_files": 1},
        )
        with tempfile.TemporaryDirectory() as temporary:
            manifest = converter.convert(Path(temporary))
        self.assertEqual(manifest["status"], "incomplete_full_migration")
        self.assertEqual(len(manifest["record_ledger"]), len(docs))
        self.assertEqual(manifest["record_ledger"]["search"]["disposition"], "unresolved_required_view")
        self.assertEqual(manifest["record_ledger"]["helper"]["disposition"], "unresolved_portal_descendant")
        self.assertTrue(manifest["record_ledger"]["helper"]["content_bearing"])
        self.assertEqual(manifest["record_ledger"]["empty-wrapper"]["disposition"], "unresolved_portal_descendant")
        self.assertFalse(manifest["record_ledger"]["empty-wrapper"]["content_bearing"])
        issue = next(x for x in manifest["issues"] if x["code"] == "unresolved_portal_descendants")
        self.assertEqual(
            issue["details"],
            {"record_count": 2, "content_bearing_count": 1, "empty_wrapper_count": 1},
        )
        self.assertNotIn("unexplained_missing_record", {issue["code"] for issue in manifest["issues"]})

    def test_deleted_reference_fallback_keeps_readable_label_and_unresolved_id(self):
        docs = [
            rem("root", "Root"),
            rem(
                "body",
                [{"i": "q", "_id": "deleted", "textOfDeletedRem": ["Readable deleted label"]}],
                "root",
            ),
        ]
        payload = {"knowledgebaseId": "synthetic-kb", "docs": docs}
        file_map = {"root": "Sources/RemNote/Root--0000000000.md"}
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            manifest = Converter(payload, "f" * 64, (), file_map=file_map, mode="full").convert(output)
            body = (output / file_map["root"]).read_text()
        self.assertIn("Readable deleted label ((deleted))", body)
        self.assertEqual(
            manifest["source_map"]["body"]["plain_original_text"],
            "Readable deleted label ((deleted))",
        )
        issue = next(x for x in manifest["issues"] if x["code"] == "unresolved_reference")
        self.assertEqual(issue["details"], {"target_id": "deleted", "fallback_label_preserved": True})

    def test_full_pdf_container_keeps_annotation_text(self):
        docs = [rem("pdf", "Reference.pdf"), rem("annotation", "Readable annotation", "pdf")]
        payload = {"knowledgebaseId": "synthetic-kb", "docs": docs}
        file_map = {"pdf": "Sources/RemNote/Reference.pdf--0000000000.md"}
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            manifest = Converter(payload, "f" * 64, (), file_map=file_map, mode="full").convert(output)
            body = (output / file_map["pdf"]).read_text()
        self.assertIn("Readable annotation", body)
        self.assertEqual(manifest["record_ledger"]["pdf"]["disposition"], "included_pdf_text_container")
        self.assertIn("pdf_binaries", manifest["omissions"])

    def test_full_references_keep_system_labels_but_not_fake_links(self):
        docs = [
            rem("root", "Root"),
            rem(
                "body",
                [
                    "Known property: ",
                    {"i": "q", "_id": "property"},
                    "; missing: ",
                    {"i": "q", "_id": "absent"},
                ],
                "root",
            ),
            rem("property", "Status"),
        ]
        payload = {"knowledgebaseId": "synthetic-kb", "docs": docs}
        file_map = {"root": "Sources/RemNote/Root--0000000000.md"}
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            manifest = Converter(payload, "f" * 64, (), file_map=file_map, mode="full").convert(output)
            body = (output / file_map["root"]).read_text()
        self.assertIn("Known property: Status", body)
        self.assertNotIn("((property))", body)
        self.assertIn("((absent))", body)
        self.assertEqual(manifest["record_ledger"]["property"]["disposition"], "unresolved_outside_document_boundary")
        codes = [issue["code"] for issue in manifest["issues"]]
        self.assertEqual(codes.count("outside_document_reference_label_only"), 1)
        self.assertEqual(codes.count("unresolved_reference"), 1)
        self.assertEqual(codes.count("unclassified_outside_document_boundary"), 1)

    def test_positive_per_record_sdk_evidence_excludes_system_definition(self):
        docs = [
            rem("root", "Root"),
            rem("body", [{"i": "q", "_id": "property"}], "root"),
            rem("property", "Status"),
        ]
        snapshot = {
            "capture": {
                "complete": True,
                "mode": "complete",
                "knowledgebase_id": "synthetic-kb",
                "knowledgebase_id_at_end": "synthetic-kb",
                "knowledgebase_consistent": True,
                "scope": {
                    "expected_portal_count": 0,
                    "processed_portal_count": 0,
                    "requested_portal_ids": [],
                    "missing_requested_portal_ids": [],
                },
                "export_comparison": {
                    "rich_text_algorithm": RICH_FINGERPRINT_ALGORITHM,
                    "structural_fields": ["id", "parent_id", "child_ids"],
                    "child_order_raw_basis": CHILD_ORDER_BASIS,
                    "raw_input_fields": ["key", "value"],
                    "sdk_input_fields": ["text", "backText"],
                    "calibrated_equivalent": True,
                    "child_order_calibrated": True,
                },
            },
            "portals": {},
            "classifications": {
                "document_and_folder": {
                    "states": {"root": {"is_document": True, "is_folder": False}}
                },
                "system_definition": {
                    "states": {
                        "property": {
                            "is_powerup": False,
                            "is_powerup_enum": False,
                            "is_powerup_property_list_item": False,
                            "is_powerup_slot": False,
                            "is_powerup_property": True,
                        }
                    }
                },
            },
        }
        payload = {"knowledgebaseId": "synthetic-kb", "docs": docs}
        file_map = {"root": "Sources/RemNote/Root--0000000000.md"}
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Converter(
                payload,
                "f" * 64,
                (),
                file_map=file_map,
                mode="full",
                snapshot_contract=snapshot,
            ).convert(Path(temporary))
        self.assertEqual(manifest["record_ledger"]["property"]["disposition"], "excluded_system_definition")
        self.assertIn("system_reference_label_only", {issue["code"] for issue in manifest["issues"]})
        self.assertNotIn("excluded_reference", {issue["code"] for issue in manifest["issues"]})
        self.assertNotIn("unclassified_outside_document_boundary", {issue["code"] for issue in manifest["issues"]})

    def test_document_plan_rejects_sdk_classified_system_boundary(self):
        docs = [rem("system", "System")]
        snapshot = snapshot_contract(docs, child_ids={"system": []})
        snapshot["classifications"] = {
            "system_definition": {
                "states": {
                    "system": {
                        "is_powerup": True,
                        "is_powerup_enum": False,
                        "is_powerup_property_list_item": False,
                        "is_powerup_slot": False,
                        "is_powerup_property": False,
                    }
                }
            }
        }
        payload = {"knowledgebaseId": "synthetic-kb", "docs": docs}
        with self.assertRaises(ExportError):
            Converter(
                payload,
                "f" * 64,
                (),
                file_map={"system": "Sources/RemNote/System.md"},
                mode="full",
                snapshot_contract=snapshot,
                document_plan={
                    "system": {
                        "path": "Sources/RemNote/System.md",
                        "evidence": "Synthetic reviewed boundary",
                    }
                },
            )

    def test_partial_snapshot_scope_cannot_pass_full_admission(self):
        docs = [
            rem("root", "Root"),
            rem("source", "Source"),
            rem("portal", "", "root", type=6, pd={"source": {"d": True}}),
        ]
        snapshot = {
            "capture": {
                "complete": True,
                "mode": "calibration",
                "knowledgebase_id": "synthetic-kb",
                "knowledgebase_id_at_end": "synthetic-kb",
                "knowledgebase_consistent": True,
                "scope": {
                    "expected_portal_count": 0,
                    "processed_portal_count": 0,
                    "requested_portal_ids": [],
                    "missing_requested_portal_ids": [],
                },
                "export_comparison": {
                    "rich_text_algorithm": RICH_FINGERPRINT_ALGORITHM,
                    "structural_fields": ["id", "parent_id", "child_ids"],
                    "child_order_raw_basis": CHILD_ORDER_BASIS,
                    "raw_input_fields": ["key", "value"],
                    "sdk_input_fields": ["text", "backText"],
                    "calibrated_equivalent": True,
                    "child_order_calibrated": True,
                },
            },
            "portals": {},
            "classifications": {
                "document_and_folder": {
                    "states": {"root": {"is_document": True, "is_folder": False}}
                }
            },
        }
        payload = {"knowledgebaseId": "synthetic-kb", "docs": docs}
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Converter(
                payload,
                "f" * 64,
                (),
                file_map={"root": "Sources/RemNote/Root--0000000000.md"},
                mode="full",
                snapshot_contract=snapshot,
            ).convert(Path(temporary))
        self.assertEqual(manifest["status"], "incomplete_full_migration")
        self.assertIn("snapshot_scope_incomplete", {issue["code"] for issue in manifest["issues"]})

    def test_native_boundary_ambiguity_is_rejected_without_timestamp_evidence(self):
        payload = {"docs": [rem("one", "Same", createdAt=1), rem("two", "Same", createdAt=2)]}
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "native.zip"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("Same.md", "")
            with self.assertRaises(ExportError):
                load_markdown_boundaries(payload, archive)

    def test_snapshot_contract_validates_identity_and_projection(self):
        source = rem("root", "Root")
        contract = {
            "schema_version": "remnote-migration-snapshot/v1",
            "capture": {
                "knowledgebase_id": "synthetic-kb",
                "complete": True,
                "export_comparison": {
                    "rich_text_algorithm": RICH_FINGERPRINT_ALGORITHM,
                    "structural_fields": ["id", "parent_id", "child_ids"],
                    "child_order_raw_basis": CHILD_ORDER_BASIS,
                    "raw_input_fields": ["key", "value"],
                    "sdk_input_fields": ["text", "backText"],
                    "calibrated_equivalent": True,
                    "child_order_calibrated": True,
                },
            },
            "records": {
                "root": {
                    "id": "root",
                    "parent_id": None,
                    "child_ids": [],
                    "export_comparable_rich_text_fingerprint": _snapshot_rich_fingerprint(source),
                }
            },
            "converter_projection": {"portal_snapshots": {}, "visibility_overrides": {}},
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "snapshot.json"
            path.write_text(json.dumps(contract))
            loaded, digest = load_snapshot_contract(path, {"knowledgebaseId": "synthetic-kb", "docs": [source]})
            self.assertEqual(loaded, contract)
            self.assertEqual(len(digest), 64)
            with self.assertRaises(ExportError):
                load_snapshot_contract(path, {"knowledgebaseId": "different-kb", "docs": [source]})
            drifted = dict(source, key=["Changed"])
            with self.assertRaises(ExportError):
                load_snapshot_contract(path, {"knowledgebaseId": "synthetic-kb", "docs": [drifted]})
            with self.assertRaises(ExportError):
                load_snapshot_contract(path, {"docs": [source]})

    def test_snapshot_media_url_counterparts_share_v2_fingerprint(self):
        remote = rem(
            "root",
            [{"i": "i", "url": "https://remnote-user-data.s3.amazonaws.com/asset.png"}],
        )
        local = rem("root", [{"i": "i", "url": "%LOCAL_FILE%asset.png"}])
        contract = snapshot_contract([remote])
        contract["records"]["root"]["export_comparable_rich_text_fingerprint"] = _snapshot_rich_fingerprint(local)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "snapshot.json"
            path.write_text(json.dumps(contract))
            load_snapshot_contract(path, {"knowledgebaseId": "synthetic-kb", "docs": [remote]})

    def test_snapshot_media_normalization_is_path_and_asset_strict(self):
        remote = rem(
            "root",
            [{
                "i": "i",
                "url": "https://remnote-user-data.s3.amazonaws.com/asset.png",
                "title": "https://remnote-user-data.s3.amazonaws.com/title.png",
            }],
        )
        variants = [
            rem("root", [{"i": "i", "url": "%LOCAL_FILE%other.png", "title": remote["key"][0]["title"]}]),
            rem("root", [{"i": "i", "url": "%LOCAL_FILE%asset.png", "title": "%LOCAL_FILE%title.png"}]),
            rem("root", [{"i": "m", "url": "%LOCAL_FILE%asset.png", "title": remote["key"][0]["title"]}]),
        ]
        for variant in variants:
            with self.subTest(variant=variant):
                contract = snapshot_contract([remote])
                contract["records"]["root"]["export_comparable_rich_text_fingerprint"] = _snapshot_rich_fingerprint(variant)
                with tempfile.TemporaryDirectory() as temporary:
                    path = Path(temporary) / "snapshot.json"
                    path.write_text(json.dumps(contract))
                    with self.assertRaisesRegex(ExportError, "rich_text=1"):
                        load_snapshot_contract(path, {"knowledgebaseId": "synthetic-kb", "docs": [remote]})

    def test_snapshot_does_not_normalize_literal_non_media_or_other_host_urls(self):
        pairs = [
            (rem("root", ["https://remnote-user-data.s3.amazonaws.com/asset.png"]),
             rem("root", ["%LOCAL_FILE%asset.png"])),
            (rem("root", [{"i": "m", "url": "https://remnote-user-data.s3.amazonaws.com/asset.png"}]),
             rem("root", [{"i": "m", "url": "%LOCAL_FILE%asset.png"}])),
            (rem("root", [{"i": "i", "url": "https://assets.example.test/asset.png"}]),
             rem("root", [{"i": "i", "url": "%LOCAL_FILE%asset.png"}])),
        ]
        for raw, sdk in pairs:
            with self.subTest(raw=raw):
                contract = snapshot_contract([raw])
                contract["records"]["root"]["export_comparable_rich_text_fingerprint"] = _snapshot_rich_fingerprint(sdk)
                with tempfile.TemporaryDirectory() as temporary:
                    path = Path(temporary) / "snapshot.json"
                    path.write_text(json.dumps(contract))
                    with self.assertRaisesRegex(ExportError, "rich_text=1"):
                        load_snapshot_contract(path, {"knowledgebaseId": "synthetic-kb", "docs": [raw]})

    def test_snapshot_rejects_lost_user_note_and_parent_change(self):
        root = rem("root", "Root")
        child = rem("child", "Child", "root")
        contract = snapshot_contract([root, child], child_ids={"root": ["child"]})
        lost = json.loads(json.dumps(contract))
        del lost["records"]["child"]
        moved = json.loads(json.dumps(contract))
        moved["records"]["child"]["parent_id"] = None
        for candidate in (lost, moved):
            with tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "snapshot.json"
                path.write_text(json.dumps(candidate))
                with self.assertRaises(ExportError):
                    load_snapshot_contract(path, {"knowledgebaseId": "synthetic-kb", "docs": [root, child]})

    def test_snapshot_allows_only_proven_generated_search_context_replacement(self):
        root = rem("root", "Root")
        search = rem("search", [{"i": "q", "_id": "query"}], "root", type=6, portalType=4)
        old = rem(
            "old-context", [], "search", type=6,
            embeddedSearchId="search-id", searchResults=["source"], value=None,
        )
        docs = [root, search, old]
        contract = snapshot_contract(docs, child_ids={"root": ["search"], "search": ["old-context"]})
        del contract["records"]["old-context"]
        contract["records"]["new-context"] = {
            "id": "new-context", "type": 6, "parent_id": "search", "child_ids": [],
            "text": None, "back_text": None,
            "export_comparable_rich_text_fingerprint": _snapshot_rich_fingerprint(rem("new-context", [])),
        }
        contract["runtime_returned_records"]["new-context"] = {
            "id": "new-context", "type": 6, "parent_id": "search", "child_ids": [],
            "text": [], "back_text": None, "bulk_present": True,
        }
        contract["portals"]["search"] = {
            "portal_type_name": "search_portal",
            "nested_contexts": {"detected_ids": ["new-context"]},
        }
        contract["records"]["search"]["child_ids"] = ["new-context"]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "snapshot.json"
            path.write_text(json.dumps(contract))
            load_snapshot_contract(path, {"knowledgebaseId": "synthetic-kb", "docs": docs})
            converter = Converter(
                {"knowledgebaseId": "synthetic-kb", "docs": docs},
                "f" * 64,
                (),
                file_map={"root": "Sources/RemNote/Root--0000000000.md"},
                mode="full",
                snapshot_contract=contract,
            )
            self.assertEqual(
                converter._build_full_record_ledger()["old-context"]["disposition"],
                "excluded_generated_search_context",
            )
            missing_proof = json.loads(json.dumps(contract))
            missing_proof["portals"]["search"]["nested_contexts"]["detected_ids"] = []
            path.write_text(json.dumps(missing_proof))
            with self.assertRaisesRegex(ExportError, "identities drifted"):
                load_snapshot_contract(path, {"knowledgebaseId": "synthetic-kb", "docs": docs})
            runtime_with_child = json.loads(json.dumps(contract))
            runtime_with_child["runtime_returned_records"]["new-context"]["child_ids"] = ["owned"]
            path.write_text(json.dumps(runtime_with_child))
            with self.assertRaisesRegex(ExportError, "identities drifted"):
                load_snapshot_contract(path, {"knowledgebaseId": "synthetic-kb", "docs": docs})
            non_search_docs = json.loads(json.dumps(docs))
            non_search_docs[1].pop("portalType")
            path.write_text(json.dumps(contract))
            with self.assertRaisesRegex(ExportError, "identities drifted"):
                load_snapshot_contract(path, {"knowledgebaseId": "synthetic-kb", "docs": non_search_docs})
            owned = rem("owned", "Owned", "old-context")
            old_with_owned_docs = docs + [owned]
            old_with_owned = json.loads(json.dumps(contract))
            old_with_owned["records"]["owned"] = {
                "id": "owned", "type": 0, "parent_id": "old-context", "child_ids": [],
                "text": ["Owned"], "back_text": None,
                "export_comparable_rich_text_fingerprint": _snapshot_rich_fingerprint(owned),
            }
            path.write_text(json.dumps(old_with_owned))
            with self.assertRaisesRegex(ExportError, "identities drifted"):
                load_snapshot_contract(path, {"knowledgebaseId": "synthetic-kb", "docs": old_with_owned_docs})

    def test_snapshot_uses_complete_sdk_order_for_tied_or_null_fractional_positions(self):
        root = rem("root", "Root")
        first = rem("first", "First", "root", None)
        second = rem("second", "Second", "root", None)
        docs = [root, first, second]
        contract = snapshot_contract(docs, child_ids={"root": ["second", "first"]})
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "snapshot.json"
            path.write_text(json.dumps(contract))
            load_snapshot_contract(path, {"knowledgebaseId": "synthetic-kb", "docs": docs})
        converter = Converter({"docs": docs}, "f" * 64, ["root"], snapshot_contract=contract)
        self.assertEqual(converter.children["root"], ["second", "first"])

    def test_snapshot_rejects_incomplete_sdk_children_for_unique_or_ambiguous_order(self):
        for positions in (("a0", "a1"), (None, None)):
            root = rem("root", "Root")
            first = rem("first", "First", "root", positions[0])
            second = rem("second", "Second", "root", positions[1])
            docs = [root, first, second]
            contract = snapshot_contract(docs, child_ids={"root": ["first"]})
            with self.subTest(positions=positions), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "snapshot.json"
                path.write_text(json.dumps(contract))
                with self.assertRaisesRegex(ExportError, "child_order_unresolved=1"):
                    load_snapshot_contract(path, {"knowledgebaseId": "synthetic-kb", "docs": docs})
                converter = Converter({"docs": docs}, "f" * 64, ["root"], snapshot_contract=contract)
                self.assertIn("snapshot_child_order_unresolved", {issue["code"] for issue in converter.issues})

    def test_rerun_removes_only_unchanged_manifest_owned_stale_files(self):
        first_payload = {"docs": [rem("one", "One"), rem("two", "Two")]}
        second_payload = {"docs": [rem("one", "One")]}
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            one_path = "Sources/RemNote/One--0000000000.md"
            two_path = "Sources/RemNote/Two--0000000000.md"
            Converter(
                first_payload,
                "f" * 64,
                (),
                file_map={"one": one_path, "two": two_path},
                mode="full",
            ).convert(output)
            self.assertTrue((output / two_path).exists())
            Converter(
                second_payload,
                "e" * 64,
                (),
                file_map={"one": one_path},
                mode="full",
            ).convert(output)
            self.assertFalse((output / two_path).exists())
            (output / one_path).write_text("manual edit")
            with self.assertRaises(ExportError):
                Converter(
                    second_payload,
                    "e" * 64,
                    (),
                    file_map={"one": one_path},
                    mode="full",
                ).convert(output)

    def test_snapshot_rejects_sibling_order_drift_after_calibration(self):
        root = rem("root", "Root")
        first = rem("first", "First", "root", "a0")
        second = rem("second", "Second", "root", "a1")
        docs = [root, first, second]
        records = {}
        for source, children in ((root, ["second", "first"]), (first, []), (second, [])):
            records[source["_id"]] = {
                "id": source["_id"],
                "parent_id": source.get("parent"),
                "child_ids": children,
                "export_comparable_rich_text_fingerprint": _snapshot_rich_fingerprint(source),
            }
        contract = {
            "schema_version": "remnote-migration-snapshot/v1",
            "capture": {
                "knowledgebase_id": "synthetic-kb",
                "complete": True,
                "export_comparison": {
                    "rich_text_algorithm": RICH_FINGERPRINT_ALGORITHM,
                    "structural_fields": ["id", "parent_id", "child_ids"],
                    "child_order_raw_basis": CHILD_ORDER_BASIS,
                    "raw_input_fields": ["key", "value"],
                    "sdk_input_fields": ["text", "backText"],
                    "calibrated_equivalent": True,
                    "child_order_calibrated": True,
                },
            },
            "records": records,
            "converter_projection": {"portal_snapshots": {}, "visibility_overrides": {}},
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "snapshot.json"
            path.write_text(json.dumps(contract))
            with self.assertRaises(ExportError):
                load_snapshot_contract(path, {"knowledgebaseId": "synthetic-kb", "docs": docs})

    def test_public_source_comparison_recomputes_when_capture_flags_are_false(self):
        docs = [rem("root", "Root"), rem("child", "Child", "root")]
        payload = {"knowledgebaseId": "synthetic-kb", "docs": docs}
        snapshot = snapshot_contract(
            docs,
            child_ids={"root": ["child"], "child": []},
        )
        snapshot["capture"]["export_comparison"]["calibrated_equivalent"] = False
        snapshot["capture"]["export_comparison"]["child_order_calibrated"] = False
        receipt, digest = create_source_comparison_receipt(
            payload, "a" * 64, snapshot, "b" * 64
        )
        self.assertEqual(receipt["status"], "complete")
        self.assertEqual(len(digest), 64)
        snapshot["records"]["child"]["export_comparable_rich_text_fingerprint"] = (
            "fnv1a64-canonical-richtext-v2-media-url:0000000000000000"
        )
        with self.assertRaises(ExportError):
            create_source_comparison_receipt(payload, "a" * 64, snapshot, "b" * 64)

    def test_scope_exclusion_gates_subtree_references_portal_copies_and_assets(self):
        asset_url = "https://assets.example.test/excluded.png"
        docs = [
            rem("root", "Root"),
            rem("reference", ["See ", {"i": "q", "_id": "excluded"}], "root", "a0"),
            rem("retained", "Retained", "root", "a1"),
            rem("excluded", "Excluded title", "root", "a2"),
            rem("excluded-child", [{"i": "i", "url": asset_url}], "excluded", "a0"),
            rem(
                "portal",
                "",
                "root",
                "a3",
                type=6,
                pd={"excluded": {"d": "a0"}, "retained": {"d": "a1"}},
            ),
        ]
        payload = {"knowledgebaseId": "synthetic-kb", "docs": docs}
        path = "Sources/RemNote/Root--0000000000.md"
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            manifest = Converter(
                payload,
                "f" * 64,
                (),
                file_map={"root": path},
                mode="full",
                exclude_subtree_roots={"excluded": "synthetic scope decision"},
            ).convert(output)
            body = (output / path).read_text()
        self.assertEqual(manifest["status"], "complete_full_migration")
        self.assertIn("[excluded source]", body)
        self.assertNotIn("Excluded title", body)
        self.assertNotIn(asset_url, manifest["assets"])
        self.assertNotIn("excluded", manifest["source_map"])
        self.assertNotIn("excluded-child", manifest["source_map"])
        self.assertEqual(len(manifest["source_map"]["retained"]["occurrences"]), 2)
        for rem_id in ("excluded", "excluded-child"):
            self.assertEqual(manifest["record_ledger"][rem_id]["disposition"], "excluded_by_scope")
        self.assertEqual(manifest["counts"]["scope_excluded_records"], 2)
        self.assertIn("excluded_reference", {issue["code"] for issue in manifest["issues"]})

    def test_excluded_subtree_does_not_follow_portal_membership(self):
        docs = [
            rem("root", "Root"),
            rem("retained", "Retained external source", "root", "a0"),
            rem("excluded", "Excluded branch", "root", "a1"),
            rem("portal", "", "excluded", "a0", type=6, pd={"retained": {"d": True}}),
        ]
        payload = {"knowledgebaseId": "synthetic-kb", "docs": docs}
        path = "Sources/RemNote/Root--0000000000.md"
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Converter(
                payload,
                "f" * 64,
                (),
                file_map={"root": path},
                mode="full",
                exclude_subtree_roots={"excluded": "synthetic scope decision"},
            ).convert(Path(temporary))
        self.assertIn("retained", manifest["source_map"])
        self.assertEqual(len(manifest["source_map"]["retained"]["occurrences"]), 1)
        self.assertEqual(manifest["record_ledger"]["portal"]["disposition"], "excluded_by_scope")

    def test_reference_metadata_materializes_external_link_before_scope_exclusion(self):
        external_url = "https://example.test/a_(b)[c]<d>"
        target = rem(
            "metadata-target",
            "Fallback target text",
            "metadata-root",
            tp={"link-type": {"t": False}},
            crt={"b": {"u": {"s": external_url}, "t": {"s": "Site [A] <B>"}}},
            ps={
                "b_u": {"v": {"s": external_url}},
                "b_t": {"v": {"s": "Site [A] <B>"}},
            },
        )
        docs = [
            rem("root", "Root"),
            rem("owner", [{"i": "q", "_id": "metadata-target"}], "root", "a0"),
            rem("portal", "", "root", "a1", type=6, pd={"owner": {"d": True}}),
            rem("metadata-root", "Metadata root"),
            target,
        ]
        payload = {"knowledgebaseId": "synthetic-kb", "docs": docs}
        path = "Sources/RemNote/Root.md"
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            manifest = Converter(
                payload,
                "f" * 64,
                (),
                file_map={"root": path},
                mode="full",
                exclude_subtree_roots={"metadata-root": "materialized metadata index"},
                reference_metadata={
                    "reviewed_root_ids": ["metadata-root"],
                    "link_type_id": "link-type",
                    "evidence": "synthetic reviewed metadata fields",
                },
            ).convert(output)
            body = (output / path).read_text()
        expected_link = r"[Site \[A\] \<B\>](https://example.test/a_%28b%29%5Bc%5D%3Cd%3E)"
        self.assertEqual(body.count(expected_link), 2)
        self.assertNotIn("Fallback target text", body)
        self.assertNotIn("metadata-target", manifest["source_map"])
        report = manifest["reference_metadata"]
        self.assertEqual(report["status"], "complete")
        self.assertEqual(report["counts"]["expected_retained_edges"], 1)
        self.assertEqual(report["counts"]["materialized_retained_edges"], 1)
        self.assertEqual(report["counts"]["materialization_appearances"], 2)
        self.assertEqual(report["missing_retained_edges"], [])
        self.assertEqual(
            manifest["source_map"]["owner"]["plain_original_text"],
            f"Site [A] <B> ({external_url})",
        )

    def test_reference_metadata_unresolved_retained_edge_is_an_error(self):
        target = rem(
            "metadata-target",
            "Target",
            "metadata-root",
            tp={"link-type": {"t": False}},
            crt={"b": {"u": {"s": "https://one.test/"}, "t": {"s": "One"}}},
            ps={
                "b_u": {"v": {"s": "https://two.test/"}},
                "b_t": {"v": {"s": "One"}},
            },
        )
        docs = [
            rem("root", "Root"),
            rem("owner", [{"i": "q", "_id": "metadata-target"}], "root"),
            rem("metadata-root", "Metadata root"),
            target,
        ]
        payload = {"knowledgebaseId": "synthetic-kb", "docs": docs}
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Converter(
                payload,
                "f" * 64,
                (),
                file_map={"root": "Sources/RemNote/Root.md"},
                mode="full",
                exclude_subtree_roots={"metadata-root": "materialized metadata index"},
                reference_metadata={
                    "reviewed_root_ids": ["metadata-root"],
                    "link_type_id": "link-type",
                    "evidence": "synthetic reviewed metadata fields",
                },
            ).convert(Path(temporary))
        self.assertEqual(manifest["status"], "incomplete_full_migration")
        self.assertEqual(manifest["reference_metadata"]["counts"]["unresolved_retained_edges"], 1)
        self.assertIn(
            "reference_metadata_unresolved_edges",
            {issue["code"] for issue in manifest["issues"]},
        )

    def test_reviewed_asset_omission_renders_external_marker_and_no_local_embed(self):
        source = "https://example.test/image_(missing)[1].png"
        docs = [
            rem("root", "Root"),
            rem("image", [{"i": "i", "url": source, "alt": "Diagram [draft]"}], "root", "a0"),
            rem("portal", "", "root", "a1", type=6, pd={"image": {"d": True}}),
        ]
        manifest, notes, _ = self.convert(
            docs,
            asset_omissions={source: "Synthetic reviewed upstream failure"},
        )
        marker = r"[Image unavailable: Diagram \[draft\]](https://example.test/image_%28missing%29%5B1%5D.png)"
        self.assertEqual(notes["root"].count(marker), 2)
        self.assertNotIn("![[Attachments/RemNote/", notes["root"])
        self.assertNotIn(source, manifest["assets"])
        self.assertEqual(manifest["asset_omissions"][source]["occurrence_count"], 2)
        self.assertEqual(manifest["counts"]["asset_omission_occurrences"], 2)

    def test_asset_omission_validation_and_unused_configuration_fail_closed(self):
        docs = [rem("root", "Root")]
        payload = {"knowledgebaseId": "synthetic-kb", "docs": docs}
        with self.assertRaises(ExportError):
            Converter(payload, "f" * 64, ("root",), asset_omissions={"file:///tmp/x": "missing"})
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Converter(
                payload,
                "f" * 64,
                ("root",),
                asset_omissions={"https://example.test/missing.png": "Synthetic reviewed failure"},
            ).convert(Path(temporary))
        self.assertEqual(manifest["status"], "incomplete")
        self.assertIn(
            "configured_asset_omission_unused",
            {issue["code"] for issue in manifest["issues"]},
        )

    def test_evidenced_flat_table_match_preserves_multiline_row_and_cells_without_recursion(self):
        docs = [
            rem("root", "Root"),
            rem("search", "", "root", "a0", type=6, portalType=4),
            rem("schema", "Schema", "search", "a0", apu={"g": {"v": True}}, ps={}, tco={}),
            rem("label", "Column", "schema", "a0", apu={"y": {"v": True}}, opfl="label"),
            rem("row", [{"i": "o", "text": "print('row')", "language": "python"}], "schema", "a1", type=1),
            rem("cell", "Cell", "row", "a0", value=[{"i": "x", "text": "x+y"}], type=2),
            rem("hidden-cell", "Secret cell", "row", "a1", type=2),
        ]
        child_ids = {
            "root": ["search"],
            "search": ["schema"],
            "schema": ["label", "row"],
            "label": [],
            "row": ["cell", "hidden-cell"],
            "cell": [],
            "hidden-cell": [],
        }
        portal = {
            "portal_id": "search",
            "portal_type": 4,
            "membership": {"complete": True, "member_ids": ["row", "cell", "hidden-cell"]},
            "positions": {
                "states": {
                    "row": {"position": 0, "visible_position": 0},
                    "cell": {"position": 0, "visible_position": 0},
                    "hidden-cell": {"position": 1, "visible_position": 1},
                }
            },
            "visibility": {"states": {"row": "root", "cell": "none", "hidden-cell": "hidden"}},
            "collapsed": {"states": {"row": True, "cell": False, "hidden-cell": False}},
            "automatic_view": {"root_result_complete": True, "root_result_ids": ["row"]},
        }
        snapshot = snapshot_contract(docs, child_ids=child_ids, portals={"search": portal})
        snapshot["classifications"] = {
            "document_and_folder": {
                "states": {
                    source["_id"]: {
                        "is_document": source["_id"] == "root",
                        "is_folder": False,
                    }
                    for source in docs
                }
            }
        }
        snapshot["capture"].update({
            "mode": "complete",
            "knowledgebase_consistent": True,
            "scope": {
                "expected_portal_count": 1,
                "processed_portal_count": 1,
                "requested_portal_ids": ["search"],
                "missing_requested_portal_ids": [],
            },
        })
        payload = {"knowledgebaseId": "synthetic-kb", "docs": docs}
        converter = Converter(
            payload,
            "f" * 64,
            (),
            file_map={"root": "Sources/RemNote/Root.md"},
            mode="full",
            snapshot_contract=snapshot,
            snapshot_contract_sha256="b" * 64,
        )
        receipt, receipt_sha = create_source_comparison_receipt(
            payload, "f" * 64, snapshot, "b" * 64
        )
        evidence = derive_portal_evidence(
            snapshot,
            converter.index,
            converter.admitted_portal_locations(),
            set(),
            raw_export_sha256="f" * 64,
            scope_policy_sha256=portal_scope_policy_sha256({
                "exclude_subtree_roots": {},
                "exclude_source_ids": {},
                "document_plan": {},
            }),
            source_comparison_receipt_sha256=receipt_sha,
            expected_knowledgebase_id="synthetic-kb",
        )
        mutated = copy.deepcopy(evidence)
        mutated["plans"]["search"]["appearances"][0]["order"] = 99
        with self.assertRaises(ExportError):
            converter.install_portal_evidence(
                mutated,
                source_comparison_receipt=receipt,
                source_comparison_receipt_sha256=receipt_sha,
            )
        converter.install_portal_evidence(
            evidence,
            source_comparison_receipt=receipt,
            source_comparison_receipt_sha256=receipt_sha,
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            manifest = converter.convert(output)
            body = (output / "Sources/RemNote/Root.md").read_text()
        self.assertIn("- ```python\n  print('row')\n  ```\n  ^", body)
        self.assertNotRegex(body, r"``` \^rem-")
        self.assertIn("Source: Root / Schema / [[Sources/RemNote/Root.md#^", body)
        self.assertIn("- Cell — $x+y$", body)
        self.assertIn("- **Columns**", body)
        self.assertIn("- Schema", body)
        self.assertIn("- Column", body)
        self.assertNotIn("Secret cell", body)
        self.assertIn("cell", manifest["source_map"])
        self.assertNotIn("hidden-cell", manifest["source_map"])
        self.assertEqual(
            manifest["record_ledger"]["hidden-cell"]["disposition"],
            "omitted_explicitly_hidden_portal_content",
        )
        self.assertNotIn(
            "unresolved_portal_descendants",
            {issue["code"] for issue in manifest["issues"]},
        )

    def test_exact_source_exclusion_promotes_retained_child_to_existing_owner(self):
        docs = [
            rem("root", "Root"),
            rem("excluded", "Excluded middle", "root"),
            rem("child", "Retained child", "excluded"),
        ]
        payload = {"knowledgebaseId": "synthetic-kb", "docs": docs}
        path = "Sources/RemNote/Root--0000000000.md"
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            manifest = Converter(
                payload,
                "f" * 64,
                (),
                file_map={"root": path},
                mode="full",
                exclude_source_ids={"excluded": "synthetic exact exclusion"},
            ).convert(output)
            body = (output / path).read_text()
        self.assertIn("Retained child", body)
        self.assertNotIn("Excluded middle", body)
        self.assertEqual(manifest["source_map"]["child"]["canonical"]["file"], path)
        self.assertEqual(manifest["record_ledger"]["excluded"]["disposition"], "excluded_by_scope")
        self.assertNotIn(
            "excluded_parent_retained_child_unowned",
            {issue["code"] for issue in manifest["issues"]},
        )

    def test_system_boundary_precedence_fails_closed_for_unowned_retained_child(self):
        docs = [rem("system", "System definition"), rem("child", "Retained child", "system")]
        snapshot = snapshot_contract(docs)
        snapshot.pop("records")
        snapshot["capture"].update({
            "mode": "complete",
            "knowledgebase_id_at_end": "synthetic-kb",
            "knowledgebase_consistent": True,
            "scope": {
                "expected_portal_count": 0,
                "processed_portal_count": 0,
                "requested_portal_ids": [],
                "missing_requested_portal_ids": [],
            },
        })
        snapshot["classifications"] = {
            "document_and_folder": {
                "states": {"system": {"is_document": True, "is_folder": False}}
            },
            "system_definition": {
                "method": "synthetic per-record predicates",
                "states": {
                    "system": {
                        "is_powerup": True,
                        "is_powerup_enum": False,
                        "is_powerup_property_list_item": False,
                        "is_powerup_slot": False,
                        "is_powerup_property": False,
                    }
                },
            },
        }
        payload = {"knowledgebaseId": "synthetic-kb", "docs": docs}
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Converter(
                payload,
                "f" * 64,
                (),
                file_map={"system": "Sources/RemNote/System--0000000000.md"},
                mode="full",
                snapshot_contract=snapshot,
            ).convert(Path(temporary))
        self.assertEqual(manifest["status"], "incomplete_full_migration")
        self.assertEqual(manifest["files"], [])
        system = manifest["record_ledger"]["system"]
        self.assertEqual(system["disposition"], "excluded_system_definition")
        self.assertEqual(system["system_definition_evidence"]["positive_predicates"], ["is_powerup"])
        issue = next(
            issue for issue in manifest["issues"]
            if issue["code"] == "excluded_parent_retained_child_unowned"
        )
        self.assertEqual(issue["rem_id"], "child")

    def test_document_plan_merges_removed_boundary_and_adds_reviewed_split(self):
        docs = [
            rem("root", "Root"),
            rem("nested", "Nested former document", "root", "a0"),
            rem("nested-body", "Nested body", "nested", "a0"),
            rem("split", "Reviewed section", "root", "a1"),
            rem("split-body", "Split body", "split", "a0"),
        ]
        payload = {"knowledgebaseId": "synthetic-kb", "docs": docs}
        native = {
            "root": "Sources/RemNote/Old/Root--0000000000.md",
            "nested": "Sources/RemNote/Old/Nested--0000000000.md",
        }
        root_path = "Sources/RemNote/Topic.md"
        split_path = "Sources/RemNote/Topic/Reviewed section.md"
        plan = {
            "root": {"path": root_path, "evidence": "synthetic reviewed group"},
            "split": {"path": split_path, "evidence": "synthetic reviewed split"},
        }
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            manifest = Converter(
                payload,
                "f" * 64,
                (),
                file_map=native,
                mode="full",
                boundary_evidence={"markdown_files": 2},
                document_plan=plan,
            ).convert(output)
            root_body = (output / root_path).read_text()
            split_body = (output / split_path).read_text()
        self.assertIn("Nested former document", root_body)
        self.assertIn("Nested body", root_body)
        self.assertIn(split_path, root_body)
        self.assertIn("Split body", split_body)
        self.assertEqual(manifest["source_map"]["nested"]["canonical"]["file"], root_path)
        self.assertEqual(manifest["source_map"]["split"]["canonical"]["file"], split_path)
        self.assertEqual(manifest["record_ledger"]["nested"]["disposition"], "included_source")
        self.assertEqual(manifest["record_ledger"]["split"]["disposition"], "included_document")
        self.assertEqual(manifest["document_boundaries"], {"markdown_files": 2})
        self.assertEqual(manifest["output_document_plan"]["added_boundary_ids"], ["split"])
        self.assertEqual(manifest["output_document_plan"]["removed_boundary_ids"], ["nested"])

    def test_scope_and_document_plan_validation_fail_closed(self):
        docs = [rem("root", "Root"), rem("other", "Other")]
        payload = {"knowledgebaseId": "synthetic-kb", "docs": docs}
        native = {"root": "Sources/RemNote/Root.md"}
        cases = (
            {"exclude_source_ids": {"missing": "reason"}},
            {"document_plan": {"missing": {"path": "Sources/RemNote/Missing.md", "evidence": "reviewed"}}},
            {"document_plan": {"root": {"path": "../escape.md", "evidence": "reviewed"}}},
            {"document_plan": {"root": {"path": "Sources/RemNote/Bad#anchor.md", "evidence": "reviewed"}}},
            {
                "reference_metadata": {
                    "reviewed_root_ids": ["missing"],
                    "link_type_id": "link-type",
                    "evidence": "reviewed",
                }
            },
            {
                "reference_metadata": {
                    "reviewed_root_ids": ["root"],
                    "link_type_id": "",
                    "evidence": "reviewed",
                }
            },
            {
                "document_plan": {
                    "root": {"path": "Sources/RemNote/Same.md", "evidence": "reviewed"},
                    "other": {"path": "sources/remnote/same.md", "evidence": "reviewed"},
                }
            },
        )
        for kwargs in cases:
            with self.subTest(kwargs=kwargs), self.assertRaises(ExportError):
                Converter(payload, "f" * 64, (), file_map=native, mode="full", **kwargs)

    def test_document_plan_cli_and_safe_rerun(self):
        docs = [rem("root", "Root"), rem("excluded", "Excluded", "root")]
        payload = {"knowledgebaseId": "synthetic-kb", "docs": docs}
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source = base / "rem.json"
            source.write_text(json.dumps(payload))
            native = base / "native.zip"
            with zipfile.ZipFile(native, "w") as handle:
                handle.writestr("Root.md", "- Excluded\n")
            output = base / "out"
            old_path = "Sources/RemNote/Grouped.md"
            config = base / "config.json"
            config.write_text(json.dumps({
                "mode": "full",
                "exclude_source_ids": {"excluded": "synthetic exact exclusion"},
                "document_plan": {
                    "root": {"path": old_path, "evidence": "synthetic reviewed plan"}
                },
            }))
            self.assertEqual(main([
                "--input", str(source),
                "--output", str(output),
                "--markdown-export", str(native),
                "--config", str(config),
            ]), 0)
            self.assertTrue((output / old_path).exists())
            new_path = "Sources/RemNote/Revised.md"
            revised = json.loads(config.read_text())
            revised["document_plan"]["root"]["path"] = new_path
            config.write_text(json.dumps(revised))
            self.assertEqual(main([
                "--input", str(source),
                "--output", str(output),
                "--markdown-export", str(native),
                "--config", str(config),
            ]), 0)
            self.assertFalse((output / old_path).exists())
            self.assertTrue((output / new_path).exists())
            (output / new_path).write_text("manual edit")
            self.assertEqual(main([
                "--input", str(source),
                "--output", str(output),
                "--markdown-export", str(native),
                "--config", str(config),
            ]), 2)

    def test_safe_full_component_has_identity_and_unicode_byte_bound(self):
        component = safe_full_component("界" * 100 + "#[x]^", "source-id")
        self.assertNotRegex(component, r"[#\[\]^]")
        self.assertLessEqual(len(component.encode("utf-8")), 120)
        self.assertEqual(component, safe_full_component("界" * 100 + "#[x]^", "source-id"))


if __name__ == "__main__":
    unittest.main()
