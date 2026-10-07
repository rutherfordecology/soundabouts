# Changelog

## v0.4.1 — 2026-10-07

- Renamed from the working name "overhrd." to Soundabouts. Saved play counts
  from before the rename start again from zero.

## v0.4.0 — 2026-10-07

- Switching between Contemporary and Traditional now keeps the same country
  and plays its music in the other style, as an extra clue, instead of starting
  a new round. Guesses carry over.
- A style button is greyed out when the current country has no music in that
  style.

## v0.3.2 — 2026-10-07

- Tracks that Apple Music or Deezer flag as explicit are no longer played.

## v0.3.1 — 2026-10-07

- The page now works when `index.html` is opened straight from disk: country
  data ships as `data/countries.js` instead of being fetched as JSON.
- "Give up and reveal" no longer shows before the first guess.

## v0.3.0 — 2026-10-07

Coverage: 164 of 197 countries are now playable (162 contemporary, 111
traditional), up from 134.

- Third data pass: for countries still thin, take musicians Wikidata knows but
  has no streaming ID for, and find the ID by exact-name search on Deezer, or
  from MusicBrainz's links where Wikidata has a MusicBrainz ID. Short or
  ambiguous names are skipped.
- Deezer previews now fall back to an artist's albums when Deezer has no "top
  tracks" for them, which is the case for many smaller artists.

## v0.2.0 — 2026-10-07

Coverage: 134 of 197 countries are now playable (129 contemporary, 73
traditional), up from 109.

- Countries with thin pools get a second, wider Wikidata query that also takes
  artists with no genre recorded and artists with only one Wikipedia article.
- Artists from that wider query must have a music occupation, which keeps
  actors and other non-musicians out.
- Fixed Cyprus resolving to the island rather than the country, which left it
  with no artists.
- The build now waits out Wikidata's one-query-a-minute throttling.

## v0.1.0 — 2026-10-07

First prototype.

- Random country, played as 30-second previews from Apple Music or Deezer; the
  track and artist stay hidden until the round ends.
- Contemporary and traditional modes, each with its own artist pool per country.
- Each guess shows distance, compass direction and a closeness percentage.
- "Play a different artist" for another clue from the same country.
- On a correct guess (or reveal): Wikipedia summary, capital, population, area,
  languages, currency, and the list of tracks heard.
- `build/build_data.py` generates `data/countries.json` from Wikidata.
