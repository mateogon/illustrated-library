import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import patch

from book_illustrator.seleccion import DRAFT_RULES, DRAFT_SCHEMA, explicit_anchor, lineup, phase_call


class SharedDraftTest(unittest.TestCase):
    def test_two_variants_request_one_draft(self):
        started, finish = Event(), Event()

        def respond(*args, **kwargs):
            started.set()
            self.assertTrue(finish.wait(timeout=5))
            return {"data": {"example": True}}

        def draft(root, key, passage="mismo corte"):
            return phase_call(root, key, "draft", passage, schema=DRAFT_SCHEMA, rules=DRAFT_RULES)

        with tempfile.TemporaryDirectory() as tmp, patch("book_illustrator.seleccion.api.structured", side_effect=respond) as api:
            book = Path(tmp) / "novela"
            content = book / "contenido"
            for root in (book, content):
                (root / "llamadas").mkdir(parents=True)
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(draft, content, "test")
                self.assertTrue(started.wait(timeout=5))
                second = pool.submit(draft, book, "test")
                finish.set()
                self.assertEqual(first.result(), second.result())
            self.assertEqual(api.call_count, 1)
            self.assertEqual(draft(book, None), first.result())
            self.assertEqual(draft(content, None), first.result())
            self.assertEqual(len(list(book.rglob("llamadas/*.json"))), 1)
            with self.assertRaises(RuntimeError):
                draft(book, None, "corte distinto")


class CollectionSelectionTest(unittest.TestCase):
    def test_annotated_anchors_require_one_explicit_paragraph(self):
        self.assertEqual(explicit_anchor('P0004: «una cita»'), 'P0004')
        self.assertEqual(explicit_anchor('el mismo (P0004), [P0004]'), 'P0004')
        for invalid in ('P0004–P0005', 'fin del párrafo', 'IDP0004', 'P004', 'P0004x'):
            self.assertEqual(explicit_anchor(invalid), invalid)

    def test_collection_interval_changes_the_count_without_lowering_minimum(self):
        cards = [{"id": str(page), "page": page, "value": 1} for page in range(1, 25)]
        regular, _ = lineup(cards, {}, 24)
        frequent, cadence = lineup(cards, {}, 24, 4)
        self.assertEqual((len(regular), len(frequent)), (4, 6))
        self.assertTrue(all(4 <= gap["pages"] <= 8 for gap in cadence["intervals"] if gap["left"] and gap["right"]))
