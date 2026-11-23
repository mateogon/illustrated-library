"""Rebuild additional pinned Spanish translations in the catalog."""

import argparse
import json
from hashlib import sha256
from html import escape
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import Request, urlopen
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

from lxml import etree, html


XHTML = "http://www.w3.org/1999/xhtml"
EPUB = "http://www.idpf.org/2007/ops"
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
ALICIA_SOURCE = "https://www.textos.info/lewis-carroll/alicia-en-el-pais-de-las-maravillas"
DICKENS_SOURCE = "https://es.wikisource.org/wiki/El_c%C3%A1ntico_de_Navidad"
CC_BY_SA = "https://creativecommons.org/licenses/by-sa/4.0/"
SURNAMES = ["Primera", "Segunda", "Tercera", "Cuarta", "Quinta"]
SCOPE = "Texto histórico en el patrimonio cultural común en Chile y en el dominio público en Estados Unidos."


def xhtml(title, body):
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<html xmlns="{XHTML}" xmlns:epub="{EPUB}" lang="es" xml:lang="es">'
        f'<head><title>{escape(title)}</title></head><body>{body}</body></html>\n'
    )


def paragraph_link(label, url):
    return f'<p><a href="{escape(url)}">{escape(label)}</a></p>'


def role(contributor_id, role_code):
    return f'<meta refines="#{contributor_id}" property="role" scheme="marc:relators">{role_code}</meta>'


def build(path, book_id, title, author, translator, source, rights, credits, sections):
    names = [f"texto-{i:02d}.xhtml" for i in range(1, len(sections) + 1)]
    links = "".join(
        f'<li><a href="{name}">{escape(heading)}</a></li>'
        for name, (heading, _) in zip(names, sections, strict=True)
    )
    nav = xhtml(
        "Índice",
        f'<nav epub:type="toc" id="indice"><h1>Índice</h1><ol>'
        f'{links}<li><a href="creditos.xhtml">Créditos y procedencia</a></li></ol></nav>',
    )
    content_items = "".join(
        f'<item id="texto-{i}" href="{name}" media-type="application/xhtml+xml" />'
        for i, name in enumerate(names, 1)
    )
    refs = "".join(f'<itemref idref="texto-{i}" />' for i in range(1, len(sections) + 1))
    title_page = xhtml(
        title,
        f'<section epub:type="titlepage"><h1>{escape(title)}</h1><p>{escape(author)}</p>'
        f'<p>Traducción de {escape(translator)}</p></section>',
    )
    package = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="edicion" version="3.0" xml:lang="es">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="edicion">urn:book-illustrator:{book_id}</dc:identifier>
    <dc:title>{escape(title)}</dc:title>
    <dc:language>es</dc:language>
    <dc:creator id="autor">{escape(author)}</dc:creator>{role("autor", "aut")}
    <dc:contributor id="traductor">{escape(translator)}</dc:contributor>{role("traductor", "trl")}
    <dc:rights>{escape(rights)}</dc:rights>
    <dc:source>{escape(source)}</dc:source>
    <meta property="dcterms:modified">2026-11-21T00:00:00Z</meta>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav" />
    <item id="titulo" href="titulo.xhtml" media-type="application/xhtml+xml" />
    {content_items}
    <item id="creditos" href="creditos.xhtml" media-type="application/xhtml+xml" />
  </manifest>
  <spine><itemref idref="titulo" />{refs}<itemref idref="creditos" /></spine>
</package>
"""
    container = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="EPUB/package.opf" media-type="application/oebps-package+xml" /></rootfiles>
</container>
"""
    pages = {
        "META-INF/container.xml": container,
        "EPUB/package.opf": package,
        "EPUB/nav.xhtml": nav,
        "EPUB/titulo.xhtml": title_page,
    }
    for name, (heading, content) in zip(names, sections, strict=True):
        pages["EPUB/" + name] = xhtml(heading, content)
    pages["EPUB/creditos.xhtml"] = xhtml(
        "Créditos y procedencia",
        f'<section epub:type="colophon"><h1>Créditos y procedencia</h1>{credits}</section>',
    )
    with ZipFile(path, "w") as book:
        book.writestr(ZipInfo("mimetype"), b"application/epub+zip", compress_type=ZIP_STORED)
        for name, content in pages.items():
            book.writestr(ZipInfo(name), content.encode("utf-8"), compress_type=ZIP_DEFLATED)
    with ZipFile(path) as book:
        assert book.testzip() is None
        for name in pages:
            etree.fromstring(book.read(name))
        toc = etree.fromstring(book.read("EPUB/nav.xhtml"))
        assert all("EPUB/" + href in book.namelist() for href in toc.xpath("//@href"))


