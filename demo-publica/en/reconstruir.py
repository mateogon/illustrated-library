"""Rebuild and verify the pinned editions of works originally written in English."""

import argparse
from hashlib import sha256
from html import escape
from html.entities import name2codepoint
from io import BytesIO
import json
from pathlib import Path
import posixpath
import re
import subprocess
from tempfile import TemporaryDirectory
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[2]
CATALOG = Path(__file__).with_name("manifest.json")
XHTML = "http://www.w3.org/1999/xhtml"
OPS = "http://www.idpf.org/2007/ops"
OPF = "http://www.idpf.org/2007/opf"
MIMETYPE = b"application/epub+zip"
REMOVED_SE = {"imprint.xhtml", "colophon.xhtml", "uncopyright.xhtml", "cover.svg", "logo.svg", "titlepage.svg", "se.css"}


def title_page(edition):
    translator = edition.get("translator")
    credit = f'<p>Traducción de {escape(translator["name"])}</p>' if translator else ""
    return xhtml(edition, edition['title'], f'<h1>{escape(edition["title"])}</h1><p>{escape(edition["author"]["name"])}</p>{credit}', 'titlepage')


def xhtml(edition, title, body, semantic):
    lang = edition['epub_language']
    return (f'<html xmlns="{XHTML}" xmlns:epub="{OPS}" xml:lang="{lang}" lang="{lang}">'
            f'<head><title>{escape(title)}</title></head><body epub:type="{semantic}">{body}</body></html>').encode()


def standard_ebooks(source, edition):
    with ZipFile(BytesIO(source)) as archive:
        root, *_ = archive.namelist()
        prefix = root + "src/epub/"
        package = ET.fromstring(archive.read(prefix + "content.opf"))
        assert edition["title"] == package.findtext("{*}metadata/{*}title")
        assert edition["epub_language"] == package.findtext("{*}metadata/{*}language")
        assert edition["author"]["name"] == package.findtext("{*}metadata/{*}creator")
        manifest = {node.get("id"): node for node in package.find("{*}manifest") if node.get("id") not in REMOVED_SE}
        keys = [node.get("idref") for node in package.find("{*}spine") if node.get("idref") in manifest]
        files = {node.get("href"): archive.read(prefix + node.get("href")) for node in manifest.values()}
    files["text/titlepage.xhtml"] = title_page(edition)
    nav = ET.fromstring(files["toc.xhtml"])
    for section in nav.findall(".//{*}nav"):
        section.attrib.pop("aria-labelledby", None)
        for ordered in section.iter(f"{{{XHTML}}}ol"):
            for li in list(ordered):
                link = li.find("{*}a")
                if link is not None and link.get("href", "").partition("#")[0] not in files:
                    ordered.remove(li)
    ET.register_namespace("", XHTML)
    ET.register_namespace("epub", OPS)
    files["toc.xhtml"] = ET.tostring(nav, encoding="utf-8")
    items = [(node.get("href"), node.get("media-type"), node.get("properties", "")) for node in manifest.values()]
    items = [(href, media, "" if href == "text/titlepage.xhtml" else properties) for href, media, properties in items]
    return files, items, [manifest[key].get("href") for key in keys]


def ncx(edition, nav):
    title = escape(edition["title"])
    uid = f'urn:book-illustrator:demo:{edition["id"]}'
    links = ET.fromstring(nav).find(f'.//{{{XHTML}}}nav[@{{{OPS}}}type="toc"]/{{{XHTML}}}ol')
    points = []
    for order, link in enumerate(links.iter(f"{{{XHTML}}}a"), 1):
        label = escape(" ".join("".join(link.itertext()).split()))
        target = escape(link.get("href"), quote=True)
        points.append(f'<navPoint id="nav-{order}" playOrder="{order}"><navLabel><text>{label}</text></navLabel>'
                      f'<content src="{target}"/></navPoint>')
    return (f'<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><head>'
            f'<meta name="dtb:uid" content="{uid}"/><meta name="dtb:depth" content="1"/>'
            f'<meta name="dtb:totalPageCount" content="0"/><meta name="dtb:maxPageNumber" content="0"/></head>'
            f'<docTitle><text>{title}</text></docTitle><navMap>{"".join(points)}</navMap></ncx>').encode()


