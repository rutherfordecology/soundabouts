"""Build data/tracks.js, the dated pieces behind Soundabouts' guess-the-year game.

Two kinds of piece, both from Wikidata and both matched to Deezer tracks:

- Classical works written before 1950: anything with a composer and a date
  (inception, else the earlier of first performance and publication) that
  falls inside the composer's lifetime. National anthems are left out.
- Songs from 1900 on: singles and songs with a single performer and a
  publication date, where that performer was around to perform it that year
  (otherwise it is a cover carrying the original's date).

A set number is kept per decade for classical works and per year for songs, so
no era or artist swamps the rest. Classical works are taken in order of how
many Wikipedia articles they have. Songs are taken from the best-known by that
measure too, but then in order of Deezer's own popularity rank, because
article counts alone favour Eurovision entries over hits. A piece is only kept
if Deezer has a track whose title and artist or composer match, and for songs
only if Deezer's release is not older than the year Wikidata gives.

Queries go to QLever's Wikidata mirror, which answers these in seconds where
the official query service times out or throttles.

Re-run to refresh:

    python build/build_tracks.py
"""
import csv
import io
import json
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "tracks.js"
CACHE = Path(__file__).resolve().parent / "cache"
UA = "soundabouts-data-build/0.4 (music geography game)"
QLEVER = "https://qlever.dev/api/wikidata"

MIN_LINKS = 8           # Wikipedia articles a piece needs to be considered
CLASSICAL_BEFORE = 1950
SONGS_FROM = 1900
PER_DECADE = 15         # classical works kept per decade
PER_COMPOSER = 3        # ...and per composer within a decade
PER_YEAR = 8            # songs kept per year from 1950
PER_EARLY_DECADE = 12   # songs kept per decade before 1950
PER_ARTIST = 5          # songs kept per artist overall
TRIES = 4               # classical candidates tried per place to fill
SONG_POOL = 40          # best-known songs looked up per year or early decade
CLASSICAL_TRACKS = 4    # recordings kept per classical work
SONG_TRACKS = 2

PREFIXES = """PREFIX wd: <http://www.wikidata.org/entity/>
PREFIX wdt: <http://www.wikidata.org/prop/direct/>
PREFIX wikibase: <http://wikiba.se/ontology#>
PREFIX schema: <http://schema.org/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
"""
# QLever only compares ?links as a number once it has been through arithmetic.
ENWIKI = """?{0}art schema:about ?{0} . ?{0}art schema:isPartOf <https://en.wikipedia.org/> .
  ?{0}art schema:name ?{0}title ."""
SONGS = f"""SELECT ?s ?sLabel ?links (MIN(YEAR(?d)) AS ?year)
  (SAMPLE(?pLabel) AS ?artist) (SAMPLE(?ptitle) AS ?wiki) (SAMPLE(?stitle) AS ?swiki)
  (COUNT(DISTINCT ?p) AS ?np)
  (MIN(YEAR(?b)) AS ?born) (MIN(YEAR(?f)) AS ?formed) (MIN(YEAR(?dd)) AS ?died)
WHERE {{
  VALUES ?type {{ wd:Q134556 wd:Q7366 wd:Q105543609 }}
  ?s wdt:P31 ?type .
  ?s wikibase:sitelinks ?links .
  FILTER(?links + 0 >= {MIN_LINKS})
  ?s wdt:P175 ?p .
  ?s wdt:P577 ?d .
  OPTIONAL {{ ?p wdt:P569 ?b }}
  OPTIONAL {{ ?p wdt:P571 ?f }}
  OPTIONAL {{ ?p wdt:P570 ?dd }}
  {ENWIKI.format("p")}
  ?p rdfs:label ?pLabel .
  FILTER(LANG(?pLabel) = "en")
  ?s rdfs:label ?sLabel .
  FILTER(LANG(?sLabel) = "en")
  OPTIONAL {{ {ENWIKI.format("s")} }}
}} GROUP BY ?s ?sLabel ?links"""
CLASSICAL = f"""SELECT ?s ?sLabel ?links (MIN(YEAR(?made)) AS ?inception)
  (MIN(YEAR(?perf)) AS ?premiere) (MIN(YEAR(?pub)) AS ?published)
  (SAMPLE(?pLabel) AS ?artist) (SAMPLE(?ptitle) AS ?wiki) (SAMPLE(?stitle) AS ?swiki)
  (SAMPLE(?native) AS ?title) (COUNT(DISTINCT ?p) AS ?np)
  (MIN(YEAR(?b)) AS ?born) (MIN(YEAR(?dd)) AS ?died)
  (GROUP_CONCAT(DISTINCT ?tl; separator="|") AS ?types)
WHERE {{
  {{ SELECT DISTINCT ?type WHERE {{
      VALUES ?root {{ wd:Q2188189 wd:Q105543609 wd:Q207628 }} ?type wdt:P279* ?root }} }}
  ?s wdt:P31 ?type .
  ?type rdfs:label ?tl .
  FILTER(LANG(?tl) = "en")
  ?s wdt:P86 ?p .
  ?s wikibase:sitelinks ?links .
  FILTER(?links + 0 >= {MIN_LINKS})
  ?p wdt:P569 ?b .
  FILTER(YEAR(?b) < {CLASSICAL_BEFORE - 30})
  OPTIONAL {{ ?p wdt:P570 ?dd }}
  OPTIONAL {{ ?s wdt:P571 ?made }}
  OPTIONAL {{ ?s wdt:P1191 ?perf }}
  OPTIONAL {{ ?s wdt:P577 ?pub }}
  OPTIONAL {{ ?s wdt:P1476 ?native }}
  {ENWIKI.format("p")}
  ?p rdfs:label ?pLabel .
  FILTER(LANG(?pLabel) = "en")
  ?s rdfs:label ?sLabel .
  FILTER(LANG(?sLabel) = "en")
  OPTIONAL {{ {ENWIKI.format("s")} }}
}} GROUP BY ?s ?sLabel ?links"""

