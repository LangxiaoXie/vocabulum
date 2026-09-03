"""
build_vocab.py — Expand French, German, and Latin vocab lists.

Primary source: kaikki.org (pre-processed Wiktionary extracts)
  - Real lemmas (not inflected forms)
  - English glosses + IPA built-in
  - Covers French, German, Latin

Frequency ranking: hermitdave OpenSubtitles frequency lists
  - French/German: rank 1–15000, paired with kaikki lemmas for CEFR assignment
  - Latin has no such list, so its five levels stay thematic (Fundamenta,
    Grammatica, Classica, Philosophica, Poetica): the English gloss picks the
    theme, and a coreness score derived from the kaikki entry itself
    (Romance descendants, polysemy, derived terms) decides which words get a
    slot and in what order.

Run:  python3 build_vocab.py                    # all three languages
      python3 build_vocab.py --only la          # Latin only
      python3 build_vocab.py --only la --cache  # reuse cached Latin pool
"""

import json, os, re, sys, time
from urllib.request import urlopen, Request
from urllib.error import URLError

BASE     = "/Users/lucasxie/vocabulum"
OUT_DATA = f"{BASE}/vocab-data.json"
OUT_DE   = f"{BASE}/vocab-de.js"
OUT_LA   = f"{BASE}/vocab-la.js"
LA_CACHE = os.path.expanduser("~/.cache/vocabulum/la-candidates.json")

KAIKKI_FR = "https://kaikki.org/dictionary/French/kaikki.org-dictionary-French.jsonl"
KAIKKI_DE = "https://kaikki.org/dictionary/German/kaikki.org-dictionary-German.jsonl"
KAIKKI_LA = "https://kaikki.org/dictionary/Latin/kaikki.org-dictionary-Latin.jsonl"
FREQ_FR   = "https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/fr/fr_50k.txt"
FREQ_DE   = "https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/de/de_50k.txt"

# ── CEFR thresholds by frequency rank ─────────────────────────────────────────
CEFR_FR = [(600,"A1 Débutant"),(1200,"A2 Élémentaire"),(2500,"B1 Intermédiaire"),(5000,"B2 Avancé"),(15000,"C1 Autonome")]
CEFR_DE = [(600,"A1 Grundwortschatz"),(1200,"A2 Alltag"),(2500,"B1 Mittelstufe"),(5000,"B2 Fortgeschritten"),(15000,"C1 Oberstufe")]

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

_FORM_OF_RE = re.compile(
    r'\b(?:inflection|form|plural|singular|genitive|dative|accusative|ablative'
    r'|vocative|nominative|comparative|superlative|participle|gerund|supine'
    r'|imperative|subjunctive|indicative|infinitive|first-person|second-person'
    r'|third-person)\b.{0,40}\bof\b', re.IGNORECASE)


def is_form_of(sense: dict) -> bool:
    """
    True if a sense describes an inflected form rather than a lemma. Shared by
    all three languages: kaikki is roughly half inflected forms, and they make
    useless flashcards ("visit: third-person singular of vīsō").
    """
    if sense.get("form_of") or sense.get("alt_of"):
        return True
    tags = sense.get("tags") or []
    if "form-of" in tags or "alt-of" in tags:
        return True
    g = (sense.get("glosses") or sense.get("raw_glosses") or [""])
    return bool(g and _FORM_OF_RE.search(g[0] or ""))



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

            # Get gloss (inflected forms are not lemmas — skip them)
            senses = [s for s in d.get("senses", []) if not is_form_of(s)]
            if not senses:
                continue
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

# ── Latin thematic levels ─────────────────────────────────────────────────────
# Latin has no OpenSubtitles frequency list, so level = theme (matched on the
# English gloss) and "frequency" = a coreness score derived from the kaikki
# entry itself (Romance descendants, polysemy, derived/related terms).
LA_LEVEL_NAMES = ["I Fundamenta", "II Grammatica", "III Classica",
                  "IV Philosophica", "V Poetica"]

