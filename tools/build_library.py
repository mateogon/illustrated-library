"""Build the public reading data from the pinned EPUB editions."""

import argparse
import copy
import hashlib
import html
import json
import os
import posixpath
import re
import shutil
import tempfile
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit
from xml.dom import minidom
from xml.etree import ElementTree as ET
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "tools/library_sources.json"
RELEASE_URL = "https://github.com/mateogon/illustrated-library/releases/download/library-v1/"
XHTML = "http://www.w3.org/1999/xhtml"
DC = "http://purl.org/dc/elements/1.1/"
OPF = "http://www.idpf.org/2007/opf"
NOTES = "EPUB/book-illustrator/notas.xhtml"
MIME = "application/epub+zip"
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
CUT = re.compile(r"P[0-9]{4}\Z")
PRIVATE_LITERAL = (
    b"file://", b"/users/", b"/volumes/", b"/home/", b"/private/tmp/", b"/private/var/",
    b"/var/folders/", b"localhost:", b"request_id", b"prompt_sha256", b"attempt_id",
)
PRIVATE_TOKENS = (
    (b"sk-", re.compile(rb"sk-(?:proj-|svcacct-)?[a-z0-9_-]{20,}")),
    (b"ghp_", re.compile(rb"ghp_[a-z0-9]{20,}")),
    (b"xox", re.compile(rb"xox[baprs]-[a-z0-9-]{10,}")),
)
TEXT_SUFFIXES = {".xml", ".opf", ".xhtml", ".ncx", ".css", ".svg"}
SPINE_TAGS = {
    "a", "abbr", "article", "aside", "b", "blockquote", "br", "caption", "cite",
    "code", "dd", "div", "dl", "dt", "em", "figcaption", "figure", "h1", "h2",
    "h3", "h4", "h5", "h6", "header", "hr", "i", "li", "nav", "ol", "p", "pre",
    "q", "s", "section", "small", "span", "strong", "sub", "sup", "table",
    "tbody", "td", "th", "thead", "tr", "u", "ul",
}
DROP_TAGS = {"script", "style", "iframe", "object", "form", "template"}
VOID_TAGS = {"br", "hr"}
SAFE_ID = re.compile(r"[A-Za-z0-9_.:\-]+\Z")


