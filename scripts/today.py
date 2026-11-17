"""Render the "today in the Official Gazette" block of the profile README.

Usage:
    python3 scripts/today.py                  # today in Mexico City
    python3 scripts/today.py --date 2026-11-13

Fetches the latest edition of the Diario Oficial de la Federación (DOF) and the
FIX and UDIS indicators from the public SIDOF API (Secretaría de Gobernación),
then writes ``assets/today.svg`` and rewrites the block between
``<!-- today:start -->`` and ``<!-- today:end -->`` in README.md and README.es.md.

Standard library only, Python 3.12+. Runs every morning from
``.github/workflows/today.yml``.

Failure policy: if SIDOF cannot be reached or answers garbage, nothing is
written, a warning is printed and the script exits 0, so the README always keeps
the last good card. If only the indicators fail, the card shows "n/d" for them.

Edition rule: "today" is the date in Mexico City (UTC-6, no daylight saving time
since 2022). Weekends and holidays usually have no edition, so the script walks
back day by day (up to ``LOOKBACK_DAYS``) and shows the latest edition with its
date.

Headline rule (see ``pick_headline``): among the notes of that day, skip the
untitled separator rows and the "avisos judiciales y generales", then rank each
note by the kind of instrument its title starts with and keep the first one in
publication order (matutina, vespertina, extraordinaria; then ``orden``):

    0. Decreto, Ley, Código: statutes, reforms and presidential decrees.
    1. Sentencia, Reglamento, Norma Oficial Mexicana.
    2. Acuerdo, Resolución, Lineamientos, Reglas, Disposiciones, Declaratoria,
       Programa, Estatuto, Circular, Criterios, Manual, Políticas, Bases.
    3. Anything else (Aviso, Convenio, Extracto, Oficio...).
    4. Routine daily publications (exchange rate, interest rates, INPC) and
       errata, which the card already shows or which carry no news.

If the edition has nothing but avisos, the card says so and links to the
searchable archive instead of a note.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

API = "https://sidof.segob.gob.mx/dof/sidof"
DOF_SITE = "https://dof.gob.mx"
ARCHIVE_URL = "https://beg-lag.github.io/dof-diario/"
WORKFLOW_URL = "https://github.com/BEG-lag/BEG-lag/blob/main/.github/workflows/today.yml"
USER_AGENT = "BEG-lag-profile/1.0 (+https://github.com/BEG-lag/BEG-lag)"

# Mexico City has used UTC-6 all year round since daylight saving time ended in 2022.
MX_TZ = timezone(timedelta(hours=-6), "CST")
LOOKBACK_DAYS = 10

START, END = "<!-- today:start -->", "<!-- today:end -->"
SVG_PATH = Path("assets/today.svg")
READMES = {"en": Path("README.md"), "es": Path("README.es.md")}

FIX_CODE, UDI_CODE = 158, 159

RETRY_STATUS = {429, 500, 502, 503, 504}
RETRIES = 3
BACKOFF = 1.0
TIMEOUT = 20.0
MIN_INTERVAL = 0.25

# Palette and type, shared with assets/hero.svg and scripts/build_cards.py.
INK2, PAPER, MUTED, BODY = "#11141b", "#eceee6", "#969dab", "#b9bec8"
LIME, AMBER, CYAN = "#cfff45", "#ffb648", "#7fd8ff"
MONO = '"JetBrains Mono",ui-monospace,SFMono-Regular,Menlo,Consolas,monospace'
SANS = 'Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif'
SERIF = '"Cormorant Garamond","Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif'

W, H, PAD = 960, 220, 32
HEADLINE_SIZE = 19
HEADLINE_WIDTH = W - 2 * PAD - 16
ELLIPSIS = "…"

DAYS_ES = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
MONTHS_ES = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)  # fmt: skip
DAYS_EN = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTHS_EN = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)  # fmt: skip

EDITIONS = (
    ("NotasMatutinas", "matutina"),
    ("NotasVespertinas", "vespertina"),
    ("NotasExtraordinarias", "extraordinaria"),
)

# Title prefixes (accent and case insensitive) for the headline tiers.
TIERS: tuple[tuple[str, ...], ...] = (
    ("decreto", "ley ", "codigo"),
    ("sentencia", "reglamento", "norma oficial mexicana"),
    (
        "acuerdo", "resolucion", "lineamientos", "reglas", "disposiciones", "declaratoria",
        "programa", "estatuto", "circular", "criterios", "manual", "politicas", "bases",
    ),
)  # fmt: skip
ROUTINE = (
    "tipo de cambio", "tasa de interes", "tasas de interes", "costo de captacion",
    "costo porcentual", "valor de la unidad de inversion", "indice nacional de precios",
    "encadenamiento de productos", "fe de erratas", "nota aclaratoria", "aclaracion",
)  # fmt: skip
OTHER_TIER = len(TIERS)
ROUTINE_TIER = OTHER_TIER + 1


class FetchError(RuntimeError):
    """SIDOF could not be reached or answered something unusable."""


Fetch = Callable[[str], dict[str, Any]]


# --------------------------------------------------------------------- HTTP

_last_request = 0.0


def http_get_json(path: str) -> dict[str, Any]:
    """GET ``{API}/{path}`` and decode the JSON object, retrying 429, 5xx and timeouts."""
    global _last_request
    url = f"{API}/{path.lstrip('/')}"
    # SIDOF has answered HTTP 406 to a bare "Accept: application/json"; keep the wildcard.
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json, */*;q=0.8"}
    )
    last_error = "unknown error"
    for attempt in range(RETRIES + 1):
        if attempt:
            time.sleep(BACKOFF * 2 ** (attempt - 1))
        wait = _last_request + MIN_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code}"
            if exc.code in RETRY_STATUS:
                continue
            raise FetchError(f"SIDOF answered {last_error} for {url}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            continue
        try:
            payload = json.loads(body)
        except ValueError as exc:
            raise FetchError(f"SIDOF returned invalid JSON for {url}") from exc
        if not isinstance(payload, dict) or payload.get("response", "OK") != "OK":
            raise FetchError(f"Unexpected SIDOF payload for {url}")
        return payload
    raise FetchError(f"SIDOF did not respond ({last_error}) after {RETRIES + 1} attempts: {url}")


# ------------------------------------------------------------------- Models


def today_mx(now: datetime | None = None) -> date:
    """Today's date in Mexico City."""
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return now.astimezone(MX_TZ).date()


