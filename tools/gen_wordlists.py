#!/usr/bin/env python3
"""Twordle word-list regeneration pipeline.

Stage 1 (this run): build the candidate pool from the current index.html lists,
attach frequency (wordfreq Zipf) + a bare-plural-noun flag (NLTK WordNet), and
emit:
  - tools/pool.json     : full candidate dataset [{w, z, plural}]
  - tools/chunks/NNN.txt : answer-candidate words, chunked, for Haiku agents
Later stages aggregate agent classifications + manual overrides and splice the
final ANSWERS / VALID_GUESSES_EXTRA back into index.html.

Run: tools/.venv/bin/python tools/gen_wordlists.py pool
"""
import re, json, sys
from pathlib import Path
import wordfreq
from nltk.stem import WordNetLemmatizer
from nltk.corpus import wordnet as wn

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
HTML = ROOT / "index.html"

# Answer-candidate frequency floor. Original used Zipf>=2.5 (stripped paean=2.41);
# 2.0 keeps the known-but-rare tier for the LLM to judge. Sub-floor known words
# come in via the manual allowlist.
ANSWER_FLOOR = 2.0
# Second answer-curation tier. The original answer pass only judged Zipf>=2.0;
# known-but-just-sub-floor words (uvula=1.98) got stranded as guess-only. The
# `ansband` stage runs the same 3-way answer pass over the [ANSWER_BAND_FLOOR,
# ANSWER_FLOOR) band's band-KEPT words so the genuinely-known ones can be
# promoted to answers while the obscure-but-real majority stay guesses.
ANSWER_BAND_FLOOR = 1.25
# Guess floor: cut the zero-frequency Scrabble junk (crwth/aahed/glout = 0.0)
# while keeping real-but-uncommon words someone might actually try. The freq data
# has a gap — nothing sits in (0, 1.0) — so 1.0 == "drop the no-usage-data tier".
GUESS_FLOOR = 1.0
CHUNK = 300

def extract_list(name, text):
    m = re.search(name + r"\s*=\s*\[(.*?)\];", text, re.S)
    if not m:
        sys.exit("could not find " + name)
    return re.findall(r'"([a-z]{5})"', m.group(1))

L = WordNetLemmatizer()
def is_plural_noun(w):
    """Bare plural of a noun: lemmatizing as a noun changes the word and the
    singular is a real noun. Leaves 'being'/'brass' alone (lemma == word)."""
    lem = L.lemmatize(w, "n")
    if lem == w:
        return False
    return bool(wn.synsets(lem, pos="n"))

def is_verb_inflection(w):
    """Bare -s/-ed inflection of a verb (gnaws, flays, hexed). Same 'grammatical
    transformation' category as a bare plural noun — fine as a guess, but kept OUT
    of answers (esp. the obscure ansband tier). Leaves base verbs/adjectives alone."""
    if not (w.endswith("s") or w.endswith("ed")):
        return False
    lem = L.lemmatize(w, "v")
    return lem != w and bool(wn.synsets(lem, pos="v"))

def main():
    text = HTML.read_text()
    answers = extract_list("ANSWERS", text)
    extra = extract_list("VALID_GUESSES_EXTRA", text)
    pool = sorted(set(answers) | set(extra))

    records = []
    for w in pool:
        records.append({"w": w, "z": round(wordfreq.zipf_frequency(w, "en"), 2),
                        "plural": is_plural_noun(w)})
    (TOOLS / "pool.json").write_text(json.dumps(records))

    # Answer candidates = freq>=floor and not a bare plural -> the LLM judges these.
    cands = [r["w"] for r in records if r["z"] >= ANSWER_FLOOR and not r["plural"]]
    cdir = TOOLS / "chunks"
    cdir.mkdir(exist_ok=True)
    for f in cdir.glob("*.txt"):
        f.unlink()
    for i in range(0, len(cands), CHUNK):
        idx = i // CHUNK
        (cdir / f"{idx:03d}.txt").write_text("\n".join(cands[i:i + CHUNK]))

    n_plural = sum(r["plural"] for r in records)
    print(f"pool={len(pool)} src_answers={len(answers)} src_extra={len(extra)} "
          f"plurals_flagged={n_plural} answer_cands={len(cands)} "
          f"chunks={(len(cands)+CHUNK-1)//CHUNK} "
          f"guess_floor_keep={sum(r['z']>=GUESS_FLOOR for r in records)}")

