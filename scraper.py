#!/usr/bin/env python3
"""
Norsk AI-medieskraper — samler og klassifiserer norske medieartikler om KI.

Verktøyet henter artikler fra norske medier via RSS og Google News,
filtrerer AI-relaterte saker, klassifiserer dem etter innramming,
og produserer analyse av kategorifordelingen.

Bruk: python scraper.py [--verbose] [--maks N]
"""

import argparse
import csv
import io
import json
import os
import re
import sys
import time
import random
from dataclasses import dataclass, field, asdict
from datetime import datetime
from difflib import SequenceMatcher
from urllib.parse import urlparse, urlencode, quote_plus

import html as html_module
from email.utils import parsedate_to_datetime

import feedparser
import requests

# Sørg for UTF-8 output på Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ============================================================================
# KONSTANTER
# ============================================================================

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTATER_DIR = os.path.join(SCRIPT_DIR, "resultater")

KATEGORIER = {
    "A": "Business / produktivitet / hype",
    "B": "Regulering / juss / compliance",
    "C": "Arbeidsmarked / automatisering",
    "D": "Geopolitikk / makt / demokrati",
    "E": "Samfunn / kultur / eksistensiell refleksjon",
    "F": "Utdanning / forskning",
    "G": "Annet",
}

KATEGORI_BESKRIVELSER = {
    "A": "AI som verktøy, effektivisering, investeringer, startups, datasentre, implementering",
    "B": "EU AI Act, KI-forordningen, personvern, GDPR, juridiske rammeverk",
    "C": "AI erstatter jobber, automatisering av yrker, omstilling, kompetansebehov, arbeidsløshet",
    "D": "USA vs Kina, Big Tech maktkonsentrasjon, digital suverenitet, forsvar, demokrati",
    "E": "Hvordan AI påvirker kultur, ytringsfrihet, polarisering, desinformasjon, AGI-risiko",
    "F": "Skoler, universiteter, juks, KI-kompetanse, akademisk forskning",
    "G": "Passer ikke i andre kategorier",
}

