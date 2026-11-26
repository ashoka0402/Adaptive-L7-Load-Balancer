/* Local experiment UI; render all untrusted text via textContent. */
(() => {
  const el = id => document.getElementById(id);
  let latest, traces = [], frames = [], playback = null, selectedBackend = "";
  const notice = msg => { el("labNotice").textContent = msg; };
  async function api(path, body) {
    try {
      const r = await fetch("/pulse/api/lab/" + path, body === undefined ? {} : {
        method:"POST", headers:{"Content-Type":"application/json","X-Pulse-Local":"1"},
        body:JSON.stringify(body)
      });
      if (!r.ok) {
        const e = await r.json().catch(()=>({}));
        throw new Error(typeof e.detail === "string" ? e.detail : "Request rejected ("+r.status+")");
      }
      notice("");
      return await r.json();
    } catch (e) { notice(e.message); return null; }
  }
  function table(target, rows, first) {
    const root = el(target); root.replaceChildren();
    if (!rows.length) return;
    const cols = [first,"rps","p95","p99","successPct","rejected","generatorDropped"];
    const labels = [first==="offeredRps"?"Offered req/s":"Strategy / policy","Goodput","p95 ms","p99 ms","Success %","Rejected","Generator drops"];
    const t = document.createElement("table"), head=t.createTHead().insertRow(), body=t.createTBody();
    for(const v of labels) { const th=document.createElement("th");th.textContent=v;head.append(th); }
    for(const r of rows) {
      const tr=body.insertRow();
      for(const k of cols) { const td=tr.insertCell();td.textContent=r[k]??"—"; }
      if(r.saturated) tr.style.color="var(--down)";
    }
    root.append(t);
  }
  function graph(rows) {
    const c=el("breakingGraph"), dpr=window.devicePixelRatio||1, w=c.clientWidth,h=c.clientHeight;
    if(!w||!h)return;
    c.width=Math.round(w*dpr);c.height=Math.round(h*dpr);
    const x=c.getContext("2d");x.scale(dpr,dpr);
    const css=getComputedStyle(document.body), color=n=>css.getPropertyValue(n).trim();
    const left=44,right=w-12,top=24,bottom=h-32,max=800;
    x.font="10px monospace";
    for(let i=0;i<=4;i++){
      const y=bottom-i*(bottom-top)/4;
      x.strokeStyle=color("--line");x.beginPath();x.moveTo(left,y);x.lineTo(right,y);x.stroke();
      x.fillStyle=color("--muted");x.textAlign="right";x.fillText(i*200,left-6,y+3);
    }
    const px=v=>left+(right-left)*v/max, py=v=>bottom-(bottom-top)*Math.min(max,v)/max;
    x.strokeStyle=color("--muted");x.setLineDash([4,4]);x.beginPath();x.moveTo(left,bottom);x.lineTo(right,top);x.stroke();x.setLineDash([]);
    x.strokeStyle=color("--brass");x.lineWidth=2;x.beginPath();
    rows.forEach((r,i)=>i?x.lineTo(px(r.offeredRps),py(r.rps)):x.moveTo(px(r.offeredRps),py(r.rps)));x.stroke();
    for(const r of rows){x.fillStyle=color(r.saturated?"--down":"--ok");x.beginPath();x.arc(px(r.offeredRps),py(r.rps),4,0,Math.PI*2);x.fill();}
    x.fillStyle=color("--muted");x.textAlign="left";x.fillText("Goodput / req/s",left,13);x.textAlign="right";x.fillText("Offered req/s →",right,h-8);
  }
  function showTrace() {
    const t=traces.find(t=>String(t.id)===el("traceSelect").value);
    el("traceView").textContent=t ? [
      "#"+t.id+" · "+t.method+" · HTTP "+t.status+" · "+t.elapsed_ms+" ms",
      ...t.attempts.flatMap((a,i)=>[
        "", "Attempt "+(i+1)+" · "+a.strategy+" · "+(a.reason||""),
        "Selected: "+(a.selected||"none")+" · "+(a.decision||"no eligible backend")+" · "+(a.routing_us??"—")+" µs",
        ...(a.eligible_group?["Eligible group: "+a.eligible_group.join(", ")]:[]),
        ...(a.sampled?["Sampled: "+a.sampled.join(", ")]:[]),
        ...((a.candidates||[]).map(b=>b.id+": "+b.reason+", active="+b.active+", EWMA="+b.ewma_ms+"ms"+(a.scores?.[b.id]===undefined?"":", score="+a.scores[b.id]))),
        "Upstream: "+(a.upstream_status??"not sent")
      ])
    ].join("\n") : "No traces yet. Generate traffic and refresh.";
  }
  function showFrame(){
    const f=frames[Number(el("replaySlider").value)];
    el("replayView").textContent=f?[
      f.t+"s · "+f.strategy, f.rps+" req/s · p95 "+f.p95+"ms · "+f.errors+"% errors",
      ...f.backends.map(b=>b.id+": "+(b.alive?"up":"down")+" · "+b.breaker+" · "+b.active+" active · "+b.fault),
      "", ...f.events.map(e=>e.msg)
    ].join("\n"):"No recording loaded.";
  }
  el("applyFault").onclick=()=>api("fault",{backend:el("faultBackend").value,fault:el("faultKind").value});
  el("scaleToggle").onclick=()=>api("scaling",{enabled:!latest?.lab?.scaling});
  el("canaryApply").onclick=()=>api("canary",{percent:Number(el("canaryPercent").value),faulty:el("canaryFault").checked,auto:el("canaryAuto").checked});
  el("policyApply").onclick=()=>api("policy",Object.fromEntries([...document.querySelectorAll("[data-policy]")].map(n=>[n.dataset.policy,n.checked])));
  document.querySelectorAll("[data-run]").forEach(b=>b.onclick=()=>api("run",{mode:b.dataset.run,scenario:el("labScenario").value,workers:Number(el("labWorkers").value)}));
  document.querySelectorAll("[data-stop]").forEach(b=>b.onclick=()=>api("stop",{}));
  el("traceRefresh").onclick=async()=>{
    const data=await api("traces");if(!data)return; traces=data.traces;
    const select=el("traceSelect");select.replaceChildren();
    for(const t of traces){const o=new Option("#"+t.id+" · "+t.status+" · "+(t.backend||"none"),t.id);select.add(o);}
    showTrace();
  };
  el("traceSelect").onchange=showTrace;
  el("recordStart").onclick=()=>api("record/start",{});
  el("recordStop").onclick=()=>api("record/stop",{});
  el("replayLoad").onclick=async()=>{
    const d=await api("replay");if(!d)return;frames=d.frames;
    el("replaySlider").max=Math.max(0,frames.length-1);el("replaySlider").value=0;showFrame();
  };
  el("replaySlider").oninput=showFrame;
  el("replayPlay").onclick=()=>{
    if(playback){clearInterval(playback);playback=null;el("replayPlay").textContent="Play";return;}
    if(!frames.length)return;el("replaySlider").value=0;showFrame();
    el("replayPlay").textContent="Pause";
    playback=setInterval(()=>{const s=el("replaySlider");if(+s.value>=+s.max){clearInterval(playback);playback=null;el("replayPlay").textContent="Play";return;}s.value=+s.value+1;showFrame();},500);
  };
  el("replayExport").onclick=()=>{
    const url=URL.createObjectURL(new Blob([JSON.stringify(frames,null,2)],{type:"application/json"}));
    const a=document.createElement("a");a.href=url;a.download="pulse-incident.json";a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  };
  let lastResultKey="", policyKey="";
  window.renderLab=s=>{
    latest=s;
    const lab=s.lab;
    if(!lab){document.querySelectorAll("#chaosLab button,#battleLab button,#recordStart,#recordStop,#replayLoad,#replayPlay,#replayExport").forEach(b=>b.disabled=true);el("replayState").textContent="Recording available in Laboratory Mode";return;}
    const select=el("faultBackend"), ids=s.backends.map(b=>b.id).join(",");
    if(ids!==selectedBackend){const prior=select.value;select.replaceChildren();for(const b of s.backends)select.add(new Option(b.id+" · "+(b.version||"v1"),b.id));if(s.backends.some(b=>b.id===prior))select.value=prior;selectedBackend=ids;}
    el("faultState").textContent=s.backends.map(b=>b.id+": "+(b.dead?"offline":b.fault||"normal")).join(" · ");
    el("scaleToggle").textContent=lab.scaling?"Disable autoscaling":"Enable autoscaling";
    el("scaleState").textContent=(lab.scaling?"Enabled":"Disabled")+" · "+s.backends.length+" total servers";
    el("canaryState").textContent=lab.canary.status+" · "+lab.canary.percent+"% v2"+(lab.canary.id?" · "+lab.canary.id:"")+(lab.canary.auto?" · auto-promote":"");
    const pk=JSON.stringify(lab.policy);if(pk!==policyKey){for(const n of document.querySelectorAll("[data-policy]"))n.checked=!!lab.policy[n.dataset.policy];policyKey=pk;}
    const locked=lab.busy||s.arenaRunning;
    for(const id of ["applyFault","scaleToggle","canaryApply","policyApply","recordStart","recordStop"])el(id).disabled=locked;
    document.querySelectorAll("[data-run]").forEach(b=>b.disabled=locked||s.running||lab.scaling||lab.canary.percent>0);
    document.querySelectorAll("[data-stop]").forEach(b=>b.disabled=!lab.busy);
    el("replayState").textContent=(lab.recording?"Recording":"Stopped")+" · "+lab.frames+" frames";
    const status=lab.state+(lab.winner?" · "+(lab.mode==="breaking"?"Highest passing step: ":"Winner: ")+lab.winner:"");
    if(lab.mode==="breaking")el("breakingState").textContent=status;
    if(lab.mode==="arena"||lab.mode==="resilience")el("battleState").textContent=status;
    const rk=JSON.stringify([lab.mode,lab.results]);
    if(rk!==lastResultKey){
      if(lab.mode==="breaking"){table("breakingTable",lab.results,"offeredRps");graph(lab.results);}
      if(lab.mode==="arena"||lab.mode==="resilience")table("battleTable",lab.results,"strategy");
      lastResultKey=rk;
    }
  };
  new ResizeObserver(()=>{if(latest?.lab?.mode==="breaking")graph(latest.lab.results);}).observe(el("breakingGraph"));
  el("theme").addEventListener("click",()=>{if(latest?.lab?.mode==="breaking")graph(latest.lab.results);});
})();