def band():
    """Stage 2b: chunk the unvetted guess-only frequency band for a second LLM pass.

    The answer pass only saw words with Zipf >= ANSWER_FLOOR; everything in
    [GUESS_FLOOR, ANSWER_FLOOR) entered the guess list purely on frequency and was
    never judged. That band is ~half Scrabble-junk (belga/quoad/zonda) interleaved
    with real-but-uncommon words (filch/inure/prigs) that frequency can't separate.
    This emits those words as chunks for agents to keep/reject -> tools/bandresults/.
    """
    text = HTML.read_text()
    guesses = extract_list("VALID_GUESSES_EXTRA", text)
    words = sorted(w for w in guesses
                   if GUESS_FLOOR <= wordfreq.zipf_frequency(w, "en") < ANSWER_FLOOR)
    bdir = TOOLS / "bandchunks"
    bdir.mkdir(exist_ok=True)
    for f in bdir.glob("*.txt"):
        f.unlink()
    for i in range(0, len(words), CHUNK):
        (bdir / f"{i // CHUNK:03d}.txt").write_text("\n".join(words[i:i + CHUNK]))
    print(f"band[{GUESS_FLOOR},{ANSWER_FLOOR})={len(words)} "
          f"chunks={(len(words)+CHUNK-1)//CHUNK}")

def ansband():
    """Stage 4b: chunk the [ANSWER_BAND_FLOOR, ANSWER_FLOOR) band for a SECOND
    answer-curation pass (3-way answer/guess/reject), so well-known words that
    just missed the 2.0 answer floor (uvula=1.98) can be promoted to answers.

    Candidates = currently-VALID, non-plural words in that band (i.e. the band
    pass already kept them as real guesses; we now ask whether they're also fair
    *answers*). Junk the band rejected is already gone, so we don't re-judge it.
    Writes chunks -> tools/ansbandchunks/; agents -> tools/ansbandresults/.
    """
    text = HTML.read_text()
    valid = set(extract_list("ANSWERS", text)) | set(extract_list("VALID_GUESSES_EXTRA", text))
    words = sorted(w for w in valid
                   if ANSWER_BAND_FLOOR <= wordfreq.zipf_frequency(w, "en") < ANSWER_FLOOR
                   and not is_plural_noun(w) and not is_verb_inflection(w))
    adir = TOOLS / "ansbandchunks"
    adir.mkdir(exist_ok=True)
    for f in adir.glob("*.txt"):
        f.unlink()
    for i in range(0, len(words), CHUNK):
        (adir / f"{i // CHUNK:03d}.txt").write_text("\n".join(words[i:i + CHUNK]))
    print(f"ansband[{ANSWER_BAND_FLOOR},{ANSWER_FLOOR})={len(words)} "
          f"chunks={(len(words)+CHUNK-1)//CHUNK}")

def read_manual(name):
    f = TOOLS / name
    if not f.exists():
        return set()
    out = set()
    for line in f.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        w = line.split()[0].lower()
        if len(w) == 5 and w.isalpha():
            out.add(w)
    return out

