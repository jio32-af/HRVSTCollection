// Procedural Web Audio synth: hum, crunch, thruster, boom. No files.
let ctx=null, humOsc=null, humGain=null, master=null, muted=false, vol=0.6;
export function initAudio(){
  if(ctx) return;
  try{
    ctx = new (window.AudioContext||window.webkitAudioContext)();
    master = ctx.createGain(); master.gain.value = vol; master.connect(ctx.destination);
    humOsc = ctx.createOscillator(); humOsc.type='sawtooth'; humOsc.frequency.value=38;
    const lp = ctx.createBiquadFilter(); lp.type='lowpass'; lp.frequency.value=160;
    humGain = ctx.createGain(); humGain.gain.value=0.05;
    humOsc.connect(lp); lp.connect(humGain); humGain.connect(master); humOsc.start();
    // meteor wind: one looping airy-noise bed, gain follows thrust (no engine putter)
    const nb=ctx.createBuffer(1,ctx.sampleRate*2,ctx.sampleRate), nd=nb.getChannelData(0);
    let lastv=0; for(let i=0;i<nd.length;i++){ const w=Math.random()*2-1; lastv=(lastv+0.02*w)/1.02; nd[i]=(lastv*3.2+w*0.25)*0.8; }
    const ns=ctx.createBufferSource(); ns.buffer=nb; ns.loop=true;
    windFilter=ctx.createBiquadFilter(); windFilter.type='bandpass'; windFilter.frequency.value=500; windFilter.Q.value=0.6;
    windGain=ctx.createGain(); windGain.gain.value=0;
    ns.connect(windFilter); windFilter.connect(windGain); windGain.connect(master); ns.start();
    const lfo=ctx.createOscillator(), lg=ctx.createGain(); // slow organic flutter on the wind
    lfo.frequency.value=0.4; lg.gain.value=180; lfo.connect(lg); lg.connect(windFilter.frequency); lfo.start();
  }catch(e){}
}
let windFilter=null, windGain=null;
// x: 0..1 thrust amount — eases a natural rushing-wind bed in and out
export function setWind(x){
  if(!ctx||!windGain) return;
  const t=ctx.currentTime, k=Math.max(0,Math.min(1,x));
  windGain.gain.setTargetAtTime(muted?0:k*0.12, t, 0.12);
  windFilter.frequency.setTargetAtTime(350+k*700, t, 0.15);
}
export function setMuted(m){ muted=m; if(master) master.gain.value = m?0:vol; }
export function setVol(v){ vol=v; if(master&&!muted) master.gain.value=v; }
export function resume(){ if(ctx&&ctx.state==='suspended') ctx.resume(); }
// proximity 0(far)..1(at horizon): pitch hum up
export function updateHum(prox){
  if(!ctx||!humOsc) return;
  humOsc.frequency.setTargetAtTime(38+prox*90, ctx.currentTime, 0.2);
  humGain.gain.setTargetAtTime(0.04+prox*0.10, ctx.currentTime, 0.2);
}
export function crunch(size){ // absorption chime: pitch falls with size
  if(!ctx||muted) return;
  const o=ctx.createOscillator(), g=ctx.createGain(); o.type='triangle';
  o.frequency.value = 900/Math.pow(Math.max(size,1),0.25) + 200;
  g.gain.setValueAtTime(0.22,ctx.currentTime); g.gain.exponentialRampToValueAtTime(0.001,ctx.currentTime+0.25);
  o.connect(g); g.connect(master); o.start(); o.stop(ctx.currentTime+0.28);
}
export function thump(){ if(!ctx||muted) return;
  const b=ctx.createBuffer(1,ctx.sampleRate*0.2,ctx.sampleRate), d=b.getChannelData(0);
  for(let i=0;i<d.length;i++) d[i]=(Math.random()*2-1)*Math.pow(1-i/d.length,2);
  const s=ctx.createBufferSource(); s.buffer=b;
  const f=ctx.createBiquadFilter(); f.type='lowpass'; f.frequency.value=900;
  const g=ctx.createGain(); g.gain.value=0.10; s.connect(f); f.connect(g); g.connect(master); s.start();
}
// ---- generative ambient score: one mood per tier, endless and file-free ----
let musicTimer=null, mStep=0;
const ROOTS=[55, 49.00, 58.27, 65.41, 46.25, 51.91, 61.74, 55*1.5, 44, 41.20]; // per-tier root
const MODES=[ // per-tier scale flavors: natural minor, dorian, phrygian, major, ...
 [0,2,3,5,7,8,10],[0,2,3,5,7,9,10],[0,1,5,7,8],[0,2,4,5,7,9,11],
 [0,3,5,7,10],[0,2,3,7,8],[0,2,5,7,9],[0,1,3,5,7,8,10],[0,2,4,7,9],[0,2,3,5,7,8,11]];
