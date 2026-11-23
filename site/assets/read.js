import {
  h, icon, loadLibrary, loadEdition, findEdition, sectionLabel, countLabel, getProgress, setProgress,
  getPref, setPref, isLineArt, readUrl, tourUrl, bookUrl, params, showError, isTyping,
} from './lib.js';

const app = document.getElementById('app');
const TEXT_SIZES = [0.95, 1.05, 1.15, 1.28, 1.42, 1.6];

try {
  const library = await loadLibrary();
  const edition = findEdition(library, params().get('edition'));
  if (!edition) throw new Error('This book is not in the library. It may have moved or the link may be incomplete.');
  app.replaceChildren(h('p', { class: 'loading' }, `Loading ${edition.title}…`));
  const data = await loadEdition(edition);
  if (!data.sections.length) throw new Error('This edition has no sections yet.');
  startReader(edition, data);
} catch (error) {
  showError(app, error);
}

function startReader(edition, data) {
  const sections = data.sections;
  let current = -1;
  let sizeIndex = clamp(getPref('textSize', 2), 0, TEXT_SIZES.length - 1);

  const progressBar = h('div', { class: 'progress', 'aria-hidden': 'true' });
  const chapterLine = h('small');
  const tocButton = h('button', { class: 'btn btn-quiet', type: 'button', 'aria-haspopup': 'dialog', onclick: openToc },
    icon('list'), h('span', { class: 'label-wide' }, 'Contents'));
  const smaller = h('button', { class: 'btn btn-quiet', type: 'button', 'aria-label': 'Smaller text', onclick: () => resize(-1) }, 'A−');
  const larger = h('button', { class: 'btn btn-quiet', type: 'button', 'aria-label': 'Larger text', onclick: () => resize(1) }, 'A+');

  const bar = h('header', { class: 'readbar' },
    h('a', { class: 'btn btn-quiet', href: './', 'aria-label': 'Bookshelf' }, icon('home')),
    tocButton,
    h('a', { class: 'title', href: bookUrl(edition.id), title: 'Book details, credits and downloads' },
      h('span', { lang: edition.language }, edition.title), chapterLine),
    h('div', { class: 'group' }, smaller, larger),
    edition.illustrationCount !== 0
      ? h('a', { class: 'btn btn-quiet', href: tourUrl(edition.id), id: 'tour-link' },
        icon('images'), h('span', { class: 'label-wide' }, 'Image tour'))
      : null,
    progressBar);

  const article = h('article', { class: 'reader', id: 'main', lang: edition.language, tabindex: '-1' });
  const nav = h('nav', { class: 'chapter-nav', 'aria-label': 'Chapter navigation' });
  const end = h('p', { class: 'reader-end' });

  const tocList = h('ol', {}, sections.map((section, index) => {
    const pictures = section.blocks.filter((b) => b.type === 'illustration').length;
    return h('li', {},
      h('a', {
        href: readUrl(edition.id, section.id), 'data-index': index,
        onclick: (event) => { event.preventDefault(); toc.close(); go(index); },
      },
      h('span', { class: 'num' }, index + 1),
      h('span', { class: 'label', lang: edition.language }, sectionLabel(section, index)),
      pictures ? h('span', { class: 'pics', 'aria-label': countLabel(pictures, 'illustration') }, `▣ ${pictures}`) : null));
  }));
  const toc = h('dialog', { class: 'drawer', 'aria-labelledby': 'toc-title' },
    h('div', { class: 'drawer-head' },
      h('h2', { id: 'toc-title' }, 'Contents'),
      h('button', { class: 'btn btn-quiet', type: 'button', 'aria-label': 'Close contents', onclick: () => toc.close() }, icon('close'))),
    tocList);
  toc.addEventListener('click', (event) => { if (event.target === toc) toc.close(); });

  const lightboxImg = h('img', { alt: '' });
  const lightboxCaption = h('p');
  const lightboxTour = h('a', { class: 'btn' }, icon('images'), 'Open in image tour');
  const lightbox = h('dialog', { class: 'lightbox', 'aria-label': 'Illustration' },
    h('div', { class: 'lb-bar' }, lightboxTour,
      h('button', { class: 'btn', type: 'button', 'aria-label': 'Close', onclick: () => lightbox.close() }, icon('close'))),
    lightboxImg, lightboxCaption);
  lightbox.addEventListener('click', (event) => { if (event.target === lightbox || event.target === lightboxImg) lightbox.close(); });

  app.replaceChildren(bar, article, nav, end, toc, lightbox);
  applySize();

  function openToc() {
    toc.showModal();
    const active = tocList.querySelector('[aria-current="page"]');
    active?.scrollIntoView({ block: 'center' });
    active?.focus();
  }

  function resize(step) {
    sizeIndex = clamp(sizeIndex + step, 0, TEXT_SIZES.length - 1);
    setPref('textSize', sizeIndex);
    applySize();
  }
  function applySize() {
    article.style.setProperty('--reader-size', `${TEXT_SIZES[sizeIndex]}rem`);
    smaller.disabled = sizeIndex === 0;
    larger.disabled = sizeIndex === TEXT_SIZES.length - 1;
  }

  // Accepts "#ill-<id>" (our figure ids) and a bare "#<id>" (the illustration id itself).
  function illustrationFromHash() {
    if (!location.hash) return null;
    let raw;
    try { raw = decodeURIComponent(location.hash.slice(1)); } catch { return null; }
    const candidates = raw.startsWith('ill-') ? [raw.slice(4), raw] : [raw];
    for (const id of candidates) {
      const index = sections.findIndex((s) => s.blocks.some((b) => b.type === 'illustration' && String(b.id) === id));
      if (index > -1) return { id, index };
    }
    return null;
  }

  function indexFromUrl() {
    const wanted = params().get('section');
    const fromHash = illustrationFromHash();
    if (wanted !== null) {
      const found = sections.findIndex((s) => String(s.id) === wanted);
      const holdsHash = fromHash && sections[found]?.blocks.some((b) => b.type === 'illustration' && String(b.id) === fromHash.id);
      if (found > -1 && (!fromHash || holdsHash)) return { index: found, explicit: true };
    }
    if (fromHash) return { index: fromHash.index, explicit: true };
    const saved = getProgress(edition.id);
    const savedIndex = saved ? sections.findIndex((s) => String(s.id) === String(saved.section)) : -1;
    return { index: Math.max(0, savedIndex), explicit: false, scroll: savedIndex > -1 ? saved.scroll : 0 };
  }

  function go(index, { push = true } = {}) {
    if (index < 0 || index >= sections.length) return;
    show(index);
    if (push) history.pushState({ index }, '', readUrl(edition.id, sections[index].id));
    window.scrollTo(0, 0);
    article.focus({ preventScroll: true });
    save();
  }

  function show(index) {
    current = index;
    const section = sections[index];
    const label = sectionLabel(section, index);
    document.title = `${label} · ${edition.title}`;
    chapterLine.textContent = `${index + 1} / ${sections.length} · ${label}`;
    chapterLine.lang = edition.language;

    const body = h('div', { class: 'section-body' });
    let figureCount = 0;
    for (const block of section.blocks) {
      if (block.type === 'html') {
        const tpl = document.createElement('template');
        tpl.innerHTML = block.html || '';
        sanitise(tpl.content);
        body.append(tpl.content);
      } else if (block.type === 'illustration') {
        figureCount += 1;
        body.append(figure(block));
      }
    }

    const kicker = h('p', { class: 'kicker' }, `${index + 1} of ${sections.length}`,
      figureCount ? ` · ${countLabel(figureCount, 'illustration')}` : '');
    const own = openingHeading(body);
    own?.classList.add('section-title');
    const subtitle = own?.nextElementSibling;
    if (subtitle?.tagName === 'P' && subtitle.textContent.trim().length < 120) subtitle.classList.add('section-subtitle');
    const heading = own ? null : h('h1', { class: 'section-title' }, label);
    article.replaceChildren(...[kicker, heading, body].filter(Boolean));

    const prev = sections[index - 1];
    const next = sections[index + 1];
    nav.replaceChildren(...[
      prev ? h('a', { class: 'prev', href: readUrl(edition.id, prev.id), rel: 'prev', onclick: (e) => { e.preventDefault(); go(index - 1); } },
        h('small', {}, '← Previous'), h('span', { lang: edition.language }, sectionLabel(prev, index - 1))) : null,
      next ? h('a', { class: 'next', href: readUrl(edition.id, next.id), rel: 'next', onclick: (e) => { e.preventDefault(); go(index + 1); } },
        h('small', {}, 'Next →'), h('span', { lang: edition.language }, sectionLabel(next, index + 1))) : null].filter(Boolean));
    end.replaceChildren(...(next ? [] : ['The end. ', h('a', { href: bookUrl(edition.id) }, 'Credits and downloads'), ' · ', h('a', { href: './' }, 'Back to the bookshelf')]));

    for (const link of tocList.querySelectorAll('a')) {
      if (Number(link.dataset.index) === index) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    }
    updateProgressBar();
  }

  function figure(block) {
    const img = h('img', {
      src: block.src, alt: block.alt || block.title || 'Illustration', loading: 'lazy', decoding: 'async',
      width: block.width || (isLineArt(block) ? null : 1200), height: block.height || (isLineArt(block) ? null : 800),
    });
    return h('figure', { class: isLineArt(block) ? 'illustration line-art' : 'illustration', id: `ill-${block.id}` },
      h('button', {
        type: 'button', 'aria-label': `Enlarge illustration: ${block.title || block.alt || ''}`,
        onclick: () => openLightbox(block),
      }, img),
      (block.title || block.description)
        ? h('figcaption', { lang: edition.language },
          block.title ? h('strong', {}, block.title, block.historical ? h('span', { class: 'tag-historical' }, 'Historical') : null) : null,
          block.description || '')
        : null);
  }

  function openLightbox(block) {
    lightboxImg.src = block.src;
    lightboxImg.classList.toggle('line-art', isLineArt(block));
    lightboxImg.alt = block.alt || block.title || '';
    lightboxCaption.textContent = [block.title, block.description].filter(Boolean).join(' — ');
    lightboxTour.href = tourUrl(edition.id, block.id);
    lightbox.showModal();
  }

  // Remove scripts, inline handlers and links that pointed at other files of the source EPUB.
  function sanitise(fragment) {
    fragment.querySelectorAll('script, style, link, meta, iframe, object, embed').forEach((el) => el.remove());
    // Empty <figure id="illustration-N"> anchors from the source would add blank space and steal hash links.
    for (const fig of fragment.querySelectorAll('figure')) {
      if (!fig.textContent.trim() && !fig.querySelector('img, svg, picture, video')) fig.remove();
    }
    for (const el of fragment.querySelectorAll('*')) {
      for (const attr of [...el.attributes]) {
        if (/^on/i.test(attr.name)) el.removeAttribute(attr.name);
      }
    }
    for (const a of fragment.querySelectorAll('a[data-section-id]')) {
      const hash = a.dataset.fragment ? `#${encodeURIComponent(a.dataset.fragment)}` : '';
      a.setAttribute('href', readUrl(edition.id, a.dataset.sectionId) + hash);
    }
    for (const a of fragment.querySelectorAll('a[href]:not([data-section-id])')) {
      const href = a.getAttribute('href');
      if (/^\s*javascript:/i.test(href)) a.removeAttribute('href');
      else if (/^(https?:|mailto:)/i.test(href)) a.rel = 'noopener';
      else if (!href.startsWith('#')) a.removeAttribute('href');
    }
  }

  // Returns the chapter's own opening heading, if its markup starts with one.
  function openingHeading(body) {
    for (const el of body.querySelectorAll('h1, h2, h3, h4, h5, h6, p, figure')) {
      if (el.tagName === 'P' && !el.textContent.trim()) continue;
      return /^H[1-6]$/.test(el.tagName) ? el : null;
    }
    return null;
  }

  // Reading position
  let saveTimer = 0;
  function save() {
    const max = document.documentElement.scrollHeight - innerHeight;
    setProgress(edition.id, {
      section: sections[current].id,
      sectionTitle: sectionLabel(sections[current], current),
      scroll: max > 0 ? scrollY / max : 0,
    });
  }
  function updateProgressBar() {
    const max = document.documentElement.scrollHeight - innerHeight;
    const within = max > 0 ? scrollY / max : 1;
    progressBar.style.width = `${((current + within) / sections.length) * 100}%`;
  }
  addEventListener('scroll', () => {
    updateProgressBar();
    clearTimeout(saveTimer);
    saveTimer = setTimeout(save, 400);
  }, { passive: true });

  addEventListener('popstate', () => {
    const { index } = indexFromUrl();
    if (index !== current) show(index);
    if (!revealFragment(hashFragment())) window.scrollTo(0, 0);
  });

  // Cross-section links from the source (notes, list of illustrations) carry
  // data-section-id and data-fragment. Switch section, then go to the fragment.
  article.addEventListener('click', (event) => {
    const link = event.target.closest('a[data-section-id]');
    if (!link || event.defaultPrevented || event.button !== 0
      || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const index = sections.findIndex((section) => String(section.id) === link.dataset.sectionId);
    if (index < 0) return;
    event.preventDefault();
    navigateTo(index, link.dataset.fragment || '');
  });

  function navigateTo(index, fragment) {
    if (index !== current) show(index);
    const target = revealFragment(fragment);
    if (!target) window.scrollTo(0, 0);
    history.pushState({ index }, '', readUrl(edition.id, sections[index].id) + (target ? `#${encodeURIComponent(target.id)}` : ''));
    article.focus({ preventScroll: true });
    save();
  }

  function hashFragment() {
    return location.hash ? safeDecode(location.hash.slice(1)) : '';
  }

  // Resolves a fragment in the rendered section: our figure ("ill-<id>" or the bare
  // illustration id, historical ones included) or any element id kept from the source HTML.
  function fragmentTarget(fragment) {
    if (!fragment) return null;
    const bare = fragment.startsWith('ill-') ? fragment.slice(4) : fragment;
    const byId = (id) => article.querySelector(`#${CSS.escape(id)}`);
    return byId(`ill-${bare}`) || byId(fragment);
  }

  function revealFragment(fragment) {
    const target = fragmentTarget(fragment);
    if (!target) return null;
    keepCentred(target);
    const mark = target.classList.contains('illustration') ? 'highlight' : 'flash';
    target.classList.add(mark);
    setTimeout(() => target.classList.remove(mark), 2500);
    return target;
  }

  addEventListener('keydown', (event) => {
    if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey || isTyping(event)) return;
    if (toc.open || lightbox.open) return;
    if (event.key === 'ArrowRight' || event.key === ']') { event.preventDefault(); go(current + 1); }
    else if (event.key === 'ArrowLeft' || event.key === '[') { event.preventDefault(); go(current - 1); }
    else if (event.key === 'c' || event.key === 't') { event.preventDefault(); openToc(); }
    else if (event.key === '+' || event.key === '=') resize(1);
    else if (event.key === '-') resize(-1);
  });

  // Initial render
  const start = indexFromUrl();
  show(start.index);
  history.replaceState({ index: start.index }, '', readUrl(edition.id, sections[start.index].id) + location.hash);
  // Run now rather than in requestAnimationFrame: frames do not fire in background tabs.
  (() => {
    const target = revealFragment(hashFragment());
    if (target) {
      history.replaceState(history.state, '', `${location.pathname}${location.search}#${encodeURIComponent(target.id)}`);
    } else if (!start.explicit && start.scroll > 0) {
      const restore = () => scrollTo(0, start.scroll * (document.documentElement.scrollHeight - innerHeight));
      restore();
      addEventListener('load', restore, { once: true });
    }
    save();
  })();
}

// Images above the target load lazily and change the layout; re-centre the target
// until they have loaded or the reader starts scrolling on their own.
function keepCentred(target) {
  const centre = () => target.scrollIntoView({ block: 'center' });
  const above = [...document.querySelectorAll('.reader img')].filter(
    (img) => img.compareDocumentPosition(target) & Node.DOCUMENT_POSITION_FOLLOWING || target.contains(img));
  above.forEach((img) => { img.loading = 'eager'; });
  let active = true;
  const stop = () => { active = false; };
  for (const type of ['wheel', 'touchstart', 'keydown', 'pointerdown']) addEventListener(type, stop, { once: true, passive: true });
  setTimeout(stop, 6000);
  for (const img of above) {
    if (!img.complete) img.addEventListener('load', () => { if (active) centre(); }, { once: true });
  }
  centre();
}

function safeDecode(value) {
  try { return decodeURIComponent(value); } catch { return value; }
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, Number(value) || 0));
}
