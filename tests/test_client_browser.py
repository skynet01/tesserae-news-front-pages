"""client.js in a live Chromium, through the host's own /_test/render page:
a page's PDF is drawn with the vendored PDF.js onto a canvas at the cell's
pixel width (times the device pixel ratio), and the JPEG stands in when the
PDF is broken or takes too long.

The PDF is one this file writes (vector shapes, no fonts), so the tests need
no network. Set ``NEWS_FRONT_PAGES_LIVE_PDF=1`` to also draw today's New York
Times PDF, downloaded from Freedom Forum when the test starts."""

from __future__ import annotations

import io
import os
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from app.main import create_app
from flask import Flask
from PIL import Image

REPO_ROOT = Path(os.environ.get("TESSERAE_TEST_ROOT", Path(__file__).resolve().parents[3]))
PLUGIN = "news_front_pages"


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            return Path(
                os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE", p.chromium.executable_path)
            ).exists()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _chromium_available(), reason="Playwright Chromium not installed"
)


def vector_pdf(width_pt: int = 612, height_pt: int = 1188) -> bytes:
    """A one-page PDF drawn with vector shapes only: white, a black band
    across the top tenth, and a hairline grid below it."""
    band = height_pt // 10
    ops = [f"0 g 0 {height_pt - band} {width_pt} {band} re f", "0 G 1 w"]
    for x in range(36, width_pt, 36):
        ops.append(f"{x} 36 m {x} {height_pt - band - 36} l S")
    content = "\n".join(ops).encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width_pt} {height_pt}] "
        "/Contents 4 0 R /Resources << >> >>".encode("ascii"),
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content),
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n%s\nendobj\n" % (number, body))
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for offset in offsets:
        out.write(b"%010d 00000 n \n" % offset)
    out.write(
        b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    )
    return out.getvalue()


def _jpeg(width: int = 700, height: int = 1359) -> bytes:
    img = Image.new("L", (width, height), 255)
    img.paste(0, (0, 0, width, height // 10))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return buf.getvalue()


@pytest.fixture
def live(tmp_path: Path) -> Iterator[tuple[str, Flask]]:
    from werkzeug.serving import make_server

    app = create_app(
        testing=False,
        data_root=tmp_path / "data",
        plugins_dir=Path(os.environ.get("TESSERAE_TEST_PLUGINS", REPO_ROOT / "plugins")),
    )
    app.config["TESTING"] = True
    store = app.config["SETTINGS_STORE"]
    store.patch_section("app", {"onboarded": True})
    store.patch_section("auth", {"disabled": True})
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", app
    finally:
        server.shutdown()
        thread.join(timeout=10)


def _no_network(req: Any, timeout: float | None = None) -> Any:
    url = req if isinstance(req, str) else req.full_url
    raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b""))  # type: ignore[arg-type]


def _edition(app: Flask, pdf: bytes | None, jpeg: bytes | None = None) -> Path:
    """Today's NY_NYT edition saved (the JPEG, and ``pdf`` beside it), and
    the paper list cut to NY_NYT, so a render downloads nothing."""
    plugin = app.config["PLUGIN_REGISTRY"].get(PLUGIN)
    module = plugin.server_module
    data_dir = Path(plugin.data_dir)
    module.save_config(
        data_dir,
        {
            "papers": [{"enabled": True, "code": "NY_NYT", "css": ""}],
            "small_screens": "top",
        },
    )
    page = Path(module.edition_path(data_dir / "pages", "NY_NYT", module._edition_today()))
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_bytes(jpeg or _jpeg())
    if pdf is not None:
        module.pdf_path(page).write_bytes(pdf)
    return page


