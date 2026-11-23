// Shared helpers for the Illustrated Library static site.
// All URLs are relative so the site works under any subpath
// (for example https://mateogon.github.io/illustrated-library/).

export const RELEASE_BASE =
  'https://github.com/mateogon/illustrated-library/releases/download/library-v1/';

const LANGUAGE_NAMES = { en: 'English', es: 'Spanish', fr: 'French', de: 'German', it: 'Italian', pt: 'Portuguese' };

export function languageName(code) {
  if (!code) return 'Unknown';
  const base = String(code).toLowerCase().split('-')[0];
  return LANGUAGE_NAMES[base] || code.toUpperCase();
}

/** Build a DOM element. Strings become text nodes, so data is never parsed as HTML. */
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === 'class') el.className = value;
    else if (key === 'dataset') Object.assign(el.dataset, value);
    else if (key.startsWith('on') && typeof value === 'function') el.addEventListener(key.slice(2), value);
    else if (value === true) el.setAttribute(key, '');
    else el.setAttribute(key, value);
  }
  appendChildren(el, children);
  return el;
}

function appendChildren(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child === undefined || child === null || child === false) continue;
    el.append(child instanceof Node ? child : String(child));
  }
}

export function params() {
  return new URLSearchParams(location.search);
}

const cache = new Map();
async function fetchJson(path) {
  if (!cache.has(path)) {
    cache.set(path, fetch(path, { cache: 'no-cache' }).then((response) => {
      if (!response.ok) throw new Error(`Could not load ${path} (HTTP ${response.status})`);
      return response.json();
    }));
  }
  return cache.get(path);
}

export async function loadLibrary() {
  const library = await fetchJson('data/library.json');
  library.works = Array.isArray(library.works) ? library.works : [];
  library.editions = Array.isArray(library.editions) ? library.editions : [];
  return library;
}

export async function loadEdition(edition) {
  const path = edition.sectionsPath || `data/editions/${edition.id}.json`;
  const data = await fetchJson(path);
  data.sections = (data.sections || []).map((section, index) => ({
    ...section,
    id: section.id ?? String(index + 1),
    blocks: section.blocks || [],
  }));
  return data;
}

export function findEdition(library, id) {
  return library.editions.find((edition) => edition.id === id);
}

export function findWork(library, workId) {
  return library.works.find((work) => work.id === workId);
}

/** Group editions by work, keeping catalog order. English editions come first in each group. */
export function groupByWork(library) {
  const groups = new Map();
  for (const edition of library.editions) {
    const key = edition.workId || edition.id;
    if (!groups.has(key)) groups.set(key, { work: findWork(library, key) || { id: key }, editions: [] });
    groups.get(key).editions.push(edition);
  }
  for (const group of groups.values()) {
    group.editions.sort((a, b) => langRank(a.language) - langRank(b.language));
  }
  return [...groups.values()];
}

function langRank(code) {
  return String(code || '').startsWith('en') ? 0 : 1;
}

export function workTitle(group) {
  const english = group.editions.find((e) => String(e.language).startsWith('en'));
  return group.work.title || (english || group.editions[0]).title;
}

export function sectionLabel(section, index) {
  const title = (section.title || '').trim();
  return title || `Section ${index + 1}`;
}

export function countLabel(count, singular, plural = `${singular}s`) {
  if (count === undefined || count === null) return '';
  return `${count} ${count === 1 ? singular : plural}`;
}