def sidof_date(day: date) -> str:
    return day.strftime("%d-%m-%Y")


_INVALID_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ufffe\uffff]")


def clean_text(value: Any) -> str | None:
    """Normalize a SIDOF string: drop "null" markers, control characters and extra spaces.

    U+2014 is turned into a hyphen to follow the profile's style guide.
    """
    if value is None:
        return None
    text = _INVALID_XML.sub(" ", str(value)).replace("\u2014", "-")
    text = " ".join(text.split())
    return None if text in {"", "null", "None"} else text


def _int(value: Any) -> int | None:
    try:
        return int(float(clean_text(value) or ""))
    except ValueError:
        return None


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


@dataclass(frozen=True, slots=True)
class Note:
    code: int
    title: str
    day: date
    edition: str
    branch: str | None
    agency: str | None
    order: float
    is_aviso: bool

    @property
    def url(self) -> str:
        return f"{DOF_SITE}/nota_detalle.php?codigo={self.code}&fecha={self.day:%d/%m/%Y}"

    @property
    def issuer(self) -> str:
        parts = [p for p in (self.branch, self.agency) if p]
        if len(parts) == 2 and parts[0] == parts[1]:
            parts = parts[:1]
        return " · ".join(parts) or "DOF"


@dataclass(frozen=True, slots=True)
class Indicator:
    value: str
    day: date


@dataclass(frozen=True, slots=True)
class Snapshot:
    today: date
    day: date
    notes: tuple[Note, ...]
    headline: Note | None
    fix: Indicator | None
    udi: Indicator | None

    @property
    def is_today(self) -> bool:
        return self.day == self.today

    @property
    def total(self) -> int:
        return len(self.notes)

    @property
    def avisos(self) -> int:
        return sum(n.is_aviso for n in self.notes)

    @property
    def editions(self) -> list[str]:
        return [name for _, name in EDITIONS if any(n.edition == name for n in self.notes)]

    @property
    def link(self) -> str:
        return self.headline.url if self.headline else ARCHIVE_URL


