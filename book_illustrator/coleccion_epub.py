"""Export the selected illustrations into the twelve reviewed Spanish EPUB editions."""

from collections import defaultdict
from copy import copy
import html.entities
from io import BytesIO
from pathlib import Path, PurePosixPath
import re
from tempfile import TemporaryDirectory
from xml.dom import Node, minidom
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

from PIL import Image

from book_illustrator.coleccion import edition_path
from book_illustrator.epub import (
    DC, NCX, OPF, PREFIX, STYLES, XHTML, append, eink_jpeg, make_notes, navigation_list,
    notes_labels, package_navigation, read_package, relative, resolve, update_package, xml_bytes,
)
from book_illustrator.imagenes import IMAGE_DIMENSIONS, png_sha256, sha256

MARKER = "bi-reading-"
COLLECTION_STYLES = STYLES + b"""
p.bi-continuation {
  text-indent: 0 !important;
  margin-top: 0 !important;
  page-break-before: auto !important;
  break-before: auto !important;
}
"""


def read_xhtml(raw):
    # Some EPUB 2 chapters rely on the XHTML external DTD for named entities.
    def entity(match):
        name = match[1].decode()
        value = html.entities.html5.get(name + ";")
        if value is None or name in {"lt", "gt", "quot", "apos", "amp"}:
            return match[0]
        return "".join(f"&#{ord(char)};" for char in value).encode()

    return minidom.parseString(re.sub(rb"&([A-Za-z][A-Za-z0-9]+);", entity, raw))


def elements(node):
    return [child for child in node.childNodes if child.nodeType == Node.ELEMENT_NODE]


def text_nodes(node):
    """Use the same raw character stream as coleccion.node_text before normalization."""
    if node.nodeType in (Node.TEXT_NODE, Node.CDATA_SECTION_NODE):
        yield node, node.data
    elif node.nodeType == Node.ELEMENT_NODE:
        if node.localName == "br":
            yield node, " "
        else:
            for child in node.childNodes:
                yield from text_nodes(child)


def document_projection(body, offsets):
    """Read the original text and the absolute text offsets of nodes of interest."""
    chunks, length = [], 0

    def visit(node):
        nonlocal length
        if node in offsets:
            offsets[node] = length
        if node.nodeType in (Node.TEXT_NODE, Node.CDATA_SECTION_NODE):
            chunks.append(node.data)
            length += len(node.data)
        elif node.nodeType == Node.ELEMENT_NODE:
            if node.localName == "br":
                chunks.append(" ")
                length += 1
            else:
                for child in node.childNodes:
                    visit(child)

    visit(body)
    return "".join(chunks)


def anchor(body, row):
    """Validate every recorded fragment and locate the last character read at this cut."""
    pieces = []
    for part in row.get("source_parts", [row]):
        node = body
        for step in part["xhtml_path"]:
            children = elements(node)
            if type(step) is not int or not 0 <= step < len(children):
                raise ValueError(f"{row['id']}: ruta XHTML fuera de rango.")
            node = children[step]
        raw = "".join(text for _, text in text_nodes(node))
        clean = " ".join(raw.split())
        if (node.localName != part["xhtml_tag"] or len(clean) != part["xhtml_text_length"]
                or not 0 <= part["text_start"] < part["text_end"] <= len(clean)):
            raise ValueError(f"{row['id']}: el nodo ya no coincide con el corpus.")
        pieces.append(clean[part["text_start"]:part["text_end"]])
    if " ".join(pieces) != row["text"] or node.localName != "p":
        raise ValueError(f"{row['id']}: la cita no coincide con el XHTML.")
    if part["text_end"] == len(clean):
        return node, len(raw), False
    # All normalized boundaries occur at the end of a word in the source paragraph.
    word_count = len(clean[:part["text_end"]].split())
    words = list(re.finditer(r"\S+", raw))
    offset = words[word_count - 1].end()
    if " ".join(raw[:offset].split()) != clean[:part["text_end"]]:
        raise ValueError(f"{row['id']}: no se pudo localizar el final exacto de la cita.")
    return node, offset, True


def split_paragraph(paragraph, offset):
    """Carry inline markup across the break, retaining each original ID exactly once."""
    for leaf, text in text_nodes(paragraph):
        if offset <= len(text):
            break
        offset -= len(text)
    else:
        raise ValueError("La división cae fuera del párrafo.")
    if leaf.nodeType not in (Node.TEXT_NODE, Node.CDATA_SECTION_NODE) or offset == 0:
        raise ValueError("La división debe quedar al final de texto visible.")
    parent = leaf.parentNode
    following = leaf.splitText(offset) if offset < len(leaf.data) else leaf.nextSibling
    while True:
        right = parent.cloneNode(False)
        for key in ("id", "xml:id", "name"):
            if right.hasAttribute(key):
                right.removeAttribute(key)
        while following:
            after = following.nextSibling
            right.appendChild(following)
            following = after
        if right.childNodes:
            if parent is paragraph:
                right.setAttribute("class", (right.getAttribute("class") + " bi-continuation").strip())
            parent.parentNode.insertBefore(right, parent.nextSibling)
        if parent is paragraph:
            return
        following = parent.nextSibling
        parent = parent.parentNode


