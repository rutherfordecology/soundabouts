(() => {
'use strict';

const $ = id => document.getElementById(id);
const audio = $('audio');
const EARTH_KM = 6371;
const HALF_WORLD_KM = Math.PI * EARTH_KM;
const COMPASS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];
const ALIASES = {
  US: 'usa america united states of america', GB: 'uk britain great britain england scotland wales',
  AE: 'uae emirates', CD: 'drc democratic republic of the congo congo kinshasa',
  CG: 'congo brazzaville republic of the congo', CI: 'cote divoire ivory coast',
  KR: 'korea south', KP: 'korea north', CZ: 'czech republic', MM: 'burma',
  TR: 'turkey turkiye', NL: 'holland', SZ: 'swaziland', MK: 'macedonia',
  CV: 'cape verde', TL: 'east timor', NZ: 'aotearoa', VA: 'vatican holy see'
};

let countries = [];
let mode = 'modern';
let round = null;   // { target, pools, ix, heard, guesses, over }
let jsonpSeq = 0;

// ---------- storage (best effort) ----------
const store = {
  get(key, fallback) {
    try { return JSON.parse(localStorage.getItem('soundabouts.' + key)) ?? fallback; }
    catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem('soundabouts.' + key, JSON.stringify(value)); } catch { /* private mode */ }
  }
};

// ---------- geography ----------
const rad = d => d * Math.PI / 180;

function distanceKm(a, b) {
  const dLat = rad(b.lat - a.lat), dLon = rad(b.lon - a.lon);
  const h = Math.sin(dLat / 2) ** 2 +
            Math.cos(rad(a.lat)) * Math.cos(rad(b.lat)) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_KM * Math.asin(Math.sqrt(h));
}

function bearing(a, b) {
  const dLon = rad(b.lon - a.lon);
  const y = Math.sin(dLon) * Math.cos(rad(b.lat));
  const x = Math.cos(rad(a.lat)) * Math.sin(rad(b.lat)) -
            Math.sin(rad(a.lat)) * Math.cos(rad(b.lat)) * Math.cos(dLon);
  return (Math.atan2(y, x) * 180 / Math.PI + 360) % 360;
}

// ---------- helpers ----------
const fold = s => s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase()
                   .replace(/[^a-z ]/g, ' ').replace(/\s+/g, ' ').trim();

function shuffle(list) {
  const a = list.slice();
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

function el(tag, props = {}, ...kids) {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...kids);
  return node;
}

// ---------- music sources ----------
async function appleTracks(id) {
  const res = await fetch(`https://itunes.apple.com/lookup?id=${id}&entity=song&limit=40`);
  if (!res.ok) throw new Error('apple ' + res.status);
  const json = await res.json();
  return json.results
    .filter(t => t.wrapperType === 'track' && t.previewUrl && String(t.artistId) === String(id) &&
                 t.trackExplicitness !== 'explicit')
    .map(t => ({ title: t.trackName, artist: t.artistName, url: t.previewUrl,
                 link: t.trackViewUrl, source: 'Apple Music' }));
}

function deezer(path) {
  // Deezer sends no CORS headers, so this one has to be JSONP.
  return new Promise((resolve, reject) => {
    const cb = '__soundabouts' + (++jsonpSeq);
    const script = document.createElement('script');
    const done = () => { delete window[cb]; script.remove(); clearTimeout(timer); };
    const timer = setTimeout(() => { done(); reject(new Error('deezer timeout')); }, 8000);
    window[cb] = json => { done(); resolve(json.data || []); };
    script.onerror = () => { done(); reject(new Error('deezer failed')); };
    script.src = `https://api.deezer.com/${path}${path.includes('?') ? '&' : '?'}output=jsonp&callback=${cb}`;
    document.head.append(script);
  });
}

