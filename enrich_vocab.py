"""
enrich_vocab.py — repair inflected-form cards and attach example sentences.

Two jobs build_vocab.py does not do:

1. REPAIR. Earlier runs stored inflected forms whose "translation" is a grammar
   description ("veux" → "inflection of vouloir:"), which teaches nothing. The
   word stays — suis, das and ist are worth knowing — but the card gets a real
   meaning taken from its base lemma. The base is usually already in the deck;
   the remainder are looked up once on Wiktionary and cached.

2. EXAMPLES. Cards get an example sentence with an English translation from
   Tatoeba (CC-BY 2.0 FR), preferring short sentences that use the word in the
   form printed on the card.

Run:  python3 enrich_vocab.py --repair
      python3 enrich_vocab.py --examples
      python3 enrich_vocab.py --repair --examples
"""

import bz2, json, os, re, sys, tarfile, time, urllib.parse, urllib.request

BASE     = "/Users/lucasxie/vocabulum"
OUT_DATA = f"{BASE}/vocab-data.json"
OUT_DE   = f"{BASE}/vocab-de.js"
OUT_LA   = f"{BASE}/vocab-la.js"
TATOEBA  = os.environ.get("TATOEBA_DIR", os.path.expanduser("~/.cache/vocabulum/tatoeba"))
WIKT_CACHE = os.path.expanduser("~/.cache/vocabulum/wiktionary-defs.json")

LANG_NAME = {"fr": "French", "de": "German", "es": "Spanish", "la": "Latin"}
TATO_CODE = {"fr": "fra", "de": "deu", "es": "spa", "la": "lat"}

_FORM_OF_RE = re.compile(
    r'\b(?:inflection|form|plural|singular|genitive|dative|accusative|ablative'
    r'|vocative|nominative|comparative|superlative|participle|gerund|supine'
    r'|imperative|subjunctive|indicative|infinitive|first-person|second-person'
    r'|third-person)\b.{0,40}\bof\b', re.IGNORECASE)

_BASE_RE  = re.compile(r'\bof\s+([^\s:,(“"]+)')
_PAREN_RE = re.compile(r'[(（]\s*[“"]([^”"]+)[”"]\s*[)）]')

# Long grammar descriptions are compressed so the meaning stays readable.
_ABBREV = [
    (r'\bfirst-person\b', '1'), (r'\bsecond-person\b', '2'), (r'\bthird-person\b', '3'),
    (r'\bsingular\b', 'sg'), (r'\bplural\b', 'pl'),
    (r'\bpresent\b', 'pres'), (r'\bpreterite\b', 'pret'), (r'\bimperfect\b', 'imperf'),
    (r'\bfuture\b', 'fut'), (r'\bconditional\b', 'cond'),
    (r'\bindicative\b', 'ind'), (r'\bsubjunctive\b', 'subj'), (r'\bimperative\b', 'imper'),
    (r'\bparticiple\b', 'part'), (r'\bmasculine\b', 'm'), (r'\bfeminine\b', 'f'),
    (r'\bneuter\b', 'n'), (r'\bnominative\b', 'nom'), (r'\baccusative\b', 'acc'),
    (r'\bgenitive\b', 'gen'), (r'\bdative\b', 'dat'), (r'\bcomparative\b', 'compar'),
    (r'\bsuperlative\b', 'superl'), (r'\bdegree\b', ''), (r'\binflection\b', 'form'),
    (r'\balternative form\b', 'variant'),
]

# "... of der: the" carries its own meaning after the colon — worth keeping when
# the base lemma has no card of its own.
_COLON_GLOSS_RE = re.compile(r'\bof\s+[^\s:]+\s*:\s*(.+)$')


def compact_grammar(tr: str) -> str:
    """'first-person singular present indicative of être' -> '1sg pres ind of être'."""
    g = _COLON_GLOSS_RE.sub(lambda m: m.group(0).split(':')[0], tr.strip())
    g = _PAREN_RE.sub('', g).rstrip(':').strip()
    for pat, rep in _ABBREV:
        g = re.sub(pat, rep, g, flags=re.IGNORECASE)
    g = re.sub(r'\b([123]) (sg|pl)\b', r'\1\2', g)      # "1 sg" -> "1sg"
    g = re.sub(r'\s{2,}', ' ', g).strip(' ,;:')
    return g