def parse_notes(payload: Mapping[str, Any], day: date) -> list[Note]:
    """Parse ``/notas/{DD-MM-YYYY}`` into notes in publication order, without separators."""
    notes: list[Note] = []
    for key, edition in EDITIONS:
        rows = payload.get(key) or []
        if not isinstance(rows, list):
            raise FetchError(f"Unexpected {key} in the SIDOF payload")
        parsed: list[Note] = []
        for raw in rows:
            if not isinstance(raw, Mapping):
                continue
            title, code = clean_text(raw.get("titulo")), _int(raw.get("codNota"))
            if not title or code is None:
                continue  # one untitled row per issuing agency acts as a section header
            try:
                note_day = datetime.strptime(clean_text(raw.get("fecha")) or "", "%d-%m-%Y").date()
            except ValueError:
                note_day = day
            try:
                order = float(clean_text(raw.get("orden")) or 0)
            except ValueError:
                order = 0.0
            branch_code = clean_text(raw.get("codOrgaUno"))
            parsed.append(
                Note(
                    code=code,
                    title=title,
                    day=note_day,
                    edition=edition,
                    branch=clean_text(raw.get("nombreCodOrgaUno")),
                    agency=clean_text(raw.get("codOrgaDos")),
                    order=order,
                    is_aviso=clean_text(raw.get("tipoNota")) == "AVISO" or branch_code == "AV",
                )
            )
        notes.extend(sorted(parsed, key=lambda n: (n.order, n.code)))
    return notes


def headline_tier(title: str) -> int:
    folded = _fold(title)
    if folded.startswith(ROUTINE):
        return ROUTINE_TIER
    for tier, prefixes in enumerate(TIERS):
        if folded.startswith(prefixes):
            return tier
    return OTHER_TIER


def pick_headline(notes: list[Note] | tuple[Note, ...]) -> Note | None:
    """The most significant non-aviso note, first in publication order within its tier."""
    candidates = [(headline_tier(n.title), i, n) for i, n in enumerate(notes) if not n.is_aviso]
    return min(candidates, key=lambda c: c[:2])[2] if candidates else None


def find_edition(fetch: Fetch, today: date) -> tuple[date, list[Note]]:
    """The latest edition published on or before ``today``."""
    for offset in range(LOOKBACK_DAYS + 1):
        day = today - timedelta(days=offset)
        notes = parse_notes(fetch(f"notas/{sidof_date(day)}"), day)
        if notes:
            return day, notes
    raise FetchError(f"No DOF edition found in the {LOOKBACK_DAYS} days before {today}")


def find_indicators(fetch: Fetch, today: date) -> dict[int, Indicator]:
    """Latest FIX and UDIS published on or before ``today``.

    UDIS appear every calendar day; the FIX only on business days.
    """
    found: dict[int, Indicator] = {}
    for offset in range(LOOKBACK_DAYS + 1):
        day = today - timedelta(days=offset)
        payload = fetch(f"indicadores/{sidof_date(day)}")
        rows = payload.get("ListaIndicadores") or []
        for raw in rows if isinstance(rows, list) else []:
            if not isinstance(raw, Mapping):
                continue
            code = _int(raw.get("codTipoIndicador"))
            value = clean_text(raw.get("valor"))
            if code not in (FIX_CODE, UDI_CODE) or code in found or not value:
                continue
            try:
                float(value.replace(",", ""))
            except ValueError:
                continue
            found[code] = Indicator(value=value, day=day)
        if FIX_CODE in found and UDI_CODE in found:
            break
    return found


def build_snapshot(fetch: Fetch, today: date) -> Snapshot:
    day, notes = find_edition(fetch, today)
    try:
        indicators = find_indicators(fetch, today)
    except FetchError as exc:
        warn(f"Indicators unavailable, the card will show n/d: {exc}")
        indicators = {}
    return Snapshot(
        today=today,
        day=day,
        notes=tuple(notes),
        headline=pick_headline(notes),
        fix=indicators.get(FIX_CODE),
        udi=indicators.get(UDI_CODE),
    )


# ------------------------------------------------------------------- Layout

_NARROW = set("il.,:;'|!()[]{}ÍÌíìïj/")
_SEMI = set('frt-"')
_WIDE = set("mwMW%@")


def text_width(text: str, size: float, *, mono: bool = False) -> float:
    """A deliberately generous width estimate, so text fits in any fallback font."""
    if mono:
        return len(text) * 0.62 * size
    total = 0.0
    for ch in text:
        if ch == " ":
            total += 0.3
        elif ch in _NARROW:
            total += 0.32
        elif ch in _SEMI:
            total += 0.4
        elif ch in _WIDE:
            total += 0.9
        elif ch.isupper():
            total += 0.72
        elif ch.isdigit():
            total += 0.62
        else:
            total += 0.58
    return total * size