def chapter_markup(raw, chapter, entries, css, notes):
    doc = read_xhtml(raw)
    [body] = doc.getElementsByTagNameNS(XHTML, "body")
    [head] = doc.getElementsByTagNameNS(XHTML, "head")
    append(head, "link", {"rel": "stylesheet", "type": "text/css", "href": relative(chapter, css)})
    ids = {node.getAttribute("id") for node in doc.getElementsByTagName("*") if node.hasAttribute("id")}
    cuts = [(anchor(body, row), row, image, jpg) for row, image, jpg in entries]
    starts = {node: None for (node, _, _), *_ in cuts}
    original = document_projection(body, starts)
    cuts.sort(key=lambda entry: starts[entry[0][0]] + entry[0][1])
    expected = {}
    # Descending order leaves every earlier offset and its original node intact.
    for (node, offset, internal), row, image, jpg in reversed(cuts):
        cut = row["id"]
        marker = MARKER + cut
        if marker in ids or marker in expected:
            raise ValueError(f"{chapter}: identificador repetido {marker}.")
        position = starts[node] + offset
        if position in expected.values():
            raise ValueError(f"{chapter}: dos cortes en la misma posición ({cut}).")
        expected[marker] = position
        if internal:
            split_paragraph(node, offset)
        prefix = node.prefix + ":" if node.prefix else ""
        block = doc.createElementNS(XHTML, prefix + "div")
        if prefix or node.parentNode.namespaceURI != XHTML:
            block.setAttribute("xmlns" + (":" + node.prefix if node.prefix else ""), XHTML)
        block.setAttribute("id", marker)
        block.setAttribute("class", "bi-illustration")
        node.parentNode.insertBefore(block, node.nextSibling)
        link = append(block, "a", {"class": "bi-image-link", "href": relative(chapter, notes) + "#bi-note-" + cut})
        alt = f"{image['title']} — {image['description']}"
        if image.get("content_label"):
            alt += " · " + image["content_label"]
        append(link, "img", {"src": relative(chapter, jpg), "alt": alt})
    if chapter.lower().endswith((".htm", ".html")):
        # HTML viewers ignore a self-closing <a/> and may wrap subsequent prose in a link.
        void = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
        for element in doc.getElementsByTagNameNS(XHTML, "*"):
            if not element.childNodes and element.localName not in void:
                element.appendChild(doc.createTextNode(""))
    return xml_bytes(doc), (sha256(original.encode()), expected)


