import {G, schwarzschild, bhRadius, boundaryR, holeMass, circularOrbitVel, stepBody, stabilize, predictPath} from './physics.js?v=29';
import {drawWorld, makeStars, tierTheme} from './renderer.js?v=29';
import {UPS, PERM, ADV, permMass, costOf, baseBP, tierInfo, maxFor, loadSave, defaultSave, storeSave, exportStr, importStr, stats} from './upgrades.js?v=29';
import * as AU from './audio.js?v=29';

const cv=document.getElementById('game'), ctx=cv.getContext('2d');
const $=id=>document.getElementById(id);
const IS_TOUCH=('ontouchstart' in window)||navigator.maxTouchPoints>0;
const DMAX=IS_TOUCH?1.2:2; // tablets/phones render lighter; desktop keeps full fidelity
const BODY_CAP=IS_TOUCH?140:220, BODY_FLOW=IS_TOUCH?130:200;
const hud=$('hud'),startEl=$('start'),forgeEl=$('forge'),pauseEl=$('pause'),endEl=$('end'),advEl=$('adv');

let save=loadSave();
let R=null; // run state
let keys={}, mouse={x:0,y:0,L:false,R:false,active:false};
let shake=0, hitstop=0;

function resize(){ const d=Math.min(DMAX,devicePixelRatio||1);
  cv.width=innerWidth*d; cv.height=innerHeight*d; }
addEventListener('resize',resize); resize();

// ---------- run setup ----------
function newRun(){
  const st=stats(save);
  const tier=save.tier||1, cycles=save.cycles||0;
  const ti=tierInfo(tier, cycles);
  // Physical mass drives gravity + size: identical every tier. The win threshold
  // (Bwin) is the physical mass scaled 3x per tier/cycle — heavy on paper only.
  const winF=Math.pow(3,(tier-1)+cycles);
  const M0=st.M0, B0=10000, Bwin0=Math.round(10000*winF);
  const r0=540+130*(tier-1);
  const v=circularOrbitVel(r0,B0);
  const mesh=[]; for(let k=0;k<11;k++) mesh.push({a:k/11*6.283,w:0.85+Math.random()*0.35});
  const craters=[]; for(let k=0;k<4;k++) craters.push({a:Math.random()*6.28,s:0.7+Math.random()*0.7});
  R={t:0, dur:st.clock, M:M0, B:B0, B0, Bwin:Bwin0, winF, st,
    p:{x:r0,y:0,vx:0,vy:-v,r:9,M:M0,angle:0,mesh,craters},
    bodies:[], cam:{x:r0,y:0,z:1}, time:0, // camera starts glued to the ship
    trail:[], moon:null, moonA:0, spawnT:0, combo:1, comboT:0, popups:[],
    nabbed:0, siphoned:0, peak: M0, shakeT:0, over:false,
    starsFar:makeStars(420,12345), starsNear:makeStars(200,999),
    heat:0, stretch:0, thrustMag:0, Rs:bhRadius(B0), BR:boundaryR(B0,B0), edgeT:0, sparks:[],
    tier, cycles, bodyMult:ti.bodyMult, speedMult:ti.speedMult,
    showPings:st.predSecs>3.4,
    predict:[], fuel:1 };
  if(st.twin>0) R.moon={x:r0+40,y:0};
  for(let i=0;i<(IS_TOUCH?36:48);i++) spawnBody(true);
  return R;
}
function bodyTier(m, pm){
  const q=m/pm;
  if(q<0.5) return 0; if(q<0.95) return 1; if(q<=1.2) return 2; if(q<=6) return 3; return 4;
}
function spawnBody(any=false){
  const a=Math.random()*6.283;
  // MEGA body: rare (~4%) golden colossus, mass max(50, 4x you), lurking near the disk. Hard, huge payoff.
  if(!any&&Math.random()<0.045&&R.t>20){
    const mrr=260+Math.random()*220;
    const mx=Math.cos(a)*mrr, my=Math.sin(a)*mrr;
    const mmass=Math.max(50,25*Math.pow(1.022,R.t)*3)*R.bodyMult;
    const mvo=circularOrbitVel(Math.max(200,mrr),R.B)*0.6*R.speedMult;
    R.bodies.push({x:mx,y:my,vx:-Math.sin(a)*mvo,vy:Math.cos(a)*mvo,mass:mmass,r:6+Math.cbrt(mmass)*1.7,tier:5,rot:0,seed:Math.random()*10,mega:true});
    if(R.bodies.length>BODY_CAP) R.bodies.splice(0,R.bodies.length-220);
    return;
  }
  const rr=260+Math.random()*900;
  const x=Math.cos(a)*rr+R.p.x*0.15, y=Math.sin(a)*rr+R.p.y*0.15;
  const late=R.t>150;
  const roll=Math.random();
  let mult;
  if(!late) mult = roll<0.45?0.1+Math.random()*0.4 : roll<0.8?0.5+Math.random()*0.45 : roll<0.94?0.8+Math.random()*0.4 : 1.5+Math.random()*3;
  else mult = roll<0.25?0.1+Math.random()*0.4 : roll<0.5?0.5+Math.random()*0.45 : roll<0.7?0.8+Math.random()*0.4 : roll<0.88?1.5+Math.random()*3 : 3+Math.random()*6;
  // spawns scale ONLY with run time — never with player mass (no endgame snowball)
  const timeScale=25*Math.pow(1.022,R.t); // ~25 → ~17,000 over 5 mins
  const mass=Math.max(0.5,timeScale*mult);
  // shinies: 1% gold (10x), 0.1% diamond (100x) — tier judged on base mass, jackpot stays edible
  const su=Math.random();
  const shiny=su<0.001?'diamond':(su<0.011?'gold':null);
  const fmass=mass*(shiny==='diamond'?100:(shiny==='gold'?10:1));
  const m2=fmass*0.65*R.bodyMult;
  const tier=bodyTier(mass,R.M);
  const vo=circularOrbitVel(Math.max(200,Math.hypot(x,y)),R.B)*(0.85+Math.random()*0.3)*R.speedMult;
  R.bodies.push({x,y,vx:-Math.sin(a)*vo,vy:Math.cos(a)*vo,mass:m2,r:4+Math.cbrt(Math.max(1,m2))*1.5,tier,rot:Math.random()*6.28,seed:Math.random()*10,moon:tier===3&&Math.random()<0.5,shiny});
  if(R.bodies.length>BODY_CAP) R.bodies.splice(0,R.bodies.length-220);
}

