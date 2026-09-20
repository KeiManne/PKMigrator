import hashlib
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path

from pilot_support import (
    SupportError,
    build_search_records,
    download_assets,
    propagate_privacy,
    validate_local_assets,
)


def occurrence(file, anchor, kind, *, portal_id=None, visible=True, path=None):
    return {
        "file": file,
        "anchor": anchor,
        "kind": kind,
        "portal_id": portal_id,
        "portal_context": [f"portal:{portal_id}"] if portal_id else None,
        "path": path or [],
        "visible": visible,
        "content_sha256": "a" * 64,
    }


def source_entry(rem_id, text, occurrences, canonical=None, document_id="doc-a"):
    return {
        "rem_id": rem_id,
        "canonical": canonical,
        "canonical_origin": {
            "document_id": document_id,
            "document_title": document_id.title(),
            "ownership_path": [document_id, rem_id],
        },
        "occurrences": occurrences,
        "plain_original_text": text,
        "timestamps": {"createdAt": 10, "m": 20, "u": 30},
        "original_parent": document_id,
    }


def manifest(source_map=None, assets=None):
    return {
        "schema_version": 1,
        "status": "complete_for_requested_pilot",
        "files": [
            {"root_id": "doc-a", "path": "notes/Document A.md"},
            {"root_id": "doc-b", "path": "notes/Document B.md"},
            {"root_id": "doc-c", "path": "notes/Document C.md"},
        ],
        "source_map": source_map or {},
        "assets": assets or {},
    }


def asset_path(url, suffix=".png"):
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    return f"Attachments/RemNote/{digest}{suffix}"


class FakeResponse:
    def __init__(self, data, content_type="image/png", url="https://media.example.test/a.png"):
        self.data = data
        self.offset = 0
        self.headers = {
            "Content-Type": content_type,
            "Content-Length": str(len(data)),
        }
        self.url = url
        self.closed = False

    def read(self, size=-1):
        if size < 0:
            size = len(self.data) - self.offset
        result = self.data[self.offset:self.offset + size]
        self.offset += len(result)
        return result

    def geturl(self):
        return self.url

    def close(self):
        self.closed = True


class CanonicalRecordTests(unittest.TestCase):
    def test_one_record_per_source_keeps_all_appearances_and_visible_scope(self):
        repeated = source_entry(
            "source-public",
            "Unique canonical evidence phrase",
            [
                occurrence(
                    "notes/Document A.md", "canonical", "canonical", path=["doc-a", "source-public"]
                ),
                occurrence(
                    "notes/Document B.md", "portal-one", "portal-copy",
                    portal_id="portal-one", path=["doc-b", "portal-one", "source-public"],
                ),
                occurrence(
                    "notes/Document C.md", "hidden", "portal-copy",
                    portal_id="portal-two", visible=False,
                    path=["doc-c", "portal-two", "source-public"],
                ),
            ],
            canonical={"file": "notes/Document A.md", "anchor": "canonical"},
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            vault = root / "reading-vault"
            vault.mkdir()
            projection = root / "projection"
            registry = build_search_records(
                manifest({"source-public": repeated}),
                projection,
                reading_vault_root=vault,
            )
            self.assertEqual(registry["counts"], {"sources": 1, "records": 1})
            self.assertEqual(len(list((projection / "records").glob("*.md"))), 1)
            record = registry["records"][0]
            self.assertEqual(record["appearance_count"], 3)
            self.assertEqual(record["source_citation"], "notes/Document A.md#^canonical")
            self.assertEqual(record["visible_in_documents"], ["doc-a", "doc-b"])
            body = (projection / record["record_path"]).read_text(encoding="utf-8")
            self.assertEqual(body.count("Unique canonical evidence phrase"), 1)
            self.assertIn('"portal_id": "portal-one"', body)
            self.assertIn('"visible": false', body)

    def test_duplicate_occurrence_metadata_does_not_duplicate_document_membership(self):
        duplicate = occurrence(
            "notes/Document B.md", "copy", "portal-copy",
            portal_id="portal-one", path=["doc-b", "portal-one", "source-public"],
        )
        entry = source_entry("source-public", "Evidence", [duplicate, dict(duplicate)], canonical=None)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            vault = root / "vault"
            vault.mkdir()
            registry = build_search_records(
                manifest({"source-public": entry}), root / "projection", reading_vault_root=vault
            )
            self.assertEqual(registry["records"][0]["visible_in_documents"], ["doc-b"])
            records_json = json.loads((root / "projection" / "records.json").read_text())
            self.assertEqual(records_json["dedup_key"], "rem_id")

    def test_projection_is_refused_inside_reading_vault(self):
        entry = source_entry("source-public", "Evidence", [], canonical=None)
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary) / "vault"
            vault.mkdir()
            with self.assertRaisesRegex(SupportError, "outside the reading vault"):
                build_search_records(
                    manifest({"source-public": entry}),
                    vault / "search-projection",
                    reading_vault_root=vault,
                )

    def test_incomplete_converter_manifest_is_not_indexed(self):
        entry = source_entry("source-public", "Evidence", [], canonical=None)
        incomplete = manifest({"source-public": entry})
        incomplete["status"] = "incomplete"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            vault = root / "vault"
            vault.mkdir()
            with self.assertRaisesRegex(SupportError, "refusing to index"):
                build_search_records(
                    incomplete, root / "projection", reading_vault_root=vault
                )


