#!/usr/bin/env python3
"""Check an Illustrated Library build offline with Python 3.8 or later."""

import argparse
import hashlib
import json
import posixpath
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

RELEASE_PREFIX = "https://github.com/mateogon/illustrated-library/releases/download/library-v1/"
HELD_BACK = ("treasure-island-es-caballero-1901", "P0682")
HISTORICAL_EDITION = "alices-adventures-in-wonderland-en"
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
CUT = re.compile(r"P[0-9]{4,}")
SHA256 = re.compile(r"[a-f0-9]{64}")
PRIVATE_MARKERS = {
    "local filesystem path": r"(?:/Users/|/home/|/Volumes/|/private/(?:tmp|var)/|/var/folders/|/tmp/book-illustrator|file://|~/Documents/|[A-Za-z]:\\Users\\)",
    "private project artifacts": r"(?:outputs/demo-publica/|book-illustrator-public-build|escenas-auditadas\.json|comparacion-cache\.json|auditoria-visual/|/mateogon/book-illustrator(?:[/.?#]|$))",
    "credentials or private request IDs": r"(?:sk-(?:proj-|svcacct-)?[\w-]{20,}|gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|req_[a-f0-9]{24,}|OPENAI_API_KEY|authorization:\s*bearer\s+\S+)",
    "private operations metadata": r'(?:"(?:apiKey|prompt_sha256|draft_call|decision_call|request_id|attempt_id|api_cost_usd|inputSha256|sourceTextSha256)"\s*:)',
}
PRIVATE_HINTS = (
    "/users/", "/home/", "/volumes/", "/private/", "/var/folders/", "/tmp/book-illustrator",
    "file://", "~/documents/", "\\users\\", "outputs/demo-publica/", "auditoria-visual/", "sk-", "ghp_", "gho_", "ghu_", "ghs_", "ghr_", "github_pat_",
    "xox", "req_", "openai_api_key", "authorization", "book-illustrator", "escenas-auditadas",
    "comparacion-cache", "apikey", "prompt_sha256", "draft_call", "decision_call",
    "request_id", "attempt_id", "api_cost_usd", "inputsha256", "sourcetextsha256",
)
BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "br", "caption", "dd", "div",
    "dl", "dt", "figcaption", "figure", "footer", "h1", "h2", "h3", "h4", "h5",
    "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section",
    "table", "td", "th", "tr", "ul",
}
SAFE_HTML_TAGS = BLOCK_TAGS | {
    "a", "abbr", "b", "bdi", "bdo", "cite", "code", "col", "colgroup", "dfn",
    "em", "i", "kbd", "mark", "q", "rp", "rt", "ruby", "s", "samp", "small",
    "span", "strong", "sub", "sup", "tbody", "tfoot", "thead", "time", "u", "var",
}
SAFE_ATTRS = {
    "aria-label", "class", "colspan", "data-fragment", "data-section-id", "datetime", "dir", "epub:type", "href",
    "id", "lang", "rel", "role", "rowspan", "scope", "title", "type", "value", "xml:lang",
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def clean(text):
    return " ".join(text.split())


def reject_private(text, location):
    if "%" in text:
        text = unquote(text)
    lowered = text.lower()
    if not any(hint in lowered for hint in PRIVATE_HINTS):
        return
    for label, pattern in PRIVATE_MARKERS.items():
        require(not re.search(pattern, text, re.IGNORECASE),
                f"{location}: possible {label}")


def load_json(path):
    def unique_keys(pairs):
        mapped = dict(pairs)
        require(len(mapped) == len(pairs), f"{path.name}: duplicate JSON field")
        return mapped

    content = path.read_text(encoding="utf-8")
    reject_private(content, path.name)
    return json.loads(content, object_pairs_hook=unique_keys)


def keys(value, expected, location):
    require(isinstance(value, dict) and value.keys() == set(expected.split()),
            f"{location}: unexpected fields")


def required_text(value, location):
    require(isinstance(value, str) and bool(clean(value)), f"{location}: missing text")


def slug(value, location):
    require(isinstance(value, str) and SLUG.fullmatch(value), f"{location}: invalid ID")


def public_url(url, location):
    require(isinstance(url, str) and url == url.strip() and not re.search(r"[\s\\]", url),
            f"{location}: invalid URL")
    reject_private(url, location)
    target = urlsplit(url)
    hostname = target.hostname or ""
    require(
        target.scheme == "https" and hostname
        and not (target.username or target.password or target.port)
        and re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,63}", hostname)
        and hostname.rsplit(".", 1)[-1] not in {"local", "localhost", "internal", "test", "invalid", "example"}
        and not hostname.startswith(("localhost.", "example."))
        and not re.search(r"(?:[?&])(?:access_token|api_key|signature|jwt|token)=", unquote(url), re.I)
        and not re.search(r"%(?![0-9a-f]{2})", url, re.I),
        f"{location}: expected a public HTTPS URL",
    )


