"""Map UI smoke test: detail panel, facet filter + period chip, trends, reading list, reset.
Serve the site first (python -m http.server -d site 8000), then: python tests/ui_test.py http://localhost:8000
"""
import asyncio
import sys
from playwright.async_api import async_playwright
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1400, "height": 950})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" and "Roboto" not in m.text else None)
        await pg.goto(sys.argv[1].rstrip("/") + "/index.html"); await pg.wait_for_timeout(8000)
        await pg.evaluate("showPaper(5)"); await pg.wait_for_timeout(500)
        await pg.screenshot(path="/tmp/ui_paper.png")
        await pg.click("#paper-agenda")
        await pg.click("#nx-filter-btn"); await pg.wait_for_timeout(300)
        await pg.check('input[data-key="f_age"][value="High School (9-12)"]')
        await pg.check('input[data-key="tier"][value="Causal evidence on learning"]')
        await pg.wait_for_timeout(800)
        print("filter count:", await pg.text_content("#filter-count"))
        print("subtitle:", await pg.evaluate("[...document.querySelectorAll('#title-container *')].find(e=>e.children.length===0&&/papers/.test(e.textContent))?.textContent"))
        await pg.click('#nx-years button[data-year="2026"]'); await pg.wait_for_timeout(800)
        print("subtitle 2026:", await pg.evaluate("[...document.querySelectorAll('#title-container *')].find(e=>e.children.length===0&&/papers/.test(e.textContent))?.textContent"), "selected:", await pg.evaluate("datamap.getSelectedIndices().size"))
        await pg.screenshot(path="/tmp/ui_filter.png")
        await pg.click("#nx-trends-btn"); await pg.wait_for_timeout(500)
        await pg.click(".trend-row"); await pg.wait_for_timeout(1500)
        await pg.screenshot(path="/tmp/ui_trends.png")
        await pg.click("#nx-agenda-btn"); await pg.wait_for_timeout(300)
        print("reading list:", await pg.text_content("#agenda-list"))
        await pg.click("#nx-reset"); await pg.wait_for_timeout(1200)
        print("after reset selected:", await pg.evaluate("datamap.getSelectedIndices().size"), "filter n:", await pg.text_content("#nx-filter-n"))
        print("errors:", errs)
        await b.close()
asyncio.run(main())
