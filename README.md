# Vocabulum

A spaced-repetition vocabulary learning web app for **German**, **French**, **Spanish**, and **Latin**. Runs entirely in the browser with no backend required.

## Features

### Learning
- **Flashcard SRS** — Simplified SM-2 spaced repetition algorithm with 4 rating buttons: Again (Q), Hard (W), OK (E), Easy (R)
- **CEFR levels** — Vocabulary divided by level (A1 → C1/C2) based on passive word count thresholds
- **Auto-pronunciation** — Web Speech API reads each word aloud when a card appears
- **Keyboard shortcuts** — Space to reveal, Q/W/E/R to rate, no mouse needed

### Languages & Word Counts

| Language | Levels | Words |
|----------|--------|-------|
| 🇩🇪 German (Deutsch) | A1 Grundwortschatz · A2 Alltag · B1 Mittelstufe · B2 Fortgeschritten · C1 Oberstufe | ~381 |
| 🇫🇷 French (Français) | A1 Débutant · A2 Élémentaire · B1 Intermédiaire · B2 Avancé | ~1,906 |
| 🇪🇸 Spanish (Español) | A1 Básico · A2 Elemental · B1 Intermedio · B2 Avanzado | ~5,000 |
| 🏛️ Latin (Latina) | I Fundamenta · II Grammatica · III Classica · IV Philosophica · V Poetica | 150 |

### Dashboard & Statistics
- **Mastery donut** — New / Learning / Mastered breakdown
- **30-day activity line chart** — Daily cards reviewed
- **52-week heatmap** — GitHub-style activity calendar
- **14-day stacked bar** — New vs. review counts per day
- **Memory retention chart** — 5 lines showing words with SRS interval >10/30/60/90 days over the last 10 days
- **Per-list progress bars** — Mastery % for every deck

### UI / UX
- **Scroll-snap sidebar** — Scroll through decks with a center-locking cursor bar; active deck snaps to the middle
- **CEFR progress bar** — Shows your current level and progress toward the next
- **Share progress** — Generates a PNG image with stats + 16-week streak calendar (downloads automatically)
- **Purchase interface** — Mock word-pack unlocking (20 words / $1) and streak recovery ($3)
- **Settings panel** — Daily new-word count, auto-pronounce toggle, slow speech toggle, `.apkg` import placeholder

## Usage

Just open `index.html` in any modern browser — no server needed.

```
open index.html
```

> **Note:** Vocabulary for French and Spanish is loaded from `vocab-data.js`. Open the file directly (not via `file://` fetch) since the data is bundled as a `<script>` tag to avoid browser CORS restrictions.

## File Structure

```
vocabulum/
├── index.html          # Main app (all HTML, CSS, JS in one file)
├── vocab-de.js         # German vocabulary (~381 words, A1–C1, with IPA + examples)
├── vocab-la.js         # Latin vocabulary (150 words, 5 thematic levels, with IPA + examples)
├── vocab-data.js       # French (~1,906) + Spanish (~5,000) words, CEFR-divided
└── vocab-data.json     # Source JSON for vocab-data.js
```

## Vocabulary Sources

- **Spanish** — Anki shared deck [241428882](https://ankiweb.net/shared/info/241428882) (frequency-ranked, CEFR-divided by rank)
- **French** — [FreeDict deu-eng](https://freedict.org) cross-referenced with [OpenSubtitles frequency list](https://github.com/hermitdave/FrequencyWords)
- **German** — Curated manually following DCC Core Latin/CEFR frequency guidelines, with IPA (X-SAMPA) and example sentences
- **Latin** — Curated manually from DCC Core Latin Vocabulary and classical sources (Virgil, Cicero, Horace, Ovid, etc.)

## SRS Algorithm

Simplified SM-2:

| Rating | Effect |
|--------|--------|
| Again (0) | Reset interval to 0; ease factor −0.20 |
| Hard (1) | Interval ×1.2; ease factor −0.15 |
| OK (2) | Standard SM-2 interval progression |
| Easy (3) | Interval × ef × 1.3; ease factor +0.15 |

A word is marked **Mastered** once its interval reaches 21+ days.

## CEFR Level Thresholds

| Level | Passive Word Count |
|-------|--------------------|
| A1 | 0 – 600 |
| A2 | 600 – 1,200 |
| B1 | 1,200 – 2,500 |
| B2 | 2,500 – 5,000 |
| C1 | 5,000 – 10,000 |
| C2 | 10,000 – 20,000 |

## License

MIT
