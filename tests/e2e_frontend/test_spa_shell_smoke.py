import pytest


@pytest.mark.playwright
def test_spa_dashboard_loads(page, mediarefinery_server):
    """Visiting the root loads the SPA shell — body renders, no 404."""
    page.goto(mediarefinery_server["base_url"])
    page.wait_for_load_state("networkidle", timeout=10000)
    title = page.title()
    assert "404" not in title, f"Got 404 page, title was: {title!r}"
    body_text = page.locator("body").inner_text()
    assert body_text.strip(), "Page body is empty"


@pytest.mark.playwright
def test_no_console_errors(page, mediarefinery_server):
    """No JS console errors when loading the SPA shell."""
    errors = []
    page.on("console", lambda msg: errors.append(msg) if msg.type == "error" else None)
    page.goto(mediarefinery_server["base_url"])
    page.wait_for_load_state("networkidle", timeout=10000)
    assert not errors, f"Console errors during page load: {[e.text for e in errors]}"


@pytest.mark.playwright
def test_screenshot_capture(page, mediarefinery_server):
    """Capture a screenshot of the SPA shell for visual review on CI."""
    page.goto(mediarefinery_server["base_url"])
    page.wait_for_load_state("networkidle", timeout=10000)
    page.screenshot(path="spa-shell-smoke.png", full_page=True)
