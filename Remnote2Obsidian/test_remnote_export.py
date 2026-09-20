import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from remnote_export import Converter, load_export, main, safe_stem, stable_anchor


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


if __name__ == "__main__":
    unittest.main()