def package_editions(book, entries, images, destination):
    source = Path(book["source"])
    source_hash = book["source_sha256"]
    raw_source = source.read_bytes()
    if sha256(raw_source) != source_hash:
        raise ValueError(f"{source.name}: el EPUB no coincide con su huella.")
    if not entries:
        raise ValueError(f"{source.name}: no hay escenas para exportar.")
    exported = []
    for eink in (False, True):
        profile = "BOOX" if eink else "Color"
        target = edition_path(book["title"], eink, destination)
        if target.resolve() == source.resolve():
            raise ValueError("La salida no puede sobrescribir el EPUB original.")
        target.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".edicion-", dir=target.parent) as temp, ZipFile(BytesIO(raw_source)) as original:
            staged = Path(temp) / target.name
            opf, package = read_package(original)
            version = package.documentElement.getAttribute("version")
            language = package_language(package)
            if not (language.lower() == book["language"].lower()
                    or language.lower().startswith(book["language"].lower() + "-")):
                raise ValueError(f"{source.name}: el idioma no coincide con el catálogo.")
            nav, ncx = package_navigation(opf, package)
            [spine] = package.getElementsByTagNameNS(OPF, "spine")
            reading = {node.getAttribute("idref") for node in elements(spine) if node.getAttribute("linear") != "no"}
            reading_paths = {resolve(opf, node.getAttribute("href"))[0]
                             for node in package.getElementsByTagNameNS(OPF, "item")
                             if node.getAttribute("id") in reading} - {nav, ncx}
            assets = str(PurePosixPath(opf).parent / PREFIX)
            notes, css = assets + "/notas.xhtml", assets + "/ilustraciones.css"
            resources = {PREFIX + "-notes": (notes, "application/xhtml+xml"), PREFIX + "-css": (css, "text/css")}
            chapters = defaultdict(list)
            note_entries = []
            for row, image in entries:
                cut = row["id"]
                if not re.fullmatch(r"P[0-9]+", cut) or PREFIX + "-" + cut in resources:
                    raise ValueError(f"{source.name}: corte inválido o duplicado {cut}.")
                if row["source_xhtml"] not in reading_paths:
                    raise ValueError(f"{cut}: el corte no forma parte de la lectura lineal.")
                jpg = f"{assets}/{cut}.jpg"
                note_entries.append((row, image, jpg))
                chapters[row["source_xhtml"]].append((row, image, jpg))
                resources[f"{PREFIX}-{cut}"] = (jpg, "image/jpeg")
            if set(name for name, _ in resources.values()).intersection(original.namelist()):
                raise ValueError(f"{source.name}: contiene recursos reservados para las ilustraciones.")
            label = "illustrated edition" if language.lower().split("-")[0] == "en" else "edición ilustrada"
            title_suffix = f" ({label}{' BOOX' if eink else ''})"
            changed = update_package(original, source_hash, notes, resources, slug=book["slug"], variant=profile,
                                     title_suffix=title_suffix)
            projections = {}
            for chapter, chapter_entries in chapters.items():
                changed[chapter], projections[chapter] = chapter_markup(original.read(chapter), chapter, chapter_entries, css, notes)
            expected_jpgs = {}
            with ZipFile(staged, "w", ZIP_DEFLATED) as output:
                output.writestr("mimetype", original.read("mimetype"), compress_type=ZIP_STORED)
                for member in original.infolist():
                    if member.filename != "mimetype":
                        data = changed.get(member.filename)
                        output.writestr(copy(member), original.read(member) if data is None else data)
                output.writestr(css, COLLECTION_STYLES)
                output.writestr(notes, make_notes(note_entries, notes, css, language, version=version))
                for row, image, jpg in note_entries:
                    raw = (images / image["file"]).read_bytes()
                    if png_sha256(raw) != image["sha256"]:
                        raise ValueError(f"{row['id']}: el PNG cambió durante la exportación.")
                    if eink:
                        jpeg = eink_jpeg(raw)
                    else:
                        with Image.open(BytesIO(raw)) as picture:
                            buffer = BytesIO()
                            picture.convert("RGB").save(buffer, format="JPEG", quality=85, optimize=True)
                            jpeg = buffer.getvalue()
                    digest = sha256(jpeg)
                    if "profile_jpeg_sha256" in image and digest != image["profile_jpeg_sha256"][profile]:
                        raise ValueError(f"{row['id']}: el JPEG {profile} difiere de la imagen auditada.")
                    output.writestr(jpg, jpeg, compress_type=ZIP_STORED)
                    expected_jpgs[jpg] = digest
            verify(staged, original, projections, resources, expected_jpgs, notes, opf, eink)
            staged.replace(target)
        print(f"{target} · {len(entries)} ilustraciones verificadas", flush=True)
        exported.append(target)
    return exported


def package_language(package):
    return package.getElementsByTagNameNS(DC, "language")[0].firstChild.data.strip()


def original_markup(body):
    ids, links = set(), set()
    images = []
    for node in body.getElementsByTagName("*"):
        for name in ("id", "xml:id", "name"):
            if node.hasAttribute(name):
                ids.add((name, node.getAttribute(name)))
        if node.localName in {"a", "area"} and node.hasAttribute("href"):
            links.add(node.getAttribute("href"))
        if node.localName in {"img", "image"}:
            images.append(node.toxml())
    return ids, links, images