# What the render left in the widget's shadow root.
_INSPECT = """() => {
  const cell = document.querySelector('.cell[data-plugin="news_front_pages"]');
  const host = cell.querySelector('.cell-content') || cell;
  const root = host.shadowRoot;
  const canvas = root.querySelector('canvas.page');
  const img = root.querySelector('img.page');
  const out = {cellW: host.getBoundingClientRect().width, cellH: host.getBoundingClientRect().height};
  if (img) out.img = {src: img.getAttribute('src'), w: img.naturalWidth, complete: img.complete};
  if (canvas) {
    const ctx = canvas.getContext('2d');
    const luma = (x, y) => {
      const d = ctx.getImageData(Math.floor(x), Math.floor(y), 1, 1).data;
      return Math.round((d[0] + d[1] + d[2]) / 3);
    };
    let dark = 0;
    const row = ctx.getImageData(0, Math.floor(canvas.height / 2), canvas.width, 1).data;
    for (let i = 0; i < row.length; i += 4) if (row[i] < 128) dark += 1;
    out.canvas = {
      w: canvas.width, h: canvas.height,
      cssW: canvas.getBoundingClientRect().width,
      cssH: canvas.getBoundingClientRect().height,
      top: canvas.getBoundingClientRect().top-host.getBoundingClientRect().top,
      fit: root.querySelector('.w').className,
      visibility: getComputedStyle(canvas).visibility,
      ms: Number(canvas.dataset.drawnMs),
      band: luma(canvas.width / 2, 4),
      paper: luma(canvas.width / 2 + 8, canvas.height * 0.97),
      darkInRow: dark,
      bottom: luma(canvas.width / 2, canvas.height - 1),
    };
  }
  return out;
}"""


def _render(
    base: str,
    *,
    scale: float = 1,
    query: str = "size=lg",
    vw: int = 1200,
    vh: int = 800,
    logs: list[str] | None = None,
    screenshot: Path | None = None,
) -> dict[str, Any]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
        )
        page = browser.new_page(viewport={"width": vw, "height": vh}, device_scale_factor=scale)
        if logs is not None:
            page.on("console", lambda m: logs.append(f"{m.type}: {m.text}"))
        page.goto(f"{base}/_test/render?plugin={PLUGIN}&{query}", wait_until="load")
        page.wait_for_function("window.__tesseraeComposed === true", timeout=30_000)
        result: dict[str, Any] = page.evaluate(_INSPECT)
        if "img" in result and not result["img"]["complete"]:
            page.wait_for_function(
                """() => {
                  const c = document.querySelector('.cell[data-plugin="news_front_pages"]');
                  const h = c.querySelector('.cell-content') || c;
                  const i = h.shadowRoot.querySelector('img.page');
                  return i && i.complete;
                }""",
                timeout=10_000,
            )
            result = page.evaluate(_INSPECT)
        if screenshot is not None:
            page.screenshot(path=str(screenshot))
        browser.close()
    return result


def test_the_pdf_is_drawn_at_the_cells_width(live: tuple[str, Flask]) -> None:
    base, app = live
    _edition(app, vector_pdf())
    logs: list[str] = []
    with patch("urllib.request.urlopen", _no_network):
        got = _render(base, logs=logs)
    canvas = got["canvas"]
    assert "img" not in got  # the JPEG never came into it
    assert canvas["w"] == round(got["cellW"]) == 1200  # one canvas pixel per screen pixel
    assert canvas["h"] == round(1200 * 1188 / 612)
    assert canvas["cssW"] == 1200
    assert canvas["visibility"] == "visible"
    assert canvas["band"] < 40 and canvas["paper"] > 215  # drawn, not blank
    assert canvas["darkInRow"] >= 10  # the hairlines
    assert canvas["ms"] > 0
    assert not [line for line in logs if line.startswith("error")]


def test_the_canvas_follows_the_device_pixel_ratio(live: tuple[str, Flask]) -> None:
    base, app = live
    _edition(app, vector_pdf())
    with patch("urllib.request.urlopen", _no_network):
        got = _render(base, scale=2)
    assert got["canvas"]["cssW"] == 1200
    assert got["canvas"]["w"] == 2400


@pytest.mark.parametrize("source", ["jpeg", "pdf", "broken-pdf"])
@pytest.mark.parametrize("fit", ["top", "whole"])
def test_nyt_ad_spread_shows_only_the_front_page(
    live: tuple[str, Flask], source: str, fit: str
) -> None:
    base, app = live
    jpeg = (Path(__file__).parent / "fixtures/nyt-20261120-ad-spread.jpg").read_bytes()
    pdf = None
    if source == "pdf":
        out = io.BytesIO()
        with Image.open(io.BytesIO(jpeg)) as img:
            img.save(out, "PDF", resolution=72)
        pdf = out.getvalue()
    elif source == "broken-pdf":
        pdf = b"%PDF-1.7 broken %%EOF"
    _edition(app, pdf, jpeg)
    # Cropping must also work when every appearance control is off.
    plugin = app.config["PLUGIN_REGISTRY"].get(PLUGIN)
    module = plugin.server_module
    config = module.load_config(Path(plugin.data_dir))
    config.update(text_blackness="off", text_sharpness="off", clean_white="off", small_screens=fit)
    module.save_config(Path(plugin.data_dir), config)
    with patch("urllib.request.urlopen", _no_network):
        got = _render(base)
    canvas = got["canvas"]
    assert abs(canvas["h"] / canvas["w"] - 566 / 350) < 0.02
    assert canvas["bottom"] > 230  # the cut lands in paper white, not the ad
    if source != "pdf":
        assert canvas["w"] == 350
    elif fit == "top":
        assert canvas["w"] == 1200
    if fit == "whole":
        assert canvas["cssW"] < got["cellW"] / 2


