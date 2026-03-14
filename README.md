# Norsk AI-medieskraper

A Python CLI tool that scrapes Norwegian media for AI-related articles, classifies them by framing, and tests whether coverage is dominated by business/hype perspectives at the expense of deeper societal discourse.

Built as an empirical evidence tool for an op-ed on Norwegian AI discourse.

## What it does

1. **Collects** ~2700 articles from 46 sources (RSS feeds + Google News with 3-year time windows)
2. **Filters** down to AI-related articles (~950 unique) using keyword matching
3. **Deduplicates** using URL normalization and title similarity
4. **Fetches article text** from each URL to give the classifier real content to work with
5. **Classifies** each article: Claude reads the content, writes a one-sentence angle summary (*vinkling*), then assigns one of 6 framing categories
6. **Analyzes** the distribution and runs a hypothesis test

## Categories

| ID | Label | Description |
|----|-------|-------------|
| A | Business / produktivitet / hype | AI as tool, efficiency, investments, startups, implementation guides |
| B | Regulering / juss / compliance | EU AI Act, privacy, GDPR, legal frameworks |
| C | Arbeidsmarked / automatisering | AI replacing jobs, automation, workforce transition, reskilling |
| D | Geopolitikk / makt / demokrati | US vs China, Big Tech power, digital sovereignty, defense, democracy |
| E | Samfunn / kultur / eksistensiell refleksjon | How AI affects culture, free speech, polarization, disinformation, AGI risk |
| F | Utdanning / forskning | Schools, universities, academic research |
| G | Annet | Doesn't fit other categories |

## Sources

**Direct RSS feeds:** NRK, VG, Aftenposten (news + opinion), Dagbladet, E24

**Google News site-specific queries** (for opinion outlets without RSS): NRK Ytring, Dagbladet Meninger, Morgenbladet, Klassekampen, DN, Minerva

**General Google News queries** (balanced — includes both hype and democracy/power search terms to avoid confirmation bias)

All Google News queries are split into **yearly time windows** covering the last 3 years (March 2023 – March 2026), using `after:` and `before:` date parameters. This ensures historical coverage beyond Google News RSS's default recency bias. Each base query runs once per year, producing ~40 total queries alongside the 7 direct RSS feeds.

## How classification works

Classification happens in three steps:

1. **Article text fetching** — For each article, the scraper fetches the actual page content and extracts the body text (up to 1500 characters). This works for direct RSS sources (NRK, VG, Aftenposten, Dagbladet, E24). Google News redirect URLs can't be resolved server-side, so those articles are classified from title only.

2. **Primary: Claude API** — Articles are batched (20 at a time) and sent to `claude-sonnet-4-6`. Per article, the API receives the title, source name, and up to 800 characters of article text (or 300 characters of RSS summary as fallback). Claude first writes a one-sentence *vinkling* (angle summary) describing the article's framing, then assigns a category. This "understand first, classify second" approach produces more accurate results. The prompt explicitly warns against defaulting to category A or F.

An `ANTHROPIC_API_KEY` is required. The scraper will exit with a clear error message if it's not set.

## Hypothesis test

The tool tests whether Norwegian AI coverage is dominated by business/hype framing:

- **Metric:** Ratio of categories A+C (business + labor market) to categories D+E (geopolitics/democracy + society/culture)
- **SUPPORTED:** ratio > 3:1
- **PARTIALLY SUPPORTED:** ratio 1.5–3:1
- **NOT SUPPORTED:** ratio < 1.5:1

## Getting started

### Prerequisites

- **Python 3.11+** — check with `python --version`
- **Anthropic API key** (required) — sign up at [console.anthropic.com](https://console.anthropic.com/) and create an API key.

### 1. Clone and install

```bash
git clone https://github.com/YOUR_USERNAME/media_webscraper.git
cd media_webscraper
pip install -r requirements.txt
```

### 2. Set your API key

The key is set as an environment variable (not in the code). It only lasts for the current terminal session.

**PowerShell (Windows):**
```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-your-key-here"
```

**cmd (Windows):**
```cmd
set ANTHROPIC_API_KEY=sk-ant-your-key-here
```

**Bash (macOS/Linux):**
```bash
export ANTHROPIC_API_KEY="sk-ant-your-key-here"
```

### 3. Try a quick test run first

```bash
python scraper.py --verbose --maks 20
```

This processes only 20 articles and shows detailed logging. Good for verifying everything works before a full run.

### 4. Run the full scraper

```bash
python scraper.py
```

**Everything is automatic from here.** The script will:

1. Fetch articles from 46 sources (~30 seconds, with polite rate-limiting delays)
2. Filter to AI-related articles and deduplicate (~950 unique articles across 3 years)
3. Fetch article body text from each URL (skips Google News redirects)
4. Send articles to Claude API in batches of 20 — Claude reads the content, writes a one-sentence angle summary, then classifies
5. Print the full analysis to the terminal
6. Write results to the `resultater/` folder

### 5. Check results

After the run, you'll find 5 files in `resultater/`:

| File | What it contains |
|------|------------------|
| `artikler.json` | Every article with title, source, date, URL, article text, angle summary (*vinkling*), category, and justification |
| `statistikk.json` | Aggregated numbers — category counts, percentages, yearly breakdown, hypothesis test result |
| `artikler.csv` | Flat table with category, vinkling, title, source, date, URL — open in Excel/Google Sheets for inspection |
| `raw_results.csv` | All AI-filtered articles with status: `beholdt`, `fjernet (duplikat)`, or `fjernet (maks-begrensning)` — full pipeline traceability |
| `rapport.md` | Human-readable report in Norwegian — copy-paste numbers directly into your op-ed |

The terminal also prints a bar chart of the category distribution and the hypothesis test verdict.

### Cost

A full run with ~950 articles costs roughly **$1–2** in Claude API usage (article text increases input tokens). You can check your usage at [console.anthropic.com](https://console.anthropic.com/).

### Flags

| Flag | Description |
|------|-------------|
| `--verbose` / `-v` | Show detailed logging |
| `--maks N` | Limit number of articles processed |

## Design principles

- **Intellectual honesty** — The tool is not optimized to confirm the hypothesis. Google News queries cover the full spectrum of AI discourse. If Norwegian media *does* discuss power and democracy, the data will show it.
- **Classification precision** — Primary framing determines the category. An article about "government investing billions in AI" is A (business), not D (society), even if it mentions societal impact in passing.
- **Fail gracefully** — One failing source never crashes the script. API errors fall back to rule-based classification silently.

## License

MIT
