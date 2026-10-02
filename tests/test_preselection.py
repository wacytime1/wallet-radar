import pytest
from app.research import Filters, passes, select_candidates


def candidate(address, **kw):
    return dict(address=address, tokens_seen_in=1, recently_active=False,
                realized_seen_usd=0, trades_seen=0, recent_trades_seen=0,
                likely_bot=False, known_infra=None) | kw


def test_obvious_machines_never_fill_profile_slots():
    rows = [candidate(f'bot{i}', likely_bot=True, tokens_seen_in=10, realized_seen_usd=1e9) for i in range(35)]
    rows += [candidate('human', tokens_seen_in=1)]
    result = select_candidates(rows)
    assert [r['address'] for r in result['selected']] == ['human']
    assert result['excluded_count'] == 35
    assert len(rows) == 36  # raw discovery count remains unchanged


@pytest.mark.parametrize('signals', [dict(known_infra='Router'), dict(likely_bot=True),
    dict(trades_seen=5001), dict(recent_trades_seen=21)])
def test_each_cheap_exclusion_has_debug_reason(signals):
    result = select_candidates([candidate('excluded', **signals)])
    assert result['selected'] == []
    assert result['excluded'][0]['reasons']


def test_thresholds_unknown_counts_and_rank():
    result = select_candidates([candidate('single', realized_seen_usd=1e6),
        candidate('inactive', tokens_seen_in=2),
        candidate('active', tokens_seen_in=2, recently_active=True),
        candidate('boundary', trades_seen=5000, recent_trades_seen=20),
        {'address':'unknown'}], limit=2)
    assert [r['address'] for r in result['selected']] == ['active','inactive']
    assert result['eligible_count'] == 5
    assert result['excluded_count'] == 0


def test_all_excluded_means_no_full_profiles():
    assert select_candidates([candidate('router',known_infra='Router')])['selected'] == []


def test_optional_maximum_trades():
    row=dict(realized_pnl_usd=20000,win_rate=.6,trades=5000,tokens_seen_in=2,days_since_last_trade=1)
    assert passes(row,Filters())
    row['trades']=5001
    assert not passes(row,Filters())
    assert passes(row,Filters(max_trades=None))
    assert not passes(row,Filters(max_trades=100))


async def test_pipeline_does_not_profile_excluded_candidates(monkeypatch):
    from app import research, profile
    research._cache.clear()
    class Client:
        credits_used = 0
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
    async def tokens(*args): return []
    async def discovery(*args): return {'candidates':[candidate('router',known_infra='Router'),candidate('bot',likely_bot=True)]}
    async def forbidden(*args): pytest.fail('Excluded candidates must not consume a wallet-profile request')
    monkeypatch.setattr(research,'CoinGeckoClient',Client)
    monkeypatch.setattr(research.scan,'_tokens_from_pools',tokens)
    monkeypatch.setattr(research.scan,'_wallets_for_tokens',discovery)
    monkeypatch.setattr(profile,'_fetch_core',forbidden)
    try:
        events=[event async for event in research.stream('base','trending_1h',8)]
        result=events[-1][1]
        assert result['candidate_count']==2
        assert result['profiled_count']==0
        assert result['preselection']['excluded_count']==2
        assert result['rows']==[]
    finally:
        research._cache.clear()
        research._runs.clear()
