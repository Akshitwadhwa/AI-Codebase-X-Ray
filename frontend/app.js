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
}
