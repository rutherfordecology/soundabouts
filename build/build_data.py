"""Build data/countries.js for Soundabouts.

Country basics come from mledoze/countries (population from Wikidata). Artists
come from Wikidata, split into a contemporary and a traditional pool per
country, in three passes:

1. Artists with a genre, three or more Wikipedia articles, and an Apple Music
   or Deezer artist ID.
2. For countries left thin: the same without the genre and article limits.
3. For countries still thin: musicians Wikidata knows but has no streaming ID
   for. Their ID is found by exact-name search on Deezer, or from MusicBrainz's
   links when Wikidata has a MusicBrainz ID.

Re-run to refresh:

    python build/build_data.py
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
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "countries.js"
CACHE = Path(__file__).resolve().parent / "cache"
UA = "soundabouts-data-build/0.1 (https://github.com/; music geography game)"
SPARQL = "https://query.wikidata.org/sparql"

PER_MODE = 14      # artists kept per country per mode
MIN_ARTISTS = 2    # a country needs this many to be playable in a mode
TOP_UP_TO = 6      # pools smaller than this take less clear-cut artists too
WIDE_BATCH = 8     # countries per query in the wider second pass
LOOKUP_BATCH = 5   # countries per query in the third pass
LOOKUP_TRIES = 25  # artists to try resolving per country per mode
MB_UA = "soundabouts-data-build/0.3 (music geography game)"

# Citizenship is sometimes recorded against a different item than the one
# carrying the ISO code.
EXTRA_QIDS = {"NL": ["Q29999"], "DK": ["Q756617"]}

TRAD_ROOTS = ["Q43343", "Q235858", "Q205049"]   # folk, traditional folk, world
CLASSICAL_ROOT = "Q9730"
TRAD_WORDS = (
    "folk", "tradition", "world music", "music of ", "ethnic", "fado", "flamenco",
    "qawwali", "ghazal", "hindustani", "carnatic", "raga", "gamelan", "griot",
    "mugham", "rebetiko", "klezmer", "celtic", "tango", "mariachi", "ranchera",
    "samba", "bossa", "son cubano", "bolero", "highlife", "mbalax", "chaabi",
    "gnawa", "taarab", "morna", "calypso", "mento", "cumbia", "vallenato", "joik",
    "throat singing", "enka", "pansori", "sevdalinka", "bhangra", "baul", "sufi",
    "maqam", "dabke", "polka", "bluegrass", "old-time", "andalusi", "gagaku",
    "persian", "arabic music", "ottoman", "khoomei", "long song", "wassoulou",
    "desert blues", "mbira", "chimurenga", "soukous", "rumba",
)
# These contain a traditional word but are not what the traditional mode means.
NOT_TRAD_WORDS = ("rock", "metal", "pop", "punk", "indie", "hip hop", "electr",
                  "fusion", "nu-", "neo")
# Classical traditions that belong in the traditional pool, not the skip list.
ART_TRAD_WORDS = ("hindustani", "carnatic", "indian classical", "persian",
                  "arabic", "ottoman", "andalusi", "gagaku", "mugham")
SKIP_WORDS = ("classical", "opera", "christmas", "children", "soundtrack",
              "film score", "comedy", "spoken", "anthem", "military", "baroque",
              "romantic music", "symphon")
NOT_MUSIC_WORDS = ("film", "pornograph", "novel", "literature", "poetry", "fiction",
                   "painting", "television", "drama", "video game", "theatre",
                   "theater", "documentary", "essay", "portrait", "photograph")
POP_WORDS = ("pop", "hip hop", "hip-hop", "rap", "trap", "r&b", "rhythm and blues",
             "electro", "dance", "house", "techno", "rock", "indie", "reggaeton",
             "afro", "drill", "edm", "punk", "metal", "soul", "funk", "dancehall",
             "reggae", "ska", "salsa", "merengue", "bachata", "soca", "kizomba",
             "zouk", "latin", "kuduro", "amapiano", "kwaito", "bongo flava",
             "disco", "synth", "urban", "contemporary", "alternative")
MUSIC_JOB_WORDS = ("singer", "musician", "rapper", "composer", "songwriter",
                   "disc jockey", "guitarist", "pianist", "drummer", "vocalist",
                   "instrumentalist", "record producer", "recording artist",
                   "bassist", "violinist", "bandleader", "percussionist")


def http_get(url, headers=None, tries=8):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return r.read().decode("utf-8")
        except Exception as e:  # noqa: BLE001 - retry anything, report at the end
            if attempt == tries - 1:
                raise
            # Wikidata sometimes throttles to one query a minute.
            wait = 65 if getattr(e, "code", None) == 429 else 5 * (attempt + 1)
            print(f"   retry in {wait}s ({e})", file=sys.stderr, flush=True)
            time.sleep(wait)


def sparql(query, cache_key=None):
    if cache_key:
        f = CACHE / f"{cache_key}.csv"
        if f.exists():
            return list(csv.DictReader(io.StringIO(f.read_text(encoding="utf-8"))))
    text = http_get(SPARQL + "?" + urllib.parse.urlencode({"query": query}),
                    {"Accept": "text/csv"})
    if cache_key:
        CACHE.mkdir(exist_ok=True)
        (CACHE / f"{cache_key}.csv").write_text(text, encoding="utf-8")
        time.sleep(1.2)
    return list(csv.DictReader(io.StringIO(text)))


def qid(uri):
    return uri.rsplit("/", 1)[-1]


def load_countries():
    f = CACHE / "mledoze.json"
    if f.exists():
        raw = json.loads(f.read_text(encoding="utf-8"))
    else:
        raw = json.loads(http_get(
            "https://raw.githubusercontent.com/mledoze/countries/master/countries.json"))
        CACHE.mkdir(exist_ok=True)
        f.write_text(json.dumps(raw), encoding="utf-8")

    wd = sparql("""SELECT ?c ?iso ?title (MAX(?p) AS ?pop) WHERE {
        ?c wdt:P297 ?iso .
        OPTIONAL { ?c wdt:P1082 ?p }
        OPTIONAL { ?art schema:about ?c ; schema:isPartOf <https://en.wikipedia.org/> ;
                        schema:name ?title }
    } GROUP BY ?c ?iso ?title""", "iso_pop")
    # A few codes sit on two items (Cyprus the island and the state); the
    # older, lower-numbered item is the country.
    by_iso = {}
    for r in sorted(wd, key=lambda r: -int(qid(r["c"])[1:])):
        by_iso[r["iso"]] = r

    out = {}
    for c in raw:
        iso = c["cca2"]
        if not c.get("unMember") and iso not in ("TW", "XK", "PS", "VA"):
            continue
        if iso not in by_iso or not c.get("latlng"):
            continue
        out[iso] = {
            "iso": iso,
            "name": c["name"]["common"],
            "flag": c.get("flag", ""),
            "lat": round(c["latlng"][0], 2),
            "lon": round(c["latlng"][1], 2),
            "capital": ", ".join(c.get("capital") or []),
            "population": int(float(by_iso[iso]["pop"])) if by_iso[iso]["pop"] else None,
            "area": c.get("area"),
            "languages": ", ".join((c.get("languages") or {}).values()),
            "currency": ", ".join(v["name"] for v in (c.get("currencies") or {}).values()),
            "region": c.get("subregion") or c.get("region") or "",
            "wiki": by_iso[iso]["title"] or c["name"]["common"],
            "_qids": [qid(by_iso[iso]["c"])] + EXTRA_QIDS.get(iso, []),
        }
    return out


def load_genres():
    labels = {qid(r["g"]): r["l"].lower() for r in sparql(
        """SELECT ?g ?l WHERE { ?g wdt:P31 wd:Q188451 ; rdfs:label ?l
           FILTER(LANG(?l)="en") }""", "genres")}
    trad = set()
    for root in TRAD_ROOTS:
        trad |= {qid(r["g"]) for r in sparql(
            f"SELECT ?g WHERE {{ ?g wdt:P279* wd:{root} }}", f"sub_{root}")}
    classical = {qid(r["g"]) for r in sparql(
        f"SELECT ?g WHERE {{ ?g wdt:P279* wd:{CLASSICAL_ROOT} }}", "sub_classical")}
    return labels, trad, classical


def fetch_labels(qids, cache_prefix):
    """English labels, lower-cased, for a set of Wikidata items."""
    out = {}
    wanted = sorted(q for q in qids if q.startswith("Q"))
    for i in range(0, len(wanted), 250):
        values = " ".join("wd:" + q for q in wanted[i:i + 250])
        for r in sparql(f"""SELECT ?g ?l WHERE {{ VALUES ?g {{ {values} }}
                ?g rdfs:label ?l FILTER(LANG(?l)="en") }}""", f"{cache_prefix}_{i // 250}"):
            out[qid(r["g"])] = r["l"].lower()
    return out


def label_missing(labels, wanted, cache_prefix):
    """Many genres artists use are not typed as 'music genre'; label those too."""
    for g, name in fetch_labels(set(wanted) - set(labels), cache_prefix).items():
        if not any(w in name for w in NOT_MUSIC_WORDS):
            labels[g] = name


ARTIST_FIELDS = """?a ?aLabel ?links
        (SAMPLE(?apple) AS ?apple) (SAMPLE(?deezer) AS ?deezer)
        (MIN(YEAR(?born)) AS ?born) (MIN(YEAR(?formed)) AS ?formed)
        (COUNT(DISTINCT ?c2) AS ?countries)
        (GROUP_CONCAT(DISTINCT STRAFTER(STR(?g), "entity/"); separator="|") AS ?genres)"""


def artists_for(iso, qids):
    values = " ".join(f"wd:{q}" for q in qids)
    return sparql(f"""SELECT {ARTIST_FIELDS}
      WHERE {{
        VALUES ?c {{ {values} }}
        {{ ?a wdt:P27 ?c }} UNION {{ ?a wdt:P495 ?c . ?a wdt:P31/wdt:P279* wd:Q2088357 }}
        ?a wdt:P136 ?g ; wikibase:sitelinks ?links .
        FILTER(?links >= 3)
        OPTIONAL {{ ?a wdt:P2850 ?apple }}
        OPTIONAL {{ ?a wdt:P2722 ?deezer }}
        FILTER(BOUND(?apple) || BOUND(?deezer))
        OPTIONAL {{ ?a wdt:P569 ?born }}
        OPTIONAL {{ ?a wdt:P571 ?formed }}
        OPTIONAL {{ ?a wdt:P27|wdt:P495 ?c2 }}
        SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en" }}
      }} GROUP BY ?a ?aLabel ?links ORDER BY DESC(?links) LIMIT 400""", f"artists_{iso}")


def artists_wide(batch):
    """Second pass for several small countries at once: genre optional, any
    artist with a Wikipedia article, plus occupations so non-musicians can go."""
    values = " ".join(f"wd:{q}" for _, qids in batch for q in qids)
    rows = sparql(f"""SELECT ?c {ARTIST_FIELDS}
        (GROUP_CONCAT(DISTINCT STRAFTER(STR(?o), "entity/"); separator="|") AS ?jobs)
      WHERE {{
        VALUES ?c {{ {values} }}
        {{ ?a wdt:P27 ?c }} UNION {{ ?a wdt:P495 ?c . ?a wdt:P31/wdt:P279* wd:Q2088357 }}
        ?a wikibase:sitelinks ?links .
        FILTER(?links >= 1)
        OPTIONAL {{ ?a wdt:P2850 ?apple }}
        OPTIONAL {{ ?a wdt:P2722 ?deezer }}
        FILTER(BOUND(?apple) || BOUND(?deezer))
        OPTIONAL {{ ?a wdt:P136 ?g }}
        OPTIONAL {{ ?a wdt:P106 ?o }}
        OPTIONAL {{ ?a wdt:P569 ?born }}
        OPTIONAL {{ ?a wdt:P571 ?formed }}
        OPTIONAL {{ ?a wdt:P27|wdt:P495 ?c2 }}
        SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en" }}
      }} GROUP BY ?c ?a ?aLabel ?links""", "wide_" + "_".join(iso for iso, _ in batch))
    by_iso = {}
    for iso, qids in batch:
        mine = [dict(r, wide=True) for r in rows if qid(r["c"]) in qids]
        by_iso[iso] = sorted(mine, key=lambda r: -int(r["links"]))
    return by_iso


def artists_unlinked(batch, job_qids):
    """Third pass: musicians with no streaming ID on Wikidata."""
    values = " ".join(f"wd:{q}" for _, qids in batch for q in qids)
    job_values = " ".join(f"wd:{q}" for q in job_qids)
    rows = sparql(f"""SELECT ?c {ARTIST_FIELDS}
        (GROUP_CONCAT(DISTINCT STRAFTER(STR(?o), "entity/"); separator="|") AS ?jobs)
        (SAMPLE(?mb) AS ?mbid)
      WHERE {{
        VALUES ?c {{ {values} }}
        {{ ?a wdt:P27 ?c ; wdt:P106 ?j . VALUES ?j {{ {job_values} }} }}
        UNION {{ ?a wdt:P495 ?c . ?a wdt:P31/wdt:P279* wd:Q2088357 }}
        ?a wikibase:sitelinks ?links .
        FILTER(?links >= 2)
        FILTER NOT EXISTS {{ ?a wdt:P2850 [] }}
        FILTER NOT EXISTS {{ ?a wdt:P2722 [] }}
        OPTIONAL {{ ?a wdt:P136 ?g }}
        OPTIONAL {{ ?a wdt:P106 ?o }}
        OPTIONAL {{ ?a wdt:P569 ?born }}
        OPTIONAL {{ ?a wdt:P571 ?formed }}
        OPTIONAL {{ ?a wdt:P27|wdt:P495 ?c2 }}
        OPTIONAL {{ ?a wdt:P434 ?mb }}
        SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en" }}
      }} GROUP BY ?c ?a ?aLabel ?links""", "unlinked_" + "_".join(iso for iso, _ in batch))
    by_iso = {}
    for iso, qids in batch:
        mine = [dict(r, wide=True) for r in rows if qid(r["c"]) in qids]
        by_iso[iso] = sorted(mine, key=lambda r: -int(r["links"]))
    return by_iso


def plain(name):
    name = unicodedata.normalize("NFD", name)
    return re.sub(r"[^a-z0-9]", "", "".join(ch for ch in name if not unicodedata.combining(ch)).lower())


def deezer_by_name(name):
    """Deezer artist ID for an exact, unambiguous name match, else None."""
    want = plain(name)
    if len(want) < 7:
        return None  # short names collide with unrelated artists too often
    url = "https://api.deezer.com/search/artist?" + urllib.parse.urlencode({"q": name, "limit": 15})
    for _ in range(4):
        time.sleep(0.15)
        data = json.loads(http_get(url))
        if "error" not in data:
            break
        time.sleep(5)  # quota: 50 requests per 5 seconds
    else:
        return None
    exact = sorted((a for a in data.get("data", []) if plain(a["name"]) == want and a.get("nb_album")),
                   key=lambda a: -a.get("nb_fan", 0))
    if not exact:
        return None
    if len(exact) > 1 and exact[1].get("nb_fan", 0) * 5 > exact[0].get("nb_fan", 0):
        return None  # two plausible artists share the name
    return str(exact[0]["id"])


def musicbrainz_links(mbid):
    """Streaming IDs from the links MusicBrainz holds for an artist."""
    time.sleep(1.1)  # MusicBrainz allows one request a second
    try:
        data = json.loads(http_get(
            f"https://musicbrainz.org/ws/2/artist/{mbid}?inc=url-rels&fmt=json",
            {"User-Agent": MB_UA}, tries=3))
    except Exception:  # noqa: BLE001 - a dead MBID is not worth stopping for
        return {}
    ids = {}
    for rel in data.get("relations", []):
        link = rel.get("url", {}).get("resource", "")
        m = re.search(r"apple\.com/.*/artist/(?:[^/]+/)?(?:id)?(\d+)", link)
        if m:
            ids["apple"] = m.group(1)
        m = re.search(r"deezer\.com/(?:\w+/)?artist/(\d+)", link)
        if m:
            ids["deezer"] = m.group(1)
    return ids


def resolve(row, cache):
    """Find streaming IDs for a third-pass artist; fills row in place."""
    key = qid(row["a"])
    if key not in cache:
        ids = {}
        deezer = deezer_by_name(row["aLabel"])
        if deezer:
            ids["deezer"] = deezer
        elif row["mbid"]:
            ids = musicbrainz_links(row["mbid"])
        cache[key] = ids
    row["apple"] = cache[key].get("apple", "")
    row["deezer"] = cache[key].get("deezer", "")
    return bool(row["apple"] or row["deezer"])


def is_trad(g, name, trad):
    if any(w in name for w in NOT_TRAD_WORDS):
        return False
    return g in trad or any(w in name for w in TRAD_WORDS)


def classify(row, labels, trad, classical, jobs):
    """Return (kind, strict) for one artist row; kind is 'trad', 'modern' or None.

    strict picks are clear-cut; the rest only top up thin pools.
    """
    year = int(row["born"] or 0) or (int(row["formed"] or 0) - 20 if row["formed"] else 0)
    if row.get("wide"):
        mine = [jobs[j] for j in row["jobs"].split("|") if j in jobs]
        if mine and not any(w in j for j in mine for w in MUSIC_JOB_WORDS):
            return None, False
        if not mine and row["born"]:
            return None, False  # a person with no occupation recorded
    genres = [g for g in row["genres"].split("|") if g in labels]
    if not genres:
        # No genre to go on: a recent musician is most likely contemporary.
        if row.get("wide") and not row["genres"] and year >= 1975:
            return "modern", False
        return None, False
    names = [labels[g] for g in genres]
    art_trad = any(w in n for n in names for w in ART_TRAD_WORDS)
    if not art_trad and (any(g in classical for g in genres)
                         or any(w in n for n in names for w in SKIP_WORDS)):
        return None, False
    n_trad = sum(1 for g, n in zip(genres, names) if is_trad(g, n, trad))
    if art_trad or n_trad == len(genres):
        return "trad", True
    if n_trad * 2 >= len(genres):
        return "trad", False
    if n_trad:
        return None, False  # mostly something else with a folk tinge
    poppy = any(w in n for n in names for w in POP_WORDS)
    if poppy and (year >= 1970 or not year):
        return "modern", True
    if (poppy and year >= 1955) or year >= 1970:
        return "modern", False
    return None, False


def build_pools(rows, labels, trad, classical, jobs):
    strict = {"modern": [], "trad": []}
    loose = {"modern": [], "trad": []}
    for r in rows:
        if int(r["countries"] or 1) > 1 or r["aLabel"].startswith("Q"):
            continue
        kind, sure = classify(r, labels, trad, classical, jobs)
        if not kind:
            continue
        entry = {"n": r["aLabel"]}
        if r["apple"]:
            entry["a"] = r["apple"]
        if r["deezer"]:
            entry["d"] = r["deezer"]
        (strict if sure else loose)[kind].append(entry)
    pools = {}
    for kind in ("modern", "trad"):
        pool = strict[kind][:PER_MODE]
        if len(pool) < TOP_UP_TO:
            pool += loose[kind][:TOP_UP_TO - len(pool)]
        pools[kind] = pool
    return pools


def all_genres(rows_by_iso):
    return {g for rows in rows_by_iso.values() for r in rows for g in r["genres"].split("|")}


def territories(have):
    """Places that are not sovereign states (French Guiana, Greenland, Puerto
    Rico...). Never the answer, but accepted as guesses so they still return a
    distance and direction."""
    raw = json.loads((CACHE / "mledoze.json").read_text(encoding="utf-8"))
    return [{"iso": c["cca2"], "name": c["name"]["common"], "flag": c.get("flag", ""),
             "lat": round(c["latlng"][0], 2), "lon": round(c["latlng"][1], 2),
             "modern": [], "trad": []}
            for c in raw if c["cca2"] not in have and c.get("latlng")]


def main():
    countries = load_countries()
    labels, trad, classical = load_genres()
    qids = {iso: c.pop("_qids") for iso, c in countries.items()}

    rows_by_iso = {}
    for iso in sorted(countries):
        try:
            rows_by_iso[iso] = artists_for(iso, qids[iso])
        except Exception as e:  # noqa: BLE001
            print(f"!! {iso}: {e}", file=sys.stderr)
            rows_by_iso[iso] = []
    label_missing(labels, all_genres(rows_by_iso), "labels")

    # Second, wider pass for countries the first pass left thin. Big countries
    # are skipped: they would time out and their pools are already full.
    thin = []
    for iso in sorted(countries):
        pools = build_pools(rows_by_iso[iso], labels, trad, classical, {})
        if len(rows_by_iso[iso]) < 150 and min(len(p) for p in pools.values()) < TOP_UP_TO:
            thin.append(iso)
    print(f"{len(thin)} countries get the wider pass", flush=True)
    for i in range(0, len(thin), WIDE_BATCH):
        batch = [(iso, qids[iso]) for iso in thin[i:i + WIDE_BATCH]]
        try:
            rows_by_iso.update(artists_wide(batch))
            print(f"   wide {i + len(batch)}/{len(thin)}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"!! wide {[iso for iso, _ in batch]}: {e}", file=sys.stderr)
    label_missing(labels, all_genres(rows_by_iso), "labels_wide")
    jobs = fetch_labels({j for rows in rows_by_iso.values() for r in rows
                         for j in r.get("jobs", "").split("|")}, "jobs")

    # Third pass: known musicians with no streaming ID on Wikidata.
    job_qids = sorted(q for q, name in jobs.items()
                      if any(w in name for w in MUSIC_JOB_WORDS))
    unlinked = {}
    for i in range(0, len(thin), LOOKUP_BATCH):
        batch = [(iso, qids[iso]) for iso in thin[i:i + LOOKUP_BATCH]]
        try:
            unlinked.update(artists_unlinked(batch, job_qids))
            print(f"   unlinked {i + len(batch)}/{len(thin)}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"!! unlinked {[iso for iso, _ in batch]}: {e}", file=sys.stderr)
    label_missing(labels, all_genres(unlinked), "labels_unlinked")
    jobs.update(fetch_labels({j for rows in unlinked.values() for r in rows
                              for j in r["jobs"].split("|")} - set(jobs), "jobs_unlinked"))

    resolved_file = CACHE / "resolved.json"
    resolved = json.loads(resolved_file.read_text(encoding="utf-8")) if resolved_file.exists() else {}
    for n, iso in enumerate(thin):
        pools = build_pools(rows_by_iso[iso], labels, trad, classical, jobs)
        need = {k: TOP_UP_TO - len(p) for k, p in pools.items()}
        tries = {"modern": 0, "trad": 0}
        found = 0
        for r in unlinked.get(iso, []):
            if int(r["countries"] or 1) > 1 or r["aLabel"].startswith("Q"):
                continue
            kind, _ = classify(r, labels, trad, classical, jobs)
            if not kind or need[kind] <= 0 or tries[kind] >= LOOKUP_TRIES:
                continue
            tries[kind] += 1
            if resolve(r, resolved):
                rows_by_iso[iso].append(r)
                need[kind] -= 1
                found += 1
        resolved_file.write_text(json.dumps(resolved), encoding="utf-8")
        print(f"   resolve {n + 1}/{len(thin)} {iso}: +{found} "
              f"(tried {tries['modern'] + tries['trad']})", flush=True)

    print(f"{len(countries)} countries, {len(labels)} genres, {len(trad)} traditional")

    for i, (iso, c) in enumerate(sorted(countries.items())):
        pools = build_pools(rows_by_iso[iso], labels, trad, classical, jobs)
        for kind, pool in pools.items():
            c[kind] = pool if len(pool) >= MIN_ARTISTS else []
        print(f"{i + 1:3}/{len(countries)} {iso} {c['name'][:28]:28} "
              f"modern {len(c['modern']):2}  trad {len(c['trad']):2}")

    data = sorted(list(countries.values()) + territories(set(countries)),
                  key=lambda c: c["name"])
    OUT.parent.mkdir(exist_ok=True)
    # A script, not JSON, so the page also works opened straight from disk.
    OUT.write_text("window.SOUNDABOUTS_COUNTRIES="
                   + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";\n",
                   encoding="utf-8")
    m = sum(1 for c in data if c["modern"])
    t = sum(1 for c in data if c["trad"])
    print(f"\nwrote {OUT} ({OUT.stat().st_size // 1024} KB): "
          f"{m} countries playable contemporary, {t} traditional")


if __name__ == "__main__":
    main()
