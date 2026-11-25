"""Real Chromium browser coverage against a real Pulse and ephemeral HTTP backend."""
from pathlib import Path

import aiohttp
import pytest
from playwright.async_api import async_playwright, expect

from tests.integration.test_proxy import running_stack


@pytest.mark.asyncio
async def test_console_end_to_end():
    reports = Path("reports")
    reports.mkdir(exist_ok=True)
    async with running_stack() as (_pulse, state, url), async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            await page.goto(url + "/pulse/")
            await expect(page.get_by_role("heading", name="Traffic is a signal. Read it.")).to_be_visible()
            await page.screenshot(path=reports / "pulse-landing.png", full_page=True)
            await page.get_by_role("button", name="Connect engine").click()
            await page.get_by_label("Admin token").fill("integration-secret-token")
            await page.get_by_role("button", name="Connect", exact=True).click()
            await expect(page.get_by_text("Engine connected", exact=True)).to_be_visible()
            await page.get_by_role("link", name="Overview", exact=True).click()
            await expect(page.get_by_role("heading", name="Traffic command center")).to_be_visible()
            async with (
                aiohttp.ClientSession() as session,
                session.get(url + "/demo") as response,
            ):
                assert response.status == 200
                await response.read()
            assert state["requests"] == 1
            await expect(page.locator("#live-nodes")).to_contain_text("backend")
            await page.screenshot(path=reports / "pulse-overview.png", full_page=True)
            await page.get_by_role("link", name="Traffic lab").click()
            await page.get_by_label("Requests / second").fill("20")
            await page.get_by_label("Duration (seconds)").fill("2")
            await page.get_by_label("Traffic pattern").select_option("steady")
            await page.get_by_role("button", name="Launch experiment").click()
            await expect(page.locator("#job-status")).to_contain_text("completed", timeout=20000)
            await expect(page.locator("#results table tbody tr")).to_have_count(3, timeout=20000)
            await expect(page.locator("#results canvas")).to_have_count(3, timeout=20000)
            # Identical offered workload for all three algorithms, not fake UI fixtures.
            for row in await page.locator("#results table tbody tr").all():
                await expect(row.locator("td").nth(2)).to_have_text("40")
            await page.locator("#scrub").fill("1")
            await expect(page.locator("#replay-time")).to_have_text("1s")
            await page.screenshot(path=reports / "pulse-arena.png", full_page=True)
            await page.get_by_role("link", name="Traffic replay").click()
            await expect(page.locator("#history-list button").first).to_be_visible()
            await page.get_by_role("button", name="Switch theme").click()
            await expect(page.locator("html")).to_have_attribute("data-theme", "light")
            await page.set_viewport_size({"width": 390, "height": 844})
            await page.screenshot(path=reports / "pulse-mobile.png", full_page=True)
            assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert not errors, errors
        finally:
            await browser.close()
