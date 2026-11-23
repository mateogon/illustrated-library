"""Utilities for packaging edition metadata, notes and grayscale images."""

from datetime import datetime, timezone
from io import BytesIO
from math import log
import posixpath
from uuid import NAMESPACE_URL, uuid5
from urllib.parse import quote, unquote, urlsplit
from xml.dom import minidom

OPF = "http://www.idpf.org/2007/opf"
DC = "http://purl.org/dc/elements/1.1/"
NCX = "http://www.daisy.org/z3986/2005/ncx/"
XHTML = "http://www.w3.org/1999/xhtml"
EPUB = "http://www.idpf.org/2007/ops"
PREFIX = "book-illustrator"
NOTES_TITLE = "Fichas de ilustración"
STYLES = b"""
.bi-illustration {
  margin: .9em auto;
  text-align: center;
  page-break-inside: avoid;
  break-inside: avoid;
}
.bi-image-link { display: block; width: 76%; max-width: 27em; margin: 0 auto; }
.bi-image-link img { display: block; width: 100%; height: auto; max-width: 100%; margin: 0 auto; }
.bi-back { text-decoration: underline; }
@media all and (orientation: landscape) {
  .bi-image-link img { width: auto; max-height: 50vh; }
}
.bi-note + .bi-note { page-break-before: always; break-before: page; }
.bi-note { page-break-inside: avoid; break-inside: avoid; }
"""


def append(parent, tag, attributes=None, content=None, *, namespace=None):
    doc = parent.ownerDocument
    if namespace is None and parent.prefix and ":" not in tag:
        tag = f"{parent.prefix}:{tag}"
    node = doc.createElementNS(namespace or parent.namespaceURI, tag)
    parent.appendChild(node)
    for key, value in (attributes or {}).items():
        node.setAttribute(key, value)
    if content is not None:
        node.appendChild(doc.createTextNode(content))
    return node


def relative(from_file, to_file):
    return quote(posixpath.relpath(to_file, posixpath.dirname(from_file)), safe="/")


def resolve(document, href):
    url = urlsplit(href)
    path = posixpath.normpath(posixpath.join(posixpath.dirname(document), unquote(url.path))) if url.path else document
    return path, unquote(url.fragment)


def xml_bytes(doc):
    # A parsed &#13; in text must remain escaped or it becomes LF on the next read.
    return doc.toxml(encoding="utf-8").replace(b"\r", b"&#13;")


def add_to_chapter(raw, chapter, entries, css, notes):
    doc = minidom.parseString(raw.replace(b"&nbsp;", b"&#160;"))
    [head] = doc.getElementsByTagNameNS(XHTML, "head")
    [body] = doc.getElementsByTagNameNS(XHTML, "body")
    append(head, "link", {"rel": "stylesheet", "type": "text/css", "href": relative(chapter, css)})
    paragraphs = body.getElementsByTagNameNS(XHTML, "p")
    ids = {node.getAttribute("id") for node in doc.getElementsByTagName("*") if node.hasAttribute("id")}
    for paragraph, image, jpg in entries:
        cut = paragraph["id"]
        anchor_id = f"bi-reading-{cut}"
        if anchor_id in ids:
            raise ValueError(f"{chapter}: el identificador {anchor_id} ya existe.")
        index = paragraph["last_xhtml_p"] - 1
        if index < 0 or index >= len(paragraphs):
            raise ValueError(f"{chapter}: párrafo XHTML fuera de rango para {cut}.")
        node = paragraphs[index]
        block = doc.createElementNS(XHTML, "div")
        block.setAttribute("id", anchor_id)
        block.setAttribute("class", "bi-illustration")
        following = node.nextSibling
        if following and following.nodeType == doc.TEXT_NODE and not following.data.strip():
            following = following.nextSibling
        node.parentNode.insertBefore(block, following)
        link = f"{relative(chapter, notes)}#bi-note-{cut}"
        image_link = append(block, "a", {"class": "bi-image-link", "href": link})
        alt = f"{image['title']} — {image['description']}"
        if content_label := image.get("content_label"):
            alt += f" · {content_label}"
        append(image_link, "img", {"src": relative(chapter, jpg), "alt": alt})
    return xml_bytes(doc)


