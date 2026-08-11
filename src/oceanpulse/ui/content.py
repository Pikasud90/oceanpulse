"""Encyclopedia and glossary content.

Everything the interface can show a number for is explained here, in plain
language, together with how that number is known and how far it can be trusted.

Two editorial rules:

**Say how we know.** A figure is only useful if the reader can tell whether it
was measured, inferred, or computed by a model. Each entry names its origin.

**State the limits in the same breath as the claim.** The caveats are not a
disclaimer appended at the end; they are part of what the quantity means.

Content is structured data rather than prose in a template so the tab can
render it, search it, and cross-link it. Blocks are `(kind, payload)` where kind
is one of: `p`, `h3`, `ul`, `formula`, `note`, `warn`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence


@dataclass(frozen=True)
class Section:
    key: str
    title: str
    summary: str
    blocks: Sequence[tuple[str, object]] = field(default_factory=tuple)

    def search_text(self) -> str:
        parts = [self.title, self.summary]
        for kind, payload in self.blocks:
            if kind == "ul" and isinstance(payload, (list, tuple)):
                parts.extend(str(item) for item in payload)
            else:
                parts.append(str(payload))
        return " ".join(parts).lower()


@dataclass(frozen=True)
class Term:
    term: str
    unit: str
    definition: str
    why: str = ""
    aliases: tuple[str, ...] = ()

    def search_text(self) -> str:
        return " ".join(
            (self.term, self.unit, self.definition, self.why, *self.aliases)
        ).lower()


# ===========================================================================
# Encyclopedia
# ===========================================================================

HOW_WE_KNOW = Section(
    key="how-we-know",
    title="How anyone knows anything about the ocean",
    summary=(
        "The ocean is mostly unvisited. Almost every number in this tool is an "
        "inference from sparse measurement, and knowing which kind matters."
    ),
    blocks=(
        (
            "p",
            "The ocean covers about 71% of the planet and averages 3.7 km deep. "
            "No instrument network comes close to sampling it. Everything we know "
            "comes from four overlapping efforts, each with a different blind spot.",
        ),
        ("h3", "1. Ships, and their accidental record"),
        (
            "p",
            "For most of recorded history, ocean data was a by-product of trade "
            "and war. Crews threw buckets over the side and read a thermometer. "
            "The archive that survives — millions of such readings — is invaluable "
            "and deeply uneven: it follows shipping lanes, thins out in the "
            "Southern Ocean, and its methods changed under the ships' feet. "
            "Bucket type alone introduces a bias of a few tenths of a degree, "
            "which is the same size as the climate signal being looked for.",
        ),
        ("h3", "2. Moored and drifting buoys"),
        (
            "p",
            "From the 1970s, instrumented buoys began returning continuous "
            "records from fixed points. They are the closest thing to ground "
            "truth for waves and near-surface temperature, and they are the "
            "reference against which satellites are calibrated. There are only "
            "a few thousand of them for an entire ocean.",
        ),
        (
            "p",
            "Since 2000 the Argo array — around 4,000 free-drifting floats that "
            "sink to 2 km and rise every ten days — has done for the ocean "
            "interior what weather balloons did for the atmosphere. It is the "
            "single biggest reason we can now say anything confident about how "
            "much heat the ocean has absorbed.",
        ),
        ("h3", "3. Satellites"),
        (
            "p",
            "Satellites gave the first genuinely global view. They do not measure "
            "the ocean directly; they measure radiation or radar echoes and infer "
            "ocean properties from them. Infrared radiometers give surface "
            "temperature but cannot see through cloud. Microwave sensors see "
            "through cloud at coarser resolution. Radar altimeters time a pulse "
            "to the surface and back, which yields sea-surface height — and, from "
            "the shape of the returned echo, wave height.",
        ),
        ("h3", "4. Models, analyses and reanalyses"),
        (
            "p",
            "Sparse observations are stitched into complete fields by physics. A "
            "model integrates the equations of fluid motion forward in time; data "
            "assimilation nudges it toward the observations as they arrive. The "
            "result is gap-free and globally consistent, which is exactly what a "
            "map needs — and it is not a measurement.",
        ),
        (
            "note",
            "This distinction runs through the whole application. Wave and "
            "current fields here are model output. Sea-surface temperature is a "
            "satellite-plus-in-situ analysis. Sea level is a satellite "
            "measurement, gridded. Each is labelled where it appears.",
        ),
        ("h3", "The four words worth keeping straight"),
        (
            "ul",
            [
                "Observation — an instrument produced this number at this place and time.",
                "Analysis — a best estimate for a past time, combining a model with the observations that have arrived.",
                "Reanalysis — an analysis recomputed for a long historical period with one fixed, modern method, so the record is consistent through time.",
                "Forecast — a model run past the last observation. Nothing has been measured yet.",
            ],
        ),
    ),
)

WAVES = Section(
    key="waves",
    title="Waves: height, period and direction",
    summary=(
        "Wind waves carry more energy per square metre than any other renewable "
        "flux at sea. Three numbers describe a sea state, and none of them "
        "describes a single wave."
    ),
    blocks=(
        ("h3", "Significant wave height (Hs, Hm0)"),
        (
            "p",
            "The sea surface is not one wave but a superposition of thousands, "
            "so a single height is meaningless. Significant wave height is "
            "roughly the average height of the highest third of waves — a "
            "definition that began as an estimate of what an experienced "
            "observer would call 'the' wave height, and that turned out to match "
            "four times the standard deviation of the surface elevation. That "
            "second definition is what instruments and models actually compute.",
        ),
        ("formula", "H<sub>m0</sub> = 4 √m₀,  where m₀ is the variance of surface elevation"),
        (
            "warn",
            "Individual waves are routinely much larger than Hs. Over a few "
            "hours the largest single wave is typically about 1.8 × Hs, and "
            "waves above 2 × Hs are observed. A 5 m sea state can hand you an "
            "11 m wave. Hs is a statistic, not a ceiling.",
        ),
        ("h3", "Peak period (Tp)"),
        (
            "p",
            "Break a record of the sea surface into its constituent frequencies "
            "and you get an energy spectrum. The peak period is the period of "
            "the frequency band carrying the most energy. Long periods mean "
            "swell — waves generated by a distant storm that have outrun their "
            "wind and sorted themselves by speed on the way. Short periods mean "
            "local wind sea, still being forced.",
        ),
        (
            "p",
            "Period matters as much as height. A 2 m swell at 16 s carries far "
            "more energy, and reaches far deeper, than a 2 m wind sea at 6 s. "
            "It is also why a distant storm can produce dangerous surf on a "
            "windless, sunny day.",
        ),
        ("h3", "Direction"),
        (
            "p",
            "The compass direction waves are coming *from*, by the usual "
            "convention. The polar plot on the Port Timeline tab shows how "
            "direction and period are distributed together, which is how a "
            "port's exposure becomes obvious: a harbour open to the southwest "
            "with a long-period southwesterly swell climate is a harbour that "
            "spends winter closed.",
        ),
        ("h3", "Where these numbers come from here"),
        (
            "p",
            "OceanPulse takes waves from Open-Meteo's marine API, which serves "
            "output from spectral wave models. Such models solve an energy "
            "balance for the wave spectrum over the whole ocean, forced by "
            "forecast winds, accounting for generation by wind, dissipation by "
            "whitecapping and bottom friction, and nonlinear transfer between "
            "frequencies. They are validated against buoys and altimeters and "
            "are generally good in the open ocean; they are weakest exactly "
            "where people live — in shallow, sheltered and semi-enclosed water.",
        ),
    ),
)

WAVE_POWER = Section(
    key="wave-power",
    title="Wave power and the power spectrum",
    summary=(
        "Energy flux per metre of wave crest, in kW/m. The distribution across "
        "the ocean is extremely skewed, which is why the chart is a spectrum "
        "rather than an average."
    ),
    blocks=(
        (
            "p",
            "A wave train transports energy. The rate at which it does so, per "
            "metre measured along the wave crest, is the wave energy flux — "
            "conventionally called wave power. In deep water it depends on the "
            "square of the height and linearly on the period:",
        ),
        (
            "formula",
            "P = ρg²/(64π) · H<sub>m0</sub>² · T<sub>e</sub>  ≈  0.49 · H<sub>m0</sub>² · T<sub>e</sub>   [kW/m]",
        ),
        (
            "p",
            "With seawater density ρ = 1025 kg/m³ and g = 9.81 m/s², the "
            "coefficient works out at 0.49 when height is in metres and period "
            "in seconds. A 2 m sea with an 8 s period carries about 16 kW/m: "
            "roughly the output of a small car engine, for every metre of a "
            "wave front that may be hundreds of kilometres long.",
        ),
        (
            "warn",
            "The formula wants the energy period Te. Open-Meteo publishes the "
            "peak period Tp, and Te ≈ 0.9 Tp for a typical wind-sea spectrum, "
            "so every wave-power figure in this tool is high by roughly 11%. "
            "The conversion factor is a named constant in the code rather than "
            "buried, so it can be changed if you have a better spectral "
            "assumption for your region.",
        ),
        ("h3", "Why the distribution is the interesting part"),
        (
            "p",
            "Wave power across the ocean is not normally distributed. Most of "
            "the sea is calm most of the time, and a small fraction of place and "
            "time carries most of the energy — the Southern Ocean, the North "
            "Atlantic in winter, and storm events lasting hours. The median "
            "sampled cell in this tool typically sits under 10 kW/m while the "
            "99th percentile runs to several hundred.",
        ),
        (
            "p",
            "That skew is why the Physics tab plots a full distribution with a "
            "density curve rather than reporting a mean. For wave energy "
            "engineering it is also the whole design problem: a converter has to "
            "earn its money from the median and survive the tail.",
        ),
    ),
)

CURRENTS = Section(
    key="currents",
    title="Ocean currents and why sea level reveals them",
    summary=(
        "Away from the equator, the slope of the sea surface and the speed of "
        "the current beneath it are two views of the same thing."
    ),
    blocks=(
        ("h3", "The rotating-planet shortcut"),
        (
            "p",
            "On a rotating planet, a large-scale current that persists for more "
            "than a day settles into a balance between two forces: the pressure "
            "gradient pushing water downhill, and the Coriolis effect deflecting "
            "it sideways. The result is counter-intuitive — the water ends up "
            "flowing *along* the slope rather than down it. This is geostrophic "
            "balance.",
        ),
        ("formula", "f·v = g·∂η/∂x    and    f·u = −g·∂η/∂y"),
        (
            "p",
            "Here η is sea-surface height, f = 2Ω·sin(latitude) is the Coriolis "
            "parameter, and u, v are the current components. The practical "
            "consequence is remarkable: measure the *shape* of the sea surface "
            "from orbit and you can compute the current without ever touching "
            "the water. Sea surface height differences of a few tens of "
            "centimetres over a hundred kilometres correspond to currents of "
            "around a metre per second.",
        ),
        (
            "warn",
            "Geostrophy fails at the equator, where f goes to zero and the "
            "assumption divides by nothing. Equatorial currents need different "
            "physics, and altimetry-derived geostrophic velocities are not "
            "meaningful within a few degrees of the line.",
        ),
        ("h3", "What geostrophy leaves out"),
        (
            "ul",
            [
                "Ekman flow — wind dragging directly on the surface layer, deflected about 45° from the wind at the surface and 90° in the depth-integrated average. This is what drives coastal upwelling.",
                "Tidal currents — strong, reversing, and dominant in many shelf seas and straits.",
                "Stokes drift — the slow net transport carried by the waves themselves, which matters for anything floating.",
                "Density-driven overturning — the slow global circulation that ventilates the deep ocean.",
            ],
        ),
        (
            "p",
            "The current field in this tool is total surface current from the "
            "wave model, which does include wind-driven flow. The separate "
            "geostrophic components from altimetry are stored alongside it, so "
            "the two can be compared where both exist.",
        ),
        ("h3", "Reading the arrows"),
        (
            "p",
            "Arrows on the Global Pulse map point the way the water is going, "
            "and their length scales with speed. Because the sampling grid is "
            "sparse, the arrows show flow at a few hundred points — not a "
            "continuous field. Eddies, fronts and jets narrower than the "
            "spacing between samples fall entirely between them.",
        ),
    ),
)

SST = Section(
    key="sst",
    title="Sea-surface temperature",
    summary=(
        "The most-measured ocean variable, and the one where 'the surface' turns "
        "out to need defining."
    ),
    blocks=(
        (
            "p",
            "Sea-surface temperature sets how much heat and moisture the ocean "
            "gives the atmosphere, so it drives weather, hurricane intensity, "
            "monsoons and El Niño. It is also the ocean variable with the "
            "longest and densest record.",
        ),
        ("h3", "Which surface?"),
        (
            "p",
            "An infrared satellite senses the top few micrometres — the skin. A "
            "microwave sensor senses the top millimetre or so. A drifting buoy "
            "measures a few tens of centimetres down. A ship's engine intake "
            "samples several metres down. On a calm sunny afternoon these differ "
            "by more than a degree, because the top of the ocean stratifies. Any "
            "long record has to define a reference depth and correct everything "
            "to it.",
        ),
        ("h3", "How OISST is built"),
        (
            "p",
            "OceanPulse takes its long temperature record from NOAA's Optimum "
            "Interpolation SST, version 2.1: daily, quarter-degree, complete, "
            "from September 1981 to the present. It blends AVHRR satellite "
            "infrared with in-situ ship and buoy data. The satellite provides "
            "coverage; the in-situ data provides the anchor, because satellite "
            "retrievals drift with instrument ageing, orbital changes and "
            "volcanic aerosol. Satellite values are bias-corrected against the "
            "in-situ network, then everything is interpolated onto the grid with "
            "a scheme that weights each observation by its expected error and "
            "its distance.",
        ),
        (
            "note",
            "'Complete' is doing work in that sentence. Where and when there "
            "were no observations, the value is an interpolation with a larger "
            "uncertainty that the gridded product does not carry alongside it. "
            "The early 1980s and the high-latitude Southern Ocean are the "
            "thinnest parts of the record.",
        ),
        (
            "p",
            "Near-term temperature in this tool comes instead from the "
            "Open-Meteo marine model at hourly resolution. Where both cover the "
            "same hour, the hourly value is kept and the daily analysis only "
            "fills gaps — the precedence is explicit in the storage layer.",
        ),
    ),
)

SEA_LEVEL = Section(
    key="sea-level",
    title="Sea level: anomaly, absolute, and rise",
    summary=(
        "Radar altimetry measures the height of the sea surface to about a "
        "centimetre from 1,300 km up. What it reports is a departure from an "
        "average, not a depth."
    ),
    blocks=(
        ("h3", "What an altimeter does"),
        (
            "p",
            "A satellite radar altimeter fires a pulse straight down and times "
            "the echo. Knowing the satellite's own orbit to within a couple of "
            "centimetres — itself a considerable achievement — gives the height "
            "of the sea surface. Corrections are needed for the atmosphere "
            "slowing the pulse, for tides, and for the fact that the ocean's "
            "own surface responds to atmospheric pressure.",
        ),
        (
            "p",
            "The continuous record starts with TOPEX/Poseidon in 1992 and runs "
            "through the Jason series to Sentinel-6. Thirty-plus years of "
            "consistent, global, centimetre-scale sea-level measurement is one "
            "of the more remarkable observational achievements in Earth science.",
        ),
        ("h3", "Anomaly, not height"),
        (
            "p",
            "Sea level anomaly (SLA) is the sea surface height minus a long-term "
            "mean surface for that location. A value of +0.15 m means the sea "
            "stands 15 cm higher there than its own average — not 15 cm above "
            "any datum you could mark on a wall. Anomalies are used because the "
            "mean surface itself is dominated by the Earth's gravity field, "
            "which is lumpy at the scale of tens of metres and has nothing to do "
            "with ocean dynamics.",
        ),
        ("h3", "Why sea level rises"),
        (
            "p",
            "Global mean sea level has risen at roughly 3.3–3.4 mm per year over "
            "the altimeter era, and the rate is increasing. Two contributions "
            "dominate: water added from melting glaciers and ice sheets, and "
            "thermal expansion as the ocean warms — seawater, like most things, "
            "takes up more room when heated. Over 90% of the excess heat trapped "
            "by greenhouse gases has gone into the ocean, and the expansion that "
            "follows is a large part of why the sea is higher.",
        ),
        (
            "p",
            "That link is why this application computes temperature and sea "
            "level anomalies together and correlates them. The positive "
            "correlation the Physics tab typically finds at a port is the local, "
            "seasonal expression of the same steric effect.",
        ),
        (
            "warn",
            "Local sea-level change departs from the global mean by a lot. Ocean "
            "circulation redistributes water, the land itself rises or subsides, "
            "and the gravitational pull of a shrinking ice sheet actually lowers "
            "sea level nearby. For any coastal question, the local trend is the "
            "one that matters, and it is not the global number.",
        ),
        (
            "note",
            "The sea-level product used here covers 2017 onwards and runs "
            "several months behind real time. Anything the tool shows for sea "
            "level is an analysis of the recent past, never of today.",
        ),
    ),
)

ANOMALIES = Section(
    key="anomalies",
    title="Anomalies, z-scores and marine heatwaves",
    summary=(
        "Absolute temperature says where you are. An anomaly says whether "
        "something unusual is happening."
    ),
    blocks=(
        (
            "p",
            "22 °C is ordinary in the Mediterranean in June and extraordinary in "
            "the North Sea. To compare places, or to detect an event, you need "
            "the departure from what is normal *there* — an anomaly.",
        ),
        ("h3", "The z-score"),
        (
            "p",
            "Dividing an anomaly by the local standard deviation gives a "
            "z-score, which is dimensionless and comparable anywhere:",
        ),
        ("formula", "Z = (x − μ) / σ"),
        (
            "p",
            "Roughly two-thirds of values sit within ±1σ and about 95% within "
            "±2σ, so a z above +2 is genuinely unusual and above +3 is rare. "
            "OceanPulse computes its baseline from a trailing window that "
            "excludes the current value — otherwise an extreme reading would "
            "inflate the very standard deviation used to judge it.",
        ),
        ("h3", "Marine heatwaves, properly defined"),
        (
            "p",
            "The accepted definition (Hobday et al., 2016) is a discrete period "
            "when temperature at a location exceeds the 90th percentile of a "
            "30-year daily climatology for at least five consecutive days, with "
            "gaps of two days or fewer tolerated within one event. The "
            "climatology is calendar-day-specific, so August is compared with "
            "other Augusts.",
        ),
        (
            "warn",
            "OceanPulse cannot compute that from a database built over weeks. "
            "What the Global Pulse tab reports as 'warm-spell cells' is a "
            "rolling-baseline approximation: cells whose z-score against their "
            "own recent history exceeds +2. It is a useful attention-getter and "
            "it is not a marine heatwave count. The label says so wherever the "
            "number appears.",
        ),
        (
            "p",
            "Marine heatwaves matter because they kill things. Documented events "
            "have caused mass coral bleaching, kelp forest collapse, seabird and "
            "marine mammal die-offs, and fishery closures. They have become "
            "markedly more frequent and longer-lasting as the ocean has warmed.",
        ),
    ),
)

COASTAL = Section(
    key="coastal",
    title="Coastal water level: what actually floods a harbour",
    summary=(
        "Flooding is a sum of contributions on very different timescales. This "
        "tool holds some of them and not others, and the difference is important."
    ),
    blocks=(
        (
            "p",
            "The water level at a quayside at any moment is the sum of several "
            "independent pieces:",
        ),
        (
            "ul",
            [
                "Mean sea level — the slowly rising baseline, millimetres per year.",
                "Tide — astronomically forced, predictable years ahead, ranging from centimetres to over 15 m depending on coastline.",
                "Storm surge — wind pushing water against the coast plus the sea rising under low pressure; hours to days, up to several metres.",
                "Wave setup — the mean water level raised by waves breaking on the shore, typically a fifth or so of offshore wave height.",
                "Wave runup — individual waves surging up the beach or structure, depending strongly on slope.",
            ],
        ),
        (
            "warn",
            "OceanPulse holds no tide predictions. Tides are the largest "
            "short-term term almost everywhere, so the 'coastal exceedance' "
            "panel on the Physics tab cannot be a flood forecast and is not one. "
            "It stacks sea-level anomaly, a surge allowance you choose, and a "
            "crude wave-setup term against a threshold you choose, to show how "
            "those components combine. Nothing more.",
        ),
        (
            "p",
            "Why the combination matters more than any single term: a surge that "
            "would be harmless at low tide overtops the wall at high tide. Risk "
            "at the coast is about coincidence, and rising mean sea level shifts "
            "the whole distribution so that coincidences which used to be rare "
            "become ordinary. This is why a few centimetres of sea-level rise "
            "translates into a large multiplication of flood frequency.",
        ),
        (
            "note",
            "For an operational answer, use your national hydrographic or "
            "meteorological service. They have the tide model, the surge model, "
            "the local bathymetry and the legal responsibility.",
        ),
    ),
)

DATA_IN_TOOL = Section(
    key="data-in-tool",
    title="What is in this tool, exactly",
    summary="Every source, its resolution, its coverage and its nature.",
    blocks=(
        ("h3", "Open-Meteo Marine — waves, currents, near-term temperature"),
        (
            "ul",
            [
                "Nature: numerical wave-model output. Not a measurement.",
                "Resolution: hourly.",
                "Coverage measured by probing, and it differs per variable: waves from about December 2021, currents from January 2022, sea-surface temperature only from December 2022.",
                "Includes future hours. Those rows are flagged as forecasts and excluded from analysis by default.",
            ],
        ),
        ("h3", "NOAA OISST v2.1 — long-run sea-surface temperature"),
        (
            "ul",
            [
                "Nature: satellite-plus-in-situ analysis on a grid.",
                "Resolution: daily, 0.25°.",
                "Coverage: September 1981 to within about two weeks of the present.",
            ],
        ),
        ("h3", "NOAA CoastWatch altimetry — sea level and geostrophic currents"),
        (
            "ul",
            [
                "Nature: satellite measurement, gridded and interpolated.",
                "Resolution: daily, 0.25°.",
                "Coverage: February 2017 onwards, running several months in arrears.",
            ],
        ),
        ("h3", "NGA World Port Index and GeoNames — the gazetteer"),
        (
            "ul",
            [
                "2,951 ports with harbour metadata, plus coastal settlements filtered against an ocean mask.",
                "Used only to find places. No port attribute is used as ocean data.",
            ],
        ),
        ("h3", "Four limits to carry into any analysis"),
        (
            "ul",
            [
                "Most of this is model or analysis output, not instrument readings.",
                "Variables begin at different dates. A model trained across one of those boundaries will learn a regime change that is an artefact of data availability, not of the ocean.",
                "The global grid is a sparse sample of a few hundred points. It cannot resolve eddies, fronts or anything coastal. Use tracked ports for spatial questions.",
                "Sea level lags by months, so no sea-level figure here describes today.",
            ],
        ),
    ),
)

SECTIONS: tuple[Section, ...] = (
    HOW_WE_KNOW,
    WAVES,
    WAVE_POWER,
    CURRENTS,
    SST,
    SEA_LEVEL,
    ANOMALIES,
    COASTAL,
    DATA_IN_TOOL,
)


# ===========================================================================
# Glossary
# ===========================================================================

GLOSSARY: tuple[Term, ...] = (
    Term(
        "Significant wave height (Hs, Hm0)",
        "m",
        "Approximately the mean height of the highest third of waves; formally four times the standard deviation of surface elevation.",
        "Individual waves reach about 1.8× this over a few hours, so it is a statistic rather than a maximum.",
        ("wave height", "hm0", "hs"),
    ),
    Term(
        "Peak wave period (Tp)",
        "s",
        "The wave period carrying the most energy in the spectrum.",
        "Long periods mean swell from a distant storm; short periods mean locally generated wind sea.",
        ("period", "tp", "swell period"),
    ),
    Term(
        "Energy period (Te)",
        "s",
        "The spectrally weighted mean period that the wave-power formula actually calls for.",
        "Te ≈ 0.9 Tp, so using Tp overstates wave power by roughly 11%.",
        ("te",),
    ),
    Term(
        "Wave direction",
        "° / compass",
        "The compass direction waves are travelling from, by convention.",
        "A port's exposure is determined by which directions are open to it.",
        ("direction", "bearing", "swell direction"),
    ),
    Term(
        "Wave energy flux (wave power)",
        "kW/m",
        "Energy transported per second per metre of wave crest: P ≈ 0.49 · Hs² · T.",
        "Scales with the square of height, so a doubling of wave height quadruples the power.",
        ("wave power", "energy flux", "kw/m"),
    ),
    Term(
        "Sea-surface temperature (SST)",
        "°C",
        "Temperature of the uppermost ocean, referenced to a defined depth because skin, sub-skin and bulk values differ.",
        "Drives heat and moisture exchange with the atmosphere, and so storms and monsoons.",
        ("sst", "temperature"),
    ),
    Term(
        "Sea level anomaly (SLA)",
        "m",
        "Sea-surface height minus the long-term mean surface at that location.",
        "Positive means the sea stands higher than its own average there; it is not a height above any land datum.",
        ("sla", "sea level", "anomaly"),
    ),
    Term(
        "Geostrophic current (ugos, vgos)",
        "m/s",
        "Current inferred from the slope of the sea surface under the balance of pressure gradient and Coriolis force.",
        "Lets a satellite measure currents without touching the water — but the method fails near the equator.",
        ("geostrophic", "ugos", "vgos"),
    ),
    Term(
        "Ocean current velocity",
        "km/h",
        "Speed of the near-surface flow, including wind-driven components.",
        "Shown on the map as arrows pointing the way the water is going.",
        ("current", "velocity", "flow"),
    ),
    Term(
        "Z-score (standardised anomaly)",
        "σ",
        "How far a value sits from its own baseline, in standard deviations: Z = (x − μ)/σ.",
        "Dimensionless, so it is comparable between places and between variables. Above +2 is unusual; above +3 is rare.",
        ("z-score", "sigma", "standardised", "standardized"),
    ),
    Term(
        "Marine heatwave",
        "days",
        "Formally, five or more consecutive days above the 90th percentile of a 30-year daily climatology (Hobday et al., 2016).",
        "This tool shows a rolling-baseline approximation instead, because it has no 30-year climatology. It is labelled as such.",
        ("heatwave", "mhw", "warm spell"),
    ),
    Term(
        "Douglas sea state",
        "scale",
        "A word describing the sea by wave height: Calm, Smooth, Slight, Moderate, Rough, Very rough, High, Very high, Phenomenal.",
        "Gives a number a meaning a reader can feel, and survives being printed in greyscale.",
        ("sea state", "douglas"),
    ),
    Term(
        "Analysis vs forecast",
        "flag",
        "An analysis describes a time that has already happened; a forecast is a model run past the last observation.",
        "OceanPulse stores both, flags them apart, and excludes forecasts from analytics by default.",
        ("forecast", "analysis", "is_forecast"),
    ),
    Term(
        "Masked cell",
        "—",
        "A grid location where a dataset holds no value, usually because the product treats it as land, ice or an enclosed sea.",
        "One product's water is another's land, which is why a port may read its temperature from a cell slightly offshore.",
        ("masked", "mask", "land"),
    ),
    Term(
        "Grid cell / sampled cell",
        "—",
        "One point in the sparse global sampling grid, spaced by equal area rather than equal degrees.",
        "The gaps between cells are not measurements, and the map does not interpolate across them.",
        ("grid", "cell", "sample"),
    ),
    Term(
        "Extensive vs intensive quantity",
        "—",
        "Extensive quantities sum over a window (counts, total energy); intensive ones are averages of a state (temperature, wave height).",
        "It decides gap filling: zero is the true value for an extensive quantity in a quiet window, and a false measurement for an intensive one.",
        ("extensive", "intensive", "gap fill"),
    ),
    Term(
        "Epoch milliseconds (UTC)",
        "ms",
        "Milliseconds since 1970-01-01T00:00:00Z, stored as an integer.",
        "Integer comparison is index-friendly and sidesteps every timezone and string-collation bug.",
        ("epoch", "timestamp", "utc"),
    ),
    Term(
        "LTTB downsampling",
        "—",
        "Largest-Triangle-Three-Buckets: reduces a series to a point budget while preserving its visual shape.",
        "Keeps peaks and troughs that naive every-nth-point sampling would drop.",
        ("lttb", "downsample"),
    ),
    Term(
        "Pearson r",
        "−1…+1",
        "Strength and sign of a linear relationship between two variables.",
        "Computed here on daily means, because the sources do not share a clock and would otherwise never overlap.",
        ("pearson", "correlation", "r"),
    ),
    Term(
        "Parquet",
        "file",
        "A compressed, columnar, typed file format that preserves dtypes and timezone-aware timestamps.",
        "Roughly 40% the size of the equivalent CSV here, and needs no re-parsing on load.",
        ("parquet", "export"),
    ),
    Term(
        "Token bucket / request budget",
        "req",
        "Rate limiting by refilling tokens at a fixed rate, plus a counted daily and hourly quota per provider.",
        "Keeps a self-hosted collector from overloading a free service or getting itself blocked.",
        ("rate limit", "budget", "token bucket", "quota"),
    ),
)


def search_sections(query: str) -> list[Section]:
    needle = (query or "").strip().lower()
    if not needle:
        return list(SECTIONS)
    return [s for s in SECTIONS if needle in s.search_text()]


def search_terms(query: str) -> list[Term]:
    needle = (query or "").strip().lower()
    if not needle:
        return list(GLOSSARY)
    return [t for t in GLOSSARY if needle in t.search_text()]