def local_file(root, relative, location):
    require(isinstance(relative, str) and relative
            and not any(part in {"", ".", ".."} or part.startswith(".") for part in relative.split("/"))
            and not re.search(r"[\\%?#:\x00-\x1f]", relative), f"{location}: invalid local path")
    path = root / relative
    require(path.is_file() and path.resolve() == path,
            f"{location}: missing file or symbolic link: {relative}")
    return path


def file_digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def epub_path(document, href):
    url = urlsplit(href)
    return posixpath.normpath(posixpath.join(posixpath.dirname(document), unquote(url.path)))


def epub_tree(archive, path, location):
    data = archive.read(path)
    reject_private(data.decode("utf-8-sig"), location)
    return ET.fromstring(data)


def epub_images_and_text(archive, document, edition_id, location):
    tree = epub_tree(archive, document, location)
    body = tree.find("{*}body")
    require(body is not None, f"{location}: XHTML has no body")
    pieces = []
    images = []
    text = []
    anchors = []
    image_nodes = []
    for node in body.iter():
        identifier = node.get("id", "")
        if identifier.startswith("bi-reading-"):
            cut = identifier[len("bi-reading-"):]
            require(CUT.fullmatch(cut) and node.get("class") == "bi-illustration",
                    f"{location}: bad reading anchor")
            link = node.find(".//{*}a")
            image = node.find(".//{*}img")
            target = urlsplit(link.attrib["href"])
            require(not target.scheme and not target.netloc and target.fragment == "bi-note-" + cut
                    and epub_path(document, target.path) == "EPUB/book-illustrator/notas.xhtml"
                    and posixpath.basename(image.attrib["src"]) == cut + ".jpg",
                    f"{location}: wrong note or asset for {cut}")
            anchors.append(cut)
            image_nodes.append(image)

    def visit(node):
        tag = node.tag.rsplit("}", 1)[-1]
        if tag in BLOCK_TAGS:
            text.append(" ")
        if tag == "img":
            path = epub_path(document, node.attrib["src"])
            filename = posixpath.basename(path)
            caption = node.attrib.get("alt", "")
            generated = posixpath.basename(posixpath.dirname(path)) == "book-illustrator"
            digest = hashlib.sha256(archive.read(path)).hexdigest()
            if generated:
                require(node in image_nodes, f"{location}: image is missing its reading anchor")
            else:
                require(edition_id == HISTORICAL_EDITION and filename.endswith(".svg"),
                        f"{location}: unexpected historical image")
            pieces.append(clean("".join(text)))
            text.clear()
            images.append((path, not generated, caption, digest))
        else:
            text.append(node.text or "")
            for child in node:
                visit(child)
                text.append(child.tail or "")
        if tag in BLOCK_TAGS:
            text.append(" ")

    visit(body)
    pieces.append(clean("".join(text)))
    title = tree.find("./{*}head/{*}title")
    return {"anchors": anchors, "images": images, "text": pieces, "title": clean("".join(title.itertext()))}