def ellipsize(text: str, size: float, max_width: float, *, mono: bool = False) -> str:
    """Cut ``text`` at a word boundary and add an ellipsis so it fits ``max_width``."""
    text = text.rstrip(" ,;:.")
    while text and text_width(text + ELLIPSIS, size, mono=mono) > max_width:
        head, sep, _ = text.rpartition(" ")
        text = (head if sep and head else text[:-1]).rstrip(" ,;:.")
    return text + ELLIPSIS


def fit(text: str, size: float, max_width: float, *, mono: bool = False) -> str:
    if text_width(text, size, mono=mono) <= max_width:
        return text
    return ellipsize(text, size, max_width, mono=mono)


def wrap(text: str, size: float, max_width: float, max_lines: int = 2) -> list[str]:
    """Greedy word wrap by estimated width; the last line ends in an ellipsis if cut."""
    words = text.split()
    lines: list[str] = []
    while words and len(lines) < max_lines:
        line = ""
        while words:
            candidate = f"{line} {words[0]}" if line else words[0]
            if text_width(candidate, size) <= max_width:
                line = candidate
                words.pop(0)
            elif not line:
                # A single word wider than the line: break it by characters.
                cut = len(words[0]) - 1
                while cut > 1 and text_width(words[0][:cut], size) > max_width:
                    cut -= 1
                line, words[0] = words[0][:cut], words[0][cut:]
                break
            else:
                break
        lines.append(line)
    if words:
        lines[-1] = ellipsize(f"{lines[-1]} {words[0]}", size, max_width)
    return lines


def shorten(text: str, limit: int) -> str:
    """Character based shortening for plain text (README line, alt text)."""
    if len(text) <= limit:
        return text
    head = text[: limit - 1]
    if " " in head:
        head = head.rsplit(" ", 1)[0]
    return head.rstrip(" ,;:.") + ELLIPSIS


def long_date_es(day: date) -> str:
    return f"{DAYS_ES[day.weekday()]} {day.day} de {MONTHS_ES[day.month - 1]} de {day.year}"


def long_date_en(day: date) -> str:
    return f"{DAYS_EN[day.weekday()]}, {MONTHS_EN[day.month - 1]} {day.day}, {day.year}"


def editions_label(editions: list[str]) -> str:
    if not editions:
        return "edición"
    if len(editions) == 1:
        return f"edición {editions[0]}"
    return f"ediciones {', '.join(editions[:-1])} y {editions[-1]}"


def _esc(text: str) -> str:
    return escape(text, quote=True)


# ---------------------------------------------------------------------- SVG


