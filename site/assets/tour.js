import {
  h, icon, loadLibrary, loadEdition, findEdition, collectIllustrations, readUrl, tourUrl, bookUrl,
  params, showError, isTyping, isLineArt,
} from './lib.js';

const app = document.getElementById('app');

try {
  const library = await loadLibrary();
  const edition = findEdition(library, params().get('edition'));
  if (!edition) throw new Error('This book is not in the library. It may have moved or the link may be incomplete.');
  app.replaceChildren(h('p', { class: 'loading' }, `Loading illustrations for ${edition.title}…`));
  const data = await loadEdition(edition);
  const items = collectIllustrations(data);
  if (!items.length) {
    showError(app, new Error('This edition has no illustrations yet.'),
      h('p', {}, h('a', { href: readUrl(edition.id) }, 'Read the book'), ' · ', h('a', { href: './' }, 'Back to the library')));
  } else {
    startTour(edition, items);
  }
} catch (error) {
  showError(app, error);
}

function startTour(edition, items) {
  let current = -1;

  const counter = h('span', { class: 'counter', 'aria-live': 'polite' });
  const image = h('img', { alt: '', decoding: 'async' });
  const prevBtn = h('button', { class: 'btn nav prev', type: 'button', 'aria-label': 'Previous illustration', onclick: () => go(current - 1) }, icon('left'));
  const nextBtn = h('button', { class: 'btn nav next', type: 'button', 'aria-label': 'Next illustration', onclick: () => go(current + 1) }, icon('right'));
  const stage = h('div', { class: 'stage' }, image, prevBtn, nextBtn);

  const kicker = h('p', { class: 'kicker' });
  const title = h('h1', { lang: edition.language });
  const description = h('p', { class: 'description', lang: edition.language });
  const excerpt = h('blockquote', { lang: edition.language });
  const readLink = h('a', { class: 'btn btn-primary' }, icon('book'), 'Read this passage');
  const caption = h('section', { class: 'caption', 'aria-live': 'polite' },
    kicker, title, description, excerpt,
    h('div', { class: 'links' }, readLink),
    h('p', { class: 'hint' }, h('kbd', {}, '←'), ' ', h('kbd', {}, '→'), ' move · ', h('kbd', {}, 'Home'), ' ',
      h('kbd', {}, 'End'), ' jump · ', h('kbd', {}, 'F'), ' full screen · ', h('kbd', {}, 'Esc'), ' leave'));

  let lastSection = null;
  const thumbs = items.map((item, index) => {
    const startsChapter = item.sectionIndex !== lastSection && index > 0;
    lastSection = item.sectionIndex;
    return h('button', {
      type: 'button', class: startsChapter ? 'chapter-break' : null,
      'aria-label': `${index + 1}. ${item.title || item.alt || 'Illustration'}`,
      title: `${item.title || ''} — ${item.sectionTitle}`,
      onclick: () => go(index),
    }, h('img', { src: item.thumb || item.src, alt: '', loading: 'lazy', decoding: 'async', class: isLineArt(item) ? 'line-art' : null }));
  });
  const strip = h('nav', { class: 'filmstrip', 'aria-label': 'All illustrations' }, thumbs);

  const fullBtn = document.fullscreenEnabled
    ? h('button', { class: 'btn', type: 'button', onclick: toggleFullscreen, 'aria-label': 'Full screen' }, icon('expand'))
    : null;

  const bar = h('header', { class: 'tourbar' },
    h('a', { class: 'btn', href: bookUrl(edition.id), 'aria-label': `Back to ${edition.title}` }, icon('left'),
      h('span', { class: 'label-wide' }, 'Book')),
    h('div', { class: 'title' }, h('span', { lang: edition.language }, edition.title), h('small', { class: 'label-wide' }, 'Image tour')),
    counter,
    fullBtn,
    h('a', { class: 'btn', href: './', 'aria-label': 'Bookshelf' }, icon('home')));

  const main = h('main', { class: 'tour', id: 'main' }, bar, stage, caption, strip);
  app.replaceChildren(main);
  document.title = `Image tour · ${edition.title}`;

  // The URL always names the current image (shareable), but the tour does not add a
  // history entry per image, so Back leaves the tour.
  function go(index) {
    if (index < 0 || index >= items.length || index === current) return;
    current = index;
    const item = items[index];

    image.classList.add('loading');
    image.onload = () => image.classList.remove('loading');
    image.src = item.src;
    image.classList.toggle('line-art', isLineArt(item));
    image.alt = item.alt || item.title || 'Illustration';

    counter.textContent = `${index + 1} / ${items.length}`;
    kicker.replaceChildren('From ', h('span', { lang: edition.language }, item.sectionTitle));
    title.replaceChildren(item.title || 'Untitled illustration',
      item.historical ? h('span', { class: 'tag-historical', title: 'Illustration from the historical source edition' }, 'Historical') : '');
    description.textContent = item.description || '';
    description.hidden = !item.description;
    excerpt.replaceChildren(...(item.excerpt
      ? [item.excerpt, h('cite', { lang: 'en' }, 'Text just before this illustration')]
      : []));
    excerpt.hidden = !item.excerpt;
    readLink.href = readUrl(edition.id, item.sectionId, item.id);

    prevBtn.disabled = index === 0;
    nextBtn.disabled = index === items.length - 1;

    thumbs.forEach((thumb, i) => thumb.setAttribute('aria-current', String(i === index)));
    thumbs[index].scrollIntoView({ block: 'nearest', inline: 'center', behavior: 'smooth' });

    history.replaceState(null, '', tourUrl(edition.id, item.id));

    for (const neighbour of [items[index + 1], items[index - 1]]) {
      if (neighbour) new Image().src = neighbour.src;
    }
  }

  function toggleFullscreen() {
    if (document.fullscreenElement) document.exitFullscreen();
    else document.documentElement.requestFullscreen?.().catch(() => {});
  }

  addEventListener('keydown', (event) => {
    if (event.altKey || event.ctrlKey || event.metaKey || isTyping(event)) return;
    const keys = {
      ArrowRight: () => go(current + 1), ArrowDown: () => go(current + 1), PageDown: () => go(current + 1), ' ': () => go(current + 1),
      ArrowLeft: () => go(current - 1), ArrowUp: () => go(current - 1), PageUp: () => go(current - 1),
      Home: () => go(0), End: () => go(items.length - 1),
      f: toggleFullscreen, F: toggleFullscreen,
      Escape: () => { if (!document.fullscreenElement) location.href = bookUrl(edition.id); },
    };
    // Let Space and arrows act on focused buttons/links normally only for activation keys.
    if (event.key === ' ' && event.target instanceof HTMLElement && event.target.matches('button, a')) return;
    if (keys[event.key]) { event.preventDefault(); keys[event.key](); }
  });

  // Swipe on touch screens.
  let touch = null;
  stage.addEventListener('pointerdown', (event) => {
    if (event.pointerType !== 'mouse') touch = { x: event.clientX, y: event.clientY };
  });
  stage.addEventListener('pointerup', (event) => {
    if (!touch) return;
    const dx = event.clientX - touch.x;
    const dy = event.clientY - touch.y;
    touch = null;
    if (Math.abs(dx) > 45 && Math.abs(dx) > Math.abs(dy) * 1.3) go(current + (dx < 0 ? 1 : -1));
  });
  stage.addEventListener('pointercancel', () => { touch = null; });

  function indexFromUrl() {
    const wanted = params().get('image');
    const found = wanted ? items.findIndex((item) => String(item.id) === wanted) : -1;
    return Math.max(0, found);
  }

  go(indexFromUrl());
}
