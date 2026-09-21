import unittest

from reference_metadata import extract_reference_metadata


ROOT = "reviewed-root"
LINK = "link-definition"


def record(rem_id, key, parent=None, **extra):
    value = {"_id": rem_id, "key": [key], "parent": parent}
    value.update(extra)
    return value


def typed_link(rem_id="link", *, url="https://example.test/page", label="Example", **extra):
    value = record(
        rem_id,
        label,
        ROOT,
        tp={LINK: {"t": False}},
        crt={"b": {"u": {"s": url}, "t": {"s": label}}},
        ps={"b_u": {"v": {"s": url}}, "b_t": {"v": {"s": label}}},
    )
    value.update(extra)
    return value


class ReferenceMetadataTests(unittest.TestCase):
    def extract(self, *items):
        return extract_reference_metadata([record(ROOT, "Website"), *items], [ROOT], LINK)

    def test_resolves_typed_link_with_matching_exact_metadata_fields(self):
        index = self.extract(typed_link())
        target = index.resolve_reference("link")
        self.assertEqual(target.url, "https://example.test/page")
        self.assertEqual(target.label, "Example")
        self.assertEqual(target.provenance["reviewed_root_id"], ROOT)
        self.assertEqual(index.unresolved, {})

    def test_uses_plain_key_when_structured_title_is_absent(self):
        item = typed_link()
        del item["crt"]["b"]["t"]
        del item["ps"]["b_t"]
        index = self.extract(item)
        self.assertEqual(index.resolve_reference("link").label, "Example")
        self.assertEqual(index.resolve_reference("link").provenance["label_field"], "key")

    def test_rejects_ambiguous_url_fields(self):
        item = typed_link()
        item["ps"]["b_u"]["v"]["s"] = "https://other.test/"
        index = self.extract(item)
        self.assertIsNone(index.resolve_reference("link"))
        self.assertEqual(index.unresolved["link"], "ambiguous-url-metadata")

    def test_rejects_missing_and_unsafe_urls(self):
        missing = typed_link("missing")
        del missing["crt"]["b"]["u"]
        del missing["ps"]["b_u"]
        unsafe = typed_link("unsafe", url="javascript:alert(1)")
        credentials = typed_link("credentials", url="https://user:secret@example.test/")
        ambiguous_parser = typed_link("ambiguous-parser", url="https://example.test\\@evil.test/")
        index = self.extract(missing, unsafe, credentials, ambiguous_parser)
        self.assertEqual(index.unresolved["missing"], "missing-url-metadata")
        self.assertEqual(index.unresolved["unsafe"], "unsafe-url-metadata")
        self.assertEqual(index.unresolved["credentials"], "unsafe-url-metadata")
        self.assertEqual(index.unresolved["ambiguous-parser"], "unsafe-url-metadata")

    def test_hostname_and_authored_descendant_are_not_inferred_as_links(self):
        bucket = record("bucket", "example.test", ROOT)
        authored = record("authored", "My own note", "bucket", value=["Keep this prose"])
        index = self.extract(bucket, authored)
        self.assertEqual(index.unresolved["bucket"], "not-typed-link-metadata")
        self.assertEqual(index.unresolved["authored"], "not-typed-link-metadata")
        self.assertEqual(index.entries, {})

    def test_unreviewed_typed_link_is_not_admitted(self):
        outside = typed_link("outside")
        outside["parent"] = None
        index = self.extract(outside)
        self.assertIsNone(index.resolve_reference("outside"))
        self.assertNotIn("outside", index.unresolved)

    def test_shared_references_resolve_to_the_same_immutable_metadata(self):
        index = self.extract(typed_link())
        first = index.resolve_reference("link")
        second = index.resolve_reference("link")
        self.assertIs(first, second)
        with self.assertRaises(TypeError):
            first.provenance["reviewed_root_id"] = "changed"

    def test_missing_reviewed_root_is_explicit(self):
        index = extract_reference_metadata([], [ROOT], LINK)
        self.assertEqual(index.unresolved[ROOT], "missing-reviewed-root")


if __name__ == "__main__":
    unittest.main()