class PrivacyDependencyTests(unittest.TestCase):
    def test_private_source_blocks_every_copy_and_taints_derived_chain(self):
        private_entry = source_entry(
            "source-private",
            "Private fixture",
            [
                occurrence("notes/Document A.md", "one", "canonical", path=["doc-a", "source-private"]),
                occurrence(
                    "notes/Document B.md", "two", "portal-copy",
                    portal_id="portal-one", path=["doc-b", "portal-one", "source-private"],
                ),
            ],
            canonical={"file": "notes/Document A.md", "anchor": "one"},
        )
        public_entry = source_entry("source-public", "Public fixture", [], canonical=None)
        result = propagate_privacy(
            manifest({"source-private": private_entry, "source-public": public_entry}),
            ["source-private"],
            [
                {"id": "derived-direct", "source_ids": ["source-private"]},
                {"id": "derived-chain", "derived_ids": ["derived-direct"]},
                {"id": "derived-public", "source_ids": ["source-public"]},
            ],
        )
        self.assertEqual(
            len(result["source_decisions"]["source-private"]["blocked_occurrences"]), 2
        )
        self.assertEqual(
            result["affected_files"], ["notes/Document A.md", "notes/Document B.md"]
        )
        self.assertTrue(result["derived_decisions"]["derived-direct"]["private"])
        self.assertTrue(result["derived_decisions"]["derived-chain"]["private"])
        self.assertFalse(result["derived_decisions"]["derived-public"]["private"])
        self.assertEqual(result["status"], "advisory_only")
        self.assertIn("does not enforce", result["limitation"])


class MediaTests(unittest.TestCase):
    png = b"\x89PNG\r\n\x1a\n" + b"synthetic-payload"

    def test_valid_image_retries_once_then_writes_deterministic_path(self):
        url = "https://media.example.test/a.png"
        relative = asset_path(url)
        calls = []

        def opener(request, timeout):
            calls.append((request.full_url, timeout))
            if len(calls) == 1:
                raise urllib.error.URLError("synthetic transient failure")
            return FakeResponse(self.png, url=url)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = download_assets(
                manifest(assets={url: {"relative_path": relative, "occurrences": [{"rem_id": "source-public"}]}}),
                root,
                max_assets=1,
                max_bytes=1024,
                timeout_seconds=0.5,
                retries=1,
                retry_delay_seconds=0,
                opener=opener,
            )
            self.assertEqual(report["status"], "complete")
            self.assertEqual(report["downloaded"][0]["attempts"], 2)
            self.assertEqual((root / relative).read_bytes(), self.png)
            self.assertEqual(report["downloaded"][0]["format"], "png")
            self.assertTrue(report["downloaded"][0]["extension_matches_signature"])

    def test_content_type_and_signature_both_must_validate(self):
        url = "https://media.example.test/not-image.png"
        relative = asset_path(url)

        def opener(request, timeout):
            return FakeResponse(b"plain text", content_type="image/png", url=url)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = download_assets(
                manifest(assets={url: {"relative_path": relative, "occurrences": []}}),
                root,
                opener=opener,
            )
            self.assertEqual(report["status"], "incomplete")
            self.assertEqual(report["failures"][0]["code"], "validation_failed")
            self.assertFalse((root / relative).exists())

    def test_escaping_or_non_deterministic_destination_is_reported(self):
        url = "https://media.example.test/a.png"
        report = download_assets(
            manifest(assets={url: {"relative_path": "../escape.png", "occurrences": []}}),
            Path(tempfile.gettempdir()) / "unused-public-fixture",
            opener=lambda request, timeout: self.fail("unsafe asset must not be fetched"),
        )
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["failures"][0]["code"], "unsafe_asset")

    def test_missing_declared_asset_is_an_explicit_validation_failure(self):
        url = "https://media.example.test/missing.png"
        relative = asset_path(url)
        with tempfile.TemporaryDirectory() as temporary:
            result = validate_local_assets(
                manifest(assets={url: {"relative_path": relative, "occurrences": []}}),
                Path(temporary),
            )
            self.assertEqual(result["status"], "incomplete")
            self.assertEqual(result["counts"], {"declared": 1, "valid": 0, "failed": 1})
            self.assertIn("missing", result["failures"][0]["message"])


if __name__ == "__main__":
    unittest.main()
