"""Actual Chromium UI against a real proxy and ephemeral backends."""
from pathlib import Path

import pytest
from playwright.async_api import async_playwright, expect

from tests.integration.test_proxy import running_stack


@pytest.mark.asyncio
async def test_console_end_to_end():
    reports = Path("reports")
    reports.mkdir(exist_ok=True)
    async with running_stack() as (pulse, state, url), async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            await page.goto(url + "/pulse/")
            await expect(page.get_by_role("heading", name="Load balancer", exact=True)).to_be_visible()
            await page.get_by_role("button", name="Connect", exact=True).click()
            await page.get_by_label("Admin token").fill("integration-secret-token")
            await page.get_by_role("dialog").get_by_role("button", name="Connect", exact=True).click()
            await expect(page.locator("#connection")).to_have_text("Connected")
            await page.get_by_label("Routing algorithm").select_option("least_connections")
            await expect(page.locator("#notice")).to_contain_text("Least Connections")
            assert pulse.config.load_balancing.strategy == "least_connections"
            await page.get_by_label("Requests", exact=True).fill("20")
            await page.get_by_label("Concurrency", exact=True).fill("4")
            await page.get_by_role("button", name="Generate traffic").click()
            await expect(page.locator("#traffic-status")).to_contain_text("20 successful")
            await expect(page.locator("#total")).to_have_text("20")
            assert state["requests"] == 20
            await expect(page.locator("#backends")).to_contain_text("backend")
            await page.screenshot(path=reports / "pulse-dashboard.png", full_page=True)
            await page.get_by_role("button", name="Battle Arena").click()
            await page.get_by_label("Mode", exact=True).select_option("simulation")
            await page.get_by_label("Requests / second").fill("20")
            await page.get_by_label("Duration (seconds)").fill("2")
            await page.get_by_role("button", name="Run comparison").click()
            await expect(page.locator("#job-status")).to_contain_text("completed", timeout=20000)
            await expect(page.locator("#results tr")).to_have_count(4)
            for row in await page.locator("#results tr").all():
                await expect(row.locator("td").nth(1)).to_have_text("40")
            await page.screenshot(path=reports / "pulse-arena.png", full_page=True)
            await page.get_by_role("button", name="Dashboard").click()
            await page.set_viewport_size({"width": 390, "height": 844})
            await page.screenshot(path=reports / "pulse-mobile.png", full_page=True)
            assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert not errors, errors
        finally:
            await browser.close()
