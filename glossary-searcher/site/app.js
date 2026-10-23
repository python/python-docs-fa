(function(){
  const REPO_URL = "https://github.com/python/python-docs-fa";
  document.getElementById('github-link').href = REPO_URL;

  // window.GLOSSARY / window.CORPUS are defined by data.js, generated at
  // build time from data/glossary.json and data/corpus.json.
  const GLOSSARY = window.GLOSSARY || [];
  // CORPUS entries shape: { msgid, msgstr, file, line } -- produced by
  // scripts/build_corpus.py from every .po file in python-docs-fa.
  const CORPUS = window.CORPUS || [];

  const MAX_RESULTS = 100;

  // Persian text contains Arabic-script letters; if the query has any, we
  // search Persian -> English, otherwise English -> Persian.
  function isPersian(term){ return /[\u0600-\u06FF]/.test(term); }

  function escapeRegex(s){ return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }

  // Builds a case-insensitive matcher. For Persian it tolerates the common
  // variants: Arabic vs Persian ya/kaf, optional ZWNJ, optional diacritics.
  function buildMatcher(term, fa){
    if (!fa) return new RegExp(escapeRegex(term), 'i');
    const optional = '[\\u200c\\u064B-\\u065F\\u0640]*';
    const src = Array.from(term.replace(/[\u200c]/g, '')).map(ch => {
      if (ch === 'ی' || ch === 'ي') return '[یي]';
      if (ch === 'ک' || ch === 'ك') return '[کك]';
      if (/\s/.test(ch)) return '[\\s\\u200c]+';
      return escapeRegex(ch);
    }).join(optional);
    return new RegExp(src, 'i');
  }

  // Returns up to MAX_RESULTS corpus entries whose msgid (EN) or msgstr (FA)
  // matches.
  function searchCorpus(re, fa){
    const field = fa ? 'msgstr' : 'msgid';
    const results = [];
    for (let i = 0; i < CORPUS.length && results.length < MAX_RESULTS; i++){
      if (re.test(CORPUS[i][field])) results.push(CORPUS[i]);
    }
    return results;
  }

  // ---------- Simple fuzzy matching for glossary lookup ----------
  function normalize(s){
    return s.toLowerCase()
      .replace(/ي/g,'ی').replace(/ك/g,'ک')
      .replace(/[\u200c\u064B-\u065F\u0640]/g,'')
      .trim();
  }

  function levenshtein(a, b){
    a = normalize(a); b = normalize(b);
    const m = a.length, n = b.length;
    if (m === 0) return n;
    if (n === 0) return m;
    const dp = new Array(n+1);
    for (let j=0; j<=n; j++) dp[j] = j;
    for (let i=1; i<=m; i++){
      let prev = dp[0];
      dp[0] = i;
      for (let j=1; j<=n; j++){
        const tmp = dp[j];
        dp[j] = Math.min(
          dp[j] + 1,
          dp[j-1] + 1,
          prev + (a[i-1] === b[j-1] ? 0 : 1)
        );
        prev = tmp;
      }
    }
    return dp[n];
  }

  function similarity(a, b){
    const dist = levenshtein(a, b);
    const maxLen = Math.max(a.length, b.length) || 1;
    return 1 - dist / maxLen;
  }

  function findGlossaryMatch(term, field){
    const norm = normalize(term);
    // exact match first (handles multi-variant "en" keys and comma lists)
    for (const entry of GLOSSARY){
      if (normalize(entry[field]) === norm) return { entry, score: 1 };
    }
    // substring / contains match
    let best = null;
    for (const entry of GLOSSARY){
      const enNorm = normalize(entry[field]);
      if (enNorm.includes(norm) || norm.includes(enNorm)){
        const score = Math.min(norm.length, enNorm.length) / Math.max(norm.length, enNorm.length);
        if (!best || score > best.score) best = { entry, score };
      }
    }
    if (best && best.score > 0.5) return best;
    // fuzzy fallback (semantic-similarity stand-in)
    best = null;
    for (const entry of GLOSSARY){
      const score = similarity(term, entry[field]);
      if (score > 0.72 && (!best || score > best.score)) best = { entry, score };
    }
    return best;
  }

  // ---------- Rendering ----------
  const hero = document.getElementById('hero');
  const resultsArea = document.getElementById('results-area');
  const input = document.getElementById('search-input');

  const PAGE_SIZE = 10;
  let currentPage = 1;
  let currentResults = [];
  let currentMatcher = null;
  let currentFa = false;

  function escapeHtml(s){
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }

  function highlight(text, re){
    const m = re && re.exec(text);
    if (!m) return escapeHtml(text);
    const i = m.index, j = i + m[0].length;
    return escapeHtml(text.slice(0,i)) + '<mark>' + escapeHtml(text.slice(i,j)) + '</mark>' + escapeHtml(text.slice(j));
  }

  function githubContributingDocumentLink(kind, term){
    if (kind === 'report'){ // translation bugs
      return "https://github.com/python/python-docs-fa/blob/3.14/CONTRIBUTING.md#%DA%AF%D8%B2%D8%A7%D8%B1%D8%B4-%D8%A7%D8%B4%DA%A9%D8%A7%D9%84-%D8%AF%D8%B1-%D8%AA%D8%B1%D8%AC%D9%85%D9%87";
    } else {
      return "https://github.com/python/python-docs-fa/blob/3.14/CONTRIBUTING.md#%D8%A7%D9%81%D8%B2%D9%88%D8%AF%D9%86-%D9%88%D8%A7%DA%98%D9%87-%DB%8C%D8%A7-%D8%A7%D8%B5%D8%B7%D9%84%D8%A7%D8%AD-%D8%AC%D8%AF%DB%8C%D8%AF-%D8%A8%D9%87-%D9%88%D8%A7%DA%98%D9%87%E2%80%8C%D9%86%D8%A7%D9%85%D9%87";
    }
  }

  function renderPagination(total){
    const pageCount = Math.ceil(total / PAGE_SIZE);
    if (pageCount <= 1) return '';
    let html = '<div class="pagination">';
    html += `<button class="page-btn" data-page="prev" ${currentPage===1?'disabled':''} aria-label="Previous page">‹</button>`;

    const pages = new Set([1, pageCount, currentPage, currentPage-1, currentPage+1]);
    let last = 0;
    for (let p=1; p<=pageCount; p++){
      if (!pages.has(p)) continue;
      if (p - last > 1) html += `<span class="page-ellipsis">…</span>`;
      html += `<button class="page-btn ${p===currentPage?'active':''}" data-page="${p}">${p}</button>`;
      last = p;
    }
    html += `<button class="page-btn" data-page="next" ${currentPage===pageCount?'disabled':''} aria-label="Next page">›</button>`;
    html += '</div>';
    return html;
  }

  function renderResultsPage(){
    const start = (currentPage-1)*PAGE_SIZE;
    const pageItems = currentResults.slice(start, start+PAGE_SIZE);
    const container = document.getElementById('result-list-container');
    if (!container) return;

    if (currentResults.length === 0){
      container.innerHTML = `<div class="empty-state">
        <p>هیچ نمونه‌ای در پیکره‌ی مستندات برای این واژه پیدا نشد.</p>
      </div>`;
      return;
    }

    let html = `<div class="results-count">
      <span>${currentResults.length.toLocaleString()} نمونه یافت شد</span>
      <span>صفحه‌ی ${currentPage} از ${Math.ceil(currentResults.length/PAGE_SIZE)}</span>
    </div>`;
    html += '<div class="result-list">';
    for (const r of pageItems){
      html += `<div class="result-row">
        <div>
          <div class="result-en">${currentFa ? escapeHtml(r.msgid) : highlight(r.msgid, currentMatcher)}</div>
          <div class="result-source">${escapeHtml(r.file)}:${r.line}</div>
        </div>
        <div class="result-fa">${currentFa ? highlight(r.msgstr, currentMatcher) : escapeHtml(r.msgstr)}</div>
      </div>`;
    }
    html += '</div>';
    html += renderPagination(currentResults.length);
    container.innerHTML = html;

    container.querySelectorAll('.page-btn[data-page]').forEach(btn => {
      btn.addEventListener('click', () => {
        const p = btn.getAttribute('data-page');
        const pageCount = Math.ceil(currentResults.length/PAGE_SIZE);
        if (p === 'prev') currentPage = Math.max(1, currentPage-1);
        else if (p === 'next') currentPage = Math.min(pageCount, currentPage+1);
        else currentPage = parseInt(p, 10);
        renderResultsPage();
        container.scrollIntoView({behavior:'smooth', block:'start'});
      });
    });
  }

  function doSearch(term){
    term = term.trim();
    if (!term) return;
    currentFa = isPersian(term);
    currentMatcher = buildMatcher(term, currentFa);
    currentPage = 1;

    hero.classList.add('compact');
    resultsArea.classList.add('visible');

    const match = findGlossaryMatch(term, currentFa ? 'fa' : 'en');
    currentResults = searchCorpus(currentMatcher, currentFa);

    let html = '';

    if (match){
      html += `<div class="glossary-card">
        <div class="glossary-card-label">واژه‌های مشابه درخواست شما که در واژه‌نامه یافت شدند:</div>
        <div class="glossary-card-term">
          <span class="glossary-en">${escapeHtml(currentFa ? match.entry.fa : match.entry.en)}</span>
          <span class="glossary-arrow">→</span>
          <span class="glossary-fa">${escapeHtml(currentFa ? match.entry.en : match.entry.fa)}</span>
        </div>
        ${match.score < 1 ? `<div class="glossary-match-score">مطابقت تقریبی (${Math.round(match.score*100)}٪)</div>` : ''}
      </div>`;
    } else {
      html += `<div class="no-glossary-note">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 9v4M12 17h.01M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z"/></svg>
        <span>این واژه در واژه‌نامه رسمی یافت نشد. نتایج زیر از پیکره‌ی ترجمه‌ی مستندات هستند.
        اگر فکر می‌کنید این واژه باید به واژه‌نامه اضافه شود،
        <a href="${githubContributingDocumentLink('suggest', term)}" target="_blank" rel="noopener">یک واژه‌ی جدید پیشنهاد دهید</a>.</span>
      </div>`;
    }

    html += `<div id="result-list-container"></div>`;

    html += `<div class="prompt-row">
      <a class="prompt-btn" href="${githubContributingDocumentLink('report', term)}" target="_blank" rel="noopener">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 9v4M12 17h.01M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z"/></svg>
        گزارش خطای ترجمه یا پیشنهاد ترجمه‌ی بهتر
      </a>
      ${!match ? `<a class="prompt-btn suggest-new" href="${githubContributingDocumentLink('suggest', term)}" target="_blank" rel="noopener">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 5v14M5 12h14"/></svg>
        پیشنهاد افزودن به واژه‌نامه
      </a>` : ''}
    </div>`;

    resultsArea.innerHTML = html;
    renderResultsPage();
  }

  let debounceTimer;
  input.addEventListener('input', () => {
    clearTimeout(debounceTimer);
    const val = input.value;
    if (!val.trim()){
      hero.classList.remove('compact');
      resultsArea.classList.remove('visible');
      resultsArea.innerHTML = '';
      return;
    }
    debounceTimer = setTimeout(() => doSearch(val), 380);
  });

  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter'){
      clearTimeout(debounceTimer);
      doSearch(input.value);
    }
  });
})();
