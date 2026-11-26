"""Capacity experiment controller; never feeds configured limits to the router."""
from __future__ import annotations

import asyncio
import time
from collections import deque
from contextlib import suppress

import httpx
from fastapi import HTTPException

IDS = tuple(f"backend-{i}" for i in range(1,7))
FIELDS = ("enabled","rate","concurrency","queue","crash","cooldown")


class CapacityController:
    def __init__(self, generator, target, token, origin_url):
        self.generator = generator
        self.target = target
        self.token = token
        self.origin_url = origin_url
        self.states = {}
        self.history = {b:deque(maxlen=120) for b in IDS}
        self.members = list(IDS[:3])
        self.standby = []
        self.strategy = ""
        self.available = False
        self.auto = False
        self.last_scale = 0.0
        self.high = self.low = 0
        self.events = deque(maxlen=50)
        self.comparison = []
        self.comparison_state = "idle"
        self.job = None

    @property
    def busy(self):
        return self.job is not None and not self.job.done()

    def event(self, message):
        self.events.append({"ts":time.time(),"message":message})

    async def call(self, url, data=None):
        if not self.token:
            raise HTTPException(409,"Capacity controls require the Compose sample cluster")
        try:
            async with httpx.AsyncClient(timeout=10,trust_env=False) as client:
                headers={"X-Benchmark-Token":self.token}
                r=await (client.get(url,headers=headers) if data is None else
                         client.post(url,json=data,headers=headers))
                r.raise_for_status()
                return r.json()
        except (httpx.HTTPError,ValueError) as exc:
            raise HTTPException(502,"Capacity or cluster control unavailable") from exc

    async def origin(self, backend, data=None):
        try:
            s=await self.call(self.origin_url(backend)+"/__benchmark/capacity",data)
        except HTTPException:
            self.states[backend]={"state":"unknown"}
            raise
        self.states[backend]=s
        return s

    async def cluster(self, data=None):
        result=await self.call(self.target+"/pulse/internal/cluster",data)
        self.members=result["members"]
        self.strategy=result["strategy"]
        if "standby" in result:
            self.standby=result["standby"]
        self.available=True
        return result

    async def configure(self, backend, data):
        result=await self.origin(backend,data)
        self.event(f"{backend}: capacity {result.get('rate')} req/s" if result.get("enabled")
                   else f"{backend}: capacity limiter disabled")
        return result

    async def preset(self, crash=False):
        errors=[]
        for backend,rate in zip(IDS,(25,50,80,80,80,80)):
            try:
                await self.configure(backend,{"enabled":True,"rate":rate,"concurrency":8,
                                             "queue":12,"crash":crash,"cooldown":10})
            except HTTPException:
                errors.append(backend)
        if errors:
            raise HTTPException(502,"Preset partially applied; unavailable: "+", ".join(errors))

    async def recover(self):
        results=await asyncio.gather(*(self.origin(b,{"recover":True}) for b in IDS),
                                     return_exceptions=True)
        failed=[b for b,r in zip(IDS,results) if isinstance(r,BaseException)]
        self.event("Capacity recovery requested")
        if failed:
            raise HTTPException(502,"Could not recover: "+", ".join(failed))

    async def tick(self):
        if not self.token:
            return
        results=await asyncio.gather(*(self.origin(b) for b in IDS),self.cluster(),
                                     return_exceptions=True)
        if isinstance(results[-1],BaseException):
            self.available=False
        for b,result in zip(IDS,results[:6]):
            if not isinstance(result,BaseException):
                self.history[b].append({"ts":time.time(),"incoming":result["incoming_rps"],
                                        "started":result["started_rps"],"rate":result["rate"],
                                        "enabled":result["enabled"]})
        if not self.auto or not self.available or self.busy:
            return
        active=[self.states.get(b,{}) for b in self.members]
        pressure=any(s.get("state") in ("failed","overloaded") or s.get("queued",0)>=3
                     for s in active)
        quiet=all(s.get("state") in ("healthy","unlimited","recovering")
                  and s.get("queued",0)==0 and s.get("incoming_rps",0)<s.get("rate",40)*.4
                  for s in active)
        self.high=self.high+1 if pressure and self.generator.running else 0
        self.low=self.low+1 if quiet else 0
        if time.monotonic()-self.last_scale<5:
            return
        try:
            if self.high>=2:
                candidates=[b for b in self.standby if b not in self.members
                            and self.states.get(b,{}).get("state") not in ("failed","unknown")]
                if candidates:
                    b=candidates[0]
                    await self.cluster({"action":"add","backend":b})
                    self.event(f"Scale out: {b} added to the live routing pool")
                    self.high=self.low=0
                    self.last_scale=time.monotonic()
            elif self.low>=8:
                extra=[b for b in self.members if b in self.standby]
                if extra:
                    b=extra[-1]
                    await self.cluster({"action":"remove","backend":b})
                    self.event(f"Scale in: {b} drained")
                    self.high=self.low=0
                    self.last_scale=time.monotonic()
        except HTTPException:
            self.last_scale=time.monotonic()
            self.event("Autoscaling action rejected; will retry")

    def snapshot(self):
        return {"states":self.states,"history":{b:list(v) for b,v in self.history.items()},
                "members":self.members,"standby":self.standby,"strategy":self.strategy,
                "available":self.available,"auto":self.auto,"events":list(self.events),
                "comparison":self.comparison,"comparisonState":self.comparison_state,
                "comparing":self.busy}

    async def compare(self, options, reset_faults):
        self.comparison=[]
        self.comparison_state="running"
        original=""
        original_auto=self.auto
        self.auto=False
        try:
            await self.cluster()
            original=self.strategy
            fixed=list(self.members)
            settings={}
            for b in fixed:
                s=await self.origin(b)
                if not s.get("enabled"):
                    raise HTTPException(409,"Enable capacity limits on all active backends first")
                settings[b]={k:s[k] for k in FIELDS}
            # Fixed membership, identical limits and restored fault/CPU state.
            for strategy in ("round_robin","latency_aware"):
                await reset_faults(fixed)
                for b,data in settings.items():
                    await self.origin(b,data)
                await asyncio.sleep(2)
                await self.cluster({"action":"strategy","strategy":strategy,"reset":True})
                self.event(f"Comparison: {strategy}")
                self.generator.task=asyncio.create_task(self.generator.run(options))
                await self.generator.task
                snapshot=self.generator.snapshot()
                if snapshot["state"] != "completed":
                    raise RuntimeError("Generator did not complete")
                origins={b:await self.origin(b) for b in fixed}
                self.comparison.append({"strategy":strategy,"result":snapshot,
                                        "origins":origins,"members":fixed,"capacity":settings})
            self.comparison_state="completed"
        except asyncio.CancelledError:
            self.comparison_state="cancelled"
            raise
        except Exception as exc:
            self.comparison_state="failed"
            self.event("Comparison failed: "+str(getattr(exc,"detail",type(exc).__name__)))
        finally:
            await self.generator.stop()
            if original:
                with suppress(HTTPException):
                    await self.cluster({"action":"strategy","strategy":original})
            self.auto=original_auto

    async def stop(self):
        if self.busy:
            self.job.cancel()
            with suppress(asyncio.CancelledError):
                await self.job