def notes_labels(language):
    if language.lower().split("-")[0] == "en":
        return "Illustration notes", "Back to the passage"
    return NOTES_TITLE, "Volver a la lectura"


def make_notes(entries, notes, css, language, *, version="2.0"):
    doctype = ('<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" '
               '"http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">') if version == "2.0" else "<!DOCTYPE html>"
    doc = minidom.parseString(
        f'{doctype}<html xmlns="{XHTML}"><head/><body/></html>'
    )
    doc.documentElement.setAttributeNS("http://www.w3.org/XML/1998/namespace", "xml:lang", language)
    if version == "3.0":
        doc.documentElement.setAttribute("lang", language)
    title, back_label = notes_labels(language)
    [head] = doc.getElementsByTagNameNS(XHTML, "head")
    [body] = doc.getElementsByTagNameNS(XHTML, "body")
    append(head, "title", content=title)
    append(head, "link", {"rel": "stylesheet", "type": "text/css", "href": relative(notes, css)})
    append(body, "h1", content=title)
    for paragraph, image, _ in entries:
        cut = paragraph["id"]
        note = append(body, "div", {"class": "bi-note", "id": f"bi-note-{cut}"})
        append(note, "h2", content=image["title"])
        append(note, "p", content=image["description"])
        if content_label := image.get("content_label"):
            append(note, "p", content=content_label)
        back = f"{relative(notes, paragraph['source_xhtml'])}#bi-reading-{cut}"
        append(append(note, "p"), "a", {"class": "bi-back", "href": back}, back_label)
    return xml_bytes(doc)


def read_package(source):
    container = minidom.parseString(source.read("META-INF/container.xml"))
    [rootfile] = container.getElementsByTagName("rootfile")
    opf_path = rootfile.getAttribute("full-path")
    package = minidom.parseString(source.read(opf_path))
    return opf_path, package


def package_navigation(opf_path, package):
    [manifest] = package.getElementsByTagNameNS(OPF, "manifest")
    [spine] = package.getElementsByTagNameNS(OPF, "spine")
    items = manifest.getElementsByTagNameNS(OPF, "item")
    nav = None
    if package.documentElement.getAttribute("version") == "3.0":
        [nav_item] = [item for item in items if "nav" in item.getAttribute("properties").split()]
        nav = resolve(opf_path, nav_item.getAttribute("href"))[0]
    ncx = None
    if ncx_id := spine.getAttribute("toc"):
        [ncx_item] = [item for item in items if item.getAttribute("id") == ncx_id]
        ncx = resolve(opf_path, ncx_item.getAttribute("href"))[0]
    return nav, ncx


def navigation_list(doc):
    [toc] = [node for node in doc.getElementsByTagNameNS(XHTML, "nav")
             if "toc" in node.getAttributeNS(EPUB, "type").split()]
    [listing] = [node for node in toc.childNodes if node.namespaceURI == XHTML and node.localName == "ol"]
    return listing


