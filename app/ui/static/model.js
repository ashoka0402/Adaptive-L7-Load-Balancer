export const ALGORITHMS = {
  round_robin: "Round Robin", weighted_round_robin: "Weighted Round Robin",
  least_connections: "Least Connections", latency_aware: "EWMA"
};
export const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
export const number = (value, digits=0) => typeof value === "number" && Number.isFinite(value) ? value.toLocaleString("en-US",{maximumFractionDigits:digits,minimumFractionDigits:digits}) : "—";
export const share = (part,total) => total > 0 ? Math.max(0,Math.min(100,100*part/total)) : 0;
export function arenaSpec(mode,rps,duration,pattern) {
  if (!["http","simulation"].includes(mode) || !Number.isInteger(rps) || rps<1 || rps>100 || !Number.isInteger(duration) || duration<1 || duration>10 || !["steady","burst"].includes(pattern)) throw Error("Invalid benchmark settings");
  return {mode,algorithms:Object.keys(ALGORITHMS),rps,duration,pattern,seed:7,concurrency:32,total_requests:4000,repetitions:1};
}