export function formatBytes(bytes) {
  if (typeof bytes !== 'number' || !Number.isFinite(bytes)) return typeof bytes === 'string' ? bytes : '';
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(bytes < 10 * 1024 * 1024 ? 1 : 0)} MB`;
}

export const DOWNLOAD_PROFILES = [
  {
    key: 'color',
    label: 'Color EPUB',
    note: 'Full-color illustrations for phones, tablets, computers and color e-readers.',
  },
  {
    key: 'boox',
    label: 'E-ink EPUB',
    note: 'Grayscale-tuned illustrations for Boox and other e-ink readers.',
  },
];

export function downloadUrl(library, download) {
  if (!download) return null;
  if (download.url) return download.url;
  if (!download.file) return null;
  const base = library.downloadBase || RELEASE_BASE;
  return base + encodeURIComponent(download.file);
}

const RIGHTS_LABELS = {
  chile: 'Chile',
  unitedStates: 'United States',
  terms: 'Terms of the digital source',
  includedLayers: 'What is included',
  generatedArt: 'New illustrations',
};

/** Normalise the rights block. Accepts a plain string or the catalog object. */
export function rightsInfo(edition) {
  const rights = edition.rights;
  if (!rights) return { statements: [], sources: [], license: '' };
  if (typeof rights === 'string') return { statements: [{ label: 'Rights', text: rights }], sources: [], license: '' };
  const statements = [];
  for (const [key, value] of Object.entries(rights)) {
    if (typeof value !== 'string' || !value.trim() || key === 'transcriptionLicense') continue;
    statements.push({ key, label: RIGHTS_LABELS[key] || humanise(key), text: value });
  }
  const order = Object.keys(RIGHTS_LABELS);
  statements.sort((a, b) => rank(order, a.key) - rank(order, b.key));
  const sources = (Array.isArray(rights.sources) ? rights.sources : [])
    .map((source) => (typeof source === 'string' ? { label: source, url: source } : source))
    .filter((source) => source && source.url);
  return { statements, sources, license: rights.transcriptionLicense || rights.license || '' };
}

function rank(order, key) {
  const index = order.indexOf(key);
  return index === -1 ? order.length : index;
}

function humanise(key) {
  const words = key.replace(/([a-z])([A-Z])/g, '$1 $2').replace(/[_-]+/g, ' ').toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** Optional free-form credits: [{role, name, url?}] or strings. */
export function extraCredits(edition) {
  const credits = Array.isArray(edition.credits) ? edition.credits : [];
  return credits.map((credit) => (typeof credit === 'string' ? { role: '', name: credit } : credit));
}

/** Flatten all illustrations of an edition, with chapter context and a short preceding passage. */
export function collectIllustrations(data) {
  const list = [];
  data.sections.forEach((section, sectionIndex) => {
    let lastText = '';
    for (const block of section.blocks) {
      if (block.type === 'html') {
        const text = htmlToText(block.html);
        if (text) lastText = text;
      } else if (block.type === 'illustration') {
        list.push({
          ...block,
          sectionId: section.id,
          sectionIndex,
          sectionTitle: sectionLabel(section, sectionIndex),
          excerpt: tail(lastText, 320),
        });
      }
    }
  });
  return list;
}

const scratch = typeof document !== 'undefined' ? document.createElement('template') : null;
function htmlToText(html) {
  if (!html || !scratch) return '';
  scratch.innerHTML = html;
  return (scratch.content.textContent || '').replace(/\s+/g, ' ').trim();
}

function tail(text, max) {
  if (text.length <= max) return text;
  const cut = text.slice(-max);
  const sentence = cut.search(/[.!?»”"]\s+\S/);
  if (sentence > -1 && sentence < max * 0.6) return cut.slice(sentence + 1).trim();
  const space = cut.indexOf(' ');
  return `…${cut.slice(space + 1)}`;
}

// Reading progress, stored only in this browser.
const PROGRESS_PREFIX = 'illustrated-library:progress:';
export function getProgress(editionId) {
  try {
    return JSON.parse(localStorage.getItem(PROGRESS_PREFIX + editionId) || 'null');
  } catch {
    return null;
  }
}
export function setProgress(editionId, value) {
  try {
    localStorage.setItem(PROGRESS_PREFIX + editionId, JSON.stringify({ ...value, at: Date.now() }));
  } catch {
    /* storage unavailable: progress is a convenience only */
  }
}

export function getPref(key, fallback) {
  try {
    const value = localStorage.getItem(`illustrated-library:${key}`);
    return value === null ? fallback : JSON.parse(value);
  } catch {
    return fallback;
  }
}
export function setPref(key, value) {
  try {
    localStorage.setItem(`illustrated-library:${key}`, JSON.stringify(value));
  } catch {
    /* ignore */
  }
}

export function readUrl(editionId, sectionId, illustrationId) {
  const query = new URLSearchParams({ edition: editionId });
  if (sectionId !== undefined && sectionId !== null) query.set('section', sectionId);
  return `read.html?${query}${illustrationId ? `#ill-${encodeURIComponent(illustrationId)}` : ''}`;
}
export function tourUrl(editionId, illustrationId) {
  const query = new URLSearchParams({ edition: editionId });
  if (illustrationId) query.set('image', illustrationId);
  return `tour.html?${query}`;
}
export function bookUrl(editionId) {
  return `book.html?${new URLSearchParams({ edition: editionId })}`;
}

/** Cover image, or a typographic placeholder when the edition has no cover. */
export function coverElement(edition, { eager = false } = {}) {
  if (edition.cover) {
    return h('img', {
      class: 'cover',
      src: edition.cover,
      alt: `Cover of ${edition.title}`,
      loading: eager ? 'eager' : 'lazy',
      decoding: 'async',
      width: 1200,
      height: 800,
    });
  }
  return h('div', { class: 'cover cover-placeholder', role: 'img', 'aria-label': `Cover of ${edition.title}` },
    h('span', { class: 'cover-title', lang: edition.language }, edition.title),
    edition.author ? h('span', { class: 'cover-author' }, edition.author) : null);
}

export function showError(container, error, extra) {
  container.replaceChildren(
    h('div', { class: 'notice notice-error', role: 'alert' },
      h('h1', {}, 'Something went wrong'),
      h('p', {}, error?.message || String(error)),
      extra || h('p', {}, h('a', { href: './' }, 'Back to the library'))),
  );
}

export function isTyping(event) {
  const target = event.target;
  return target instanceof HTMLElement
    && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName));
}

// Small inline icon set (stroke icons, 24×24 grid).
const ICONS = {
  book: 'M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5zM4 20.5A2.5 2.5 0 0 0 6.5 23H20v-5',
  images: 'M3 5h14v14H3zM7 1h14v14M3 15l4-4 4 4 2-2 4 4M8.5 9.5a1 1 0 1 0 0-.01',
  list: 'M8 6h13M8 12h13M8 18h13M3.5 6h.01M3.5 12h.01M3.5 18h.01',
  left: 'M15 18l-6-6 6-6',
  right: 'M9 18l6-6-6-6',
  close: 'M6 6l12 12M18 6L6 18',
  download: 'M12 3v12M7 10l5 5 5-5M4 21h16',
  expand: 'M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5',
  text: 'M4 7V5h12v2M10 5v14M7 19h6M15 13v-1h6v1M18 12v7M16.5 19h3',
  home: 'M3 11l9-8 9 8M5 9.5V21h14V9.5',
  external: 'M14 4h6v6M20 4l-9 9M18 14v6H4V6h6',
};

export function icon(name, label) {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '1.8');
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('stroke-linejoin', 'round');
  if (label) {
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', label);
  } else {
    svg.setAttribute('aria-hidden', 'true');
  }
  const path = document.createElementNS(ns, 'path');
  path.setAttribute('d', ICONS[name] || '');
  svg.append(path);
  return svg;
}

export function siteHeader(current) {
  return h('header', { class: 'site-header' },
    h('a', { class: 'brand', href: './' }, icon('book'), 'Illustrated Library'),
    h('nav', { 'aria-label': 'Site' },
      h('a', { class: 'btn btn-quiet', href: './', 'aria-current': current === 'shelf' ? 'page' : null }, 'Bookshelf'),
      h('a', { class: 'btn btn-quiet', href: './#about' }, 'About')));
}

export function siteFooter() {
  return h('footer', { class: 'site-footer', id: 'about' },
    h('p', {}, 'Illustrated Library publishes public-domain books with new illustrations placed chapter by chapter. Each book keeps its original language; credits, rights and source links are listed on every edition page.'),
    h('p', {}, 'Reading progress is stored only in this browser. No accounts, no tracking.'));
}

/** "15 illustrations", or "15 new illustrations · 42 historical" when the source edition had its own art. */
export function illustrationSummary(edition) {
  const historical = Number(edition.historicalIllustrations) || 0;
  if (!historical) return countLabel(edition.illustrationCount, 'illustration');
  return `${countLabel(edition.illustrationCount, 'new illustration')} · ${historical} historical`;
}

/** Historical line art is often transparent SVG; it needs a light plate to stay visible in dark mode. */
export function isLineArt(block) {
  return Boolean(block.historical) || /\.svg(\?|#|$)/i.test(block.src || '');
}
