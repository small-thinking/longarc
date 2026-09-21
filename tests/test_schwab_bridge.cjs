const assert = require('node:assert/strict');
const {test} = require('node:test');
const {collectSchwabChain} = require('../scripts/collect_schwab_chain.js');

function browser({failCenter=null, emptyReads=0, domTables=null,
  url='https://client.schwab.com/retail/research/#/etfs/options/QQQ'}={}) {
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
    evaluateAll:async(fn)=>++reads<=emptyReads?[]:domTables?fn(domTables):[table],
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

test('explicit IAU and third-symbol captures preserve requested identity',async()=>{
  for (const symbol of ['IAU','SPY']) {
    const b=browser({url:`https://client.schwab.com/retail/research/#/etfs/options/${symbol}`});
    const r=await collectSchwabChain(b.tab,{
      symbol,expiries:['2026-10-16'],strikeCenters:[105],
    },b.observe);
    assert.equal(r.symbol,symbol);
    assert.equal(r.slices.length,1);
    assert.deepEqual(r.errors,[]);
  }
});

test('symbol route must match exactly before any UI interaction',async()=>{
  for (const url of [
    'https://client.schwab.com/retail/research/#/etfs/options/QQQ',
    'https://client.schwab.com/retail/research/#/etfs/options/IAUX',
    'https://client.schwab.com/retail/research/#/etfs/options/IAU/extra',
    'https://client.schwab.com/retail/research/#/etfs/options/iau',
    'https://client.schwab.com/login?next=/etfs/options/IAU',
    'https://example.com/retail/research/#/etfs/options/IAU',
  ]) {
    const b=browser({url});
    const r=await collectSchwabChain(b.tab,{
      symbol:'IAU',expiries:['2026-10-16'],strikeCenters:[105],
    },b.observe);
    assert.equal(r.errors[0].code,'setup_failed_check_login_and_page_structure',url);
    assert.equal(r.slices.length,0);
    assert.deepEqual(b.actions,[]);
  }
});

test('default QQQ rejects prefix matches',async()=>{
  const b=browser({url:'https://client.schwab.com/retail/research/#/etfs/options/QQQM'});
  const r=await collectSchwabChain(b.tab,{
    expiries:['2026-10-16'],strikeCenters:[105],
  },b.observe);
  assert.equal(r.symbol,'QQQ');
  assert.equal(r.slices.length,0);
  assert.deepEqual(b.actions,[]);
});

test('invalid explicit symbols fail before UI interaction',async()=>{
  for (const symbol of [null,'','iau',' IAU','IAU\n','IAU/QQQ',123,'A'.repeat(16),'1ABC']) {
    const b=browser();
    await assert.rejects(collectSchwabChain(b.tab,{
      symbol,expiries:['2026-10-16'],strikeCenters:[105],
    },b.observe),/symbol/);
    assert.deepEqual(b.actions,[]);
  }
});

test('accordion-section heading identifies IAU tables without legacy h2',async()=>{
  const rows=[['Strike','Bid','Ask'],['105','1','1.1']].map(cells=>({
    innerText:cells.join('\t'),
    querySelectorAll:()=>cells.map(innerText=>({innerText})),
  }));
  const table={
    closest:(selector)=>selector==='pf3-sdps-accordion-section'
      ? {innerText:'Oct. 16, 2026 (Fri: 26 days) Toggle\nStrike\n105'}
      : {id:'chains-accord-control-1',parentElement:{querySelector:()=>null}},
    querySelectorAll:()=>rows,
  };
  const b=browser({domTables:[table],
    url:'https://client.schwab.com/retail/research/#/etfs/options/IAU'});
  const r=await collectSchwabChain(b.tab,{
    symbol:'IAU',expiries:['2026-10-16'],strikeCenters:[105],
  },b.observe);
  assert.deepEqual(r.errors,[]);
  assert.equal(r.slices.length,1);
  assert.deepEqual(r.slices[0].headers,['Strike','Bid','Ask']);
  assert.deepEqual(r.slices[0].rows,[['105','1','1.1']]);
});
