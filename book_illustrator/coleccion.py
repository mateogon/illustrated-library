"""Prepare local EPUB editions for selection, retaining their XHTML anchors."""

import html
import json
import posixpath
import re
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import unquote, urldefrag
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from book_illustrator.corpus import ROOT, corpus

OUT = ROOT / "outputs/coleccion"
LOCAL_SELECTION = OUT / "prospectivo"
PUBLIC_SELECTION = ROOT / "outputs/demo-publica/seleccion"
PUBLIC_CATALOG = ROOT / "demo-publica/catalogo.json"


def edition_path(title, eink, destination=OUT / "epubs"):
    profile = "BOOX" if eink else "Color"
    return destination / profile / (title + (" - ilustrado BOOX.epub" if eink else " - ilustrado.epub"))


HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
EPUB_TYPE = "{http://www.idpf.org/2007/ops}type"
PUBLIC_EXCLUDED_ROLES = set(
    "colophon copyright-page cover dedication endnotes epigraph footnotes frontmatter halftitlepage "
    "loi title titlepage toc z3998:frontispiece".split()
)
PUBLIC_PARATEXT = {
    "don-quijote-es": {*range(1, 8), *range(60, 66)},
    "don-quijote-en": {1, 2, 3, 56, 57},
    "alicia-gutierrez-gili-1927-es": {2},
}


def local(node):
    return node.tag.split("}")[-1]


def read_body(epub, path):
    def entity(match):
        name = match[1].decode()
        value = html.entities.html5.get(name + ";")
        if name in {"lt", "gt", "quot", "apos", "amp"} or value is None:
            return match[0]
        return "".join(f"&#{ord(char)};" for char in value).encode()

    raw = re.sub(rb"&([A-Za-z][A-Za-z0-9]+);", entity, epub.read(path))
    return ET.fromstring(raw).find("./{*}body")


def node_text(node):
    # Inline spans often cover only the first letter of a word.
    pieces = [node.text or ""]
    for child in node:
        pieces.append(" " if local(child) == "br" else node_text(child))
        pieces.append(child.tail or "")
    return "".join(pieces)


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False) as temp:
        temp.write(data)
    Path(temp.name).replace(path)


def encode(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def public_catalog():
    catalog = json.loads(PUBLIC_CATALOG.read_bytes())
    if catalog["version"] != 1:
        raise ValueError("Versión de catálogo público no admitida.")
    editions = catalog["editions"]
    result = {row["id"]: row for row in editions}
    if len(result) != len(editions) or any(not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug) for slug in result):
        raise ValueError("Identificador inválido o repetido en el catálogo público.")
    return result


def public_source(edition):
    source = (ROOT / edition["path"]).resolve()
    shelf = (ROOT / "outputs/demo-publica").resolve()
    if source.suffix != ".epub" or not source.is_relative_to(shelf):
        raise ValueError(f"{edition['id']}: ruta fuera del catálogo local.")
    if source.stat().st_size != edition["bytes"] or sha256(source.read_bytes()).hexdigest() != edition["sha256"]:
        raise ValueError(f"{edition['id']}: cambió el EPUB del catálogo.")
    return source


def public_paragraphs(node, path=()):
    tag = local(node)
    roles = set(node.get(EPUB_TYPE, "").split()) | set(node.get("class", "").split())
    if tag in HEADINGS | {"aside", "figure", "footer", "nav", "script", "style"} or roles & PUBLIC_EXCLUDED_ROLES:
        return
    if tag == "p":
        text = " ".join(node_text(node).split())
        if any(c.isalnum() for c in text):
            yield path, text
        return
    for i, child in enumerate(node):
        yield from public_paragraphs(child, (*path, i))


def build_public(edition, source):
    """Read only narrative XHTML in spine order. Chapter labels stay outside model input."""
    slug, title = edition["id"], edition["title"]
    rows, excluded, seen = [], [], set()
    with ZipFile(source) as epub:
        opf = ET.fromstring(epub.read("META-INF/container.xml")).find(".//{*}rootfile").get("full-path")
        package = ET.fromstring(epub.read(opf))
        language = package.findtext("./{*}metadata/{*}language").strip().lower()
        if language.split("-")[0] != edition["language"]:
            raise ValueError(f"{slug}: idioma distinto al catálogo.")
        author = package.findtext("./{*}metadata/{*}creator") or ""
        files = {item.get("id"): item for item in package.findall("./{*}manifest/{*}item")}
        for i, ref in enumerate(package.findall("./{*}spine/{*}itemref"), 1):
            item = files[ref.get("idref")]
            href = unquote(urldefrag(item.get("href"))[0])
            path = posixpath.normpath(posixpath.join(posixpath.dirname(opf), href))
            if (item.get("media-type") != "application/xhtml+xml" or ref.get("linear") == "no" or
                    "nav" in item.get("properties", "").split() or path in seen or
                    i in PUBLIC_PARATEXT.get(slug, ())):
                excluded.append(dict(source_xhtml=path, reason="fuera del texto narrativo"))
                continue
            seen.add(path)
            body = read_body(epub, path)
            if body is None:
                raise ValueError(f"{slug}: falta el cuerpo XHTML: {path}")
            heading = next((node for node in body.iter() if local(node) in HEADINGS), None)
            chapter = " ".join(node_text(heading).split()) if heading is not None else f"Sección {i}"
            paragraphs = list(public_paragraphs(body))
            if not paragraphs or chapter.casefold() in {"edición", "edition", "créditos de la edición"}:
                excluded.append(dict(source_xhtml=path, reason="fuera del texto narrativo"))
                continue
            for locator, prose in paragraphs:
                rows.append(dict(text=prose, chapter=chapter, source_xhtml=path,
                                 xhtml_path=list(locator), xhtml_tag="p", text_start=0,
                                 text_end=len(prose), xhtml_text_length=len(prose)))
    width = max(4, len(str(len(rows))))
    for number, row in enumerate(rows, 1):
        row["id"] = f"P{number:0{width}}"
    return corpus(title, source, edition["sha256"], rows, [(slug, 0, len(rows))], exclusions=excluded,
                  language=language, author=author)


def prepare_public(slug, *, destination=PUBLIC_SELECTION):
    edition = public_catalog()[slug]
    source = public_source(edition)
    result = build_public(edition, source)
    target = destination / slug / "corpus.json"
    data = encode(result)
    if not target.exists() or target.read_bytes() != data:
        atomic_write(target, data)
    return dict(slug=slug, title=edition["title"], author=result["author"], language=result["language"],
                source=str(source), source_sha256=edition["sha256"], corpus=str(target),
                corpus_sha256=sha256(data).hexdigest(), page_count=result["page_count"],
                narrative_words=result["narrative_words"], units=result["units"])
