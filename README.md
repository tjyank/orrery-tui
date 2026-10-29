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
| `--scale log\|sqrt\|linear` | radial distance compression |
| `--date 1977-09-05` | start at a UTC date/time instead of now |
| `--offline` | Uses the last cached fetch |
| `--ascii` | ASCII glyphs for bodies, for terminals with poor Unicode fonts |
| `--once --size 140x40` | print a single frame and exit |
| `--system jupiter` | start in a planet's moon system view |
| `--fresh` | ignore remembered view settings, and don't save them |
| `--no-mouse` | don't capture the mouse |

## Keys

| Key | Action |
|---|---|
| `←` `→` / `h` `l` | rotate around the ecliptic pole |
| `↑` `↓` / `k` `j` | tilt the camera, from edge-on to top-down |
| `+` `-` | zoom |
| `i` | snap back to the true isometric view |
| `Tab` / `Shift-Tab` | select the next or previous body |
| `f` | follow the selected body |
| `,` `.` | slower or faster time, including reverse: real time → 1 min/s → 1 hr/s → 1 day/s → 1 wk/s → 30 days/s → 1 yr/s |
| `space` | pause or resume time |
| `t` | jump back to present, at real time |
| `m` | cycle the distance scale: log → sqrt → linear |
| `o` | cycle the orbits: all → only the selected body's → none |
| `d` `n` | toggle drop lines to the ecliptic, and names |
| `a` | auto-spin the camera |
| `p` | toggle the info panel |
| `?` | show or hide the key legend |
| `r` | refetch from JPL Horizons |
| `Enter` / `v` | open the selected planet's moon system view |
| `J` | jump menu: a typed date or offset, or a preset moment |
| `b` | show or hide dwarf planets, asteroids and comets |
| `q` / `Esc` | quit |

### Mouse

Drag to rotate and tilt, scroll to zoom, and click a body or its name to select it. Double-click a planet to open its moons, or any other body to follow it. In the moon view, click anywhere on the planet to show its details again, and double-click a moon or the planet to follow it. Right-click backs out of the moon view or the jump menu. Pass `--no-mouse` to keep your terminal's text selection instead.

### Jumping in time

`J` opens a menu: jump to now, type a UTC date or an offset from the time shown (`+30d`, `-2y`; units `h d w m y`), or pick a preset. The presets are the next total solar eclipse, next lunar eclipse, next Mars and Jupiter oppositions, Halley's next perihelion, and moments like Apollo 11's landing and Voyager 2 at Neptune. After a long jump the app switches straight to the offline model.

### Small bodies

Ceres, Haumea, Makemake and Eris, Pallas and Vesta, and the comets Halley, Encke and 67P/Churyumov–Gerasimenko. They're drawn smaller, with fainter orbits, and comets grow a tail pointing away from the Sun inside about 4 au. For eccentric orbits the panel adds perihelion distance and date. `b` hides them all.

### Remembered settings

The camera, zoom, scale, toggles and selection for both views are saved on quit and restored next time, in `%APPDATA%\orrery\settings.json` on Windows or `~/.config/orrery/settings.json` elsewhere. `--scale` still overrides the saved scale, and `--fresh` starts from the defaults.

### Planetary system view

Opening a planet with `Enter` shows it at the centre, lit from the Sun's direction, with its moons on their real orbits. The `N` axis is its north pole, showing its tilt, and the ☼ marker at the edge points to the Sun. Moons in the planet's shadow are drawn dimmed. Rotate, tilt, zoom, time and the `o d n a i p` toggles work as in the main view; the camera is kept separate from the main one.

| Key | Action |
|---|---|
| `Tab` / `Shift-Tab` | cycle the planet and its moons |
| `[` `]` | previous or next planet's system |
| `f` | follow the selected moon |
| `m` | cycle the distance scale: log  → sqrt → linear |
| `Esc` / `Enter` / `v` | back to the solar system |

The panel lists **upcoming events**: moons entering the planet's shadow, and transits and occultations as seen from Earth. Countdowns run against simulated time, and the footer announces each event as it begins (at up to 1 hr/s). In Earth's system the list shows solar and lunar eclipses for the next 400 days, with their type: total, annular or partial. Times are geometric, with no light-travel delay added. They also treat the planet as a sphere, so events at oblate Saturn and Jupiter can be off by a few minutes.

Moon positions are fetched from Horizons for the system you open. They are cached in `~/.cache/orrery/horizons-moons.json` for 5 days and re-fetched once simulated time drifts 2 days from the data epoch, because moon orbits are perturbed much faster than planetary ones. Offline, the app uses built-in elements fitted to Horizons at 2026-01-01.

