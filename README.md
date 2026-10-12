# Soundabouts

Guess the country from its music. A country is picked at random, you hear a
30-second clip by an artist from there, and every guess tells you how far away
and in which direction the answer lies.

Or guess by time: you hear one piece and guess the year. Each guess only says
whether the music is newer or older. From 1950 on the exact year is needed;
before 1950 the right decade will do, and before 1600 anything within 50 years.

Static site: `index.html`, `style.css`, `app.js`, `data/countries.js` and
`data/tracks.js`. No build step, no API keys, works from GitHub Pages.

## Where the music comes from

`build/build_data.py` asks Wikidata for artists from each country that have an
Apple Music or Deezer artist ID and at least one music genre, then sorts them
into two pools:

- **Traditional** — at least half the artist's genres are folk, traditional or
  world music (by Wikidata's genre hierarchy, or by name).
- **Contemporary** — no traditional genres, a pop/rock/hip-hop/electronic style
  genre, and born from 1970 on (or a group formed from 1990 on).

Classical artists, artists tied to more than one country, and anything
ambiguous are left out. Countries that end up with fewer than six artists in a
pool get a second, wider query: genre becomes optional (a musician born from
1975 on with no genre counts as contemporary) and one Wikipedia article is
enough. A country needs two artists in a pool to be playable in that mode.

Countries still thin after that get a third pass: musicians Wikidata lists
with no streaming ID are looked up by exact name on Deezer (names under seven
letters, and names shared by two comparably popular artists, are skipped), or
through MusicBrainz's links when Wikidata has a MusicBrainz ID. A name match
can occasionally land on the wrong artist.

Territories with at least 5,000 people (Puerto Rico, Hong Kong, Guadeloupe...)
go through the second and third passes only, and match artists by birthplace as
well as citizenship, because their citizenship is usually recorded as the
parent state. Smaller territories, and any that end up without artists, are
accepted as guesses but are never the answer.

As of v0.9.0 that gives 183 playable places: 164 of 197 countries and 19
territories.

At play time the page looks up the artist's tracks and streams a preview
straight from Apple or Deezer.

Each artist also carries the title of their English Wikipedia article, where
they have one, for the blurb shown when a round ends.

Refresh the data with:

    python build/build_data.py

Query results are cached in `build/cache/`; delete it to fetch fresh.

## Where the years come from

`build/build_tracks.py` builds the guess-by-time list from Wikidata (through
QLever's mirror, which is much faster for these queries) and matches each
piece to tracks on Deezer:

- **Classical works** written before 1950: anything with a composer and a
  date that falls inside the composer's lifetime. The date is the work's
  inception, or failing that the earlier of first performance and
  publication. National anthems are left out. A Deezer track counts if its
  title contains the work's name and the composer is credited on it.
- **Songs** from 1900 on: singles and songs with a single performer and a
  publication date. Songs dated before their performer was 12, before the
  band formed or after the performer died are dropped, since that is a cover
  carrying the original's date. The Deezer track must be by that artist with
  that title, and its release must not be older than the year Wikidata gives.

Classical works are taken in order of how many Wikipedia articles they have,
up to 15 per decade and three per composer in a decade. For songs, the 40
with the most articles in each year are looked up and the 8 most played on
Deezer are kept (12 per decade before 1950), one per artist per year and five
per artist overall. Ranking by articles alone fills recent years with
Eurovision entries.

As of v0.10.0 that gives 1,015 pieces: 371 classical works and 644 songs. A
year is only as good as Wikidata's date for it, and a classical match can
occasionally be an arrangement or a different work with the same name by the
same composer.

    python build/build_tracks.py

## Running locally

Open `index.html` in a browser. No server needed.
