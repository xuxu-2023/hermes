const { wallet, block, tools } = require('nanocurrency-web');
const RPC_URLS = (process.env.NANO_RPC_URLS || 'https://rainstorm.city/api,https://nanoslo.0x.no/proxy,https://rpc.nano.to').split(',').map(s=>s.trim()).filter(Boolean);
const RPC_KEY = process.env.NANO_RPC_KEY || '';
const REP = 'nano_3arg3asgtigae3xckabaaewkx3bzsh7nwz7jkmjos79ihyaxwphhm6qgjps4';
const ZERO = '0'.repeat(64); const RAW = BigInt('1000000000000000000000000000000');
function xnoToRaw(x){ const [w,f='']=String(x).split('.'); return (BigInt(w||'0')*RAW + BigInt((f+'0'.repeat(30)).slice(0,30))).toString(); }
async function rpc(action, params={}, ms=12000){ const h={'Content-Type':'application/json'}; if(RPC_KEY)h['Authorization']=RPC_KEY; let last;
  for(const url of RPC_URLS){ const c=new AbortController(); const t=setTimeout(()=>c.abort(),ms);
    try{ const r=await fetch(url,{method:'POST',headers:h,body:JSON.stringify({action,...params}),signal:c.signal}); const d=await r.json();
      if(d.error&&d.error!=='Account not found')throw new Error(d.error); return d; }catch(e){ last=e; }finally{ clearTimeout(t); } }
  throw last||new Error('all RPC endpoints failed'); }
async function work(hash, difficulty){ for(let i=0;i<4;i++){ try{ const r=await rpc('work_generate',{hash,difficulty},28000); if(r.work)return r.work; }catch(e){} } throw new Error('work_generate failed'); }
async function info(a){ const r=await rpc('account_info',{account:a,representative:true,pending:true}); if(r.error==='Account not found')return null; return {balance:r.balance||'0',frontier:r.frontier||'',representative:r.representative||''}; }
function valid(a){ if(!a||(!a.startsWith('nano_')&&!a.startsWith('xrb_')))return false; try{return tools.addressToPublicKey(a)!==null;}catch{return false;} }
async function receive(seed){ const w=wallet.fromLegacySeed(seed).accounts[0]; let nfo=await info(w.address); let n=0; const hs=[];
  for(let p=0;p<10;p++){ const pend=await rpc('receivable',{account:w.address,count:'10',source:true}); const e=Object.entries(pend.blocks||{}); if(!e.length)break;
    const [bh,bi]=e[0]; const amount=typeof bi==='string'?bi:bi.amount; const op=nfo&&nfo.frontier;
    const wk=await work(op?nfo.frontier:tools.addressToPublicKey(w.address),'fffffe0000000000');
    const blk=block.receive({walletBalanceRaw:op?nfo.balance:'0',toAddress:w.address,representativeAddress:(op&&nfo.representative)||REP,frontier:op?nfo.frontier:ZERO,transactionHash:bh,amountRaw:amount,work:wk},w.privateKey);
    const pr=await rpc('process',{json_block:'true',subtype:op?'receive':'open',block:blk}); if(pr.hash){hs.push(pr.hash);n++;nfo=await info(w.address);} }
  return {received:n,hashes:hs,balanceRaw:nfo?nfo.balance:'0'}; }
async function send(seed,to,amountRaw){ if(!valid(to))throw new Error('invalid recipient'); if(BigInt(amountRaw)<=0n)throw new Error('amount must be > 0');
  const w=wallet.fromLegacySeed(seed).accounts[0]; let nfo=await info(w.address);
  if(!nfo||BigInt(nfo.balance)<BigInt(amountRaw)){ await receive(seed); nfo=await info(w.address); }
  if(!nfo)throw new Error('account unopened / no funds'); if(BigInt(amountRaw)>BigInt(nfo.balance))throw new Error('insufficient balance');
  const wk=await work(nfo.frontier,'fffffff800000000');
  const signed=block.send({walletBalanceRaw:nfo.balance,fromAddress:w.address,toAddress:to,representativeAddress:nfo.representative||REP,frontier:nfo.frontier,amountRaw:String(amountRaw),work:wk},w.privateKey);
  const res=await rpc('process',{json_block:'true',subtype:'send',block:signed}); if(!res.hash)throw new Error('process failed'); return res.hash; }
(async()=>{ const [cmd,a1,a2]=process.argv.slice(2); const seed=process.env.NANO_SEED||'';
  if(cmd==='new'){ const w=wallet.generateLegacy(); console.log(JSON.stringify({seed:w.seed,address:w.accounts[0].address})); return; }
  if(cmd==='address'){ console.log(wallet.fromLegacySeed(seed).accounts[0].address); return; }
  if(cmd==='balance'){ const a=wallet.fromLegacySeed(seed).accounts[0].address; const i=await info(a); console.log(JSON.stringify({address:a,balanceRaw:i?i.balance:'0',balanceXno:i?Number(BigInt(i.balance)*1000000n/RAW)/1e6:0})); return; }
  if(cmd==='receive'){ console.log(JSON.stringify({ok:true,...(await receive(seed))})); return; }
  if(cmd==='fund'){ const a=wallet.fromLegacySeed(seed).accounts[0].address; const i=await info(a); const amt=a1?xnoToRaw(a1):'';
    console.log(JSON.stringify({address:a,needXno:a1||null,uri:'nano:'+a+(amt?'?amount='+amt:''),balanceXno:i?Number(BigInt(i.balance)*1000000n/RAW)/1e6:0,message:'Ask your owner to fund this address'+(a1?' with about '+a1+' XNO':'')+'.'})); return; }
  if(cmd==='send'){ const hash=await send(seed,a1,a2); console.log(JSON.stringify({ok:true,hash,to:a1,amountRaw:a2})); return; }
  console.error('usage: nano-pay.cjs new | address | balance | receive | fund [amountXno] | send <toAddress> <amountRaw>'); process.exit(1);
})().catch(e=>{ console.error('ERROR:',e.message); process.exit(1); });