def update_package(source, source_hash, notes, resources, *, slug="edition", variant="contenido", title_suffix=" (edición ilustrada)"):
    opf_path, package = read_package(source)
    version = package.documentElement.getAttribute("version")
    if version not in {"2.0", "3.0"}:
        raise ValueError(f"Versión EPUB no admitida: {version}")
    if version == "3.0" and "META-INF/encryption.xml" in source.namelist():
        raise ValueError("Cambiar el identificador podría invalidar recursos cifrados u ofuscados.")
    [metadata] = package.getElementsByTagNameNS(OPF, "metadata")
    [manifest] = package.getElementsByTagNameNS(OPF, "manifest")
    [spine] = package.getElementsByTagNameNS(OPF, "spine")
    titles = metadata.getElementsByTagNameNS(DC, "title")
    title = next((node for node in titles if any(
        meta.getAttribute("property") == "title-type"
        and meta.getAttribute("refines") == "#" + node.getAttribute("id")
        and meta.firstChild.data.strip() == "main"
        for meta in metadata.getElementsByTagNameNS(OPF, "meta")
    )), titles[0])
    title.firstChild.data += title_suffix
    language = metadata.getElementsByTagNameNS(DC, "language")[0].firstChild.data.strip()
    notes_title, _ = notes_labels(language)
    edition = uuid5(NAMESPACE_URL, f"{PREFIX}:{slug}:{variant}:{source_hash}")
    uid = f"urn:uuid:{edition}"
    uid_id = f"{PREFIX}-uid"
    existing = {node.getAttribute("id") for node in package.getElementsByTagName("*")}
    if existing.intersection({uid_id, *resources}):
        raise ValueError("La edición ya usa identificadores reservados de Book Illustrator.")
    identifier_tag = f"{title.prefix}:identifier" if title.prefix else "identifier"
    identifier = append(metadata, identifier_tag, {"id": uid_id}, uid, namespace=DC)
    if not title.prefix:
        identifier.setAttribute("xmlns", DC)
    nav_path, ncx_path = package_navigation(opf_path, package)
    package.documentElement.setAttribute("unique-identifier", uid_id)
    for item_id, (path, media) in resources.items():
        append(manifest, "item", {"id": item_id, "href": relative(opf_path, path), "media-type": media})
    append(spine, "itemref", {"idref": f"{PREFIX}-notes"})

    changed = {}
    if nav_path:
        [modified] = [node for node in metadata.getElementsByTagNameNS(OPF, "meta")
                      if node.getAttribute("property") == "dcterms:modified"]
        modified.firstChild.data = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        doc = minidom.parseString(source.read(nav_path))
        listing = navigation_list(doc)
        append(append(listing, "li"), "a", {"href": relative(nav_path, notes)}, notes_title)
        changed[nav_path] = xml_bytes(doc)
    changed[opf_path] = xml_bytes(package)
    if ncx_path:
        changed[ncx_path] = update_ncx(source.read(ncx_path), ncx_path, notes, notes_title, uid, title.firstChild.data)
    return changed


def update_ncx(raw, ncx_path, notes, notes_title, uid, title):
    ncx = minidom.parseString(raw)
    for node in ncx.getElementsByTagNameNS(NCX, "meta"):
        if node.getAttribute("name") == "dtb:uid":
            node.setAttribute("content", uid)
    [doc_title] = ncx.getElementsByTagNameNS(NCX, "docTitle")
    [label] = doc_title.getElementsByTagNameNS(NCX, "text")
    if label.firstChild:
        label.firstChild.data = title
    else:
        label.appendChild(ncx.createTextNode(title))
    [nav] = ncx.getElementsByTagNameNS(NCX, "navMap")
    orders = [node.getAttribute("playOrder") for node in ncx.getElementsByTagName("*")
              if node.hasAttribute("playOrder")]
    attrs = {"id": f"{PREFIX}-notes"}
    if orders:
        attrs["playOrder"] = str(1 + max(map(int, orders)))
    note_nav = append(nav, "navPoint", attrs)
    append(append(note_nav, "navLabel"), "text", content=notes_title)
    append(note_nav, "content", {"src": relative(ncx_path, notes)})
    return xml_bytes(ncx)


def eink_jpeg(data):
    try:
        from PIL import Image, ImageFilter, ImageOps, ImageStat
    except ImportError as exc:
        raise ValueError("El perfil e-ink requiere Pillow. Ejecuta con uv run --with pillow python.") from exc

    with Image.open(BytesIO(data)) as source:
        gray = source.convert("L")
    normalized = ImageOps.autocontrast(gray, cutoff=(0.25, 0.5))
    median = max(1, min(254, ImageStat.Stat(normalized).median[0]))
    # Lift deep shadows more than daylight scenes; keep part of the original
    # exposure so night scenes still look like night.
    gamma = max(0.43, min(1.0, log(150 / 255) / log(median / 255)))
    lifted = normalized.point([round(255 * (value / 255) ** gamma) for value in range(256)])
    detailed = lifted.filter(ImageFilter.UnsharpMask(radius=14, percent=65, threshold=4))
    result = Image.blend(gray, detailed, 0.87)
    output = BytesIO()
    result.save(output, format="JPEG", quality=88, optimize=True)
    return output.getvalue()
