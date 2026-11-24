// Canvas2D renderer: parallax stars, black hole, bodies, player, predictor.
// One nebula identity per ladder tier — Tier 1 is the Horsehead Ember.
export const THEMES=[
 {name:'Horsehead Ember', bg:['#0c0605','#04060c'], disk:['rgba(255,110,60,0.55)','rgba(255,230,180,0.18)','rgba(255,150,120,0.75)'], accent:'#ff9a5c'},
 {name:'Violet Veil', bg:['#0d0716','#04060c'], disk:['rgba(190,110,255,0.55)','rgba(230,200,255,0.18)','rgba(140,220,255,0.75)'], accent:'#c99aff'},
 {name:'Teal Abyss', bg:['#04100f','#04060c'], disk:['rgba(60,255,200,0.50)','rgba(200,255,240,0.16)','rgba(120,200,255,0.75)'], accent:'#39e6ff'},
 {name:'Golden Expanse', bg:['#100b03','#04060c'], disk:['rgba(255,190,60,0.55)','rgba(255,245,210,0.20)','rgba(255,220,130,0.80)'], accent:'#ffd34d'},
 {name:'Crimson Storm', bg:['#100404','#04060c'], disk:['rgba(255,60,80,0.55)','rgba(255,200,200,0.18)','rgba(255,130,150,0.78)'], accent:'#ff5c72'},
 {name:'Jade Drift', bg:['#041007','#04060c'], disk:['rgba(60,255,140,0.50)','rgba(220,255,230,0.16)','rgba(150,255,190,0.75)'], accent:'#34f5a5'},
 {name:'Cobalt Deep', bg:['#040a18','#04060c'], disk:['rgba(70,130,255,0.55)','rgba(210,230,255,0.18)','rgba(150,200,255,0.80)'], accent:'#6aa8ff'},
 {name:'Magenta Rift', bg:['#100412','#04060c'], disk:['rgba(255,70,200,0.50)','rgba(255,210,245,0.16)','rgba(200,160,255,0.75)'], accent:'#ff6ad5'},
 {name:'Pale Aurora', bg:['#0a1014','#04060c'], disk:['rgba(170,255,240,0.45)','rgba(240,255,252,0.20)','rgba(200,255,250,0.80)'], accent:'#a8fff2'},
 {name:'Prismatic Void', bg:['#0b0714','#04060c'], disk:['rgba(255,170,60,0.50)','rgba(200,255,220,0.16)','rgba(150,220,255,0.80)'], accent:'#ffe9a8'},
];
export function tierTheme(t){ return THEMES[((t||1)-1)%THEMES.length]; }
// Seeded, slow-drifting nebula clouds per theme — painted, never downloaded.
const nebCache={};
function nebFor(th){
  if(nebCache[th.name]) return nebCache[th.name];
  let s=0; for(const ch of th.name) s=(s*31+ch.charCodeAt(0))>>>0;
  const rnd=()=>((s=(s*16807)%2147483647)/2147483647);
  const blobs=[];
  for(let i=0;i<16;i++) blobs.push({x:rnd()*4000-2000,y:rnd()*4000-2000,r:220+rnd()*420,
    c:i%2?th.disk[0]:th.accent, a:0.05+rnd()*0.09, ph:rnd()*6.28, sp:0.05+rnd()*0.12});
  nebCache[th.name]=blobs; return blobs;
}
export function makeStars(n, seed){
  let s=seed; const rnd=()=>((s=(s*16807)%2147483647)/2147483647);
  const a=[]; for(let i=0;i<n;i++) a.push({x:rnd()*4000-2000,y:rnd()*4000-2000,r:rnd()*1.6+0.3,t:rnd()*6.28});
  return a;
}
// Lensing: displace star by bending away from center near horizon.
function lens(x,y,cx,cy,Rs){
  const dx=x-cx, dy=y-cy, r=Math.hypot(dx,dy)||1;
  const bend = Math.min(60, Rs*Rs*2.2/Math.max(r,1));
  return [x+dx/r*bend, y+dy/r*bend];
}
export function drawWorld(ctx, S){
  const {cam, starsFar, starsNear, Rs} = S;
  const th=S.theme||THEMES[2];
  const bgg=ctx.createLinearGradient(0,0,0,ctx.canvas.height);
  bgg.addColorStop(0,th.bg[0]); bgg.addColorStop(1,th.bg[1]);
  ctx.fillStyle=bgg; ctx.fillRect(0,0,ctx.canvas.width,ctx.canvas.height);
  const W=ctx.canvas.width, H=ctx.canvas.height;
  // nebula wash: additive drifting clouds in this tier's palette
  ctx.save(); ctx.globalCompositeOperation='lighter';
  for(const nb of nebFor(th)){
    const bx2=(nb.x*0.12-cam.x*0.12)*cam.z+W/2+Math.cos(S.time*nb.sp+nb.ph)*30;
    const by2=(nb.y*0.12-cam.y*0.12)*cam.z+H/2+Math.sin(S.time*nb.sp+nb.ph)*30;
    if(bx2<-700||by2<-700||bx2>W+700||by2>H+700) continue;
    const nr=nb.r*cam.z+40;
    const ng=ctx.createRadialGradient(bx2,by2,0,bx2,by2,nr);
    ng.addColorStop(0,nb.c); ng.addColorStop(1,'rgba(0,0,0,0)');
    ctx.globalAlpha=nb.a; ctx.fillStyle=ng;
    ctx.fillRect(bx2-nr,by2-nr,nr*2,nr*2);
  }
  ctx.restore(); ctx.globalAlpha=1;
  const toX=x=>(x-cam.x)*cam.z+W/2, toY=y=>(y-cam.y)*cam.z+H/2;
  // parallax star layers
  for(const [layer,alpha] of [[starsFar,0.55],[starsNear,0.95]]){
    const par = layer===starsFar?0.25:0.55;
    for(const st of layer){
      let sx=(st.x*par-cam.x*par)*cam.z+W/2, sy=(st.y*par-cam.y*par)*cam.z+H/2;
      if(sx<-10||sy<-10||sx>W+10||sy>H+10) continue;
      const tw=0.6+0.4*Math.sin(S.time*2+st.t);
      ctx.globalAlpha=alpha*tw;
      ctx.fillStyle='#cfe4ff';
      const r=st.r*cam.z*0.9+0.3;
      ctx.fillRect(sx,sy,r,r);
    }
  }
  ctx.globalAlpha=1;
  const bx=toX(0), by=toY(0), rs=Rs*cam.z;
  // lensed ghost stars near hole
  ctx.save();
  for(let i=0;i<40;i++){
    const a=i/40*6.283+S.time*0.05, rr=rs*(1.3+(i%5)*0.35);
    const gx=bx+Math.cos(a)*rr, gy=by+Math.sin(a)*rr*0.9;
    ctx.globalAlpha=0.5; ctx.fillStyle='#9fd8ff'; ctx.fillRect(gx,gy,1.6,1.6);
  }
  ctx.restore(); ctx.globalAlpha=1;
  // accretion disk: layered ellipses with doppler beaming (4.2x horizon radius)
  const diskR=rs*4.2;
  if(diskR>2){
    ctx.save(); ctx.translate(bx,by);
    for(let L=3;L>=1;L--){
      const rr=diskR*(0.55+0.22*L);
      ctx.save(); ctx.scale(1,0.38); ctx.rotate(-0.25+S.time*0.02*L);
      const g=ctx.createLinearGradient(-rr,0,rr,0);
      g.addColorStop(0,th.disk[0]); g.addColorStop(0.5,th.disk[1]);
      g.addColorStop(1,th.disk[2]);
      ctx.strokeStyle=g; ctx.lineWidth=Math.max(2,rs*0.16*L*0.6);
      ctx.beginPath();
      for(let k=0;k<=64;k++){ const a=k/64*6.283; const w=1+0.08*Math.sin(a*5+S.time*2+L*2);
        const px=Math.cos(a)*rr*w, py=Math.sin(a)*rr*w; k?ctx.lineTo(px,py):ctx.moveTo(px,py); }
      ctx.closePath(); ctx.stroke(); ctx.restore();
    }
    ctx.restore();
  }
  // photon ring + horizon
  if(rs>0.5){
    const g2=ctx.createRadialGradient(bx,by,rs*0.8,bx,by,rs*1.35);
    g2.addColorStop(0,'rgba(0,0,0,1)'); g2.addColorStop(0.75,'rgba(160,220,255,0.9)'); g2.addColorStop(1,'rgba(160,220,255,0)');
    ctx.fillStyle=g2; ctx.beginPath(); ctx.arc(bx,by,rs*1.35,0,6.29); ctx.fill();
    ctx.fillStyle='#000'; ctx.beginPath(); ctx.arc(bx,by,rs,0,6.29); ctx.fill();
  }
  // bodies
  for(const b of S.bodies){
    const sx=toX(b.x), sy=toY(b.y);
    if(sx<-60||sy<-60||sx>W+60||sy>H+60) continue;
    const r=Math.max(2,b.r*cam.z);
    drawBody(ctx,b,sx,sy,r,S);
  }
  // trajectory predictor
  if(S.predict&&S.predict.length){
    for(let i=0;i<S.predict.length;i++){
      const p=S.predict[i], sx=toX(p.x), sy=toY(p.y);
      let col='#39e6ff';
      if(p.r<Rs*1.6) col='#ff3b57'; else if(p.r<Rs*3.2) col='#ffb347';
      ctx.fillStyle=col; ctx.globalAlpha=0.85-i/S.predict.length*0.5;
      ctx.fillRect(sx,sy,2.4,2.4);
    }
    ctx.globalAlpha=1;
  }
  // outer containment ring: glowing futuristic dashed wall
  if(S.BR){
    const br=S.BR*cam.z;
    ctx.save();
    ctx.strokeStyle=S.nearEdge||S.edgeT>0?'rgba(255,70,90,.9)':th.accent;
    ctx.lineWidth=S.edgeT>0?3.5:2;
    ctx.shadowBlur=S.edgeT>0?22:10;
    ctx.shadowColor=S.nearEdge||S.edgeT>0?'#ff465a':th.accent;
    ctx.setLineDash([26,18]);
    ctx.lineDashOffset=-S.time*40;
    ctx.beginPath(); ctx.arc(bx,by,br,0,6.29); ctx.stroke();
    ctx.setLineDash([]); ctx.shadowBlur=0;
    // warning ticks when the player nears the edge
    if(S.nearEdge||S.edgeT>0){
      ctx.strokeStyle='rgba(255,180,70,.95)'; ctx.lineWidth=3;
      for(let k=0;k<36;k++){ const a=k/36*6.283;
        const c=Math.cos(a), s2=Math.sin(a);
        // ticks cluster toward the player's side of the ring
        ctx.beginPath();
        ctx.moveTo(bx+c*(br-12),by+s2*(br-12));
        ctx.lineTo(bx+c*(br+2),by+s2*(br+2));
        ctx.stroke(); }
    }
    ctx.restore();
  }
  // spark particles (containment bounce)
  if(S.sparks) for(const sp of S.sparks){
    const sx=toX(sp.x), sy=toY(sp.y);
    ctx.globalAlpha=Math.max(0,Math.min(1,sp.life*1.8));
    ctx.fillStyle=sp.c||'#aef3ff';
    ctx.fillRect(sx-1.5,sy-1.5,3,3);
  }
  ctx.globalAlpha=1;
  // player
  drawPlayer(ctx,S,toX(S.p.x),toY(S.p.y),S);
  if(S.stab){ // stabilizer lock indicator
    const sx=toX(S.p.x), sy=toY(S.p.y);
    ctx.save(); ctx.strokeStyle='rgba(52,245,165,.9)'; ctx.setLineDash([6,6]);
    ctx.lineDashOffset=S.time*30; ctx.lineWidth=1.6;
    ctx.beginPath(); ctx.arc(sx,sy,Math.max(8,S.p.r*cam.z+12),0,6.29); ctx.stroke();
    ctx.restore();
  }
  // moonlet
  if(S.moon){ const mx=toX(S.moon.x),my=toY(S.moon.y);
    ctx.fillStyle='#9adcff'; ctx.beginPath(); ctx.arc(mx,my,Math.max(2,5*cam.z),0,6.29); ctx.fill(); }
}
function drawBody(ctx,b,sx,sy,r,S){
  const edible=b.mass<S.p.M;
  ctx.save(); ctx.translate(sx,sy); ctx.rotate(b.rot||0);
  if(b.tier===5){ // MEGA colossus: radiant gold with crackling crown
    ctx.shadowBlur=24; ctx.shadowColor='#ffd34d';
    const g=ctx.createRadialGradient(0,0,r*0.2,0,0,r);
    g.addColorStop(0,'#fff6d8'); g.addColorStop(0.55,'#f5b93c'); g.addColorStop(1,'#7a4a12');
    ctx.fillStyle=g; ctx.beginPath(); ctx.arc(0,0,r,0,6.29); ctx.fill();
    ctx.shadowBlur=0;
    ctx.strokeStyle='rgba(255,240,190,.9)'; ctx.lineWidth=2.5;
    ctx.beginPath(); ctx.arc(0,0,r*1.18+Math.sin(S.time*4+b.seed)*2,0,6.29); ctx.stroke();
    ctx.fillStyle='rgba(90,50,5,.5)';
    ctx.beginPath(); ctx.arc(-r*0.3,-r*0.15,r*0.22,0,6.29); ctx.arc(r*0.25,r*0.3,r*0.16,0,6.29); ctx.fill();
  }
  if(b.shiny==='gold'){ ctx.shadowBlur=18; ctx.shadowColor='#ffd34d'; }
  if(b.shiny==='diamond'){ ctx.shadowBlur=22; ctx.shadowColor='#d8fbff'; }
  if(b.tier===0){ // comet: bright head + fading tail along its motion
    const sp2=Math.max(1,Math.hypot(b.vx,b.vy)), tx=-b.vx/sp2, ty=-b.vy/sp2;
    const tg=ctx.createLinearGradient(0,0,tx*r*4,ty*r*4);
    tg.addColorStop(0,'rgba(191,233,255,.85)'); tg.addColorStop(1,'rgba(191,233,255,0)');
    ctx.strokeStyle=tg; ctx.lineWidth=Math.max(1.5,r*0.7); ctx.lineCap='round';
    ctx.beginPath(); ctx.moveTo(0,0); ctx.lineTo(tx*r*4,ty*r*4); ctx.stroke();
    ctx.fillStyle='#eaf7ff'; ctx.globalAlpha=0.95;
    ctx.beginPath(); ctx.arc(0,0,r*0.85,0,6.29); ctx.fill();
    ctx.fillStyle='rgba(255,255,255,.95)'; ctx.beginPath(); ctx.arc(-r*0.28,-r*0.28,r*0.32,0,6.29); ctx.fill();
    ctx.globalAlpha=1;
  } else if(b.tier===1){ ctx.fillStyle='#8a7f74'; ctx.beginPath();
    for(let k=0;k<8;k++){const a=k/8*6.283,w=1+0.18*Math.sin(k*3+b.seed);const px=Math.cos(a)*r*w,py=Math.sin(a)*r*w;k?ctx.lineTo(px,py):ctx.moveTo(px,py);}
    ctx.closePath(); ctx.fill(); ctx.fillStyle='rgba(0,0,0,.35)';
    ctx.beginPath(); ctx.arc(-r*0.3,-r*0.2,r*0.28,0,6.29); ctx.arc(r*0.3,r*0.25,r*0.2,0,6.29); ctx.fill();
  } else if(b.tier===2){ const g=ctx.createLinearGradient(-r,-r,r,r);
    g.addColorStop(0,'#ffd9a0'); g.addColorStop(0.5,'#8c5a2b'); g.addColorStop(1,'#3a2415');
    ctx.fillStyle=g; ctx.beginPath(); ctx.arc(0,0,r,0,6.29); ctx.fill();
    ctx.strokeStyle='rgba(255,240,200,.5)'; ctx.beginPath(); ctx.arc(0,0,r*0.6,0.6,2.4); ctx.stroke();
  } else if(b.tier===3){ const g=ctx.createLinearGradient(0,-r,0,r);
    g.addColorStop(0,'#e8b06a'); g.addColorStop(0.5,'#7a4a8c'); g.addColorStop(1,'#2c5a7a');
    ctx.fillStyle=g; ctx.beginPath(); ctx.arc(0,0,r,0,6.29); ctx.fill();
    ctx.strokeStyle='rgba(255,255,255,.35)'; for(let k=-1;k<=1;k++){ctx.beginPath();ctx.ellipse(0,k*r*0.3,r*0.95,r*0.2,0,0,6.29);ctx.stroke();}
    if(b.moon){ ctx.fillStyle='#ccc'; ctx.beginPath(); ctx.arc(r*1.3,0,r*0.25,0,6.29); ctx.fill(); }
  } else { ctx.fillStyle='#eaf6ff'; ctx.beginPath(); ctx.arc(0,0,r,0,6.29); ctx.fill();
    ctx.strokeStyle='#7fd0ff'; ctx.lineWidth=2; ctx.beginPath(); ctx.arc(0,0,r*1.35,0,6.29); ctx.stroke(); }
  ctx.restore();
  // reticles
  const d=Math.hypot(b.x-S.p.x,b.y-S.p.y);
  if(d<420){
    ctx.save(); ctx.translate(sx,sy);
    if(edible){ ctx.strokeStyle='rgba(52,245,165,.8)'; ctx.setLineDash([4,4]);
      ctx.beginPath(); ctx.arc(0,0,r+7,0,6.29); ctx.stroke(); ctx.setLineDash([]); }
    else { ctx.strokeStyle='rgba(255,59,87,.85)'; ctx.lineWidth=1.6;
      ctx.beginPath(); ctx.arc(0,0,r+5,0,6.29); ctx.stroke(); }
    ctx.restore();
  }
  // nav-computer edge ping for high-value metals
  if(S.showPings&&((b.tier===2||b.tier===5)||b.shiny)&&edible){
    ctx.fillStyle=b.shiny==='diamond'?'#d8fbff':(b.shiny==='gold'?'#ffd34d':(b.tier===5?'#ffe9a8':'#ffd166')); ctx.font='11px sans-serif';
    const dpr=Math.min(2,devicePixelRatio||1);
    const cx=Math.max(14,Math.min(ctx.canvas.width/dpr-90,sx)), cy=b.y<S.p.y?26:ctx.canvas.height/dpr-26;
    ctx.fillText(b.shiny==='diamond'?('◈ DIAMOND ×100'):b.shiny==='gold'?('◈ GOLD ×10'):(b.tier===5?('◈ MEGA '+Math.round(b.mass)):('◈ Fe '+Math.round(b.mass))),cx,cy);
  }
}
function drawPlayer(ctx,S,sx,sy){
  const stretch=S.stretch||0;
  ctx.save(); ctx.translate(sx,sy); ctx.rotate(S.p.angle+(stretch>0?Math.atan2(-S.p.y,-S.p.x)-S.p.angle:0)*Math.min(1,stretch));
  const pr=Math.max(4,S.p.r*S.cam.z);
  if(stretch>0) ctx.scale(1+stretch*0.9,1-stretch*0.35);
  // RCS plumes
  if(S.thrustMag>5){ ctx.fillStyle='rgba(120,220,255,.8)';
    ctx.beginPath(); ctx.moveTo(-pr,0); ctx.lineTo(-pr-S.thrustMag*0.12,-pr*0.5); ctx.lineTo(-pr-S.thrustMag*0.12,pr*0.5); ctx.closePath(); ctx.fill(); }
  // craggy mesh
  const v=S.p.mesh;
  ctx.beginPath(); for(let k=0;k<v.length;k++){const px=Math.cos(v[k].a)*pr*v[k].w,py=Math.sin(v[k].a)*pr*v[k].w;k?ctx.lineTo(px,py):ctx.moveTo(px,py);}
  ctx.closePath();
  const g=ctx.createLinearGradient(-pr,-pr,pr,pr);
  g.addColorStop(0,'#a99f92'); g.addColorStop(0.5,'#5d564c'); g.addColorStop(1,'#2b2723');
  ctx.fillStyle=g; ctx.fill();
  ctx.fillStyle='rgba(0,0,0,.4)';
  for(const c of S.p.craters){ ctx.beginPath(); ctx.arc(Math.cos(c.a)*pr*0.55,Math.sin(c.a)*pr*0.55,pr*0.16*c.s,0,6.29); ctx.fill(); }
  ctx.strokeStyle='#7fe8d0'; ctx.lineWidth=1; ctx.beginPath(); ctx.moveTo(-pr*0.5,pr*0.1); ctx.lineTo(0,-pr*0.1); ctx.lineTo(pr*0.5,pr*0.25); ctx.stroke();
  // thermal glow near disk
  if(S.heat>0.02){ ctx.globalAlpha=Math.min(0.85,S.heat); ctx.strokeStyle='#ff7a3c'; ctx.lineWidth=2.5;
    ctx.beginPath(); ctx.arc(0,0,pr+3,0,6.29); ctx.stroke(); ctx.globalAlpha=1; }
  // wake trail
  ctx.restore();
  if(S.trail.length>1){ ctx.save(); ctx.globalAlpha=0.5; ctx.strokeStyle='#39e6ff'; ctx.lineWidth=2;
    ctx.beginPath(); S.trail.forEach((t,i)=>{ const x=(t.x-S.cam.x)*S.cam.z+ctx.canvas.width/2, y=(t.y-S.cam.y)*S.cam.z+ctx.canvas.height/2;
      i?ctx.lineTo(x,y):ctx.moveTo(x,y); }); ctx.stroke(); ctx.restore(); }
}