def emit():
    """Aggregate agent classifications + manual overrides -> answers.txt / guesses.txt."""
    records = json.loads((TOOLS / "pool.json").read_text())
    z = {r["w"]: r["z"] for r in records}
    label = {}
    for f in sorted((TOOLS / "results").glob("*.csv")):
        for line in f.read_text().splitlines():
            parts = line.strip().split(",")
            if len(parts) == 2 and len(parts[0].strip()) == 5 and parts[1].strip() in ("answer", "guess", "reject"):
                label[parts[0].strip().lower()] = parts[1].strip()
    cands = [r["w"] for r in records if r["z"] >= ANSWER_FLOOR and not r["plural"]]
    missing = [w for w in cands if w not in label]
    for w in missing:          # dropped by an agent -> safe default: a valid guess, not an answer
        label[w] = "guess"

    # Second-pass band vetting (tools/bandresults/*.csv): keep/reject for the
    # [GUESS_FLOOR, ANSWER_FLOOR) guess-only tier the answer pass never saw.
    band_rejects = set()
    bres = TOOLS / "bandresults"
    if bres.exists():
        for f in sorted(bres.glob("*.csv")):
            for line in f.read_text().splitlines():
                parts = line.strip().split(",")
                if len(parts) == 2 and len(parts[0].strip()) == 5 and parts[1].strip() == "reject":
                    band_rejects.add(parts[0].strip().lower())

    # Second answer-curation pass (tools/ansbandresults/*.csv): promote the
    # well-known [ANSWER_BAND_FLOOR, ANSWER_FLOOR) words to answers. Only "answer"
    # matters here -- non-answers stay the valid guesses they already are.
    ansband_answers = set()
    abres = TOOLS / "ansbandresults"
    if abres.exists():
        for f in sorted(abres.glob("*.csv")):
            for line in f.read_text().splitlines():
                parts = line.strip().split(",")
                if len(parts) == 2 and len(parts[0].strip()) == 5 and parts[1].strip() == "answer":
                    w = parts[0].strip().lower()
                    # Honor the no-grammatical-transformation rule for the obscure band:
                    # an agent may label a verb inflection "answer"; keep it a guess instead.
                    if not is_verb_inflection(w):
                        ansband_answers.add(w)

    allow, deny = read_manual("manual_allow.txt"), read_manual("manual_deny.txt")
    # keep_guess: force a band word back IN as a legal guess (rescue an over-zealous
    # band reject) WITHOUT promoting it to an answer -- unlike allow, which makes answers.
    keep_guess = read_manual("manual_keep_guess.txt")
    rejects = (({w for w, l in label.items() if l == "reject"} | band_rejects) - allow) - keep_guess
    answers = ({w for w, l in label.items() if l == "answer"} | ansband_answers | allow) - deny
    valid = {w for w, zz in z.items() if zz >= GUESS_FLOOR}   # guess floor: cut Scrabble junk
    valid = (valid - rejects - deny) | answers | allow         # answers/allow are always valid guesses
    guess_only = sorted(valid - answers)
    answers = sorted(answers)

    (TOOLS / "answers.txt").write_text("\n".join(answers))
    (TOOLS / "guesses.txt").write_text("\n".join(guess_only))
    print(f"ANSWERS={len(answers)} GUESS_ONLY={len(guess_only)} ALL_VALID={len(set(answers)|set(guess_only))} "
          f"missing_defaulted={len(missing)} rejects={len(rejects)} band_rejects={len(band_rejects)} "
          f"ansband_promoted={len(ansband_answers)} allow={len(allow)} keep_guess={len(keep_guess)} deny={len(deny)}")

def splice():
    """Replace the ANSWERS and VALID_GUESSES_EXTRA constants in index.html with
    the regenerated lists (VALID_GUESSES_EXTRA = guess-only, disjoint from answers;
    ALL_VALID = the union, computed in-app)."""
    answers = sorted(set(open(TOOLS / "answers.txt").read().split()))
    guesses = sorted(set(open(TOOLS / "guesses.txt").read().split()))
    js = lambda ws: "[" + ",".join('"%s"' % w for w in ws) + "]"
    text = HTML.read_text()
    text, n1 = re.subn(r"const ANSWERS\s*=\s*\[.*?\];",
                       "const ANSWERS = " + js(answers) + ";", text, count=1, flags=re.S)
    text, n2 = re.subn(r"const VALID_GUESSES_EXTRA\s*=\s*\[.*?\];",
                       "const VALID_GUESSES_EXTRA = " + js(guesses) + ";", text, count=1, flags=re.S)
    if n1 != 1 or n2 != 1:
        sys.exit(f"splice failed: ANSWERS={n1} VALID_GUESSES_EXTRA={n2}")
    HTML.write_text(text)
    print(f"spliced ANSWERS={len(answers)} VALID_GUESSES_EXTRA={len(guesses)} all_valid={len(set(answers)|set(guesses))}")

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "pool"
    {"pool": main, "band": band, "ansband": ansband, "emit": emit, "splice": splice}[mode]()
