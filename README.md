# orrery-tui

A live, isometric, rotatable model of the solar system in your terminal.

Planet positions come from **NASA/JPL Horizons**: heliocentric ecliptic J2000 state vectors, fetched for the current moment. Each state vector is turned into an osculating Keplerian orbit, so the planets move smoothly at any time-lapse speed. When the simulated time drifts more than 20 days from the data epoch, the app fetches fresh data in the background. While you're watching the present, it also refreshes every 15 minutes.

```
 ORRERY 2026-10-27 19:06:44 UTC  [real time]  ● LIVE  JPL Horizons (epoch 2026-10-27 19:06)
                     ⢀⣀⡠⠤⠤⠒⠒⠒⠊⠉⠉⠉⠉⠉⠉⠉⠉⠉⠒⠒⠒⠲⠤⠤⢄⣀⡀
          ⢠⠃⢠⠔⡡⡪⠏ ⢀⡤⠞⠉⠈⠑⢢⣤⡲⠝⠉  ⣀⡠⢴⡲⠮⠝⠓⠊⠉⠉⠁  ●⣤Jupiter
      ⢸ ⢸  ⢸⡇    ⡇   ⢸⡇   Sun⠷☼⠁   Venus ●[●]⢠ Earth   ...
```

## Run

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -e .
.\.venv\Scripts\orrery            # or: python -m orrery
```

On macOS/Linux, use `.venv/bin/...`. It needs Python 3.10+ and a truecolor terminal. Windows Terminal, iTerm2, kitty, WezTerm and most modern Linux terminals all work.

| Option | |
|---|---|
| `--scale log\|sqrt\|linear` | radial distance compression (default `log`; `linear` is to scale) |
| `--date 1977-09-05` | start at a UTC date/time instead of now |
| `--offline` | never touch the network. It uses the last cached fetch (if under 45 days old), otherwise the analytic model |
| `--ascii` | ASCII glyphs for bodies, for terminals with poor Unicode fonts |
| `--once --size 140x40` | print a single frame and exit (handy for screenshots and CI) |

## Keys

| Key | Action |
|---|---|
| `←` `→` / `h` `l` | rotate around the ecliptic pole (yaw) |
| `↑` `↓` / `k` `j` | tilt the camera, from edge-on to top-down |
| `+` `-` | zoom |
| `i` | snap back to the true isometric view (yaw 45°, elevation 35.26°) |
| `Tab` / `Shift-Tab` | select the next or previous body (shown in the info panel) |
| `f` | follow (centre the view on) the selected body |
| `,` `.` | slower or faster time, including reverse: real time → 1 min/s → 1 hr/s → 1 day/s → 1 wk/s → 30 days/s → 1 yr/s |
| `space` | pause or resume time |
| `t` | jump back to now, at real time |
| `m` | cycle the distance scale: log → sqrt → linear |
| `g` `o` `d` `n` | toggle the grid, orbits, drop lines to the ecliptic, and names |
| `a` | auto-spin the camera |
| `p` | toggle the info panel (shown when the terminal is ≥100 columns) |
| `r` | refetch from JPL Horizons now |
| `q` / `Esc` | quit |

## How it works

- **`orrery/ephemeris.py`**: queries the Horizons API (`EPHEM_TYPE=VECTORS`, `CENTER=500@10`, ecliptic J2000, au and au/day) one body at a time. It goes sequentially because Horizons answers concurrent requests with HTTP 503. It retries 503s with backoff, caches the last fetch at `~/.cache/orrery/horizons.json`, and runs all network work on a background thread. HTTPS uses the OS certificate store via `truststore`, so it also works behind TLS-inspecting proxies.
- **`orrery/kepler.py`**: converts state vectors to osculating elements and propagates them two-body style (Kepler's equation by Newton iteration).
- **`orrery/bodies.py`**: body catalogue and JPL's *Keplerian Elements for Approximate Positions* (valid 1800–2050), which serve as the offline fallback.
- **`orrery/render.py`**: orthographic camera (isometric by default) projecting onto a braille-dot canvas. Each terminal cell is 2×4 dots, which gives roughly square pixels. Orbit segments behind the Sun's plane of view are drawn dimmer, and drop lines from each planet to the ecliptic show inclination (look at Pluto).
- **`orrery/terminal.py`**: raw ANSI output plus non-blocking keys (`msvcrt` on Windows, `termios` elsewhere). It has no curses dependency.

The header shows where the data came from: green **LIVE**, yellow **CACHED** or red **OFFLINE**. If Horizons can't be reached, the footer shows the reason.

Accuracy: two-body propagation from a Horizons epoch stays within a small fraction of a degree for months. That's far below one braille dot, and the app re-anchors long before the error would show.

## Tests

```powershell
.\.venv\Scripts\pip install -e ".[dev]"
.\.venv\Scripts\python -m pytest
```