// ---------- UI: forge ----------
const byId={}; for(const d of UPS) byId[d.id]=d;
const TREE_ROOT='ion';
const TREE_COLS=[
  {title:'Propulsion & Navigation', ids:['nav','hull','stab','brake']},
  {title:'Mass & Composition', ids:['core','dense','twin']},
  {title:'Gravity & Harvesting', ids:['hill','roche','harv']},
  {title:'Relativistic & Time', ids:['time','hawk','res']},
];
const PREREQ={nav:'ion',hull:'nav',stab:'hull',brake:'stab',core:'ion',dense:'core',twin:'dense',hill:'ion',roche:'hill',harv:'roche',time:'ion',hawk:'time',res:'hawk'};
const ABBR={ion:'IO',nav:'NV',hull:'HU',stab:'ST',brake:'BR',core:'PC',dense:'HD',twin:'BT',hill:'HS',roche:'RL',harv:'AH',time:'TD',hawk:'HR',res:'SR'};
let selId='ion';
function renderForge(){
  $('bank').textContent=save.bp.toFixed(2)+' BP · '+save.rp.toFixed(0)+' RP · Tier '+(save.tier||1)+'/10 caps (win to uncap +1/level)';
  const bb=$('btnBulk'); if(bb) bb.textContent=bulk===1?'BUY ×1':'BUY ×5 (−10%)';
  const t=$('tree'); t.innerHTML='<svg id="links"></svg>';
  const effLvl=id=>(save.ups[id]||0)+(save.rpups[id]||0); // BP + RP levels combined
  const isOpen=id=>{ const pre=PREREQ[id]||null; return !pre||effLvl(pre)>0; };
  const mkNode=d=>{
    const open=isOpen(d.id);
    const mx=maxFor(d,save.tier), lvl=effLvl(d.id), rpl=save.rpups[d.id]||0;
    const k=bulkK(mx,lvl);
    const afford=open&&k>0&&(save.bp>=bulkCost(d,lvl,k)||save.rp>=rpCost(d,lvl,k));
    const div=document.createElement('div');
    div.className='tnode'+(open?'':' locked')+(lvl>0?' bought':'')+(lvl>=mx?' maxed':'')+(rpl>0?' rp':'')+(afford?' afford':'')+(selId===d.id?' sel':'');
    div.dataset.id=d.id;
    div.innerHTML='<span class="dot">'+(ABBR[d.id]||'??')+'</span><span class="nlv">'+lvl+'/'+mx+'</span>'+(rpl>0?'<span class="rlv">+'+rpl+'</span>':'');
    div.title=d.name+' '+lvl+'/'+mx+(rpl>0?' ('+rpl+' RP, permanent)':'')+(afford?' — AFFORDABLE':'');
    div.onclick=()=>{ selId=d.id; renderForge(); };
    return div;
  };
  const renderDetail=()=>{
    const box=$('nodeDetail'); if(!box) return;
    const d=byId[selId]||byId[TREE_ROOT];
    const pre=PREREQ[d.id]||null, open=isOpen(d.id);
    const mx=maxFor(d,save.tier), lvl=effLvl(d.id), bl=save.ups[d.id]||0, rl=save.rpups[d.id]||0;
    const k=bulkK(mx,lvl);
    import('./upgrades.js?v=29').then(m=>{
      box.innerHTML='<h4>'+d.name+' <span class="lv">'+lvl+'/'+mx+'</span></h4>'+
        '<p>'+d.desc+'</p><div class="tip">'+m.nextTip(d,lvl,mx,save.tier)+'</div>'+
        '<div class="lvrow">BP lv '+bl+' (resets) · RP lv '+rl+' (permanent)</div>';
      const row=document.createElement('div'); row.className='buyrow';
      const b1=document.createElement('button'), b2=document.createElement('button');
      if(!open){ b1.textContent='NEEDS '+byId[pre].name.toUpperCase(); b1.disabled=true; b2.style.display='none'; }
      else if(k<=0){ b1.textContent='MAXED'; b1.disabled=true; b2.style.display='none'; }
      else {
        const cBP=bulkCost(d,lvl,k), cRP=rpCost(d,lvl,k);
        b1.textContent='BUY '+(k>1?k+'× ':'')+cBP.toFixed(k===5?1:0)+' BP';
        b1.disabled=save.bp<cBP;
        b1.onclick=()=>{ const mx2=maxFor(d,save.tier), lv2=effLvl(d.id), kk=bulkK(mx2,lv2); if(kk<=0) return;
          const cc=bulkCost(d,lv2,kk); if(save.bp>=cc){ save.bp-=cc; save.ups[d.id]=(save.ups[d.id]||0)+kk; storeSave(save); refreshBank(); renderForge(); } };
        b2.textContent='BUY '+(k>1?k+'× ':'')+cRP.toFixed(k===5?1:0)+' RP';
        b2.disabled=save.rp<cRP;
        b2.onclick=()=>{ const mx2=maxFor(d,save.tier), lv2=effLvl(d.id), kk=bulkK(mx2,lv2); if(kk<=0) return;
          const cc=rpCost(d,lv2,kk); if(save.rp>=cc){ save.rp-=cc; save.rpups[d.id]=(save.rpups[d.id]||0)+kk; storeSave(save); refreshBank(); renderForge(); } };
      }
      row.appendChild(b1); row.appendChild(b2); box.appendChild(row);
    });
  };
  const root=document.createElement('div'); root.className='treeroot';
  root.appendChild(mkNode(byId[TREE_ROOT]));
  root.firstChild.classList.add('rootnode'); t.appendChild(root);
  const cols=document.createElement('div'); cols.className='cols';
  for(const c of TREE_COLS){
    const col=document.createElement('div'); col.className='branch';
    const h=document.createElement('h3'); h.textContent=c.title; col.appendChild(h);
    for(const id of [...c.ids].reverse()) col.appendChild(mkNode(byId[id])); // nearest child sits closest to the root
    cols.appendChild(col);
  }
  t.appendChild(cols);
  renderDetail();
  renderEternal();
  requestAnimationFrame(drawLinks);
}
function renderAdvs(){
  const box=$('advs'); if(!box) return;
  const got=ADV.filter(a=>save.adv[a.id]).length;
  box.innerHTML='<h3>ADVANCEMENTS '+got+'/'+ADV.length+' (+2 BP each)</h3>';
  for(const a of ADV){
    const div=document.createElement('div');
    div.className='ad'+(save.adv[a.id]?' got':'');
    div.textContent=(save.adv[a.id]?'◆ ':'◇ ')+a.name+' — '+a.desc;
    box.appendChild(div);
  }
}
function renderEternal(){
  const box=$('eternal'); if(!box) return;
  const has=save.rebirths>0||(save.rp||0)>0||Object.keys(save.perm||{}).length>0;
  if(!has){ box.innerHTML='<h3>ETERNAL · beat Tier 10 and REBIRTH to earn RP for upgrades that survive every reset.</h3>'; return; }
  box.innerHTML='<h3>ETERNAL · '+save.rp.toFixed(0)+' RP · rebirths '+save.rebirths+' · permanent mass ×'+permMass(save).toFixed(2)+'</h3>';
  for(const d of PERM){
    const lvl=save.perm[d.id]||0, k=bulkK(d.max,lvl);
    const fake={base:d.base};
    const div=document.createElement('div'); div.className='et';
    div.innerHTML='<span class="lv">'+lvl+'/'+d.max+'</span><h4>'+d.name+'</h4><p>'+d.desc+'</p>';
    const b=document.createElement('button');
    if(k<=0){ b.textContent='MAXED'; b.disabled=true; }
    else { const cost=rpCost(fake,lvl,k);
      b.textContent='BUY '+(k>1?k+'× ':'')+cost.toFixed(k===5?1:0)+' RP';
      b.disabled=save.rp<cost;
      b.onclick=()=>{ const lv2=save.perm[d.id]||0, kk=bulkK(d.max,lv2); if(kk<=0) return;
        const cc=rpCost(fake,lv2,kk); if(save.rp>=cc){ save.rp-=cc; save.perm[d.id]=lv2+kk; storeSave(save); refreshBank(); renderForge(); } };
    }
    div.appendChild(b); box.appendChild(div);
  }
}
function drawLinks(){
  const t=$('tree'), svg=$('links'); if(!t||!svg) return;
  const tr=t.getBoundingClientRect(); svg.setAttribute('width',t.offsetWidth); svg.setAttribute('height',t.offsetHeight);
  let html='';
  for(const [child,parent] of Object.entries(PREREQ)){
    const pe=t.querySelector('[data-id="'+parent+'"]'), ce=t.querySelector('[data-id="'+child+'"]');
    if(!pe||!ce) continue;
    const pr=pe.getBoundingClientRect(), cr=ce.getBoundingClientRect();
    const x1=pr.left+pr.width/2-tr.left, y1=pr.bottom-tr.top;
    const x2=cr.left+cr.width/2-tr.left, y2=cr.top-tr.top;
    const open=((save.ups[parent]||0)+(save.rpups[parent]||0))>0;
    const col=open?'rgba(57,230,255,.85)':'rgba(42,58,82,.9)';
    html+='<path class="'+(open?'open':'shut')+'" d="M'+x1+' '+y1+' C'+x1+' '+(y1+22)+','+x2+' '+(y2-22)+','+x2+' '+y2+'" stroke="'+col+'" stroke-width="2" fill="none"/>';
  }
  svg.innerHTML=html;
}
addEventListener('resize',()=>{ if(!$('forge').classList.contains('hidden')) drawLinks(); });
function refreshBank(){
  $('bankLine').textContent='Tier '+(save.tier||1)+'/10'+(save.cycles?' · Cycle '+save.cycles:'')+' · BP: '+save.bp.toFixed(2)+((save.rp||0)>0||save.rebirths>0?' · RP: '+save.rp.toFixed(0)+' (×'+save.rebirths+')':'')+' · Runs: '+save.totalRuns+' · Best: '+Math.round(save.best);
  const mt=$('menuTier'); if(mt) mt.textContent='TIER '+(save.tier||1)+'/10'+(save.rebirths>0?' · ∞'+save.rebirths:'')+' · '+save.bp.toFixed(1)+' BP';
}