def test_nyt_crop_preserves_normal_pages_and_follows_the_gutter(
    live: tuple[str, Flask],
) -> None:
    import base64

    from playwright.sync_api import sync_playwright

    base, _ = live
    fixtures = Path(__file__).parent / "fixtures"
    urls = [
        "data:image/jpeg;base64," + base64.b64encode((fixtures / name).read_bytes()).decode()
        for name in ("nyt-20261119-normal.jpg", "nyt-20261120-ad-spread.jpg")
    ]
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
        )
        page = browser.new_page()
        page.goto(base)
        result = page.evaluate(
            """async (urls) => {
              const m = await import('/plugins/news_front_pages/client.js');
              const results = [];
              for (const [index, url] of urls.entries()) {
                const img = new Image(); img.src = url; await img.decode();
                for (const extra of (index ? [-50, 0, 80] : [0])) {
                  for (const code of ['NY_NYT', 'WSJ']) {
                    const c = document.createElement('canvas');
                    c.width = img.width + extra; c.height = img.height;
                    const ctx = c.getContext('2d');
                    ctx.drawImage(img, extra, 0);
                    const before = ctx.getImageData(0, 0, c.width, c.height);
                    const crop = index && code === 'NY_NYT';
                    const left = crop ? c.width - 350 : 0;
                    m.cropNytPage(c, code);
                    const after = ctx.getImageData(0, 0, c.width, c.height);
                    let equal = true;
                    for (let y = 0; y < c.height; y++) {
                      for (let x = 0; x < c.width * 4; x++) {
                        if (after.data[y*c.width*4+x] !==
                            before.data[y*before.width*4+left*4+x]) equal = false;
                      }
                    }
                    results.push({equal, w:c.width, expected:crop ? 350 : before.width});
                  }
                }
              }
              // A wide image without a white gutter is left intact.
              const c = document.createElement('canvas'); c.width=700; c.height=671;
              c.getContext('2d').fillRect(0, 0, 700, 671);
              m.cropNytPage(c, 'NY_NYT');
              results.push({equal:true, w:c.width, expected:700});
              return results;
            }""",
            urls,
        )
        browser.close()
    assert all(r["equal"] and r["w"] == r["expected"] for r in result), result