async function deezerTracks(id) {
  const mine = list => list
    .filter(t => t.preview && String(t.artist.id) === String(id) &&
                 !t.explicit_lyrics && t.explicit_content_lyrics !== 1)
    .map(t => ({ title: t.title, artist: t.artist.name, url: t.preview,
                 link: t.link, source: 'Deezer' }));
  const top = mine(await deezer(`artist/${id}/top?limit=40`));
  if (top.length) return top;
  // Deezer has no "top tracks" for many smaller artists; their albums still play.
  const albums = shuffle(await deezer(`artist/${id}/albums?limit=25`));
  for (const album of albums.slice(0, 3)) {
    const tracks = mine(await deezer(`album/${album.id}/tracks?limit=40`));
    if (tracks.length) return tracks;
  }
  return [];
}

async function tracksFor(artist) {
  if (artist.tracks) return artist.tracks;
  let tracks = [];
  if (artist.a) tracks = await appleTracks(artist.a).catch(() => []);
  if (!tracks.length && artist.d) tracks = await deezerTracks(artist.d).catch(() => []);
  artist.tracks = shuffle(tracks);
  return artist.tracks;
}

// ---------- round ----------
function pickTarget() {
  const recent = store.get('recent', []);
  const pool = countries.filter(c => c[mode].length);
  const fresh = pool.filter(c => !recent.includes(c.iso));
  const from = fresh.length ? fresh : pool;
  return from[Math.floor(Math.random() * from.length)];
}

function newRound() {
  audio.pause();
  audio.removeAttribute('src');
  const target = pickTarget();
  const pool = kind => shuffle(target[kind]).map(a => ({ ...a }));
  round = { target, pools: { modern: pool('modern'), trad: pool('trad') },
            ix: { modern: -1, trad: -1 }, heard: [], guesses: [], over: false };
  store.set('recent', [target.iso, ...store.get('recent', [])].slice(0, 40));

  $('guesses').replaceChildren();
  $('resultwin').hidden = true;
  $('guessing').hidden = false;
  $('reveal').hidden = true;
  $('guess').value = '';
  $('guess').disabled = false;
  $('play').disabled = false;
  $('next').disabled = false;
  setRing(0);
  setIcon(false);
  $('next').classList.remove('offer');
  $('clipno').textContent = 'CLIP --';
  $('status').textContent = 'Press play to listen';
  showModes();
}

// Load the next playable clip. Must be called from a tap so mobile lets it play.
async function loadClip() {
  const r = round;
  $('play').disabled = true;
  $('next').disabled = true;
  $('status').textContent = 'Finding a track…';
  const kind = mode;
  const artists = r.pools[kind];
  for (let tries = 0; tries < artists.length; tries++) {
    r.ix[kind] = (r.ix[kind] + 1) % artists.length;
    const artist = artists[r.ix[kind]];
    const tracks = await tracksFor(artist);
    if (r !== round || kind !== mode) return;
    const track = tracks.find(t => !r.heard.includes(t)) || tracks[0];
    if (!track) continue;
    if (!r.heard.includes(track)) r.heard.push(track);
    audio.src = track.url;
    $('play').disabled = false;
    $('next').disabled = false;
    try {
      await audio.play();
    } catch {
      $('status').textContent = 'Press play to listen';
    }
    return;
  }
  if (r.heard.length) {
    // Mid-round switch to a style with nothing playable: keep the round going.
    $('play').disabled = false;
    $('status').textContent = 'Nothing playable in this style for this country';
    return;
  }
  $('status').textContent = 'No playable tracks for this one, picking another…';
  setTimeout(newRound, 1200);
}

function setRing(fraction) {
  const t = Math.floor(audio.currentTime || 0);
  $('bar').style.width = `${fraction * 100}%`;
  $('time').textContent = `${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')}`;
}

function setIcon(playing) {
  // The play key stays down while a clip is playing, like a tape deck.
  $('play').setAttribute('aria-pressed', playing);
  $('state').textContent = playing ? '▶' : (audio.src && audio.currentTime ? '❚❚' : '■');
  $('deck').classList.toggle('playing', playing);
}

// ---------- sound: volume and level bars ----------
// Audio runs through a Web Audio graph so the bars can read the real signal and
// the volume slider works on iPhone, where audio.volume is read-only.
let ctx = null, gain = null, analyser = null, bins = null;
const edges = [];
const bars = [...document.querySelectorAll('.vis i')];