// ---------- screens ----------
function show(el){ for(const s of [startEl,forgeEl,pauseEl,endEl,advEl]) if(s) s.classList.add('hidden'); if(el) el.classList.remove('hidden'); hud.classList.toggle('hidden', el!==null && el!==hud && (el===startEl||el===forgeEl||el===endEl||el===advEl)); if(el===null) hud.classList.remove('hidden');
  const touch=document.body.classList.contains('touch'), hid=hud.classList.contains('hidden')||!touch;
  $('stick').classList.toggle('hidden',hid); $('touchBtns').classList.toggle('hidden',hid); }
$('btnStart').onclick=()=>{ AU.initAudio(); AU.resume(); startRun(); };
$('btnGo').onclick=()=>{ AU.initAudio(); AU.resume(); startRun(); };
$('btnForge').onclick=()=>{ renderForge(); show(forgeEl); };
let bulk=1;
$('btnBulk').onclick=()=>{ bulk=bulk===1?5:1; renderForge(); };
$('btnRefundRP').onclick=()=>{
  let total=0, n=0;
  for(const d of UPS){ const l=save.rpups[d.id]||0; if(l<=0) continue;
    for(let i=0;i<l;i++) total+=Math.ceil(d.base*Math.pow(1.20,i));
    n+=l; }
  if(!n){ showToast('No RP levels to refund.'); return; }
  if(!confirm('Refund '+n+' permanent RP level'+(n>1?'s':'')+' for '+total.toFixed(0)+' RP?')) return;
  for(const d of UPS) save.rpups[d.id]=0;
  save.rp=Number((save.rp+total).toFixed(2));
  if(selId&&!byId[selId]) selId='ion';
  storeSave(save); refreshBank(); renderForge();
  showToast('Refunded '+n+' levels → +'+total.toFixed(0)+' RP');
};
// bulk pricing: buy k=min(bulk,remaining); a full 5 costs 10% less than five singles
function bulkK(mx,lvl){ const rem=mx-lvl; return rem<=0?0:Math.min(bulk,rem); }
function bulkCost(def,lvl,k){ let s=0; for(let i=0;i<k;i++) s+=costOf(def,lvl+i); return k===5?s*0.9:s; }
// RP purchases share the unified 1.20 curve with BP
function rpCost(def,lvl,k){ let s=0; for(let i=0;i<k;i++) s+=Math.ceil(def.base*Math.pow(1.20,lvl+i)); return k===5?s*0.9:s; }
$('btnAdv').onclick=()=>{ renderAdvs(); show(advEl); };
$('btnAdvBack').onclick=()=>{ show(startEl); refreshBank(); };
$('btnAdvGo').onclick=()=>{ AU.initAudio(); AU.resume(); startRun(); };
$('btnForge2').onclick=()=>{ renderForge(); show(forgeEl); };
$('btnBack').onclick=()=>{ newRun(); R.menu=true; R.paused=true; show(startEl); refreshBank(); };
$('btnAgain').onclick=()=>{ show(null); startRun(); };
$('btnRebirth').onclick=()=>{ doRebirth(); };
function doRebirth(){
  const gain=Math.round((2+R.cycles)*(1+0.5*(save.rebirths||0)));
  save.rp=(save.rp||0)+gain; save.rebirths=(save.rebirths||0)+1;
  save.tier=1; save.cycles=0; save.ups={}; save.bp=0;
  unlockAdv('reborn');
  storeSave(save); refreshBank();
  showToast('REBIRTH ×'+save.rebirths+' · +'+gain+' RP · permanent mass ×'+permMass(save).toFixed(2));
  newRun(); R.menu=true; R.paused=true; show(startEl);
}
function unlockAdv(id){
  if(save.adv[id]) return;
  const d=ADV.find(a=>a.id===id); if(!d) return;
  save.adv[id]=1; save.bp=Number((save.bp+2).toFixed(2));
  storeSave(save); refreshBank();
  showToast('ADVANCEMENT: '+d.name+' (+2 BP)');
}
let toastT=null;
function showToast(msg){
  const el=$('toast'); if(!el) return;
  el.textContent=msg; el.classList.remove('hidden');
  if(toastT) clearTimeout(toastT);
  toastT=setTimeout(()=>el.classList.add('hidden'),3600);
}
$('btnPause').onclick=()=>{ if(R&&!R.over){ R.paused=true; show(pauseEl); } };
$('btnWipe').onclick=()=>{
  if(!confirm('Wipe everything (BP, tiers, upgrades, records) and start from Tier 1?')) return;
  try{ localStorage.removeItem('blackhole_save_v1'); }catch(e){}
  save=defaultSave(); storeSave(save); refreshBank();
  newRun(); R.menu=true; R.paused=true; show(startEl);
};
$('btnResume').onclick=()=>{ if(R){ R.paused=false; show(null); } };
$('btnQuit').onclick=()=>{ if(R) endRun('quit'); };
$('btnMute').onclick=e=>{ save.muted=!save.muted; storeSave(save); AU.setMuted(save.muted); e.target.textContent=save.muted?'MUT':'SND'; };
$('vol').oninput=e=>{ AU.setVol(e.target.value/100); save.vol=e.target.value/100; storeSave(save); };
$('btnExport').onclick=()=>{ const b=$('saveBox'); b.classList.remove('hidden'); b.value=exportStr(save); b.select(); };
$('btnImport').onclick=()=>{ const b=$('saveBox'); if(b.classList.contains('hidden')){b.classList.remove('hidden');b.value='';b.focus();} else { try{ save=importStr(b.value); storeSave(save); refreshBank(); b.classList.add('hidden'); }catch(e){ b.value='INVALID SAVE'; } } };
addEventListener('keydown',e=>{
  keys[e.key.toLowerCase()]=true;
  if(e.key==='Escape'&&R&&!R.over&&!R.menu){ R.paused=!R.paused; show(R.paused?pauseEl:null); }
  if(['arrowup','arrowdown','arrowleft','arrowright',' '].includes(e.key.toLowerCase())) e.preventDefault();
});
addEventListener('keyup',e=>{ keys[e.key.toLowerCase()]=false; });
cv.addEventListener('mousemove',e=>{ mouse.x=e.clientX; mouse.y=e.clientY; mouse.active=true; });
cv.addEventListener('mousedown',e=>{ AU.resume(); if(e.button===0)mouse.L=true; if(e.button===2)mouse.R=true; });
addEventListener('mouseup',e=>{ if(e.button===0)mouse.L=false; if(e.button===2)mouse.R=false; });
cv.addEventListener('contextmenu',e=>e.preventDefault());
// Mobile: left-half floating stick = WASD thrust, right-half hold = boost toward finger.
const joy={x:0,y:0,id:null,bx:0,by:0}; let boostId=null, brakeTouch=false, stabTouch=false;
if(IS_TOUCH) document.body.classList.add('touch');
function stickDraw(dx,dy){ const k=$('knob'); if(k) k.style.transform='translate('+dx+'px,'+dy+'px)'; }
cv.addEventListener('touchstart',e=>{ e.preventDefault(); AU.resume();
  for(const t of e.changedTouches){
    if(t.clientX<innerWidth/2&&joy.id===null){ joy.id=t.identifier; joy.bx=t.clientX; joy.by=t.clientY; }
    else if(boostId===null){ boostId=t.identifier; mouse.x=t.clientX; mouse.y=t.clientY; mouse.L=true; }
  }
},{passive:false});
cv.addEventListener('touchmove',e=>{ e.preventDefault();
  for(const t of e.changedTouches){
    if(t.identifier===joy.id){ let dx=t.clientX-joy.bx, dy=t.clientY-joy.by;
      const m=Math.hypot(dx,dy)||1, cl=Math.min(1,60/m); dx*=cl; dy*=cl;
      joy.x=dx/60; joy.y=dy/60; stickDraw(dx,dy); }
    else if(t.identifier===boostId){ mouse.x=t.clientX; mouse.y=t.clientY; }
  }
},{passive:false});
function touchEnd(e){ for(const t of e.changedTouches){
    if(t.identifier===joy.id){ joy.id=null; joy.x=0; joy.y=0; stickDraw(0,0); }
    if(t.identifier===boostId){ boostId=null; mouse.L=false; } } }
