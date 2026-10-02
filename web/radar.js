'use strict';
const $ = id => document.getElementById(id);
const state = {result: null, rows: [], stream: null, busy: false, filterVersion: 0, drawerVersion: 0, returnFocus: null};
const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const money = value => value == null ? '—' : new Intl.NumberFormat('en-US', {style:'currency', currency:'USD', ...(value !== 0 && Math.abs(value) < 1 ? {maximumSignificantDigits:4} : {maximumFractionDigits:0})}).format(value);
const pct = value => value == null ? '—' : `${(value * 100).toFixed(1)}%`;
const short = value => value.length > 14 ? `${value.slice(0,6)}…${value.slice(-4)}` : value;
const age = timestamp => {
  if (!timestamp) return 'Unknown';
  const seconds = Math.max(0, Date.now()/1000 - timestamp);
  if (seconds < 60) return 'just now';
  if (seconds < 3600) return `${Math.floor(seconds/60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds/3600)}h ago`;
  return `${Math.floor(seconds/86400)}d ago`;
};
function showError(message) { $('error').textContent = message; $('error').hidden = !message; }
function stage(name) { document.querySelectorAll('.pipeline li').forEach(li => li.classList.toggle('active', li.id === `step-${name}`)); }
function setBusy(busy) {
  state.busy = busy;
  ['scan','refresh','chain','source','count'].forEach(id => {$(id).disabled = busy;});
  $('scan').textContent = busy ? 'Scanning…' : 'Scan';
  $('apply').disabled = busy || !state.result;
}
async function api(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let message = 'The request could not be completed. Try again.';
    try { const body = await response.json(); if (typeof body.detail === 'string') message = body.detail; } catch (_) {}
    throw new Error(message);
  }
  return response.json();
}
function tokens(items) {
  $('tokens').innerHTML = items.map(t => `<div class="token"><b>${esc(t.symbol)}</b><span>${esc(money(t.price_usd))} · ${t.change_1h == null ? '1h change unavailable' : `${esc(t.change_1h.toFixed(1))}% 1h`}</span></div>`).join('');
}
function filters() {
  const form = $('filters');
  if (!form.reportValidity()) return null;
  const values = {};
  ['min_pnl','min_win_rate','min_trades','max_days','min_tokens'].forEach(name => { values[name] = Number(form.elements[name].value); });
  const maxTrades = form.elements.max_trades;
  values.max_trades = maxTrades ? (maxTrades.value.trim() === '' ? null : Number(maxTrades.value)) : 5000;
  values.min_win_rate /= 100;
  ['recently_active','exclude_bots','exclude_infra'].forEach(name => { values[name] = form.elements[name].checked; });
  return values;
}
async function applyFilters() {
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
}
function renderQualityDebug(result) {
  $('total-researchable').textContent = result.researchable_count;
  const view = $('quality-debug');
  if (view) view.innerHTML = `<details><summary>Profile quality diagnostics · app-derived (${result.profile_rows.length} profiles)</summary><pre style="overflow:auto;max-height:360px;white-space:pre-wrap;font-size:11px">${esc(JSON.stringify(result.quality_debug,null,2))}</pre></details>`;
}

