"""Tests for scripts/today.py, offline, against SIDOF responses recorded on 2026-11-17."""

from __future__ import annotations

import io
import urllib.error
import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import today

SVG_NS = "{http://www.w3.org/2000/svg}"
TUESDAY = date(2026, 11, 17)  # regular weekday edition
HOLIDAY = date(2026, 11, 16)  # Monday, Revolution Day: no edition, nor on the 14th and 15th


def svg_texts(svg: str) -> list[str]:
    root = ET.fromstring(svg)
    return ["".join(el.itertext()) for el in root.iter(f"{SVG_NS}text")]


def note(title: str, *, code: int = 1, aviso: bool = False, order: float = 1.0) -> today.Note:
    return today.Note(
        code=code,
        title=title,
        day=TUESDAY,
        edition="matutina",
        branch="PODER EJECUTIVO",
        agency="SECRETARIA DE ECONOMIA",
        order=order,
        is_aviso=aviso,
    )


def payload(*rows: dict) -> dict:
    return {"messageCode": 200, "response": "OK", "NotasMatutinas": list(rows)}


# ------------------------------------------------------------ normal weekday


def test_weekday_edition(recorded):
    s = today.build_snapshot(recorded, TUESDAY)

    assert s.day == TUESDAY and s.is_today
    assert s.total == 204 and s.avisos == 186
    assert s.editions == ["matutina"]
    # No Decreto that day, so the SCJN Sentencia (tier 1) beats the earlier Acuerdos.
    assert s.headline.code == 5803873
    assert s.headline.title.startswith("Sentencia dictada por el Tribunal Pleno")
    assert s.headline.url == "https://dof.gob.mx/nota_detalle.php?codigo=5803873&fecha=17/11/2026"
    assert (s.fix.value, s.fix.day) == ("18.5525", TUESDAY)
    assert (s.udi.value, s.udi.day) == ("8.913896", TUESDAY)
    assert recorded.calls == ["notas/17-11-2026", "indicadores/17-11-2026"]


def test_weekday_card_and_readme(recorded, site):
    assert today.main(["--date", "2026-11-17"], fetch=recorded, root=site) == 0

    svg = (site / "assets" / "today.svg").read_text(encoding="utf-8")
    texts = svg_texts(svg)
    assert "HOY EN EL DOF" in texts
    assert "§ martes 17 de noviembre de 2026" in texts
    assert "204  186 avisos" in texts
    assert "18.5525" in texts and "8.913896" in texts
    assert "FIX USD · 17/11" in texts and "UDI · 17/11" in texts
    assert any(t.startswith("fuente: DOF / SIDOF") for t in texts)
    assert "prefers-reduced-motion" in svg

    en = (site / "README.md").read_text(encoding="utf-8")
    es = (site / "README.es.md").read_text(encoding="utf-8")
    official = "https://dof.gob.mx/nota_detalle.php?codigo=5803873&amp;fecha=17/11/2026"
    for text in (en, es):
        assert "old block" not in text
        assert text.count(today.START) == 1 and text.count(today.END) == 1
        assert f'<a href="{official}"><img src="assets/today.svg"' in text
        assert "https://beg-lag.github.io/dof-diario/" in text
        assert text.endswith("## Law, compiled\n")
    assert "DOF, Tuesday, November 17, 2026: <a href" in en
    assert "DOF del martes 17 de noviembre de 2026: <a href" in es


def test_second_run_changes_nothing(recorded, site, capsys):
    today.main(["--date", "2026-11-17"], fetch=recorded, root=site)
    capsys.readouterr()
    today.main(["--date", "2026-11-17"], fetch=recorded, root=site)
    assert "changed: nothing" in capsys.readouterr().out


def test_today_is_mexico_city_date():
    # 02:30 UTC on the 18th is still 20:30 on the 17th in Mexico City (UTC-6).
    assert today.today_mx(datetime(2026, 11, 18, 2, 30, tzinfo=UTC)) == TUESDAY
    assert today.today_mx(datetime(2026, 11, 18, 6, 0, tzinfo=UTC)) == date(2026, 11, 18)


# ----------------------------------------------------------- weekend fallback


def test_holiday_falls_back_to_latest_edition(recorded):
    s = today.build_snapshot(recorded, HOLIDAY)

    assert s.day == date(2026, 11, 13) and not s.is_today
    assert recorded.calls[:4] == [
        "notas/16-11-2026",
        "notas/15-11-2026",
        "notas/14-11-2026",
        "notas/13-11-2026",
    ]
    assert s.editions == ["matutina", "vespertina"]
    assert s.total == 66 and s.avisos == 44
    assert s.headline.code == 5803757  # Decreto: Ley de Ingresos de la Federación 2027
    # UDIS exist for every calendar day; the FIX comes from the last business day.
    assert (s.udi.value, s.udi.day) == ("8.911577", HOLIDAY)
    assert (s.fix.value, s.fix.day) == ("18.6188", date(2026, 11, 13))