def epub_sections(path, edition):
    location = path.name
    with zipfile.ZipFile(path) as archive:
        require(archive.read("mimetype") == b"application/epub+zip", f"{location}: bad EPUB mimetype")
        members = archive.namelist()
        require(len(members) == len(set(members)), f"{location}: duplicate EPUB resource")
        for name in members:
            require(not name.startswith("/") and "\\" not in name
                    and not any(part.startswith(".") for part in name.split("/"))
                    and posixpath.basename(name) != "calibre_bookmarks.txt",
                    f"{location}: invalid or reader-private EPUB resource")
            if edition["id"] == HELD_BACK[0]:
                require(posixpath.basename(name) != HELD_BACK[1] + ".jpg",
                        f"{location}: held cut persists in archive")
            reject_private(name, location)
            if Path(name).suffix.lower() in {".css", ".ncx", ".svg"}:
                reject_private(archive.read(name).decode("utf-8-sig"), location)
        container = epub_tree(archive, "META-INF/container.xml", location)
        package_path = container.find(".//{*}rootfile").attrib["full-path"]
        package = epub_tree(archive, package_path, location)
        metadata = package.find("{*}metadata")

        def values(tag):
            return {clean(node.text or "") for node in metadata.findall(f"{{*}}{tag}")}

        language = values("language")
        require(edition["language"] in {item.split("-", 1)[0] for item in language},
                f"{location}: wrong language")
        authors = values("creator")
        require(edition["author"] in authors or (
            edition["author"] == "Anonymous" and "Anónimo" in authors
        ), f"{location}: author differs from the EPUB")
        translator = edition["translator"]
        if translator:
            require(translator in values("contributor"), f"{location}: missing translator credit")
        require(any(title == edition["title"] or title.startswith(edition["title"] + " (")
                    for title in values("title")), f"{location}: title differs from the EPUB")
        rights = values("rights")
        require(bool(rights) and all(rights), f"{location}: missing rights notice")
        license_id = edition["rights"]["transcriptionLicense"]
        if license_id:
            normalize = lambda value: re.sub(r"[^a-z0-9.]", "", value.lower())
            require(any(normalize(license_id) in normalize(notice) for notice in rights),
                    f"{location}: missing EPUB transcription license")
        source_urls = values("source")
        for url in source_urls:
            public_url(url, location)
        require(not source_urls or bool(source_urls & {s["url"] for s in edition["rights"]["sources"]}),
                f"{location}: source record is missing from the public credits")
        manifest = {item.attrib["id"]: epub_path(package_path, item.attrib["href"])
                    for item in package.findall("{*}manifest/{*}item")}
        sections = [manifest[item.attrib["idref"]] for item in package.findall("{*}spine/{*}itemref")]
        require(len(sections) == len(set(sections)), f"{location}: duplicate spine document")
        narrative = [name for name in sections if not name.endswith("/book-illustrator/notas.xhtml")]
        notes = [name for name in sections if name not in narrative]
        require(len(notes) == 1, f"{location}: missing illustration notes document")
        notes_tree = epub_tree(archive, notes[0], location)
        documented = {}
        for note in notes_tree.iter():
            note_id = note.get("id", "")
            if not note_id.startswith("bi-note-"):
                continue
            cut = note_id[len("bi-note-"):]
            require(CUT.fullmatch(cut) and cut not in documented, f"{location}: bad illustration note")
            link = note.find(".//{*}a")
            target = urlsplit(link.attrib["href"])
            require(not target.scheme and not target.netloc and target.fragment == "bi-reading-" + cut,
                    f"{location}: bad back link for {cut}")
            documented[cut] = epub_path(notes[0], target.path)
        reading = [epub_images_and_text(archive, name, edition["id"], f"{location} {name}")
                   for name in narrative]
        anchored = {}
        for name, section in zip(narrative, reading):
            for cut in section["anchors"]:
                require(cut not in anchored, f"{location}: duplicate anchor {cut}")
                anchored[cut] = name
        require(documented == anchored, f"{location}: notes do not point to their reading anchors")
        return reading