def load_all():
    with open(OUT_DATA) as f:
        data = json.load(f)
    de = json.loads(re.search(r'const VOCAB_DE\s*=\s*(\{.*\});?\s*$',
                              open(OUT_DE).read(), re.DOTALL).group(1))
    la = json.loads(re.search(r'const VOCAB_LA\s*=\s*(\{.*\});?\s*$',
                              open(OUT_LA).read(), re.DOTALL).group(1))
    return data, de, la


def save_all(data, de, la):
    with open(OUT_DATA, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    with open(f"{BASE}/vocab-data.js", "w", encoding="utf-8") as f:
        f.write("const VOCAB_DATA = " + json.dumps(data, ensure_ascii=False,
                                                   separators=(",", ":")) + ";")
    with open(OUT_DE, "w", encoding="utf-8") as f:
        f.write("const VOCAB_DE = " + json.dumps(de, ensure_ascii=False,
                                                 separators=(",", ":")) + ";")
    with open(OUT_LA, "w", encoding="utf-8") as f:
        f.write("const VOCAB_LA = " + json.dumps(la, ensure_ascii=False,
                                                 separators=(",", ":")) + ";")


# ── 1. Repair inflected-form cards ────────────────────────────────────────────

def wiktionary_def(word: str, language: str, cache: dict) -> str:
    """First English definition of `word` in `language`, cached on disk."""
    key = f"{language}:{word}"
    if key in cache:
        return cache[key]
    url = ("https://en.wiktionary.org/api/rest_v1/page/definition/"
           + urllib.parse.quote(word, safe=""))
    req = urllib.request.Request(url, headers={"User-Agent": "vocabulum/1.0"})
    out, failed = "", False
    for attempt in range(3):
        try:
            payload = json.load(urllib.request.urlopen(req, timeout=20))
        except Exception:
            # Wikimedia throttles bursts. Back off and retry rather than
            # caching the failure as "this word has no definition".
            failed = True
            time.sleep(2 * (attempt + 1))
            continue
        failed = False
        for blocks in payload.values():
            for b in blocks:
                if b.get("language", "").lower() != language.lower():
                    continue
                for d in b.get("definitions", []):
                    t = re.sub(r'<[^>]+>', '', d.get("definition", ""))
                    t = re.split(r'\.mw-parser-output', t)[0]      # strip leaked CSS
                    t = re.sub(r'\s{2,}', ' ', t).strip(' .;,')
                    if t and len(t) > 2 and not _FORM_OF_RE.search(t):
                        out = t[:70].strip()
                        break
                if out:
                    break
            if out:
                break
        break
    if not failed:
        cache[key] = out          # only a real answer is worth remembering
    time.sleep(0.4)               # be polite to the API
    return out


def repair_forms(lists: dict, lang: str, cache: dict) -> dict:
    """
    Give every inflected-form card a real meaning, taken from its base lemma.
    The grammar description is kept, compressed, after the meaning:
        veux -> "to want · 1sg pres ind of vouloir"
    """
    language = LANG_NAME[lang]
    index = {}
    for entries in lists.values():
        for e in entries:
            index.setdefault(e["w"].lower(), e)

    stats = {"from_deck": 0, "from_paren": 0, "from_wiktionary": 0, "unresolved": 0}
    pending = []

    for entries in lists.values():
        for e in entries:
            tr = e.get("tr", "")
            if " · " in tr or not _FORM_OF_RE.search(tr):
                continue          # already carries a meaning
            m = _BASE_RE.search(tr)
            base = m.group(1).strip('.,;:"“”').lower() if m else ""
            grammar = compact_grammar(tr)

            target = index.get(base)
            meaning = ""
            if target is not None and target is not e and not _FORM_OF_RE.search(target.get("tr", "")):
                meaning = target["tr"]
                stats["from_deck"] += 1
            else:
                paren = _PAREN_RE.search(tr) or _COLON_GLOSS_RE.search(tr)
                if paren:
                    meaning = paren.group(1).strip(' "“”()')
                    stats["from_paren"] += 1
                elif base:
                    pending.append((e, base, grammar))
                    continue
            if meaning:
                e["tr"] = f"{meaning} · {grammar}"
            else:
                stats["unresolved"] += 1

    if pending:
        uniq = sorted({b for _, b, _ in pending})
        print(f"  Looking up {len(uniq)} base lemmas on Wiktionary...", flush=True)
        for i, b in enumerate(uniq, 1):
            wiktionary_def(b, language, cache)
            if i % 25 == 0:
                print(f"    {i}/{len(uniq)}", flush=True)
        for e, base, grammar in pending:
            meaning = cache.get(f"{language}:{base}", "")
            if meaning:
                e["tr"] = f"{meaning} · {grammar}"
                stats["from_wiktionary"] += 1
            else:
                e["tr"] = grammar
                stats["unresolved"] += 1

    return stats


# ── 2. Example sentences from Tatoeba ─────────────────────────────────────────

_ARTICLE_RE = re.compile(r"^(der|die|das|le|la|les|l'|el|los|las|un|une|ein|eine)\s+", re.I)
_TOKEN_RE   = re.compile(r"[^\W\d_]+", re.UNICODE)


def card_key(word: str) -> str:
    """The single word a card is really about: no article, no gloss tail."""
    w = _ARTICLE_RE.sub("", word.strip())
    w = w.split(",")[0].split(";")[0].split("(")[0].strip()
    return w.lower()


def load_tatoeba_pairs(langs: list) -> dict:
    """
    {lang: [(foreign_sentence, english_sentence), ...]} from the Tatoeba export.
    One pass over links.csv covers every language at once — it is by far the
    biggest file, so scanning it per language would dominate the runtime.
    """
    want = {TATO_CODE[l]: l for l in langs}
    sent = {}                                    # sentence id -> (lang, text)
    for code, lang in want.items():
        path = os.path.join(TATOEBA, f"{code}.tsv.bz2")
        n = 0
        with bz2.open(path, "rt", encoding="utf-8") as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) >= 3:
                    sent[parts[0]] = (lang, parts[2])
                    n += 1
        print(f"  {lang}: {n:,} Tatoeba sentences", flush=True)

    print("  Scanning links.csv...", flush=True)
    need_eng, link = {}, {}                      # eng id -> text, foreign id -> eng id
    with tarfile.open(os.path.join(TATOEBA, "links.tar.bz2"), "r:bz2") as tar:
        member = tar.extractfile("links.csv")
        for raw in member:
            a, _, b = raw.decode("utf-8", "replace").partition("\t")
            b = b.strip()
            if a in sent and a not in link:
                link[a] = b
                need_eng[b] = None
            elif b in sent and b not in link:
                link[b] = a
                need_eng[a] = None
    print(f"  {len(link):,} linked sentences", flush=True)

    with bz2.open(os.path.join(TATOEBA, "eng.tsv.bz2"), "rt", encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 3 and parts[0] in need_eng:
                need_eng[parts[0]] = parts[2]

    pairs = {l: [] for l in langs}
    for sid, (lang, text) in sent.items():
        eng = need_eng.get(link.get(sid, ""))
        if eng:
            pairs[lang].append((text, eng))
    for lang in langs:
        print(f"  {lang}: {len(pairs[lang]):,} sentences with an English translation")
    return pairs


def index_sentences(pairs: list, targets: set) -> tuple:
    """
    Inverted index token -> sentence ids, plus a 4-letter stem index. The stem
    index is what catches lemma cards: "parler" never appears literally in a
    sentence, but "parle" and "parlons" share its stem.
    """
    exact, stem = {}, {}
    stems = {t[:4] for t in targets if len(t) >= 5}
    for i, (foreign, _) in enumerate(pairs):
        for tok in {m.group(0).lower() for m in _TOKEN_RE.finditer(foreign)}:
            if tok in targets:
                exact.setdefault(tok, []).append(i)
            if len(tok) >= 4 and tok[:4] in stems:
                stem.setdefault(tok[:4], []).append(i)
    return exact, stem


def pick_sentence(cands: list, pairs: list, max_words: int = 14):
    """Shortest natural-looking sentence that still says something."""
    best, best_score = None, 1e9
    for i in cands[:400]:
        foreign, eng = pairs[i]
        nw = len(foreign.split())
        if nw < 3 or nw > max_words or len(foreign) > 110:
            continue
        # 6-9 words reads best on a flashcard; longer or shorter is penalised.
        score = abs(nw - 7) + len(eng) / 60.0
        if score < best_score:
            best, best_score = (foreign, eng), score
    return best


def attach_examples(lists: dict, lang: str, pairs: list, overwrite: bool = False) -> dict:
    cards = [e for entries in lists.values() for e in entries]
    todo  = [e for e in cards if overwrite or not e.get("ex")]
    targets = {card_key(e["w"]) for e in todo}
    targets.discard("")
    print(f"  Indexing {len(pairs):,} sentences for {len(targets):,} words...", flush=True)
    exact, stem = index_sentences(pairs, targets)

    stats = {"exact": 0, "stem": 0, "none": 0}
    for e in todo:
        key = card_key(e["w"])
        hit = pick_sentence(exact.get(key, []), pairs)
        if hit:
            stats["exact"] += 1
        elif len(key) >= 5:
            hit = pick_sentence(stem.get(key[:4], []), pairs)
            if hit:
                stats["stem"] += 1
        if hit:
            e["ex"], e["exTr"] = hit
        else:
            stats["none"] += 1
    return stats


# ── 3. Unique card ids ────────────────────────────────────────────────────────

def fix_duplicate_ids(lists: dict, lang: str) -> int:
    """
    build_vocab.py numbered ids from a counter that restarts each run, so words
    added in different runs collide. The app keys spaced-repetition progress on
    the id (state.srs[id]), which means two words share one review schedule.

    The first card to claim an id keeps it — that preserves the review history
    already stored against it — and later claimants get a fresh one.
    """
    used, renamed = set(), 0
    counter = 0
    for entries in lists.values():
        for e in entries:
            if e["id"] not in used:
                used.add(e["id"])
                continue
            while True:
                counter += 1
                new = f"{lang}_x{counter:05d}"
                if new not in used:
                    break
            e["id"] = new
            used.add(new)
            renamed += 1
    return renamed


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    do_repair   = "--repair" in sys.argv
    do_ids      = "--fix-ids" in sys.argv
    do_examples = "--examples" in sys.argv
    overwrite   = "--overwrite-examples" in sys.argv
    if not (do_repair or do_examples or do_ids):
        print(__doc__)
        return

    data, de, la = load_all()
    lists = {"fr": data["fr"], "es": data["es"], "de": de, "la": la}

    if do_repair:
        print("=== REPAIR inflected-form cards ===")
        cache = {}
        if os.path.exists(WIKT_CACHE):
            with open(WIKT_CACHE) as f:
                cache = json.load(f)
        for lang in ("fr", "de", "es", "la"):
            n = sum(1 for v in lists[lang].values() for e in v
                    if _FORM_OF_RE.search(e.get("tr", "")))
            if not n:
                print(f"  {lang.upper()}: nothing to repair")
                continue
            print(f"  {lang.upper()}: {n} cards")
            s = repair_forms(lists[lang], lang, cache)
            print(f"     meaning from a card already in the deck: {s['from_deck']}")
            print(f"     meaning from the gloss's own parenthetical: {s['from_paren']}")
            print(f"     meaning looked up on Wiktionary: {s['from_wiktionary']}")
            print(f"     still without a meaning: {s['unresolved']}")
        os.makedirs(os.path.dirname(WIKT_CACHE), exist_ok=True)
        with open(WIKT_CACHE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)

    if do_ids:
        print("\n=== FIX duplicate card ids ===")
        for lang in ("fr", "de", "es", "la"):
            n = fix_duplicate_ids(lists[lang], lang)
            print(f"  {lang.upper()}: {n} ids reassigned")

    if do_examples:
        print("\n=== EXAMPLE sentences (Tatoeba) ===")
        langs = ["fr", "de", "es", "la"]
        pairs = load_tatoeba_pairs(langs)
        for lang in langs:
            have = sum(1 for v in lists[lang].values() for e in v if e.get("ex"))
            total = sum(len(v) for v in lists[lang].values())
            print(f"\n  {lang.upper()}: {have}/{total} already have one")
            s = attach_examples(lists[lang], lang, pairs[lang], overwrite)
            now = sum(1 for v in lists[lang].values() for e in v if e.get("ex"))
            print(f"     matched on the exact word: {s['exact']}")
            print(f"     matched on a shared stem:  {s['stem']}")
            print(f"     no sentence found:         {s['none']}")
            print(f"     coverage now: {now}/{total} ({now*100//max(total,1)}%)")

    print("\nWriting files...")
    save_all(data, de, la)
    print("Done.")


if __name__ == "__main__":
    main()
