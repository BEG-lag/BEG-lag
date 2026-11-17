"""Generate the project cards shown in the profile README.

Usage: python3 scripts/build_cards.py
Reads scripts/projects.json and writes assets/cards/<slug>.svg.
No dependencies beyond the standard library.
"""

from __future__ import annotations

import json
import textwrap
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
W, H = 480, 250
PAD = 28
INK2, PAPER, MUTED, BODY = "#11141b", "#eceee6", "#969dab", "#b9bec8"

MONO = '"JetBrains Mono",ui-monospace,SFMono-Regular,Menlo,Consolas,monospace'
SANS = 'Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif'


def chip(x: float, label: str, accent: str) -> tuple[str, float]:
    width = len(label) * 7.2 + 22
    svg = (
        f'<g transform="translate({x:.1f} {H - PAD - 24})">'
        f'<rect width="{width:.1f}" height="24" rx="12" fill="none" stroke="{accent}" stroke-opacity=".45"/>'
        f'<text x="{width / 2:.1f}" y="16" text-anchor="middle" font-family=\'{MONO}\' '
        f'font-size="11.5" fill="{PAPER}">{escape(label)}</text></g>'
    )
    return svg, width


def card(p: dict) -> str:
    accent = p["accent"]
    lines = textwrap.wrap(p["desc"], width=54)[:3]
    desc = "".join(
        f'<text x="{PAD}" y="{124 + i * 22}" font-family=\'{SANS}\' font-size="14.5" fill="{BODY}">'
        f"{escape(line)}</text>"
        for i, line in enumerate(lines)
    )
    chips, x = [], PAD
    for tag in p["tags"]:
        svg, width = chip(x, tag, accent)
        chips.append(svg)
        x += width + 8

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" role="img" aria-label="{escape(p['title'])}: {escape(p['desc'])}">
  <style>
    .dot{{animation:pulse 2.6s ease-in-out infinite;transform-box:fill-box;transform-origin:center}}
    .sheen{{animation:sheen 6s ease-in-out infinite}}
    @keyframes pulse{{0%,100%{{opacity:1;transform:scale(1)}}50%{{opacity:.35;transform:scale(.7)}}}}
    @keyframes sheen{{0%,70%{{transform:translateX(-160px)}}100%{{transform:translateX({W + 160}px)}}}}
    @media (prefers-reduced-motion:reduce){{.dot,.sheen{{animation:none}}.sheen{{opacity:0}}}}
  </style>
  <defs>
    <clipPath id="c"><rect width="{W}" height="{H}" rx="18"/></clipPath>
    <linearGradient id="s" x1="0" x2="1"><stop offset="0" stop-color="{accent}" stop-opacity="0"/><stop offset=".5" stop-color="{accent}" stop-opacity=".08"/><stop offset="1" stop-color="{accent}" stop-opacity="0"/></linearGradient>
  </defs>
  <g clip-path="url(#c)">
    <rect width="{W}" height="{H}" fill="{INK2}"/>
    <rect width="{W}" height="3" fill="{accent}"/>
    <rect class="sheen" x="0" y="0" width="140" height="{H}" fill="url(#s)" transform="skewX(-12)"/>
    <circle class="dot" cx="{PAD + 4}" cy="40" r="4" fill="{accent}"/>
    <text x="{PAD + 16}" y="44.5" font-family='{MONO}' font-size="12" letter-spacing="1.6" fill="{accent}">{escape(p['kicker'].upper())}</text>
    <text x="{W - PAD}" y="45" text-anchor="end" font-family='{MONO}' font-size="16" fill="{MUTED}">&#8599;</text>
    <text x="{PAD}" y="88" font-family='{MONO}' font-size="27" font-weight="700" fill="{PAPER}">{escape(p['title'])}</text>
    {desc}
    {''.join(chips)}
  </g>
  <rect x=".5" y=".5" width="{W - 1}" height="{H - 1}" rx="18" fill="none" stroke="{PAPER}" stroke-opacity=".12"/>
</svg>
"""


def main() -> None:
    projects = json.loads((ROOT / "scripts" / "projects.json").read_text(encoding="utf-8"))
    out = ROOT / "assets" / "cards"
    out.mkdir(parents=True, exist_ok=True)
    for p in projects:
        (out / f"{p['slug']}.svg").write_text(card(p), encoding="utf-8")
        print(f"assets/cards/{p['slug']}.svg")


if __name__ == "__main__":
    main()
