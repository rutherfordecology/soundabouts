# Soundabouts

Guess the country from its music. A country is picked at random, you hear a
30-second clip by an artist from there, and every guess tells you how far away
and in which direction the answer lies.

Static site: `index.html`, `style.css`, `app.js` and `data/countries.js`. No
build step, no API keys, works from GitHub Pages.

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

Refresh the data with:

    python build/build_data.py

Query results are cached in `build/cache/`; delete it to fetch fresh.

## Running locally

Open `index.html` in a browser. No server needed.
