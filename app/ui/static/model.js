export const ALGORITHMS = {
  round_robin: "Round Robin", weighted_round_robin: "Weighted Round Robin",
  least_connections: "Least Connections", latency_aware: "EWMA"
};
export const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
export const number = (value, digits=0) => typeof value === "number" && Number.isFinite(value) ? value.toLocaleString("en-US",{maximumFractionDigits:digits,minimumFractionDigits:digits}) : "—";
export const share = (part,total) => total > 0 ? Math.max(0,Math.min(100,100*part/total)) : 0;
export const SCENARIOS = {normal:"Three backends with different service delays.",slow:"Backend 1 gets 150 ms of additional delay between 30% and 75% of the run.",outage:"Backend 1 returns failures from 30% to 75% of the run, then recovers.",overload:"Three slow backends with one concurrent request each. Demonstrates saturation and dropped/failed requests."};
export function arenaSpec(mode,rps,duration,pattern,scenario="normal") {
  if (!["http","simulation"].includes(mode) || !Number.isInteger(rps) || rps<1 || rps>100 || !Number.isInteger(duration) || duration<1 || duration>10 || !["steady","burst","ramp"].includes(pattern) || !Object.hasOwn(SCENARIOS,scenario)) throw Error("Invalid benchmark settings");
  const spec={mode,algorithms:Object.keys(ALGORITHMS),rps,duration,pattern,seed:7,concurrency:32,total_requests:4000,repetitions:1};
  if(scenario==="slow"||scenario==="outage") spec.faults=[{at:duration*.3,backend:"b1",kind:scenario==="slow"?"latency":"down",value:scenario==="slow"?150:0},{at:duration*.75,backend:"b1",kind:"restore"}];
  if(scenario==="overload") spec.nodes=[{id:"b1",capacity:1,latency_ms:100,queue_limit:4},{id:"b2",capacity:1,latency_ms:120,queue_limit:4,weight:2},{id:"b3",capacity:1,latency_ms:150,queue_limit:4}];
  return spec;
}
