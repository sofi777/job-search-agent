"""src/rag.py - pure text-splitting functions only (no embedding model load, keeps this fast).
count_tokens/embed/chunk_text/retrieve all need sentence-transformers and are out of scope here."""
import unittest

from src import rag


class SplitSentencesTests(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(rag.split_sentences("One. Two! Three?"), ["One.", "Two!", "Three?"])

    def test_empty(self):
        self.assertEqual(rag.split_sentences("   "), [])


class SplitParagraphsTests(unittest.TestCase):
    def test_splits_on_blank_lines(self):
        text = "Para one.\n\nPara two.\n\n\nPara three."
        self.assertEqual(rag.split_paragraphs(text), ["Para one.", "Para two.", "Para three."])


class SplitByHeadingsTests(unittest.TestCase):
    def test_none_without_headings(self):
        self.assertIsNone(rag.split_by_headings("just plain text, no headings"))

    def test_splits_on_markdown_headings(self):
        text = "## Story One\nContent A\n\n## Story Two\nContent B"
        blocks = rag.split_by_headings(text)
        self.assertEqual(len(blocks), 2)
        self.assertTrue(blocks[0].startswith("## Story One"))
        self.assertIn("Content A", blocks[0])
        self.assertTrue(blocks[1].startswith("## Story Two"))


class SplitResumeSectionsTests(unittest.TestCase):
    def test_none_without_keywords(self):
        self.assertIsNone(rag.split_resume_sections("no section headers here at all"))

    def test_splits_on_keywords_and_keeps_preamble(self):
        text = "Jamie Doe, PM\n\nEXPERIENCE\nDid stuff.\n\nEDUCATION\nBA."
        sections = rag.split_resume_sections(text)
        labels = [label for label, _ in sections]
        self.assertIn(None, labels)  # preamble kept
        self.assertIn("EXPERIENCE", labels)
        self.assertIn("EDUCATION", labels)


class SplitRolesTests(unittest.TestCase):
    def test_single_role_returned_unchanged(self):
        block = "EXPERIENCE\nAcme Inc, PM (Jan 2020 - Present)\nDid things."
        self.assertEqual(rag.split_roles(block), [block])

    def test_splits_multiple_roles_on_date_ranges(self):
        block = (
            "EXPERIENCE\n"
            "Acme Inc, PM (Jan 2020 - Dec 2021). Shipped X.\n"
            "Globex Inc, Sr PM (Jan 2022 - Present). Shipped Y."
        )
        roles = rag.split_roles(block)
        self.assertEqual(len(roles), 2)
        self.assertIn("Acme", roles[0])
        self.assertIn("Globex", roles[1])


class ChunkResumeTests(unittest.TestCase):
    def test_prefers_headings_and_splits_experience_roles(self):
        text = (
            "## Experience\n"
            "Acme Inc, PM (Jan 2020 - Dec 2021). Did X.\n"
            "Globex Inc, PM (Jan 2022 - Present). Did Y.\n\n"
            "## Education\nBA, State University"
        )
        chunks = rag.chunk_resume(text)
        self.assertEqual(len(chunks), 3)  # 2 roles + 1 education section
        self.assertTrue(any("Acme" in c for c in chunks))
        self.assertTrue(any("Globex" in c for c in chunks))
        self.assertTrue(any("Education" in c for c in chunks))

    def test_falls_back_to_keyword_sections_without_headings(self):
        text = "EXPERIENCE\nAcme Inc, PM (Jan 2020 - Present). Did X.\n\nEDUCATION\nBA."
        chunks = rag.chunk_resume(text)
        self.assertTrue(any("Acme" in c for c in chunks))
        self.assertTrue(any("BA" in c for c in chunks))

    def test_whole_document_fallback_when_no_structure(self):
        text = "just a plain paragraph resume with no structure"
        self.assertEqual(rag.chunk_resume(text), [text])

    def test_empty_text_returns_empty_list(self):
        self.assertEqual(rag.chunk_resume("   "), [])


if __name__ == "__main__":
    unittest.main()
