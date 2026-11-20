#!/usr/bin/env python3
"""
Eager Production site test suite — drag/scroll/resize stress + smoke test.

Setup (once):
    python3 -m venv .venv-tests
    .venv-tests/bin/pip install playwright
    # uses the system Chromium; override with CHROME=/path/to/browser

Run:
    .venv-tests/bin/python tests/site_test.py drag    # window-dragging stress suite
    .venv-tests/bin/python tests/site_test.py smoke   # boot, fonts, language switch, player

The script serves the repo root on a random local port and tears it down after.
Exit code 0 = all checks clean.
"""
import argparse, asyncio, contextlib, functools, http.server, json, os, random
import socket, threading, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHROME = os.environ.get('CHROME', '/usr/bin/chromium')

# ---------------------------------------------------------------- helpers ---

class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a): pass

@contextlib.asynccontextmanager
async def serve_root():
    """Serve ROOT on 127.0.0.1:<random port>."""
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
    handler = functools.partial(Quiet, directory=str(ROOT))
    httpd = http.server.ThreadingHTTPServer(('127.0.0.1', port), handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield f'http://127.0.0.1:{port}'
    finally:
        httpd.shutdown()

# ------------------------------------------------------- drag stress suite ---

# Global: every window, every moment — no stuck drag state, no legacy values.
CHECK_GLOBAL = """() => {
  const bad = [];
  document.querySelectorAll('.win').forEach(w => {
    if (w.classList.contains('dragging')) bad.push((w.id||w.className) + ' STUCK dragging class');
    if (w.style.zIndex === '60') bad.push((w.id||w.className) + ' legacy zIndex 60');
  });
  return bad;
}"""

# Per-window: immediately after ITS drag — the titlebar must be fully
# visible (±6 px) and above the taskbar at the scroll position it was
# dragged at, and the window's left/right edges inside the viewport.
# The window BODY may legitimately hang below the fold / behind the
# taskbar, like a real window manager.
CHECK_ONE = """(id) => {
  const w = document.getElementById(id);
  if (!w) return ['no such window ' + id];
  const r = w.getBoundingClientRect();
  const t = w.querySelector('.win-title').getBoundingClientRect();
  const tb = document.querySelector('.taskbar');
  const tbTop = tb ? tb.getBoundingClientRect().top : innerHeight;
  const bad = [], TOL = 6;
  if (t.top < -TOL) bad.push(id + ' titlebar top ' + t.top.toFixed(1));
  if (t.bottom > tbTop + TOL) bad.push(id + ' titlebar under taskbar ' + t.bottom.toFixed(1) + ' > ' + tbTop.toFixed(1));
  if (r.left < -TOL) bad.push(id + ' left ' + r.left.toFixed(1));
  if (r.right > innerWidth + TOL) bad.push(id + ' right ' + r.right.toFixed(1) + ' > ' + innerWidth);
  return bad;
}"""

WINS = [('player', '.win-title'), ('services', '.win-title'), ('process', '.win-title'),
        ('about', '.win-title'), ('contact', '.win-title')]

OWNERSHIP = """([id, x, y]) => {
  const el = document.elementFromPoint(x, y);
  const w = el && el.closest('.win');
  return w ? (w.id || '(anon)') : '(none)';
}"""

async def drag(page, win_id, title_sel, steps=12, scroll_mid=0):
    """Grab a window by its titlebar, wiggle it around (optionally scrolling
    the page mid-drag with the wheel held down), then release."""
    for attempt in range(5):
        box = await page.locator(f'#{win_id} {title_sel}').bounding_box()
        if not box:
            return [f'!! no box for {win_id}']
        sx, sy = box['x'] + box['width'] / 2, box['y'] + 8
        owner = await page.evaluate(OWNERSHIP, [win_id, sx, sy])
        if owner == win_id:
            break
        # Something else owns the click point. Legitimate desktop states:
        # - another window overlaps the titlebar -> reset window positions
        #   by dispatching a real resize event: that runs the site's own
        #   reset path, which also clears the internal dx/dy drag offsets
        #   (clearing only the style would leave them stale and skew the
        #   next drag — the exact bug fixed in the site code earlier)
        # - the fixed taskbar / an overlay covers it, or the point is off
        #   viewport -> scroll the titlebar into the safe band
        #
        # The site sets `scroll-behavior: smooth` on <html>, so scrollBy()
        # glides for ~500 ms. Force instant scrolling, then wait until the
        # position is genuinely stable before measuring anything again.
        await page.evaluate("""([sel, pad]) => {
            document.documentElement.style.scrollBehavior = 'auto';
            window.dispatchEvent(new Event('resize'));
            const t = document.querySelector(sel);
            const tb = document.querySelector('.taskbar');
            const tbTop = tb ? tb.getBoundingClientRect().top : innerHeight;
            const r = t.getBoundingClientRect();
            let dx = 0, dy = 0;
            if (r.bottom > tbTop - 4) dy = r.bottom - tbTop + pad;
            if (r.top < 0) dy = r.top - pad;
            if (r.left < 0) dx = r.left - pad;
            if (r.right > innerWidth) dx = r.right - innerWidth + pad;
            window.scrollBy({left: dx, top: dy, behavior: 'instant'});
        }""", [f'#{win_id} {title_sel}', 24])
        await page.evaluate("""() => new Promise(resolve => {
            let last = [scrollX, scrollY], stable = 0;
            const iv = setInterval(() => {
                const cur = [scrollX, scrollY];
                if (cur[0] === last[0] && cur[1] === last[1]) {
                    if (++stable >= 3) { clearInterval(iv); resolve(); }
                } else stable = 0;
                last = cur;
            }, 60);
        })""")
    else:
        return [f'!! {win_id} titlebar still covered by {owner} after resets']
    await page.mouse.move(sx, sy)
    await page.mouse.down()
    x, y = sx, sy
    pts = []
    for _ in range(steps):
        x += random.uniform(-180, 180)
        y += random.uniform(-120, 140)
        pts.append((x, y))
    half = steps // 2
    for i, (xx, yy) in enumerate(pts):
        await page.mouse.move(xx, yy, steps=2)
        if scroll_mid and i == half:
            await page.mouse.wheel(0, scroll_mid)
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.008)
    await page.mouse.up()
    await asyncio.sleep(0.05)
    return (await page.evaluate(CHECK_GLOBAL)
            + await page.evaluate(CHECK_ONE, win_id))

