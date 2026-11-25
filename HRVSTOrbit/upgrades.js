// 12-upgrade rebirth tree: defs, cost, effects. Persisted in localStorage.
export const UPS = [
 {id:'core',  br:'Mass & Composition', name:'Primordial Core', max:15, base:1, desc:'+25% compounding starting mass M0.'},
 {id:'dense', br:'Mass & Composition', name:'Hyper-Dense Matter', max:10, base:1, desc:'+15% mass per absorption.'},
 {id:'twin',  br:'Mass & Composition', name:'Binary Twin', max:5, base:3, desc:'Moonlet harvests nearby dust.'},
 {id:'ion',   br:'Propulsion & Navigation', name:'Ion Thruster Array', max:10, base:1, desc:'+20% thrust & delta-v.'},
 {id:'nav',   br:'Propulsion & Navigation', name:'Predictive Nav-Computer', max:5, base:2, desc:'Longer prediction arc + metal pings.'},
 {id:'hull',  br:'Propulsion & Navigation', name:'Tidal Hull Reinforcement', max:8, base:2, desc:'Shrinks spaghettification radius 6%/lvl.'},
 {id:'stab',  br:'Propulsion & Navigation', name:'Stabilizer Core', max:5, base:2, desc:'Unlocks Space/Shift orbit stabilizer; faster lock per level.'},
 {id:'brake', br:'Propulsion & Navigation', name:'Inertial Brakes', max:5, base:2, desc:'Unlocks Right-click retro brake; +40% brake power per level.'},
 {id:'hill',  br:'Gravity & Harvesting', name:'Hill Sphere Expansion', max:12, base:1, desc:'+18% capture radius per level; reach also grows with your mass.'},
 {id:'roche', br:'Gravity & Harvesting', name:'Roche Limit Siphon', max:8, base:3, desc:'Strip bodies up to 1.25x mass nearby.'},
 {id:'harv',  br:'Gravity & Harvesting', name:'Accretion Harvester', max:10, base:2, desc:'Gain mass inside outer disk.'},
 {id:'time',  br:'Relativistic & Time', name:'Time Dilation Engine', max:6, base:4, desc:'+15s run clock per level.'},
 {id:'hawk',  br:'Relativistic & Time', name:'Hawking Radiation Tap', max:8, base:3, desc:'Slows hole growth; siphon 0.05% B/s in danger zone.'},
 {id:'res',   br:'Relativistic & Time', name:'Singularity Resonance', max:10, base:2, desc:'+12% final BP per level.'},
];
export function costOf(def, lvl){ return Math.ceil(def.base * Math.pow(1.65, lvl)); }
// Sublinear Rebirth curve, trimmed slightly: low ratios pay fairly, parity pays 10.
export function baseBP(M, B){
  if(!(M>0&&B>0)) return 0;
  let r = Number((10 * Math.pow(M / B, 0.66)).toFixed(2));
  if(M > 20) r = Math.max(0.45, r);
  return r;
}
// Singularity Ladder: 10 tiers. Each tier raises the hole, enriches bodies,
// shortens the clock slightly, and uncaps every upgrade by +1 level.
export function tierInfo(tier){
  const t = Math.max(1, tier || 1);
  return {
    // Win-threshold mass: 3x heavier every tier. Gravity and visual size stay
    // fixed (they run off the physical mass, which resets every tier).
    b0: 10000 * Math.pow(3, t - 1),
    bodyMult: 1 + 0.35 * (t - 1),
    speedMult: 1 + 0.05 * (t - 1),
    clockBase: Math.max(210, 300 - 10 * (t - 1)),
    m0Mult: 1 + 0.5 * (t - 1),
    gainMult: 1 + 0.15 * (t - 1),
  };
}
export function maxFor(def, tier){ return def.max + (Math.max(1, tier || 1) - 1); }
export function defaultSave(){ return {bp:0, rp:0, rebirths:0, perm:{}, adv:{}, totalRuns:0, best:10, tier:1, cycles:0, ups:{}, rpups:{}, muted:false, vol:0.6}; }
const KEY='blackhole_save_v1';
export function loadSave(){
  try{ const raw=localStorage.getItem(KEY); if(!raw) return defaultSave();
    const s=Object.assign(defaultSave(), JSON.parse(raw));
    if(typeof s.bp!=='number'){ // ancient pre-BP save: main currency was called RP
      if(typeof s.rp==='number'&&typeof s.rb!=='number'){ s.bp=s.rp; s.rp=0; }
      else s.bp=0;
    }
    if(typeof s.rb==='number'){ s.rp=s.rb; delete s.rb; } // legacy RB → RP
    if(typeof s.rp!=='number') s.rp=0;
    s.ups=s.ups||{}; s.rpups=s.rpups||{}; s.perm=s.perm||{}; s.adv=s.adv||{};
    return s;
  }catch(e){ return defaultSave(); }
}
export function storeSave(s){ try{ localStorage.setItem(KEY, JSON.stringify(s)); }catch(e){ showSaveWarn(); } }
export function showSaveWarn(){ const w=document.getElementById('warn'); if(w) w.textContent='SAVE FAILED — STORAGE BLOCKED'; }
export function exportStr(s){ return btoa(unescape(encodeURIComponent(JSON.stringify(s)))); }
export function importStr(str){
  const s=Object.assign(defaultSave(), JSON.parse(decodeURIComponent(escape(atob(str.trim())))));
  if(typeof s.bp!=='number'&&typeof s.rp!=='number'&&typeof s.rb!=='number') throw new Error('bad save');
  if(typeof s.bp!=='number'){
    if(typeof s.rp==='number'&&typeof s.rb!=='number'){ s.bp=s.rp; s.rp=0; }
    else s.bp=0;
  }
  if(typeof s.rb==='number'){ s.rp=s.rb; delete s.rb; }
  if(typeof s.rp!=='number') s.rp=0;
  s.ups=s.ups||{}; s.rpups=s.rpups||{}; s.perm=s.perm||{}; s.adv=s.adv||{}; return s;
}
// Derived stats from levels + ladder tier:
// Permanent (RP) upgrades: survive every rebirth.
export const PERM=[
 {id:'emass', name:'Eternal Core', max:20, base:2, desc:'+10% mass & gains, forever.'},
 {id:'egreed', name:'Eternal Greed', max:20, base:2, desc:'+10% blackhole points, forever.'},
 {id:'eengine', name:'Eternal Engine', max:20, base:1, desc:'+6% thrust, forever.'},
];
// Rebirth multiplier: 1.1x base, +0.1x per rebirth, × eternal core.
export function permMass(save){ return (1.1+0.1*(save.rebirths||0))*(1+0.10*((save.perm||{}).emass||0)); }
// Advancements: one-time milestones, +2 BP each, kept forever.
export const ADV=[
 {id:'first', name:'First Contact', desc:'Absorb your first body.'},
 {id:'m100', name:'Triple Digits', desc:'Reach 100 mass in one run.'},
 {id:'m1000', name:'Kiloton', desc:'Reach 1,000 mass in one run.'},
 {id:'mega', name:'Colossus Slayer', desc:'Swallow a MEGA colossus.'},
 {id:'gold', name:'Gold Rush', desc:'Eat a golden body.'},
 {id:'dia', name:'Pressure Made', desc:'Eat a diamond body.'},
 {id:'combo', name:'Feeding Frenzy', desc:'Reach a 4x combo.'},
 {id:'win', name:'God Eater', desc:'Beat any black hole.'},
 {id:'t5', name:'Halfway to Hell', desc:'Reach Tier 5.'},
 {id:'t10', name:'Edge of Infinity', desc:'Reach Tier 10.'},
 {id:'reborn', name:'Born Again', desc:'Rebirth once.'},
];
export function stats(save){
  const L=id=>(save.ups[id]||0)+(save.rpups[id]||0); // BP levels reset on rebirth; RP levels are permanent
  const P=id=>(save.perm||{})[id]||0;
  const mob=(typeof document!=='undefined')&&document.body.classList.contains('touch');
  const ti=tierInfo(save.tier);
  const pm=permMass(save);
  return {
    M0: 10*Math.pow(1.25,L('core'))*ti.m0Mult*pm,
    gain: (1+0.15*L('dense'))*ti.gainMult*pm,
    thrust: 1+0.20*L('ion'),
    thr: (mob?120:95)*(1+0.20*L('ion'))*(1+0.06*P('eengine')),
    maxSpd: mob?860:780,
    damp: mob?0.995:0.996,
    predSecs: 3+L('nav')*1.2,
    hull: Math.pow(0.94,L('hull')),
    stab: L('stab'),
    brake: L('brake'),
    magnet: 1+0.18*L('hill'),
    roche: L('roche'),
    harv: L('harv'),
    clock: ti.clockBase+15*L('time'),
    hawk: L('hawk'),
    bpMult: (1+0.12*L('res'))*(1+0.10*P('egreed')),
    twin: L('twin'),
  };
}
export function nextTip(def, lvl, cap, tier){
  const cur=curVal(def.id,lvl,tier), nx=curVal(def.id,lvl+1,tier);
  return lvl>=(cap||def.max)?'MAXED':(''+cur+' → '+nx);
}
function curVal(id,lvl,tier){
  switch(id){
    case 'core': return (10*Math.pow(1.25,lvl)).toFixed(1)+' M0';
    case 'dense': return '+'+(15*lvl)+'%';
    case 'twin': return 'lvl '+lvl;
    case 'ion': return '+'+(20*lvl)+'%';
    case 'nav': return (3+lvl*1.2).toFixed(1)+'s arc';
    case 'hull': return '×'+Math.pow(0.94,lvl).toFixed(2)+' radius';
    case 'stab': return lvl>0?('lock '+(0.7+0.3*lvl).toFixed(1)+'x'):'LOCKED';
    case 'brake': return lvl>0?('+'+(40*lvl)+'% power'):'LOCKED';
    case 'hill': return '+'+(18*lvl)+'%';
    case 'roche': return lvl>0?'ON '+lvl:'OFF';
    case 'harv': return '+'+lvl+' m/s';
    case 'time': return (tierInfo(tier).clockBase+15*lvl)+'s';
    case 'hawk': return '-'+(8*lvl)+'% growth';
    case 'res': return '+'+(12*lvl)+'% BP';
    default: return 'lvl '+lvl;
  }
}