def test_nyt_bottom_ads_preserve_articles_and_other_papers(live: tuple[str, Flask]) -> None:
    import base64

    from playwright.sync_api import sync_playwright

    base, _ = live
    fixtures = Path(__file__).parent / "fixtures"
    names = [f"nyt-202611{day}.jpg" for day in (15, 16, 17, 18)] + [
        "nyt-20261119-normal.jpg",
        "nyt-20261120-ad-spread.jpg",
    ]
    urls = [
        "data:image/jpeg;base64," + base64.b64encode((fixtures / n).read_bytes()).decode()
        for n in names
    ]
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
        )
        page = browser.new_page()
        page.goto(base)
        results = page.evaluate(
            """async (urls) => {
              const m = await import('/plugins/news_front_pages/client.js');
              const results=[];
              for (const [index,url] of urls.entries()) {
                const img=new Image();img.src=url;await img.decode();
                for (const code of ['NY_NYT','WSJ']) {
                  const c=document.createElement('canvas');c.width=img.width;c.height=img.height;
                  const ctx=c.getContext('2d');ctx.drawImage(img,0,0);
                  m.cropNytPage(c,code);
                  const before=ctx.getImageData(0,0,c.width,c.height);
                  m.cropNytBottomAd(c,code);
                  const after=ctx.getImageData(0,0,c.width,c.height);
                  results.push({index,code,w:c.width,h:c.height,oldH:before.height,
                    equal:after.data.every((value,i)=>value===before.data[i])});
                }
              }
              // Vary only the real display ad's height. Article pixels and
              // their footer remain exactly where the original put them.
              const img=new Image();img.src=urls[1];await img.decode();
              for (const bandHeight of [35,115,150]) {
                const c=document.createElement('canvas');c.width=350;c.height=570+bandHeight+29;
                const ctx=c.getContext('2d');ctx.fillStyle='white';ctx.fillRect(0,0,c.width,c.height);
                ctx.drawImage(img,0,0,350,570,0,0,350,570);
                ctx.drawImage(img,0,570,350,72,0,570,350,bandHeight);
                const before=ctx.getImageData(0,0,c.width,c.height);
                m.cropNytBottomAd(c,'NY_NYT');
                const after=ctx.getImageData(0,0,c.width,c.height);
                results.push({index:1,code:'NY_NYT',w:c.width,h:c.height,oldH:before.height,
                    equal:after.data.every((value,i)=>value===before.data[i])});
              }
              // A band without a recognizable footer could be a news photo.
              const c=document.createElement('canvas');c.width=img.width;c.height=img.height;
              const ctx=c.getContext('2d');ctx.drawImage(img,0,0);
              ctx.fillStyle='white';ctx.fillRect(350*.74,530,350*.24,35);
              m.cropNytBottomAd(c,'NY_NYT');
              results.push({index:0,code:'NY_NYT',w:c.width,h:c.height,oldH:img.height,equal:true});
              // Text below the image is a caption, so keep the entire page.
              ctx.drawImage(img,0,0);ctx.fillStyle='black';ctx.font='10px serif';
              ctx.fillText('Photo caption: article text must stay visible.',10,660);
              m.cropNytBottomAd(c,'NY_NYT');
              results.push({index:0,code:'NY_NYT',w:c.width,h:c.height,oldH:img.height,equal:true});
              const small=document.createElement('canvas');small.width=175;small.height=336;
              small.getContext('2d').drawImage(img,0,0,175,336);
              m.cropNytBottomAd(small,'NY_NYT');
              results.push({index:0,code:'NY_NYT',w:175,h:small.height,oldH:336,equal:true});
              // Other papers do not even read the canvas or its dimensions.
              m.cropNytBottomAd({get width(){throw Error('read other paper')}},'WSJ');
              return results;
            }""",
            urls,
        )
        browser.close()
    for result in results:
        assert result["equal"], result  # every retained article pixel is identical
        if result["code"] == "NY_NYT" and result["index"] in (1, 5):
            assert 1.60 < result["h"] / result["w"] < 1.64, result
        else:
            assert result["h"] == result["oldH"], result


@pytest.mark.parametrize("size", [(1440, 2560), (1200, 1600)])
@pytest.mark.parametrize("fit", ["top", "whole"])
@pytest.mark.parametrize("source", ["jpeg", "pdf"])
def test_cropped_nyt_keeps_its_aspect_and_masthead_on_portrait_panels(
    live: tuple[str, Flask], size: tuple[int, int], fit: str, source: str
) -> None:
    base, app = live
    jpeg = (Path(__file__).parent / "fixtures/nyt-20261120-ad-spread.jpg").read_bytes()
    pdf = None
    if source == "pdf":
        out = io.BytesIO()
        with Image.open(io.BytesIO(jpeg)) as image:
            image.save(out, "PDF", resolution=72)
        pdf = out.getvalue()
    _edition(app, pdf, jpeg)
    plugin = app.config["PLUGIN_REGISTRY"].get(PLUGIN)
    config = plugin.server_module.load_config(Path(plugin.data_dir))
    config["small_screens"] = fit
    plugin.server_module.save_config(Path(plugin.data_dir), config)
    w, h = size
    with patch("urllib.request.urlopen", _no_network):
        got = _render(base, query=f"size=lg&w={w}&h={h}", vw=w, vh=h)
    canvas = got["canvas"]
    assert abs(canvas["h"] / canvas["w"] - 566 / 350) < 0.02
    assert abs(canvas["cssH"] / canvas["cssW"] - canvas["h"] / canvas["w"]) < 0.005
    assert abs(canvas["top"]) <= 1  # the masthead stays at the top
    assert canvas["visibility"] == "visible"
    if fit == "top" or size == (1440, 2560):
        assert abs(canvas["cssW"] - w) <= 1
    else:
        assert canvas["cssW"] < w
        assert abs(canvas["cssH"] - h) <= 1


