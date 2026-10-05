const form = document.querySelector('#analyze-form');
const input = document.querySelector('#repository-url');
const button = document.querySelector('#analyze-button');
const status = document.querySelector('#status');
const results = document.querySelector('#results');

const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' }[char]));

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  button.disabled = true;
  results.classList.add('hidden');
  status.classList.remove('error');
  status.textContent = 'Downloading and scanning the repository on the server…';
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 45000);
  try {
    const response = await fetch('/api/analyze', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ repository_url: input.value }), signal: controller.signal });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || 'Analysis failed');
    render(payload);
    status.textContent = 'Analysis complete. Temporary repository files have been removed from the server.';
  } catch (error) { status.textContent = error.name === 'AbortError' ? 'Analysis exceeded 45 seconds. Try a smaller public repository.' : error.message; status.classList.add('error'); }
  finally { clearTimeout(timeout); button.disabled = false; }
});

function render(data) {
  const summary = data.summary;
  document.querySelector('#repo-name').textContent = data.repository;
  document.querySelector('#analysis-id').textContent = data.analysis_id ? `Saved analysis · ${data.analysis_id}.json` : '';
  document.querySelector('#primary-language').textContent = summary.primary_language ? `primary: ${summary.primary_language}` : 'primary: n/a';
  document.querySelector('#branch').textContent = `branch: ${data.default_branch}`;
  const metrics = [['files', summary.files], ['folders', summary.folders], ['source files', summary.source_files], ['classes', summary.classes], ['functions', summary.functions], ['imports', summary.imports], ['tests', summary.tests]];
  document.querySelector('#metrics').innerHTML = metrics.map(([label, value]) => `<div class="metric"><div class="metric-value">${value}</div><div class="metric-label">${label}</div></div>`).join('');
  document.querySelector('#file-count').textContent = `${data.files.length} files`;
  document.querySelector('#files').innerHTML = data.files.map((file) => `<div class="file">${escapeHtml(file)}</div>`).join('') || '<p>No supported text files found.</p>';
  document.querySelector('#symbol-count').textContent = `${data.symbols.length} symbols`;
  document.querySelector('#symbols').innerHTML = data.symbols.map((symbol) => `<div class="symbol"><span class="symbol-kind">${escapeHtml(symbol.kind)}</span><span class="symbol-name">${escapeHtml(symbol.name)}</span><span class="symbol-file">${escapeHtml(symbol.file_path)}:${symbol.start_line}</span></div>`).join('') || '<p>No symbols detected.</p>';
  document.querySelector('#relationship-count').textContent = `${data.relationships.length} edges`;
  document.querySelector('#relationship-list').innerHTML = data.relationships.map((item) => `<div class="relationship"><span class="relationship-kind">${escapeHtml(item.kind)}</span><span>${escapeHtml(item.source)}</span><span>→</span><span class="relationship-target">${escapeHtml(item.target)}</span></div>`).join('') || '<p>No relationships detected.</p>';
  document.querySelector('#warnings').textContent = data.warnings.length ? `Warnings: ${data.warnings.join(' ')}` : '';
  results.classList.remove('hidden');
  if (data.analysis_id) prepareImpact(data.analysis_id);
}

const impact = document.querySelector('#impact');
const symbolForm = document.querySelector('#symbol-form');
const symbolQuery = document.querySelector('#symbol-query');
const symbolHits = document.querySelector('#symbol-hits');
const impactBody = document.querySelector('#impact-body');
const openForm = document.querySelector('#open-form');
const openId = document.querySelector('#open-id');
let currentAnalysisId = '';
let selectedSymbolId = '';

openForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const id = openId.value.trim().toLowerCase();
  status.classList.remove('error');
  status.textContent = 'Loading saved analysis…';
  try {
    const response = await fetch(`/api/analyses/${encodeURIComponent(id)}`);
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || 'Analysis was not found');
    render(payload);
    status.textContent = `Opened saved analysis ${id}.`;
  } catch (error) {
    status.textContent = error.message;
    status.classList.add('error');
  }
});

function prepareImpact(analysisId) {
  currentAnalysisId = analysisId;
  impact.classList.remove('hidden');
  document.querySelector('#ask').classList.remove('hidden');
  loadProviders();
  symbolQuery.value = 'process_payment';
  searchSymbols('process_payment', true);
}

async function loadProviders() {
  const response = await fetch('/api/providers');
  const providers = await response.json();
  const primary = document.querySelector('#ask-provider');
  const compare = document.querySelector('#ask-compare');
  const configured = providers.filter((item) => item.configured);
  primary.innerHTML = configured.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.label)}</option>`).join('');
  compare.innerHTML = `<option value="">No comparison</option><option value="without-rag">Without RAG</option>` + configured.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.label)}</option>`).join('');
}