cv.addEventListener('touchend',touchEnd); cv.addEventListener('touchcancel',touchEnd);
$('btnBrake').addEventListener('touchstart',e=>{ e.preventDefault(); brakeTouch=true; $('btnBrake').classList.add('on'); },{passive:false});
$('btnBrake').addEventListener('touchend',e=>{ e.preventDefault(); brakeTouch=false; $('btnBrake').classList.remove('on'); });
$('btnStab').addEventListener('touchstart',e=>{ e.preventDefault(); stabTouch=!stabTouch; $('btnStab').classList.toggle('on',stabTouch); },{passive:false});

function startErr(msg){ const bl=$('bankLine'); if(bl) bl.textContent='START FAILED: '+msg+' — screenshot this and send it back'; }
function startRun(){
  try{ newRun(); AU.startMusic(save.tier); }
  catch(err){ console.error(err); startErr(err&&err.message||err); show(startEl); return; }
  show(null);
}
addEventListener('error',e=>{ if(!R||R.menu) startErr(e.message||'unknown error'); });
function popup(txt,wx,wy,color){ const d=document.createElement('div'); d.className='pop'; d.textContent=txt;
  if(color)d.style.color=color;
  const z=R.cam.z,W=cv.width/Math.min(DMAX,devicePixelRatio||1),H=cv.height/Math.min(DMAX,devicePixelRatio||1);
  d.style.left=((wx-R.cam.x)*z+W/2)+'px'; d.style.top=((wy-R.cam.y)*z+H/2)+'px';
  $('popups').appendChild(d); setTimeout(()=>d.remove(),1000); }

