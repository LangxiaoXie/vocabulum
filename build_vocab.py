"""
build_vocab.py — Expand French, German, and Latin vocab lists.

Primary source: kaikki.org (pre-processed Wiktionary extracts)
  - Real lemmas (not inflected forms)
  - English glosses + IPA built-in
  - Covers French, German, Latin

Frequency ranking: hermitdave OpenSubtitles frequency lists
  - French/German: rank 1–15000, paired with kaikki lemmas for CEFR assignment

Run:  python3 build_vocab.py
"""

import json, re, sys, time
from urllib.request import urlopen, Request
from urllib.error import URLError

BASE     = "/Users/lucasxie/vocabulum"
OUT_DATA = f"{BASE}/vocab-data.json"
OUT_DE   = f"{BASE}/vocab-de.js"
OUT_LA   = f"{BASE}/vocab-la.js"

KAIKKI_FR = "https://kaikki.org/dictionary/French/kaikki.org-dictionary-French.jsonl"
KAIKKI_DE = "https://kaikki.org/dictionary/German/kaikki.org-dictionary-German.jsonl"
KAIKKI_LA = "https://kaikki.org/dictionary/Latin/kaikki.org-dictionary-Latin.jsonl"
FREQ_FR   = "https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/fr/fr_50k.txt"
FREQ_DE   = "https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/de/de_50k.txt"

# ── CEFR thresholds by frequency rank ─────────────────────────────────────────
CEFR_FR = [(600,"A1 Débutant"),(1200,"A2 Élémentaire"),(2500,"B1 Intermédiaire"),(5000,"B2 Avancé"),(15000,"C1 Autonome")]
CEFR_DE = [(600,"A1 Grundwortschatz"),(1200,"A2 Alltag"),(2500,"B1 Mittelstufe"),(5000,"B2 Fortgeschritten"),(15000,"C1 Oberstufe")]
LA_LEVELS= [(200,"I Fundamenta"),(400,"II Grammatica"),(600,"III Classica"),(800,"IV Philosophica"),(99999,"V Poetica")]

# POS normalisation
POS_FR = {"noun":"Nom","verb":"Verbe","adj":"Adj","adv":"Adv","prep":"Prép",
          "conj":"Conj","pron":"Pron","article":"Art","intj":"Interj","num":"Num",
          "name":"Nom propre","abbrev":"Abbr","phrase":"Expr","det":"Dét"}
POS_DE = {"noun":"Nomen","verb":"Verb","adj":"Adjektiv","adv":"Adverb","prep":"Präp",
          "conj":"Konj","pron":"Pronomen","article":"Artikel","intj":"Interj",
          "num":"Numerale","name":"Eigenname","abbrev":"Abk","phrase":"Ausdruck","det":"Artikel"}
POS_LA = {"noun":"nomen","verb":"verbum","adj":"adiectivum","adv":"adverbium",
          "prep":"praepositio","conj":"coniunctio","pron":"pronomen","num":"numerale",
          "name":"nomen proprium","particle":"particula","phrase":"locutio"}

SKIP_POS = {"name","abbrev"}   # skip proper nouns and abbreviations

# ── Helpers ───────────────────────────────────────────────────────────────────

def fetch_bytes(url: str) -> bytes:
    req = Request(url, headers={"User-Agent": "vocabulum-builder/2.0"})
    with urlopen(req, timeout=60) as r:
        return r.read()

def fetch_freq_rank(url: str, max_rank: int) -> dict[str, int]:
    """Return {lowercase_word: rank} for top max_rank words."""
    raw = fetch_bytes(url).decode("utf-8")
    rank_map: dict[str, int] = {}
    rank = 0
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        word = parts[0].lower()
        if not word or any(c.isdigit() or c in "'-" for c in word[:1]):
            continue
        rank += 1
        if word not in rank_map:
            rank_map[word] = rank
        if rank >= max_rank:
            break
    return rank_map

def rank_to_level(rank: int, thresholds: list) -> str:
    for threshold, label in thresholds:
        if rank <= threshold:
            return label
    return thresholds[-1][1]

def clean_gloss(gloss: str) -> str:
    """Strip markup, keep first sense, max 70 chars."""
    g = re.sub(r'\{\{[^}]+\}\}', '', gloss)  # remove templates
    g = re.sub(r'\[\[([^\]|]+\|)?([^\]]+)\]\]', r'\2', g)  # [[link|text]] → text
    g = re.sub(r"'''?([^']+)'''?", r'\1', g)  # bold/italic
    g = re.sub(r'<[^>]+>', '', g)  # HTML tags
    g = g.split(';')[0].split(',')[0].strip()
    return g[:70].strip()

