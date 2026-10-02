"""Offline research evidence, thresholds, cache and bounded orchestration tests."""
import time
import pytest
from app import research, profile
from core.client import CoinGeckoError, PlanRestrictedError


def good(**changes):
    return dict(realized_pnl_usd=20000, win_rate=.55, trades=20,
                days_since_last_trade=14, tokens_seen_in=2, research_score=60,
                last_seen_ts=time.time(), **{}) | changes


@pytest.mark.parametrize('changes', [
    {'win_rate': .54}, {'realized_pnl_usd': 0}, {'realized_pnl_usd': -1},
    {'trades': 4}, {'days_since_last_trade': 30.01}, {'tokens_seen_in': 1},
    {'likely_bot': True}, {'label': 'bot_like'}, {'style': 'bot'},
    {'known_infra': 'Router'}, {'is_infra': True}, {'error': 'Unavailable'},
])
def test_default_threshold_rejects(changes):
    assert not research.passes(good(**changes), research.Filters())


def test_inclusive_thresholds_and_strict_positive_pnl():
    assert research.passes(good(), research.Filters())
    assert not research.passes(good(realized_pnl_usd=100), research.Filters(min_pnl=100))
    assert research.passes(good(realized_pnl_usd=101), research.Filters(min_pnl=100))


@pytest.mark.parametrize('field', ['realized_pnl_usd', 'win_rate', 'trades', 'days_since_last_trade', 'tokens_seen_in'])
def test_missing_required_evidence(field):
    assert not research.passes(good(**{field: None}), research.Filters())
    row = good()
    row.pop(field)
    assert not research.passes(row, research.Filters())


def test_optional_missing_sections_do_not_exclude():
    assert research.passes(good(status={'balances': 'unavailable'}, holdings=[]), research.Filters())


def test_toggle_overrides():
    assert research.passes(good(likely_bot=True, known_infra='Router'), research.Filters(exclude_bots=False, exclude_infra=False))
    assert not research.passes(good(days_since_last_trade=4), research.Filters(recently_active=True))
    assert research.passes(good(days_since_last_trade=3), research.Filters(recently_active=True))


def test_lifetime_evidence_not_partial_fifo_or_unknown_zero(monkeypatch):
    monkeypatch.setattr(research.time, 'time', lambda: 100000)
    row = research.evidence({'recent_last_ts': 99900}, {
        'pnl_features': {'available': True, 'lifetime_realized_pnl_usd': 500, 'total_buys': 8, 'total_sells': 7},
        'match_metrics': {'trades': 2, 'realized_pnl_usd': 20}, 'last_seen_ts': 90000, 'skill_score': 75})
    assert row['realized_pnl_usd'] == 500
    assert row['trades'] == 15
    assert row['last_seen_ts'] == 99900
    unknown = research.evidence({}, {})
    assert unknown['realized_pnl_usd'] is None
    assert unknown['trades'] is None
    assert unknown['research_score'] is None


def test_fifo_fallback():
    row = research.evidence({}, {'match_metrics': {'trades': 2, 'realized_pnl_usd': 20},
                                 'status': {'trades': 'ok'}, 'recent_trades': [{}, {}, {}]})
    assert row['realized_pnl_usd'] == 20
    assert row['trades'] == 3
    assert 'FIFO' in row['pnl_basis']


def test_shortlist_recalculates_inactivity_and_sorts(monkeypatch):
    monkeypatch.setattr(research.time, 'time', lambda: 2000000)
    rows = [good(address='low', research_score=40, last_seen_ts=1999999),
            good(address='high', research_score=80, last_seen_ts=1999999),
            good(address='old', last_seen_ts=-1000000)]
    assert [r['address'] for r in research.shortlist({'rows': rows}, research.Filters())] == ['high', 'low']


@pytest.mark.parametrize('error, phrase', [(CoinGeckoError(429, 'secret'), 'rate limit'),
    (PlanRestrictedError(403, 'secret'), 'Analyst'), (CoinGeckoError(404, 'secret'), 'network'),
    (RuntimeError('secret'), 'could not')])
def test_errors_do_not_leak_details(error, phrase):
    assert phrase in research.safe_error(error)
    assert 'secret' not in research.safe_error(error)