def body_html(element):
    return "".join(etree.tostring(child, encoding="unicode", with_tail=True) for child in element)


def read_source(url, name, source_dir):
    if source_dir is not None:
        return (source_dir / name).read_bytes()
    request = Request(url, headers={"User-Agent": "BookIllustratorDemo/1.0 (bibliographic reproduction)"})
    with urlopen(request, timeout=45) as response:
        return response.read()


def alicia(path, edition, source_dir):
    source_epub = read_source(edition["urls"]["digital_epub"], "alicia.epub", source_dir)
    if sha256(source_epub).hexdigest() != edition["verification"]["digital_source_sha256"]:
        raise ValueError("La huella del EPUB fuente de Alicia no coincide con el manifiesto")
    sections = []
    with ZipFile(BytesIO(source_epub)) as source:
        for number in range(1, 14):
            document = etree.fromstring(source.read(f"OEBPS/{number:04d}.xhtml"))
            body = document.find("{*}body")
            heading = body.find("{*}h2").text
            expected_heading = "Prefacio" if number == 1 else f"{number - 1}. "
            assert heading.startswith(expected_heading)
            assert {el.tag.rsplit("}", 1)[-1] for el in body.iter()} <= {"body", "h2", "p", "em", "br"}
            for node in body.iter():
                node.tag = node.tag.rsplit("}", 1)[-1]
                node.attrib.clear()
            etree.cleanup_namespaces(body)
            sections.append((heading, body_html(body)))
    assert "pueblo sumiso" in sections[3][1]
    assert "días felices de las vacaciones estivales" in sections[-1][1]
    credits = (
        "<p>Lewis Carroll (Charles Lutwidge Dodgson, 1832–1898). "
        "Traducción de Juan Gutiérrez Gili (1894–1939), publicada por Editorial Mentora "
        "en Barcelona en octubre de 1927.</p>"
        "<p>EPUB de texto basado en la edición digital de Edu Robsy para textos.info "
        "(publicada en 2016, actualizada en 2022). Conserva sus variantes de transcripción. "
        "Se regeneraron la portada de texto, los metadatos y la navegación. "
        "Se excluyeron la cubierta, el retrato, el perfil biográfico del sitio y sus materiales editoriales. "
        "Este archivo tampoco contiene las ilustraciones y viñetas de Lola Anglada de la edición impresa.</p>"
        f"<p>{SCOPE}</p>"
        + paragraph_link("Fuente digital y crédito editorial", ALICIA_SOURCE)
        + paragraph_link("Referencia bibliográfica y estudio de la traducción",
                         "https://cvc.cervantes.es/trujaman/anteriores/abril_15/01042015.htm")
    )
    build(path, edition["id"], edition["title"], "Lewis Carroll", "Juan Gutiérrez Gili",
          ALICIA_SOURCE, SCOPE, credits, sections)


