"""Typed state keys for aiohttp handlers."""
from aiohttp import web

from app.config import AppConfig
from app.models.backend import BackendRegistry

CONFIG_KEY = web.AppKey("config", AppConfig)
REGISTRY_KEY = web.AppKey("registry", BackendRegistry)