class Fragment(HTMLParser):
    """Read visible text and links while checking the public HTML subset."""

    def __init__(self, location):
        super().__init__(convert_charrefs=True)
        self.location = location
        self.text = []
        self.ids = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        require(tag in SAFE_HTML_TAGS, f"{self.location}: unexpected HTML tag <{tag}>")
        attr_map = dict(attrs)
        require(len(attr_map) == len(attrs), f"{self.location}: duplicate HTML attribute")
        for attr, value in attrs:
            require(attr in SAFE_ATTRS, f"{self.location}: unexpected HTML attribute {attr}")
            if attr in {"data-fragment", "data-section-id", "rel"}:
                require(tag == "a" and attr_map.get("href"), f"{self.location}: stray link attribute")
            if attr == "id":
                required_text(value, self.location)
                self.ids.append(value)
            elif attr == "href":
                require(tag == "a", f"{self.location}: href outside a link")
                required_text(value, self.location)
                self.links.append((value, attr_map.get("data-section-id"), attr_map.get("data-fragment")))
        if tag in BLOCK_TAGS:
            self.text.append(" ")

    def handle_endtag(self, tag):
        require(tag in SAFE_HTML_TAGS, f"{self.location}: unexpected HTML closing tag </{tag}>")
        if tag in BLOCK_TAGS:
            self.text.append(" ")

    def handle_data(self, text):
        self.text.append(text)

    def handle_comment(self, data):
        raise ValueError(f"{self.location}: unexpected HTML comment")

    def handle_decl(self, decl):
        raise ValueError(f"{self.location}: unexpected HTML declaration")


def check_links(sections, edition):
    for section_id, section in sections.items():
        for href, target_id, fragment in section["links"]:
            if href.startswith("https://"):
                require(not target_id and not fragment, f"{edition}: external link with a local target")
                public_url(href, f"{edition} {section_id}")
                continue
            location = f"{edition} {section_id} link"
            require(href.startswith("#") and href[1:], f"{location}: unexpected relative URL")
            if target_id:
                require(href == "#" + target_id and target_id in sections,
                        f"{location}: broken section link {href}")
                if fragment:
                    require(unquote(fragment) in sections[target_id]["ids"],
                            f"{location}: missing fragment in section {target_id}")
            else:
                require(not fragment and unquote(href[1:]) in section["ids"],
                        f"{location}: missing anchor {href}")