// Must run inside a tap: browsers only start an audio context from a gesture.
function wake() {
  if (!ctx) {
    try {
      ctx = new (window.AudioContext || window.webkitAudioContext)();
      analyser = ctx.createAnalyser();
      analyser.fftSize = 256;
      analyser.smoothingTimeConstant = 0.75;
      gain = ctx.createGain();
      ctx.createMediaElementSource(audio).connect(analyser).connect(gain).connect(ctx.destination);
      bins = new Uint8Array(analyser.frequencyBinCount);
      // Bar edges spaced logarithmically from about 170 Hz to 15 kHz.
      const top = Math.min(bins.length - 1, Math.round(15000 / (ctx.sampleRate / analyser.fftSize)));
      let last = 0;
      for (let i = 0; i <= bars.length; i++) {
        last = Math.max(last + 1, Math.round(Math.pow(top, i / bars.length)));
        edges.push(last);
      }
      audio.volume = 1;
      setVolume(Number($('vol').value));
    } catch {
      ctx = null;
      analyser = null;
      $('deck').classList.add('fake');  // fall back to the canned animation
    }
  }
  if (ctx && ctx.state !== 'running') ctx.resume();
}

function drawLevels() {
  if (!analyser) return;
  if (audio.paused) {
    bars.forEach(bar => { bar.style.transform = ''; });
    return;
  }
  analyser.getByteFrequencyData(bins);
  bars.forEach((bar, i) => {
    let sum = 0;
    for (let j = edges[i]; j < edges[i + 1]; j++) sum += bins[j];
    const level = sum / (edges[i + 1] - edges[i]) / 255;
    bar.style.transform = `scaleY(${Math.max(0.06, level).toFixed(3)})`;
  });
  requestAnimationFrame(drawLevels);
}

function setVolume(v) {
  v = Math.max(0, Math.min(100, Math.round(v)));
  if (gain) gain.gain.value = v / 100;
  else audio.volume = v / 100;
  store.set('volume', v);
  $('vol').value = v;
  // Like the original, the groove runs from green through to red as it goes up.
  $('vol').style.setProperty('--level', `hsl(${Math.round(120 - v * 1.15)} 90% 42%)`);
  $('vollab').textContent = `VOL ${String(v).padStart(2, '0')}`;
}

function wireVolume() {
  $('vol').addEventListener('input', e => setVolume(Number(e.target.value)));
  setVolume(store.get('volume', 80));
}

// ---------- guessing ----------
function matches(query) {
  const q = fold(query);
  if (!q) return [];
  const guessed = new Set(round.guesses.map(g => g.iso));
  const scored = [];
  for (const c of countries) {
    if (guessed.has(c.iso)) continue;
    const name = c.folded;
    let score = -1;
    if (name.startsWith(q)) score = 0;
    else if (name.split(' ').some(w => w.startsWith(q))) score = 1;
    else if (name.includes(q)) score = 2;
    else if (c.alias.split(' ').some(w => w.startsWith(q)) || c.alias.includes(q)) score = 3;
    if (score >= 0) scored.push([score, c]);
  }
  return scored.sort((a, b) => a[0] - b[0] || a[1].name.localeCompare(b[1].name))
               .slice(0, 8).map(s => s[1]);
}

function showSuggestions() {
  const list = $('suggest');
  const found = round && !round.over ? matches($('guess').value) : [];
  list.replaceChildren(...found.map((c, i) => {
    const li = el('li', { role: 'option', textContent: `${c.flag} ${c.name}` });
    li.dataset.iso = c.iso;
    li.setAttribute('aria-selected', i === 0);
    return li;
  }));
  list.hidden = !found.length;
  $('guess').setAttribute('aria-expanded', !!found.length);
}