function why(row) {
  return [row.realized_pnl_usd == null ? null : `${money(row.realized_pnl_usd)} PnL`, row.win_rate == null ? null : `${pct(row.win_rate)} win rate`, `${row.tokens_seen_in} scanned tokens`, row.last_seen_ts ? `active ${age(row.last_seen_ts)}` : null].filter(Boolean).map(esc).join(' · ');
}
function renderRows() {
  const key = $('sort').value;
  const rows = [...state.rows].sort((a,b) => (b[key] ?? -Infinity) - (a[key] ?? -Infinity));
  $('rows').innerHTML = rows.length ? rows.map(row => `<tr>
  <td><button class="wallet-link" data-wallet="${esc(row.address)}">${esc(short(row.address))}</button><button class="copy" data-copy="${esc(row.address)}" aria-label="Copy full wallet address" title="Copy full address">Copy</button></td>
  <td><span class="score" title="Computed by this app from CoinGecko API data.">${esc(row.research_score ?? '—')}</span></td>
  <td class="${row.realized_pnl_usd >= 0 ? 'positive':'negative'}" title="${esc(row.pnl_basis)}">${esc(money(row.realized_pnl_usd))}</td>
  <td title="${esc(row.win_rate_basis)}">${esc(pct(row.win_rate))}</td><td title="${esc(row.trades_basis)}">${esc(row.trades ?? '—')}</td>
  <td title="${esc((row.seen_in || []).map(t=>t.symbol).join(', '))}">${esc(row.tokens_seen_in ?? '—')}</td><td>${esc(age(row.last_seen_ts))}</td><td class="why">${why(row)}</td></tr>`).join('') : '<tr><td colspan="8" class="empty">No wallets passed the current filters. Try relaxing one of the filters.</td></tr>';
  $('shortlist-note').textContent = `${state.rows.length} wallets worth a closer look · sorted by ${$('sort').selectedOptions[0].textContent}`;
}
function resetResults() {
  state.result = null; state.rows = []; state.filterVersion++;
  ['total-tokens','total-candidates','total-profiles','total-discovery','total-researchable','total-passed'].forEach(id => {$(id).textContent = '—';});
  $('rows').innerHTML = '<tr><td colspan="8" class="empty">Finding wallets behind the selected active tokens…</td></tr>';
  if ($('quality-debug')) $('quality-debug').replaceChildren();
  $('tokens').replaceChildren(); $('warnings').textContent = ''; $('cache-status').textContent = '';
  $('requests').textContent = 'API credits used this scan: unavailable';
  $('shortlist-note').textContent = 'The shortlist will appear when wallet analysis finishes.';
  closeDrawer();
}
function scan(refresh = false) {
  if (state.busy) return;
  showError(''); resetResults(); setBusy(true); stage('tokens');
  $('status').textContent = 'Finding active tokens…';
  const params = new URLSearchParams({chain:$('chain').value, source:$('source').value, n_tokens:$('count').value, refresh:String(refresh), max_trades:$('filters').elements.max_trades.value.trim() || '-1'});
  const stream = new EventSource(`/api/research/scan?${params}`); state.stream = stream;
  const stop = () => { stream.close(); state.stream = null; setBusy(false); };
  stream.addEventListener('progress', event => {
    const data = JSON.parse(event.data); $('status').textContent = data.message;
    if (data.message.startsWith('Finding traders')) stage('traders');
    if (data.message.startsWith('Profiling') || data.message.startsWith('Analysed')) stage('profiles');
    if (data.message.startsWith('Applying')) stage('filters');
    if (data.candidates != null) $('total-candidates').textContent = data.candidates;
  });
  stream.addEventListener('tokens', event => {const data = JSON.parse(event.data); tokens(data); $('total-tokens').textContent = data.length;});
  stream.addEventListener('result', event => {
    state.result = normalizeResearchPool(JSON.parse(event.data)); const result = state.result;
    renderQualityDebug(result);
    tokens(result.tokens); $('total-tokens').textContent = result.tokens.length;
    $('total-candidates').textContent = result.candidate_count; $('total-profiles').textContent = result.profiled_count;
    $('cache-status').textContent = result.cached_at ? `Cached ${age(result.cached_at)}` : 'Fresh scan';
    $('requests').textContent = `API credits: unavailable · ${result.successful_requests} successful API requests this scan`;
    $('total-discovery').textContent = result.preselection?.eligible_count ?? '—';
    $('total-researchable').textContent = result.researchable_count;
    const incomplete = (result.profile_rows || result.rows).filter(r => r.error || Object.values(r.status || {}).some(status => status !== 'ok')).length;
    const selection = result.preselection;
    const qualityNotes = result.quality_excluded ? [`Post-profile quality gate: ${result.quality_excluded.length} excluded; ${result.stop_reason}. Gate uses the Maximum trades value when the scan starts; later filter edits only narrow the saved research pool.`] : [];
    const selectionNotes = selection ? [`App-derived pre-selection: ${selection.eligible_count} eligible; ${selection.excluded_count} excluded. ${Object.entries(selection.reasons).map(([reason,count])=>`${count}: ${reason}`).join('; ')}. Counts may overlap.`] : [];
    $('warnings').textContent = [...qualityNotes, ...selectionNotes, ...(result.warnings || []), ...(incomplete ? [`${incomplete} profiles have missing sections; available evidence is retained.`] : []), ...(!result.tokens.length ? ['No active tokens returned for this source. Try another source.'] : []), ...(result.tokens.length && !result.candidate_count ? ['No wallet candidates returned. Try another source or network.'] : [])].join(' ');
    stop(); applyFilters();
  });
  stream.addEventListener('fail', event => {stop(); showError(JSON.parse(event.data).error); $('status').textContent = 'Scan could not finish';});
  stream.onerror = () => {stop(); showError('The scan connection was interrupted. Click Scan to retry.'); $('status').textContent = 'Scan interrupted';};
}
function sectionState(status) {
  if (status === 'unavailable') return 'Unavailable for this network';
  if (status === 'locked') return 'This feature requires CoinGecko Analyst API access.';
  if (status === 'error') return 'This section could not be loaded. Other wallet data remains available.';
  return 'No data returned.';
}
function metric(label, value) {return `<div><span>${esc(label)}</span><b>${esc(value)}</b></div>`;}
function drawWallet(row) {
  const status = row.status || {};
  const tradeRows = row.recent_trades || [];
  const holdings = row.holdings || [];
  const tags = [...new Set([...(row.tags || []), row.label, row.likely_bot ? 'likely bot' : null, row.known_infra || (row.is_infra ? 'likely infrastructure' : null)].filter(Boolean))];
  $('drawer-content').innerHTML = `<div class="eyebrow">WALLET EVIDENCE</div><h2 id="drawer-title">Wallet details</h2><p class="address">${esc(row.address)}</p><button data-copy="${esc(row.address)}" type="button">Copy full address</button>
  <div class="metrics">${metric('Research Score · app-derived', row.research_score ?? '—')}${metric('Realized PnL', money(row.realized_pnl_usd))}${metric('Unrealized PnL', money(row.unrealized_pnl_usd))}${metric('Win rate · app-derived', pct(row.win_rate))}${metric('Trades · returned sample', row.trades ?? '—')}${metric('Last active', age(row.last_seen_ts))}</div>
  <h3>CoinGecko API data</h3><p class="note">Network scanned: ${esc(row.chain)}<br>Chains active on: ${esc((row.active_chains || []).join(', ') || 'Unavailable')}<br>Underlying PnL, swaps and holdings are supplied by CoinGecko. Last activity is the latest observed trade in the returned wallet or pool history.</p>
  <h3>Seen in this scan · app-derived</h3><div class="tags">${(row.seen_in || []).map(t=>`<span class="tag" title="${esc(t.address)}">${esc(t.symbol)}</span>`).join('') || '<span class="muted">No token evidence returned.</span>'}</div>
  <h3>Metric basis &amp; app-derived analysis</h3><p class="note">Realized PnL: ${esc(row.pnl_basis)}.<br>Win rate: ${esc(row.win_rate_basis)}.<br>Trades: ${esc(row.trades_basis)}.<br>PnL sanity checks exclude implausible outliers; totals may then represent only the returned token sample.<br>Research Score: 50% win rate + 30% positive realized PnL (capped at $50,000) + 20% sample size (capped at 20). Win rate uses FIFO matched lots, falling back to sold tokens when there are no matches. Sample size is the larger of matched lots and sold tokens. Computed by this app from CoinGecko API data.</p>
  <div class="tags">${tags.map(t=>`<span class="tag">${esc(t.replaceAll('_',' '))}</span>`).join('')}</div><p class="note">Labels and bot flags are local heuristics, not official CoinGecko classifications. ${esc(row.infra_note || '')}</p>
  ${row.error ? `<p class="unavailable">${esc(row.error)}</p>` : ''}
  ${status.pnl !== 'ok' ? `<p class="unavailable">PnL: ${esc(sectionState(status.pnl))}</p>` : ''}
  <h3>Recent trades · CoinGecko API</h3>${status.trades !== 'ok' ? `<p class="unavailable">${esc(sectionState(status.trades))}</p>` : tradeRows.length ? `<div class="table-scroll"><table><thead><tr><th>Time</th><th>Trade</th><th>USD</th><th>Token addresses</th></tr></thead><tbody>${tradeRows.slice(0,30).map(t=>`<tr><td>${esc(t.ts || '—')}</td><td>${esc(t.kind || '—')}</td><td>${esc(money(t.usd))}</td><td title="${esc(t.from_token)} → ${esc(t.to_token)}">${esc(short(t.from_token || '—'))} → ${esc(short(t.to_token || '—'))}</td></tr>`).join('')}</tbody></table></div>` : '<p class="unavailable">No recent trades returned.</p>'}
  <h3>Current holdings · CoinGecko API</h3>${status.balances !== 'ok' ? `<p class="unavailable">${esc(sectionState(status.balances))}</p>` : holdings.length ? `<div class="table-scroll"><table><thead><tr><th>Token</th><th>Network</th><th>Balance</th><th>Value</th></tr></thead><tbody>${holdings.slice(0,30).map(h=>`<tr><td>${esc(h.symbol || short(h.address || '—'))}</td><td>${esc(h.network)}</td><td>${esc(h.balance ?? '—')}</td><td>${esc(money(h.value_usd))}</td></tr>`).join('')}</tbody></table></div>` : '<p class="unavailable">No holdings returned above the $1 API query minimum.</p>'}
  <p class="note">Details come from this scan's cached profile. Refresh from API to request new data.</p>`;
}
async function openWallet(address, trigger) {
  const version = ++state.drawerVersion; state.returnFocus = trigger;
  $('drawer-overlay').hidden = false; document.body.style.overflow = 'hidden';
  $('drawer-content').innerHTML = '<h2 id="drawer-title">Wallet details</h2><p>Loading saved wallet evidence…</p>';
  $('close-drawer').focus();
  try {
    const row = await api(`/api/research/wallet/${encodeURIComponent(address)}?run_id=${encodeURIComponent(state.result.run_id)}`);
    if (version === state.drawerVersion) drawWallet(row);
  } catch (error) {if (version === state.drawerVersion) $('drawer-content').innerHTML = `<h2 id="drawer-title">Wallet details</h2><p class="unavailable">${esc(error.message)}</p>`;}
}
function closeDrawer() {
  state.drawerVersion++; $('drawer-overlay').hidden = true; document.body.style.overflow = '';
  if (state.returnFocus?.isConnected) state.returnFocus.focus();
}
async function copyAddress(button) {
  try { await navigator.clipboard.writeText(button.dataset.copy); const label = button.textContent; button.textContent = 'Copied'; setTimeout(()=>{button.textContent=label;},1500); }
  catch (_) {showError('Copy unavailable. Select the full address in the wallet details.');}
}
$('scan').addEventListener('click', ()=>scan(false)); $('refresh').addEventListener('click', ()=>scan(true));
$('filters').addEventListener('submit', event=>{event.preventDefault();showError('');applyFilters();});
$('reset').addEventListener('click', ()=>{$('filters').reset();if (!state.busy) applyFilters();});
$('sort').addEventListener('change', renderRows);
['chain','source','count'].forEach(id => $(id).addEventListener('change', ()=>{
  resetResults(); $('apply').disabled = true;
  $('status').textContent = 'Selection changed. Click Scan to research this token set.';
  $('rows').innerHTML = '<tr><td colspan="8" class="empty">Click Scan to analyse the selected network and source.</td></tr>';
  document.querySelectorAll('.pipeline li').forEach(li=>li.classList.remove('active'));
}));
$('rows').addEventListener('click', event=>{const copy=event.target.closest('[data-copy]'); if(copy) return copyAddress(copy); const link=event.target.closest('[data-wallet]'); if(link) openWallet(link.dataset.wallet,link);});
$('drawer-content').addEventListener('click', event=>{const button=event.target.closest('[data-copy]');if(button) copyAddress(button);});
$('close-drawer').addEventListener('click', closeDrawer);
$('drawer-overlay').addEventListener('click', event=>{if(event.target===$('drawer-overlay')) closeDrawer();});
document.addEventListener('keydown', event=>{
  if ($('drawer-overlay').hidden) return;
  if (event.key==='Escape') closeDrawer();
  if (event.key==='Tab') {
    const elements=[...$('drawer').querySelectorAll('button,a[href],input,select,[tabindex="0"]')];
    const first=elements[0],last=elements.at(-1);
    if(event.shiftKey && document.activeElement===first){event.preventDefault();last.focus();}
    else if(!event.shiftKey && document.activeElement===last){event.preventDefault();first.focus();}
  }
});
window.addEventListener('pagehide', ()=>state.stream?.close());
(async ()=>{
  try {
    const config = await api('/api/research/config');
    $('chain').innerHTML = config.chains.map(c=>`<option value="${esc(c.id)}">${esc(c.name)}</option>`).join('');
    $('chain').value = config.default_chain;
    $('source').innerHTML = Object.entries(config.sources).map(([key,label])=>`<option value="${esc(key)}">${esc(label)}</option>`).join('');
    $('status').textContent = config.key_configured ? 'Ready to research · Base / Trending 1h' : 'Configure your .env to run a real scan';
    if (!config.key_configured) showError('CoinGecko API key is not configured. Create .env in the project folder, then restart the app.');
    $('scan').disabled = !config.key_configured; $('refresh').disabled = !config.key_configured;
  } catch(error) {showError(error.message);$('status').textContent='Configuration could not load';}
})();
