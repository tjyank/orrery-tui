# orrery-tui

A live, isometric, rotatable model of the solar system in your terminal.

Planet positions come from **NASA/JPL Horizons**. Each state vector is turned into an osculating Keplerian orbit. When the simulated time drifts more than 20 days from the data epoch, the app fetches fresh data in the background. 

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
| `--offline` | Uses the last cached fetch |
| `--ascii` | ASCII glyphs for bodies, for terminals with poor Unicode fonts |
| `--once --size 140x40` | print a single frame and exit |
| `--system jupiter` | start in a planet's moon system view |

## Keys

| Key | Action |
|---|---|
| `←` `→` / `h` `l` | rotate around the ecliptic pole (yaw) |
| `↑` `↓` / `k` `j` | tilt the camera, from edge-on to top-down |
| `+` `-` | zoom |
| `i` | snap back to the true isometric view |
| `Tab` / `Shift-Tab` | select the next or previous body |
| `f` | follow the selected body |
| `,` `.` | slower or faster time, including reverse: real time → 1 min/s → 1 hr/s → 1 day/s → 1 wk/s → 30 days/s → 1 yr/s |
| `space` | pause or resume time |
| `t` | jump back to present, at real time |
| `m` | cycle the distance scale: log → sqrt → linear |
| `g` `o` `d` `n` | toggle the grid, orbits, drop lines to the ecliptic, and names |
| `a` | auto-spin the camera |
| `p` | toggle the info panel |
| `r` | refetch from JPL Horizons |
| `Enter` / `v` | open the selected planet's moon system view |
| `q` / `Esc` | quit |

### Planetary system view

Opening a planet with `Enter` shows it at the centre, lit from the Sun's direction, with its moons on their real orbits. The rings mark distances in the planet's equatorial plane, the `N` axis is its north pole, and the ☼ marker at the edge points to the Sun. Moons in the planet's shadow are drawn dimmed. Rotate, tilt, zoom, time and the `g o d n a i p` toggles work as in the main view; the camera is kept separate from the main one.

| Key | Action |
|---|---|
| `Tab` / `Shift-Tab` | cycle the planet and its moons |
| `[` `]` | previous or next planet's system |
| `f` | follow the selected moon |
| `m` | cycle the distance scale: log  → sqrt → linear |
| `Esc` / `Enter` / `v` | back to the solar system |

Moon positions are fetched from Horizons for the system you open. They are cached in `~/.cache/orrery/horizons-moons.json` for 5 days and re-fetched once simulated time drifts 2 days from the data epoch, because moon orbits are perturbed much faster than planetary ones. Offline, the app uses built-in elements fitted to Horizons at 2026-01-01. Those are good to a few degrees of orbital phase near the present.

## How it works

- **`orrery/ephemeris.py`**: queries the Horizons API (`EPHEM_TYPE=VECTORS`, `CENTER=500@10`, ecliptic J2000, au and au/day) one body at a time. It goes sequentially because Horizons answers concurrent requests with HTTP 503. It retries 503s with backoff, caches the last fetch at `~/.cache/orrery/horizons.json`, and runs all network work on a background thread. HTTPS uses the OS certificate store via `truststore`.
- **`orrery/kepler.py`**: converts state vectors to osculating elements and propagates them two-body style.
- **`orrery/bodies.py`**: body catalogue and JPL's *Keplerian Elements for Approximate Positions*, which serve as the offline fallback.
- **`orrery/render.py`**: orthographic camera projecting onto a braille-dot canvas. Each terminal cell is 2×4 dots, which gives roughly square pixels. Orbit segments behind the Sun's plane of view are drawn dimmer, and drop lines from each planet to the ecliptic show inclination.
- **`orrery/terminal.py`**: raw ANSI output plus non-blocking keys (`msvcrt` on Windows, `termios` elsewhere). It has no curses dependency.

The header shows where the data came from: green **LIVE**, yellow **CACHED** or red **OFFLINE**. If Horizons can't be reached, the footer shows the reason.

Accuracy: two-body propagation from a Horizons epoch stays within a small fraction of a degree for months.

## Tests

```powershell
.\.venv\Scripts\pip install -e ".[dev]"
.\.venv\Scripts\python -m pytest
```