def verify(path, original, projections, resources, expected_jpgs, notes, opf, eink):
    with ZipFile(path) as edition:
        def require(condition, detail):
            if not condition:
                raise ValueError(f"{path.name}: {detail}")

        files = edition.namelist()
        require(files[0] == "mimetype" and edition.getinfo("mimetype").compress_type == ZIP_STORED
                and edition.read("mimetype") == b"application/epub+zip"
                and edition.testzip() is None, "contenedor EPUB inválido")
        require(len(files) == len(set(files)) and set(files) == set(original.namelist()) | {p for p, _ in resources.values()},
                "el contenido del ZIP no coincide con la edición")
        package = minidom.parseString(edition.read(opf))
        old_package = minidom.parseString(original.read(opf))
        language = package_language(old_package)
        require(package_language(package) == language, "cambió el idioma de la edición")
        items = {n.getAttribute("id"): (resolve(opf, n.getAttribute("href"))[0], n.getAttribute("media-type"))
                 for n in package.getElementsByTagNameNS(OPF, "item")}
        require(all(items.get(key) == entry for key, entry in resources.items()), "manifiesto de imágenes incompleto")
        for tag in ("manifest", "spine"):
            old = elements(old_package.getElementsByTagNameNS(OPF, tag)[0])
            new = elements(package.getElementsByTagNameNS(OPF, tag)[0])
            require([n.toxml() for n in old] == [n.toxml() for n in new[:len(old)]],
                    f"cambió el orden o un recurso de {tag}")
        last = elements(package.getElementsByTagNameNS(OPF, "spine")[0])[-1]
        require(last.getAttribute("idref") == PREFIX + "-notes", "fichas ausentes del orden de lectura")
        nav, ncx = package_navigation(opf, package)
        require((nav, ncx) == package_navigation(opf, old_package) and (nav or ncx), "cambió la ubicación del índice")
        if nav:
            before = elements(navigation_list(read_xhtml(original.read(nav))))
            after = elements(navigation_list(read_xhtml(edition.read(nav))))
            check_toc(before, after, nav, notes, XHTML, "a", "href", require)
        if ncx:
            before = elements(minidom.parseString(original.read(ncx)).getElementsByTagNameNS(NCX, "navMap")[0])
            after = elements(minidom.parseString(edition.read(ncx)).getElementsByTagNameNS(NCX, "navMap")[0])
            check_toc(before, after, ncx, notes, NCX, "content", "src", require)
        for member in original.infolist():
            if member.filename not in {*projections, nav, ncx, opf}:
                require(edition.read(member.filename) == original.read(member),
                        f"recurso original modificado: {member.filename}")
        notes_dom = read_xhtml(edition.read(notes))
        note_root = notes_dom.documentElement
        require(note_root.getAttribute("xml:lang") == language
                and (not nav or note_root.getAttribute("lang") == language)
                and notes_dom.getElementsByTagNameNS(XHTML, "title")[0].firstChild.data == notes_labels(language)[0],
                "idioma o título de las fichas incorrecto")
        notes_by_id = {n.getAttribute("id"): n for n in notes_dom.getElementsByTagNameNS(XHTML, "div")}
        require(len(notes_by_id) == len(expected_jpgs), "número de fichas incorrecto")
        for chapter, (text_hash, expected) in projections.items():
            doc = read_xhtml(edition.read(chapter))
            [body] = doc.getElementsByTagNameNS(XHTML, "body")
            blocks = {n.getAttribute("id"): n for n in body.getElementsByTagNameNS(XHTML, "div") if n.getAttribute("class") == "bi-illustration"}
            require(set(blocks) == set(expected), f"{chapter}: cortes incorrectos")
            offsets = dict.fromkeys(blocks.values())
            text = document_projection(body, offsets)
            require(sha256(text.encode()) == text_hash, f"{chapter}: cambió el texto original")
            [original_body] = read_xhtml(original.read(chapter)).getElementsByTagNameNS(XHTML, "body")
            ids, links, images = original_markup(original_body)
            for marker, node in blocks.items():
                cut = marker[len(MARKER):]
                require(offsets[node] == expected[marker], f"{cut}: posición de lectura incorrecta")
                [link] = node.getElementsByTagNameNS(XHTML, "a")
                [image] = node.getElementsByTagNameNS(XHTML, "img")
                note = "bi-note-" + cut
                jpeg = resolve(chapter, image.getAttribute("src"))[0]
                require(jpeg == items[PREFIX + "-" + cut][0] and image.getAttribute("alt"), f"{cut}: recurso o texto alternativo incorrecto")
                require(resolve(chapter, link.getAttribute("href")) == (notes, note), f"{cut}: enlace de ficha incorrecto")
                [back] = notes_by_id[note].getElementsByTagNameNS(XHTML, "a")
                require(resolve(notes, back.getAttribute("href")) == (chapter, marker), f"{cut}: enlace de regreso incorrecto")
                data = edition.read(jpeg)
                require(sha256(data) == expected_jpgs[jpeg], f"{cut}: imagen de otro corte")
                with Image.open(BytesIO(data)) as picture:
                    picture.load()
                    require(picture.size == IMAGE_DIMENSIONS and picture.mode == ("L" if eink else "RGB"),
                            f"{cut}: dimensiones o perfil incorrectos")
                node.parentNode.removeChild(node)
            output_ids, output_links, output_images = original_markup(body)
            require(ids == output_ids and links == output_links and images == output_images,
                    f"{chapter}: cambiaron anclas, enlaces o imágenes originales")


def check_toc(before, after, path, notes, namespace, tag, attribute, require):
    require(len(after) == len(before) + 1 and [n.toxml() for n in after[:-1]] == [n.toxml() for n in before],
            f"cambiaron las entradas originales de {path}")
    [link] = after[-1].getElementsByTagNameNS(namespace, tag)
    require(resolve(path, link.getAttribute(attribute)) == (notes, ""), f"fichas ausentes de {path}")