document.querySelector('#ask-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const question = document.querySelector('#ask-question').value.trim();
  if (!currentAnalysisId || !question) return;
  const answers = document.querySelector('#ask-answers');
  answers.innerHTML = '<p class="reason">Reading the saved scan…</p>';
  status.classList.remove('error');
  try {
    const compare = document.querySelector('#ask-compare').value;
    const response = await fetch(`/api/analyses/${currentAnalysisId}/ask`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, provider: document.querySelector('#ask-provider').value, compare_with: compare || null }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || 'The question failed');
    answers.innerHTML = payload.answers.map(renderAnswer).join('');
  } catch (error) {
    answers.innerHTML = '';
    status.textContent = error.message;
    status.classList.add('error');
  }
});

function renderAnswer(answer) {
  const citations = answer.citations.length
    ? answer.citations.map((item) => `<div class="evidence">${escapeHtml(item.path)}${item.line ? `:${item.line}` : ''}</div>`).join('')
    : '<p class="reason">No file citation.</p>';
  const mode = answer.mode === 'baseline' ? 'without RAG' : 'with RAG';
  return `<article class="panel answer"><div class="panel-title"><h3>${escapeHtml(answer.provider)}</h3><span>${escapeHtml(mode)}${answer.refused ? ' · not in this scan' : ''}</span></div><p>${escapeHtml(answer.answer)}</p><div class="stack"><h4>Citations</h4>${citations}</div></article>`;
}

symbolForm.addEventListener('submit', (event) => {
  event.preventDefault();
  searchSymbols(symbolQuery.value.trim(), true);
});

document.querySelector('#depth-2').addEventListener('change', () => {
  if (selectedSymbolId) selectSymbol(selectedSymbolId);
});

document.querySelector('#architecture-button').addEventListener('click', async () => {
  if (!currentAnalysisId) return;
  const response = await fetch(`/api/analyses/${currentAnalysisId}/graph?view=architecture`);
  const graph = await response.json();
  if (!response.ok) return;
  document.querySelector('#graph-note').textContent = graph.truncated ? `${graph.nodes.length} nodes · truncated` : `${graph.nodes.length} nodes`;
  const container = document.querySelector('#flow');
  const draw = () => window.XRayGraph.mountGraph(container, graph, selectSymbol);
  if (window.XRayGraph) draw();
  else document.addEventListener('xray-graph', draw, { once: true });
});

async function searchSymbols(query, selectFirst) {
  symbolHits.innerHTML = '';
  if (!currentAnalysisId || !query) return;
  const response = await fetch(`/api/analyses/${currentAnalysisId}/symbols?q=${encodeURIComponent(query)}`);
  const matches = await response.json();
  if (!response.ok) {
    symbolHits.innerHTML = `<p>${escapeHtml(matches.detail || 'Symbol search failed')}</p>`;
    return;
  }
  if (!matches.length) {
    symbolHits.innerHTML = '<p>No symbol matches that name.</p>';
    return;
  }
  symbolHits.innerHTML = matches.map((symbol) => `<button type="button" class="hit" data-symbol="${escapeHtml(symbol.symbol_id)}">${escapeHtml(symbol.kind)} ${escapeHtml(symbol.parent ? `${symbol.parent}.` : '')}${escapeHtml(symbol.name)} · ${escapeHtml(symbol.file_path)}:${symbol.start_line}</button>`).join('');
  symbolHits.querySelectorAll('.hit').forEach((button) => button.addEventListener('click', () => selectSymbol(button.dataset.symbol)));
  if (selectFirst) selectSymbol(matches.find((symbol) => symbol.parent)?.symbol_id || matches[0].symbol_id);
}

function selectSymbol(symbolId) {
  loadImpact(symbolId).catch((error) => {
    status.textContent = error.message;
    status.classList.add('error');
  });
}

function evidenceList(items, empty) {
  if (!items.length) return `<p class="reason">${empty}</p>`;
  return items.map((item) => `<div class="evidence"><b>${escapeHtml(item.name)}</b> ${escapeHtml(item.file_path || '')}${item.line ? `:${item.line}` : ''} · ${escapeHtml(item.reason)}${item.confidence && item.confidence !== 'file' ? ` · ${escapeHtml(item.confidence)}` : ''}${item.depth > 1 ? ' · depth 2' : ''}${item.raw && item.raw !== item.name ? `<div class="reason">${escapeHtml(item.raw)}</div>` : ''}</div>`).join('');
}

