"""Build names.bin: the names, brands and places list used by the Android app (text/NameIndex.kt).

    python tools/build_names.py <sources-dir> <out names.bin> [--curated tools/names_curated.txt]

Sources (public, downloaded once into <sources-dir>; see tools/fetch_name_sources.sh):
  IN.txt, cities500.txt     GeoNames (CC BY 4.0, https://www.geonames.org): Indian places, world cities
  *.csv                     Wikidata labels (CC0): brands, Indian companies/people/films, apps, products, notable people...
  words_alpha.txt           English words (public domain): used only to NEVER rewrite an ordinary word
  hi_50k.txt                Hindi word frequencies (CC BY-SA 4.0, FrequencyWords): same purpose, transliterated

File layout (little endian):
  header   'LFN2', version, N terms, A curated terms, O ordinary keys, 4 section offsets
  blob     N x [u8 len][ascii canonical spelling]        (the names, in input order)
  exact    N x u32 blob offsets, sorted by key           (key = lowercase letters and digits, no spaces)
  skel     A x u32 blob offsets, sorted by (skeleton, key)    (curated terms only: these may fix near-miss spellings)
  ordblob  O x [u8 len][ascii key]; ordidx: O x u32 offsets sorted   (words that must not be touched)
"""
import csv
import glob
import os
import re
import struct
import sys
import unicodedata


def fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))


OK = re.compile(r"^[A-Za-z][A-Za-z0-9 .'&\-!]{2,38}[A-Za-z0-9!]$")


def key_of(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def skeleton(s: str) -> str:
    """Must stay identical to Names.skeleton in Names.kt."""
    x = re.sub(r"[^a-z]", "", s.lower())
    for a, b in (("ph", "f"), ("ck", "k"), ("kh", "k"), ("gh", "g"), ("sh", "s"), ("ch", "c"), ("th", "t"), ("dh", "d"), ("bh", "b"),
                 ("x", "ks"), ("q", "k")):
        x = x.replace(a, b)
    out = []
    for n, c in enumerate(x):
        m = {"w": "v", "z": "s", "c": "k"}.get(c, c)
        if c == "y" and n > 0:
            continue
        if n > 0 and c in "aeiouh":
            continue
        if out and out[-1] == m:
            continue
        out.append(m)
    return "".join(out)


def main():
    src, out = sys.argv[1], sys.argv[2]
    curated_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "names_curated.txt")
    if "--curated" in sys.argv:
        curated_path = sys.argv[sys.argv.index("--curated") + 1]

    # ---- words that must never be rewritten
    ordinary = set()
    for line in open(os.path.join(src, "words_alpha.txt"), encoding="utf-8"):
        w = line.strip().lower()
        if 3 <= len(w) <= 24:
            ordinary.add(w)
    hi = os.path.join(src, "hi_50k.txt")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from roman_hindi import to_roman                       # same sounds as Hinglish.kt, for the word list
    if os.path.exists(hi):
        for line in open(hi, encoding="utf-8"):
            parts = line.split()
            if parts:
                r = key_of(to_roman(parts[0]))
                if len(r) >= 3:
                    ordinary.add(r)
    ordinary.update(("hai hain nahi nahin kya kaise kahan kyun kyon abhi phir bhi hi toh aur lekin mein par ko ka ki ke se mera meri mujhe "
                     "tum aap hum woh yeh kal aaj theek thik accha bahut thoda zyada ghar kaam baat log paisa paise bhai dost samay baad pehle "
                     "liye saath bilkul zaroor shukriya namaste please thanks okay meeting report office client email phone message call "
                     "time today tomorrow morning evening night week month year good best team work project plan task issue bug fix release "
                     "build test chips snack biscuit juice water milk tea coffee rice dal roti sabzi chai paneer").split())

    terms = {}                  # key -> (canonical, curated)

    def add(t: str, curated: bool):
        t = t.strip()
        if not t:
            return
        f = fold(t)
        if not OK.match(f):
            return
        k = key_of(f)
        if len(k) < 4 or (k in ordinary and not curated):
            return
        old = terms.get(k)
        if old is None or (curated and not old[1]):
            terms[k] = (f, curated)

    for line in open(curated_path, encoding="utf-8"):
        if line.strip() and not line.startswith("#"):
            add(line, True)
    n_cur = len(terms)
    # Wikidata lists that are good enough to fix near-miss spellings
    for name in ("brands", "apps", "products", "companies_in", "given", "cities_in"):
        p = os.path.join(src, name + ".csv")
        if os.path.exists(p):
            for row in csv.reader(open(p, encoding="utf-8")):
                if row and row[0] != "l":
                    add(row[0], True)
    n_a = len(terms)
    # bulk: exact (case-insensitive) matches only
    for fn in ("IN.txt", "cities500.txt"):
        p = os.path.join(src, fn)
        if os.path.exists(p):
            for line in open(p, encoding="utf-8"):
                parts = line.rstrip("\n").split("\t")
                for t in [parts[1], parts[2]] + parts[3].split(","):
                    add(t, False)
    for p in sorted(glob.glob(os.path.join(src, "*.csv"))):
        for row in csv.reader(open(p, encoding="utf-8")):
            if row and row[0] != "l":
                add(row[0], False)
    print(f"terms: {len(terms):,} (curated/fuzzy-capable: {sum(1 for v in terms.values() if v[1]):,}); ordinary words: {len(ordinary):,}")

    ordered = sorted(terms.items())                                  # by key
    blob = bytearray()
    offs = {}
    for k, (canon, _cur) in ordered:
        b = canon.encode("ascii")
        offs[k] = len(blob)
        blob += bytes([len(b)]) + b
    exact = [offs[k] for k, _ in ordered]
    cur_keys = [k for k, (_c, cur) in ordered if cur]
    cur_keys.sort(key=lambda k: (skeleton(terms[k][0]), k))
    skel = [offs[k] for k in cur_keys]
    ords = sorted(ordinary)
    ordblob = bytearray()
    ordoffs = []
    for w in ords:
        ordoffs.append(len(ordblob))
        b = w.encode("ascii", "ignore")
        ordblob += bytes([len(b)]) + b

    hdr = 4 + 4 * 8
    p_blob = hdr
    p_exact = p_blob + len(blob)
    p_skel = p_exact + 4 * len(exact)
    p_ordblob = p_skel + 4 * len(skel)
    p_ordidx = p_ordblob + len(ordblob)
    with open(out, "wb") as f:
        f.write(b"LFN2")
        f.write(struct.pack("<8I", 2, len(exact), len(skel), len(ords), p_exact, p_skel, p_ordblob, p_ordidx))
        f.write(blob)
        f.write(struct.pack(f"<{len(exact)}I", *exact))
        f.write(struct.pack(f"<{len(skel)}I", *skel))
        f.write(ordblob)
        f.write(struct.pack(f"<{len(ordoffs)}I", *ordoffs))
    print(f"wrote {out}: {os.path.getsize(out) / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