def render_svg(s: Snapshot) -> str:
    accent = LIME if s.is_today else AMBER
    title = "Hoy en el DOF" if s.is_today else "Última edición del DOF"
    kicker = title.upper()
    date_text = long_date_es(s.day)
    if s.headline:
        issuer = fit(s.headline.issuer.upper(), 12, W - 2 * PAD, mono=True)
        headline = s.headline.title
    else:
        issuer = "AVISOS JUDICIALES Y GENERALES"
        headline = (
            "Esta edición solo trae avisos judiciales y generales. Consulta el archivo completo."
        )
    lines = wrap(headline, HEADLINE_SIZE, HEADLINE_WIDTH)
    fix = s.fix.value if s.fix else "n/d"
    udi = s.udi.value if s.udi else "n/d"
    fix_label = f"FIX USD · {s.fix.day:%d/%m}" if s.fix else "FIX USD"
    udi_label = f"UDI · {s.udi.day:%d/%m}" if s.udi else "UDI"
    total = f"{s.total:,}"
    desc = (
        f"{title}, {date_text}. {shorten(headline, 300)} "
        f"{total} notas, {s.avisos:,} de ellas avisos. FIX {fix}. UDI {udi}. Fuente: DOF / SIDOF."
    )

    headline_svg = "".join(
        f'<text class="rise r{i}" x="{PAD}" y="{106 + i * 27}" font-family=\'{SANS}\' '
        f'font-size="{HEADLINE_SIZE}" font-weight="600" fill="{PAPER}">{_esc(line)}</text>'
        for i, line in enumerate(lines)
    )

    def stat(x: int, label: str, value: str, color: str, extra: str = "") -> str:
        fill = MUTED if value == "n/d" else color
        tail = ""
        if extra:
            tail = f'<tspan font-size="12" font-weight="400" fill="{MUTED}">{_esc(extra)}</tspan>'
        return (
            f'<text x="{x}" y="178" font-family=\'{MONO}\' font-size="10.5" letter-spacing="1.2" '
            f'fill="{MUTED}">{_esc(label)}</text>'
            f'<text x="{x}" y="203" font-family=\'{MONO}\' font-size="21" font-weight="700" '
            f'fill="{fill}">{_esc(value)}{tail}</text>'
        )

    stats = (
        stat(PAD, "NOTAS", total, accent, f"  {s.avisos:,} avisos")
        + stat(PAD + 230, fix_label, fix, AMBER)
        + stat(PAD + 410, udi_label, udi, CYAN)
    )

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" role="img" aria-labelledby="t d">
  <title id="t">{_esc(title)}: {_esc(date_text)}</title>
  <desc id="d">{_esc(desc)}</desc>
  <style>
    .dot{{animation:pulse 2.6s ease-in-out infinite;transform-box:fill-box;transform-origin:center}}
    .ring{{animation:ring 2.6s ease-out infinite;transform-box:fill-box;transform-origin:center}}
    .sheen{{animation:sheen 7s ease-in-out infinite}}
    .rise{{animation:rise .9s cubic-bezier(.2,.7,.2,1) both}}
    .r1{{animation-delay:.12s}}
    @keyframes pulse{{0%,100%{{opacity:1;transform:scale(1)}}50%{{opacity:.35;transform:scale(.7)}}}}
    @keyframes ring{{0%{{opacity:.55;transform:scale(1)}}100%{{opacity:0;transform:scale(3.2)}}}}
    @keyframes sheen{{0%,70%{{transform:translateX(-200px)}}100%{{transform:translateX({W + 200}px)}}}}
    @keyframes rise{{from{{opacity:0;transform:translateY(6px)}}to{{opacity:1;transform:none}}}}
    @media (prefers-reduced-motion:reduce){{.dot,.ring,.sheen,.rise{{animation:none}}.ring,.sheen{{opacity:0}}}}
  </style>
  <defs>
    <clipPath id="c"><rect width="{W}" height="{H}" rx="18"/></clipPath>
    <linearGradient id="s" x1="0" x2="1"><stop offset="0" stop-color="{accent}" stop-opacity="0"/><stop offset=".5" stop-color="{accent}" stop-opacity=".07"/><stop offset="1" stop-color="{accent}" stop-opacity="0"/></linearGradient>
  </defs>
  <g clip-path="url(#c)">
    <rect width="{W}" height="{H}" fill="{INK2}"/>
    <rect width="{W}" height="3" fill="{accent}"/>
    <rect class="sheen" x="0" y="0" width="180" height="{H}" fill="url(#s)" transform="skewX(-12)"/>
    <circle class="ring" cx="{PAD + 4}" cy="38" r="4" fill="none" stroke="{accent}"/>
    <circle class="dot" cx="{PAD + 4}" cy="38" r="4" fill="{accent}"/>
    <text x="{PAD + 16}" y="42.5" font-family='{MONO}' font-size="12" letter-spacing="1.6" fill="{accent}">{_esc(kicker)}</text>
    <text x="{W - PAD}" y="44" text-anchor="end" font-family='{SERIF}' font-style="italic" font-size="22" fill="{PAPER}"><tspan fill="{accent}">§ </tspan>{_esc(date_text)}</text>
    <text x="{PAD}" y="74" font-family='{MONO}' font-size="12" letter-spacing=".4" fill="{MUTED}">{_esc(issuer)}</text>
    {headline_svg}
    <rect x="{PAD}" y="152" width="{W - 2 * PAD}" height="1" fill="{PAPER}" fill-opacity=".08"/>
    {stats}
    <text x="{W - PAD}" y="178" text-anchor="end" font-family='{MONO}' font-size="10.5" letter-spacing="1.2" fill="{MUTED}">{_esc(editions_label(s.editions).upper())}</text>
    <text x="{W - PAD}" y="202" text-anchor="end" font-family='{MONO}' font-size="12" fill="{MUTED}">fuente: DOF / SIDOF <tspan fill="{accent}">&#8599;</tspan></text>
  </g>
  <rect x=".5" y=".5" width="{W - 1}" height="{H - 1}" rx="18" fill="none" stroke="{PAPER}" stroke-opacity=".12"/>