def web_book(site, edition, expected, boox_images, used_images):
    edition_id = edition["id"]
    expected_path = f"data/books/{edition_id}.json"
    require(edition["sectionsPath"] == expected_path, f"{edition_id}: unexpected book path")
    sections_file = local_file(site, expected_path, edition_id)
    book = load_json(sections_file)
    keys(book, "id sections", edition_id)
    require(book["id"] == edition_id, f"{edition_id}: book ID differs from the catalog")
    sections = book["sections"]
    require(len(sections) == len(expected) == len(boox_images) == edition["sectionCount"],
            f"{edition_id}: EPUB and web section counts differ")

    link_index = {}
    new_sources = []
    original_sources = []
    generated = set()
    for index, (section, color, boox) in enumerate(zip(sections, expected, boox_images), 1):
        location = f"{edition_id} section {index}"
        keys(section, "id title blocks", location)
        section_id = section["id"]
        require(section_id == f"s{index:03}", f"{location}: wrong section ID or sequence")
        require(section["title"] == color["title"] == boox["title"],
                f"{location}: EPUB and web titles differ")
        links = []
        fragment_ids = set()
        segments = []
        spans = []
        image_blocks = []
        require(isinstance(section["blocks"], list), f"{location}: invalid blocks")
        for block in section["blocks"]:
            if block["type"] == "html":
                keys(block, "type html", location)
                required_text(block["html"], location)
                parser = Fragment(location)
                parser.feed(block["html"])
                parser.close()
                require(not fragment_ids.intersection(parser.ids)
                        and len(parser.ids) == len(set(parser.ids)),
                        f"{location}: duplicate fragment ID")
                fragment_ids.update(parser.ids)
                spans.extend(parser.text)
                links.extend(parser.links)
                continue
            keys(block, "type id src alt title description historical", location)
            require(block["type"] == "illustration", f"{location}: wrong block type")
            segments.append(clean("".join(spans)))
            spans.clear()
            image_blocks.append(block)
        segments.append(clean("".join(spans)))

        require(segments == color["text"] == boox["text"],
                f"{location}: web text or inline illustration placement differs from the EPUB")
        color_images = color["images"]
        require(len(image_blocks) == len(color_images) == len(boox["images"]),
                f"{location}: illustration count differs from the EPUB")
        for block, color_image, boox_image in zip(image_blocks, color_images, boox["images"]):
            require(color_image[:3] == boox_image[:3],
                    f"{location}: EPUB profiles disagree on illustration order or caption")
            epub_name, historical, caption, digest = color_image
            filename = posixpath.basename(epub_name)
            image_id = Path(filename).stem
            require(block["historical"] is historical, f"{location}: wrong illustration origin")
            if historical:
                path = f"images/{edition_id}/historical/{filename}"
                original_sources.append(path)
                require(boox_image[3] == digest, f"{location}: historical image differs across EPUB profiles")
            else:
                path = f"images/{edition_id}/{filename}"
                new_sources.append(path)
                require((edition_id, image_id) != HELD_BACK and image_id not in generated,
                        f"{location}: held or duplicate cut {image_id}")
                generated.add(image_id)
            require(block["src"] == path, f"{location}: image is out of reading order: {path}")
            required_text(block["id"], location)
            required_text(block["alt"], location)
            if not historical:
                require(block["id"] == image_id, f"{location}: generated cut ID differs from the EPUB")
                required_text(block["title"], location)
                required_text(block["description"], location)
                formatted = f"{block['title']} — {block['description']}"
                require(block["alt"] in {formatted, caption}, f"{location}: wrong image alternative text")
                require(caption in {
                    formatted, formatted + " · Scene reframed for content",
                    formatted + " · Encuadre adaptado por contenido",
                }, f"{location}: image title or caption differs from the EPUB")
            else:
                require(block["id"] == image_id and block["title"] == "Illustration by John Tenniel"
                        and clean(block["alt"]) == clean(block["description"]) == clean(caption),
                        f"{location}: historical image title, attribution or alternative text differs")
            image_file = local_file(site, path, location)
            require(file_digest(image_file) == digest, f"{location}: image bytes differ from the Color EPUB")
            used_images.add(path)
        image_ids = {block["id"] for block in image_blocks}
        require(len(image_ids) == len(image_blocks), f"{location}: duplicate inline image ID")
        link_index[section_id] = {"ids": fragment_ids | image_ids, "links": links}
    check_links(link_index, edition_id)
    require(len(new_sources) == edition["illustrationCount"] and bool(new_sources),
            f"{edition_id}: generated total disagrees with the catalog")
    require(len(original_sources) == edition["historicalIllustrations"],
            f"{edition_id}: historical total disagrees with the catalog")
    require(edition["cover"] == new_sources[0], f"{edition_id}: wrong cover")
    return len(new_sources), len(original_sources)