async function loadImpact(symbolId) {
  selectedSymbolId = symbolId;
  status.classList.remove('error');
  document.querySelector('#file-view').classList.add('hidden');
  symbolHits.querySelectorAll('.hit').forEach((button) => button.classList.toggle('active', button.dataset.symbol === symbolId));
  const depth = document.querySelector('#depth-2').checked ? 2 : 1;
  const encoded = symbolId.split('/').map(encodeURIComponent).join('/');
  const response = await fetch(`/api/analyses/${currentAnalysisId}/impact/${encoded}?depth=${depth}`);
  const report = await response.json();
  if (!response.ok) throw new Error(report.detail || 'Impact failed');
  const symbol = report.symbol;
  document.querySelector('#impact-summary').innerHTML = `<div class="panel-title"><h3>${escapeHtml(symbol.parent ? `${symbol.parent}.` : '')}${escapeHtml(symbol.name)}</h3><span>${escapeHtml(symbol.kind)}</span></div><p class="impact-meta">${escapeHtml(symbol.file_path)}:${symbol.start_line}-${symbol.end_line}</p><pre class="snippet">${escapeHtml(symbol.snippet || 'No snippet stored for this symbol.')}</pre><div class="stack"><h4>Callers</h4>${evidenceList(report.callers, 'No resolved callers.')}<h4>Callees</h4>${evidenceList(report.callees, 'No resolved callees.')}<h4>Bases</h4>${evidenceList(report.bases, 'No resolved base class.')}</div>`;
  const flags = report.risk.length ? report.risk.map((flag) => `<div class="flag"><code>${escapeHtml(flag.code)}</code> ${escapeHtml(flag.message)}</div>`).join('') : '<p class="reason">No risk flags for this symbol.</p>';
  const reviews = report.review_files.map((file) => `<button type="button" class="hit" data-path="${escapeHtml(file.path)}">${escapeHtml(file.path)}<div class="reason">${escapeHtml(file.reasons.join(' · '))}</div></button>`).join('');
  document.querySelector('#impact-side').innerHTML = `<div class="panel-title"><h3>Review</h3><span>${report.review_files.length} files</span></div><div class="stack"><h4>Risk</h4>${flags}<h4>Imports</h4>${evidenceList(report.imports, 'No resolved imports.')}<h4>Tests</h4>${evidenceList(report.tests, 'No test evidence.')}<h4>Files to review</h4>${reviews || '<p class="reason">None.</p>'}<h4>Unresolved</h4>${evidenceList(report.unresolved, 'No unbound project calls.')}</div>`;
  document.querySelector('#impact-side').querySelectorAll('[data-path]').forEach((button) => button.addEventListener('click', () => loadFile(button.dataset.path)));
  impactBody.classList.remove('hidden');
  const graphResponse = await fetch(`/api/analyses/${currentAnalysisId}/graph?view=neighborhood&symbol_id=${encodeURIComponent(symbolId)}`);
  const graph = await graphResponse.json();
  document.querySelector('#graph-note').textContent = graphResponse.ok ? `${graph.nodes.length} nodes` : 'Map unavailable';
  const container = document.querySelector('#flow');
  const draw = () => window.XRayGraph.mountGraph(container, graph, selectSymbol);
  if (window.XRayGraph) draw();
  else document.addEventListener('xray-graph', draw, { once: true });
}

async function loadFile(path) {
  const response = await fetch(`/api/analyses/${currentAnalysisId}/file?path=${encodeURIComponent(path)}`);
  const payload = await response.json();
  const panel = document.querySelector('#file-view');
  if (!response.ok) {
    panel.innerHTML = `<p>${escapeHtml(payload.detail || 'File was not found')}</p>`;
    return;
  }
  const symbols = payload.symbols.map((symbol) => `<button type="button" class="hit" data-symbol="${escapeHtml(symbol.symbol_id)}">${escapeHtml(symbol.kind)} ${escapeHtml(symbol.name)} · line ${symbol.start_line}</button>`).join('');
  panel.innerHTML = `<div class="panel-title"><h3>${escapeHtml(payload.path)}</h3><span>${payload.is_test ? 'test file' : `${payload.symbols.length} symbols`}</span></div><div class="stack"><h4>Symbols</h4>${symbols || '<p class="reason">No symbols in this file.</p>'}<h4>Imports</h4>${evidenceList(payload.imports, 'None.')}<h4>Imported by</h4>${evidenceList(payload.imported_by, 'None.')}<h4>Tests</h4>${evidenceList(payload.tests, 'None.')}</div>`;
  panel.classList.remove('hidden');
  panel.querySelectorAll('[data-symbol]').forEach((button) => button.addEventListener('click', () => selectSymbol(button.dataset.symbol)));
}