# Søkeord for å filtrere AI-relaterte artikler.
# Mønstre med \b krever ordgrense for å unngå falske treff.
AI_SOKEORD = [
    r"kunstig intelligens",
    r"\bKI\b",
    r"\bAI\b",
    r"ChatGPT",
    r"OpenAI",
    r"Anthropic",
    r"\bClaude\b",
    r"\bGPT\b",
    r"maskinlæring",
    r"machine learning",
    r"deepfake",
    r"chatbot",
    r"algoritme",
    r"generativ",
    r"språkmodell",
    r"\bLLM\b",
    r"copilot",
    r"automatisering",
    r"robotisering",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

# Timeout for HTTP-forespørsler (sekunder)
REQUEST_TIMEOUT = 15


def _google_news_url(query: str, after: str = "", before: str = "") -> str:
    """Bygger Google News RSS-URL med norsk lokale og valgfritt tidsvindu."""
    full_query = query
    if after:
        full_query += f" after:{after}"
    if before:
        full_query += f" before:{before}"
    return (
        f"https://news.google.com/rss/search?"
        f"q={quote_plus(full_query)}&hl=no&gl=NO&ceid=NO:no"
    )


# Tidsvinduer for Google News-søk (3 år tilbake, årlige vinduer)
TIDSVINDUER = [
    ("2023-03-14", "2024-03-14"),
    ("2024-03-14", "2025-03-14"),
    ("2025-03-14", "2026-03-14"),
]

# Google News base-søk som kjøres per tidsvindu
_GOOGLE_NEWS_SOEK = [
    # Site-spesifikke (meningsstoff uten RSS)
    ("NRK Ytring (via Google)", 'site:nrk.no/ytring AI OR KI OR "kunstig intelligens"', "meningsstoff"),
    ("Dagbladet Meninger (via Google)", 'site:dagbladet.no/meninger AI OR KI OR "kunstig intelligens"', "meningsstoff"),
    ("Aftenposten Meninger (via Google)", 'site:aftenposten.no/meninger AI OR KI OR "kunstig intelligens"', "meningsstoff"),
    ("Morgenbladet (via Google)", 'site:morgenbladet.no AI OR KI OR "kunstig intelligens"', "meningsstoff"),
    ("Klassekampen (via Google)", 'site:klassekampen.no AI OR KI OR "kunstig intelligens"', "meningsstoff"),
    ("DN Debatt (via Google)", 'site:dn.no AI OR KI OR "kunstig intelligens"', "meningsstoff"),
    ("Minerva (via Google)", 'site:minervanett.no AI OR KI OR "kunstig intelligens"', "meningsstoff"),
    # Site-spesifikke (historisk dekning for direkte RSS-kilder)
    ("NRK (via Google)", 'site:nrk.no AI OR KI OR "kunstig intelligens"', "nyheter"),
    ("Aftenposten (via Google)", 'site:aftenposten.no AI OR KI OR "kunstig intelligens"', "nyheter"),
    ("VG (via Google)", 'site:vg.no AI OR KI OR "kunstig intelligens"', "nyheter"),
    ("Dagbladet (via Google)", 'site:dagbladet.no AI OR KI OR "kunstig intelligens"', "nyheter"),
    ("E24 (via Google)", 'site:e24.no AI OR KI OR "kunstig intelligens"', "nyheter"),
    # Generelle søk (balansert)
    ("Google News: kunstig intelligens", "kunstig intelligens", "aggregert"),
    ("Google News: AI Norge", "AI Norge", "aggregert"),
    ("Google News: AI demokrati Norge", "AI demokrati Norge", "aggregert"),
    ("Google News: AI makt Norge", "AI makt Norge", "aggregert"),
    ("Google News: KI Norge", "KI Norge", "aggregert"),
    ("Google News: KI demokrati Norge", "KI demokrati Norge", "aggregert"),
    ("Google News: KI makt Norge", "KI makt Norge", "aggregert"),
]


def _bygg_google_news_kilder() -> dict:
    """Genererer Google News-kilder med tidsvinduer (3 år, årlige vinduer)."""
    kilder = {}
    for navn, soek, kilde_type in _GOOGLE_NEWS_SOEK:
        for after, before in TIDSVINDUER:
            aar = after[:4]
            neste_aar = before[:4]
            vindu_navn = f"{navn} {aar}-{neste_aar}"
            kilder[vindu_navn] = {
                "url": _google_news_url(soek, after=after, before=before),
                "type": kilde_type,
            }
    return kilder


# Alle kilder vi henter fra.
# type: "meningsstoff" (prioritert), "nyheter", eller "aggregert"
KILDER = {
    # --- Direkte RSS-feeds (kun nåværende artikler) ---
    "Aftenposten Meninger": {
        "url": "https://www.aftenposten.no/rss/meninger",
        "type": "meningsstoff",
    },
    "NRK": {
        "url": "https://www.nrk.no/toppsaker.rss",
        "type": "nyheter",
    },
    "NRK Siste": {
        "url": "https://www.nrk.no/nyheter/siste.rss",
        "type": "nyheter",
    },
    "VG": {
        "url": "https://www.vg.no/rss/feed/",
        "type": "nyheter",
    },
    "Aftenposten": {
        "url": "https://www.aftenposten.no/rss",
        "type": "nyheter",
    },
    "Dagbladet": {
        "url": "https://www.dagbladet.no/rss/nyheter",
        "type": "nyheter",
    },
    "E24": {
        "url": "https://e24.no/rss",
        "type": "nyheter",
    },
    # --- Google News med tidsvinduer (generert) ---
    **_bygg_google_news_kilder(),
}

# ============================================================================
# DATAMODELL
# ============================================================================


@dataclass
class Artikkel:
    """Representerer én mediartikkel."""

    tittel: str
    url: str
    kilde: str
    dato: str  # ISO 8601 eller tom streng
    sammendrag: str
    artikkeltekst: str = ""  # Utdrag av artikkelteksten (første ~1500 tegn)
    vinkling: str = ""  # Én setning: artiklens vinkling/innramming (fra Claude)
    kategori: str = ""
    kategori_begrunnelse: str = ""
    er_meningsstoff: bool = False
    sokeord_treff: list = field(default_factory=list)


# ============================================================================
# HENTING AV ARTIKLER
# ============================================================================


def _rens_html(tekst: str) -> str:
    """Fjerner HTML-tagger og dekoder HTML-entiteter fra tekst."""
    if not tekst:
        return ""
    tekst = re.sub(r"<[^>]+>", " ", tekst)
    tekst = html_module.unescape(tekst)
    tekst = re.sub(r"\s+", " ", tekst)
    return tekst.strip()


def _parse_dato(entry) -> str:
    """Prøver å hente publiseringsdato fra RSS-entry.

    Returnerer alltid ISO 8601-format (YYYY-MM-DDTHH:MM:SS) eller tom streng.
    """
    for felt in ("published", "updated", "created"):
        verdi = entry.get(felt, "")
        if verdi:
            # Strategi 1: feedparsers struct_time
            parsed = entry.get(f"{felt}_parsed")
            if parsed:
                try:
                    return datetime(*parsed[:6]).isoformat()
                except (ValueError, TypeError):
                    pass
            # Strategi 2: RFC 2822-datoer (vanlig i RSS)
            try:
                return parsedate_to_datetime(verdi).isoformat()
            except (ValueError, TypeError):
                pass
    return ""


def hent_rss_artikler(kilde_navn: str, url: str, verbose: bool = False) -> list[Artikkel]:
    """Henter og parser artikler fra én RSS-feed."""
    er_meningsstoff = KILDER.get(kilde_navn, {}).get("type") == "meningsstoff"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  ADVARSEL: Kunne ikke hente fra {kilde_navn}: {e}")
        return []

    feed = feedparser.parse(resp.content)

    if feed.bozo and not feed.entries:
        print(f"  ADVARSEL: Ugyldig RSS fra {kilde_navn}: {feed.bozo_exception}")
        return []

    artikler = []
    for entry in feed.entries:
        tittel = _rens_html(entry.get("title", "")).strip() # type: ignore
        if not tittel:
            continue

        lenke = entry.get("link", "")
        sammendrag = _rens_html(entry.get("summary", entry.get("description", ""))) # type: ignore
        dato = _parse_dato(entry)

        # For Google News: prøv å hente den faktiske kilden
        faktisk_kilde = kilde_navn
        source = entry.get("source", {})
        if source and hasattr(source, "get"):
            faktisk_kilde = source.get("title", kilde_navn)

        artikler.append(
            Artikkel(
                tittel=tittel,
                url=lenke, # type: ignore
                kilde=faktisk_kilde,
                dato=dato,
                sammendrag=sammendrag[:500],  # Begrens lengde
                er_meningsstoff=er_meningsstoff,
            )
        )

    if verbose and feed.bozo:
        print(f"  INFO: {kilde_navn} hadde parsing-problemer, men {len(artikler)} artikler hentet")

    return artikler


def hent_alle_artikler(verbose: bool = False) -> list[Artikkel]:
    """Henter artikler fra alle konfigurerte kilder."""
    alle = []
    print("\n--- Henter artikler ---")

    for navn, info in KILDER.items():
        artikler = hent_rss_artikler(navn, info["url"], verbose)
        antall = len(artikler)
        if antall > 0:
            print(f"  {navn}: {antall} artikler")
        elif verbose:
            print(f"  {navn}: 0 artikler")

        alle.extend(artikler)

        # Vent mellom forespørsler for å være snill mot serverne
        time.sleep(random.uniform(0.3, 0.5))

    print(f"\nTotalt hentet: {len(alle)} artikler fra {len(KILDER)} kilder")
    return alle


# ============================================================================
# HENTING AV ARTIKKELTEKST
# ============================================================================


def _ekstraher_div_innhold(html_tekst: str, klasse: str) -> str:
    """Ekstraherer innholdet av en div med gitt klasse, håndterer nesting korrekt."""
    # Finn åpnings-taggen
    pattern = rf'<div[^>]*class="[^"]*{re.escape(klasse)}[^"]*"[^>]*>'
    match = re.search(pattern, html_tekst, re.IGNORECASE)
    if not match:
        return ""

    # Tell div-nesting for å finne riktig lukke-tag
    pos = match.end()
    dybde = 1
    while dybde > 0 and pos < len(html_tekst):
        neste_aapne = re.search(r"<div[\s>]", html_tekst[pos:], re.IGNORECASE)
        neste_lukke = re.search(r"</div>", html_tekst[pos:], re.IGNORECASE)

        if not neste_lukke:
            break

        if neste_aapne and neste_aapne.start() < neste_lukke.start():
            dybde += 1
            pos += neste_aapne.end()
        else:
            dybde -= 1
            if dybde == 0:
                return html_tekst[match.end() : pos + neste_lukke.start()]
            pos += neste_lukke.end()

    return ""


def _ekstraher_tekst_fra_html(html_innhold: str) -> str:
    """Ekstraherer brødtekst fra HTML ved å finne artikkelinnhold."""
    # Fjern script, style, nav, header, footer, aside og deres innhold
    tekst = re.sub(
        r"<(script|style|nav|header|footer|aside|menu|noscript)[^>]*>.*?</\1>",
        "", html_innhold, flags=re.DOTALL | re.IGNORECASE
    )

    # Strategi 1: Finn <article>-innhold
    article_match = re.search(r"<article[^>]*>(.*?)</article>", tekst, re.DOTALL | re.IGNORECASE)
    kandidat_tekst = article_match.group(1) if article_match else tekst

    # Strategi 2: Finn div med artikkel-lignende klasser
    if not article_match:
        for klasse in ["article-body", "article__body", "story-body", "entry-content",
                        "post-content", "article-content", "rich-text", "body-text"]:
            kandidat_tekst = _ekstraher_div_innhold(tekst, klasse)
            if kandidat_tekst:
                break

    # Hent tekst fra <p>-tagger (bare de med substansiell tekst)
    paragraphs = re.findall(r"<p[^>]*>(.*?)</p>", kandidat_tekst, re.DOTALL | re.IGNORECASE)
    if paragraphs:
        # Filtrer ut korte paragraphs (navigasjon, knapper etc.)
        lange_paragraphs = [_rens_html(p) for p in paragraphs if len(_rens_html(p)) > 30]
        if lange_paragraphs:
            tekst = " ".join(lange_paragraphs)
        else:
            tekst = _rens_html(kandidat_tekst)
    else:
        tekst = _rens_html(kandidat_tekst)

    # Fjern veldig korte resultater
    if len(tekst) < 80:
        return ""

    return tekst[:1500]


def hent_artikkeltekst(url: str) -> str:
    """Henter og ekstraherer brødtekst fra en artikkel-URL."""
    if not url:
        return ""

    # Hopp over Google News redirect-URLer (kan ikke følges server-side)
    if "news.google.com" in url:
        return ""

    try:
        resp = requests.get(
            url, headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True
        )
        resp.raise_for_status()

        # Sjekk at vi fikk HTML tilbake
        content_type = resp.headers.get("Content-Type", "")
        if "text/html" not in content_type:
            return ""

        return _ekstraher_tekst_fra_html(resp.text)

    except (requests.RequestException, Exception):
        return ""


def hent_tekst_for_alle(artikler: list[Artikkel], verbose: bool = False) -> list[Artikkel]:
    """Henter artikkeltekst for alle artikler."""
    print(f"\n--- Henter artikkeltekst for {len(artikler)} artikler ---")
    hentet = 0
    feilet = 0

    for i, a in enumerate(artikler):
        if verbose and (i + 1) % 20 == 0:
            print(f"  Fremgang: {i + 1}/{len(artikler)} ({hentet} hentet, {feilet} feilet)")

        tekst = hent_artikkeltekst(a.url)
        if tekst:
            a.artikkeltekst = tekst
            hentet += 1
        else:
            feilet += 1

        # Vent mellom forespørsler
        time.sleep(random.uniform(0.3, 0.5))

    print(f"  Artikkeltekst hentet: {hentet} av {len(artikler)} ({feilet} feilet/paywalled)")
    return artikler


# ============================================================================
# AI-FILTRERING
# ============================================================================


def er_ai_relatert(tittel: str, sammendrag: str) -> tuple[bool, list[str]]:
    """
    Sjekker om en artikkel handler om AI basert på nøkkelord.
    Returnerer (er_relatert, liste_over_treff).
    """
    tekst = f"{tittel} {sammendrag}"
    treff = []

    for moenster in AI_SOKEORD:
        if re.search(moenster, tekst, re.IGNORECASE):
            # Lagre et lesbart navn for treffet
            lesbart = moenster.replace(r"\b", "").replace("\\b", "")
            if lesbart not in treff:
                treff.append(lesbart)

    return (len(treff) > 0, treff)


def filtrer_ai_artikler(
    artikler: list[Artikkel], verbose: bool = False
) -> list[Artikkel]:
    """Filtrerer ut kun AI-relaterte artikler."""
    filtrert = []
    for a in artikler:
        er_ai, treff = er_ai_relatert(a.tittel, a.sammendrag)
        if er_ai:
            a.sokeord_treff = treff
            filtrert.append(a)

    print(f"AI-relaterte artikler etter filtrering: {len(filtrert)} av {len(artikler)}")
    return filtrert


# ============================================================================
# DEDUPLISERING
# ============================================================================


def _normaliser_url(url: str) -> str:
    """Normaliserer URL for sammenligning."""
    if not url:
        return ""
    parsed = urlparse(url)
    # Fjern www., query-parametre, og trailing slash
    host = parsed.hostname or ""
    host = host.removeprefix("www.")
    path = parsed.path.rstrip("/")
    return f"{host}{path}"


def _normaliser_tittel(tittel: str) -> str:
    """Normaliserer tittel for sammenligning."""
    tittel = tittel.lower().strip()
    tittel = re.sub(r"[^\w\s]", "", tittel)
    tittel = re.sub(r"\s+", " ", tittel)
    return tittel


def dedupliser(artikler: list[Artikkel], verbose: bool = False) -> list[Artikkel]:
    """Fjerner duplikater basert på URL og tittellikhet."""
    unike: list[Artikkel] = []
    normaliserte: list[dict] = []
    fjernet = 0

    for a in artikler:
        norm_url = _normaliser_url(a.url)
        norm_tittel = _normaliser_tittel(a.tittel)

        er_duplikat = False
        for i, eksisterende in enumerate(normaliserte):
            # Sjekk URL-match
            if norm_url and eksisterende["url"] and norm_url == eksisterende["url"]:
                er_duplikat = True
                # Behold meningsstoff fremfor nyheter
                if a.er_meningsstoff and not unike[i].er_meningsstoff:
                    unike[i] = a
                    normaliserte[i] = {"url": norm_url, "tittel": norm_tittel}
                break

            # Sjekk tittellikhet
            if len(norm_tittel) > 10 and len(eksisterende["tittel"]) > 10:
                likhet = SequenceMatcher(
                    None, eksisterende["tittel"], norm_tittel
                ).ratio()
                if likhet > 0.75:
                    er_duplikat = True
                    if a.er_meningsstoff and not unike[i].er_meningsstoff:
                        unike[i] = a
                        normaliserte[i] = {"url": norm_url, "tittel": norm_tittel}
                    break

        if not er_duplikat:
            unike.append(a)
            normaliserte.append({"url": norm_url, "tittel": norm_tittel})
        else:
            fjernet += 1

    if fjernet > 0:
        print(f"Fjernet {fjernet} duplikater, {len(unike)} unike artikler gjenstår")
    return unike


# ============================================================================
# KLASSIFISERING — CLAUDE API
# ============================================================================

KLASSIFISERING_PROMPT = """Du er en medieanalytiker som klassifiserer norske artikler om kunstig intelligens.

For hver artikkel skal du:
1. FØRST les og forstå innholdet (tittel, kilde, og artikkeltekst)
2. Skriv én setning som oppsummerer artiklens vinkling/innramming
3. DERETTER velg den kategorien som passer best

Kategorier:

A) Business / produktivitet / hype — AI som verktøy, effektivisering, investeringer, startups, datasentre, implementeringsguider, «slik bruker du ChatGPT»
B) Regulering / juss / compliance — EU AI Act, KI-forordningen, personvern, GDPR, juridiske rammeverk, tilsyn
C) Arbeidsmarked / automatisering — AI erstatter jobber, automatisering av yrker, omstilling, kompetansebehov, arbeidsløshet
D) Geopolitikk / makt / demokrati — USA vs Kina, Big Tech maktkonsentrasjon, digital suverenitet, forsvar, demokrati, maktkonsentrasjon
E) Samfunn / kultur / eksistensiell refleksjon — hvordan AI påvirker kultur, ytringsfrihet, polarisering, desinformasjon, AGI-risiko
F) Utdanning / forskning — skoler, universiteter, juks, KI-kompetanse, akademisk forskning
G) Annet — passer genuint ikke i noen av kategoriene A-F

VIKTIGE REGLER:
- Velg kategorien som best beskriver artiklens HOVEDINNRAMMING, ikke alle temaer den berører.
- IKKE default til kategori A med mindre artikkelen genuint fokuserer på business/produktivitet.
- IKKE default til kategori G — de fleste AI-artikler passer i A-F. Bruk G kun når artikkelen virkelig ikke handler om noen av de andre temaene.
- En artikkel om AI i helsevesenet som effektivisering = A. En artikkel om AI i helsevesenet som etisk bekymring = E.
- En artikkel om at «regjeringen bevilger milliarder til KI» = A (investering/satsing), ikke E.
- En artikkel om at AI erstatter kundeservice-ansatte = C (arbeidsmarked), ikke A eller E.
- En artikkel om AI i skolen = F, ikke A.
- En artikkel om Big Tech og makt over demokratiet = D, ikke E.
- Primærinnramming er det som teller.
- Vær intellektuelt ærlig — ikke la bias påvirke klassifiseringen.

Artikler å klassifisere:
{artikler_json}

Svar BARE med JSON i dette formatet, ingen annen tekst:
[
  {{"id": 0, "vinkling": "Én setning som beskriver artiklens vinkling", "kategori": "D", "begrunnelse": "kort begrunnelse for kategorivalget"}},
  ...
]"""


MAX_RETRIES = 2


def klassifiser_claude(
    artikler: list[Artikkel], verbose: bool = False
) -> list[Artikkel]:
    """Klassifiserer artikler med Claude API."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("\n[!] ANTHROPIC_API_KEY er ikke satt.")
        print("    Sett miljøvariabelen før kjøring:")
        print('    PowerShell:  $env:ANTHROPIC_API_KEY = "sk-ant-..."')
        print('    Bash:        export ANTHROPIC_API_KEY="sk-ant-..."')
        sys.exit(1)

    try:
        import anthropic
    except ImportError:
        print("\n[!] anthropic-pakken er ikke installert.")
        print("    Installer med: pip install anthropic")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    klassifiserte = []

    # Del opp i batches på 20
    batch_storrelse = 20
    for i in range(0, len(artikler), batch_storrelse):
        batch = artikler[i : i + batch_storrelse]
        batch_nr = i // batch_storrelse + 1
        total_batches = (len(artikler) + batch_storrelse - 1) // batch_storrelse

        if verbose:
            print(f"  Klassifiserer batch {batch_nr}/{total_batches} ({len(batch)} artikler)...")

        # Bygg artikkel-JSON for promptet
        artikler_data = []
        for j, a in enumerate(batch):
            entry = {
                "id": j,
                "tittel": a.tittel,
                "kilde": a.kilde,
            }
            if a.artikkeltekst:
                entry["tekst"] = a.artikkeltekst[:800]
            elif a.sammendrag:
                entry["tekst"] = a.sammendrag[:300]
            artikler_data.append(entry)

        prompt = KLASSIFISERING_PROMPT.format(
            artikler_json=json.dumps(artikler_data, ensure_ascii=False, indent=2)
        )

        # Retry-logikk
        siste_feil = None
        for forsok in range(MAX_RETRIES + 1):
            try:
                response = client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=4000,
                    messages=[{"role": "user", "content": prompt}],
                )

                # Parse JSON-svar
                svar_tekst = response.content[0].text.strip()
                try:
                    resultater = json.loads(svar_tekst)
                except json.JSONDecodeError:
                    json_match = re.search(r"\[.*\]", svar_tekst, re.DOTALL)
                    if json_match:
                        resultater = json.loads(json_match.group())
                    else:
                        raise ValueError("Kunne ikke parse JSON fra Claude-svar")

                # Tilordne kategorier og vinklinger
                resultat_map = {r["id"]: r for r in resultater}
                for j, a in enumerate(batch):
                    if j in resultat_map:
                        r = resultat_map[j]
                        a.kategori = r.get("kategori", "G").upper()
                        a.vinkling = r.get("vinkling", "")
                        a.kategori_begrunnelse = r.get("begrunnelse", "")
                        if a.kategori not in KATEGORIER:
                            a.kategori = "G"
                    else:
                        a.kategori = "G"
                        a.kategori_begrunnelse = "Mangler i Claude-svar"
                    klassifiserte.append(a)

                siste_feil = None
                break  # Suksess — gå videre til neste batch

            except Exception as e:
                siste_feil = e
                if forsok < MAX_RETRIES:
                    print(f"  ADVARSEL: Batch {batch_nr} feilet (forsøk {forsok + 1}): {e}")
                    print(f"  Prøver på nytt om 2 sekunder...")
                    time.sleep(2.0)

        if siste_feil is not None:
            print(f"\n[!] Claude API feilet for batch {batch_nr} etter {MAX_RETRIES + 1} forsøk: {siste_feil}")
            print(f"    {len(klassifiserte)} av {len(artikler)} artikler ble klassifisert før feilen.")
            sys.exit(1)

        # Vent litt mellom API-kall
        if i + batch_storrelse < len(artikler):
            time.sleep(1.0)

    return klassifiserte


# ============================================================================
# ANALYSE
# ============================================================================


def analyser(artikler: list[Artikkel]) -> dict:
    """Beregner statistikk over kategorifordeling."""
    total = len(artikler)
    if total == 0:
        return {
            "total": 0,
            "fordeling": {k: 0 for k in KATEGORIER},
            "prosent": {k: 0.0 for k in KATEGORIER},
            "meningsstoff_antall": 0,
            "nyheter_antall": 0,
            "kilder_fordeling": {},
        }

    # Fordeling per kategori
    fordeling = {k: 0 for k in KATEGORIER}
    for a in artikler:
        kat = a.kategori if a.kategori in KATEGORIER else "G"
        fordeling[kat] += 1

    prosent = {k: (v / total) * 100 for k, v in fordeling.items()}

    # Meningsstoff vs nyheter
    meningsstoff = sum(1 for a in artikler if a.er_meningsstoff)

    # Kilder-fordeling
    kilder_fordeling: dict[str, int] = {}
    for a in artikler:
        kilder_fordeling[a.kilde] = kilder_fordeling.get(a.kilde, 0) + 1
    kilder_fordeling = dict(
        sorted(kilder_fordeling.items(), key=lambda x: x[1], reverse=True)
    )

    # Datoer og årsfordeling
    datoer = []
    for a in artikler:
        if a.dato:
            try:
                datoer.append(a.dato[:10])  # YYYY-MM-DD
            except (IndexError, TypeError):
                pass
    datoer.sort()
    tidligste_dato = datoer[0] if datoer else ""
    seneste_dato = datoer[-1] if datoer else ""

    aars_fordeling: dict[str, int] = {}
    for d in datoer:
        aar = d[:4]
        aars_fordeling[aar] = aars_fordeling.get(aar, 0) + 1
    aars_fordeling = dict(sorted(aars_fordeling.items()))

    return {
        "total": total,
        "fordeling": fordeling,
        "prosent": prosent,
        "meningsstoff_antall": meningsstoff,
        "nyheter_antall": total - meningsstoff,
        "kilder_fordeling": kilder_fordeling,
        "tidligste_dato": tidligste_dato,
        "seneste_dato": seneste_dato,
        "aars_fordeling": aars_fordeling,
    }


# ============================================================================
# TERMINAL-RAPPORT
# ============================================================================


def skriv_terminal_rapport(artikler: list[Artikkel], statistikk: dict):
    """Skriver ut analyse til terminalen med visuelle søylediagrammer."""
    total = statistikk["total"]
    if total == 0:
        print("\n[!] Ingen artikler å analysere.")
        return

    bar_bredde = 30  # Maks bredde på søylediagram

    print("\n" + "=" * 60)
    print("  NORSK AI-MEDIEANALYSE")
    print("=" * 60)
    print(f"\nAntall artikler analysert: {total}")
    print(
        f"Meningsstoff: {statistikk['meningsstoff_antall']} | "
        f"Nyheter: {statistikk['nyheter_antall']}"
    )
    if statistikk.get("tidligste_dato") and statistikk.get("seneste_dato"):
        print(
            f"Tidsperiode: {statistikk['tidligste_dato']} til {statistikk['seneste_dato']}"
        )
    if statistikk.get("aars_fordeling"):
        aar_str = ", ".join(
            f"{aar}: {antall}" for aar, antall in statistikk["aars_fordeling"].items()
        )
        print(f"Per år: {aar_str}")

    print("\nFordeling:")
    maks_antall = max(statistikk["fordeling"].values()) if statistikk["fordeling"] else 1
    for kat_id, kat_navn in KATEGORIER.items():
        antall = statistikk["fordeling"][kat_id]
        pst = statistikk["prosent"][kat_id]
        # Søylediagram
        if maks_antall > 0:
            bar_len = int((antall / maks_antall) * bar_bredde)
        else:
            bar_len = 0
        bar = "#" * bar_len + "-" * (bar_bredde - bar_len)
        # Kort etikett
        kort_navn = kat_navn[:35].ljust(35)
        print(f"  {kat_id}) {kort_navn} {bar} {pst:5.1f}% ({antall})")

    # Topp kilder
    print("\nTopp kilder:")
    for kilde, antall in list(statistikk["kilder_fordeling"].items())[:10]:
        print(f"  {kilde}: {antall}")

    print("=" * 60)


# ============================================================================
# FIL-OUTPUT
# ============================================================================


def skriv_filer(
    artikler: list[Artikkel],
    statistikk: dict,
    raw_results: list[dict],
    verbose: bool = False,
):
    """Skriver resultater til JSON, CSV og Markdown i resultater/."""
    os.makedirs(RESULTATER_DIR, exist_ok=True)

    # --- artikler.json ---
    artikler_data = [asdict(a) for a in artikler]
    with open(os.path.join(RESULTATER_DIR, "artikler.json"), "w", encoding="utf-8") as f:
        json.dump(artikler_data, f, ensure_ascii=False, indent=2)

    # --- statistikk.json ---
    with open(os.path.join(RESULTATER_DIR, "statistikk.json"), "w", encoding="utf-8") as f:
        json.dump(statistikk, f, ensure_ascii=False, indent=2)

    # --- artikler.csv ---
    with open(os.path.join(RESULTATER_DIR, "artikler.csv"), "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "kategori", "vinkling", "tittel", "kilde", "dato", "url"
        ])
        for a in artikler:
            writer.writerow([
                a.kategori, a.vinkling, a.tittel, a.kilde, a.dato, a.url
            ])

    # --- raw_results.csv ---
    with open(os.path.join(RESULTATER_DIR, "raw_results.csv"), "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["url", "kilde", "tittel", "status"])
        for r in raw_results:
            writer.writerow([r["url"], r["kilde"], r["tittel"], r["status"]])

    # --- rapport.md ---
    _skriv_rapport_md(artikler, statistikk)

    beholdt = sum(1 for r in raw_results if r["status"] == "beholdt")
    print(f"\nResultater skrevet til {RESULTATER_DIR}")
    print(f"  raw_results.csv: {len(raw_results)} AI-artikler ({beholdt} beholdt)")
    if verbose:
        print("  - artikler.json")
        print("  - statistikk.json")
        print("  - artikler.csv")
        print("  - raw_results.csv")
        print("  - rapport.md")


def _skriv_rapport_md(artikler: list[Artikkel], statistikk: dict):
    """Skriver menneskelesbar rapport i Markdown."""
    total = statistikk["total"]
    linjer = [
        "# Analyse av norsk mediedekning av kunstig intelligens\n",
        f"*Generert: {datetime.now().strftime('%Y-%m-%d %H:%M')}*\n",
        f"## Sammendrag\n",
        f"Totalt **{total}** AI-relaterte artikler ble analysert fra norske medier.\n",
    ]

    # Tidsperiode
    if statistikk.get("tidligste_dato") and statistikk.get("seneste_dato"):
        linjer.append(
            f"Tidsperiode: **{statistikk['tidligste_dato']}** til "
            f"**{statistikk['seneste_dato']}**\n"
        )

    # Årsfordeling
    if statistikk.get("aars_fordeling"):
        linjer.append("## Fordeling per år\n")
        linjer.append("| År | Antall artikler |")
        linjer.append("|----|-----------------|")
        for aar, antall in statistikk["aars_fordeling"].items():
            linjer.append(f"| {aar} | {antall} |")
        linjer.append("")

    # Kategorifordeling
    linjer.append("## Fordeling per kategori\n")
    linjer.append("| Kategori | Antall | Prosent |")
    linjer.append("|----------|--------|---------|")
    for kat_id, kat_navn in KATEGORIER.items():
        antall = statistikk["fordeling"][kat_id]
        pst = statistikk["prosent"][kat_id]
        linjer.append(f"| {kat_id}: {kat_navn} | {antall} | {pst:.1f}% |")
    linjer.append("")

    # Topp 5 eksempler per kategori
    linjer.append("## Eksempler per kategori\n")
    for kat_id, kat_navn in KATEGORIER.items():
        kat_artikler = [a for a in artikler if a.kategori == kat_id]
        if not kat_artikler:
            continue
        linjer.append(f"### {kat_id}: {kat_navn}\n")
        for a in kat_artikler[:5]:
            if a.vinkling:
                linjer.append(f"- **{a.tittel}** ({a.kilde}) — *{a.vinkling}*")
            else:
                linjer.append(f"- **{a.tittel}** ({a.kilde})")
        linjer.append("")

    # Kilder
    linjer.append("## Kilder\n")
    for kilde, antall in list(statistikk["kilder_fordeling"].items())[:15]:
        linjer.append(f"- {kilde}: {antall} artikler")
    linjer.append("")

    # Oppsummering på norsk
    linjer.append("## Oppsummering\n")
    if total > 0:
        # Finn de to største kategoriene
        sortert = sorted(statistikk["fordeling"].items(), key=lambda x: x[1], reverse=True)
        storste = sortert[0]
        nest_storste = sortert[1] if len(sortert) > 1 else None
        linjer.append(
            f"Analysen av {total} AI-relaterte artikler fra norske medier viser at "
            f"den største kategorien er {storste[0]} ({KATEGORIER[storste[0]]}) "
            f"med {statistikk['prosent'][storste[0]]:.0f} prosent av dekningen"
            + (f", etterfulgt av {nest_storste[0]} ({KATEGORIER[nest_storste[0]]}) "
               f"med {statistikk['prosent'][nest_storste[0]]:.0f} prosent."
               if nest_storste else ".")
        )
    else:
        linjer.append("Ingen artikler ble funnet for analyse.")
    linjer.append("")

    # Metode
    linjer.append("## Metode\n")
    linjer.append(
        f"Artikler ble hentet fra {len(KILDER)} kilder via RSS og Google News. "
        f"AI-relaterte artikler ble filtrert basert på nøkkelord i tittel og sammendrag. "
        f"Klassifisering ble utført med Claude API (claude-sonnet-4-6)."
    )
    linjer.append("")

    with open(os.path.join(RESULTATER_DIR, "rapport.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(linjer))


def _bygg_raw_results(
    alle_ai: list[Artikkel],
    beholdt_etter_dedup: set,
    pre_maks: list[Artikkel] | None,
    beholdt_etter_maks: set | None,
) -> list[dict]:
    """Bygger raw_results-liste med status for hver AI-filtrert artikkel."""
    # Finn hvilke som ble fjernet i hvert steg
    maks_begrenset = set()
    if pre_maks is not None and beholdt_etter_maks is not None:
        maks_begrenset = {id(a) for a in pre_maks} - beholdt_etter_maks

    results = []
    for a in alle_ai:
        a_id = id(a)
        if a_id not in beholdt_etter_dedup:
            status = "fjernet (duplikat)"
        elif a_id in maks_begrenset:
            status = "fjernet (maks-begrensning)"
        else:
            status = "beholdt"

        results.append({
            "url": a.url,
            "kilde": a.kilde,
            "tittel": a.tittel,
            "status": status,
        })

    return results


# ============================================================================
# HOVEDPROGRAM
# ============================================================================


def main():
    parser = argparse.ArgumentParser(
        description="Norsk AI-medieskraper — samler og klassifiserer AI-artikler"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Vis detaljert logging",
    )
    parser.add_argument(
        "--maks",
        type=int,
        default=0,
        help="Begrens antall artikler som behandles (0 = ingen begrensning)",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  Norsk AI-medieskraper")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    # 1. Hent artikler fra alle kilder
    alle_artikler = hent_alle_artikler(verbose=args.verbose)

    if not alle_artikler:
        print("\n[!] Ingen artikler hentet. Sjekk nettverkstilkobling og kilder.")
        return

    # 2. Filtrer AI-relaterte artikler
    ai_artikler = filtrer_ai_artikler(alle_artikler, verbose=args.verbose)

    if not ai_artikler:
        print("\n[!] Ingen AI-relaterte artikler funnet.")
        return

    # Lagre alle AI-filtrerte for raw_results
    alle_ai_artikler = list(ai_artikler)

    # 3. Dedupliser
    ai_artikler = dedupliser(ai_artikler, verbose=args.verbose)
    beholdt_etter_dedup = {id(a) for a in ai_artikler}

    # 4. Begrens antall om ønsket
    if args.maks > 0 and len(ai_artikler) > args.maks:
        print(f"Begrenset til {args.maks} artikler (av {len(ai_artikler)})")
        ai_artikler_pre_maks = list(ai_artikler)
        ai_artikler = ai_artikler[: args.maks]
        beholdt_etter_maks = {id(a) for a in ai_artikler}
    else:
        ai_artikler_pre_maks = None
        beholdt_etter_maks = None

    # 5. Hent artikkeltekst fra URL-er (for bedre klassifisering)
    ai_artikler = hent_tekst_for_alle(ai_artikler, verbose=args.verbose)

    # 6. Klassifiser med Claude API
    print(f"\n--- Klassifiserer {len(ai_artikler)} artikler ---")
    ai_artikler = klassifiser_claude(ai_artikler, verbose=args.verbose)

    # 7. Bygg raw_results med status
    raw_results = _bygg_raw_results(
        alle_ai_artikler, beholdt_etter_dedup,
        ai_artikler_pre_maks, beholdt_etter_maks,
    )

    # 8. Analyser
    statistikk = analyser(ai_artikler)

    # 9. Skriv ut resultater
    skriv_terminal_rapport(ai_artikler, statistikk)
    skriv_filer(ai_artikler, statistikk, raw_results, verbose=args.verbose)


if __name__ == "__main__":
    main()