def check_downloads(releases, edition, assets):
    edition_id = edition["id"]
    keys(edition["downloads"], "color boox", edition_id)
    paths = {}
    for profile, download in edition["downloads"].items():
        location = f"{edition_id} {profile}"
        keys(download, "file bytes sha256 url", location)
        name = f"{edition_id}-{profile}.epub"
        require(download["file"] == name and download["url"] == RELEASE_PREFIX + name,
                f"{location}: unexpected release asset or URL")
        public_url(download["url"], location)
        path = local_file(releases, name, location)
        require(type(download["bytes"]) is int and path.stat().st_size == download["bytes"],
                f"{location}: EPUB byte count differs")
        require(SHA256.fullmatch(download["sha256"]) and file_digest(path) == download["sha256"],
                f"{location}: EPUB SHA-256 differs")
        assets[name] = download["sha256"]
        paths[profile] = path
    return paths


def check_credits(edition):
    edition_id = edition["id"]
    rights = edition["rights"]
    keys(rights, "chile unitedStates terms transcriptionLicense includedLayers generatedArt sources", edition_id)
    for field in ("chile", "unitedStates", "terms", "includedLayers", "generatedArt"):
        required_text(rights[field], f"{edition_id} rights {field}")
    license_id = rights["transcriptionLicense"]
    if license_id is not None:
        required_text(license_id, f"{edition_id} transcription license")
    if license_id in {"CC-BY-SA-3.0", "CC-BY-SA-4.0"}:
        version = license_id.rsplit("-", 1)[1]
        require(any(urlsplit(source["url"]).hostname == "creativecommons.org"
                    and urlsplit(source["url"]).path.startswith(f"/licenses/by-sa/{version}/")
                    for source in rights["sources"]), f"{edition_id}: omitted attribution license link")
    require(isinstance(rights["sources"], list) and len(rights["sources"]) >= 2,
            f"{edition_id}: incomplete source credits")
    urls = set()
    for source in rights["sources"]:
        keys(source, "label url", edition_id)
        required_text(source["label"], f"{edition_id} source label")
        public_url(source["url"], f"{edition_id} source")
        urls.add(source["url"])
    require(len(urls) == len(rights["sources"]), f"{edition_id}: duplicate source URL")
    require(any(urlsplit(url).hostname == "openai.com"
                and "/policies/services-agreement/" in urlsplit(url).path for url in urls),
            f"{edition_id}: missing generated art terms")
    if edition["historicalIllustrations"]:
        require("tenniel" in rights["includedLayers"].lower(),
                f"{edition_id}: missing historical artwork attribution")


def tree_files(root):
    return {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file() or path.is_symlink()}


def check_release_checksums(releases, assets):
    sums = releases / "SHA256SUMS.txt"
    if not sums.exists():
        return False
    sums = local_file(releases, sums.name, "checksums")
    expected = {}
    for line in sums.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64}) [ *]([^/]+\.epub)", line)
        require(match and match[2] not in expected, "SHA256SUMS.txt: malformed or duplicate entry")
        expected[match[2]] = match[1]
    require(expected == assets, "SHA256SUMS.txt: asset names or hashes differ from the library")
    return True


