"""Typed keys for state shared by aiohttp handlers."""

from aiohttp import web

from app.config import AppConfig
from app.control.controller import AdaptiveController
from app.models.backend import BackendRegistry
from app.telemetry.pipeline import TelemetryPipeline

CONFIG_KEY = web.AppKey("config", AppConfig)
REGISTRY_KEY = web.AppKey("registry", BackendRegistry)
CONTROLLER_KEY = web.AppKey("adaptive_controller", AdaptiveController)
TELEMETRY_KEY = web.AppKey("telemetry", TelemetryPipeline)