function moveSelection(step) {
  const items = [...$('suggest').children];
  if (!items.length) return;
  const at = items.findIndex(li => li.getAttribute('aria-selected') === 'true');
  const to = (at + step + items.length) % items.length;
  items.forEach((li, i) => li.setAttribute('aria-selected', i === to));
  items[to].scrollIntoView({ block: 'nearest' });
}

function submitGuess(iso) {
  const c = countries.find(x => x.iso === iso);
  if (!c || round.over || round.guesses.includes(c)) return;
  round.guesses.push(c);
  $('guess').value = '';
  showSuggestions();
  $('reveal').hidden = false;

  const hit = c === round.target;
  const km = distanceKm(c, round.target);
  const deg = bearing(c, round.target);
  const arrow = el('b', { textContent: hit ? '🎉' : '↑' });
  if (!hit) arrow.style.transform = `rotate(${deg}deg)`;
  const row = el('li', { className: hit ? 'hit' : '' },
    el('span', { className: 'name', textContent: `${c.flag} ${c.name}` }),
    el('span', { className: 'km', textContent: hit ? '0 km' : `${Math.round(km).toLocaleString()} km` }),
    el('span', { className: 'dir' }, arrow, hit ? '' : COMPASS[Math.round(deg / 45) % 8]),
    el('span', { className: 'pct', textContent: `${Math.round((1 - km / HALF_WORLD_KM) * 100)}%` }));
  $('guesses').prepend(row);
  if (hit) finish(true);
}

// ---------- result ----------
async function finish(won) {
  const r = round;
  r.over = true;
  $('guess').disabled = true;
  $('reveal').hidden = true;
  $('suggest').hidden = true;
  $('next').disabled = true;
  showModes();

  const tally = store.get('tally', { played: 0, won: 0, guesses: 0 });
  tally.played++;
  if (won) { tally.won++; tally.guesses += r.guesses.length; }
  store.set('tally', tally);
  showTally();

  const c = r.target;
  const n = r.guesses.length;
  const box = $('result');
  const summary = el('p', { textContent: 'Loading summary…' });
  const picture = el('div');
  const wikiUrl = 'https://en.wikipedia.org/wiki/' + encodeURIComponent(c.wiki.replace(/ /g, '_'));
  const fact = (label, value) => value
    ? [el('dt', { textContent: label }), el('dd', { textContent: value })] : [];
  const again = el('button', { className: 'key again', textContent: 'Play another country' });
  again.onclick = newRound;

  box.replaceChildren(
    el('h2', { textContent: `${c.flag} ${c.name}` }),
    el('p', { className: 'sub', textContent: won
      ? `Got it in ${n} guess${n === 1 ? '' : 'es'}.`
      : `Revealed after ${n} guess${n === 1 ? '' : 'es'}.` }),
    picture,
    summary,
    el('dl', {},
      ...fact('Capital', c.capital),
      ...fact('Population', c.population && c.population.toLocaleString()),
      ...fact('Area', c.area > 0 && `${Math.round(c.area).toLocaleString()} km²`),
      ...fact('Languages', c.languages),
      ...fact('Currency', c.currency),
      ...fact('Region', c.region)),
    el('a', { href: wikiUrl, target: '_blank', rel: 'noopener', textContent: 'Read more on Wikipedia' }),
    el('h3', { textContent: 'What you heard' }),
    el('ul', {}, ...r.heard.map(t => el('li', {},
      el('a', { href: t.link, target: '_blank', rel: 'noopener', textContent: t.title }),
      ` — ${t.artist} (${t.source})`))),
    again);
  $('resultwin').hidden = false;
  $('resultwin').scrollIntoView({ behavior: 'smooth', block: 'start' });

  try {
    const res = await fetch('https://en.wikipedia.org/api/rest_v1/page/summary/' +
                            encodeURIComponent(c.wiki.replace(/ /g, '_')));
    const page = await res.json();
    if (r !== round) return;
    summary.textContent = page.extract || '';
    if (page.thumbnail) picture.append(el('img', { src: page.thumbnail.source, alt: `Flag or image of ${c.name}` }));
  } catch {
    summary.textContent = '';
  }
}