# Words a Deezer title may add to a song's name and still be the same recording.
SAME_RECORDING = {"remaster", "remastered", "version", "single", "album", "mono", "stereo",
                  "original", "edit", "radio", "mix", "digital", "the", "from", "soundtrack",
                  "motion", "picture", "explicit", "clean", "main", "lp"}


def http_get(url, data=None, headers=None, tries=5):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **(headers or {})})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                return r.read().decode("utf-8")
        except Exception as e:  # noqa: BLE001 - retry anything, report at the end
            if attempt == tries - 1:
                raise
            wait = 5 * (attempt + 1)
            print(f"   retry in {wait}s ({e})", file=sys.stderr, flush=True)
            time.sleep(wait)


def sparql(query, cache_key):
    f = CACHE / f"{cache_key}.csv"
    if not f.exists():
        CACHE.mkdir(exist_ok=True)
        text = http_get(QLEVER, urllib.parse.urlencode({"query": PREFIXES + query}).encode(),
                        {"Accept": "text/csv"})
        f.write_text(text, encoding="utf-8", newline="")
    return list(csv.DictReader(io.StringIO(f.read_text(encoding="utf-8"))))


def fold(text):
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def qid(uri):
    return uri.rsplit("/", 1)[-1]


class Deezer:
    """Deezer's public API, with every answer cached so a re-run is offline."""

    def __init__(self):
        self.file = CACHE / "deezer_tracks.json"
        self.cache = json.loads(self.file.read_text(encoding="utf-8")) if self.file.exists() else {}
        self.fresh = 0

    def get(self, path):
        if path not in self.cache:
            for _ in range(6):
                time.sleep(0.12)  # quota: 50 requests per 5 seconds
                data = json.loads(http_get("https://api.deezer.com/" + path))
                if data.get("error", {}).get("code") != 4:
                    break
                time.sleep(5)
            self.cache[path] = data
            self.fresh += 1
            if self.fresh % 100 == 0:
                self.save()
        return self.cache[path]

    def search(self, query, limit):
        found = self.get("search?" + urllib.parse.urlencode({"q": query, "limit": limit}))
        return [t for t in found.get("data", [])
                if t.get("preview") and t.get("readable") and not t.get("explicit_lyrics")]

    def save(self):
        CACHE.mkdir(exist_ok=True)
        self.file.write_text(json.dumps(self.cache), encoding="utf-8")


def whole(phrase, text):
    """True if phrase appears in text as whole words (so No. 1 is not No. 10)."""
    return bool(phrase) and f" {phrase} " in f" {text} "


def classical_tracks(deezer, row):
    surname = fold(row["artist"]).split(" ")[-1]
    titles = {fold(row["sLabel"]), fold(row["title"])} - {""}
    picked, seen = [], set()
    for title in sorted(titles, key=len, reverse=True):
        if len(title) < 4:
            continue
        for t in deezer.search(f"{surname} {title}", 40):
            name = fold(t["title"])
            credit = " ".join(fold(x) for x in (t["artist"]["name"], t["album"]["title"], t["title"]))
            if not whole(title, name) or not whole(surname, credit) or name in seen:
                continue
            seen.add(name)
            picked.append(t["id"])
            if len(picked) == CLASSICAL_TRACKS:
                return picked
    return picked


def song_tracks(deezer, row, year):
    artist, title = fold(row["artist"]), fold(row["sLabel"])
    if not title:
        return [], 0
    picked, rank = [], 0
    for t in deezer.search(f'{row["artist"]} {row["sLabel"]}', 15):
        name = fold(t["title"])
        if fold(t["artist"]["name"]) != artist or not (name + " ").startswith(title + " "):
            continue
        extra = name[len(title):].split()
        if any(w not in SAME_RECORDING and not w.isdigit() for w in extra):
            continue
        if not picked:
            # A release older than the song's own date means one of them is wrong.
            released = deezer.get(f"track/{t['id']}").get("release_date") or ""
            if not released[:4].isdigit() or int(released[:4]) < year:
                continue
            rank = t.get("rank", 0)
        picked.append(t["id"])
        if len(picked) == SONG_TRACKS:
            break
    return picked, rank


