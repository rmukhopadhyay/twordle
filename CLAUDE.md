# Twordle

A Wordle-inspired two-player guessing game, with a solo mode that plays like the original. Playable in any browser — desktop or mobile. Two players can play in person (pass-and-play on one device) or remotely on two devices — remote turns sync automatically through a tiny optional web relay (a Cloudflare Worker), with the URL/QR share link kept as both the required first-contact handoff and the offline fallback. The front end is a single self-contained HTML file, no build step, deployed via GitHub Pages; the relay is the only server-side piece and is effectively free.

**Live URL:** https://rmukhopadhyay.github.io/twordle  
**Repo:** https://github.com/rmukhopadhyay/twordle

---

## Architecture

Everything lives in `index.html` — HTML, CSS, and a single `<script type="module">` in one file. UI is built with **Preact + HTM**, with a couple of small utilities, all loaded from `esm.sh` at runtime:

```js
import { h, render } from 'https://esm.sh/preact@10.22.0';
import { useReducer, useEffect, useState, useRef, useCallback, useMemo } from 'https://esm.sh/preact@10.22.0/hooks';
import htm from 'https://esm.sh/htm@3.1.1';
import qrcode from 'https://esm.sh/qrcode-generator@1.4.4';   // QR rendering for share URLs
import LZString from 'https://esm.sh/lz-string@1.5.0';        // compress game state for URLs
```

HTM gives JSX-like syntax via tagged template literals — no build step, no transpiler. Attributes follow HTML conventions (`class`, not `className`).

The file is ~195 KB; ~140 KB of that is the embedded word lists. First load fetches ~25 KB of Preact + HTM + qrcode + LZ-string from the CDN (then browser-cached).

The state machine lives in a single `useReducer` at the top of the `App` component. Every screen transition is a named action; the reducer is the source of truth. Components are pure functions of `(state, dispatch)`.

---

## Word Lists

Two lists are embedded as JS constants:

- **`ANSWERS`** (~2,620 words): The pool of possible solutions, and the only words allowed as secret words (validated via `ANSWERS_SET`). Curated to "fair, commonly-known real words" — proper nouns, bare plural nouns, and Scrabble-junk removed; known-but-rare words (paean, pluot, umami) kept.
- **`VALID_GUESSES_EXTRA`** (~5,485 words): Extra legal guesses **disjoint from** `ANSWERS` — real but too obscure/inflected to be a fair solution (incl. plurals like "crabs"). Words like "amman"/"crwth" are in **neither** list.
- **`ALL_VALID`** (~8,105): `new Set([...ANSWERS, ...VALID_GUESSES_EXTRA])`, used for guess validation.

### Regenerating the lists (`tools/gen_wordlists.py`)
A committed, reproducible pipeline (replaces the lost original script). Stages:
1. `pool` — extract the current pool from `index.html`, attach **wordfreq** Zipf + a bare-plural-noun flag (**NLTK** WordNet lemmatizer), apply the guess floor (Zipf ≥ 1.0 drops the zero-frequency Scrabble-junk tier), and chunk the answer-candidates (Zipf ≥ 2.0, non-plural) into `tools/chunks/`.
2. **LLM curation** — Haiku subagents classify each chunk → `answer` / `guess` / `reject` (proper-nouns & junk → reject, removed from both lists), writing `tools/results/*.csv` (committed — the curation record).
3. `emit` — aggregate results + `tools/manual_allow.txt` / `manual_deny.txt` overrides → `answers.txt` / `guesses.txt`.
4. `splice` — rewrite the two `const` arrays in `index.html`.

```bash
python3 -m venv tools/.venv && tools/.venv/bin/pip install wordfreq nltk
tools/.venv/bin/python tools/gen_wordlists.py pool      # → chunks for the agents
# (run Haiku agents over tools/chunks/*.txt → tools/results/*.csv)
tools/.venv/bin/python tools/gen_wordlists.py emit      # aggregate + overrides
tools/.venv/bin/python tools/gen_wordlists.py splice    # write into index.html
```
`tools/.venv/`, `pool.json`, `chunks/`, `answers.txt`, `guesses.txt` are gitignored (regenerable); the script, manual lists, and `results/` are committed.

---

## Auth

Client-side password gate using the Web Crypto API. No server involved.

- **Salt** (baked into HTML): `2887189806cd0b6d11cfb3dab14548c1933e1205f23456f6bafeea6b52e5dbc0`
- **Expected hash** (`AUTH_HASH`): SHA-256 of `salt + password`, hex-encoded
- **Storage**: `localStorage` key `_wpa` — shared across all tabs/windows of the same origin
- **TTL**: 365 days, slid forward on each App mount via `writeAuth()`. An active user effectively never expires; an inactive user is kicked to login after a year.
- **Auth state** lives outside the reducer (separate `useState` in `App`) so it isn't part of the saved game state
- **Logout**: `clearAuth()` + `clearSaves()` removes `_wpa`, `_tw_games_v1`, and `_tw_meta_v1`. Then dispatches `RESET`.