def check(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def is_private(data):
    folded = data.lower()
    return any(value in folded for value in PRIVATE_LITERAL) or any(
        prefix in folded and pattern.search(folded) for prefix, pattern in PRIVATE_TOKENS
    )


def check_jpeg(data, name):
    check(data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9"), f"Invalid JPEG: {name}")
    offset = 2
    while offset + 4 <= len(data):
        check(data[offset] == 0xFF, f"Invalid JPEG header: {name}")
        code = data[offset + 1]
        if code == 0xDA:
            return
        size = int.from_bytes(data[offset + 2:offset + 4], "big")
        end = offset + size + 2
        check(size >= 2 and end <= len(data), f"Invalid JPEG segment: {name}")
        if 0xE0 <= code <= 0xEF or code == 0xFE:
            # The release JPEGs carry their JFIF header, without EXIF, XMP or comments.
            check(code == 0xE0 and data[offset + 4:end].startswith(b"JFIF\0"), f"Extra JPEG metadata: {name}")
        offset = end
    raise ValueError(f"JPEG has no scan: {name}")


def write_bytes(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_bytes() == data:
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as staged:
        staged.write(data)
        staged_path = Path(staged.name)
    try:
        staged_path.chmod(0o644)
        staged_path.replace(path)
    finally:
        staged_path.unlink(missing_ok=True)


def write_json(path, value):
    data = (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
    check(not is_private(data), f"Private string in {path.name}")
    write_bytes(path, data)


def safe_path(root, name):
    path = (root / name).resolve()
    check(path.is_relative_to(root), f"Path outside the input directory: {Path(name).name}")
    return path


def relative_member(base, href):
    return posixpath.normpath(posixpath.join(posixpath.dirname(base), unquote(href)))


def epub_package(epub):
    container = ET.fromstring(epub.read("META-INF/container.xml"))
    opf = container.find(".//{*}rootfile").attrib["full-path"]
    package = ET.fromstring(epub.read(opf))
    manifest = {
        item.attrib["id"]: relative_member(opf, item.attrib["href"])
        for item in package.findall("./{*}manifest/{*}item")
    }
    spine = [manifest[ref.attrib["idref"]] for ref in package.findall("./{*}spine/{*}itemref")]
    check(len(spine) == len(set(spine)), "Repeated document in EPUB spine")
    check(set(manifest.values()) <= set(epub.namelist()), "Missing EPUB manifest resource")
    return opf, package, spine


def public_epub_check(epub):
    names = epub.namelist()
    check(names[0] == "mimetype" and epub.read("mimetype") == MIME.encode(), "Invalid EPUB MIME")
    check(len(set(names)) == len(names), "Duplicate archive members")
    for name in names:
        check(name == posixpath.normpath(name) and not name.startswith(("/", "../"))
              and "\\" not in name and not is_private(name.encode()), "Unsafe EPUB member name")
        if name == "mimetype":
            continue
        suffix = Path(name).suffix
        check(suffix in TEXT_SUFFIXES | {".jpg"}, f"Unreviewed EPUB resource type: {suffix}")
        if suffix in TEXT_SUFFIXES:
            raw = epub.read(name)
            check(not is_private(raw) and (b"%" not in raw or not is_private(unquote(raw.decode()).encode())),
                  f"Private metadata in EPUB member {name}")
        elif suffix == ".jpg":
            check_jpeg(epub.read(name), name)


def element_text(node):
    return " ".join("".join(node.itertext()).split()) if node is not None else ""


def illustration_notes(epub):
    root = ET.fromstring(epub.read(NOTES))
    notes = {}
    for node in root.findall(".//{*}div"):
        if node.get("class") != "bi-note":
            continue
        cut = node.get("id", "").removeprefix("bi-note-")
        check(CUT.fullmatch(cut) and cut not in notes, "Invalid or repeated illustration note")
        title = element_text(node.find("{*}h2"))
        description = element_text(node.find("{*}p"))
        link = node.find(".//{*}a")
        target = urlsplit(link.attrib["href"])
        check(not target.scheme and not target.netloc and target.fragment == "bi-reading-" + cut,
              "Invalid illustration backlink")
        check(title and description, f"Incomplete illustration note: {cut}")
        notes[cut] = {
            "title": title, "description": description, "chapter": relative_member(NOTES, target.path),
        }
    return notes


def remove_dom_element(doc, element_id):
    nodes = [node for node in doc.getElementsByTagName("*") if node.getAttribute("id") == element_id]
    check(len(nodes) == 1, f"Cannot identify held node {element_id}")
    nodes[0].parentNode.removeChild(nodes[0])


def clean_epub(source, target, edition):
    """Preserve vetted resources and remove only the explicitly held cut."""
    with ZipFile(source) as original:
        public_epub_check(original)
        opf, _, spine = epub_package(original)
        notes = illustration_notes(original)
        check(len(notes) == edition["inputIllustrations"], f"Unexpected illustration count for {edition['id']}")
        package_dom = minidom.parseString(original.read(opf))
        metadata = package_dom.getElementsByTagNameNS(OPF, "metadata")[0]
        metadata_changed = False
        source_url = edition["rights"]["sources"][0]["url"]
        # Brand the ebook as our extracted edition. Cite the transcription in the public catalogue.
        if urlsplit(source_url).hostname in {"www.gutenberg.org", "gutenberg.org"}:
            source_url = None
        for name, value in (
            ("rights", rights_notice(edition)),
            ("source", source_url),
        ):
            if value and not metadata.getElementsByTagNameNS(DC, name):
                child = package_dom.createElementNS(DC, "dc:" + name)
                child.appendChild(package_dom.createTextNode(value))
                metadata.appendChild(child)
                metadata_changed = True

        replacements = {}
        excluded = set()
        if edition.get("editorialNotes"):
            notes_dom = minidom.parseString(original.read(NOTES))
            for note in edition["editorialNotes"]:
                cut = note["cut"]
                chapter = notes[cut]["chapter"]
                chapter_dom = minidom.parseString(replacements.get(chapter) or original.read(chapter))
                remove_dom_element(chapter_dom, "bi-reading-" + cut)
                replacements[chapter] = chapter_dom.toxml(encoding="utf-8")
                remove_dom_element(notes_dom, "bi-note-" + cut)
                item_nodes = [
                    node for node in package_dom.getElementsByTagNameNS(OPF, "item")
                    if posixpath.basename(node.getAttribute("href")) == cut + ".jpg"
                ]
                check(len(item_nodes) == 1, f"Cannot identify held image: {cut}")
                item = item_nodes[0]
                excluded.add(relative_member(opf, item.getAttribute("href")))
                item.parentNode.removeChild(item)
            replacements[NOTES] = notes_dom.toxml(encoding="utf-8")
        if excluded or metadata_changed:
            replacements[opf] = package_dom.toxml(encoding="utf-8")

        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=target.parent) as stage:
            staged = Path(stage) / target.name
            if not replacements:
                shutil.copyfile(source, staged)
            else:
                with ZipFile(staged, "w") as out:
                    for member in original.infolist():
                        if member.filename not in excluded:
                            data = replacements.get(member.filename)
                            out.writestr(copy.copy(member), original.read(member) if data is None else data)
            verify_release(staged, edition)
            with ZipFile(staged) as public:
                public_epub_check(public)
                new_opf, _, new_spine = epub_package(public)
                check(new_spine == spine and new_opf == opf, "The public release changed the reading order")
                check(set(public.namelist()) == set(original.namelist()) - excluded,
                      "Unexpected EPUB resource change")
                if excluded:
                    for chapter in set(replacements) & set(spine) - {NOTES}:
                        before = ET.fromstring(original.read(chapter))
                        after = ET.fromstring(public.read(chapter))
                        check(element_text(before) == element_text(after), "A correction changed the book text")
            staged.chmod(0o644)
            staged.replace(target)


def rights_notice(edition):
    notice = "Historical text in the public domain in Chile and the United States."
    license_name = edition["rights"]["transcriptionLicense"]
    if license_name:
        notice += f" Digital source contributions are credited under {license_name}."
    return notice


def verify_release(path, edition):
    with ZipFile(path) as epub:
        notes = illustration_notes(epub)
        held = {entry["cut"] for entry in edition.get("editorialNotes", [])}
        check(not held.intersection(notes) and len(notes) == edition["inputIllustrations"] - len(held),
              f"Wrong count or held cut in {path.name}")


class Section:
    """Convert a reading document into safe HTML fragments around its images."""

    def __init__(self, epub, edition, path, sections, notes, site):
        self.epub = epub
        self.edition = edition
        self.path = path
        self.sections = sections
        self.notes = notes
        self.site = site
        self.blocks = []
        self.stack = []
        self.parts = []
        self.has_content = False

    def flush_html(self):
        if self.has_content:
            markup = "".join(self.parts) + "".join(close for _, close in reversed(self.stack))
            self.blocks.append({"type": "html", "html": markup})
        self.parts = [opened for opened, _ in self.stack]
        self.has_content = False

    def add_text(self, value):
        if value:
            self.parts.append(html.escape(value, quote=False))
            self.has_content |= bool(value.strip())

    def add_image(self, node, cut=None):
        self.flush_html()
        asset = relative_member(self.path, node.attrib["src"])
        alt = " ".join(node.attrib["alt"].split())
        check(bool(alt), f"Missing image description in {self.path}")
        data = self.epub.read(asset)
        edition_id = self.edition["id"]
        if cut:
            note = self.notes[cut]
            check(note["chapter"] == self.path and posixpath.basename(asset) == cut + ".jpg",
                  f"Image or reading link mismatch for {cut}")
            target = f"images/{edition_id}/{cut}.jpg"
            title, description = note["title"], note["description"]
        else:
            name = posixpath.basename(asset)
            check(edition_id == "alices-adventures-in-wonderland-en"
                  and re.fullmatch(r"illustration-[0-9]+\.svg", name),
                  f"Unreviewed original image: {asset}")
            verify_historical_svg(data)
            target = f"images/{edition_id}/historical/{name}"
            cut = Path(name).stem
            title, description = "Illustration by John Tenniel", alt
        write_bytes(self.site / target, data)
        self.blocks.append({
            "type": "illustration", "id": cut, "src": target, "alt": alt, "title": title,
            "description": description, "historical": not target.endswith(".jpg"),
        })

    def safe_attributes(self, node, tag):
        attrs = {}
        identifier = node.get("id")
        if identifier and SAFE_ID.fullmatch(identifier):
            attrs["id"] = identifier
        if tag == "a" and node.get("href"):
            link = urlsplit(node.attrib["href"])
            fragment = unquote(link.fragment)
            if link.scheme in {"http", "https"} and link.netloc and not link.username:
                attrs["href"] = node.attrib["href"]
                attrs["rel"] = "noopener noreferrer"
            elif not link.scheme and not link.netloc:
                target = relative_member(self.path, link.path) if link.path else self.path
                if target == self.path and fragment and SAFE_ID.fullmatch(fragment):
                    attrs["href"] = "#" + fragment
                elif target in self.sections:
                    section = self.sections[target]
                    attrs["href"] = "#" + section
                    attrs["data-section-id"] = section
                    if fragment and SAFE_ID.fullmatch(fragment):
                        attrs["data-fragment"] = fragment
        return "".join(f' {key}="{html.escape(value, quote=True)}"' for key, value in attrs.items())

    def walk(self, node):
        tag = node.tag.rsplit("}", 1)[-1]
        if tag in DROP_TAGS:
            raise ValueError(f"Active content in EPUB reading document: {self.path}")
        if node.get("class") == "bi-illustration":
            cut = node.attrib["id"].removeprefix("bi-reading-")
            check(CUT.fullmatch(cut) and cut in self.notes, f"Missing match for cut {cut}")
            self.add_image(node.find(".//{*}img"), cut)
            return
        if tag == "img":
            self.add_image(node)
            return
        if tag in VOID_TAGS:
            self.parts.append(f"<{tag}>")
            self.has_content = True
            return
        keep = tag in SPINE_TAGS
        if keep:
            self.parts.append(f"<{tag}{self.safe_attributes(node, tag)}>")
            self.stack.append((f"<{tag}>", f"</{tag}>"))
        self.add_text(node.text)
        for child in node:
            self.walk(child)
            self.add_text(child.tail)
        if keep:
            self.parts.append(self.stack.pop()[1])


def verify_historical_svg(data):
    root = ET.fromstring(data)
    permitted = {"svg", "title", "path", "g", "circle", "polygon", "line", "rect", "ellipse", "polyline"}
    for node in root.iter():
        check(node.tag.startswith("{http://www.w3.org/2000/svg}") and node.tag.rsplit("}", 1)[-1] in permitted,
              "Unexpected element in a historical illustration")
        for name, value in node.attrib.items():
            check(not re.search(r"(?:^on|href|style)", name, re.IGNORECASE) and "url(" not in value.lower(),
                  "External or active content in a historical illustration")


def reading_data(epub_path, edition, site):
    with ZipFile(epub_path) as epub:
        public_epub_check(epub)
        _, _, spine = epub_package(epub)
        notes = illustration_notes(epub)
        parts = [path for path in spine if path != NOTES]
        index = {path: f"s{number:03}" for number, path in enumerate(parts, 1)}
        sections = []
        seen = []
        for path in parts:
            raw = ET.fromstring(epub.read(path))
            body = raw.find("{*}body")
            check(body is not None and raw.tag == "{" + XHTML + "}html", f"Missing XHTML body in {path}")
            formatter = Section(epub, edition, path, index, notes, site)
            formatter.walk(body)
            formatter.flush_html()
            section_title = element_text(raw.find("./{*}head/{*}title"))
            check(bool(section_title), f"Missing chapter title in {path}")
            seen.extend(block for block in formatter.blocks if block["type"] == "illustration")
            sections.append({"id": index[path], "title": section_title, "blocks": formatter.blocks})
        generated = [image for image in seen if not image["historical"]]
        history = {image["src"] for image in seen if image["historical"]}
        check(len(generated) == len(notes) and {image["id"] for image in generated} == set(notes),
              f"Missing or duplicate web illustration for {edition['id']}")
        check(len(history) == edition["historicalIllustrations"], f"Historical art count mismatch for {edition['id']}")
        check(bool(generated), f"No illustrations for {edition['id']}")
        return sections, len(generated), generated[0]["src"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-catalog", type=Path,
                        help="Optionally create release EPUBs using the original, pinned catalogue")
    parser.add_argument("--release-dir", type=Path, default=ROOT / "releases",
                        help="Directory containing or receiving the 32 release EPUBs")
    parser.add_argument("--site", type=Path, default=ROOT / "site", help="Site directory for data and images")
    args = parser.parse_args()
    edition_catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    check(edition_catalog["schemaVersion"] == 1, "Unsupported library metadata version")
    check(len(edition_catalog["works"]) == 10 and len(edition_catalog["editions"]) == 16,
          "Unexpected library roster")
    sources = {}
    if args.source_catalog:
        document = json.loads(args.source_catalog.read_text(encoding="utf-8"))
        sources = {entry["id"]: entry for entry in document["editions"]}
    editions = []
    for item in edition_catalog["editions"]:
        public_fields = {
            "id", "workId", "title", "language", "author", "translator", "description", "editionNote",
            "rights", "historicalIllustrations", "editorialNotes",
        }
        edition = {key: value for key, value in item.items() if key in public_fields}
        edition_id = edition["id"]
        check(SLUG.fullmatch(edition_id) and all(previous["id"] != edition_id for previous in editions),
              f"Invalid or repeated edition identifier: {edition_id}")
        downloads = {}
        for profile in ("Color", "BOOX"):
            slug = profile.lower()
            name = f"{edition_id}-{slug}.epub"
            artifact = args.release_dir / name
            if args.source_catalog:
                entry = sources[edition_id]
                check(entry["language"] == edition["language"] and entry["work"] == edition["workId"]
                      and entry["approved"] == item["inputIllustrations"], f"Source record mismatch for {edition_id}")
                root = args.source_catalog.resolve().parent
                source = safe_path(root, entry["files"][profile])
                reader_copy = safe_path(root, entry["reader_files"][profile])
                check(sha256(source.read_bytes()) == sha256(reader_copy.read_bytes())
                      == item["inputSha256"][profile], f"Source hash mismatch for {edition_id} {profile}")
                clean_epub(source, artifact, item)
            else:
                verify_release(artifact, item)
                with ZipFile(artifact) as epub:
                    public_epub_check(epub)
            data = artifact.read_bytes()
            downloads[slug] = {"file": name, "url": RELEASE_URL + quote(name), "bytes": len(data),
                               "sha256": sha256(data)}
        sections, count, cover = reading_data(args.release_dir / downloads["color"]["file"], item, args.site)
        section_file = f"data/books/{edition_id}.json"
        write_json(args.site / section_file, {"id": edition_id, "sections": sections})
        edition.update({"cover": cover, "sectionCount": len(sections), "illustrationCount": count,
                        "sectionsPath": section_file, "downloads": downloads})
        editions.append(edition)
        print(f"{edition_id}: {len(sections)} reading sections, {count} new illustrations")
    check(sum(edition["illustrationCount"] for edition in editions) == 557, "Unexpected public image total")
    write_json(args.site / "data/library.json",
               {"schemaVersion": 1, "works": edition_catalog["works"], "editions": editions})


if __name__ == "__main__":
    main()