def make_epub(edition, files, items, spine):
    rights = escape(edition["rights"]["embedded_notice"])
    source = escape(edition["source"]["credit_url"])
    author = escape(edition["author"]["name"])
    title = escape(edition["title"])
    lang = edition["epub_language"]
    uid = f'urn:book-illustrator:demo:{edition["id"]}'
    modified = edition["modified"]
    role_rows = []
    for role, key in (("trl", "translator"), ("ill", "illustrator")):
        if edition.get(key):
            name = escape(edition[key]["name"])
            role_rows.append(f'<dc:contributor id="{role}">{name}</dc:contributor>'
                             f'<meta property="role" refines="#{role}" scheme="marc:relators">{role}</meta>')
    files["toc.ncx"] = ncx(edition, files["toc.xhtml"])
    items.append(("toc.ncx", "application/x-dtbncx+xml", ""))
    identifiers = {href: f'item-{number}' for number, (href, _, _) in enumerate(items, 1)}
    manifest = []
    for href, media, properties in items:
        prop = f' properties="{properties}"' if properties else ""
        manifest.append(f'<item id="{identifiers[href]}" href="{href}" media-type="{media}"{prop}/>')
    reading_order = ''.join(f'<itemref idref="{identifiers[href]}"/>' for href in spine)
    files["content.opf"] = (
        f'<package xmlns="{OPF}" xmlns:dc="http://purl.org/dc/elements/1.1/" version="3.0" '
        f'unique-identifier="uid" xml:lang="{lang}"><metadata><dc:identifier id="uid">{uid}</dc:identifier>'
        f'<dc:title>{title}</dc:title><dc:creator>{author}</dc:creator><dc:language>{lang}</dc:language>'
        f'<dc:source>{source}</dc:source><dc:rights>{rights}</dc:rights>{"".join(role_rows)}'
        f'<meta property="dcterms:modified">{modified}</meta></metadata><manifest>{"".join(manifest)}</manifest>'
        f'<spine toc="{identifiers["toc.ncx"]}">{reading_order}</spine></package>'
    ).encode()
    parsed = {name: ET.fromstring(data) for name, data in files.items() if name.endswith((".xhtml", ".opf", ".ncx", ".svg"))}
    ids = {name: {element.get("id") for element in tree.iter()} for name, tree in parsed.items()}
    for filename, tree in parsed.items():
        for node in tree.iter():
            for attr in ("href", "src"):
                if attr not in node.attrib:
                    continue
                uri = urlsplit(node.get(attr))
                if uri.scheme or uri.netloc:
                    continue
                target = posixpath.normpath(posixpath.join(posixpath.dirname(filename), unquote(uri.path))) if uri.path else filename
                if target not in files or (uri.fragment and unquote(uri.fragment) not in ids.get(target, set())):
                    raise ValueError(f"{filename}: enlace roto a {node.get(attr)}")
    container = (b'<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
                 b'<rootfiles><rootfile full-path="EPUB/content.opf" media-type="application/oebps-package+xml"/>'
                 b'</rootfiles></container>')
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr(ZipInfo("mimetype"), MIMETYPE, compress_type=ZIP_STORED)
        archive.writestr(ZipInfo("META-INF/container.xml"), container, compress_type=ZIP_DEFLATED)
        for name, data in sorted(files.items()):
            archive.writestr(ZipInfo("EPUB/" + name), data, compress_type=ZIP_DEFLATED, compresslevel=9)
    return output.getvalue()


