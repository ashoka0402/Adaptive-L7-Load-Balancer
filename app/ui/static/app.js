import {ALGORITHMS,esc,number,share,arenaSpec,SCENARIOS} from "./model.js";
const $=id=>document.getElementById(id);
let online=false,loading=false,history=[],jobId=null,jobBusy=false,generating=false,trafficStop=false;
const terminal=new Set(["completed","cancelled","failed"]);
const options=Object.entries(ALGORITHMS).map(([key,label])=>'<option value="'+key+'">'+label+'</option>').join("");
$("strategy").innerHTML=options;
function notice(s){$("notice").textContent=s}
function setConnected(value){
 online=value; $("connection").textContent=value?"Connected":"Not connected";
 $("strategy").disabled=!value; $("generate").disabled=!value||generating; $("run-arena").disabled=!value||jobBusy;
 $("cancel-arena").disabled=!value||!jobBusy;
}
async function api(path,method="GET",body){
 const res=await fetch("/admin/"+path,{method,headers:{"X-Pulse-Local":"1",...(body===undefined?{}:{"Content-Type":"application/json"})},...(body===undefined?{}:{body:JSON.stringify(body)}),cache:"no-store"});
 if(!res.ok){let msg="Request failed ("+res.status+")";try{msg=(await res.json()).error||msg}catch{}throw Error(msg)}
 return res.json();
}
const themeColor=name=>getComputedStyle(document.documentElement).getPropertyValue(name).trim();
function chart(id,series){
 const canvas=$(id),width=canvas.clientWidth,height=canvas.clientHeight;if(!width||!height)return;
 const ratio=Math.min(devicePixelRatio||1,2);canvas.width=width*ratio;canvas.height=height*ratio;
 const ctx=canvas.getContext("2d");ctx.scale(ratio,ratio);
 const right=width-12,left=40,top=12,bottom=height-24;
 const now=history.at(-1)?.time||Date.now()/1000;
 const samples=history.filter(x=>x.time>=now-60);
 const values=samples.flatMap(x=>series.map(([key])=>x[key])).filter(Number.isFinite);
 const max=Math.max(1,...values);
 ctx.font="11px system-ui";ctx.strokeStyle=themeColor("--line");ctx.fillStyle=themeColor("--muted");ctx.lineWidth=1;
 for(let i=0;i<=4;i++){const y=top+(bottom-top)*i/4;ctx.beginPath();ctx.moveTo(left,y);ctx.lineTo(right,y);ctx.stroke();ctx.fillText(number(max*(1-i/4),max<10?1:0),2,y+4)}
 ctx.fillText("−60s",left,height-5);ctx.textAlign="right";ctx.fillText("now",right,height-5);
 for(const [key,color] of series){
  ctx.strokeStyle=themeColor(color);ctx.fillStyle=ctx.strokeStyle;ctx.lineWidth=2;ctx.beginPath();let started=false;
  for(const sample of samples){const v=sample[key];if(!Number.isFinite(v)){started=false;continue}
   const x=right-(now-sample.time)*(right-left)/60,y=bottom-v/max*(bottom-top);
   if(started)ctx.lineTo(x,y);else ctx.moveTo(x,y);started=true;
  }ctx.stroke();
  const last=samples.at(-1);if(last&&Number.isFinite(last[key])){ctx.beginPath();ctx.arc(right,bottom-last[key]/max*(bottom-top),2.8,0,Math.PI*2);ctx.fill()}
 }
 if(!values.length){ctx.fillStyle=themeColor("--muted");ctx.textAlign="center";ctx.fillText("Waiting for traffic",(left+right)/2,(top+bottom)/2)}
}
function redraw(){
 chart("rate-chart",[["rate","--blue"]]);chart("latency-chart",[["p50","--blue"],["latency","--teal"],["p99","--purple"]]);
 chart("pressure-chart",[["active","--blue"],["queue","--amber"]]);chart("error-chart",[["error","--red"]]);
}
function setTheme(theme){
 document.documentElement.dataset.theme=theme;
 $("theme-icon").textContent=theme==="dark"?"☀":"☾";
 $("theme-toggle").setAttribute("aria-label",theme==="dark"?"Switch to light mode":"Switch to dark mode");
 try{localStorage.setItem("pulse-theme",theme)}catch{}
 redraw();
}
let savedTheme="dark";try{savedTheme=localStorage.getItem("pulse-theme")==="light"?"light":"dark"}catch{}
setTheme(savedTheme);
$("theme-toggle").addEventListener("click",()=>setTheme(document.documentElement.dataset.theme==="dark"?"light":"dark"));
function renderLive(data,append=true){
 const m=data.metrics; $("total").textContent=number(m.total);$("rps").textContent=number(m.recent_success_rps_lower_bound);
 $("latency").textContent=m.p95_ms==null?"—":number(m.p95_ms,1)+" ms";$("errors").textContent=number(m.errors);
 $("strategy").value=data.strategy;$("active").textContent=number(data.active)+" active · "+number(data.queue)+" queued";
 const total=data.backends.reduce((sum,b)=>sum+b.total_requests,0);
 $("backends").innerHTML=data.backends.map(b=>{
  const status=!b.enabled?"Disabled":b.circuit_state==="OPEN"?"Circuit open":b.status==="HEALTHY"?"Healthy":b.status==="DRAINING"?"Draining":"Unhealthy";
  const good=status==="Healthy",pct=share(b.total_requests,total);
  return '<tr><td><strong>'+esc(b.id)+'</strong><small>'+esc(b.host)+':'+esc(b.port)+'</small></td><td><span class="status '+(good?"good":"bad")+'">'+status+'</span></td><td>'+number(b.total_requests)+'</td><td><div class="share"><span style="width:'+pct+'%"></span></div>'+number(pct,0)+'%</td><td>'+number(b.active_connections)+'</td><td>'+(b.successful_requests?number(b.average_latency_ms,1)+" ms":"—")+'</td><td><button class="small-button" data-backend="'+esc(b.id)+'" data-enable="'+(!b.enabled)+'">'+(b.enabled?"Disable":"Enable")+'</button></td></tr>'
 }).join("")||'<tr><td colspan="7">No backend servers configured.</td></tr>';
 $("topology-nodes").innerHTML=data.backends.map(b=>'<div class="node '+(b.eligible?"node-up":"node-down")+'"><strong>'+esc(b.id)+'</strong><small>'+ (b.eligible?"Available":"Unavailable")+' · '+number(b.total_requests)+' requests</small><div class="bar-track"><i style="width:'+share(b.total_requests,total)+'%"></i></div></div>').join("")||'<small>No backends</small>';
 if(append){history.push({time:data.timestamp,rate:m.recent_success_rps_lower_bound,latency:m.p95_ms,p50:m.p50_ms,p99:m.p99_ms,active:data.active,queue:data.queue,error:m.error_rate==null?null:m.error_rate*100});history=history.filter(x=>x.time>=data.timestamp-60).slice(-120)}
 redraw();
}
async function refresh(){
 if(loading)return;loading=true;
 try{const data=await api("console");if(!online){history=[];setConnected(true);notice("")}renderLive(data)}catch(e){setConnected(false);trafficStop=true;notice("Engine unavailable · "+e.message)}finally{loading=false}
}
document.querySelectorAll("[data-page]").forEach(btn=>btn.addEventListener("click",()=>{
 const id=btn.dataset.page;for(const page of ["dashboard","arena"])$(page).hidden=id!==page;
 document.querySelectorAll("[data-page]").forEach(b=>{b.classList.toggle("active",b===btn);if(b===btn)b.setAttribute("aria-current","page");else b.removeAttribute("aria-current")});
 if(id==="dashboard")redraw()
}));
$("strategy").addEventListener("change",async()=>{const selected=$("strategy").value;try{await api("strategy","PATCH",{strategy:selected});notice("Routing algorithm changed to "+ALGORITHMS[selected]);await refresh()}catch(e){notice(e.message);await refresh()}});
$("backends").addEventListener("click",async e=>{
 const b=e.target.closest("[data-backend]");if(!b||!online)return;b.disabled=true;
 try{await api("backends/"+encodeURIComponent(b.dataset.backend),"PATCH",{enabled:b.dataset.enable==="true"});await refresh()}catch(err){notice(err.message);b.disabled=false}
});
$("traffic-form").addEventListener("submit",async e=>{
 e.preventDefault();if(!online||generating)return;
 const count=Number($("count").value),concurrency=Number($("concurrency").value),rate=Number($("traffic-rate").value);
 if(!Number.isInteger(count)||count<1||count>1000||!Number.isInteger(concurrency)||concurrency<1||concurrency>50||!Number.isInteger(rate)||rate<0||rate>200)return;
 generating=true;trafficStop=false;setConnected(online);$("stop-traffic").disabled=false;let sent=0,done=0,ok=0,nextStart=performance.now();
 const report=()=>{$("traffic-progress").max=count;$("traffic-progress").value=done;$("traffic-status").textContent=done+" / "+count+" completed · "+ok+" successful · "+(done-ok)+" failed"};
 report();
 async function worker(){while(!trafficStop&&sent<count){sent++;if(rate){const slot=Math.max(nextStart,performance.now());nextStart=slot+1000/rate;while(!trafficStop&&performance.now()<slot)await new Promise(resolve=>setTimeout(resolve,Math.min(100,Math.max(0,slot-performance.now()))));if(trafficStop)return;}try{const r=await fetch("/api/test",{cache:"no-store",signal:AbortSignal.timeout(10000)});await r.arrayBuffer();if(r.status<500)ok++}catch{}done++;report()}}
 try{await Promise.all(Array.from({length:Math.min(count,concurrency)},worker));report();if(trafficStop)$("traffic-status").textContent+=" · stopped"}
 finally{generating=false;$("stop-traffic").disabled=true;setConnected(online);await refresh()}
});
$("stop-traffic").addEventListener("click",()=>{trafficStop=true;$("stop-traffic").disabled=true});
const presets={normal:[200,10,50],burst:[500,50,0],sustained:[1000,25,100]};
document.querySelectorAll("[data-preset]").forEach(b=>b.addEventListener("click",()=>{if(generating)return;const [count,concurrency,rate]=presets[b.dataset.preset];$("count").value=count;$("concurrency").value=concurrency;$("traffic-rate").value=rate}));
function scenarioNote(){$("scenario-note").textContent=SCENARIOS[$("scenario").value]}
$("scenario").addEventListener("change",scenarioNote);scenarioNote();
function bars(id,runs,metric){
 const max=Math.max(1,...runs.map(r=>r.summary[metric]||0));
 $(id).innerHTML=runs.map(r=>'<div class="bar-row"><span>'+esc(ALGORITHMS[r.algorithm]||r.algorithm)+'</span><div class="bar-track"><i style="width:'+share(r.summary[metric]||0,max)+'%"></i></div><strong>'+number(r.summary[metric],1)+'</strong></div>').join("");
}
function renderJob(job){
 $("job-status").textContent=job.state+(job.progress?.algorithm&&!terminal.has(job.state)?" · "+(ALGORITHMS[job.progress.algorithm]||job.progress.algorithm):"")+(job.error?" · "+job.error:"");
 $("result-note").textContent=job.spec.mode==="http"?"Measured local HTTP · one run per algorithm":"Modeled simulation · one run per algorithm";
 $("results").innerHTML=job.runs.map(r=>{const m=r.summary;return '<tr><td><strong>'+esc(ALGORITHMS[r.algorithm]||r.algorithm)+'</strong></td><td>'+number(m.offered)+'</td><td>'+number(m.successful_rps,1)+'</td><td>'+number(m.p95_ms,1)+'</td><td>'+number(m.p99_ms,1)+'</td><td>'+number(m.errors)+'</td></tr>'}).join("")||'<tr><td colspan="6">Waiting for results.</td></tr>';
 bars("throughput-bars",job.runs,"successful_rps");bars("latency-bars",job.runs,"p95_ms");bars("error-bars",job.runs,"errors");
 $("arena-progress").value=job.runs.length;
 $("distribution-bars").innerHTML=job.runs.map(r=>{const entries=Object.entries(r.summary.distribution||{}),total=entries.reduce((s,[,n])=>s+n,0);return '<div class="distribution-row"><strong>'+esc(ALGORITHMS[r.algorithm]||r.algorithm)+'</strong><div class="stack">'+entries.map(([id,n],i)=>'<i class="segment-'+(i%4)+'" style="width:'+share(n,total)+'%" title="'+esc(id)+': '+number(n)+'"></i>').join("")+'</div><small>'+entries.map(([id,n])=>esc(id)+": "+number(n)).join(" · ")+'</small></div>'}).join("");
}
$("arena-form").addEventListener("submit",async e=>{
 e.preventDefault();if(!online||jobBusy)return;jobBusy=true;setConnected(online);
 try{const spec=arenaSpec($("mode").value,Number($("arena-rps").value),Number($("duration").value),$("pattern").value,$("scenario").value);const job=await api("experiments","POST",spec);jobId=job.id;renderJob(job)}
 catch(err){notice(err.message);jobBusy=false;setConnected(online)}
});
$("cancel-arena").addEventListener("click",async()=>{if(!jobId)return;try{renderJob(await api("experiments/"+jobId+"/cancel","POST"))}catch(e){notice(e.message)}});
let pollingJob=false;
async function pollJob(){if(!online||!jobBusy||!jobId||pollingJob)return;pollingJob=true;try{const job=await api("experiments/"+jobId);renderJob(job);if(terminal.has(job.state)){jobBusy=false;setConnected(online)}}catch(e){notice(e.message)}finally{pollingJob=false}}
refresh();
setInterval(()=>{refresh();pollJob()},1000);
window.addEventListener("resize",redraw);
