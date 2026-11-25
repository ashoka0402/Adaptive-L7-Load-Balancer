// Pure data transforms used by both the console and its unit tests.
export const ALGORITHMS = {
  round_robin: "Round Robin", weighted_round_robin: "Weighted RR",
  least_connections: "Least Connections", latency_aware: "EWMA Latency",
  adaptive: "Pulse Adaptive", predictive: "Pulse Predictive",
  autopilot: "Pulse Autopilot", learning: "Contextual Learning"
};
export const TERMINAL = new Set(["completed", "failed", "cancelled"]);
export function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, char => ({
    "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"
  })[char]);
}
export function num(value, digits = 1) {
  return value == null || !Number.isFinite(Number(value)) ? "—" :
    Number(value).toLocaleString("en-US", {maximumFractionDigits:digits});
}
export const pct = value => value == null ? "—" : num(value*100,1)+"%";
export const clamp = (value,min,max) => Math.max(min,Math.min(max,value));
export const algorithmName = value => ALGORITHMS[value] ?? value ?? "—";
export function frameAt(frames, seconds) {
  if (!frames?.length) return null;
  let lo=0,hi=frames.length-1;
  while(lo<hi){const mid=Math.ceil((lo+hi)/2);if(frames[mid].time<=seconds)lo=mid;else hi=mid-1;}
  return frames[lo].time<=seconds ? frames[lo] : null;
}
export function topologyNodes(frame, previous) {
  return Object.entries(frame?.nodes ?? {}).map(([id,n]) => {
    const old=previous?.nodes?.[id];
    const dt=(frame?.time ?? 0)-(previous?.time ?? 0);
    const rate=old?.routed!=null && n.routed!=null && dt>0 ? Math.max(0,(n.routed-old.routed)/dt) : null;
    return {id,active:n.active ?? 0,queue:n.queue ?? null,capacity:n.capacity,
      down:n.down ?? false,probability:n.probability ?? null,rate,
      status:n.down?"OFFLINE":(n.active >= n.capacity?"SATURATED":"HEALTHY")};
  });
}
export function liveNodes(current, previous) {
  const dt=(current?.timestamp ?? 0)-(previous?.timestamp ?? 0);
  return (current?.backends ?? []).map(n => {
    const old=previous?.backends?.find(x=>x.id===n.id);
    return {id:n.id,active:n.active_connections,queue:n.queue,capacity:n.capacity,
      down:!n.eligible,status:!n.enabled?"DISABLED":n.circuit_state==="OPEN"?"CIRCUIT OPEN":n.circuit_state==="HALF_OPEN"?"RECOVERING":n.status,
      probability:n.probability,latency:n.average_latency_ms,
      rate:old && dt>0 && n.total_requests>=old.total_requests ? (n.total_requests-old.total_requests)/dt : null};
  });
}
export function seriesFromRequests(run, step=.25) {
  const buckets=new Map();
  for(const r of run?.requests ?? []){
    const bucket=Math.floor(r.completed/step);
    if(!buckets.has(bucket))buckets.set(bucket,[]);
    buckets.get(bucket).push(r);
  }
  if(!buckets.size)return [];
  const last=Math.max(...buckets.keys());
  return Array.from({length:last+1},(_,i)=>{
    const rows=buckets.get(i)??[],latencies=rows.map(r=>r.latency_ms).sort((a,b)=>a-b);
    const percentile=q=>latencies.length?latencies[Math.max(0,Math.ceil(q*latencies.length)-1)]:null;
    const success=rows.filter(r=>r.status==="ok").length;
    return {time:(i+1)*step,rps:success/step,p50:percentile(.5),p95:percentile(.95),p99:percentile(.99),error:rows.length?(rows.length-success)/rows.length*100:null};
  });
}
export function runProgress(job){
  if(!job)return 0;if(job.state==="completed")return 100;
  const total=(job.spec?.repetitions??1)*(job.spec?.algorithms?.length??1);
  const p=job.progress??{};const fraction=p.offered?clamp(p.completed/p.offered,0,1):0;
  return clamp(((job.runs?.length??0)+fraction)/Math.max(total,1)*100,0,99.9);
}
export function validateDraft(spec){
  if(!spec.algorithms.length)throw Error("Select at least one routing algorithm.");
  if(!spec.nodes.length)throw Error("Add at least one backend.");
  if(new Set(spec.nodes.map(n=>n.id)).size!==spec.nodes.length)throw Error("Backend IDs must be unique.");
  if(spec.faults.some(f=>!spec.nodes.some(n=>n.id===f.backend)))throw Error("Every fault must target a configured backend.");
  return spec;
}
