// Pure filtering over profiles already returned by the scan. No fetch or scan calls.
function filterCachedProfiles(rows, filters, now = Date.now() / 1000) {
  const rejectedBy = {};
  const selected = [];
  for (const original of rows) {
    const row = {...original, days_since_last_trade: original.last_seen_ts ? Math.max(0, (now-original.last_seen_ts)/86400) : null};
    const reasons = [];
    for (const [key,limit,strict] of [['realized_pnl_usd',filters.min_pnl,true],['win_rate',filters.min_win_rate,false],['trades',filters.min_trades,false],['tokens_seen_in',filters.min_tokens,false]]) {
      const value = row[key];
      if (value == null || !Number.isFinite(value)) reasons.push(`missing ${key}`);
      else if (strict ? value <= limit : value < limit) reasons.push(key);
    }
    const maxTrades = filters.max_trades === undefined ? 5000 : filters.max_trades;
    if(maxTrades !== null && row.trades != null && row.trades > maxTrades) reasons.push('maximum trades');
    const days=row.days_since_last_trade;
    if(days == null) reasons.push('missing activity');
    else if(days > filters.max_days) reasons.push('inactivity');
    if(filters.recently_active && (days == null || days > 3)) reasons.push('recent activity');
    if(filters.exclude_bots && (row.likely_bot || row.label === 'bot_like' || row.style === 'bot')) reasons.push('likely bots');
    if(filters.exclude_infra && (row.known_infra || row.is_infra)) reasons.push('infrastructure');
    if(row.error) reasons.push('profile error');
    for (const reason of reasons) rejectedBy[reason] = (rejectedBy[reason] || 0) + 1;
    if(!reasons.length) selected.push(row);
  }
  selected.sort((a,b)=>(b.research_score ?? 0)-(a.research_score ?? 0));
  return {rows:selected,passed_count:selected.length,evaluated_count:rows.length,applied_filters:{...filters},rejected_by:rejectedBy};
}

// Normalize both modern and legacy cached payloads before counting or user filtering.
function normalizeResearchPool(result) {
  const raw = result.profile_rows || result.rows || [];
  const maximum = result.quality_max_trades === undefined ? 5000 : result.quality_max_trades;
  const diagnostics = raw.map(row => {
    const reasons = [];
    const bot = Boolean(row.likely_bot || row.label === 'bot_like' || row.style === 'bot');
    const infra = Boolean(row.known_infra || row.is_infra || row.label === 'protocol');
    if(bot) reasons.push('likely bot');
    if(infra) reasons.push('infrastructure');
    if(row.error) reasons.push('profile error');
    if(row.trades == null) reasons.push('trade count unavailable');
    else if(maximum !== null && row.trades > maximum) reasons.push('maximum trades research preference');
    const stored = row.quality_debug || {};
    return {...stored, address:row.address, likely_bot:bot,
      likely_bot_reasons:stored.likely_bot_reasons || [...(row.discovery_bot_reasons || []), ...(row.likely_bot && !row.discovery_bot_reasons ? ['legacy discovery flag; source not retained'] : []), ...(row.label==='bot_like' ? [`bot_like label: sampled rate=${row.recent_trades_per_day ?? row.activity?.recent_trades_per_day} >50/day`] : []), ...(row.style==='bot' ? [`bot style: sampled rate>50/day OR gap<=10s with >=20 swaps; rate=${row.activity?.recent_trades_per_day}, gap=${row.activity?.median_seconds_between_trades}, sample=${row.activity?.recent_trades}`] : [])],
      infrastructure:infra, infrastructure_reasons:stored.infrastructure_reasons || (infra ? [row.infra_note || row.known_infra || 'protocol label / is_infra flag; raw reason not retained'] : []),
      trade_count:row.trades ?? null,trade_count_source:row.trades_basis || 'Legacy count: source not recorded',
      maximum_trades:maximum,excluded_by_quality_gate:reasons,researchable:reasons.length===0};
  });
  const rows=raw.filter((row,index)=>diagnostics[index].researchable);
  return {...result, pool_version:2,profile_rows:raw,rows,researchable_count:rows.length,
    quality_max_trades:maximum,quality_debug:diagnostics,
    quality_excluded:diagnostics.filter(d=>!d.researchable).map(d=>({address:d.address,reasons:d.excluded_by_quality_gate}))};
}