def caballero(source, edition):
    def entity(match):
        code = name2codepoint.get(match[1])
        return f"&#{code};" if code is not None else match[0]

    def has_anchor(element, identifier):
        return element.find(f'.//{{{XHTML}}}a[@id="{identifier}"]') is not None

    def clean(parent):
        parent.attrib.pop("name", None)
        for index in range(len(parent) - 1, -1, -1):
            child = parent[index]
            if child.get("href", "").startswith("images/"):
                # Conserva el texto que va después de la imagen en el original.
                if index:
                    previous = parent[index - 1]
                    previous.tail = (previous.tail or "") + (child.tail or "")
                else:
                    parent.text = (parent.text or "") + (child.tail or "")
                parent.remove(child)
            else:
                clean(child)

    raw = re.sub(r"&([a-zA-Z][a-zA-Z0-9]+);", entity, source.decode("utf-8-sig"))
    document = ET.fromstring(raw)
    body = list(document.find("{*}body"))
    begin = next(i for i, element in enumerate(body) if has_anchor(element, "PROLOGO_DE_LA_EDICION_ESPANOLA"))
    first_part = next(i for i, element in enumerate(body) if has_anchor(element, "PARTE_I"))
    end = next(i for i, element in enumerate(body) if element.get("class") == "c" and "".join(element.itertext()) == "FIN")
    notes, = [element for element in body if element.get("class") == "footnotes"]
    assert len(notes.findall('{*}div[@class="footnote"]')) == 6
    groups = [("prologo", body[begin:first_part - 1], "frontmatter")]
    for element in body[first_part:end + 1]:
        heading = element.tag.split("}")[-1]
        if heading in {"h2", "h3"}:
            anchor, = [node.get("id") for node in element.findall("{*}a") if node.get("id", "").startswith(("PARTE_", "CAPITULO_"))]
            groups.append((anchor.lower(), [], "bodymatter"))
        if element.get("class") != "figcenter":
            groups[-1][1].append(element)
    groups.append(("notas", [notes], "backmatter"))
    assert sum(name.startswith("capitulo_") for name, _, _ in groups) == 34
    assert sum(name.startswith("parte_") for name, _, _ in groups) == 6

    ET.register_namespace("", XHTML)
    anchors = {node.get("id"): name + ".xhtml" for name, elements, _ in groups
               for element in elements for node in element.iter() if "id" in node.attrib}
    label = "Página de título"
    links = [f'<li><a href="text/titlepage.xhtml">{label}</a></li>']
    files = {"text/titlepage.xhtml": title_page(edition)}
    for name, elements, semantic in groups:
        for element in elements:
            clean(element)
            for anchor in element.iter(f"{{{XHTML}}}a"):
                target = anchor.get("href", "")
                if target.startswith("#"):
                    target_file = anchors[target[1:]]
                    if target_file != name + ".xhtml":
                        anchor.set("href", target_file + target)
        heading = elements[0].find("{*}h2") if name == "notas" else elements[0]
        label = ": ".join(filter(None, (part.strip() for part in "".join(heading.itertext()).splitlines())))
        if name in {"capitulo_xxxii", "capitulo_xxxiv"}:
            # La numeración aparece grabada solo en la imagen de esos capítulos.
            label = f"CAPÍTULO {name.split('_')[1].upper()}: {label}"
        href = f"text/{name}.xhtml"
        content = "".join(ET.tostring(element, encoding="unicode") for element in elements)
        css = b'<link rel="stylesheet" href="../lectura.css"/>'
        files[href] = xhtml(edition, label, content, semantic).replace(b"</head>", css + b"</head>", 1)
        links.append(f'<li><a href="{href}">{escape(label)}</a></li>')
    spine = list(files)
    files["toc.xhtml"] = xhtml(edition, "Índice", f'<nav epub:type="toc"><h1>Índice</h1><ol>{"".join(links)}</ol></nav>', "frontmatter")
    files["lectura.css"] = b"""p { margin: .55em 0; }
h2, h3, .c { text-align: center; }
.r { text-align: right; }
.blockquot, .poetry { margin: 1em 2em; }
.poem span { display: block; }
.smcap { font-variant: small-caps; }
.fnanchor { vertical-align: super; font-size: .8em; }
"""
    items = [(key, "application/xhtml+xml", "") for key in spine]
    items.extend([("toc.xhtml", "application/xhtml+xml", "nav"), ("lectura.css", "text/css", "")])
    return files, items, spine


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("editions", nargs="*", metavar="EDITION")
    parser.add_argument("--source-dir", "--fuentes", dest="fuentes", type=Path, help="Read source files from an existing directory instead of downloading them.")
    parser.add_argument("--verify", "--verificar", dest="verificar", action="store_true", help="Verify local EPUBs without rebuilding them.")
    args = parser.parse_args()
    entries = json.loads(CATALOG.read_text(encoding="utf-8"))["editions"]
    known = {entry["id"] for entry in entries}
    if missing := set(args.editions) - known:
        parser.error(f"ediciones desconocidas: {', '.join(sorted(missing))}")
    with TemporaryDirectory(prefix="book-illustrator-demo-en-") as temporary:
        for entry in entries:
            if args.editions and entry["id"] not in args.editions:
                continue
            target = ROOT / entry["path"]
            if args.verificar:
                data = target.read_bytes()
            else:
                origin = entry["source"]
                path = (args.fuentes or Path(temporary)) / origin["filename"]
                if not args.fuentes:
                    subprocess.run(["curl", "-fSsL", "--connect-timeout", "8", "--max-time", "120",
                                    origin["download_url"], "-o", str(path)], check=True)
                source = path.read_bytes()
                if sha256(source).hexdigest() != origin["sha256"]:
                    raise ValueError(f"{path}: huella de la fuente distinta de la edición fijada")
                build = standard_ebooks if origin["kind"] == "standard-ebooks" else caballero
                data = make_epub(entry, *build(source, entry))
            if len(data) != entry["bytes"] or sha256(data).hexdigest() != entry["sha256"]:
                raise ValueError(f"{target}: tamaño o huella distinto del manifiesto")
            if not args.verificar:
                target.parent.mkdir(parents=True, exist_ok=True)
                with TemporaryDirectory(dir=target.parent) as staging:
                    artifact = Path(staging) / target.name
                    artifact.write_bytes(data)
                    artifact.replace(target)
            print(f'{entry["id"]} {len(data)} {entry["sha256"]}')


if __name__ == "__main__":
    main()
