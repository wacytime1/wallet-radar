"""Exact relaxed-filter regression; all profiles here are offline test fixtures."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

import pytest
from app import research

RELAXED = dict(min_pnl=0, min_win_rate=.5, min_trades=3, max_days=30,
               min_tokens=1, max_trades=5000, recently_active=False, exclude_bots=True, exclude_infra=True)


def profiles():
    now = time.time()
    eligible = dict(address='0x' + '1'*40, realized_pnl_usd=100, win_rate=.5, trades=3,
                    tokens_seen_in=1, last_seen_ts=now-20*86400, research_score=50,
                    label='flipper', seen_in=[{'symbol':'TEST'}])
    return [eligible] + [dict(eligible, address='0x' + f'{i:040x}', likely_bot=True) for i in range(2,31)]


def test_backend_relaxed_filters_use_cached_30_not_defaults(monkeypatch):
    from app import server
    from core.client import CoinGeckoClient
    async def forbidden(*args, **kwargs):
        pytest.fail('Filtering must never call CoinGecko')
    monkeypatch.setattr(CoinGeckoClient, 'get', forbidden)
    research._runs['regression'] = (time.time(), {'rows': profiles(), 'quality_max_trades': None})
    try:
        default = server.research_shortlist(server.ShortlistRequest(run_id='regression'))
        assert default['passed_count'] == 0
        relaxed = server.research_shortlist(server.ShortlistRequest(run_id='regression', filters=RELAXED))
        assert relaxed['passed_count'] == 1
        assert relaxed['evaluated_count'] == 1
        assert relaxed['applied_filters'] == RELAXED
        assert len(research.get_run('regression')['quality_excluded']) == 29
    finally:
        research._runs.pop('regression', None)


@pytest.mark.parametrize("hotfix", [False, True])
def test_actual_submit_handler_updates_counter_subtitle_table_without_fetch(tmp_path, hotfix):
    node = os.environ.get('WALLET_RADAR_NODE') or shutil.which('node')
    if not node:
        pytest.skip('Node required to run the actual frontend event handler')
    root = Path(__file__).resolve().parents[1]
    fixture = tmp_path / 'profiles.json'
    fixture.write_text(json.dumps(profiles()))
    script = r'''
const fs=require('fs'), vm=require('vm'), assert=require('assert/strict');
const rows=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
const elements={}, events={}, requests=[];
function element(id) {
  return elements[id] ||= {id,value:'',textContent:'',innerHTML:'',disabled:false,hidden:true,
    classList:{toggle(){},remove(){}}, selectedOptions:[{textContent:'Research Score'}],
    addEventListener(name,fn){events[id+':'+name]=fn},replaceChildren(){},focus(){}};
}
const form=element('filters');form.reportValidity=()=>true;
form.elements={};
for(const [name,value] of Object.entries({min_pnl:0,min_win_rate:50,min_trades:3,max_days:30,min_tokens:1})) form.elements[name]={value:String(value)};
for(const name of ['exclude_bots','exclude_infra','recently_active']) form.elements[name]={checked:name!=='recently_active'};
element('sort').value='research_score';
const context=vm.createContext({console,Intl,Date,Number,String,Set,URLSearchParams,setTimeout,
 document:{getElementById:element,querySelectorAll:()=>[],addEventListener(){},body:{style:{}}},
 window:{addEventListener(){}},EventSource:function(){throw Error('Unexpected scan')},
 fetch:async(url)=>{requests.push(url);assert.equal(url,'/api/research/config');return {ok:true,json:async()=>({chains:[{id:'base',name:'Base'}],default_chain:'base',sources:{trending_1h:'Trending 1h'},key_configured:true})}}, fixtureRows:rows});
context.window=context; context.addEventListener=()=>{};
vm.runInContext(fs.readFileSync(process.argv[3],'utf8'),context);
vm.runInContext(fs.readFileSync(process.argv[4],'utf8'),context);
(async()=>{
  await new Promise(resolve=>setTimeout(resolve,0));
  vm.runInContext("state.result={run_id:'cached',rows:fixtureRows,quality_max_trades:null};state.busy=false;",context);
  element('total-passed').textContent='0';
  if(process.argv[5]) {
    vm.runInContext("applyFilters=async function(){throw Error('Old server-dependent handler')};",context);
    vm.runInContext(fs.readFileSync(process.argv[5],'utf8'),context);
    await new Promise(resolve=>setTimeout(resolve,0));
  }
  events['filters:submit']({preventDefault(){}});
  await new Promise(resolve=>setTimeout(resolve,0));
  assert.equal(element('total-passed').textContent,1);
  assert.match(element('shortlist-note').textContent,/1 wallets/);
  assert.match(element('rows').innerHTML,/data-wallet=/);
  assert.match(element('status').textContent,/1 \/ 1 saved profiles passed/);
  assert.equal(element('apply').disabled,false);
  assert.deepEqual(requests,['/api/research/config']);
  // The optional activity ceiling is read from the form, and blank disables it.
  form.elements.max_trades={value:'5000'};
  vm.runInContext('state.result.rows[0].trades=5001',context);
  events['filters:submit']({preventDefault(){}});
  assert.equal(element('total-passed').textContent,0);
  assert.match(element('rows').innerHTML,/maximum trades/);
  form.elements.max_trades.value='';
  events['filters:submit']({preventDefault(){}});
  assert.equal(element('total-passed').textContent,1);
  // A genuinely empty result must show that the click ran and explain exclusions.
  form.elements.min_win_rate.value='100';
  events['filters:submit']({preventDefault(){}});
  assert.equal(element('total-passed').textContent,0);
  assert.match(element('rows').innerHTML,/Filters applied/);
  assert.match(element('rows').innerHTML,/win rate/);
  assert.equal(requests.length,1);
  console.log('Actual submit handler: current inputs, count, subtitle, table and zero-result feedback verified; no filtering network requests.');
})().catch(e=>{console.error(e);process.exitCode=1});
'''
    harness = tmp_path / 'frontend.cjs'
    harness.write_text(script)
    command = [node,str(harness),str(fixture),str(root/'web/research-filters.js'),str(root/'web/radar.js')]
    if hotfix:
        command.append(str(root/'web/filter-hotfix.js'))
    result = subprocess.run(command,capture_output=True,text=True,timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