function endRun(reason){
  if(R.over) return; R.over=true;
  const win = reason==='win';
  AU.boom(win);
  let bp=Math.max(0,baseBP(R.M,R.Bwin));
  const tierMult=(1+0.5*((R.tier||1)-1));
  const finalBP=Number((bp*R.st.bpMult*tierMult).toFixed(2));
  save.bp=Number((save.bp+finalBP).toFixed(2));
  save.totalRuns++; save.best=Math.max(save.best,Math.round(R.M));
  let ascended=false, endless=false;
  if(win){
    if(R.tier<10){ save.tier=R.tier+1; ascended=true; }
    else { save.cycles=(save.cycles||0)+1; endless=true; }
    unlockAdv('win');
    if(save.tier>=5) unlockAdv('t5');
    if(save.tier>=10||R.tier>=10) unlockAdv('t10');
  }
  storeSave(save); refreshBank();
  const rbGain=endless?Math.round((2+R.cycles)*(1+0.5*(save.rebirths||0))):0;
  $('endTitle').textContent = win?(ascended?('TIER '+save.tier+' UNLOCKED'):(endless?'REBIRTH AVAILABLE':'SINGULARITY ACHIEVED')):(reason==='horizon'?'SPAGHETTIFIED':'TIME COLLAPSE');
  $('endFlavor').textContent = win?(ascended?('You drank the Tier '+R.tier+' hole. Tier '+save.tier+'/10 waits: a heavier hole, richer bodies, stronger upgrades.')
    : endless?('REBIRTH: reset the ladder to Tier 1 (tiers, upgrades and BP wiped — RP and Eternal upgrades kept forever). Reward: +'+rbGain+' RP now, permanent mass multi rises to '+(1.1+0.1*(save.rebirths+1)).toFixed(2)+'x (base 1.1x, +0.1x per rebirth; more cycles banked = more RP). Spend RP on Eternal upgrades that persist through every rebirth.')
    :'You out-massed the hole and drank it in a relativistic shockwave.')
    : reason==='horizon'?'You crossed the event horizon while lighter than the hole.':'The doom clock hit zero with M ≤ B.';
  $('btnRebirth').classList.toggle('hidden',!endless);
  $('breakdown').innerHTML=
    '<div>Ladder <b>Tier '+R.tier+'/10'+(R.cycles?' · Cycle '+R.cycles:'')+'</b></div>'+
    '<div>Final mass M <b>'+Math.round(R.M).toLocaleString()+'</b></div>'+
    '<div>Win threshold B <b>'+Math.round(R.Bwin).toLocaleString()+'</b></div>'+
    '<div>Ratio M/B <b>'+(R.M/R.Bwin).toFixed(4)+'</b></div>'+
    '<div>Bodies eaten <b>'+R.nabbed+'</b></div>'+
    '<div>BP earned (×'+(R.st.bpMult*tierMult).toFixed(2)+') <b>+'+finalBP.toFixed(2)+'</b></div>'+
    '<div>Bank <b>'+save.bp.toFixed(2)+' BP</b></div>';
  setTimeout(()=>show(endEl), win?1600:900);
}

