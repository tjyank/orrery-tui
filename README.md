# orrery-tui

<img width="800" height="448" alt="m2-res_1076p-ezgif com-video-to-gif-converter (1)" src="https://github.com/user-attachments/assets/1491e6bc-bd6e-4d74-905f-ad5deca71704" />

A live, isometric, rotatable model of the solar system in your terminal.

Planet positions come from **NASA/JPL Horizons**. Each state vector is turned into an osculating Keplerian orbit. When the simulated time drifts more than 20 days from the data epoch, the app fetches fresh data in the background.

## Install

```
pip install orrery-tui
orrery
```

Or with [pipx](https://pipx.pypa.io/), which keeps command-line apps in their own environments: `pipx install orrery-tui`. If `orrery` isn't found after a plain `pip install`, run `python -m orrery` instead.

It needs Python 3.10+ and a truecolor terminal. Windows Terminal, iTerm2, kitty, WezTerm and most modern Linux terminals all work.

To work on the code instead, clone the repo and install it in editable mode with the test tools: `pip install -e ".[dev]"`, then run `python -m pytest`.

| Option | |
|---|---|
| `--scale log\|sqrt\|linear` | radial distance compression |
| `--date 1977-09-05` | start at a UTC date/time instead of now |
| `--offline` | never contact Horizons: use the last cached fetch, or the built-in orbits |
| `--ascii` | ASCII glyphs for bodies, for terminals with poor Unicode fonts |
| `--once --size 140x40` | print a single frame and exit |
| `--system jupiter` | start in a planet's moon system view |
| `--stars` | start in the map of the nearest stars |
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
| `S` | the star map: the nearest stars around the Sun |
| `q` / `Esc` | quit |

### Mouse

Drag to rotate and tilt, scroll to zoom, and click a body or its name to select it. Double-click a planet to open its moons, or any other body to follow it. In the moon view, click anywhere on the planet to show its details again, and double-click a moon or the planet to follow it. Right-click backs out of the moon view or the jump menu. Pass `--no-mouse` to keep your terminal's text selection instead.

### Jumping in time

`J` opens a menu: jump to now, type a UTC date or an offset from the time shown (`+30d`, `-2y`; units `h d w m y`). The presets are the next total solar eclipse, next lunar eclipse, next Mars and Jupiter oppositions, Halley's next perihelion, and moments like Apollo 11's landing and Voyager 2 at Neptune. After a long jump the app switches straight to the offline model, while Horizons loads data for the new date. That model is accurate for 1800–2050; outside that range, positions are only rough until the live data arrives.

### Small bodies

Ceres, Haumea, Makemake and Eris, Pallas and Vesta, and the comets Halley, Encke and 67P/Churyumov–Gerasimenko. They're drawn smaller, with fainter orbits, and comets grow a tail pointing away from the Sun inside about 4 au. For eccentric orbits the panel adds perihelion distance and date. `b` hides them all. Their orbits change slowly, so routine refreshes skip them: they're re-fetched once simulated time is 10 days from their data.

### Trails

`T` draws a fading trail behind each body: where it has been over the last 15 seconds of real time, at the current speed. At real time that's too short to see, so speed time up with `.`. Without a followed body, a trail stops short of a full orbit.

### Adding any asteroid or comet

Press `/` and type a name, number or designation as JPL Horizons knows it: `Apophis`, `Bennu`, `433`, `Hale-Bopp`, `2P`, `C/2021 S4`. The lookup runs in the background, and the object is added, selected and drawn like the built-in small bodies.

### Remembered settings

The camera, zoom, scale, toggles and selection for both views are saved on quit and restored next time, in `%APPDATA%\orrery\settings.json` on Windows or `~/.config/orrery/settings.json` elsewhere. `--scale` still overrides the saved scale, and `--fresh` starts from the defaults.

### Planetary system view

Opening a planet with `Enter` shows it at the centre with its moons on their real orbits. The `N` axis is its north pole, showing its tilt, and the ☼ marker at the edge points to the Sun. Moons in the planet's shadow are drawn dimmed. Rotate, tilt, zoom, time and the `o d n a i p` toggles work as in the main view; the camera is kept separate from the main one.

| Key | Action |
|---|---|
| `Tab` / `Shift-Tab` | cycle the planet and its moons |
| `[` `]` | previous or next planet's system |
| `f` | follow the selected moon |
| `m` | cycle the distance scale: log  → sqrt → linear |
| `Esc` / `Enter` / `v` | back to the solar system |

## The star map

`S` zooms out from the planets to the stars around the Sun: about 50 star systems within 20 light years at their real 3D positions in galactic coordinates, plus bright landmarks further out such as Vega, Arcturus and Aldebaran. `Esc` or `S` goes back to the planets, which wait where you left them. The map opens in the year the orrery shows.

The grid is the galactic plane. Drop lines show how far above or below it each star sits, and the spokes point toward the galactic centre (Sagittarius), the direction of the Sun's orbit (Cygnus), the anticentre (Auriga) and l 270° (Vela).

Pick a star and press `Enter` to **view from it**: the camera glides over, the grid recentres, and the panel shows how bright every other star, the Sun included, would look from there.

**Time runs in years here**, up to 100,000 either way, with the slider at the bottom. Stars drift along their real space motions, leaving faint tracks back to where they are today. Watch Barnard's Star pass within 3.8 ly around 11,700 CE, or Ross 248 become our nearest neighbour around 38,000 CE.

| Key | Action |
|---|---|
| `←` `→` / `h` `l` | rotate around the galactic pole |
| `↑` `↓` / `k` `j` | tilt |
| `+` `-` | zoom |
| `i` | back to the true isometric view |
| `a` | auto-spin |
| `Tab` / `Shift-Tab` | next or previous star, nearest first |
| `/` | find a star by name (`proxima`, `tau ceti`, `Gliese 876`, `Dog Star`) |
| `Enter` / `v` | view from the selected star |
| `s` / `Backspace` | back to the Sun |
| `[` `]` | range: 10 → 15 → 20 → 30 → 50 → 70 ly |
| `,` `.` | time slower or faster, through reverse: 10k ← 1k ← 100 ← 10 yr/s → 10 → 100 → 1k → 10k yr/s |
| `space` | play or pause time |
| `t` | back to the present, paused |
| `y` | go to a year: `38000`, `10000 BCE`, `+5000` (from now), `-20000` |
| `T` | motion tracks on or off |
| `m` / `M` | mark the selected star, to measure the distance to another / clear the mark |
| `g` `d` | grid, drop lines |
| `n` | names: all → bright → none |
| `p` | info panel |
| `?` | key list |
| `Esc` / `S` | back to the planets |
| `q` | quit |

Mouse: drag to rotate and tilt, scroll to zoom, click to select, double-click to view from a star, right-click to return to the Sun. Click or drag on the time slider to scrub through time.

**On the map**, glyphs show intrinsic brightness: `●` for stars about as luminous as the Sun or more, `•` for K and early M dwarfs, `∙` for faint red dwarfs and white dwarfs, `·` for brown dwarfs. The most luminous stars get a halo. Colour follows spectral class, from blue-white A stars through yellow G and orange K to red M dwarfs, with pale blue for white dwarfs and magenta for brown dwarfs. Multiple systems such as α Centauri AB are drawn as one point, with their members listed in the panel.

**The motion model** is heliocentric and straight-line: each star moves at constant velocity relative to the Sun, from its proper motion and radial velocity. Over tens of thousands of years that matches published close-approach predictions well; beyond that the stars' curved paths around the Galaxy matter, so the slider stops at ±100,000 years. Gravity between stars isn't modelled. A few objects (WISE 0855, SCR 1845's companion, LHS 292) have no measured radial velocity, so they move only across the sky and their panel speed is a lower bound.

The catalog, in `orrery/stellar/catalog.py`, is compiled from the RECONS and Gaia DR3 nearby-star lists; distances are good to about 0.05 ly. Planet counts are confirmed planets and will go out of date. The star map also runs on its own with `python -m orrery.stellar` (options: `--radius 30`, `--from Sirius`, `--select "tau ceti"`, `--year 38000`).
