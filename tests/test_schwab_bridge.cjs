const assert = require('node:assert/strict');
const {test} = require('node:test');
const {collectSchwabChain} = require('../scripts/collect_schwab_chain.js');

function browser({failCenter=null, emptyReads=0, url='https://client.schwab.com/retail/research/#/etfs/options/QQQ'}={}) {
  let center, reads=0, observed=0;
  const actions=[];
  const table={label:'Oct. 16, 2026 (Fri: 26 days) Toggle', cells:[
    ['Strike','Bid','Ask'],['105.00','1.00','1.10'],
  ]};
  const locator=(name)=>({
    selectOption:async({label})=>{
      actions.push(['select',name,label]);
      if (name === 'Strike to center on') {
        if (+label === failCenter) throw new Error('fixture failure');
        center=+label;
      }
    },
    click:async()=>actions.push(['click',String(name)]),
    getAttribute:async()=> 'true',
    allTextContents:async()=>['Oct. 16, 2026 (Fri: 26 days) Toggle'],
    evaluateAll:async()=>++reads<=emptyReads?[]:[table],
  });
  const tab={url:async()=>url,playwright:{
    getByLabel:(name)=>locator(name),getByRole:(role,args={})=>locator(args.name || role),
  }};
  return {tab, observe:async()=>{observed++;}, actions,
    counts:()=>({reads,observed,center})};
}

test('bounded read-only capture visits requested windows; missing expiry is explicit',async()=>{
  const b=browser();
  const r=await collectSchwabChain(b.tab,{
    expiries:['2026-10-16','2026-10-23'],strikeCenters:[105,110],
  },b.observe);
  assert.equal(r.slices.length,2);
  assert.equal(r.errors[0].code,'expiry_not_in_visible_menu');
  assert.ok(b.actions.every(([kind,name])=> kind==='select' || ['Filters','Apply'].includes(name)));
  assert.equal(r.slices[0].quote_at,null);
  assert.equal(r.completeness,'visible_windows_only_not_full_listed_chain');
});

test('empty table reads retry at most three times and preserve good captures',async()=>{
  const b=browser({emptyReads:3});
  const r=await collectSchwabChain(b.tab,{
    expiries:['2026-10-16'],strikeCenters:[105,110],
  },b.observe);
  assert.equal(b.counts().reads,4);
  assert.equal(r.errors[0].code,'empty_table_after_3_reads');
  assert.equal(r.slices.length,1);
});

test('unexpected dialog failure stops interactions and retains prior window',async()=>{
  const b=browser({failCenter:110});
  const r=await collectSchwabChain(b.tab,{
    expiries:['2026-10-16'],strikeCenters:[105,110,115],
  },b.observe);
  assert.equal(r.slices.length,1);
  assert.equal(r.errors[0].code,'window_read_failed');
  assert.ok(!b.actions.some(a=>a[2]==='115'));
});

test('login or wrong page is recorded without trying to log in',async()=>{
  const b=browser({url:'https://client.schwab.com/login'});
  const r=await collectSchwabChain(b.tab,{expiries:['2026-10-16'],strikeCenters:[105]},b.observe);
  assert.equal(r.errors[0].code,'setup_failed_check_login_and_page_structure');
  assert.deepEqual(b.actions,[]);
});
