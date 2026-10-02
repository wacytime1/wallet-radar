// Install the corrected filter handler in an already-open page, preserving its real scan.
// Only the local filter script is loaded; this never requests CoinGecko or a scan.
(async () => {
  if (typeof state === 'undefined' || !state.result) {
    throw new Error('Keep the completed Wallet Radar scan open before installing this filter fix.');
  }
  if (typeof normalizeResearchPool !== 'function') {
    await new Promise((resolve,reject) => {
      const script = document.createElement('script');
      script.src = '/static/research-filters.js';
      script.onload = resolve;
      script.onerror = () => reject(new Error('Local filter script could not load.'));
      document.head.appendChild(script);
    });
  }
window.filters = function filters() {
  const form = $('filters');
  if (!form.reportValidity()) return null;
  const values = {};
  ['min_pnl','min_win_rate','min_trades','max_days','min_tokens'].forEach(name => { values[name] = Number(form.elements[name].value); });
  const maxTrades = form.elements.max_trades;
  values.max_trades = maxTrades ? (maxTrades.value.trim() === '' ? null : Number(maxTrades.value)) : 5000;
  values.min_win_rate /= 100;
  ['recently_active','exclude_bots','exclude_infra'].forEach(name => { values[name] = form.elements[name].checked; });
  return values;
};
window.renderQualityDebug = function renderQualityDebug(result) {
  $('total-researchable').textContent = result.researchable_count;
  const view = $('quality-debug');
  if (view) view.innerHTML = `<details><summary>Profile quality diagnostics · app-derived (${result.profile_rows.length} profiles)</summary><pre style="overflow:auto;max-height:360px;white-space:pre-wrap;font-size:11px">${esc(JSON.stringify(result.quality_debug,null,2))}</pre></details>`;
}
;
window.applyFilters = async function applyFilters() {
  const values = filters();
  if (!values) { $('status').textContent = 'Filters not applied. Check the highlighted input.'; return; }
  if (!state.result) { showError('No saved scan is loaded. Keep the completed scan open to apply filters.'); return; }
  state.result = normalizeResearchPool(state.result);
  renderQualityDebug(state.result);
  const version = ++state.filterVersion;
  const runId = state.result.run_id;
  stage('filters'); $('apply').disabled = true;
  $('apply').textContent = 'Applying…';
  $('status').textContent = 'Applying your current filters to saved profiles…';
  try {
    const result = filterCachedProfiles(state.result.rows, values);
    if (version !== state.filterVersion || state.result?.run_id !== runId) return;
    state.rows = result.rows;
    $('total-passed').textContent = result.passed_count;
    $('shortlist-note').textContent = `${result.passed_count} wallets worth a closer look · highest Research Score first`;
    $('status').textContent = `${result.passed_count} wallets shortlisted`;
    renderRows(); stage('shortlist');
    $('status').textContent = `Filters applied · ${result.passed_count} / ${result.evaluated_count ?? state.result.rows.length} saved profiles passed · no new API requests`;
    if (!result.rows.length && result.rejected_by) {
      const summary = Object.entries(result.rejected_by).map(([reason,count]) => `${count} ${reason.replaceAll('_',' ')}`).join('; ');
      $('rows').innerHTML = `<tr><td colspan="8" class="empty">Filters applied. No saved profiles passed.<br>${esc(summary)}<br>A profile can fail more than one rule.</td></tr>`;
    }
  } catch (error) { if (version === state.filterVersion) { showError(error.message); $('status').textContent = `Filters were not applied: ${error.message}`; } }
  finally { if (version === state.filterVersion) { $('apply').disabled = state.busy; $('apply').textContent = 'Apply my filters'; } }
};
  await window.applyFilters();
})().catch(error => { showError(error.message); $('status').textContent = error.message; });
