// Newtonian orbital physics: semi-implicit Euler + sub-stepping.
// Gentle arcade gravity: only a slight pull so WASD stays in charge.
export const G = 900;
export function schwarzschild(B){ return 26 * Math.cbrt(B / 10000); }
// Visual + event-horizon scale: imposing hole that dominates the arena.
export function bhRadius(B){ return 88 * Math.pow(B / 10000, 0.34); }
// Hard circular arena boundary (base), growing slowly as the hole bulks up late-game.
export const BOUNDARY_R = 2600;
export function boundaryR(B, B0){ return BOUNDARY_R * Math.pow(Math.max(1, B / B0), 0.15); }
// Orbit Stabilizer: ease velocity toward a clean circular orbit at current radius,
// preserving the player's existing prograde/retrograde direction.
export function stabilize(s, B, dt, rate){
  const r = Math.max(Math.hypot(s.x, s.y), 1);
  const circ = Math.sqrt(G * B / r);
  let tx = -s.y / r, ty = s.x / r;
  if(s.vx * tx + s.vy * ty < 0){ tx = -tx; ty = -ty; }
  const k = 1 - Math.exp(-2.5 * (rate||1) * dt);
  s.vx += (tx * circ - s.vx) * k;
  s.vy += (ty * circ - s.vy) * k;
}
export function holeMass(B0, t, hawkingLvl){
  // base exponential +0.18%/s (slow, beatable pressure), Hawking slows it further
  const rate = 0.0018 * Math.pow(0.92, hawkingLvl);
  return B0 * Math.exp(rate * t);
}
export function circularOrbitVel(r, B){ return Math.sqrt(G * B / Math.max(r, 1)); }
// Integrate player state {x,y,vx,vy} toward origin with thrust accel.
export function stepBody(s, B, dt, thrust, maxSpd){
  const SUB = 4, h = dt / SUB;
  const thrusting = thrust && Math.hypot(thrust.ax, thrust.ay) > 1;
  for(let i=0;i<SUB;i++){
    const r2 = Math.max(s.x*s.x + s.y*s.y, 64);
    const r = Math.sqrt(r2);
    const a = G * B / r2;
    let ax = -a * s.x / r + (thrust?thrust.ax:0);
    let ay = -a * s.y / r + (thrust?thrust.ay:0);
    s.vx += ax*h; s.vy += ay*h;
    if(!thrusting){ s.vx *= 0.995; s.vy *= 0.995; } // coast damping: smooth orbits
    s.x += s.vx*h; s.y += s.vy*h;
  }
  if(maxSpd){ // delta-v cap
    const sp = Math.hypot(s.vx, s.vy);
    if(sp > maxSpd){ s.vx *= maxSpd/sp; s.vy *= maxSpd/sp; }
  }
  return s;
}
// Predict next `secs` of path (no thrust) for trajectory arc.
export function predictPath(s, B, secs, n=42){
  const pts=[]; let x=s.x,y=s.y,vx=s.vx,vy=s.vy; const h=secs/n;
  for(let i=0;i<n;i++){
    for(let k=0;k<3;k++){
      const r2=Math.max(x*x+y*y,64), r=Math.sqrt(r2), a=G*B/r2;
      vx+=-a*x/r*(h/3); vy+=-a*y/r*(h/3); x+=vx*(h/3); y+=vy*(h/3);
    }
    pts.push({x,y,r:Math.hypot(x,y)});
  }
  return pts;
}