def classical_year(row):
    year = int(row["inception"] or 0) or min(
        [int(row[k]) for k in ("premiere", "published") if row[k]] or [0])
    born = int(row["born"])
    died = int(row["died"] or born + 90)
    if not year or not born + 10 <= year <= died + 1:
        return 0
    return year


def could_have_performed(row, year):
    """False when the performer was a child, unformed or dead that year, which
    means Wikidata names someone who covered the song, not who it is dated by."""
    born, formed, died = (int(row[k] or 0) for k in ("born", "formed", "died"))
    if born and year < born + 12:
        return False
    if formed and not born and year < formed:
        return False
    return not died or year <= died


def entry(row, year, ids, classical):
    e = {"id": qid(row["s"]), "t": row["sLabel"], "by": row["artist"], "y": year, "w": row["wiki"]}
    if row["swiki"]:
        e["pw"] = row["swiki"]
    if classical:
        e["c"] = 1
    e["d"] = ids
    return e


def best_known(candidates):
    return sorted(candidates, key=lambda c: -int(c[0]["links"]))


def classical_fill(deezer, candidates):
    """The best-known works of a decade that Deezer can play."""
    kept, tried = [], 0
    per_composer = Counter()
    for row, year in best_known(candidates):
        if len(kept) == PER_DECADE or tried == PER_DECADE * TRIES:
            break
        if per_composer[row["artist"]] == PER_COMPOSER:
            continue
        tried += 1
        ids = classical_tracks(deezer, row)
        if ids:
            per_composer[row["artist"]] += 1
            kept.append((row, year, ids))
    return kept


def song_fill(deezer, candidates, quota, per_span, per_artist):
    """Of a year's best-known songs, the ones most played on Deezer."""
    found = []
    for row, year in best_known(candidates)[:SONG_POOL]:
        ids, rank = song_tracks(deezer, row, year)
        if ids:
            found.append((rank, row, year, ids))
    kept = []
    here = Counter()
    for rank, row, year, ids in sorted(found, key=lambda f: -f[0]):
        artist = row["artist"]
        if len(kept) < quota and here[artist] < per_span and per_artist[artist] < PER_ARTIST:
            here[artist] += 1
            per_artist[artist] += 1
            kept.append((row, year, ids))
    return kept


def main():
    deezer = Deezer()
    pieces = []

    works = {}
    for row in sparql(CLASSICAL, "tracks_classical"):
        year = classical_year(row)
        if (year and year < CLASSICAL_BEFORE and row["np"] == "1"
                and "anthem" not in row["types"] and not row["sLabel"].startswith("Q")):
            works.setdefault(year // 10, []).append((row, year))
    for decade in sorted(works):
        kept = classical_fill(deezer, works[decade])
        pieces += [entry(row, year, ids, True) for row, year, ids in kept]
        print(f"{decade * 10}s: {len(kept):2} of {len(works[decade]):3} classical works", flush=True)
    deezer.save()

    songs = {}
    for row in sparql(SONGS, "tracks_songs"):
        year = int(row["year"] or 0)
        # More than one performer usually means the original and its covers.
        if (year >= SONGS_FROM and row["np"] == "1" and not row["sLabel"].startswith("Q")
                and could_have_performed(row, year)):
            songs.setdefault(year if year >= CLASSICAL_BEFORE else year // 10 * 10, []).append((row, year))
    per_artist = Counter()
    for span in sorted(songs):
        early = span < CLASSICAL_BEFORE
        kept = song_fill(deezer, songs[span], PER_EARLY_DECADE if early else PER_YEAR,
                         2 if early else 1, per_artist)
        pieces += [entry(row, year, ids, False) for row, year, ids in kept]
        print(f"{span}: {len(kept):2} of {len(songs[span]):3} songs", flush=True)
    deezer.save()

    # A few old songs come through both doors; keep the song, not the work.
    pieces = list({p["id"]: p for p in pieces}.values())
    pieces.sort(key=lambda p: (p["y"], p["t"]))
    OUT.parent.mkdir(exist_ok=True)
    # A script, not JSON, so the page also works opened straight from disk.
    OUT.write_text("window.SOUNDABOUTS_TRACKS="
                   + json.dumps(pieces, ensure_ascii=False, separators=(",", ":")) + ";\n",
                   encoding="utf-8")
    classical = sum(1 for p in pieces if p.get("c"))
    print(f"\nwrote {OUT} ({OUT.stat().st_size // 1024} KB): "
          f"{classical} classical works, {len(pieces) - classical} songs")


if __name__ == "__main__":
    main()