</svg>
"""


# ------------------------------------------------------------------- README


def readme_block(s: Snapshot, lang: str) -> str:
    fix = s.fix.value if s.fix else "n/d"
    udi = s.udi.value if s.udi else "n/d"
    if lang == "es":
        when = long_date_es(s.day)
        prefix = f"DOF del {when}" if s.is_today else f"Última edición del DOF, {when}"
        if s.headline:
            title = shorten(s.headline.title, 140)
        else:
            title = "solo avisos judiciales y generales"
        alt = f"{prefix}: {shorten(s.headline.title if s.headline else title, 220)} FIX {fix}, UDI {udi}."
        counts = f"{s.total:,} notas"
        archive, updated = "busca en el archivo", "se actualiza cada mañana"
    else:
        when = long_date_en(s.day)
        prefix = f"DOF, {when}" if s.is_today else f"Latest DOF edition, {when}"
        if s.headline:
            title = shorten(s.headline.title, 140)
        else:
            title = "only judicial and general notices"
        alt = (
            f"Mexico's Official Gazette, {when}: "
            f"{shorten(s.headline.title if s.headline else title, 220)} FIX {fix}, UDI {udi}."
        )
        counts = f"{s.total:,} notes"
        archive, updated = "search the archive", "refreshed every morning"

    link = _esc(s.link)
    line = (
        f'{_esc(prefix)}: <a href="{link}">{_esc(title)}</a> · {counts} · FIX {_esc(fix)} · '
        f'UDI {_esc(udi)} · <a href="{ARCHIVE_URL}">{archive}</a> · '
        f'<a href="{WORKFLOW_URL}">{updated}</a>'
    )
    return (
        "<p>\n"
        f'  <a href="{link}"><img src="{SVG_PATH.as_posix()}" width="100%" alt="{_esc(alt)}"></a>\n'
        "</p>\n"
        "\n"
        f"<sub>{line}</sub>"
    )


_BLOCK = re.compile(re.escape(START) + r".*?" + re.escape(END), re.DOTALL)


def replace_block(text: str, block: str) -> str | None:
    """Swap the content between the markers; ``None`` if the markers are missing."""
    if not _BLOCK.search(text):
        return None
    return _BLOCK.sub(lambda _: f"{START}\n{block}\n{END}", text, count=1)


# --------------------------------------------------------------------- Main


def warn(message: str) -> None:
    prefix = "::warning::" if os.environ.get("GITHUB_ACTIONS") else "warning: "
    print(f"{prefix}{message}", file=sys.stderr)


def _write(path: Path, content: str) -> bool:
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)
    return True


def write_outputs(s: Snapshot, root: Path) -> list[Path]:
    """Render everything first, then write only the files whose content changed."""
    outputs: dict[Path, str] = {root / SVG_PATH: render_svg(s)}
    for lang, rel in READMES.items():
        path = root / rel
        if not path.exists():
            warn(f"{rel} not found; skipped")
            continue
        new = replace_block(path.read_text(encoding="utf-8"), readme_block(s, lang))
        if new is None:
            warn(f"{rel} has no {START} ... {END} markers; skipped")
            continue
        outputs[path] = new
    return [path for path, content in outputs.items() if _write(path, content)]


def summary(s: Snapshot) -> str:
    headline = f"{s.headline.code} {shorten(s.headline.title, 90)}" if s.headline else "only avisos"
    fix = f"{s.fix.value} ({s.fix.day:%d/%m})" if s.fix else "n/d"
    udi = f"{s.udi.value} ({s.udi.day:%d/%m})" if s.udi else "n/d"
    return (
        f"DOF {s.day:%d/%m/%Y} ({', '.join(s.editions)}): {s.total} notes, {s.avisos} avisos\n"
        f"headline: {headline}\nFIX {fix} | UDI {udi}"
    )


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        help="reference date YYYY-MM-DD (default: today in Mexico City)",
    )
    return parser.parse_args(argv)


def main(
    argv: list[str] | None = None,
    *,
    fetch: Fetch = http_get_json,
    now: datetime | None = None,
    root: Path = ROOT,
) -> int:
    args = parse_args(argv)
    today = args.date or today_mx(now)
    try:
        snapshot = build_snapshot(fetch, today)
    except FetchError as exc:
        warn(f"SIDOF unavailable, keeping the previous card: {exc}")
        return 0
    changed = write_outputs(snapshot, root)
    print(summary(snapshot))
    print("changed: " + (", ".join(p.relative_to(root).as_posix() for p in changed) or "nothing"))
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a", encoding="utf-8") as fh:
            fh.write(f"edition={snapshot.day.isoformat()}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
