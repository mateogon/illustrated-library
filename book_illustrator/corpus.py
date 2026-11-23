"""EPUB spine and narrative corpus utilities."""

from pathlib import PurePosixPath
from xml.etree import ElementTree as ET

from book_illustrator.pacing import paginate
from book_illustrator.runtime import ROOT as ROOT

WORDS_PER_PAGE = 300


def epub_package(book):
    container = ET.fromstring(book.read("META-INF/container.xml"))
    path = container.find(".//{*}rootfile").attrib["full-path"]
    package = ET.fromstring(book.read(path))
    folder = PurePosixPath(path).parent
    manifest = {
        node.attrib["id"]: str(folder / node.attrib["href"])
        for node in package.findall("./{*}manifest/{*}item")
    }
    spine = [
        manifest[node.attrib["idref"]]
        for node in package.findall("./{*}spine/{*}itemref")
    ]
    return package, spine


def page_map(rows):
    return {
        pid: page
        for page, ids in enumerate(paginate(rows, target=WORDS_PER_PAGE), 1)
        for pid in ids
    }


def corpus(title, source, source_hash, rows, intervals, *, exclusions, **metadata):
    ids = [row["id"] for row in rows]
    if not ids or ids != sorted(set(ids)) or any(not row["text"] for row in rows):
        raise ValueError(
            f"El corpus {title} debe tener unidades ordenadas y no vacías."
        )
    if [start for _, start, _ in intervals] != [0] + [
        end for _, _, end in intervals[:-1]
    ]:
        raise ValueError(f"Los segmentos de {title} deben ser contiguos.")
    if intervals[-1][2] != len(rows) or any(
        start >= end for _, start, end in intervals
    ):
        raise ValueError(
            f"Los segmentos de {title} deben cubrir la narración completa."
        )
    pages = page_map(rows)
    chapters = {}
    for row in rows:
        row["page"] = pages[row["id"]]
        chapter = chapters.setdefault(
            row["chapter"],
            dict(id=row["chapter"], source_xhtml=row["source_xhtml"], units=0, words=0),
        )
        chapter["units"] += 1
        chapter["words"] += len(row["text"].split())

    return dict(
        title=title,
        source=source.relative_to(ROOT).as_posix(),
        source_sha256=source_hash,
        narrative_words=sum(chapter["words"] for chapter in chapters.values()),
        units=len(rows),
        page_count=max(pages.values()),
        words_per_page=WORDS_PER_PAGE,
        chapters=list(chapters.values()),
        exclusions=exclusions,
        segments=[
            dict(
                id=name,
                start=rows[start]["id"],
                end=rows[end - 1]["id"],
                units=end - start,
                words=sum(len(row["text"].split()) for row in rows[start:end]),
                first_page=rows[start]["page"],
                last_page=rows[end - 1]["page"],
            )
            for name, start, end in intervals
        ],
        paragraphs=rows,
        **metadata,
    )