To change the password, generate a new hash:
```python
import hashlib
salt = "2887189806cd0b6d11cfb3dab14548c1933e1205f23456f6bafeea6b52e5dbc0"
print(hashlib.sha256((salt + "new-password").encode()).hexdigest())
```
Then update `AUTH_HASH` in `index.html`.

---

## Game Modes

### Solo
Standard Wordle: 6 attempts, random word from `ANSWERS`. After the game ends, a summary screen shows the word, guess count, and a comment. From there the player can start a new game or return to the home screen.

### 2-Player ("Twordle")
Fixed 3-round match. Two delivery modes share **identical scoring and win conditions** — they only differ in how the device "passes" between players.

**Win conditions** (checked after each complete round, same in both modes):
- If one player solved their word and the other didn't → winner declared immediately, no more rounds
- If both solved or both stumped → tie that round, continue
- After all 3 rounds with no asymmetric stumping → compare **total guess counts** (lower wins)
- Equal total guesses → **tie** (the intended outcome is trash talk and a rematch)

Stumped players count as 6 guesses for the tiebreaker.

#### Pass-and-Play (mode `'l'`)

In-person on one shared device. Each "handoff" is a physical pass.

1. **Setup**: Enter both player names (Player 1 = device-holder/initiator, Player 2 = opponent)
2. **Mode chooser**: Pick Pass & Play (vs Remote)
3. **Word entry — Player 1 (initiator)**: Enters 3 secret words for Player 2 to guess. Words must be in `ANSWERS`. Inputs are masked by an overlay (see "Word-entry masking" below) with an eye toggle. All 3 must be different.
4. **Handoff**: Pass device to Player 2
5. **Word entry — Player 2**: Enters 3 words for Player 1
6. **Handoff**: Pass device back to Player 1 to start Round 1
7. **Each round**: Player 1 guesses → handoff → (Player 2 reviews P1's board + standings) → Player 2 guesses → round evaluated → handoff → … (the pre-turn `review` is shared with remote — see "Review screen")

#### Remote (mode `'r'`)

Asynchronous, one device per player. Every device-handoff in pass-and-play becomes a URL share moment — the initiator and opponent send the same link (with the encoded game state in the URL hash) back and forth via WhatsApp / iMessage / SMS / AirDrop / QR. Each player accumulates their active games on the home screen in an active-games list.

The entry flow is the same as Pass-and-Play through the mode chooser; picking Remote just switches the placeholder game's `mode` to `'r'` and routes word entry through a share screen instead of an on-device handoff. On receipt the opponent's app decodes the URL, drops the entry into their on-disk games dict, and lands them on the right next-step screen (accept-challenge, game, round-summary, or game-over). See "Remote 2-player mode" below for the exact turn sequence and the `advanceRemoteAfterTurn` logic.

---

## State Shape

The reducer state is **pointer + dict**: one `currentGameId` and a `games` map keyed by id.

```js
{
  screen: 'home' | 'setup' | 'mode-chooser' | 'accept-challenge'
        | 'word-entry' | 'handoff' | 'share' | 'review'
        | 'game' | 'solo-summary' | 'round-summary' | 'game-over',
  currentGameId: null | string,
  games: { [gameId]: Game },
  // transient UI (never persisted, never encoded):
  handoff: { badge, icon, title, desc, btnText, next } | null,
  pendingContinue: false,
  revealing: false,
  ui: { errorTick, errorMsg },
}

// Game (encodable):
{
  id, mode,                                // mode: 's' solo · 'l' local-2p · 'r' remote
  players: [p1, p2],
  challengeFor: [[w0,w1,w2], [w0,w1,w2]],  // [guesserIdx][roundIdx]
  results: [[r,r],[r,r],[r,r]],            // [roundIdx][playerIdx]
  round, turn, wordEntryPhase,             // wordEntryPhase: 0=P1 sets, 1=P2 sets
  target, guesses[], scores[][], current, over, won,
  turnFor, turnCounter,                    // remote-only
  reviewBoard,                             // sender's just-played snapshot {p,r,gs,sc,tg}; cleared by REVIEW_DONE
  myRole,                                  // per-device (0|1); stored in disk wrapper, NOT encoded
  _lastUpdate,                             // transient: sort key for the active-games list
}
```

Components read the active game via `getCurrentGame(state)`; reducer actions mutate it via `withCurrentGame(state, update)`. Summary screens (`SoloSummary`, `GameOver`, `RoundSummary`) compute their display data from the game directly — no separate state slices.

**Why validation lives in the reducer, not the Game component**: rapid keystrokes (typing the 5th letter and hitting Enter back-to-back) batch into one render cycle. A component-side validator reads `current` from a stale closure and rejects the guess. The reducer sees each dispatch's updated state, so `SUBMIT_GUESS` always validates against the latest `current`.

---

## Game encoding

`encodeGame(game) → LZ-string compressed JSON` is the **shared serialization** used for the URL hash (remote shares), each entry in the on-disk games dict, **and the web-relay payload** — all three carry the identical blob. Short keys, single-char score codes (`'a'`/`'p'`/`'c'`). Versioned via `v: 1`. The output of `LZString.compressToEncodedURIComponent` is already URL-component-safe — no separate base64 step needed.

`decodeGame` reverses it: `decompressFromEncodedURIComponent` → `JSON.parse` → reject anything whose `v !== GAME_VERSION`, else `materialize` back into a full game. (An earlier plain `base64url(JSON)` format and its decode fallback were removed once all testers had migrated — old links/saves no longer decode.) Typical fresh-challenge URL payload is ~217 chars; denser → sparser QR codes that scan better in poor lighting.

## Remote 2-player mode (mode `'r'`)

Same per-game shape as local 2P, but each device-handoff is replaced by sharing a URL. **Two hash formats** (see "Link formats" below): `<base>#r:<encodedGame>` carries full state; `<base>#g:<gameId>` is a stateless id link that pulls state from the relay. The hash is read on App mount (after auth) AND on every `hashchange` and `visibilitychange` event, then stripped via `history.replaceState`. Decoding is now **async** (an `#g:` link must fetch from the relay) and **reconciles**: it loads whichever of the URL's own state vs. the relay copy has the newer `turnCounter`. The listeners are critical: on iOS Safari (and sometimes Android Chrome) tapping a share link with the app already open focuses the existing tab and updates `location.hash` in place instead of reloading — without those listeners the UI would silently stay stale until the user reloaded or logged out and back in.

Turn order (initiator = P1, plays first):
1. P1 enters both names → ModeChooser → picks Remote → WordEntry → enters 3 secret words for P2 → `WORD_ENTRY_DONE` for mode `'r'` → ShareScreen
2. P2 opens link → AcceptChallenge → enters their 3 words → `REMOTE_ACCEPT_DONE` → ShareScreen (sends back)
3. P1 opens link → game R1 → plays → `REVEAL_DONE` advances state for P2 → ShareScreen
4. P2 opens link → game R1 → plays → both rounds done, eval determines next → ShareScreen
5. … alternates through R2, R3 → game-over

`advanceRemoteAfterTurn(g, results, justWon)` computes the state updates that position the URL recipient *exactly* at the right step (their target, their turn). If P1 just played, swap to P2's turn for the same round. If P2 just played and round eval continues, keep state at "round-summary handoff" (don't advance — the receiver advances via REMOTE_ADVANCE_NEXT_ROUND). If asymmetric stump or last round, mark `over` true. In every case it also captures a snapshot of the sender's just-played board into `reviewBoard` so the receiver can see it.

