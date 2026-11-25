import test from "node:test";
import assert from "node:assert/strict";
import {esc,frameAt,topologyNodes,liveNodes,seriesFromRequests,runProgress,validateDraft} from "../app/ui/static/model.js";

test("API text cannot inject markup",()=>{
  assert.equal(esc('<img src=x onerror="alert(1)">'),"&lt;img src=x onerror=&quot;alert(1)&quot;&gt;");
});
test("replay selects the last observed frame without future leakage",()=>{
  const frames=[{time:0},{time:1},{time:2}];
  assert.equal(frameAt(frames,1.5),frames[1]);
  assert.equal(frameAt(frames,-1),null);
});
test("topology animation rate is derived from counter deltas",()=>{
  const a={time:0,nodes:{x:{routed:2,active:1,capacity:3}}};
  const b={time:2,nodes:{x:{routed:12,active:2,capacity:3}}};
  assert.equal(topologyNodes(b,a)[0].rate,5);
  assert.equal(topologyNodes(b,null)[0].rate,null);
  const liveA={timestamp:10,backends:[{id:"x",total_requests:20}]};
  const liveB={timestamp:12,backends:[{id:"x",total_requests:30,eligible:true,enabled:true}]};
  assert.equal(liveNodes(liveB,liveA)[0].rate,5);
});
test("chart buckets use real successful completions and percentile observations",()=>{
  const run={requests:[{completed:.1,latency_ms:10,status:"ok"},{completed:.2,latency_ms:50,status:"error"},{completed:.3,latency_ms:20,status:"ok"}]};
  const rows=seriesFromRequests(run,.25);
  assert.equal(rows[0].rps,4);assert.equal(rows[0].p99,50);assert.equal(rows[0].error,50);
  assert.equal(rows[1].rps,4);assert.equal(rows[1].p50,20);
});
test("progress uses completed runs rather than invented percentages",()=>{
  assert.equal(runProgress({state:"completed"}),100);
  assert.equal(runProgress({state:"running",spec:{algorithms:["a","b"],repetitions:1},runs:[{}],progress:{completed:50,offered:100}}),75);
});
test("invalid or duplicate backend configuration is rejected",()=>{
  assert.throws(()=>validateDraft({algorithms:["a"],nodes:[{id:"b"},{id:"b"}],faults:[]}));
  assert.throws(()=>validateDraft({algorithms:["a"],nodes:[{id:"b"}],faults:[{backend:"c"}]}));
});
