import {
  h, icon, loadLibrary, groupByWork, workTitle, languageName, countLabel, getProgress,
  coverElement, bookUrl, illustrationSummary, readUrl, tourUrl, params, showError, siteHeader, siteFooter,
} from './lib.js';

const app = document.getElementById('app');

try {
  const library = await loadLibrary();
  render(library);
} catch (error) {
  showError(app, error);
}

function render(library) {
  const groups = groupByWork(library);
  const languages = [...new Set(library.editions.map((e) => String(e.language || '').split('-')[0]).filter(Boolean))];
  const state = { lang: params().get('lang') || 'all', query: '' };
  if (state.lang !== 'all' && !languages.includes(state.lang)) state.lang = 'all';

  const totalImages = library.editions.reduce((sum, e) => sum + (Number(e.illustrationCount) || 0), 0);
  const shelf = h('ul', { class: 'shelf', id: 'shelf' });
  const empty = h('p', { class: 'empty', hidden: true }, 'No books match this filter.');
  const status = h('p', { class: 'visually-hidden', role: 'status', 'aria-live': 'polite' });

  const chips = h('div', { class: 'chips', role: 'group', 'aria-label': 'Filter by language' },
    ['all', ...languages].map((code) => h('button', {
      class: 'chip', type: 'button', 'data-lang': code, 'aria-pressed': String(state.lang === code),
      onclick: () => { state.lang = code; update(); },
    }, code === 'all' ? 'All languages' : languageName(code))));

  const search = h('input', {
    class: 'search', type: 'search', placeholder: 'Search title or author', 'aria-label': 'Search title or author',
    oninput: (event) => { state.query = event.target.value; update(); },
  });

  app.replaceChildren(
    siteHeader('shelf'),
    h('main', { id: 'main' },
      h('section', { class: 'hero' },
        h('h1', {}, 'Illustrated Library'),
        h('p', {}, 'Public-domain classics with new illustrations placed chapter by chapter. Read in your browser, walk through every picture in an image tour, or download the EPUB for color screens and e-ink readers.'),
        h('p', { class: 'stats' }, [
          countLabel(groups.length, 'work'),
          countLabel(library.editions.length, 'edition'),
          totalImages ? countLabel(totalImages, 'new illustration') : '',
        ].filter(Boolean).join(' · '))),
      h('div', { class: 'toolbar' }, search, languages.length > 1 ? chips : null),
      status, shelf, empty),
    siteFooter());

  function update() {
    for (const chip of chips.querySelectorAll('.chip')) {
      chip.setAttribute('aria-pressed', String(chip.dataset.lang === state.lang));
    }
    const url = new URL(location.href);
    if (state.lang === 'all') url.searchParams.delete('lang'); else url.searchParams.set('lang', state.lang);
    history.replaceState(null, '', url);

    const query = normalise(state.query);
    const cards = [];
    for (const group of groups) {
      const editions = group.editions.filter((edition) => {
        if (state.lang !== 'all' && !String(edition.language).startsWith(state.lang)) return false;
        if (!query) return true;
        const haystack = normalise([workTitle(group), group.work.author, edition.title, edition.author, edition.translator].join(' '));
        return haystack.includes(query);
      });
      if (editions.length) cards.push(workCard(group, editions));
    }
    shelf.replaceChildren(...cards);
    empty.hidden = cards.length > 0;
    status.textContent = `${countLabel(cards.length, 'work')} shown`;
  }

  update();
}

function workCard(group, editions) {
  const lead = editions[0];
  const title = workTitle(group);
  const author = group.work.author || lead.author;
  return h('li', { class: 'work-card' },
    h('a', { class: 'cover-link', href: bookUrl(lead.id), tabindex: '-1', 'aria-hidden': 'true' }, coverElement(lead)),
    h('div', {},
      h('h2', {}, title),
      author ? h('p', { class: 'author' }, author) : null,
      group.work.description ? h('p', { class: 'work-desc' }, group.work.description) : null,
      h('ul', { class: 'editions' }, editions.map(editionRow))));
}

function editionRow(edition) {
  const progress = getProgress(edition.id);
  const meta = [
    countLabel(edition.sectionCount, 'chapter'),
    illustrationSummary(edition),
    edition.translator ? `Translated by ${edition.translator}` : '',
  ].filter(Boolean).join(' · ');
  return h('li', { class: 'edition-row' },
    h('div', { class: 'edition-head' },
      h('span', { class: 'badge' }, languageName(edition.language)),
      h('a', { class: 'edition-title', href: bookUrl(edition.id), lang: edition.language }, edition.title)),
    meta ? h('p', { class: 'edition-meta' }, meta) : null,
    h('div', { class: 'edition-actions' },
      h('a', { class: 'btn btn-primary', href: readUrl(edition.id, progress?.section) },
        icon('book'), progress ? 'Continue' : 'Read'),
      edition.illustrationCount !== 0
        ? h('a', { class: 'btn', href: tourUrl(edition.id) }, icon('images'), 'Image tour')
        : null,
      h('a', { class: 'btn', href: bookUrl(edition.id), 'aria-label': `Details and downloads: ${edition.title}` },
        icon('download'), 'Downloads')),
    progress?.sectionTitle
      ? h('span', { class: 'continue' }, 'Last read: ', h('span', { lang: edition.language }, progress.sectionTitle))
      : null);
}

function normalise(text) {
  return String(text || '').normalize('NFD').replace(/\p{Diacritic}/gu, '').toLowerCase().trim();
}
