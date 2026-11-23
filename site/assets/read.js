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
  if (!data.sections.length) throw new Error('This edition has no chapters yet.');
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

  function indexFromUrl() {
    const wanted = params().get('section');
    if (wanted !== null) {
      const found = sections.findIndex((s) => String(s.id) === wanted);
      if (found > -1) return { index: found, explicit: true };
    }
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
      width: block.width || 1200, height: block.height || 800,
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
    for (const el of fragment.querySelectorAll('*')) {
      for (const attr of [...el.attributes]) {
        if (/^on/i.test(attr.name)) el.removeAttribute(attr.name);
      }
    }
    for (const a of fragment.querySelectorAll('a[href]')) {
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
    show(index);
    window.scrollTo(0, 0);
  });

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
  requestAnimationFrame(() => {
    const target = location.hash ? document.getElementById(decodeURIComponent(location.hash.slice(1))) : null;
    if (target) {
      target.scrollIntoView({ block: 'center' });
      if (target.classList.contains('illustration')) {
        target.classList.add('highlight');
        const img = target.querySelector('img');
        if (img && !img.complete) img.addEventListener('load', () => target.scrollIntoView({ block: 'center' }), { once: true });
        setTimeout(() => target.classList.remove('highlight'), 2500);
      }
    } else if (!start.explicit && start.scroll > 0) {
      const restore = () => scrollTo(0, start.scroll * (document.documentElement.scrollHeight - innerHeight));
      restore();
      addEventListener('load', restore, { once: true });
    }
    save();
  });
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, Number(value) || 0));
}
