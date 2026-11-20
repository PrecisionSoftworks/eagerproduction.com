# Site test suite

Headless-browser tests for the window system and core interactions. Uses the
system Chromium via [Playwright](https://playwright.dev/python/) — no browser
download needed.

## Setup (once)

```sh
python3 -m venv .venv-tests
.venv-tests/bin/pip install playwright
```

## Run

```sh
.venv-tests/bin/python tests/site_test.py           # drag stress suite (default)
.venv-tests/bin/python tests/site_test.py smoke     # boot / fonts / i18n / player
```

The script serves the repo root on a random local port and tears it down after.
Exit code `0` = clean, `1` = failures listed as `(scenario, window, violations)`.

## What the drag suite covers

Every scenario re-checks after each drag that no window got stuck with a
`dragging` class, and that no legacy state (`zIndex 60`) survives. Bounds
assertions are described below the table.

| Scenario | Regression it guards against |
|---|---|
| 3 rounds of chaotic drags on all 5 windows | general drag math / accumulation |
| mouse-wheel scroll held mid-drag, up and down | bounds captured once at `pointerdown` going stale (windows dragged outside the viewport) |
| 4 viewport resizes between drags | internal `dx/dy` not reset on resize (window teleports on next drag) |
| `pointercancel` mid-drag, then dragging again | lost capture bricking a window's drag forever |
| no-jump-on-grab (5px nudge per window) | clamping the whole window instead of its titlebar — a window hanging below the fold must follow the pointer, not teleport into view |
| 6 rapid-fire drags on the player | state accumulation on one window |
| double-click reset, then drag | reset path consistency |
| stacking check | dragged window must end up above all others |

After each drag the suite re-checks that the just-dragged window's **titlebar**
is fully visible (±6 px) and above the taskbar at its drag-time scroll
position, and that the window's left/right edges stayed inside the viewport.
The window body is allowed to hang below the fold or behind the taskbar —
like a real window manager, only the titlebar must stay reachable. Windows
dragged earlier may legitimately be scrolled off-screen.

Before every grab the suite verifies with `elementFromPoint` that the intended
window actually owns the click point. If an earlier-dragged window covers the
titlebar — correct desktop behaviour — the suite dispatches a real `resize`
event (the site's own window-reset path, which also clears the internal drag
offsets) and scrolls the titlebar into the safe band, then retries, up to 5
times. It also forces `scroll-behavior: auto` for the session and waits for
the scroll position to settle before measuring, because the site's smooth
scrolling otherwise glides under the measurements.

## What the smoke suite covers

Play button `aria-pressed`, EN→BG language switch, the ♦ symbol actually
present in the Bulgarian LCD marquee, **zero requests to any origin other than
the local server** (self-contained check), plus a full-page screenshot saved to
`.test-smoke.png`.

## Notes

- Chromium path defaults to `/usr/bin/chromium`; override with `CHROME=/path`.
- The randomness is seeded, so runs are reproducible.
- Deleting `.test-smoke.png` after runs is safe; it's a debug artifact.
