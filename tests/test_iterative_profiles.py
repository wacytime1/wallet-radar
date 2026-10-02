import pytest
from app import research, profile


def row(i, trades=100, **kw):
    return {'address':str(i), 'trades':trades, 'status':{'pnl':'ok'}, **kw}


async def run_pool(count, bad_until=0, **options):
    calls=[]
    async def fetch(c):
        calls.append(c['address'])
        return row(c['address'], trades=100000 if int(c['address'])<bad_until else 100)
    events=[e async for e in research.profile_batches([{'address':str(i)} for i in range(count)],fetch,**options)]
    return events[-1][1],calls


async def test_continue_after_machine_dominated_batch():
    pool,calls=await run_pool(74,bad_until=30)
    assert len(calls)==74
    assert pool['researchable_count']==44
    assert pool['profiled_count']==74
    assert len(pool['quality_excluded'])==30


async def test_stop_after_batch_reaches_target():
    pool,calls=await run_pool(100)
    assert len(calls)==60
    assert pool['researchable_count']==60
    assert pool['stop_reason']=='target reached'


async def test_hard_cap_even_when_all_rejected():
    pool,calls=await run_pool(400,bad_until=400,cap=900)
    assert len(calls)==300
    assert pool['profiled_count']==300
    assert pool['researchable_count']==0
    assert pool['stop_reason']=='profile cap reached'


async def test_do_not_force_fill_when_candidates_exhausted():
    pool,calls=await run_pool(8)
    assert pool['researchable_count']==8
    assert pool['stop_reason']=='candidates exhausted'


@pytest.mark.parametrize('change', [{'trades':5001}, {'is_infra':True}, {'label':'bot_like'}, {'style':'bot'}, {'trades':None}])
def test_post_profile_gate(change):
    assert research.quality_reasons(row(1,**change)) if 'trades' not in change else research.quality_reasons(row(1,change['trades']))


def test_activity_preference_can_be_disabled():
    assert not research.quality_reasons(row(1,5000))
    assert not research.quality_reasons(row(1,100000),max_trades=None)


async def test_profiles_cached_across_batched_passes():
    profile._cache.clear()
    calls=[]
    class Client:
        async def wallet_pnl(self,*a,**k): calls.append('pnl'); return {}
        async def wallet_trades(self,*a,**k): calls.append('trades'); return []
        async def wallet_balances(self,*a,**k): calls.append('balances'); return {}
    async def fetch(c):
        await profile._fetch_core(Client(),'base',c['address'])
        return row(c['address'])
    candidates=[{'address':str(i)} for i in range(60)]
    try:
        for _ in range(2):
            events=[e async for e in research.profile_batches(candidates,fetch)]
            assert events[-1][1]['researchable_count']==60
        assert len(calls)==180  # only the first pass makes three calls per wallet across two batches
    finally:
        profile._cache.clear()


async def test_deep_batches_never_repeat_and_return_only_valid_pool():
    calls=[]
    async def fetch(c):
        calls.append(c['address'])
        i=int(c['address'])
        return row(i, trades=100 if i>=277 else 100000)
    candidates=[{'address':str(i)} for i in range(400)]
    candidates.insert(35,{'address':'1'})
    events=[e async for e in research.profile_batches(candidates,fetch)]
    pool=events[-1][1]
    assert len(calls)==300
    assert len(set(calls))==300
    assert calls==[str(i) for i in range(300)]
    assert pool['researchable_count']==23
    assert pool['profiled_count']==300
    assert pool['stop_reason']=='profile cap reached'


async def test_first_thirty_does_not_stop_deeper_search():
    pool,calls=await run_pool(100,bad_until=5)
    assert len(calls)==60
    assert pool['researchable_count']==55


def test_strong_defaults_never_relax_to_manufacture_shortlist():
    from app import server
    f=research.Filters()
    assert (f.min_pnl,f.min_win_rate,f.min_trades,f.max_trades,f.max_days,f.min_tokens)==(10000,.55,20,5000,30,2)
    assert f.exclude_bots and f.exclude_infra and not f.recently_active
    assert server.ResearchFilters().model_dump()==f.__dict__
    import time
    weak=row('weak',trades=100,realized_pnl_usd=500,win_rate=.8,tokens_seen_in=2,last_seen_ts=time.time())
    result={'rows':[weak]}
    for _ in range(2):
        assert research.shortlist(result,f)==[]
        assert f.min_pnl==10000
