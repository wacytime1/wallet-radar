"""Legacy payloads must never inflate the quality-gated research pool."""
import json
import os
from pathlib import Path
import subprocess
import time

from app import research


def machine_rows():
    return [dict(address=str(i),trades=100000,likely_bot=True,label='protocol',is_infra=True,
                 realized_pnl_usd=None,trades_basis='Buys + sells in returned PnL token sample') for i in range(30)]


def test_legacy_thirty_researchable_is_corrected_to_zero():
    payload={'rows':machine_rows(),'researchable_count':30,'profiled_count':30}
    result=research.normalize_pool(payload)
    assert result['researchable_count']==0
    assert result['rows']==[]
    assert result['profiled_count']==30
    assert len(result['quality_debug'])==30
    for debug in result['quality_debug']:
        assert debug['likely_bot'] and debug['infrastructure']
        assert debug['trade_count']==100000
        assert not debug['researchable']
        assert 'maximum trades research preference' in debug['excluded_by_quality_gate']
        assert debug['likely_bot_reasons'] and debug['infrastructure_reasons']


def test_relaxed_user_filters_cannot_restore_quality_excluded_wallets():
    payload={'rows':machine_rows()}
    assert research.shortlist(payload,research.Filters(min_pnl=-1,min_win_rate=0,min_trades=0,
        max_trades=None,max_days=999,min_tokens=0,exclude_bots=False,exclude_infra=False))==[]


def test_missing_pnl_is_not_a_quality_gate_rule():
    row={'address':'researchable','trades':10,'realized_pnl_usd':None,'last_seen_ts':time.time()}
    result=research.normalize_pool({'rows':[row]})
    assert result['researchable_count']==1
    assert research.shortlist(result,research.Filters())==[]


def test_frontend_legacy_counter_and_pool_agree(tmp_path):
    node=os.environ.get('WALLET_RADAR_NODE')
    if not node:
        import pytest
        pytest.skip('Node required')
    root=Path(__file__).resolve().parents[1]
    fixture=tmp_path/'legacy.json';fixture.write_text(json.dumps({'rows':machine_rows(),'researchable_count':30}))
    script=tmp_path/'check.cjs'
    script.write_text("""
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const ctx=vm.createContext({Date,Number,Boolean,Object});
vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),ctx);
ctx.payload=JSON.parse(fs.readFileSync(process.argv[3],'utf8'));
const result=vm.runInContext('normalizeResearchPool(payload)',ctx);
assert.equal(result.researchable_count,0);assert.equal(result.rows.length,0);
assert.equal(result.quality_debug.length,30);
assert.ok(result.quality_debug.every(d=>!d.researchable && d.likely_bot && d.infrastructure));
""")
    result=subprocess.run([node,str(script),str(root/'web/research-filters.js'),str(fixture)],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