LA_THEMES = {
 # Fundamenta is matched on content words only: English function words
 # ("and", "or", "not", "all", "other") turn up incidentally in any gloss and
 # would sweep unrelated lemmas in — abduco "to lead away or aside" matched on
 # "or" before they were removed. Latin function words reach this level through
 # LA_FUNCTION_POS instead.
 "I Fundamenta": """be become have hold keep do act make do build go come
    walk run stand sit lie fall rise move turn carry bear bring lead send
    give take get put place receive find seek use work help begin cease
    see look watch hear listen feel touch know want wish need must able
    open close fill empty join mix cut break join house home door table bread
    food eat drink man woman person child boy girl father mother son daughter
    parent family friend neighbour day year month hour time thing self
    body head hand foot eye ear mouth life live born big small great little
    large long short high low new old young many few whole half""",
 "II Grammatica": """word name noun verb adjective adverb pronoun case
    declension conjugation speech speak say tell talk call ask question answer
    write writing written book letter page volume scroll read reader language
    tongue grammar rhetoric teach teacher learn learner school student pupil
    study disciple lesson memory remember recall mind think thought
    understand meaning sense sentence phrase syllable accent voice sound
    translate interpret record list note copy account narrate story tale
    explain define definition""",
 "III Classica": """war peace battle fight army legion soldier troop weapon
    arms sword shield spear helmet camp enemy foe victory triumph defeat
    conquer king queen prince rule reign kingdom empire emperor consul praetor
    senate senator citizen city town wall gate tower fort republic
    people nation tribe law legal court judge trial crime guilt
    punishment penalty money coin tax tribute trade merchant
    market ship sail fleet harbour road bridge temple priest sacrifice altar
    god goddess worship province colony slave servant master household
    farm plough harvest horse chariot""",
 "IV Philosophica": """truth true false falsehood justice unjust injustice
    virtue vice soul spirit reason rational essence existence substance
    accident principle purpose wisdom wise folly knowledge ignorance science
    philosophy philosopher freedom liberty will desire choice necessity
    eternal eternity infinite finite universal particular idea concept notion
    opinion belief faith doubt proof argument logic ethics moral morality
    duty honour dignity divine divinity immortal mortal happiness blessed
    contemplate contemplation judgement conscience intellect perceive
    perception understanding nature natural virtueless piety impiety""",
 "V Poetica": """love beloved lover beauty beautiful fair lovely sweet
    charming grace night dark darkness star constellation moon sun dawn dusk
    evening sky heaven cloud wind breeze rain storm thunder snow frost sea
    wave shore river stream spring fountain water fire flame light shadow
    forest wood grove tree leaf branch flower rose lily garden meadow field
    mountain valley cave bird swan dove nightingale eagle wolf lion deer bee
    honey wine feast banquet song sing music lyre pipe dance poem verse poet
    muse dream sleep death die dead grave tomb ghost shade tear weep grief
    sorrow pain wound joy laugh smile kiss heart breast blood fate fortune
    hope fear anger rage summer winter autumn gold golden silver"""
}
LA_THEME_WORDS = {lvl: set(kw.split()) for lvl, kw in LA_THEMES.items()}
# Tie-break order: the fallback bucket (Fundamenta) is considered last.
LA_TIE_ORDER = ["II Grammatica", "IV Philosophica", "III Classica",
                "V Poetica", "I Fundamenta"]
# Function-word parts of speech always belong to Fundamenta.
LA_FUNCTION_POS = {"pron", "prep", "conj", "det", "article", "particle", "num"}
LA_SKIP_POS = {"name", "abbrev", "character", "symbol", "punct", "prefix",
               "suffix", "infix", "interfix", "romanization"}
LA_PER_LEVEL = 200

