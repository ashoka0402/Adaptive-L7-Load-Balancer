"""Browser regression coverage for the active FastAPI dashboard layout."""
import json
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

HTML = Path(__file__).resolve().parents[1] / "app/fastapi_lb/dashboard.html"


@pytest.mark.parametrize("width,height", [(1440, 768), (1280, 720), (1024, 600), (390, 844)])
def test_dashboard_scroll_and_graphs(width, height):
    snapshot = {
        "running": True, "arenaRunning": False, "arena": [
            {"strategy": "round_robin", "rps": 125, "p95": 28, "successPct": 100}
        ], "demo": True, "strategy": "round_robin", "workers": 24, "elapsed": 15,
        "totals": {"total": 1200, "ok": 1200, "failed": 0}, "successPct": 100,
        "rps": 125, "p50": 12, "p95": 28, "p99": 40,
        "series": {"rps": [50, 100, 125], "p95": [10, 22, 28], "errors": [0, 2, 0]},
        "backends": [
            {"id": x, "label": "Backend " + x, "dead": False, "alive": True,
             "breaker": "closed", "active": 2, "latency": 12, "served": 100, "share": 17}
            for x in "ABCDEF"
        ], "events": [{"msg": "Backend online", "kind": "good", "ts": 0}],
    }
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": height})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.add_init_script(
            "window.EventSource = class { constructor() {setTimeout(()=>"
            "this.onmessage?.({data: JSON.stringify(" + json.dumps(snapshot)
            + ")}),0)} };"
        )
        page.route("http://pulse.test/", lambda route: route.fulfill(
            status=200, content_type="text/html", body=HTML.read_text(encoding="utf-8")
        ))
        page.goto("http://pulse.test/")
        page.wait_for_selector("#ports .port:nth-child(6)")
        page.wait_for_function("document.querySelector('#scopeValue').textContent === '125 req/s'")
        assert page.evaluate("document.documentElement.scrollHeight > innerHeight")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        for name in ("graph", "scope", "latencyScope", "errorScope", "arenaResult", "feed"):
            item = page.locator("#" + name)
            item.scroll_into_view_if_needed()
            assert item.is_visible()
            box = item.bounding_box()
            assert box and box["width"] > 100 and box["height"] > 20
            assert box["x"] >= -1 and box["x"] + box["width"] <= width + 1
            assert box["y"] >= -1 and box["y"] + box["height"] <= height + 1
        page.locator("#theme").click()
        assert page.locator("body").evaluate("el => el.classList.contains('light')")
        assert not errors
        browser.close()