async def test_stream_caps_profiles_retains_failure_and_uses_cache(monkeypatch):
    research._cache.clear()
    research._runs.clear()
    class Client:
        credits_used = 0
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
    monkeypatch.setattr(research, 'CoinGeckoClient', Client)
    async def tokens(*args): return [{'address': 'token', 'symbol': 'T'}]
    async def discover(*args):
        return {'candidates': [dict(address=str(i), known_infra=None, likely_bot=False,
            tokens_seen_in=2, recently_active=True, realized_seen_usd=100) for i in range(40)]}
    calls = []
    async def fetch(client, chain, address):
        calls.append(address)
        if address == '0': raise RuntimeError('private details')
        return {'pnl': {}, 'trades': [], 'balances': {},
                'status': {'pnl': 'unavailable', 'trades': 'ok', 'balances': 'unavailable'}}
    monkeypatch.setattr(research.scan, '_tokens_from_pools', tokens)
    monkeypatch.setattr(research.scan, '_wallets_for_tokens', discover)
    monkeypatch.setattr(profile, '_fetch_core', fetch)
    events = [e async for e in research.stream('base', 'trending_1h', 8)]
    result = events[-1][1]
    assert len(calls) == 40
    assert len(result['rows']) == 39
    assert len(result['profile_rows']) == 40
    assert result['candidate_count'] == 40
    assert result['profiled_count'] == 40
    assert any(row.get('error') for row in result['profile_rows'])
    assert 'private details' not in str(result)
    cached = [e async for e in research.stream('base', 'trending_1h', 8)]
    assert len(calls) == 40
    assert cached[0][1]['successful_requests'] == 0
    assert cached[0][1]['cached_at']
    assert research.get_run(result['run_id'])
    refreshed = [e async for e in research.stream('base', 'trending_1h', 8, refresh=True)]
    assert len(calls) == 80
    assert refreshed[-1][1]['run_id'] != result['run_id']
    research._cache.clear()
    research._runs.clear()


def test_research_routes_offline(monkeypatch):
    from fastapi.testclient import TestClient
    from app import server
    async def probe(client): return {'analyst': True, 'paid': True}
    monkeypatch.setattr(server, 'probe_capabilities', probe)
    monkeypatch.setattr(server.config.core_config, 'API_KEY', '')
    row = good(address='0x' + '1'*40)
    research._runs['test'] = (time.time(), {'rows': [row]})
    try:
        with TestClient(server.app) as client:
            config = client.get('/api/research/config').json()
            assert config['default_chain'] == 'base'
            assert len(config['chains']) > 1
            assert config['key_configured'] is False
            assert client.get('/api/research/scan?chain=unknown').status_code == 400
            result = client.get('/api/research/scan')
            assert 'CoinGecko API key is not configured.' in result.text
            assert client.post('/api/research/shortlist', json={'run_id':'test'}).json()['passed_count'] == 1
            assert client.post('/api/research/shortlist', json={'run_id':'test', 'filters':{'min_win_rate':1.1}}).status_code == 422
            assert client.post('/api/research/shortlist', json={'run_id':'missing'}).status_code == 404
            assert client.get('/api/research/wallet/' + row['address'], params={'run_id':'test'}).json()['address'] == row['address']
            assert client.get('/api/research/wallet/unknown', params={'run_id':'test'}).status_code == 404
    finally:
        research._runs.clear()


async def test_profile_cache_and_unsupported_balances(monkeypatch):
    profile._cache.clear()
    calls = []
    class Client:
        async def wallet_pnl(self, *args, **kwargs):
            calls.append('pnl')
            return {'total_tokens': 1}
        async def wallet_trades(self, *args, **kwargs):
            calls.append('trades')
            return []
        async def wallet_balances(self, *args, **kwargs):
            calls.append('balances')
            return {}
    try:
        first = await profile._fetch_core(Client(), 'solana', 'offline-wallet')
        second = await profile._fetch_core(Client(), 'solana', 'offline-wallet')
        assert first == second
        assert calls == ['pnl', 'trades']
        assert first['status']['balances'] == 'unavailable'
    finally:
        profile._cache.clear()


def test_run_expires(monkeypatch):
    monkeypatch.setattr(research.time, 'time', lambda: 2000)
    research._runs['expired'] = (0, {'rows': []})
    try:
        assert research.get_run('expired') is None
    finally:
        research._runs.clear()