def la_coreness(d: dict, n_senses: int, has_ipa: bool) -> float:
    """
    Frequency proxy for a Latin lemma. No Latin frequency list exists, so we
    score commonness from signals inside the entry itself. Survival into the
    Romance languages is the strongest of them: everyday Latin words are the
    ones that became French/Spanish/Italian.
    """
    from math import log1p
    return (3.0 * log1p(len(d.get("descendants") or []))
            + 2.0 * log1p(n_senses)
            + 1.5 * log1p(len(d.get("derived") or []))
            + 1.0 * log1p(len(d.get("related") or []))
            + 0.5 * log1p(len(d.get("forms") or []))
            + (0.5 if has_ipa else 0.0))


def _theme_hit(gloss: str, min_hits: int = 1):
    """Best-matching theme for one gloss, or None when nothing clears min_hits."""
    tokens = set(re.findall(r"[a-z]+", gloss.lower()))
    if not tokens:
        return None
    best, best_hits = None, min_hits - 1
    for level in LA_TIE_ORDER:
        hits = len(tokens & LA_THEME_WORDS[level])
        if hits > best_hits:
            best, best_hits = level, hits
    return best


def la_classify(gloss: str, pos: str, wide_gloss: str = ""):
    """
    Route a lemma to a thematic level by keyword hits in its English gloss.
    The primary sense is tried first — it carries the central meaning — and the
    wider gloss (first few senses) is only consulted when that finds nothing.
    Without this, sol "the Sun" matches "god" in a later sense and lands in
    Classica instead of Poetica.
    """
    if pos in LA_FUNCTION_POS:
        return "I Fundamenta"
    # A single keyword hit in a peripheral sense is noise (gas "state of matter"
    # matched Philosophica that way), so the wider gloss must clear two.
    return _theme_hit(gloss) or (_theme_hit(wide_gloss, 2) if wide_gloss else None)


def la_example(senses: list) -> tuple:
    """First usable (Latin text, English translation) pair from the senses."""
    for s in senses:
        for e in (s.get("examples") or []):
            text = (e.get("text") or "").strip()
            eng  = (e.get("english") or e.get("translation") or "").strip()
            if text and eng and len(text) <= 120:
                text = re.sub(r'\s*\[…\]\s*', ' ', text).strip(' "')
                eng  = re.sub(r'\s*\[…\]\s*', ' ', eng).strip(' "')
                if text and eng:
                    return text, eng
    return "", ""


def collect_latin(url: str, existing: set, cache_path: str = "") -> list:
    """
    Full streaming pass over the Latin kaikki dump, collecting every lemma
    candidate with its coreness score and the glosses needed to theme it. A
    full pass (not an early stop) is what makes selection frequency-aware: we
    need to see every candidate before we can keep the most core ones.

    Themes are assigned later, in assign_latin_levels, so the cached pool can
    be re-bucketed after a keyword change without re-downloading 1.2 GB.
    """
    records, seen = [], set(existing)
    total_read = kept = skipped_form = 0

    req = Request(url, headers={"User-Agent": "vocabulum-builder/2.0"})
    with urlopen(req, timeout=180) as resp:
        for raw_line in resp:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            total_read += 1
            if total_read % 50000 == 0:
                print(f"  Scanned {total_read:,} entries, {kept:,} candidates, "
                      f"{skipped_form:,} inflected forms skipped...", flush=True)

            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue

            word = d.get("word", "").strip()
            pos  = d.get("pos", "").lower()
            if not word or len(word) < 2 or pos in LA_SKIP_POS:
                continue
            if word.lower() in seen:
                continue
            if not re.fullmatch(r"[A-Za-zĀĒĪŌŪāēīōūÿĀ-ſ' -]+", word):
                continue

            lemma_senses = [s for s in d.get("senses", []) if not is_form_of(s)]
            if not lemma_senses:
                skipped_form += 1
                continue

            gloss = primary = ""
            for s in lemma_senses:
                raw = (s.get("glosses") or s.get("raw_glosses") or [""])
                raw = raw[0] if raw else ""
                g = clean_gloss(raw)
                if g and not g.startswith("(") and len(g) > 2:
                    gloss, primary = g, raw
                    break
            if not gloss:
                continue

            # The wider gloss (first few senses) is the tiebreak for theming;
            # clean_gloss truncates at the first comma and loses most keywords.
            wide = " ".join((s.get("glosses") or [""])[0]
                            for s in lemma_senses[:3] if s.get("glosses"))
            ipa = next((s.get("ipa", "") for s in (d.get("sounds") or []) if s.get("ipa")), "")
            ex, ex_tr = la_example(lemma_senses)

            records.append({
                "w": word, "pos": pos, "primary": primary, "wide": wide,
                "score": la_coreness(d, len(lemma_senses), bool(ipa)),
                "entry": {"id": "", "w": word, "ph": ipa,
                          "pos": POS_LA.get(pos, pos.capitalize()),
                          "tr": gloss, "ex": ex, "exTr": ex_tr},
            })
            kept += 1
            seen.add(word.lower())

    print(f"  Scanned {total_read:,} total entries")
    print(f"  Skipped {skipped_form:,} inflected-form entries")
    print(f"  Collected {kept:,} lemma candidates")

    if cache_path:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False)
        print(f"  Cached to {cache_path}")
    return records