// Menu backdrop: drift bodies on rails and glide the camera onto the hole.
function driftMenu(dt){
  for(const b of R.bodies){
    const br2=Math.max(b.x*b.x+b.y*b.y,64), br=Math.sqrt(br2);
    const a=G*R.B/br2; b.vx+=-a*b.x/br*dt; b.vy+=-a*b.y/br*dt;
    b.x+=b.vx*dt; b.y+=b.vy*dt; b.rot+=dt*0.4;
    if(br<R.Rs*0.9){ b.x*=-0.9; b.y*=-0.9; } // recycle instead of vanishing
  }
  R.cam.x+=(0-R.cam.x)*dt*0.5; R.cam.y+=(0-R.cam.y)*dt*0.5;
  R.cam.z+=(0.55-R.cam.z)*dt*0.5;
}

// ---------- main loop ----------
let last=performance.now();
function frame(now){
  requestAnimationFrame(frame);
  let dt=Math.min(0.033,(now-last)/1000); last=now;
  if(!R||R.over){ if(R&&R.over) drawEndBlast(dt); return; }
  if(R.paused){ // menus: live backdrop sim (title) or frozen frame (pause)
    if(R.menu){ R.time+=dt; driftMenu(dt); }
    render(dt); return;
  }
  if(hitstop>0){ hitstop-=dt; return; }
  R.time+=dt; R.t+=dt;
  const st=R.st;
  R.B=holeMass(R.B0,R.t,st.hawk);
  R.Bwin=R.B*R.winF;
  R.Rs=bhRadius(R.B);
  R.BR=boundaryR(R.B,R.B0);
  const r=Math.hypot(R.p.x,R.p.y);
  // thrust input: crisp screen-directional keys (world axes match screen axes)
  let ax=0,ay=0; const base=st.thr;
  if(keys['w']||keys['arrowup'])ay-=base; if(keys['s']||keys['arrowdown'])ay+=base;
  if(keys['a']||keys['arrowleft'])ax-=base; if(keys['d']||keys['arrowright'])ax+=base;
  // mouse: smooth proportional steering toward the cursor
  const dpr=Math.min(DMAX,devicePixelRatio||1), W=cv.width/dpr, H=cv.height/dpr;
  if(mouse.L||mouse.R||mouse.active){
    const wx=(mouse.x-W/2)/R.cam.z+R.cam.x, wy=(mouse.y-H/2)/R.cam.z+R.cam.y;
    let dx=wx-R.p.x, dy=wy-R.p.y; const d=Math.hypot(dx,dy)||1;
    const k=Math.min(1,d/240); // proportional: gentle near cursor, full at range
    if(mouse.L){ ax+=dx/d*base*1.4*k; ay+=dy/d*base*1.4*k; }
    else if(mouse.R&&st.brake>0){ const bm=(1+0.4*st.brake)*k; ax-=dx/d*base*bm; ay-=dy/d*base*bm; }
    else if(mouse.active&&d>60){ ax+=dx/d*base*0.5*k; ay+=dy/d*base*0.5*k; }
  }
  if(joy.x||joy.y){ ax+=joy.x*base; ay+=joy.y*base; } // mobile stick: screen-directional
  if(brakeTouch&&st.brake>0){ const sp=Math.hypot(R.p.vx,R.p.vy)||1; // mobile BRAKE: retrograde burn
    ax+=-R.p.vx/sp*base*(1+0.4*st.brake); ay+=-R.p.vy/sp*base*(1+0.4*st.brake); }
  R.thrustMag=Math.hypot(ax,ay);
  AU.setWind(R.thrustMag>1?Math.min(1,R.thrustMag/300):0);
  // manual = hard inputs only: hover-steer must not block the stabilizer
  R.manual=!!(keys['w']||keys['a']||keys['s']||keys['d']||keys['arrowup']||keys['arrowdown']||keys['arrowleft']||keys['arrowright']||mouse.L||mouse.R||joy.x||joy.y||brakeTouch);
  stepBody(R.p,R.B,dt,{ax,ay},st.maxSpd,st.damp);
  // Orbit Stabilizer (Stabilizer Core upgrade): Space / Shift / STAB button eases into a circular orbit
  R.stab=st.stab>0&&(keys[' ']||keys['shift']||stabTouch)&&!R.manual;
  if(R.stab) stabilize(R.p,R.B,dt,0.7+0.3*st.stab);
  // Outer containment wall: deflect radial velocity inward + spark burst
  let rr=Math.hypot(R.p.x,R.p.y);
  if(rr>R.BR){
    const nx=R.p.x/rr, ny=R.p.y/rr;
    const vr=R.p.vx*nx+R.p.vy*ny;
    if(vr>0){ R.p.vx-=1.8*vr*nx; R.p.vy-=1.8*vr*ny; }
    R.p.x*=R.BR/rr; R.p.y*=R.BR/rr; rr=R.BR;
    R.edgeT=1; shake=Math.max(shake,4);
    for(let i=0;i<12;i++){ const a=Math.random()*6.283, sp=80+Math.random()*160;
      R.sparks.push({x:R.p.x,y:R.p.y,vx:-nx*sp+Math.cos(a)*90,vy:-ny*sp+Math.sin(a)*90,life:0.6}); }
    popup('CONTAINMENT',R.p.x,R.p.y,'#39e6ff');
  }
  R.edgeT=Math.max(0,R.edgeT-dt*1.5);
  for(let i=R.sparks.length-1;i>=0;i--){ const sp=R.sparks[i];
    sp.life-=dt; sp.x+=sp.vx*dt; sp.y+=sp.vy*dt; sp.vx*=0.98; sp.vy*=0.98;
    if(sp.life<=0) R.sparks.splice(i,1); }
  R.nearEdge=(R.BR-rr)<350;
  R.p.M=R.M; R.p.r=5+Math.cbrt(R.M)*1.6;
  R.p.angle=Math.atan2(R.p.vy,R.p.vx);
  // magnet: forgiving on mobile, classic tight reach on desktop (caps lowered vs snowball)
  const magR=IS_TOUCH?Math.min(300,(18+R.p.r*1.6)*st.magnet*Math.pow(Math.max(1,R.M/10),0.14))
    :Math.min(260,(14+R.p.r*1.4)*st.magnet*Math.pow(Math.max(1,R.M/10),0.14));
  for(let i=R.bodies.length-1;i>=0;i--){
    const b=R.bodies[i];
    // drift bodies on rails
    const br2=Math.max(b.x*b.x+b.y*b.y,64), br=Math.sqrt(br2);
    const a=G*R.B/br2; b.vx+=-a*b.x/br*dt; b.vy+=-a*b.y/br*dt; b.x+=b.vx*dt; b.y+=b.vy*dt; b.rot+=dt*0.4;
    // roche siphon: shrink bigger bodies nearby
    if(st.roche>0&&b.mass>R.M&&b.mass<=R.M*1.25){
      const d=Math.hypot(b.x-R.p.x,b.y-R.p.y);
      if(d<140+st.roche*25){ const strip=Math.min(b.mass*(0.35+st.roche*0.15)*dt,(R.M*0.02+1)*(1+st.roche*0.2)); b.mass-=strip; R.M+=strip; R.siphoned+=strip; b.r=4+Math.cbrt(Math.max(1,b.mass))*1.5; }
    }
    const d=Math.hypot(b.x-R.p.x,b.y-R.p.y);
    if(d<magR&&b.mass<R.M&&d>1){ // gentle velocity pull: meteors curve in, never snap
      const pull=85*dt*(1-d/magR);
      b.vx+=((R.p.x-b.x)/d)*pull; b.vy+=((R.p.y-b.y)/d)*pull;
    }
    if(d<R.p.r+b.r){
      if(b.mass<R.M){ // eat (mega/shiny gains nerfed: no skipping the endgame)
        let gmult=1;
        if(b.mega) gmult=0.3; else if(b.shiny==='diamond') gmult=0.1; else if(b.shiny==='gold') gmult=0.5;
        const gain=b.mass*st.gain*(0.9+0.2*Math.random())*gmult;
        R.M+=gain; R.nabbed++; R.combo=Math.min(5,R.combo+0.12); R.comboT=4;
        AU.crunch(b.mass); popup('+'+Math.round(gain),b.x,b.y);
        // feeding burst: tier-colored particles, gold for MEGAs
        const cols=['#bfe9ff','#c9b8a8','#ff9a3c','#c9a0ff','#eaf6ff','#ffd34d'];
        const nn=b.tier===5?16:10+Math.floor(Math.random()*5);
        for(let k=0;k<nn;k++){ const ea=Math.random()*6.283, esp=40+Math.random()*170;
          R.sparks.push({x:b.x,y:b.y,vx:Math.cos(ea)*esp,vy:Math.sin(ea)*esp,life:0.5+Math.random()*0.3,c:cols[b.tier]||'#aef3ff'}); }
        if(b.mass>R.M*0.3){ shake=6; hitstop=0.03; }
        else if(b.tier>=2||R.combo>=3){ shake=Math.max(shake,4); } // arcade thump on big bites
        if(R.nabbed===1) unlockAdv('first');
        if(b.tier===5) unlockAdv('mega');
        if(b.shiny==='gold') unlockAdv('gold');
        if(b.shiny==='diamond') unlockAdv('dia');
        if(R.M>=100) unlockAdv('m100');
        if(R.M>=1000) unlockAdv('m1000');
        if(R.combo>=4) unlockAdv('combo');
        R.bodies.splice(i,1); spawnBody();
      } else { // bounce off + mass loss
        const nx=(R.p.x-b.x)/d, ny=(R.p.y-b.y)/d;
        R.p.vx=nx*220; R.p.vy=ny*220;
        R.M=Math.max(1,R.M*0.97-1); popup('HULL HIT',R.p.x,R.p.y,'#ff3b57'); shake=9;
        b.x-=nx*10; b.y-=ny*10;
      }
    }
    if(br<R.Rs*0.9){ R.bodies.splice(i,1); spawnBody(); } // hole snacks too
  }
  // moonlet harvest
  if(R.moon&&st.twin>0){ R.moonA+=dt*1.6;
    R.moon.x=R.p.x+Math.cos(R.moonA)*54; R.moon.y=R.p.y+Math.sin(R.moonA)*54;
    for(let i=R.bodies.length-1;i>=0;i--){ const b=R.bodies[i];
      if(b.tier===0&&Math.hypot(b.x-R.moon.x,b.y-R.moon.y)<30){ R.M+=b.mass*0.8; R.bodies.splice(i,1); spawnBody(); popup('+' + Math.round(b.mass*0.8),b.x,b.y,'#9adcff'); } } }
  // accretion harvester inside outer disk
  if(st.harv>0&&r<R.Rs*2.6&&r>R.Rs){ const spd=Math.hypot(R.p.vx,R.p.vy);
    R.M+=st.harv*(0.4+spd/400)*dt*8; }
  // hawking siphon in danger zone
  if(st.hawk>0&&r<R.Rs*3){ R.M+=R.B*0.0005*dt; }
  if(R.comboT>0){ R.comboT-=dt; if(R.comboT<=0) R.combo=1; }
  R.peak=Math.max(R.peak,R.M);
  R.heat=Math.max(0,Math.min(1,1-(r-R.Rs)/(R.Rs*2.5)));
  R.stretch=r<R.Rs*1.6*st.hull?Math.min(1,(R.Rs*1.6*st.hull-r)/(R.Rs*0.6)):0;
  // trail
  R.trail.push({x:R.p.x,y:R.p.y}); if(R.trail.length>40)R.trail.shift();
  // camera: tight on the character early, pulls back as you grow huge
  const tz=Math.max(0.12,Math.min(2.2,900/Math.max(360,r*1.1,R.p.r*14)));
  R.cam.z+=(tz-R.cam.z)*dt*2.5;
  const cf=IS_TOUCH?1:0.95, crate=IS_TOUCH?7:4;
  R.cam.x+=(R.p.x*cf-R.cam.x)*dt*crate; R.cam.y+=(R.p.y*cf-R.cam.y)*dt*crate;
  if(shake>0)shake-=dt*30;
  // predictor
  R.predict=predictPath(R.p,R.B,st.predSecs,42);
  // audio hum
  AU.updateHum(Math.max(0,1-r/(R.Rs*8)));
  // HUD
  updateHUD();
  // win/loss
  if(R.M>R.Bwin){ endRun('win'); drawEndBlast(0.016,true); return; }
  const effRs=R.Rs*st.hull;
  if(r<=effRs&&R.M<=R.Bwin){ endRun('horizon'); return; }
  if(R.t>=R.dur){ endRun(R.M>R.Bwin?'win':'timeout'); return; }
  if(Math.random()<dt*3.5&&R.bodies.length<BODY_FLOW) spawnBody();
  render(dt);
}
function updateHUD(){
  $('hM').textContent=Math.round(R.M).toLocaleString();
  $('hB').textContent=Math.round(R.Bwin).toLocaleString();
  $('hR').textContent=(R.M/R.Bwin).toFixed(4);
  let bp=baseBP(R.M,R.Bwin)*(1+0.5*((R.tier||1)-1));
  $('hBP').textContent=(bp*R.st.bpMult).toFixed(2);
  const thm=tierTheme(R.tier);
  $('tier').textContent='TIER '+R.tier+'/10 · '+thm.name.toUpperCase()+(R.cycles?' ∞'+R.cycles:'');
  $('tier').style.color=thm.accent;
  const left=Math.max(0,R.dur-R.t), m=Math.floor(left/60), s=Math.floor(left%60);
  const te=$('timer'); te.textContent=m+':'+String(s).padStart(2,'0');
  te.classList.toggle('low',left<30);
  const frac=Math.min(1,Math.log10(R.M+1)/Math.log10(R.Bwin+1));
  $('progfill').style.width=(frac*100)+'%';
  $('proglabel').textContent='log M '+Math.round(R.M)+' / log B '+Math.round(R.B);
  $('combo').textContent='x'+R.combo.toFixed(1);
  $('warn').textContent=R.stretch>0?'◈ TIDAL SHEAR ◈':(R.edgeT>0||R.nearEdge?'◈ CONTAINMENT EDGE ◈':(R.t>R.dur-30?'◈ COLLAPSE IMMINENT ◈':(R.stab?'STABILIZING ORBIT':'')));
}
let blast=0;
function render(dt){
  ctx.setTransform(Math.min(DMAX,devicePixelRatio||1),0,0,Math.min(DMAX,devicePixelRatio||1),0,0);
  if(shake>0) ctx.translate((Math.random()-0.5)*shake,(Math.random()-0.5)*shake);
  drawWorld(ctx,{cam:R.cam,starsFar:R.starsFar,starsNear:R.starsNear,Rs:R.Rs,p:R.p,bodies:R.bodies,theme:tierTheme(R.tier),touch:IS_TOUCH,
    predict:R.predict,time:R.time,moon:R.moon,trail:R.trail,heat:R.heat,stretch:R.stretch,
    thrustMag:R.thrustMag,showPings:R.showPings,BR:R.BR,nearEdge:R.nearEdge,edgeT:R.edgeT,
    sparks:R.sparks,stab:R.stab});
}
function drawEndBlast(dt,win=false){ blast+=dt; render(0); }

// init
AU.setMuted(save.muted); AU.setVol(save.vol??0.6);
$('btnMute').textContent=save.muted?'MUT':'SND'; $('vol').value=(save.vol??0.6)*100;
refreshBank(); newRun(); R.menu=true; R.paused=true; show(startEl);
requestAnimationFrame(frame);