def navidad(path, edition, source_dir):
    labels = [
        "Primera estrofa", "Segunda estrofa. El primero de los tres espíritus",
        "Tercera estrofa. El segundo de los tres espíritus",
        "Cuarta estrofa. El último de los espíritus", "Quinta estrofa. Conclusión",
    ]
    sections = []
    revisions = edition["urls"]["digital_source_pages"]
    for number, (heading, url) in enumerate(zip(labels, revisions, strict=True), 1):
        source = read_source(url, f"navidad-{number}.html", source_dir)
        doc = html.fromstring(source)
        permalink, = doc.xpath('//*[@id="t-permalink"]/a/@href')
        if "https://es.wikisource.org" + permalink != url:
            raise ValueError(f"La estrofa {number} no corresponde a la revisión esperada de Wikisource")
        block, = doc.xpath('//div[contains(concat(" ", normalize-space(@class), " "), " prp-pages-output ")]')
        duplicate_heading, = block.xpath('./div[@class="ws-div"][1]')
        duplicate_heading.drop_tree()
        for node in block.xpath('.//style | .//link | .//script | .//span[contains(@class,"pagenum")]'):
            node.drop_tree()
        notes = []
        for ref_index, ref in enumerate(block.xpath('.//sup[contains(@class,"mw-ref")]'), 1):
            link, = ref.xpath('./a')
            note_key = link.get("href").removeprefix("#")
            note, = doc.xpath(f'//*[@id="mw-reference-text-{note_key}"]')
            assert note.text_content().strip()
            ref.attrib.clear()
            ref.set("id", f"llamada-{ref_index}")
            link.attrib.clear()
            link.set("href", f"#nota-{ref_index}")
            link.set(f"{{{EPUB}}}type", "noteref")
            link.text = str(ref_index)
            for child in list(link):
                link.remove(child)
            note.attrib.clear()
            for italic in note.xpath('.//i'):
                italic.tag = "em"
                italic.attrib.clear()
            notes.append(
                f'<li id="nota-{ref_index}">{escape(note.text or "")}{body_html(note)} '
                f'<a href="#llamada-{ref_index}" aria-label="Volver a la llamada {ref_index}">↩</a></li>'
            )
        assert len(notes) == [4, 0, 1, 1, 0][number - 1]
        for span in block.xpath('.//span'):
            span.drop_tag()
        for italic in block.xpath('.//i'):
            italic.tag = "em"
        for node in list(block):
            if node.tag in {"div", "p"} and not node.text_content().strip():
                node.drop_tree()
        for node in block.iter():
            if node.tag not in {"a", "sup"}:
                node.attrib.clear()
        assert {el.tag for el in block.iter()} <= {"div", "p", "em", "br", "sup", "a"}
        assert next(iter(block)).text_content().strip().startswith(
            ["Para empezar", "Cuando Scrooge", "Se despertó", "Cuando llegó", "Y era una columna"][number - 1]
        )
        if number == 5:
            assert "Dios nos bendiga" in block.text_content()
        paragraphs = len(block.xpath('.//p'))
        assert paragraphs >= [175, 150, 145, 160, 75][number - 1]
        content = f'<section epub:type="chapter"><h1>{escape(heading)}</h1>{body_html(block)}</section>'
        if notes:
            content += '<aside epub:type="footnotes"><h2>Notas de la edición de 1883</h2><ol>'
            content += "".join(notes) + "</ol></aside>"
        sections.append((heading, content))
    rights = SCOPE + " Aportes de la transcripción digital bajo CC BY-SA 4.0."
    credits = (
        "<p>Charles Dickens (1812–1870). Traducción de Luis Barthe (1857–1921). "
        "Madrid, Imprenta y Litografía de la Biblioteca Universal, 1883, tomo LXXXIX.</p>"
        "<p>Contiene las cinco estrofas del relato y las seis notas que las acompañan en el impreso. "
        "El volumen contenía además una biografía y otro relato, «La fortuna de un estudiante». "
        "El EPUB se dedica a El cántico de Navidad y conserva la ortografía de la transcripción.</p>"
        f"<p>{rights} Se adaptó el formato al EPUB y se enlazaron las notas.</p>"
        + paragraph_link("Transcripción y colaboradores de Wikisource", DICKENS_SOURCE)
        + paragraph_link("Licencia Creative Commons Atribución-CompartirIgual 4.0", CC_BY_SA)
        + paragraph_link("Registro y facsímil de la Universidad de Alicante",
                         "https://www.cervantesvirtual.com/obra/el-cantico-de-navidad/")
        + "".join(paragraph_link(f"{label} · registro de revisión", link)
                  for label, link in zip(SURNAMES, revisions, strict=True))
    )
    build(path, edition["id"], edition["title"], "Charles Dickens", "Luis Barthe",
          DICKENS_SOURCE, rights, credits, sections)


def main():
    builders = {
        "alicia-gutierrez-gili-1927-es": alicia,
        "el-cantico-de-navidad-barthe-1883-es": navidad,
    }
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("id", nargs="?", choices=builders)
    parser.add_argument("--source-dir", type=Path, help="Read existing source files from this directory.")
    parser.add_argument("--output-dir", type=Path, help="Write reconstructed EPUBs to this directory for verification.")
    args = parser.parse_args()
    catalog = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
    for edition in catalog["editions"]:
        edition_id = edition["id"]
        if args.id and args.id != edition_id:
            continue
        destination = ROOT / edition["path"]
        if args.output_dir:
            destination = args.output_dir / destination.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=destination.parent, prefix=".preparar-traducciones-") as temporary:
            candidate = Path(temporary) / destination.name
            builders[edition_id](candidate, edition, args.source_dir)
            content = candidate.read_bytes()
            digest = sha256(content).hexdigest()
            if len(content) != edition["bytes"] or digest != edition["sha256"]:
                raise ValueError(
                    f"{edition_id}: se generaron {len(content)} bytes y SHA-256 {digest}. "
                    f"El manifiesto espera {edition['bytes']} bytes y SHA-256 {edition['sha256']}."
                )
            candidate.replace(destination)
        print(f"{digest}  {destination}")


if __name__ == "__main__":
    main()
