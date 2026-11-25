"""Authenticated Lab/Arena/Replay API, using bounded local experiments only."""
from __future__ import annotations

import asyncio
import csv
import hmac
import io
import json

from aiohttp import web
from pydantic import ValidationError

from app.app_keys import CONFIG_KEY
from app.experiments.manager import ExperimentManager, TERMINAL
from app.experiments.spec import ExperimentSpec

EXPERIMENTS_KEY = web.AppKey("experiments",ExperimentManager)


def authorize(request: web.Request) -> None:
    token = request.app[CONFIG_KEY].server.admin_token
    if not hmac.compare_digest(request.headers.get("Authorization",""),f"Bearer {token}"):
        raise web.HTTPUnauthorized()


def get_job(request: web.Request):
    job = request.app[EXPERIMENTS_KEY].jobs.get(request.match_info["id"])
    if job is None:
        raise web.HTTPNotFound()
    return job


async def create(request: web.Request) -> web.Response:
    authorize(request)
    try:
        spec=ExperimentSpec.model_validate(await request.json())
        job=request.app[EXPERIMENTS_KEY].start(spec)
    except (ValueError,ValidationError) as exc:
        return web.json_response({"error":str(exc)},status=400)
    except RuntimeError as exc:
        return web.json_response({"error":str(exc)},status=409)
    return web.json_response(job.info(),status=202)


async def history(request: web.Request) -> web.Response:
    authorize(request)
    manager=request.app[EXPERIMENTS_KEY]
    saved=await asyncio.to_thread(manager.store.recent_experiments) if manager.store else []
    return web.json_response({"active":[j.info() for j in manager.jobs.values()],"saved":saved})


async def result(request: web.Request) -> web.Response:
    authorize(request)
    manager=request.app[EXPERIMENTS_KEY]
    job=manager.jobs.get(request.match_info["id"])
    if job:
        return web.json_response(job.info(full=request.query.get("full")=="true"))
    if manager.store:
        saved=await asyncio.to_thread(manager.store.get_experiment,request.match_info["id"])
        if saved:
            return web.json_response(saved)
    raise web.HTTPNotFound()


async def control(request: web.Request) -> web.Response:
    authorize(request)
    manager=request.app[EXPERIMENTS_KEY]
    job=get_job(request)
    try:
        action=request.match_info["action"]
        if action=="pause":
            manager.pause(job)
        elif action=="resume":
            manager.resume(job)
        elif action=="cancel":
            await manager.cancel(job)
        elif action in {"replay","reset"}:
            if job.state not in TERMINAL:
                raise RuntimeError("cancel or finish experiment before replay")
            job=manager.start(job.spec)
        else:
            raise web.HTTPNotFound()
    except RuntimeError as exc:
        return web.json_response({"error":str(exc)},status=409)
    return web.json_response(job.info())


async def export(request: web.Request) -> web.Response:
    authorize(request)
    job=get_job(request)
    if request.query.get("format")!="csv":
        return web.json_response(job.info(full=True))
    buffer=io.StringIO()
    writer=csv.DictWriter(buffer,fieldnames=["algorithm","seed","id","arrival","completed","class","backend","status","latency_ms"])
    writer.writeheader()
    for run in job.runs:
        for row in run["requests"]:
            writer.writerow({"algorithm":run["algorithm"],"seed":run["seed"],**row})
    return web.Response(text=buffer.getvalue(),content_type="text/csv")


async def stream(request: web.Request) -> web.StreamResponse:
    authorize(request)
    job=get_job(request)
    response=web.StreamResponse(headers={"Content-Type":"text/event-stream","Cache-Control":"no-cache"})
    await response.prepare(request)
    try:
        for _ in range(600):
            await response.write(("data: "+json.dumps(job.info())+"\n\n").encode())
            if job.state in TERMINAL:
                break
            await asyncio.sleep(.25)
    except (ConnectionResetError, OSError):
        pass
    return response


def setup_experiment_routes(app: web.Application, manager: ExperimentManager) -> None:
    app[EXPERIMENTS_KEY]=manager
    app.router.add_post("/admin/experiments",create)
    app.router.add_get("/admin/experiments",history)
    app.router.add_get("/admin/experiments/{id}",result)
    app.router.add_get("/admin/experiments/{id}/export",export)
    app.router.add_get("/admin/experiments/{id}/stream",stream)
    app.router.add_post("/admin/experiments/{id}/{action}",control)
