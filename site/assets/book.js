import {
  h, icon, loadLibrary, loadEdition, findEdition, findWork, languageName, countLabel, getProgress,
  coverElement, readUrl, tourUrl, bookUrl, params, showError, siteHeader, siteFooter,
  DOWNLOAD_PROFILES, downloadUrl, illustrationSummary, formatBytes, rightsInfo, extraCredits, sectionLabel,
} from './lib.js';

const app = document.getElementById('app');

try {
  const library = await loadLibrary();
  const edition = findEdition(library, params().get('edition'));
  if (!edition) throw new Error('This book is not in the library. It may have moved or the link may be incomplete.');
  render(library, edition);
} catch (error) {
  showError(app, error);
}

function render(library, edition) {
  const work = findWork(library, edition.workId) || {};
  const progress = getProgress(edition.id);
  const siblings = library.editions.filter((e) => e.workId && e.workId === edition.workId && e.id !== edition.id);
  document.title = `${edition.title} · Illustrated Library`;

  const contents = h('section', { 'aria-labelledby': 'contents-h' },
    h('h2', { id: 'contents-h' }, 'Contents'),
    h('p', { class: 'loading', style: 'text-align:left;padding:0' }, 'Loading contents…'));

  app.replaceChildren(
    siteHeader(),
    h('main', { id: 'main', class: 'book' },
      h('div', { class: 'book-cover' }, coverElement(edition, { eager: true })),
      h('div', {},
        h('span', { class: 'badge' }, languageName(edition.language)),
        h('h1', { lang: edition.language }, edition.title),
        h('p', { class: 'byline' },
          edition.author || work.author || '',
          edition.translator ? ` · Translated by ${edition.translator}` : ''),
        edition.description ? h('p', { class: 'description' }, edition.description) : null,
        edition.editionNote ? h('p', { class: 'edition-note' }, edition.editionNote) : null,
        h('p', { class: 'stats' }, [
          countLabel(edition.sectionCount, 'section'),
          illustrationSummary(edition),
        ].filter(Boolean).join(' · ')),
        h('div', { class: 'actions' },
          h('a', { class: 'btn btn-primary', href: readUrl(edition.id, progress?.section) },
            icon('book'), progress ? 'Continue reading' : 'Start reading'),
          edition.illustrationCount !== 0
            ? h('a', { class: 'btn', href: tourUrl(edition.id) }, icon('images'), 'Image tour')
            : null),
        progress?.sectionTitle
          ? h('p', { class: 'stats' }, 'Last read: ', h('span', { lang: edition.language }, progress.sectionTitle))
          : null,

        h('section', { 'aria-labelledby': 'downloads-h' },
          h('h2', { id: 'downloads-h' }, 'Download EPUB'),
          h('div', { class: 'downloads' }, DOWNLOAD_PROFILES.map((profile) => downloadCard(library, edition, profile)))),

        creditsSection(edition, work),

        siblings.length
          ? h('section', { 'aria-labelledby': 'other-h' },
            h('h2', { id: 'other-h' }, 'Other editions'),
            h('div', { class: 'other-editions' }, siblings.map((other) => h('a', { class: 'btn', href: bookUrl(other.id) },
              h('span', { class: 'badge' }, languageName(other.language)),
              h('span', { lang: other.language }, other.title)))))
          : null,

        contents)),
    siteFooter());

  loadEdition(edition).then((data) => renderContents(contents, edition, data)).catch((error) => {
    contents.querySelector('.loading').textContent = `Contents could not be loaded: ${error.message}`;
  });
}

function downloadCard(library, edition, profile) {
  const download = edition.downloads?.[profile.key];
  const url = downloadUrl(library, download);
  const size = formatBytes(download?.bytes ?? download?.size);
  const body = [
    icon('download'),
    h('strong', {}, download?.label || profile.label),
    h('span', {}, profile.note),
    h('small', {}, url ? [size, download?.file].filter(Boolean).join(' · ') : 'Not available for this edition yet'),
  ];
  return url
    ? h('a', { class: 'download', href: url, rel: 'noopener', 'data-profile': profile.key }, body)
    : h('div', { class: 'download unavailable', 'data-profile': profile.key }, body);
}

function creditsSection(edition, work) {
  const rights = rightsInfo(edition);
  const rows = [];
  const add = (term, ...value) => {
    if (value.some((v) => v !== '' && v !== null && v !== undefined)) rows.push(h('dt', {}, term), h('dd', {}, ...value));
  };
  add('Author', edition.author || work.author || '');
  add('Translator', edition.translator || '');
  add('Language', languageName(edition.language));
  add('Text license', rights.license || '');
  if (Number(edition.historicalIllustrations) > 0) {
    add('Historical art', countLabel(Number(edition.historicalIllustrations), 'historical illustration'), ' from the source edition');
  }
  for (const credit of extraCredits(edition)) {
    add(credit.role || 'Credit', credit.url ? externalLink(credit.url, credit.name) : credit.name);
  }

  return [
    h('section', { 'aria-labelledby': 'credits-h' },
      h('h2', { id: 'credits-h' }, 'Credits'),
      rows.length ? h('dl', { class: 'credits' }, rows) : null),
    rights.statements.length
      ? h('section', { 'aria-labelledby': 'rights-h' },
        h('h2', { id: 'rights-h' }, 'Rights'),
        h('dl', { class: 'credits rights' }, rights.statements.flatMap((statement) => [
          h('dt', {}, statement.label), h('dd', { class: 'rights-text' }, statement.text)])))
      : null,
    rights.sources.length
      ? h('section', { 'aria-labelledby': 'sources-h' },
        h('h2', { id: 'sources-h' }, 'Sources'),
        h('ul', { class: 'sources' }, rights.sources.map((source) => h('li', {},
          externalLink(source.url, source.label || hostOf(source.url)),
          h('span', { class: 'host' }, hostOf(source.url))))))
      : null,
  ];
}

function externalLink(url, text) {
  return h('a', { href: url, rel: 'noopener' }, text || url);
}

function hostOf(url) {
  try { return new URL(url).hostname.replace(/^www\./, ''); } catch { return url; }
}

function renderContents(container, edition, data) {
  const items = data.sections.map((section, index) => {
    const pictures = section.blocks.filter((b) => b.type === 'illustration').length;
    return h('li', {},
      h('a', { href: readUrl(edition.id, section.id), lang: edition.language }, sectionLabel(section, index)),
      pictures ? h('span', { class: 'count' }, countLabel(pictures, 'image')) : null);
  });
  const list = h('ol', { class: 'toc-list' }, items);
  const body = items.length > 30
    ? h('details', { class: 'contents' }, h('summary', {}, `Show all ${items.length} sections`), list)
    : list;
  container.replaceChildren(container.firstChild, body);
}
