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

    allow, deny = read_manual("manual_allow.txt"), read_manual("manual_deny.txt")
    rejects = {w for w, l in label.items() if l == "reject"} - allow
    answers = ({w for w, l in label.items() if l == "answer"} | allow) - deny
    valid = {w for w, zz in z.items() if zz >= GUESS_FLOOR}   # guess floor: cut Scrabble junk
    valid = (valid - rejects - deny) | answers | allow         # answers/allow are always valid guesses
    guess_only = sorted(valid - answers)
    answers = sorted(answers)

    (TOOLS / "answers.txt").write_text("\n".join(answers))
    (TOOLS / "guesses.txt").write_text("\n".join(guess_only))
    print(f"ANSWERS={len(answers)} GUESS_ONLY={len(guess_only)} ALL_VALID={len(set(answers)|set(guess_only))} "
          f"missing_defaulted={len(missing)} rejects={len(rejects)} allow={len(allow)} deny={len(deny)}")

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
    {"pool": main, "emit": emit, "splice": splice}[mode]()
