# Provenance and licensing

## Source code

The snapshot contains the Book Illustrator production source, its catalog recipes, and local tests. A project source-code license has yet to be confirmed by the rights holder. The public-domain status of an input work and the terms of upstream packages do not grant a license to Book Illustrator source code.

## Edition sources

The [catalog](demo-publica/catalogo.json) lists 16 editions covering ten works. It records a repository-relative path, a source SHA-256, an edition ID, a work ID, and a manifest link for each edition. Follow `source_manifest` from the catalog's directory. A work may have a different edition in each language. Translations have their own source evidence and their own scene cuts.

| Recipe and manifest | Command from the repository root |
|---|---|
| [English originals and the Caballero translation](demo-publica/en/manifest.json) | `python -B demo-publica/en/reconstruir.py` |
| [Spanish originals and historical English translations](demo-publica/es/manifest.json) | `python -B demo-publica/es/preparar.py` |
| [Additional Spanish translations](demo-publica/traducciones/manifest.json) | `python -B demo-publica/traducciones/preparar.py` |

Each recipe can prepare a named edition and accepts a directory of previously fetched sources. Use `--help` for the exact flags. The recipes check the reconstructed EPUB against its recorded size and hash. The English-source recipe was verified with zlib 1.2.12. If an upstream file or a recipe's ZIP compression changes, the hash check fails instead of admitting a different edition.

The catalog's rights review covers Chile and the United States. The individual manifests record the historical text, translations, notes, artwork, digital sources, and components omitted from each derivative. Recheck the intended territories and the exact files you plan to distribute. [Chile's Law 17.336](https://www.bcn.cl/leychile/navegar?idNorma=28933) · [U.S. Copyright Office, duration of copyright](https://www.copyright.gov/circs/circ15a.pdf)

Modern editorial contributions retain their own terms. The Standard Ebooks contributions in the included recipes use CC0. The Wikisource transcriptions used for *Sub terra* and *El cántico de Navidad* retain CC BY-SA 3.0 and 4.0 respectively, with attribution and source details in their manifests. The 42 Tenniel illustrations in the English *Alice’s Adventures in Wonderland* are historical assets, distinct from any new illustrations the program adds. [Standard Ebooks dedication](https://standardebooks.org/about/standard-ebooks-and-the-public-domain) · [Sub terra manifest](demo-publica/es/manifest.json) · [El cántico de Navidad manifest](demo-publica/traducciones/manifest.json)

## New illustrations and third-party tools

The locally drawn test fixtures and the provider-generated illustrations have different provenance. Historical text and transcription terms do not establish the license of a newly generated image. Keep the generation receipt and check the provider terms and your distribution rights before bundling the new illustrations. The source snapshot carries the code that creates these files.

The dependencies keep their upstream licenses. The project loads them from the packages installed in your environment. EPUBCheck runs from a separately downloaded JAR. [Beautiful Soup](https://pypi.org/project/beautifulsoup4/) · [lxml](https://lxml.de/) · [OpenAI Python SDK](https://github.com/openai/openai-python) · [Pillow](https://github.com/python-pillow/Pillow) · [EPUBCheck](https://github.com/w3c/epubcheck)