async def drag_suite(page, failed):
    async def run(tag, **kw):
        for w, t in WINS:
            await page.locator(f'#{w}').scroll_into_view_if_needed()
            bad = await drag(page, w, t, **kw)
            if bad:
                failed.append((tag, w, bad))

    # 1. plain chaotic drags
    for rnd in range(3):
        await run(f'round{rnd}')
    # 2. scroll mid-drag with the wheel held down (stale-bounds killer)
    await run('scroll-up-mid-drag', scroll_mid=-600)
    await run('scroll-down-mid-drag', scroll_mid=600)
    # 3. resize churn between drags (stale dx/dy killer)
    for vw, vh in [(1100, 760), (1600, 800), (1440, 900), (1000, 900)]:
        await page.set_viewport_size({'width': vw, 'height': vh})
        await page.wait_for_timeout(250)
        await run(f'resize-{vw}x{vh}')
    # 4. pointercancel mid-drag, window must recover and stay draggable
    for w, t in WINS:
        await page.locator(f'#{w}').scroll_into_view_if_needed()
        box = await page.locator(f'#{w} {t}').bounding_box()
        await page.mouse.move(box['x'] + 20, box['y'] + 8)
        await page.mouse.down()
        await page.mouse.move(box['x'] + 300, box['y'] - 100, steps=3)
        await page.evaluate(
            f"""() => document.querySelector('#{w} .win-title')
                   .dispatchEvent(new PointerEvent('pointercancel', {{bubbles: true}}))""")
        await page.mouse.up()
        await asyncio.sleep(0.05)
        bad = (await page.evaluate(CHECK_GLOBAL)
               + await page.evaluate(CHECK_ONE, w))
        if bad:
            failed.append(('pointercancel', w, bad))
        bad = await drag(page, w, t)
        if bad:
            failed.append(('post-cancel-drag', w, bad))
    # 5. no-jump-on-grab: windows that hang below the fold must follow the
    #    pointer smoothly instead of teleporting fully into view
    for w, t in WINS:
        await page.locator(f'#{w}').scroll_into_view_if_needed()
        before = await page.evaluate(
            """(id) => { const r = document.getElementById(id).getBoundingClientRect();
                          return [r.left, r.top]; }""", w)
        box = await page.locator(f'#{w} {t}').bounding_box()
        await page.mouse.move(box['x'] + box['width'] / 2, box['y'] + 8)
        await page.mouse.down()
        await page.mouse.move(box['x'] + box['width'] / 2 + 4, box['y'] + 12, steps=2)
        await page.mouse.up()
        await asyncio.sleep(0.05)
        after = await page.evaluate(
            """(id) => { const r = document.getElementById(id).getBoundingClientRect();
                          return [r.left, r.top]; }""", w)
        moved = ((after[0] - before[0]) ** 2 + (after[1] - before[1]) ** 2) ** 0.5
        if moved > 15:
            failed.append(('no-jump-on-grab', w, [f'moved {moved:.1f}px on a 5px nudge']))
        bad = await page.evaluate(CHECK_GLOBAL) + await page.evaluate(CHECK_ONE, w)
        if bad:
            failed.append(('no-jump-on-grab-check', w, bad))
    # 6. rapid-fire drags on one window (state accumulation)
    for i in range(6):
        await page.locator('#player').scroll_into_view_if_needed()
        bad = await drag(page, 'player', '.win-title', steps=25)
        if bad:
            failed.append((f'rapid{i}', 'player', bad))
    # 7. double-click reset, then drag again
    await page.locator('#player').scroll_into_view_if_needed()
    await page.locator('#player .win-title').dblclick()
    await page.wait_for_timeout(100)
    bad = await drag(page, 'player', '.win-title')
    if bad:
        failed.append(('dblclick', 'player', bad))
    # 8. stacking: the last dragged window must sit above every other window
    z_player = await page.evaluate("parseInt(document.querySelector('#player').style.zIndex||'0')")
    others = await page.evaluate("""() => ['services','process','about','contact']
        .map(id => parseInt(document.getElementById(id).style.zIndex||'0'))""")
    if z_player <= max(others):
        failed.append(('stacking', 'player', [f'player z {z_player} <= max other {max(others)}']))