def test_holiday_card_says_which_edition(recorded, site):
    today.main(["--date", "2026-11-16"], fetch=recorded, root=site)

    texts = svg_texts((site / "assets" / "today.svg").read_text(encoding="utf-8"))
    assert "ÚLTIMA EDICIÓN DEL DOF" in texts
    assert "§ viernes 13 de noviembre de 2026" in texts
    assert "FIX USD · 13/11" in texts and "UDI · 16/11" in texts
    assert "EDICIONES MATUTINA Y VESPERTINA" in texts
    en = (site / "README.md").read_text(encoding="utf-8")
    es = (site / "README.es.md").read_text(encoding="utf-8")
    assert "Latest DOF edition, Friday, November 13, 2026" in en
    assert "Última edición del DOF, viernes 13 de noviembre de 2026" in es
    assert "codigo=5803757&amp;fecha=13/11/2026" in en


# ---------------------------------------------------------------- API failure


def test_api_failure_keeps_previous_card(site, capsys):
    before = {p: p.read_bytes() for p in site.rglob("*") if p.is_file()}

    def down(path: str) -> dict:
        raise today.FetchError("SIDOF did not respond (HTTP 503) after 4 attempts")

    assert today.main([], fetch=down, root=site) == 0
    assert {p: p.read_bytes() for p in site.rglob("*") if p.is_file()} == before
    assert "keeping the previous card" in capsys.readouterr().err


def test_no_edition_in_lookback_keeps_previous_card(site, capsys):
    empty = {"messageCode": 200, "response": "OK", "NotasMatutinas": [], "ListaIndicadores": []}
    before = (site / "README.md").read_text(encoding="utf-8")

    assert today.main(["--date", "2026-11-17"], fetch=lambda path: empty, root=site) == 0
    assert (site / "README.md").read_text(encoding="utf-8") == before
    assert "No DOF edition found" in capsys.readouterr().err


def test_indicator_failure_shows_nd(recorded, site):
    def notes_only(path: str) -> dict:
        if path.startswith("indicadores"):
            raise today.FetchError("HTTP 500")
        return recorded(path)

    today.main(["--date", "2026-11-17"], fetch=notes_only, root=site)
    texts = svg_texts((site / "assets" / "today.svg").read_text(encoding="utf-8"))
    assert texts.count("n/d") == 2
    assert "FIX n/d" in (site / "README.md").read_text(encoding="utf-8")


def test_http_client_retries_then_raises(monkeypatch):
    calls = []

    def fail(request, timeout):
        calls.append(request.full_url)
        raise urllib.error.HTTPError(request.full_url, 500, "Internal Server Error", {}, None)

    monkeypatch.setattr(today.urllib.request, "urlopen", fail)
    monkeypatch.setattr(today.time, "sleep", lambda seconds: None)
    with pytest.raises(today.FetchError, match="HTTP 500"):
        today.http_get_json("notas/17-11-2026")
    assert len(calls) == today.RETRIES + 1


def test_http_client_sends_wildcard_accept(monkeypatch):
    seen = {}

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def ok(request, timeout):
        seen.update(request.headers)
        return Response(b'{"messageCode": 200, "response": "OK", "ListaIndicadores": []}')

    monkeypatch.setattr(today.urllib.request, "urlopen", ok)
    assert today.http_get_json("indicadores/17-11-2026")["ListaIndicadores"] == []
    assert "*/*" in seen["Accept"]  # a bare application/json has been answered with HTTP 406


def test_invalid_json_is_a_fetch_error(monkeypatch):
    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(today.urllib.request, "urlopen", lambda r, timeout: Response(b"<html>"))
    with pytest.raises(today.FetchError, match="invalid JSON"):
        today.http_get_json("notas/17-11-2026")


# ----------------------------------------------------- long headline wrapping


def test_long_headline_wraps_to_two_lines_with_ellipsis():
    title = "Decreto por el que se reforman, adicionan y derogan diversas disposiciones " * 8
    lines = today.wrap(title.strip(), today.HEADLINE_SIZE, today.HEADLINE_WIDTH)

    assert len(lines) == 2
    assert lines[-1].endswith("…") and not lines[0].endswith("…")
    assert not lines[-1].endswith(" …") and not lines[-1].endswith(",…")
    for line in lines:
        assert today.text_width(line, today.HEADLINE_SIZE) <= today.HEADLINE_WIDTH


def test_short_headline_is_one_line_without_ellipsis():
    assert today.wrap("Acuerdo breve.", 19, 800) == ["Acuerdo breve."]


def test_unbreakable_word_is_cut():
    lines = today.wrap("X" * 400, 19, 300)
    assert len(lines) == 2 and lines[1].endswith("…")
    assert all(today.text_width(line, 19) <= 300 for line in lines)


