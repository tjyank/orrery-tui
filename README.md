# orrery-tui

https://github.com/user-attachments/assets/dd376fb0-338d-4f55-b933-c671a1648623

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
| `--offline` | never contact Horizons: use the last cached fetch, or the built-in orbits |
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
| `T` | trails: where each body has recently been |
| `a` | auto-spin the camera |
| `p` | toggle the info panel |
| `?` | show or hide the key legend |
| `r` | refetch from JPL Horizons |
| `Enter` / `v` | open the selected planet's moon system view |
| `J` | jump menu: a typed date or offset, or a preset moment |
| `b` | show or hide dwarf planets, asteroids and comets |
| `/` | add any asteroid or comet, by name or designation |
| `X` | remove the selected object you added |
| `q` / `Esc` | quit |

### Mouse

Drag to rotate and tilt, scroll to zoom, and click a body or its name to select it. Double-click a planet to open its moons, or any other body to follow it. In the moon view, click anywhere on the planet to show its details again, and double-click a moon or the planet to follow it. Right-click backs out of the moon view or the jump menu. Pass `--no-mouse` to keep your terminal's text selection instead.

### Jumping in time

`J` opens a menu: jump to now, type a UTC date or an offset from the time shown (`+30d`, `-2y`; units `h d w m y`), or pick a preset. The presets are the next total solar eclipse, next lunar eclipse, next Mars and Jupiter oppositions, Halley's next perihelion, and moments like Apollo 11's landing and Voyager 2 at Neptune. After a long jump the app switches straight to the offline model, while Horizons loads data for the new date. That model is accurate for 1800–2050; outside that range, positions are only rough until the live data arrives.

### Small bodies

Ceres, Haumea, Makemake and Eris, Pallas and Vesta, and the comets Halley, Encke and 67P/Churyumov–Gerasimenko. They're drawn smaller, with fainter orbits, and comets grow a tail pointing away from the Sun inside about 4 au. For eccentric orbits the panel adds perihelion distance and date. `b` hides them all. Their orbits change slowly, so routine refreshes skip them: they're re-fetched once simulated time is 10 days from their data.

### Trails

`T` draws a fading trail behind each body: where it has been over the last 15 seconds of real time, at the current speed. At real time that's too short to see, so speed time up with `.` The dots are evenly spaced in time, so speed shows: Mercury's dots spread out as it swings close to the Sun. Without a followed body, a trail stops short of a full orbit.

### Adding any asteroid or comet

Press `/` and type a name, number or designation as JPL Horizons knows it: `Apophis`, `Bennu`, `433`, `Hale-Bopp`, `2P`, `C/2021 S4`. The lookup runs in the background, and the object is added, selected and drawn like the built-in small bodies.

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