const TEMPO=[300,280,260,250,240,230,220,210,200,190]; // ms per 8th, quickens up the ladder
function mnoise(root,semi){ return root*Math.pow(2,semi/12); }
function pad(freq,t,dur,bright){
  const o1=ctx.createOscillator(),o2=ctx.createOscillator(),g=ctx.createGain(),f=ctx.createBiquadFilter();
  o1.type='sawtooth'; o2.type='sawtooth'; o1.frequency.value=freq; o2.frequency.value=freq*1.003;
  f.type='lowpass'; f.frequency.value=300+bright*900;
  g.gain.setValueAtTime(0.0001,t); g.gain.linearRampToValueAtTime(0.05,t+dur*0.4);
  g.gain.linearRampToValueAtTime(0.0001,t+dur);
  o1.connect(f); o2.connect(f); f.connect(g); g.connect(master);
  o1.start(t); o2.start(t); o1.stop(t+dur+0.05); o2.stop(t+dur+0.05);
}
function pluck(freq,t){
  const o=ctx.createOscillator(),g=ctx.createGain(); o.type='sine'; o.frequency.value=freq;
  g.gain.setValueAtTime(0.09,t); g.gain.exponentialRampToValueAtTime(0.001,t+0.9);
  o.connect(g); g.connect(master); o.start(t); o.stop(t+1);
}
function sub(freq,t,dur){
  const o=ctx.createOscillator(),g=ctx.createGain(); o.type='sine'; o.frequency.value=freq/2;
  g.gain.setValueAtTime(0.10,t); g.gain.exponentialRampToValueAtTime(0.001,t+dur);
  o.connect(g); g.connect(master); o.start(t); o.stop(t+dur);
}
export function startMusic(tier){
  stopMusic(); if(!ctx) return;
  const ti=Math.min(9,Math.max(0,(tier||1)-1));
  const root=ROOTS[ti], mode=MODES[ti], stepMs=TEMPO[ti], bright=ti/9;
  mStep=0;
  musicTimer=setInterval(()=>{
    if(muted) { mStep++; return; }
    const t=ctx.currentTime+0.05, s=mStep%64;
    if(s===0){ pad(mnoise(root,0),t,stepMs*32/1000,bright); pad(mnoise(root,mode[2]),t,stepMs*32/1000,bright); }
    if(s===32){ pad(mnoise(root,mode[4%mode.length]),t,stepMs*32/1000,bright); pad(mnoise(root,mode[6%mode.length]),t,stepMs*32/1000,bright); }
    if(s%16===0) sub(mnoise(root,0),t,stepMs*16/1000);
    if(Math.random()<0.35){ const semi=mode[Math.floor(Math.random()*mode.length)]+12; pluck(mnoise(root,semi),t); }
    mStep++;
  }, stepMs);
}
export function stopMusic(){ if(musicTimer){ clearInterval(musicTimer); musicTimer=null; } }
export function boom(win){
  if(!ctx||muted) return;
  const o=ctx.createOscillator(), g=ctx.createGain(); o.type='sine';
  o.frequency.setValueAtTime(win?160:110, ctx.currentTime);
  o.frequency.exponentialRampToValueAtTime(28, ctx.currentTime+1.1);
  g.gain.setValueAtTime(0.5,ctx.currentTime); g.gain.exponentialRampToValueAtTime(0.001,ctx.currentTime+1.2);
  o.connect(g); g.connect(master); o.start(); o.stop(ctx.currentTime+1.25);
}