function showTally() {
  const t = store.get('tally', { played: 0, won: 0, guesses: 0 });
  $('tally').textContent = t.played
    ? `Played ${t.played} · solved ${t.won}` + (t.won ? ` · average ${(t.guesses / t.won).toFixed(1)} guesses` : '')
    : '';
}

// ---------- wiring ----------
function showModes() {
  document.querySelectorAll('.modes button').forEach(b => {
    const none = round && !round.over && !round.target[b.dataset.mode].length;
    b.setAttribute('aria-pressed', b.dataset.mode === mode);
    b.disabled = none;
    b.title = none ? 'No music in this style found for this country' : '';
  });
  $('stylelab').textContent = mode === 'trad' ? 'TRADITIONAL' : 'CONTEMPORARY';
}

// Switching style mid-round keeps the same country and plays its other pool.
function setMode(next) {
  mode = next;
  store.set('mode', mode);
  showModes();
  if (!round) return newRound();
  if (!round.target[mode].length) return;
  audio.pause();
  audio.removeAttribute('src');
  setRing(0);
  loadClip();
}

$('play').onclick = () => {
  if (!round) return;
  wake();
  if (!audio.src) loadClip();
  else if (audio.paused) audio.play();
};
$('pause').onclick = () => audio.pause();
$('stop').onclick = () => {
  audio.pause();
  if (audio.src) audio.currentTime = 0;
  setRing(0);
  setIcon(false);
};
$('next').onclick = () => { wake(); audio.pause(); loadClip(); };
$('reveal').onclick = () => finish(false);
document.querySelectorAll('.modes button').forEach(b =>
  b.onclick = () => { if (b.dataset.mode !== mode) { wake(); setMode(b.dataset.mode); } });

audio.addEventListener('playing', () => {
  setIcon(true);
  drawLevels();
  $('next').classList.remove('offer');
  $('clipno').textContent = `CLIP ${String(round.heard.length).padStart(2, '0')}`;
  $('status').textContent = round.over ? 'Playing' : `Clip ${round.heard.length} · where is this from?`;
});
audio.addEventListener('pause', () => { setIcon(false); drawLevels(); });
audio.addEventListener('timeupdate', () => setRing(audio.duration ? audio.currentTime / audio.duration : 0));
// When a clip finishes, offer another artist from the same country.
audio.addEventListener('ended', () => {
  setRing(0);
  $('status').textContent = 'Clip finished · replay it or try another';
  if (!$('next').disabled) $('next').classList.add('offer');
});
audio.addEventListener('error', () => { if (audio.src && round && !round.over) loadClip(); });

$('guess').addEventListener('input', showSuggestions);
$('guess').addEventListener('keydown', e => {
  if (e.key === 'ArrowDown') { e.preventDefault(); moveSelection(1); }
  else if (e.key === 'ArrowUp') { e.preventDefault(); moveSelection(-1); }
  else if (e.key === 'Enter') {
    const pick = $('suggest').querySelector('[aria-selected="true"]');
    if (pick) submitGuess(pick.dataset.iso);
  }
});
$('suggest').addEventListener('click', e => {
  const li = e.target.closest('li');
  if (li) submitGuess(li.dataset.iso);
});

$('aboutlink').onclick = e => { e.preventDefault(); $('about').showModal(); };
// A click on the dimmed backdrop (the dialog element itself) closes it.
$('about').onclick = e => { if (e.target === $('about')) $('about').close(); };

if ('mediaSession' in navigator) {
  // Keep the lock screen from giving the answer away.
  navigator.mediaSession.metadata = new MediaMetadata({ title: 'Mystery track', artist: 'Soundabouts' });
}

wireVolume();
countries = window.SOUNDABOUTS_COUNTRIES || [];
if (countries.length) {
  for (const c of countries) {
    c.folded = fold(c.name);
    c.alias = ALIASES[c.iso] || '';
  }
  showTally();
  setMode(store.get('mode', 'modern') === 'trad' ? 'trad' : 'modern');
} else {
  $('status').textContent = 'Could not load the country list.';
}
})();