def stream_kaikki(url: str, rank_map: dict[str, int],
                  existing: set[str], pos_map: dict,
                  thresholds: list, id_prefix: str,
                  max_per_level: dict[str, int],
                  require_ipa: bool = False) -> dict[str, list[dict]]:
    """
    Stream kaikki JSONL, collect entries by CEFR level.
    Stops when all levels are full.
    """
    by_level: dict[str, list[dict]] = {label: [] for _, label in thresholds}
    counts  = {label: 0 for _, label in thresholds}
    seen    = set(existing)
    total_read = 0

    req = Request(url, headers={"User-Agent": "vocabulum-builder/2.0"})
    with urlopen(req, timeout=120) as resp:
        for raw_line in resp:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            total_read += 1
            if total_read % 10000 == 0:
                filled = sum(counts[l] for l in counts)
                print(f"  Scanned {total_read:,} entries, kept {filled} so far...", flush=True)

            # Check if all levels full
            if all(counts[label] >= max_per_level.get(label, 99999) for _, label in thresholds):
                break

            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue

            word = d.get("word", "").strip()
            pos  = d.get("pos",  "").lower()

            if not word or len(word) < 2:
                continue
            if pos in SKIP_POS:
                continue
            if word.lower() in seen:
                continue

            # Get gloss
            senses = d.get("senses", [])
            gloss  = ""
            for s in senses:
                g = (s.get("glosses") or s.get("raw_glosses") or [""])
                g = g[0] if g else ""
                g = clean_gloss(g)
                if g and not g.startswith("(") and len(g) > 2:
                    gloss = g
                    break
            if not gloss:
                continue

            # Get IPA
            sounds = d.get("sounds", [])
            ipa    = next((s.get("ipa","") for s in sounds if s.get("ipa")), "")
            if require_ipa and not ipa:
                continue

            # Determine rank / level
            rank = rank_map.get(word.lower(), rank_map.get(word, None))
            if rank is None:
                # No frequency data → put in last level bucket if space
                level = thresholds[-1][1]
            else:
                level = rank_to_level(rank, thresholds)

            if counts[level] >= max_per_level.get(level, 99999):
                continue

            # Normalise POS
            pos_label = pos_map.get(pos, pos.capitalize())

            entry = {
                "id":   f"{id_prefix}_{len(seen):05d}",
                "w":    word,
                "ph":   ipa,
                "pos":  pos_label,
                "tr":   gloss,
                "ex":   "",
                "exTr":"",
                "rank": rank or (100000 + counts[level]),
            }
            by_level[level].append(entry)
            counts[level] += 1
            seen.add(word.lower())

    print(f"  Scanned {total_read:,} total entries")
    for label, n in counts.items():
        print(f"  {label}: {n} new entries")
    return by_level

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    # Load existing data
    print("Loading existing data...")
    with open(OUT_DATA) as f:
        vocab_data = json.load(f)

    de_text  = open(OUT_DE).read()
    de_match = re.search(r'const VOCAB_DE\s*=\s*(\{.*\});?\s*$', de_text, re.DOTALL)
    vocab_de = json.loads(de_match.group(1)) if de_match else {}

    la_text  = open(OUT_LA).read()
    la_match = re.search(r'const VOCAB_LA\s*=\s*(\{.*\});?\s*$', la_text, re.DOTALL)
    vocab_la = json.loads(la_match.group(1)) if la_match else {}

    fr_existing = {e["w"].lower() for lvl in vocab_data.get("fr",{}).values() for e in lvl}
    de_existing = {e["w"].lower() for lvl in vocab_de.values() for e in lvl}
    la_existing = {e["w"].lower() for lvl in vocab_la.values() for e in lvl}

    fr_counts_before = {lvl: len(entries) for lvl, entries in vocab_data.get("fr",{}).items()}
    de_counts_before = {lvl: len(entries) for lvl, entries in vocab_de.items()}
    la_counts_before = {lvl: len(entries) for lvl, entries in vocab_la.items()}

    print(f"  Existing: FR={len(fr_existing)}  DE={len(de_existing)}  LA={len(la_existing)}")

    # ── French ────────────────────────────────────────────────────────────────
    print("\n=== FRENCH ===")
    print("Fetching frequency list (top 15,000)...")
    fr_rank = fetch_freq_rank(FREQ_FR, 15000)
    print(f"  {len(fr_rank):,} ranked words")

    # How many more words needed per level
    fr_targets = {
        "A1 Débutant":       max(0, 600  - fr_counts_before.get("A1 Débutant",0)),
        "A2 Élémentaire":    max(0, 600  - fr_counts_before.get("A2 Élémentaire",0)),
        "B1 Intermédiaire":  max(0, 1300 - fr_counts_before.get("B1 Intermédiaire",0)),
        "B2 Avancé":         max(0, 2499 - fr_counts_before.get("B2 Avancé",0)),
        "C1 Autonome":       300,
    }
    print(f"  Targets (new words needed): {fr_targets}")
    print("Streaming kaikki.org French...")
    fr_new = stream_kaikki(KAIKKI_FR, fr_rank, fr_existing, POS_FR, CEFR_FR,
                           "fr", fr_targets, require_ipa=False)

    # Merge
    fr_merged = {lvl: list(entries) for lvl, entries in vocab_data.get("fr",{}).items()}
    for level, new_entries in fr_new.items():
        if level not in fr_merged:
            fr_merged[level] = []
        existing_w = {e["w"] for e in fr_merged[level]}
        for e in new_entries:
            if e["w"] not in existing_w:
                fr_merged[level].append(e)
        fr_merged[level].sort(key=lambda e: e.get("rank", 99999))

    print("\nFrench results:")
    for lvl, entries in fr_merged.items():
        was = fr_counts_before.get(lvl, 0)
        print(f"  {lvl}: {was} → {len(entries)} (+{len(entries)-was})")

    vocab_data["fr"] = fr_merged

    # ── German ────────────────────────────────────────────────────────────────
    print("\n=== GERMAN ===")
    print("Fetching frequency list (top 15,000)...")
    de_rank = fetch_freq_rank(FREQ_DE, 15000)
    print(f"  {len(de_rank):,} ranked words")

    de_targets = {
        "A1 Grundwortschatz": max(0, 600  - de_counts_before.get("A1 Grundwortschatz",0)),
        "A2 Alltag":          max(0, 600  - de_counts_before.get("A2 Alltag",0)),
        "B1 Mittelstufe":     max(0, 1300 - de_counts_before.get("B1 Mittelstufe",0)),
        "B2 Fortgeschritten": max(0, 2499 - de_counts_before.get("B2 Fortgeschritten",0)),
        "C1 Oberstufe":       max(0, 300  - de_counts_before.get("C1 Oberstufe",0)),
    }
    print(f"  Targets (new words needed): {de_targets}")
    print("Streaming kaikki.org German...")
    de_new = stream_kaikki(KAIKKI_DE, de_rank, de_existing, POS_DE, CEFR_DE,
                           "de", de_targets, require_ipa=True)

    de_merged = {lvl: list(entries) for lvl, entries in vocab_de.items()}
    for level, new_entries in de_new.items():
        if level not in de_merged:
            de_merged[level] = []
        existing_w = {e["w"] for e in de_merged[level]}
        for e in new_entries:
            if e["w"] not in existing_w:
                de_merged[level].append(e)
        de_merged[level].sort(key=lambda e: e.get("rank", 99999))

    print("\nGerman results:")
    for lvl, entries in de_merged.items():
        was = de_counts_before.get(lvl, 0)
        print(f"  {lvl}: {was} → {len(entries)} (+{len(entries)-was})")

    # ── Latin ─────────────────────────────────────────────────────────────────
    print("\n=== LATIN ===")
    la_targets = {
        "I Fundamenta":   max(0, 200 - la_counts_before.get("I Fundamenta",0)),
        "II Grammatica":  max(0, 200 - la_counts_before.get("II Grammatica",0)),
        "III Classica":   max(0, 200 - la_counts_before.get("III Classica",0)),
        "IV Philosophica":max(0, 200 - la_counts_before.get("IV Philosophica",0)),
        "V Poetica":      max(0, 200 - la_counts_before.get("V Poetica",0)),
    }
    # For Latin no frequency list → use empty rank map, words go into levels by index
    print(f"  Targets: {la_targets}")
    print("Streaming kaikki.org Latin...")
    la_new = stream_kaikki(KAIKKI_LA, {}, la_existing, POS_LA, LA_LEVELS,
                           "la", la_targets, require_ipa=False)

    la_merged = {lvl: list(entries) for lvl, entries in vocab_la.items()}
    for level, new_entries in la_new.items():
        if level not in la_merged:
            la_merged[level] = []
        existing_w = {e["w"] for e in la_merged[level]}
        for e in new_entries:
            if e["w"] not in existing_w:
                la_merged[level].append(e)

    print("\nLatin results:")
    for lvl, entries in la_merged.items():
        was = la_counts_before.get(lvl, 0)
        print(f"  {lvl}: {was} → {len(entries)} (+{len(entries)-was})")

    # ── Write outputs ─────────────────────────────────────────────────────────
    print("\nWriting files...")
    with open(OUT_DATA, "w", encoding="utf-8") as f:
        json.dump(vocab_data, f, ensure_ascii=False, separators=(",", ":"))
    js_data = "const VOCAB_DATA = " + json.dumps(vocab_data, ensure_ascii=False, separators=(",",":")) + ";"
    with open(f"{BASE}/vocab-data.js", "w", encoding="utf-8") as f:
        f.write(js_data)

    de_js = "const VOCAB_DE = " + json.dumps(de_merged, ensure_ascii=False, separators=(",",":")) + ";"
    with open(OUT_DE, "w", encoding="utf-8") as f:
        f.write(de_js)

    la_js = "const VOCAB_LA = " + json.dumps(la_merged, ensure_ascii=False, separators=(",",":")) + ";"
    with open(OUT_LA, "w", encoding="utf-8") as f:
        f.write(la_js)

    print("Done.")


if __name__ == "__main__":
    main()