def test_real_long_title_fits_the_card(recorded, site):
    today.main(["--date", "2026-11-17"], fetch=recorded, root=site)
    svg = (site / "assets" / "today.svg").read_text(encoding="utf-8")
    headline = [
        el for el in ET.fromstring(svg).iter(f"{SVG_NS}text") if "rise" in el.get("class", "")
    ]
    assert len(headline) == 2
    assert headline[0].text.startswith("Sentencia dictada por el Tribunal Pleno")
    assert headline[1].text.endswith("Lenia Batres Guadarrama.") or headline[1].text.endswith("…")


# --------------------------------------------------------------- XML escaping


def test_xml_and_html_escaping(site):
    nasty = "Acuerdo <b>\"Uno\" & 'Dos'</b>\x07 \u2014 fin"
    rows = [
        {"codNota": 9, "titulo": nasty, "fecha": "17-11-2026", "codOrgaUno": "PE",
         "nombreCodOrgaUno": "PODER EJECUTIVO", "codOrgaDos": "A & B <C>", "orden": 1.0},
    ]  # fmt: skip
    indicators = {
        "ListaIndicadores": [{"codTipoIndicador": 158, "valor": "18.0", "fecha": "17-11-2026"}]
    }

    def fetch(path: str) -> dict:
        return payload(*rows) if path.startswith("notas") else indicators

    today.main(["--date", "2026-11-17"], fetch=fetch, root=site)
    svg = (site / "assets" / "today.svg").read_text(encoding="utf-8")
    texts = svg_texts(svg)  # raises if the SVG is not well formed

    assert "Acuerdo <b>\"Uno\" & 'Dos'</b> - fin" in texts
    assert "PODER EJECUTIVO · A & B <C>" in texts
    assert "<b>" not in svg and "\x07" not in svg and "\u2014" not in svg
    readme = (site / "README.md").read_text(encoding="utf-8")
    block = readme.split(today.START)[1].split(today.END)[0]
    assert "<b>" not in block and "&lt;b&gt;&quot;Uno&quot; &amp;" in block
    assert "\u2014" not in readme


# --------------------------------------------------------------- headline rule


def test_decreto_beats_earlier_acuerdo():
    notes = [note("Acuerdo por el que...", code=1), note("Decreto por el que...", code=2, order=2)]
    assert today.pick_headline(notes).code == 2


def test_avisos_and_routine_notes_rank_last():
    notes = [
        note("Aviso REF:581596", code=1, aviso=True),
        note("Tipo de cambio para solventar obligaciones...", code=2),
        note("Convenio de Coordinación...", code=3),
    ]
    assert today.pick_headline(notes).code == 3
    assert today.pick_headline(notes[:2]).code == 2
    assert today.pick_headline(notes[:1]) is None


def test_tier_matching_ignores_case_and_accents():
    assert today.headline_tier("DECRETO por el que se reforma") == 0
    assert today.headline_tier("Resolución final de la investigación") == 2
    assert today.headline_tier("Índice nacional de precios al consumidor") == today.ROUTINE_TIER
    assert today.headline_tier("Leyenda") == today.OTHER_TIER


def test_edition_with_only_avisos(site):
    rows = [{"codNota": 1, "titulo": "Aviso REF:1", "tipoNota": "AVISO", "codOrgaUno": "AV"}]

    def fetch(path: str) -> dict:
        return payload(*rows) if path.startswith("notas") else {"ListaIndicadores": []}

    today.main(["--date", "2026-11-17"], fetch=fetch, root=site)
    texts = svg_texts((site / "assets" / "today.svg").read_text(encoding="utf-8"))
    assert any(t.startswith("Esta edición solo trae avisos") for t in texts)
    assert '<a href="https://beg-lag.github.io/dof-diario/"><img' in (site / "README.md").read_text(
        encoding="utf-8"
    )


# ------------------------------------------------------------- README markers


def test_missing_markers_leave_readme_untouched(recorded, site, capsys):
    (site / "README.es.md").write_text("# Sin marcadores\n", encoding="utf-8")
    today.main(["--date", "2026-11-17"], fetch=recorded, root=site)

    assert (site / "README.es.md").read_text(encoding="utf-8") == "# Sin marcadores\n"
    assert "no <!-- today:start -->" in capsys.readouterr().err
    assert "DOF, Tuesday" in (site / "README.md").read_text(encoding="utf-8")


def test_replace_block_is_idempotent():
    text = f"a\n{today.START}\nx\n{today.END}\nb\n"
    once = today.replace_block(text, "new")
    assert once == f"a\n{today.START}\nnew\n{today.END}\nb\n"
    assert today.replace_block(once, "new") == once
    assert today.replace_block("no markers", "new") is None


def test_repository_readmes_have_markers():
    root = Path(__file__).resolve().parent.parent
    for name in ("README.md", "README.es.md"):
        text = (root / name).read_text(encoding="utf-8")
        assert text.count(today.START) == 1 and text.count(today.END) == 1