# ------------------------------------------------------------ smoke suite ---

async def smoke_suite(page, failed):
    await page.goto(page.url, wait_until='networkidle')
    await page.wait_for_timeout(800)
    await page.click('#btnPlay')
    await page.wait_for_timeout(400)
    pressed = await page.get_attribute('#btnPlay', 'aria-pressed')
    if pressed != 'true':
        failed.append(('smoke', 'player', [f'play aria-pressed={pressed!r}']))
    await page.click('#btnLang')
    await page.click('#langMenu [data-lang="bg"]')
    await page.wait_for_timeout(300)
    if await page.evaluate('document.documentElement.lang') != 'bg':
        failed.append(('smoke', 'i18n', ['lang switch to bg failed']))
    mq = await page.evaluate("document.getElementById('mqA').textContent")
    if '♦' not in mq:
        failed.append(('smoke', 'marquee', ['♦ missing from bg marquee']))
    # every font referenced by fonts.css must have been served locally
    external = await page.evaluate(
        """() => performance.getEntriesByType('resource')
             .map(e => e.name).filter(n => !n.startsWith(location.origin))""")
    if external:
        failed.append(('smoke', 'self-contained', external))
    await page.screenshot(path=str(ROOT / '.test-smoke.png'), full_page=True)

# ------------------------------------------------------------------- main ---

async def main(suite):
    random.seed(11)
    failures, errors = [], []
    from playwright.async_api import async_playwright
    async with serve_root() as base, async_playwright() as p:
        browser = await p.chromium.launch(executable_path=CHROME,
                                          args=['--no-sandbox', '--disable-gpu'])
        page = await browser.new_page(viewport={'width': 1440, 'height': 900})
        page.on('pageerror', lambda e: errors.append('pageerror: ' + str(e)))
        page.on('console', lambda m: errors.append('console: ' + m.text) if m.type == 'error' else None)
        await page.goto(f'{base}/index.html')
        # the boot splash swallows clicks until it auto-dismisses at ~1.5 s
        await page.wait_for_selector('#boot', state='hidden', timeout=5000)
        await page.wait_for_timeout(1200)
        await (drag_suite if suite == 'drag' else smoke_suite)(page, failures)
        await browser.close()
    print('FAILURES:', json.dumps(failures, indent=1) if failures else 'NONE — all checks clean')
    print('CONSOLE/PAGE ERRORS:', errors if errors else 'none')
    return 1 if failures or errors else 0

if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('suite', nargs='?', default='drag', choices=['drag', 'smoke'])
    args = ap.parse_args()
    sys.exit(asyncio.run(main(args.suite)))
