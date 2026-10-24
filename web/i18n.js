/* Language switch (VI / EN / RU) shared by the 3D map and the dashboard.
   The pages are written in Vietnamese; this layer translates what is on screen, including text the page code
   writes later, from a table keyed by the original Vietnamese (web/i18n_dict.json, inlined at build time).
   Lookup order for each text: exact match, then the same text with its numbers swapped in positionally,
   then known phrases replaced inside the text. The original is kept per node, so switching back is exact. */
(function () {
  const DICT = /*__I18N_DICT__*/{};
  const LANGS = ['vi', 'en', 'ru'], COL = { en: 0, ru: 1 }, KEY = 'vhm-lang';
  const VI = /[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]/i;
  const NUM = /\d+(?:[.,]\d+)*/g, HOLE = '\u0001';
  const ATTRS = ['aria-label', 'title', 'placeholder', 'alt'];
  const SKIP = { SCRIPT: 1, STYLE: 1, NOSCRIPT: 1, TEXTAREA: 1 };

  let lang = 'vi';
  try {
    const q = new URLSearchParams(location.search).get('lang'), s = localStorage.getItem(KEY);
    lang = LANGS.includes(q) ? q : LANGS.includes(s) ? s : 'vi';
  } catch (e) { /* storage blocked: stay in Vietnamese */ }

  // number templates: "Khối phòng (533)" also matches "Khối phòng (426)"
  const TPL = new Map();
  for (const k in DICT) {
    const kn = k.match(NUM); if (!kn) continue;
    const tk = k.replace(NUM, HOLE); if (TPL.has(tk)) continue;
    const v = DICT[k].map((s) => ((s.match(NUM) || []).length === kn.length ? s.replace(NUM, HOLE) : null));
    if (v.every((x) => x != null)) TPL.set(tk, v);
  }
  // phrase fallback for text the code assembles from pieces; longest phrase first, whole words only
  const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const LETTER = /[\p{L}\p{N}]/u;
  const frags = Object.keys(DICT).filter((k) => k.length > 1).sort((a, b) => b.length - a.length);
  const FRAG = frags.length ? new RegExp(frags.map((k) => (LETTER.test(k[0]) ? '(?<![\\p{L}\\p{N}])' : '') + esc(k) +
    (LETTER.test(k[k.length - 1]) ? '(?![\\p{L}\\p{N}])' : '')).join('|'), 'gu') : null;

  // the pages write numbers the Vietnamese way (1.830 and 20,0): English gets 1,830 and 20.0, Russian 1 830 and 20,0
  const VNUM = /(?<![\d.,])(\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+,\d+)(?!\d|[.,]\d)/g;
  const numFix = (s) => s.replace(VNUM, (x) => (lang === 'en' ? x.replace(/[.,]/g, (c) => (c === '.' ? ',' : '.')) : x.replace(/\./g, ' ')));

  const cache = new Map();
  function tr(s) {
    if (lang === 'vi') return s;
    const hit = cache.get(s); if (hit !== undefined) return hit;
    const out = numFix(/\p{L}/u.test(s) ? words(s) : s);
    if (cache.size > 20000) cache.clear();
    cache.set(s, out);
    return out;
  }
  function words(s) {
    const m = /^(\s*)([\s\S]*?)(\s*)$/.exec(s), core = m[2], col = COL[lang];
    let out = DICT[core] ? DICT[core][col] : null;
    if (out == null) {
      const nums = core.match(NUM), t = nums && TPL.get(core.replace(NUM, HOLE));
      if (t) { let j = 0; out = t[col].replace(/\u0001/g, () => nums[j++]); }
    }
    if (out == null) out = FRAG ? core.replace(FRAG, (x) => DICT[x][col]) : core;
    return m[1] + out + m[3];
  }

  // per text node / attribute: the Vietnamese source and what we last wrote
  const textSrc = new WeakMap(), attrSrc = new WeakMap();
  let changed = false;
  function doText(n) {
    const cur = n.nodeValue, rec = textSrc.get(n);
    const src = rec && cur === rec.out ? rec.src : cur;
    const out = tr(src);
    if (out !== src || rec) textSrc.set(n, { src, out });
    if (out !== cur) { n.nodeValue = out; changed = true; }
  }
  function doAttr(el, a) {
    const cur = el.getAttribute(a); if (cur == null) return;
    let recs = attrSrc.get(el); const rec = recs && recs[a];
    const src = rec && cur === rec.out ? rec.src : cur;
    const out = tr(src);
    if (out !== src || rec) { if (!recs) attrSrc.set(el, (recs = {})); recs[a] = { src, out }; }
    if (out !== cur) { el.setAttribute(a, out); changed = true; }
  }
  function walk(root) {
    if (root.nodeType === 3) { if (!root.parentNode || !SKIP[root.parentNode.nodeName]) doText(root); return; }
    if (root.nodeType !== 1 || SKIP[root.nodeName]) return;
    if (root.hasAttribute('data-lang-slot')) { fillSlot(root); return; }
    for (const a of ATTRS) if (root.hasAttribute(a)) doAttr(root, a);
    for (let c = root.firstChild; c; c = c.nextSibling) walk(c);
  }

  // the switch itself: filled into every [data-lang-slot]; pages keep that element out of their own re-renders
  const NAMES = { vi: 'Tiếng Việt', en: 'English', ru: 'Русский' };
  function fillSlot(slot) {
    if (!slot.firstChild) {
      slot.className = 'lang-switch'; slot.setAttribute('role', 'group'); slot.setAttribute('aria-label', 'Ngôn ngữ / Language / Язык');
      slot.innerHTML = LANGS.map((l) => `<button type="button" data-lang="${l}" lang="${l}" title="${NAMES[l]}">${l.toUpperCase()}</button>`).join('');
      slot.addEventListener('click', (e) => { const b = e.target.closest('[data-lang]'); if (b) setLang(b.dataset.lang); });
    }
    slot.querySelectorAll('[data-lang]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.lang === lang)));
  }
  const css = document.createElement('style');
  css.textContent = '.lang-switch{display:inline-flex;flex:none;align-items:stretch;height:44px;border:1px solid currentColor;border-radius:8px;overflow:hidden}' +
    '.lang-switch button{min-width:38px;padding:0 8px;border:0;background:transparent;color:inherit;font:600 11.5px/1 "JetBrains Mono",ui-monospace,monospace;letter-spacing:.04em;cursor:pointer;opacity:.6}' +
    '.lang-switch button+button{border-left:1px solid currentColor}' +
    '.lang-switch button[aria-pressed="true"]{opacity:1;background:var(--lang-ink,#2E3A26);color:var(--lang-on,#F4F0DB)}' +
    '.lang-switch button:hover{opacity:1}';
  document.head.appendChild(css);

  let busy = false;
  function run(fn) {
    busy = true; changed = false;
    try { fn(); } finally { busy = false; }
    if (changed) document.dispatchEvent(new CustomEvent('i18n:changed', { detail: { lang } }));
  }
  function setLang(l) {
    if (!LANGS.includes(l) || l === lang) return;
    lang = l; cache.clear();
    try { localStorage.setItem(KEY, l); } catch (e) { /* not remembered */ }
    document.documentElement.lang = l;
    run(() => walk(document.documentElement));
    document.querySelectorAll('[data-lang-slot]').forEach(fillSlot);
  }
  window.vhmLang = { get: () => lang, set: setLang, tr: (s) => tr(s) };

  new MutationObserver((list) => {
    if (busy) return;
    run(() => {
      for (const m of list) {
        if (m.type === 'childList') m.addedNodes.forEach(walk);
        else if (m.type === 'characterData') { if (m.target.parentNode && !SKIP[m.target.parentNode.nodeName]) doText(m.target); }
        else if (m.target.nodeType === 1) doAttr(m.target, m.attributeName);
      }
    });
  }).observe(document.documentElement, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRS });

  const start = () => { document.documentElement.lang = lang; run(() => walk(document.documentElement)); };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