`turnFor` indicates which player the URL is meant for; `turnCounter` is a monotonic per-game counter (stored but not yet enforced for divergence detection — see open work below).

### End-of-game share affordance

When `REVEAL_DONE` produces `over: true` in remote mode, the **sender** goes to `game-over` (not `share`) and the `GameOver` component embeds the full share UI — URL, Copy Link, Show QR Code, Web Share when available. The change came from a real bug: a player tapped past the standalone share screen, the receiver never got the final result, and the game appeared stuck for them.

`dropLeavingCurrent` keeps a finished remote game in the dict when the sender still hasn't shared (`turnFor !== myRole`), so they can resume + re-share via the active-games list. Finished remote receivers and finished local matches are still dropped on leave.

### Review screen

Before a 2-player guesser takes their turn they land on a `ReviewBoard` first — it shows the previous player's actual guesses and tile colors against the secret word, plus "Bob solved it in 3!" / "Bob was stumped.", **a "Score so far" `Scoreboard`** (running standings through the round the opponent just played, so the player can calibrate risk before their turn), and a Continue button. **This is shared by both 2P modes** for parity: in remote it's reached on URL receipt (`reviewBoard && turnFor === myRole`); in local pass-and-play, `REVEAL_DONE` captures the just-played board into `reviewBoard` and `START_TURN` routes the incoming player to `review` before `game`. The first turn of a match has no `reviewBoard`, so it goes straight to `game`. Tapping Continue dispatches `REVIEW_DONE`, which clears `reviewBoard` and routes to whatever would have applied otherwise (`game`, `round-summary`, or `game-over`). The snapshot lives in the encoded URL as a compact `rb` field (player index, round, guesses, single-char score codes, target). The sender never sees `'review'` — `REVEAL_DONE` explicitly sets their screen to share/round-summary/game-over; `RESUME_GAME` only routes to review when `turnFor === myRole`.