def test_a_broken_pdf_falls_back_to_the_jpeg(live: tuple[str, Flask]) -> None:
    base, app = live
    _edition(app, b"%PDF-1.7\nthis is not a PDF at all\n%%EOF\n")
    logs: list[str] = []
    with patch("urllib.request.urlopen", _no_network):
        got = _render(base, logs=logs)
    assert "img" not in got
    assert got["canvas"]["w"] == 700  # the fallback JPEG, processed at its own size
    assert got["canvas"]["band"] < 40 and got["canvas"]["paper"] > 215
    assert any("drew the JPEG, the PDF failed" in line for line in logs)


def test_a_missing_pdf_falls_back_to_the_jpeg(live: tuple[str, Flask]) -> None:
    """The PDF went between the render's data and the browser asking for it
    (pruned, say): a 404, so the JPEG."""
    base, app = live
    _edition(app, vector_pdf())
    module = app.config["PLUGIN_REGISTRY"].get(PLUGIN).server_module
    real = module._pdf_beside

    def gone_after_the_look(path: Path) -> Path | None:
        found: Path | None = real(path)
        if found is not None:
            found.unlink()
        return found

    with (
        patch("urllib.request.urlopen", _no_network),
        patch.object(module, "_pdf_beside", gone_after_the_look),
    ):
        got = _render(base)
    assert "img" not in got
    assert got["canvas"]["w"] == 700
    assert got["canvas"]["band"] < 40 and got["canvas"]["paper"] > 215


def test_a_pdf_over_its_budget_gives_up(live: tuple[str, Flask]) -> None:
    """drawPdf stops at its budget and says so; render() then shows the JPEG."""
    from playwright.sync_api import sync_playwright

    base, app = live
    page_file = _edition(app, vector_pdf())
    pdf_url = f"/plugins/{PLUGIN}/page/{page_file.with_suffix('.pdf').name}"
    with patch("urllib.request.urlopen", _no_network), sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
        )
        page = browser.new_page()
        page.goto(f"{base}/_test/render?plugin={PLUGIN}&size=lg", wait_until="load")
        page.wait_for_function("window.__tesseraeComposed === true", timeout=30_000)
        outcome = page.evaluate(
            """async (url) => {
              const m = await import('/plugins/news_front_pages/client.js');
              const c = document.createElement('canvas');
              document.body.append(c);
              try { await m.drawPdf(c, url, 1); return 'drawn'; }
              catch (err) { return String(err.message); }
            }""",
            pdf_url,
        )
        browser.close()
    assert outcome == "took longer than 1 ms"


def _todays_nyt_pdf() -> bytes:
    """Today's (or the latest of the last three days') New York Times PDF."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    today = datetime.now(ZoneInfo("America/New_York")).date()
    for back in range(3):
        day = today - timedelta(days=back)
        url = f"https://cdn.freedomforum.org/dfp/pdf{day.day}/NY_NYT.pdf"
        request = urllib.request.Request(
            url, headers={"User-Agent": "tesserae/0.1 (+news_front_pages)"}
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as resp:
                return bytes(resp.read())
        except urllib.error.HTTPError:
            continue
    pytest.skip("no New York Times PDF in the last three days")


@pytest.mark.skipif(
    os.environ.get("NEWS_FRONT_PAGES_LIVE_PDF") != "1",
    reason="set NEWS_FRONT_PAGES_LIVE_PDF=1",
)
def test_a_real_front_page_is_drawn_on_the_big_portrait_panel(
    live: tuple[str, Flask],
) -> None:
    body = _todays_nyt_pdf()
    base, app = live
    _edition(app, body)
    with patch("urllib.request.urlopen", _no_network):
        got = _render(base, query="size=lg&w=1440&h=2560", vw=1440, vh=2560)
    canvas = got["canvas"]
    assert canvas["w"] == 1440 and canvas["visibility"] == "visible"
    assert canvas["darkInRow"] > 50  # text and pictures across the middle of the page
    print(f"NY_NYT PDF ({len(body) // 1024} KB) drawn 1440 px wide in {canvas['ms']} ms")