def check_library(site, releases):
    library = load_json(local_file(site, "data/library.json", "library"))
    keys(library, "schemaVersion works editions", "library")
    require(type(library["schemaVersion"]) is int and library["schemaVersion"] == 1,
            "library: unsupported schema version")
    require(len(library["works"]) == 10 and len(library["editions"]) == 16,
            "library: expected 10 works and 16 editions")
    works = {}
    for work in library["works"]:
        keys(work, "id title description editionIds", "work")
        work_id = work["id"]
        slug(work_id, "work")
        require(work_id not in works, f"{work_id}: duplicate work")
        required_text(work["title"], f"{work_id} title")
        required_text(work["description"], f"{work_id} description")
        require(isinstance(work["editionIds"], list) and bool(work["editionIds"]),
                f"{work_id}: missing editions")
        for edition_id in work["editionIds"]:
            slug(edition_id, work_id)
        require(len(work["editionIds"]) == len(set(work["editionIds"])), f"{work_id}: duplicate edition reference")
        works[work_id] = set(work["editionIds"])

    observed = {work_id: set() for work_id in works}
    edition_ids = set()
    assets = {}
    image_paths = set()
    counts = Counter()
    sections = 0
    for edition in library["editions"]:
        edition_id = edition["id"]
        fields = ("id workId title language author translator description editionNote rights cover "
                  "sectionCount illustrationCount historicalIllustrations sectionsPath downloads")
        if edition_id == HELD_BACK[0]:
            fields += " editorialNotes"
            notes = edition["editorialNotes"]
            require(len(notes) == 1, f"{edition_id}: unexpected editorial exceptions")
            keys(notes[0], "cut sectionTitle text", edition_id)
            require(notes[0]["cut"] == HELD_BACK[1], f"{edition_id}: wrong editorial exception")
            required_text(notes[0]["sectionTitle"], f"{edition_id} editorial exception title")
            required_text(notes[0]["text"], f"{edition_id} editorial exception text")
        keys(edition, fields, edition_id)
        slug(edition_id, "edition")
        require(edition_id not in edition_ids, f"{edition_id}: duplicate edition")
        edition_ids.add(edition_id)
        work_id = edition["workId"]
        require(work_id in works, f"{edition_id}: unknown work")
        observed[work_id].add(edition_id)
        require(edition["language"] in {"es", "en"}, f"{edition_id}: unsupported language")
        counts[edition["language"]] += 1
        for field in ("title", "author", "description", "editionNote"):
            required_text(edition[field], f"{edition_id} {field}")
        if edition["translator"] is not None:
            required_text(edition["translator"], f"{edition_id} translator")
        for field in ("sectionCount", "illustrationCount", "historicalIllustrations"):
            require(type(edition[field]) is int and edition[field] >= 0,
                    f"{edition_id}: invalid {field}")
        check_credits(edition)
        downloads = check_downloads(releases, edition, assets)
        color = epub_sections(downloads["color"], edition)
        boox = epub_sections(downloads["boox"], edition)
        new, historical = web_book(site, edition, color, boox, image_paths)
        require(historical == (42 if edition_id == HISTORICAL_EDITION else 0),
                f"{edition_id}: unexpected historical image count")
        counts["new"] += new
        counts["historical"] += historical
        sections += len(color)
        print(f"{edition_id}: {len(color)} sections, {new} illustrations, {historical} historical, 2 EPUBs")

    require(works == observed, "library: work and edition references disagree")
    require(counts == {"en": 9, "es": 7, "new": 557, "historical": 42},
            f"library: unexpected totals {dict(counts)}")
    require(tree_files(site / "data") == {"library.json", *(f"books/{i}.json" for i in edition_ids)},
            "site/data: missing or undeclared files")
    require({f"images/{item}" for item in tree_files(site / "images")} == image_paths,
            "site/images: missing or undeclared files")
    has_sums = check_release_checksums(releases, assets)
    release_files = set(assets) | ({"SHA256SUMS.txt"} if has_sums else set())
    require(tree_files(releases) == release_files, "releases: missing or undeclared files")
    require(len(assets) == 32, "releases: expected 32 EPUBs")
    print(
        f"Verified 10 works, 16 editions, {sections} sections, "
        f"557 new illustrations, 42 historical illustrations and 32 release EPUBs. "
        f"SHA256SUMS.txt: {'verified' if has_sums else 'not present'}."
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    repository = Path(__file__).resolve().parents[1]
    parser.add_argument("--site", type=Path, default=repository / "site")
    parser.add_argument("--releases", type=Path, help="Defaults to releases/ beside the site directory.")
    args = parser.parse_args()
    site = args.site.resolve()
    releases = (args.releases or (site.parent / "releases")).resolve()
    try:
        check_library(site, releases)
    except (ValueError, TypeError, AttributeError, KeyError, OSError, ET.ParseError, zipfile.BadZipFile) as error:
        print(f"Library check failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
