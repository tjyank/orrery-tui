"""The stellar neighbourhood: star systems within about 20 light years, plus a few bright landmarks.

Positions are J2000 (RA, Dec) with distances in light years, mostly from Gaia DR3 and the
RECONS / Wikipedia nearest-star lists. Members of a multiple system share the system's position:
they are at most a fraction of a light year apart, far below what the view can show.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .coords import (SUN_ABS_MAG, Vec3, absolute_mag, equatorial_xyz, galactic_lb, parse_dec, parse_ra,
                     to_galactic)

RGB = tuple[int, int, int]


@dataclass(frozen=True)
class Star:
    name: str  # component label: "A", "B", or a proper name
    spectral: str
    vmag: float | None  # apparent visual magnitude from Earth; None for brown dwarfs too faint to matter


@dataclass(frozen=True)
class System:
    key: str
    name: str
    ra: str  # "hh mm ss.s"
    dec: str  # "+dd mm ss"
    dist_ly: float
    stars: tuple[Star, ...]
    aka: str = ""
    note: str = ""
    planets: int = 0  # confirmed planets
    landmark: bool = False  # a bright star beyond the neighbourhood proper, for orientation
    pos: Vec3 = field(init=False, compare=False)  # galactic Cartesian, light years, Sun at the origin

    def __post_init__(self):
        if self.dist_ly == 0:
            p = (0.0, 0.0, 0.0)
        else:
            p = to_galactic(equatorial_xyz(parse_ra(self.ra), parse_dec(self.dec), self.dist_ly))
        object.__setattr__(self, "pos", p)

    @property
    def primary(self) -> Star:
        return self.stars[0]

    @property
    def abs_mag(self) -> float | None:
        """Absolute visual magnitude of the brightest member (None: brown dwarfs only)."""
        if self.dist_ly == 0:
            return SUN_ABS_MAG
        mags = [s.vmag for s in self.stars if s.vmag is not None]
        return absolute_mag(min(mags), self.dist_ly) if mags else None

    @property
    def combined_abs_mag(self) -> float | None:
        if self.dist_ly == 0:
            return SUN_ABS_MAG
        mags = [s.vmag for s in self.stars if s.vmag is not None]
        if not mags:
            return None
        flux = sum(10 ** (-0.4 * m) for m in mags)
        return absolute_mag(-2.5 * math.log10(flux), self.dist_ly)

    @property
    def color(self) -> RGB:
        return spectral_color(self.primary.spectral)

    @property
    def galactic(self) -> tuple[float, float]:
        return galactic_lb(self.pos)

    def names(self) -> list[str]:
        """Everything a search may match."""
        out = [self.name, *[a.strip() for a in self.aka.split(",") if a.strip()]]
        out += [s.name for s in self.stars if len(s.name) > 2]
        return out


_CLASS_COLORS: dict[str, RGB] = {
    "O": (150, 170, 255), "B": (165, 190, 255), "A": (210, 222, 255), "F": (248, 246, 236),
    "G": (255, 230, 150), "K": (255, 182, 100), "M": (255, 120, 85),
    "D": (185, 215, 255),  # white dwarfs
    "L": (205, 90, 100), "T": (175, 85, 165), "Y": (135, 80, 170),  # brown dwarfs
}


def spectral_class(spectral: str) -> str:
    s = spectral[2:] if spectral.startswith("sd") else spectral
    return s[:1].upper() if s else "G"


def spectral_color(spectral: str) -> RGB:
    return _CLASS_COLORS.get(spectral_class(spectral), (220, 220, 220))


def kind(spectral: str) -> str:
    c = spectral_class(spectral)
    if c == "D":
        return "white dwarf"
    if c in "LTY":
        return "brown dwarf"
    if "IV" in spectral and "V" not in spectral.replace("IV", ""):
        return "subgiant"
    if "III" in spectral or "II" in spectral.replace("III", ""):
        return "giant"
    if spectral.startswith("sd"):
        return "subdwarf"
    return {"M": "red dwarf", "K": "orange dwarf", "G": "yellow dwarf"}.get(c, "main sequence")


S = Star
SYSTEMS: list[System] = [
    System("sun", "Sun", "", "", 0.0, (S("Sun", "G2V", -26.74),), aka="Sol",
           note="Our own star. Everything here is shown as seen from it, until you view from another star.",
           planets=8),
    System("alphacen", "α Centauri", "14 39 36.5", "-60 50 02", 4.37,
           (S("A", "G2V", 0.01), S("B", "K1V", 1.33)), aka="Alpha Centauri, Rigil Kentaurus, Toliman",
           note="The nearest Sun-like stars: a pair circling each other every 80 years."),
    System("proxima", "Proxima Centauri", "14 29 42.9", "-62 40 46", 4.246, (S("Proxima", "M5.5Ve", 11.13),),
           aka="Alpha Centauri C, Proxima",
           note="The closest star to the Sun, bound to α Centauri 0.2 ly away. A flare star.", planets=2),
    System("barnard", "Barnard's Star", "17 57 48.5", "+04 41 36", 5.96, (S("Barnard's Star", "M4V", 9.51),),
           note="The fastest-moving star in our sky: 10.4″ a year. It will pass within 3.8 ly of the Sun in 9800 years.",
           planets=4),
    System("luhman16", "Luhman 16", "10 49 18.7", "-53 19 10", 6.50, (S("A", "L7.5", None), S("B", "T0.5", None)),
           aka="WISE 1049-5319", note="The nearest brown dwarfs: a pair found by WISE in 2013."),
    System("wise0855", "WISE 0855−0714", "08 55 10.8", "-07 14 42", 7.43, (S("WISE 0855", "Y4", None),),
           aka="WISE 0855-0714", note="The coldest known brown dwarf, near freezing point, with water-ice clouds."),
    System("wolf359", "Wolf 359", "10 56 29.2", "+07 00 53", 7.86, (S("Wolf 359", "M6V", 13.51),),
           aka="CN Leonis", note="One of the faintest, lowest-mass stars known, and a frequent flarer."),
    System("lalande21185", "Lalande 21185", "11 03 20.2", "+35 58 12", 8.31, (S("Lalande 21185", "M2V", 7.52),),
           aka="Gliese 411, HD 95735", note="The brightest red dwarf in the northern sky.", planets=1),
    System("sirius", "Sirius", "06 45 08.9", "-16 42 58", 8.60, (S("A", "A1V", -1.46), S("B", "DA2", 8.44)),
           aka="Alpha Canis Majoris, Dog Star",
           note="The brightest star in the night sky, with a white dwarf companion the size of Earth."),
    System("luyten7268", "Luyten 726-8", "01 39 01.3", "-17 57 01", 8.73,
           (S("BL Ceti", "M5.5V", 12.54), S("UV Ceti", "M6V", 12.99)), aka="Gliese 65, BL Ceti, UV Ceti",
           note="UV Ceti gave its name to flare stars: it can brighten five-fold in minutes."),
    System("ross154", "Ross 154", "18 49 49.4", "-23 50 10", 9.70, (S("Ross 154", "M3.5Ve", 10.43),),
           aka="V1216 Sagittarii, Gliese 729"),
    System("ross248", "Ross 248", "23 41 54.7", "+44 10 30", 10.30, (S("Ross 248", "M5.5V", 12.29),),
           aka="HH Andromedae, Gliese 905",
           note="In about 36,000 years it will be the Sun's nearest neighbour, at 3 ly."),
    System("epseri", "ε Eridani", "03 32 55.8", "-09 27 30", 10.47, (S("Ran", "K2V", 3.73),),
           aka="Epsilon Eridani, Ran", note="A young star with dust belts like our asteroid and Kuiper belts.",
           planets=1),
    System("lacaille9352", "Lacaille 9352", "23 05 52.0", "-35 51 11", 10.72, (S("Lacaille 9352", "M0.5V", 7.34),),
           aka="Gliese 887", planets=2),
    System("ross128", "Ross 128", "11 47 44.4", "+00 48 16", 11.01, (S("Ross 128", "M4V", 11.13),),
           aka="FI Virginis, Gliese 447", note="A quiet red dwarf with a temperate Earth-mass planet.", planets=1),
    System("ezaqr", "EZ Aquarii", "22 38 33.4", "-15 18 07", 11.11,
           (S("A", "M5V", 13.33), S("B", "M5V", 13.27), S("C", "M6.5V", 14.03)), aka="Gliese 866, Luyten 789-6"),
    System("61cyg", "61 Cygni", "21 06 53.9", "+38 44 58", 11.40, (S("A", "K5V", 5.21), S("B", "K7V", 6.03)),
           aka="Bessel's Star", note="The first star to have its distance measured, by Bessel in 1838."),
    System("procyon", "Procyon", "07 39 18.1", "+05 13 30", 11.46, (S("A", "F5IV-V", 0.34), S("B", "DQZ", 10.7)),
           aka="Alpha Canis Minoris", note="A subgiant beginning to swell, with a white dwarf companion."),
    System("struve2398", "Struve 2398", "18 42 46.7", "+59 37 49", 11.49, (S("A", "M3V", 8.90), S("B", "M3.5V", 9.69)),
           aka="Gliese 725"),
    System("groombridge34", "Groombridge 34", "00 18 22.9", "+44 01 23", 11.62,
           (S("A", "M1.5V", 8.08), S("B", "M3.5V", 11.06)), aka="Gliese 15", planets=2),
    System("dxcnc", "DX Cancri", "08 29 49.3", "+26 46 37", 11.68, (S("DX Cancri", "M6.5V", 14.81),),
           aka="Gliese 1111"),
    System("epsind", "ε Indi", "22 03 21.7", "-56 47 10", 11.87,
           (S("A", "K5V", 4.69), S("Ba", "T1", None), S("Bb", "T6", None)), aka="Epsilon Indi",
           note="An orange dwarf with a pair of brown dwarfs, and a cold giant planet imaged by JWST.", planets=1),
    System("tauceti", "τ Ceti", "01 44 04.1", "-15 56 15", 11.91, (S("τ Ceti", "G8V", 3.50),),
           aka="Tau Ceti", note="The nearest single Sun-like star; a long-time favourite of SETI and science fiction."),
    System("gj1061", "GJ 1061", "03 35 59.7", "-44 30 45", 11.98, (S("GJ 1061", "M5.5V", 13.03),), planets=3),
    System("yzceti", "YZ Ceti", "01 12 30.6", "-16 59 56", 12.11, (S("YZ Ceti", "M4.5V", 12.07),),
           aka="Gliese 54.1", planets=3),
    System("luyten", "Luyten's Star", "07 27 24.5", "+05 13 33", 12.35, (S("Luyten's Star", "M3.5V", 9.85),),
           aka="Gliese 273", note="Only 1.1 ly from Procyon, which would shine at magnitude −4.5 in its sky.",
           planets=2),
    System("teegarden", "Teegarden's Star", "02 53 00.9", "+16 52 53", 12.50, (S("Teegarden's Star", "M7V", 15.13),),
           note="Found only in 2003: a tiny star with two Earth-mass planets in its temperate zone.", planets=3),
    System("kapteyn", "Kapteyn's Star", "05 11 40.6", "-45 01 06", 12.83, (S("Kapteyn's Star", "sdM1", 8.85),),
           aka="Gliese 191", note="A halo star passing through: it orbits the Galaxy backwards."),
    System("lacaille8760", "Lacaille 8760", "21 17 15.3", "-38 52 03", 12.95, (S("AX Microscopii", "M0V", 6.67),),
           aka="AX Microscopii, Gliese 825", note="One of the brightest red dwarfs, near naked-eye visibility."),
    System("scr1845", "SCR 1845−6357", "18 45 05.3", "-63 57 48", 13.05,
           (S("A", "M8.5V", 17.4), S("B", "T6", None)), aka="SCR 1845-6357"),
    System("kruger60", "Kruger 60", "22 27 59.5", "+57 41 45", 13.07, (S("A", "M3V", 9.79), S("B", "M4V", 11.41)),
           aka="DO Cephei"),
    System("wolf1061", "Wolf 1061", "16 30 18.1", "-12 39 45", 14.05, (S("Wolf 1061", "M3V", 10.07),),
           aka="Gliese 628", planets=3),
    System("wolf424", "Wolf 424", "12 33 17.2", "+09 01 15", 14.05, (S("A", "M5.5V", 13.18), S("B", "M7V", 13.17)),
           aka="FL Virginis"),
    System("vanmaanen", "Van Maanen's Star", "00 49 09.9", "+05 23 19", 14.07, (S("Van Maanen 2", "DZ7", 12.38),),
           aka="Van Maanen 2", note="The nearest single white dwarf, with metals from shredded planets in its atmosphere."),
    System("gj1", "Gliese 1", "00 05 24.4", "-37 21 27", 14.17, (S("Gliese 1", "M1.5V", 8.56),)),
    System("tzari", "TZ Arietis", "02 00 13.2", "+13 03 08", 14.58, (S("TZ Arietis", "M4.5V", 12.3),),
           aka="Gliese 83.1", planets=1),
    System("gj687", "Gliese 687", "17 36 25.9", "+68 20 21", 14.84, (S("Gliese 687", "M3V", 9.15),), planets=2),
    System("lhs292", "LHS 292", "10 48 12.6", "-11 20 14", 14.8, (S("LHS 292", "M6.5V", 15.6),)),
    System("gj674", "Gliese 674", "17 28 39.9", "-46 53 43", 14.84, (S("Gliese 674", "M3V", 9.38),), planets=1),
    System("gj876", "Gliese 876", "22 53 16.7", "-14 15 49", 15.24, (S("Gliese 876", "M4V", 10.17),),
           note="The first red dwarf found with a planet; its planets are locked in a 1:2:4 resonance.", planets=4),
    System("gj832", "Gliese 832", "21 33 34.0", "-49 00 32", 16.20, (S("Gliese 832", "M2V", 8.66),), planets=1),
    System("40eri", "40 Eridani", "04 15 16.3", "-07 39 10", 16.33,
           (S("Keid", "K0V", 4.43), S("B", "DA4", 9.52), S("C", "M4.5V", 11.24)), aka="Omicron2 Eridani, Keid",
           note="A triple with the most easily seen white dwarf. In Star Trek, Vulcan orbits here."),
    System("70oph", "70 Ophiuchi", "18 05 27.3", "+02 30 00", 16.59, (S("A", "K0V", 4.2), S("B", "K4V", 6.2)),
           note="Two orange dwarfs on an 88-year orbit, studied since William Herschel."),
    System("altair", "Altair", "19 50 47.0", "+08 52 06", 16.73, (S("Altair", "A7V", 0.76),),
           aka="Alpha Aquilae",
           note="Spins so fast (once every 9 hours) that it bulges 20% wider at the equator."),
    System("sigdra", "σ Draconis", "19 32 21.6", "+69 39 40", 18.8, (S("Alsafi", "G9V", 4.67),),
           aka="Sigma Draconis, Alsafi"),
    System("etacas", "η Cassiopeiae", "00 49 06.3", "+57 48 55", 19.4, (S("Achird", "G0V", 3.44), S("B", "K7V", 7.51)),
           aka="Eta Cassiopeiae, Achird", note="A pair that would look much like the Sun and a fainter orange star."),
    System("36oph", "36 Ophiuchi", "17 15 20.9", "-26 36 10", 19.5,
           (S("A", "K2V", 5.07), S("B", "K1V", 5.11), S("C", "K5V", 6.33)), aka="Guniibuu"),
    System("82eri", "82 Eridani", "03 19 55.7", "-43 04 11", 19.7, (S("82 Eridani", "G8V", 4.25),),
           aka="HD 20794", planets=3),
    System("delpav", "δ Pavonis", "20 08 43.6", "-66 10 55", 19.9, (S("δ Pavonis", "G8IV", 3.55),),
           aka="Delta Pavonis", note="An old, metal-rich near-twin of the Sun, starting to leave the main sequence."),
    # Bright landmarks beyond the neighbourhood, for orientation when zoomed out.
    System("vega", "Vega", "18 36 56.3", "+38 47 01", 25.0, (S("Vega", "A0V", 0.03),), aka="Alpha Lyrae",
           note="The zero point of the magnitude scale, with a dusty disc around it.", landmark=True),
    System("fomalhaut", "Fomalhaut", "22 57 39.0", "-29 37 20", 25.1, (S("Fomalhaut", "A3V", 1.16),),
           aka="Alpha Piscis Austrini", note="Surrounded by a sharp-edged debris ring imaged by Hubble and JWST.",
           landmark=True),
    System("pollux", "Pollux", "07 45 18.9", "+28 01 34", 33.8, (S("Pollux", "K0III", 1.14),), aka="Beta Geminorum",
           note="The nearest giant star, with a giant planet of its own.", planets=1, landmark=True),
    System("arcturus", "Arcturus", "14 15 39.7", "+19 10 57", 36.7, (S("Arcturus", "K1.5III", -0.05),),
           aka="Alpha Bootis", note="The brightest star of the northern sky: an old red giant 25 times the Sun's size.",
           landmark=True),
    System("capella", "Capella", "05 16 41.4", "+45 59 53", 42.9, (S("Aa", "K0III", 0.76), S("Ab", "G1III", 0.96)),
           aka="Alpha Aurigae", note="Two yellow giants on a 104-day orbit.", landmark=True),
    System("aldebaran", "Aldebaran", "04 35 55.2", "+16 30 33", 65.3, (S("Aldebaran", "K5III", 0.86),),
           aka="Alpha Tauri", note="The red eye of Taurus, in front of (not part of) the Hyades cluster.", landmark=True),
]
BY_KEY: dict[str, System] = {s.key: s for s in SYSTEMS}
