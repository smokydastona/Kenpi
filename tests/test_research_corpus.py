import unittest

from kenpi.tools import research_corpus as rc


class ResearchCorpusTests(unittest.TestCase):
    def test_corpus_valid(self):
        projects = rc.load_corpus()
        self.assertGreater(len(projects), 30)

    def test_policy(self):
        self.assertTrue(rc.reuse_policy("GPL-3.0").startswith("reference-only"))
        self.assertTrue(rc.reuse_policy("LGPL-2.1").startswith("reference-only"))
        self.assertTrue(rc.reuse_policy(None).startswith("reference-only"))
        self.assertTrue(rc.reuse_policy("MIT").startswith("may-depend"))

    def test_dashcam_entries_never_claim_lite_parity(self):
        for p in rc.load_corpus():
            if p["category"] == "dashcam" and "Lite" not in p["slug"]:
                self.assertNotIn(
                    "verified", p["kenpi_use"].lower().replace("unverified", "")
                )

    def test_render_marks_unverified(self):
        text = rc.render_markdown(rc.load_corpus(), rc.load_metadata())
        self.assertIn("No project listed here is", text)


if __name__ == "__main__":
    unittest.main()
