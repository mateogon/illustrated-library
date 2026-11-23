"""Rebuild pinned Hispanic works and historical translations in the catalog."""

import argparse
import hashlib
import html
import json
import posixpath
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZIP_STORED, ZipFile, ZipInfo

from bs4 import BeautifulSoup, Tag


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
XHTML = "http://www.w3.org/1999/xhtml"
HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
CONTAINER = """<?xml version="1.0" encoding="UTF-8"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles><rootfile full-path="EPUB/package.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""
TEXT_ELEMENTS = {
    "blockquote", "br", "cite", "div", "em", "h1", "h2", "h3", "h4", "h5", "h6",
    "hr", "i", "li", "ol", "p", "pre", "q", "small", "span", "strong", "sub",
    "sup", "table", "tbody", "td", "th", "thead", "tr", "ul",
}


def read_spine(path):
    with ZipFile(path) as source:
        wrapper = ET.fromstring(source.read("META-INF/container.xml"))
        opf_path = wrapper.find(".//{*}rootfile").attrib["full-path"]
        opf = ET.fromstring(source.read(opf_path))
        items = {node.attrib["id"]: node.attrib["href"] for node in opf.findall("./{*}manifest/{*}item")}
        return [
            source.read(posixpath.join(posixpath.dirname(opf_path), items[ref.attrib["idref"]]))
            for ref in opf.findall("./{*}spine/{*}itemref")
        ]


def clean(node):
    for child in node.find_all(recursive=False):
        clean(child)
        if child.name == "div" and node.name in HEADINGS:
            # Wikisource usa un bloque para centrar el texto dentro del encabezado.
            child.unwrap()
        elif child.name in TEXT_ELEMENTS:
            child.attrs = {}
        else:
            child.unwrap()


def extract(source_path, entry):
    recipe = entry["recipe"]
    documents = read_spine(source_path)
    sections = []
    section_content = []
    section_title = None
    for index in range(recipe["first"], recipe["last"] + 1):
        document = BeautifulSoup(documents[index], "html.parser")
        for annotation in document.select(recipe["omit"]):
            annotation.decompose()
        for root in document.select(recipe["selector"]):
            clean(root)
            if recipe.get("split_before"):
                for child in root.children:
                    if isinstance(child, Tag) and child.name in recipe["split_before"]:
                        if section_title:
                            sections.append((section_title, "".join(section_content)))
                        section_title = " ".join(child.get_text().split())
                        section_content = []
                    section_content.append(str(child))
                continue
            text = root.get_text(" ", strip=True)
            if not text:
                raise ValueError(f"Sección vacía de {entry['id']}")
            heading = root.find(HEADINGS)
            title = recipe.get("titles_by_spine", {}).get(str(index))
            if not title and heading:
                title = " ".join(heading.get_text().split())
            if not title:
                raise ValueError(f"Sección sin título de {entry['id']}")
            sections.append((title, root.decode_contents(formatter="minimal")))
    if section_title:
        sections.append((section_title, "".join(section_content)))
    if recipe.get("title_order"):
        by_title = dict(sections)
        sections = [(title, by_title[title]) for title in recipe["title_order"]]
    if len(sections) != recipe["sections"]:
        raise ValueError(f"{entry['id']}: encontré {len(sections)} secciones, esperaba {recipe['sections']}")
    return sections


def xhtml(title, content, language):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="{XHTML}" xmlns:epub="http://www.idpf.org/2007/ops" lang="{language}" xml:lang="{language}">
  <head><title>{html.escape(title)}</title></head>
  <body>{content}</body>
</html>"""


def build(entry, source):
    book_id = entry["id"]
    language = entry["language"]
    title = html.escape(entry["title"])
    author = html.escape(entry["author"])
    entries = []
    references = []
    nav_links = []
    pages = {}
    sections = extract(source, entry)
    section_remark = entry.get("reader_credit")
    if section_remark:
        sections.append((section_remark["title"], section_remark["xhtml"]))
    for index, (heading, content) in enumerate(sections, 1):
        name = f"section-{index:03d}"
        href = f"{name}.xhtml"
        entries.append(f'<item id="{name}" href="{href}" media-type="application/xhtml+xml"/>')
        references.append(f'<itemref idref="{name}"/>')
        nav_links.append(f'<li><a href="{href}">{html.escape(heading)}</a></li>')
        pages[f"EPUB/{href}"] = xhtml(heading, content, language)
    nav_label = "Contents" if language == "en" else "Índice"
    nav = f'<nav epub:type="toc" id="toc"><h1>{nav_label}</h1><ol>' + "".join(nav_links) + "</ol></nav>"
    translation = ""
    if entry.get("translator"):
        translator = html.escape(entry["translator"])
        translation = f'<dc:contributor id="translator">{translator}</dc:contributor><meta refines="#translator" property="role" scheme="marc:relators">trl</meta>'
    opf = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="book-id" version="3.0" xml:lang="{language}">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="book-id">urn:book-illustrator:demo:{book_id}</dc:identifier>
    <dc:title>{title}</dc:title>
    <dc:creator>{author}</dc:creator>
    <dc:language>{language}</dc:language>
    {translation}
    <meta property="dcterms:modified">2026-11-21T00:00:00Z</meta>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    {"".join(entries)}
  </manifest>
  <spine>{"".join(references)}</spine>
</package>"""
    pages.update({
        "META-INF/container.xml": CONTAINER,
        "EPUB/package.opf": opf,
        "EPUB/nav.xhtml": xhtml(entry["title"], nav, language),
    })
    for document in pages.values():
        ET.fromstring(document)
    with tempfile.TemporaryFile() as buffer:
        with ZipFile(buffer, "w") as epub:
            epub.writestr(ZipInfo("mimetype"), b"application/epub+zip", compress_type=ZIP_STORED)
            for name, content in pages.items():
                # Sin compresión para que zlib no cambie el hash entre plataformas.
                epub.writestr(ZipInfo(name), content.encode("utf-8"), compress_type=ZIP_STORED)
        buffer.seek(0)
        return buffer.read()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ids", nargs="*", help="Only rebuild the specified catalog editions.")
    parser.add_argument("--source-dir", type=Path, help="Read existing source files from this directory.")
    args = parser.parse_args()
    manifest = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as temp:
        for entry in manifest["editions"]:
            if args.ids and entry["id"] not in args.ids:
                continue
            recipe = entry["recipe"]
            source = (args.source_dir or Path(temp)) / recipe["filename"]
            if not args.source_dir:
                subprocess.run([
                    "curl", "--fail", "--silent", "--show-error", "--location",
                    "--retry", "2", "--max-time", "240", "--output", str(source), recipe["url"],
                ], check=True)
            source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            if source_hash != recipe["sha256"] and not recipe.get("generated_export"):
                raise SystemExit(f"{entry['id']}: cambió la fuente ({source_hash})")
            data = build(entry, source)
            digest = hashlib.sha256(data).hexdigest()
            if digest != entry["sha256"] or len(data) != entry["bytes"]:
                raise SystemExit(f"{entry['id']}: el resultado difiere del manifiesto ({len(data)}, {digest})")
            destination = ROOT / entry["path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            print(f"{destination.relative_to(ROOT)}: {len(data)} bytes, SHA-256 {digest}")


if __name__ == "__main__":
    main()
