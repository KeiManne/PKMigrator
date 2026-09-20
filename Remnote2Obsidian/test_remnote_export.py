import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from remnote_export import (
    Converter,
    ExportError,
    _snapshot_rich_fingerprint,
    load_export,
    load_markdown_boundaries,
    load_snapshot_contract,
    main,
    safe_full_component,
    safe_stem,
    stable_anchor,
)


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

    def test_full_ledger_accounts_for_unresolved_view_and_query_helper(self):
        docs = [
            rem("root", "Root"),
            rem("search", "", "root", "a0", type=6, portalType=4, searchResults=["missing"]),
            rem("helper", "query:", "search", "a0"),
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
        self.assertEqual(manifest["record_ledger"]["helper"]["disposition"], "excluded_portal_implementation_record")
        self.assertNotIn("unexplained_missing_record", {issue["code"] for issue in manifest["issues"]})

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
                    "rich_text_algorithm": "fnv1a64-canonical-richtext-v1",
                    "structural_fields": ["id", "parent_id", "child_ids"],
                    "child_order_raw_basis": "Compare SDK children array order with raw siblings sorted by fractional f, using raw record ordinal as the tie-break.",
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
        self.assertNotIn("unclassified_outside_document_boundary", {issue["code"] for issue in manifest["issues"]})

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
                    "rich_text_algorithm": "fnv1a64-canonical-richtext-v1",
                    "structural_fields": ["id", "parent_id", "child_ids"],
                    "child_order_raw_basis": "Compare SDK children array order with raw siblings sorted by fractional f, using raw record ordinal as the tie-break.",
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
                    "rich_text_algorithm": "fnv1a64-canonical-richtext-v1",
                    "structural_fields": ["id", "parent_id", "child_ids"],
                    "child_order_raw_basis": "Compare SDK children array order with raw siblings sorted by fractional f, using raw record ordinal as the tie-break.",
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
                    "rich_text_algorithm": "fnv1a64-canonical-richtext-v1",
                    "structural_fields": ["id", "parent_id", "child_ids"],
                    "child_order_raw_basis": "Compare SDK children array order with raw siblings sorted by fractional f, using raw record ordinal as the tie-break.",
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

    def test_safe_full_component_has_identity_and_unicode_byte_bound(self):
        component = safe_full_component("界" * 100 + "#[x]^", "source-id")
        self.assertNotRegex(component, r"[#\[\]^]")
        self.assertLessEqual(len(component.encode("utf-8")), 120)
        self.assertEqual(component, safe_full_component("界" * 100 + "#[x]^", "source-id"))


if __name__ == "__main__":
    unittest.main()
