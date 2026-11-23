import unittest

from book_illustrator.coleccion_epub import (
    XHTML, chapter_markup, document_projection, read_xhtml, sha256,
)


class CollectionEpubTest(unittest.TestCase):
    def test_carriage_return_entity_survives_round_trip(self):
        source = f'<html xmlns="{XHTML}"><head/><body><p>Uno.&#13; Dos.</p></body></html>'.encode()
        row = dict(id="P0001", xhtml_path=[0], text="Uno.", xhtml_tag="p",
                   text_start=0, text_end=4, xhtml_text_length=9)
        entry = (row, {"title": "Uno", "description": "Escena"}, "a.jpg")
        out, (original, expected) = chapter_markup(source, "cap.xhtml", [entry], "s.css", "n.xhtml")
        [body] = read_xhtml(out).getElementsByTagNameNS(XHTML, "body")
        [marker] = body.getElementsByTagNameNS(XHTML, "div")
        position = {marker: None}
        self.assertEqual(sha256(document_projection(body, position).encode()), original)
        self.assertEqual(position[marker], expected["bi-reading-P0001"])
        self.assertIn(b"&#13;", out)

    def test_empty_anchors_remain_closed_for_html_readers(self):
        source = f'<html xmlns="{XHTML}"><head/><body><p><a></a>Uno.</p></body></html>'.encode()
        row = dict(id="P0001", xhtml_path=[0], text="Uno.", xhtml_tag="p",
                   text_start=0, text_end=4, xhtml_text_length=4)
        out, _ = chapter_markup(source, "cap.html", [(row, {"title": "Uno", "description": "Escena"}, "a.jpg")], "s.css", "n.xhtml")
        self.assertIn(b"<a></a>Uno.", out)

    def test_two_cuts_inside_emphasis_keep_text_inline_markup_and_existing_ids(self):
        source = f'<html xmlns="{XHTML}"><head/><body><p id="unique"> Uno  <em id="accent">dos   tres cuatro</em> cinco.</p></body></html>'.encode()
        clean = "Uno dos tres cuatro cinco."
        entries = []
        for cut, (start, end) in enumerate(((0, 7), (8, 12)), 1):
            row = dict(id=f"P000{cut}", xhtml_path=[0], text=clean[start:end], xhtml_tag="p",
                       text_start=start, text_end=end, xhtml_text_length=len(clean))
            entries.append((row, {"title": "Muestra", "description": "Escena."}, f"Images/{cut}.jpg"))
        out, (original, expected) = chapter_markup(source, "cap.xhtml", entries, "style.css", "notas.xhtml")
        doc = read_xhtml(out)
        [body] = doc.getElementsByTagNameNS(XHTML, "body")
        blocks = {n:n.getAttribute("id") for n in body.getElementsByTagNameNS(XHTML, "div")}
        offsets = dict.fromkeys(blocks)
        self.assertEqual(sha256(document_projection(body, offsets).encode()), original)
        self.assertEqual({v:offsets[n] for n,v in blocks.items()}, expected)
        self.assertEqual([n.firstChild.data for n in body.getElementsByTagNameNS(XHTML, "em")],
                         ["dos", "   tres", " cuatro"])
        self.assertEqual(len([n for n in body.getElementsByTagName("*") if n.getAttribute("id") == "accent"]), 1)
        self.assertEqual(len([n for n in body.getElementsByTagName("*") if n.getAttribute("id") == "unique"]), 1)
        self.assertEqual(len([n for n in body.getElementsByTagNameNS(XHTML, "p")
                              if "bi-continuation" in n.getAttribute("class").split()]), 2)