def assign_latin_levels(records: list, rescue: dict, skip: set) -> dict:
    """
    Bucket cached candidates into the five themes. Words kept from a previous
    run stay in their old level when no theme fits, rather than being swept
    into one catch-all the way the frequency-threshold version swept every
    Latin word into V Poetica.
    """
    by_level = {lvl: {"rescued": [], "new": []} for lvl in LA_LEVEL_NAMES}
    for r in records:
        if r["w"].lower() in skip:
            continue
        is_rescued = r["w"].lower() in rescue
        level = la_classify(r["primary"], r["pos"], r["wide"])
        if level is None:
            if not is_rescued:
                continue
            level = rescue[r["w"].lower()]
        by_level[level]["rescued" if is_rescued else "new"].append(r)
    for lvl in by_level:
        for bucket in ("rescued", "new"):
            by_level[lvl][bucket].sort(key=lambda r: -r["score"])
    return by_level


def resort_latin_existing(vocab_la: dict) -> tuple:
    """
    Split the current Latin data into hand-curated entries (kept in their level,
    they carry cited examples) and previously scraped ones. Scraped inflected
    forms are dropped; the rest become a rescue map {word: original level} to be
    re-derived from kaikki during the streaming pass.
    """
    curated = {lvl: [] for lvl in LA_LEVEL_NAMES}
    rescue  = {}
    dropped = []

    for level, entries in vocab_la.items():
        for e in entries:
            if "_q_" in e.get("id", "") or e.get("ex"):
                curated.setdefault(level, []).append(e)
            elif _FORM_OF_RE.search(e.get("tr", "")):
                dropped.append(e["w"])
            else:
                rescue[e["w"].lower()] = level

    return curated, rescue, dropped


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    langs = {"fr", "de", "la"}
    if "--only" in sys.argv:
        langs = {s.strip().lower() for s in sys.argv[sys.argv.index("--only") + 1].split(",")}
    print(f"Building: {', '.join(sorted(langs))}")

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

    fr_counts_before = {lvl: len(entries) for lvl, entries in vocab_data.get("fr",{}).items()}
    de_counts_before = {lvl: len(entries) for lvl, entries in vocab_de.items()}
    la_counts_before = {lvl: len(entries) for lvl, entries in vocab_la.items()}

    print(f"  Existing: FR={len(fr_existing)}  DE={len(de_existing)}  "
          f"LA={sum(la_counts_before.values())}")

    # ── French ────────────────────────────────────────────────────────────────
    if "fr" in langs:
        print("\n=== FRENCH ===")
        print("Fetching frequency list (top 15,000)...")
        fr_rank = fetch_freq_rank(FREQ_FR, 15000)
        print(f"  {len(fr_rank):,} ranked words")

        fr_targets = {
            "A1 Débutant":       max(0, 600  - fr_counts_before.get("A1 Débutant",0)),
            "A2 Élémentaire":    max(0, 600  - fr_counts_before.get("A2 Élémentaire",0)),
            "B1 Intermédiaire":  max(0, 1300 - fr_counts_before.get("B1 Intermédiaire",0)),
            "B2 Avancé":         max(0, 2499 - fr_counts_before.get("B2 Avancé",0)),
            "C1 Autonome":       max(0, 300  - fr_counts_before.get("C1 Autonome",0)),
        }
        print(f"  Targets (new words needed): {fr_targets}")
        print("Streaming kaikki.org French...")
        fr_new = stream_kaikki(KAIKKI_FR, fr_rank, fr_existing, POS_FR, CEFR_FR,
                               "fr", fr_targets, require_ipa=False)

        fr_merged = {lvl: list(entries) for lvl, entries in vocab_data.get("fr",{}).items()}
        for level, new_entries in fr_new.items():
            fr_merged.setdefault(level, [])
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
    de_merged = vocab_de
    if "de" in langs:
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
            de_merged.setdefault(level, [])
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
    la_merged = vocab_la
    if "la" in langs:
        print("\n=== LATIN ===")
        curated, rescue, dropped = resort_latin_existing(vocab_la)
        print(f"  Curated entries kept in their level:     {sum(len(v) for v in curated.values())}")
        print(f"  Scraped lemmas to re-derive and re-sort: {len(rescue)}")
        print(f"  Inflected forms dropped:                 {len(dropped)}"
              + (f"  e.g. {', '.join(dropped[:8])}" if dropped else ""))

        # Curated entries are written "lemma, genitive" ("pax, pacis") while
        # kaikki words are bare lemmas, so both forms have to be matched or
        # every curated word comes back a second time as its own duplicate.
        la_seen = set()
        for entries in curated.values():
            for e in entries:
                la_seen.add(e["w"].lower())
                la_seen.add(e["w"].split(",")[0].strip().lower())

        if "--cache" in sys.argv and os.path.exists(LA_CACHE):
            print(f"Reusing cached candidate pool: {LA_CACHE}")
            with open(LA_CACHE) as f:
                records = json.load(f)
            print(f"  {len(records):,} candidates")
        else:
            print("Streaming kaikki.org Latin (full pass, ~1.2 GB)...")
            records = collect_latin(KAIKKI_LA, la_seen, LA_CACHE)

        by_level = assign_latin_levels(records, rescue, la_seen)
        found = sum(len(by_level[l]["rescued"]) for l in LA_LEVEL_NAMES)
        print(f"  Re-derived {found} of {len(rescue)} kept lemmas")
        print("  New candidates per theme: "
              + ", ".join(f"{lvl.split()[0]}={len(by_level[lvl]['new'])}"
                          for lvl in LA_LEVEL_NAMES))

        la_merged, stats = {}, {}
        for lvl in LA_LEVEL_NAMES:
            cur  = list(curated.get(lvl, []))
            res  = by_level[lvl]["rescued"]
            room = max(0, LA_PER_LEVEL - len(cur) - len(res))
            new  = by_level[lvl]["new"][:room]
            # Kept words are guaranteed a slot but not a good one: they are
            # ordered against the new candidates by coreness, so a rescued
            # oddity like "encyclopaedia" sinks below genuinely common words.
            rest = sorted(res + new, key=lambda r: -r["score"])
            entries = cur + [r["entry"] for r in rest]
            stats[lvl] = (len(cur), len(res), len(new))
            for i, e in enumerate(entries):
                if not e.get("id"):
                    e["id"] = f"la_{lvl.split()[0].lower()}_{i:04d}"
                e.pop("rank", None)
            la_merged[lvl] = entries

        print("\nLatin results:")
        for lvl in LA_LEVEL_NAMES:
            was = la_counts_before.get(lvl, 0)
            c_, r_, n_ = stats[lvl]
            print(f"  {lvl}: {was} → {len(la_merged[lvl])}  "
                  f"({c_} curated, {r_} re-sorted, {n_} new)")

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