### Open work / known gaps (deferred)

- **Stale-state clobber — now handled.** `LOAD_FROM_URL` and `RELAY_SYNC` both reject an incoming game whose `turnCounter` isn't strictly newer than the local copy, and the relay Worker enforces the same compare-and-set server-side. Re-opening an old link or a redundant relay poll is now an idempotent no-op instead of a clobber. (We silently ignore the stale state rather than showing a "this link is stale" prompt — that UI is still unbuilt, but the data-loss risk is gone.)
- **No anti-peek for secret words.** A curious opponent can decompress the URL hash — or now fetch the relay by game id — and see upcoming words. Honor-system for friends-and-family use.

## Web relay sync (optional, mode `'r'`)

Remote mode can hand turns off two ways: the original **manual share** (URL hash / QR) and an optional **web relay** that auto-delivers turns so players don't shuttle a link every round. The relay is **best-effort and additive** — if it's unreachable, disabled, or the network is hostile, remote mode silently falls back to exactly the manual-share behavior. Nothing about gameplay blocks on it.

### The Worker (`relay/`)

A tiny Cloudflare Worker + one KV namespace (`GAMES`). It's deliberately dumb — it store-and-forwards the *same* `encodeGame` blob the URL hash carries, keyed by the 6-char game id, and never understands the game:

- `GET /g/:id` → `{ v, tc, savedAt }` (404 if unknown)
- `PUT /g/:id` → 200 if `tc` (turnCounter) is strictly newer; **409 + the current record** if stale. This compare-and-set is the server-side half of the stale-state guard.
- KV entries TTL out after 7 days (mirrors the client's `SAVE_TTL_MS`).
- CORS locked to the Pages origin + `http://localhost:8765` (local dev).
- Live at `https://twordle-relay.rmukhopadhyay.workers.dev`. Deploy steps (wrangler login → `kv namespace create` → `deploy`) and `curl` smoke tests are in `relay/README.md`. KV namespace id and `workers_dev`/`preview_urls` settings live in `relay/wrangler.toml`.

### Client wiring (all gated behind `RELAY_URL`)

`RELAY_URL` is a const next to the URL-share helpers holding the Worker base URL. Set it to `''` to disable the relay entirely — the app then behaves byte-for-byte as it did pre-relay, so `main` is always safe to ship with the relay off.

- **`relayPut(game)` / `relayGet(id)`** — shuttle the encoded blob; every call swallows its own errors.
- **Push effect** (`App`): when the current remote game sits on a *sender* handoff screen — `share`, `round-summary`, or `game-over`, with `turnFor !== myRole` — push the encoded state. Deduped on the exact blob so incidental re-renders don't re-PUT. **Pushing on `round-summary` is load-bearing**: it's what makes the round boundary auto-deliver instead of forcing the "Send to X for Round N" tap.
- **Poll effect** (`App`): for every match where we're waiting on the opponent (`isWaitingRemote(g)`: remote, not over, `turnFor !== myRole`), poll `relayGet` every `RELAY_POLL_MS` (6 s) and immediately on tab focus. A strictly-newer `turnCounter` dispatches `RELAY_SYNC`. The effect no-ops (no interval) when nothing is waiting.
- **`RELAY_SYNC` action**: the *gentle* counterpart to `LOAD_FROM_URL`. A link tap (`LOAD_FROM_URL`) always navigates; a background poll (`RELAY_SYNC`) only navigates if the user is already looking at that game (`currentGameId === id`) — otherwise it just refreshes the dict so the active-games row flips to "your turn", never yanking the user out of Home. It also requires the game to already exist locally (you can't be synced into a match you've never heard of).

### First contact still goes by link

The relay can't deliver to a device that's never heard of the game id, so **the very first handoff (P1's challenge → P2) still needs the manual share link / QR.** Once P2 opens that first link, both devices know the id and poll, so every subsequent turn arrives automatically.

### Relay-primary UX

The UI treats the relay as the transport and the link as a *notification poke*, not a worse transport (we have no push, so the manual link is how you tell a human it's their turn). Concretely:

- **Sync status** — `App` holds a transient `syncStatus` map (`{ gameId: 'syncing'|'synced'|'failed' }`) via `useState`, **outside the reducer** (never persisted/encoded), updated by the push effect (`pushAndTrack` → `relayPut` now returns a delivered? boolean; 200 or 409 both = delivered). A failed push retries on tab-focus.
- **Waiting screens** — non-first handoffs render the `RelayShareBlock` `waiting` variant: a sync chip, a demoted "Nudge X a link" button, and the raw link/QR collapsed behind a toggle. On `failed`, the nudge elevates (`btn-g`) since that's when the manual fallback matters. The whole thing **degrades to the prominent `invite` UI when `RELAY_URL` is empty**, so relay-off behaves like the pre-relay app.
- **Active-games list** surfaces only the actionable `failed` state ("⚠ not sent"); full syncing/synced lives on the waiting screen.
- The sender lands on a dedicated **waiting screen** after a turn (not Home) — contextual, with a "Back to games" escape. The poll auto-advances them when the opponent plays.

## myRole (per-device perspective)

Each device tags every game with a `myRole` (0 or 1) — "which player am I in this match." Stored in the on-disk wrapper (`_tw_games_v1` entry → `myRole` field) alongside `savedAt`, **not** in the encoded game itself (each device has its own perspective; the URL shouldn't carry it). Set on game creation (initiator = 0) or first URL receipt (= `turnFor` at receive time).

Used by the active-games list to label `Your turn` vs `Waiting on Bob`, and by `RESUME_GAME` to pick the right screen.

## Active games list

Rendered on `Home` below the main buttons via the `ActiveGames` component. Shows every entry in the `games` dict that has a meaningful next step (blank/never-played solos are filtered). Each row:

- **Tap** → `RESUME_GAME({gameId})` — picks the right screen by mode and state (own turn → game/accept-challenge; waiting → share; over → game-over).
- **× button** → `DISMISS_GAME({gameId})` after a confirm prompt — manual cleanup (the 7-day TTL handles abandoned ones automatically).

Status labels are computed by `describeGame(g)`:

| Mode | State | Label |
|---|---|---|
| `s` | in-progress | "N/6 guesses" |
| `s` | over | "Solved" / "Stumped" |
| `l` | setup | "Setup in progress" |
| `l` | playing | "Round N of 3" |
| `r` | new challenge to me | "Accept the challenge" |
| `r` | my turn | "Your turn · Round N" |
| `r` | their turn | "Waiting on Bob" |
| `r` | over | "Match over" |

## Link formats — online nudge vs. offline pass

There are **two** kinds of remote link, to stop stale state-encoded URLs from littering the chat thread (and "time-travelling" the recipient to an old mid-game state when reopened):

- **`#g:<gameId>` — the online nudge.** Carries only the game id; opening it pulls the *current* state from the relay (`relayGet`). It's the **same short link every turn**, so it can't litter with N distinct stale links, and it can never time-travel (always resolves to "now", or to the final state once over). Built by `buildNudgeUrl`. This is what the **Nudge** button sends when the turn is `synced`.
- **`#r:<encodedGame>` — full state.** Works with **no relay at all**. Used by: (1) the **first invite** (recipient knows nothing yet, so it must carry everything — and works even offline), and (2) the **offline pass** (the link + QR behind the "No internet? Pass it manually" toggle, for SMS-only or in-person scanning). Built by `buildShareUrl`. The Nudge button also falls back to this when the turn **hasn't synced** (offline → the id link would have nothing to resolve to).

**Reconcile-on-open:** the hash loader (now async) fetches the relay copy for the link's game id and loads whichever has the newer `turnCounter` — so even an old `#r:` link, reopened while online, resolves to the latest state instead of time-travelling. `LOAD_FROM_URL`'s `turnCounter` guard is the final backstop against clobbering newer *local* state.

### Sharing UX

`RelayShareBlock` (invite variant) and the offline section both still offer Web Share / Copy Link / QR; the QR always carries **full state** (its whole point is the no-connectivity case). The encoded payload runs through LZ-string before going into the URL hash, which keeps the QR sparse enough to scan reliably even on a phone screen.

---

## Game State Persistence

Two `localStorage` keys:

- `_tw_games_v1`: `{ [gameId]: { value: <encoded>, savedAt } }` — the games dict. Each entry has its own TTL (7 days), so games age out independently.
- `_tw_meta_v1`: `{ currentGameId, screen }` — the "where am I" pointer (also TTL-wrapped).

State is saved on every reducer change via one `useEffect([state])` that calls `saveState(state)` → `writeGames` + `writeMeta`. Transient screens (`setup`, `word-entry`, `handoff`) fall back to `home` on rehydrate. `revealing` and `ui.errorTick` reset. Saved state is cleared on logout.

A finished game (`solo-summary` / `game-over`) is dropped from the dict when the user leaves it via Menu / Play Again / Change Mode — no UI path leads back into a finished game anyway. Blank solo games (started but never guessed) are also dropped on `GO_HOME`. Other in-progress games stay in the dict and surface in the active-games list on Home.

Handoff `next` is stored as `{type: 'START_TURN', round, player}` (data, not a function) so the whole reducer state remains JSON-serializable.

---

## Components

| Component | Renders for screen | Notes |
|---|---|---|
| `App` | (root) | Manages auth state separately; loads/persists reducer state; parses incoming share URLs from `location.hash` |
| `Login` | (auth gate) | SHA-256 check via Web Crypto |
| `Home` | `home` | Mode selection + logout + active-games list |
| `Setup` | `setup` | 2-player name entry (step 1 of unified 2P entry flow) |
| `ModeChooser` | `mode-chooser` | Step 2 of 2P entry: pick Pass & Play vs Remote |
| `AcceptChallenge` | `accept-challenge` | Opponent's first turn after opening a fresh challenge link: enters 3 words, sends back |
| `WordEntry` | `word-entry` | 3 masked inputs, eye toggle per row (pass-and-play word entry) |
| `Handoff` | `handoff` | Reads `state.handoff.next` and dispatches it on click (pass-and-play only) |
| `ShareScreen` | `share` | Two variants via `RelayShareBlock`: **invite** (first contact / relay off — prominent Share/Copy/QR) vs **waiting** (relay on — sync chip + demoted nudge). Detector: first invite ⟺ `challengeFor[0].length === 0`. Replaces `Handoff` for remote mode |
| `RelayShareBlock` | (shared) | The share/sync affordance. `variant='invite'` = prominent **full-state** link/QR (first contact); `variant='waiting'` = sync chip + a **Nudge** button that sends the stateless `#g:` id link (or full state if unsynced), with the **offline pass** (full-state link + QR) behind a "No internet? Pass it manually" toggle. Used by ShareScreen, RoundSummary (sender), GameOver (sender) |
| `ReviewBoard` | `review` | Previous player's just-played board snapshot + a "Score so far" `Scoreboard` (running standings), shown before each 2P turn in **both** local & remote; Continue → REVIEW_DONE |
| `Game` | `game` | Board + keyboard. Owns reveal animation timing, dispatches `REVEAL_DONE` |
| `SoloSummary` | `solo-summary` | Result + word + Play Again / Change Mode + 2-Player CTA |
| `RoundSummary` | `round-summary` | Scoreboard. Local-2P → handoff; remote-receiver → next round. Remote-sender: relay on → waiting (`RelayShareBlock`); relay off → "Send to X for Round N" button |
| `GameOver` | `game-over` | Stump / guess-total / tie outcome + final scoreboard. Primary **Rematch** button (`REMATCH` — fresh match, same players + mode, skips Setup/ModeChooser) + secondary **Home**/**Done**. **Remote sender** (turnFor !== myRole): relay on → "result on its way" + waiting affordance; relay off → prominent embedded share |
| `Scoreboard` | (shared) | Used by RoundSummary and GameOver. A stumped cell shows the **target word** (gold, from `challengeFor[pi][r]`) instead of a bare ✗; solved cells show the guess count |
| `TopBar` | (shared) | Unified header on every screen except `login` & `home`. Owns the left **Home/Cancel** button (`topbarLeftAction`). |
| `ActiveGames` | (shared, on Home) | List of in-progress games sorted by most recent activity; tap to resume, × to dismiss |
| `Disclaimer` | (shared, on Login + Home) | Small attribution + IP-respect notice ("Inspired by Wordle® by The New York Times Company...") |
| `RulesBlurb` | (shared) | Collapsible "How it works" explaining the non-obvious 2P scoring (3 rounds, pick words for each other, lowest total wins, asymmetric stump = instant win). On `ModeChooser` **and** `AcceptChallenge` (the invited friend never sees ModeChooser) |

### TopBar and the left button (Home / Cancel)

`TopBar` props: `state`, `dispatch`, optional `title`, `badge` (chip), `meta` (plain text under title), `right` (action node, e.g. solo's New Game). Its left button just **navigates home** — labeled **"⌂ Home"**, with **no confirm** — because going home is non-destructive: any real match is preserved in the Active games list and fully resumable.

The exception is the **initiator's setup flow**, where leaving discards a not-yet-saved challenge. There the button becomes **"✕ Cancel"**. The behavior is classified by `topbarLeftAction(state)`:

- `'cancel'` — `setup` (names) and `mode-chooser`: cancel instantly (nothing meaningful is entered yet; the placeholder game drops as a blank setup).
- `'cancel-confirm'` — `word-entry` with `wordEntryPhase === 0` (initiator typing their 3 words): confirms **"Discard this challenge setup?"** first.
- `'home'` — everywhere else, including the **opponent's** first setup (`accept-challenge`, local `word-entry` phase 1): the received challenge / live match is saved and resumable, so it just goes home.

**Why this replaced the old rule:** the previous "⌂ Menu" button confirmed *"End the current 2-player match and return to the menu?"* on nearly every 2P screen (gated by an old `isInProgressMatch` check). That was misleading — going home doesn't end the match, it parks it in Active games — and it made the button feel broken (tapping it "did nothing" when users reflexively cancelled the confirm). Home is now non-destructive by default; the only real discard is the initiator's pre-lock setup, which is the one place that confirms.

---

## Reveal Animation

The reducer marks `state.revealing = true` when a guess is submitted. The `Game` component watches `guesses.length` and schedules:

1. Per-tile flips with a 300 ms stagger (CSS `animation-delay` driven by local `revealedCols` counter)
2. After the full reveal:
   - **Win (solo)**: toast a praise message, then `REVEAL_DONE` after 2.4 s (so the bounce animation can play)
   - **Win (2-player, both local & remote)**: toast + immediate `REVEAL_DONE`
   - **Loss (solo)**: toast the answer, then `REVEAL_DONE` after 3.6 s
   - **Loss (2-player, both local & remote)**: toast + immediate `REVEAL_DONE`
   - **Mid-game**: `REVEAL_DONE` right after the animation

`REVEAL_DONE` clears `revealing`, sets `over`/`won`, and routes by mode:
- **Solo**: transition to `solo-summary`.
- **Local 2P**: set `pendingContinue` (game screen shows a Continue overlay after a 1.8 s delay).
- **Remote 2P**: compute the next-player state via `advanceRemoteAfterTurn` (which also snapshots the just-played board into `reviewBoard`), then choose screen:
  - **Game over** → `game-over` (with embedded share UI for the sender)
  - **Both finished this round, eval = continue** → `round-summary`
  - **Otherwise (mid-round handoff)** → `share`

---

## Mobile / Responsive

- Viewport meta includes `user-scalable=no, maximum-scale=1` to prevent double-tap zoom
- `touch-action: manipulation` on all keys and buttons to kill the 300 ms tap delay
- `overflow: hidden` + `overscroll-behavior: none` on html/body — keeps the game screen pinned (no rubber-banding while playing)
- **Scrollable non-game screens.** Because the body is `overflow: hidden`, every screen whose content can grow taller than the viewport (`#s-login`, `#s-setup`, `#s-mode-chooser`, `#s-accept-challenge`, `#s-word-entry`, `#s-home`, `#s-share`, `#s-handoff`, `#s-round-summary`, `#s-game-over`, `#s-solo-summary`, `#s-review`) gets `height: 100dvh; overflow-y: auto` so it scrolls internally. The inner body containers use `justify-content: safe center` — short content vertically centers, overflowing content pins to flex-start so the top header and bottom action button are both reachable.
- **All screen heights use `dvh`, never `vh`.** Every `#s-*` screen's `min-height` (and the scrollable ones' `height`) is `100dvh`, so layout tracks the *dynamic* viewport. This matters on iOS Safari: plain `100vh` measures the URL-bar-*retracted* (tall) viewport, so a `min-height:100vh` forces a screen taller than the visible area and clips the bottom (logo/disclaimer on login & home, action buttons elsewhere) while the floating URL bar is showing. `100dvh` shrinks with the bar, so nothing is cut off. (Earlier the screens had `height:100dvh` but a stale per-screen `min-height:100vh` was overriding it — both are `dvh` now.)
- Tile size uses `clamp(44px, calc((100vw - 52px) / 5), 62px)` — scales on narrow screens
- Keyboard uses `flex: 1` keys with 2px side padding so keys fill the viewport width
- Keyboard layout is 3 rows: QWERTYUIOP / ASDFGHJKL / Enter · ZXCVBNM · ⌫ (action keys are 1.5× a letter key, via `.key.wide`)

---

## Word-entry masking

The 3-word inputs on `WordEntry` and `AcceptChallenge` need to be hidden from a peeking opponent without triggering password-manager prompts. **We don't use `type="password"`** (browser saves a credential prompt) and **we don't use `-webkit-text-security: disc`** (iOS Safari briefly reveals the last typed character — Apple's "show last char" carry-over from real password fields).

Instead:
1. Input is `type="text"` with `autocomplete="off"`, `autocapitalize="characters"`, `autocorrect="off"`, `spellcheck="false"`, and no `name` attribute
2. CSS class `.masked` sets `color: transparent; caret-color: var(--text)` so the input is invisible-but-typable with a visible cursor
3. A `pointer-events: none` `.word-input-mask` div is absolutely positioned over the input and renders one `●` per character in the current value
4. The eye toggle just adds/removes the `masked` class

No browser quirks possible because we render the dots ourselves.

---

## Recent decisions worth knowing

These came up during iterative play-testing and informed the current state of the code — useful context for picking up future work.

- **2P entry flow is unified** through Setup → ModeChooser → WordEntry. The bespoke "Play Remotely" entry and `RemoteSetup` component were removed in favor of the unified path with a `mode-chooser` step that flips the placeholder game's mode to `'r'` or `'l'`.
- **Initiator (P1) sets words first in BOTH 2P modes.** `wordEntryPhase` semantics flipped: 0 = initiator setting, 1 = opponent setting. (Old: P2 set first in pass-and-play.)
- **End-of-game share UI lives on `game-over`**, not on a separate share screen, because users tapped past the share screen without sharing.
- **Finished remote games aren't dropped when the sender hasn't shared yet** — they show "Send final result to X" in the active-games list, tappable to re-share.
- **Hashchange + visibilitychange listeners on App** are load-bearing for the iOS in-tab navigation case. Without them, share links sometimes silently fail to update.
- **Solo summary copy was reverted** from a "warmup" theme to the original `Genius!`/`A hole-in-one. Legendary.` etc. The CTA below ("Ready for a real challenge? ⚔ 2 Player") is kept.
- **The disclaimer** (Wordle® attribution + fair-use notice) was a deliberate add — keep it on `Login` and `Home`.
- **Relay-primary UX, link demoted to a "nudge."** Remote handoffs now show a waiting screen with a sync chip; the share link is reframed as a notification poke (since there's no push) + offline fallback, not the primary transport. The one exception is the mandatory first invite. All of it degrades to the old share-forward UI when `RELAY_URL` is empty. See "Relay-primary UX" above.
- **Stumped players now see the answer persistently, not just a 3.5s toast.** A stumped guesser's word is surfaced on every screen they can land on: a `#stump-overlay` banner on the board (local players, who linger via `pendingContinue`), a `.stump-reveal` banner on the remote waiting screen (read from the `reviewBoard` snapshot's `tg`), and the word in the `Scoreboard` stumped cell (round-summary + game-over). The ephemeral loss toast stays as immediate feedback. Remote flow is unchanged — turns still auto-deliver; the answer just appears wherever the stumped player ends up. (The answer is always recoverable as `game.target` / `challengeFor[pi][r]` — no state changes were needed.)
- **Two link formats: stateless `#g:` nudge (online) vs. full-state `#r:` (first invite + offline pass).** State-encoded links were littering chat threads and time-travelling reopened-old-link recipients to stale mid-game states. The per-turn nudge is now a bare `#g:<id>` link (relay supplies state — constant per game, never stale); full state is reserved for the mandatory first invite and the explicit "No internet? Pass it manually" offline tool (link + QR). Opening any link reconciles against the relay (newer `turnCounter` wins). See "Link formats" above.
- **TopBar left button is "⌂ Home" (no confirm), "✕ Cancel" only in initiator setup.** The old "⌂ Menu" confirmed "End the current 2-player match?" on nearly every 2P screen — misleading (home parks the match in Active games, doesn't end it) and it felt broken. Home is now non-destructive by default; only the initiator's pre-lock setup discards anything, and only `word-entry` confirms. See "TopBar and the left button" above.

---

## Deployment

GitHub Pages, served from the `main` branch root. After any change to `index.html`:

```bash
git add . && git commit -m "description" && git push
```

Pages redeploys automatically within ~60 seconds.

**The relay deploys separately** and far less often (only when `relay/worker.js` or `relay/wrangler.toml` change), to the owner's Cloudflare account:

```bash
cd relay && npx wrangler deploy
```

The two are independent: the front end on Pages points at the Worker via `RELAY_URL`, and the Worker's CORS allowlist names the Pages origin. Pushing `index.html` with `RELAY_URL` set is what turns the relay on for live users.

---

## Local development

`index.html` runs from the filesystem in any modern browser, but ES module imports require an HTTP origin. Serve it locally with:

```bash
python3 -m http.server 8765
# then open http://localhost:8765
```

Port **8765 matters**: it's in the relay Worker's CORS allowlist, so the web relay works against the live Worker during local dev (two browsers / a normal + incognito window simulate the two players). For relay work specifically, `cd relay && npx wrangler tail` streams live Worker request logs, and `npx wrangler dev` runs the Worker locally against a simulated KV.
