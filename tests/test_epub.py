import unittest
from xml.etree import ElementTree as ET

from book_illustrator.epub import XHTML, add_to_chapter, make_notes


class EPUBPlacementTest(unittest.TestCase):
    def test_epub3_notes_keep_both_language_attributes_and_encode_back_path(self):
        entry = (
            {"id": "P0001", "source_xhtml": "EPUB/Text/chapter 1.xhtml"},
            {"title": "Title", "description": "Description"}, "image.jpg",
        )
        source = make_notes([entry], "EPUB/notes.xhtml", "EPUB/style.css", "en-GB", version="3.0")
        root = ET.fromstring(source)
        self.assertEqual(root.attrib["{http://www.w3.org/XML/1998/namespace}lang"], "en-GB")
        self.assertEqual(root.attrib["lang"], "en-GB")
        self.assertEqual(root.findtext(".//{*}title"), "Illustration notes")
        link = root.find(".//{*}a")
        self.assertEqual(link.get("href"), "Text/chapter%201.xhtml#bi-reading-P0001")
        self.assertEqual(link.text, "Back to the passage")

    def test_illustration_follows_its_exact_nested_paragraph_and_links_back(self):
        source = f"""<html xmlns="{XHTML}">
  <head><title>Prueba</title></head>
  <body>
    <p>Primero</p>
    <div>
      <p>Segundo <em>con énfasis</em> y espacio&nbsp;duro.</p>
      <p>Tercero</p>
    </div>
  </body>
</html>""".encode()
        chapter_path = "OEBPS/Text/chapter.xhtml"
        notes_path = "OEBPS/Text/notes.xhtml"
        css_path = "OEBPS/Styles/illustrations.css"
        entry = (
            {"id": "P0002", "last_xhtml_p": 2, "source_xhtml": chapter_path},
            {"title": "Título", "description": "Descripción", "content_label": "Adaptado"},
            "OEBPS/Images/P0002.jpg",
        )
        chapter = ET.fromstring(add_to_chapter(source, chapter_path, [entry], css_path, notes_path))
        parent = chapter.find("./{*}body/{*}div")
        block = parent[1]
        self.assertEqual(block.attrib["id"], "bi-reading-P0002")
        self.assertEqual(block.find(".//{*}img").attrib, {
            "src": "../Images/P0002.jpg", "alt": "Título — Descripción · Adaptado",
        })
        self.assertEqual([link.attrib["href"] for link in block.findall(".//{*}a")], ["notes.xhtml#bi-note-P0002"])
        self.assertEqual("".join(block.itertext()), "")
        head = chapter.find("{*}head")
        self.assertEqual(head.find("{*}link").attrib["href"], "../Styles/illustrations.css")
        head.remove(head.find("{*}link"))
        parent.remove(block)
        self.assertEqual(ET.tostring(chapter), ET.tostring(ET.fromstring(source.replace(b"&nbsp;", b"&#160;"))))

        notes = ET.fromstring(make_notes([entry], notes_path, css_path, "es"))
        note = notes.find("./{*}body/{*}div")
        self.assertEqual(note.attrib["id"], "bi-note-P0002")
        self.assertEqual(note.find("{*}h2").text, "Título")
        self.assertEqual([p.text for p in note.findall("{*}p")][:2], ["Descripción", "Adaptado"])
        self.assertEqual(note.find(".//{*}a").attrib["href"], "chapter.xhtml#bi-reading-P0002")
