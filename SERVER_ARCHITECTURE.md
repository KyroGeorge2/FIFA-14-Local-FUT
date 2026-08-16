# FIFA 14 Local FUT Server Architecture

This document is a working reference for how the local FIFA 14 FUT server is structured and what each part is responsible for.

## 1. Purpose

This project does not run a real EA backend. Instead, it creates a localhost compatibility layer that mimics the protocols and HTTP routes FIFA 14 expects during startup and gameplay.

The server is designed to:

- answer FIFA 14 startup/bootstrap traffic;
- present a believable FUT login/session flow;
- serve synthetic but structurally valid HTTP JSON/XML responses;
- persist local user state in SQLite so a fresh account can evolve across sessions;
- provide a stable local development/debug target for FUT reverse engineering.

The implementation lives primarily in:

- `server/probe.py`
- `server/local_identity.py`
- `server/beta_identity.py`

---

## 2. High-level runtime model

At startup, `server/probe.py` builds multiple listeners in parallel:

- redirector on TCP port `42127` (or TLS redirector mode)
- main Blaze listener on TCP port `42128`
- bootstrap HTTP server on port `8080`
- FUT HTTP server on port `8099`
- optional dynamic HTTP listener and optional LSX/GOSCA listeners

The `main()` function in `probe.py` creates the server instances, attaches the persistence layer, starts each `serve_forever()` thread, and then blocks while the process stays alive.

The important idea is that the process is a protocol shim, not a full game server. It exposes the exact ports and shapes FIFA 14 expects, then returns controlled synthetic payloads.

---

## 3. Main server components

### 3.1 `BlazeProbe`

This is the most important network listener for the login/bootstrap path.

It handles raw Blaze packets, parses headers, and responds to component/command combinations that FIFA 14 sends during login and config fetches. Important behaviors include:

- `OriginLogin` handling (`component == 1`, command `0x98`)
- `PreAuth` response (`component == 9`, command `7`)
- `FetchConfig` response (`component == 9`, command `1`)
- `Ping` and `PostAuth` responses
- fake login notifications such as user-authenticated / user-added / user-extended-data
- game reporting completion handling for match result notifications

This is where the fake “EA-style” network semantics are implemented. The server tries to produce valid Blaze TDF response bodies and match the expected retail schema as closely as possible.

### 3.2 `HttpProbe`

This is the HTTP compatibility layer used by FIFA 14 FUT routes.

It handles:

- local bootstrap XML like `/futBoot.xml`
- FUT localization XML (`/fut/loc/...`)
- dynamic messages endpoints
- FUT player metadata endpoints
- Icebreaker pack list fixtures
- `/ut/auth`
- `/ut/game/fifa14/...` account, club, item, squad, market, trade, pack, and tournaments endpoints
- debug endpoints like `/__fifa14_local_fut_health`

The handler is a giant route dispatcher. It inspects:

- path
- HTTP method
- query string
- request body
- identity store state

Then it returns a JSON/XML payload from either:

- a fixed synthetic document,
- a generated local identity response,
- or a database-backed stateful response.

### 3.3 `TcpProbe` and `TlsTcpProbe`

These are lower-level probe listeners used for connection and TLS diagnostics.

- `TcpProbe` reads raw bytes and identifies whether a payload looks like TLS or a Blaze frame.
- `TlsTcpProbe` wraps sockets in an SSL context and can act as a redirector/proxy-like endpoint for compatibility testing.

These are mainly used to observe what FIFA 14 tries to connect to and to reproduce old EA compatibility quirks.

### 3.4 `GoscaProbe`

This is a specialized listener for GOSca certificate/redirector behavior. It returns a certificate payload shaped like an EA-style XML response when enabled.

---

## 4. Identity and persistence model

### 4.1 `LocalIdentityStore`

`server/local_identity.py` defines the SQLite-backed persistence layer.

It creates tables such as:

- `identity`
- `sessions`
- `clubs`
- `fut_users`
- `squads`
- `squad_players`
- `items`
- `consumable_effects`

This store is responsible for:

- account/session creation
- user profile data
- club data and coins
- squad list and squad membership
- item collections, quick-sell, moves, activation
- consumable usage
- marketplace/trade state
- pack purchases and reward fulfillment

The state is stored in a SQLite database, with a default path under:

- `artifacts/local-fut.sqlite3`

This makes the server stateful across launches instead of a completely ephemeral mock.

### 4.2 `BetaIdentityStore`

`server/beta_identity.py` extends the base identity store for the newer beta progression flow.

It adds richer local FUT state for:

- starter clubs
- match assets and stadium data
- offline seasons and tournaments
- competition progression and rewards
- wallet transactions
- beta-specific match settlement behavior

The `main()` function selects one of the two stores via:

- `--beta-mode` to choose `BetaIdentityStore`
- otherwise `LocalIdentityStore`

---

## 5. How a login flow works

The server is designed to match the observed retail sequence.

### Startup sequence

1. FIFA 14 connects to the redirector/proxy listeners.
2. The redirector or TLS probe detects the requested server or redirector flow.
3. FIFA 14 sends Blaze requests like `OriginLogin`, `PreAuth`, and `FetchConfig`.
4. `BlazeProbe` answers with synthetic but valid records.
5. The client then requests FUT HTTP endpoints like `/ut/auth` and `/ut/game/fifa14/user/accountinfo`.
6. The `HttpProbe` returns JSON bodies that describe a local account, local club, local settings, and onboarding state.

### Identity initialization

The `LocalIdentityStore` creates a fresh local persona when the account is in a first-use mode. It keeps a single stable local identity and can also create a returning-user state when a club already exists.

The account payload is produced through helper functions such as:

- `build_fut_account_info()`
- `build_fut_auth_response()`
- `build_fut_phishing_question()`
- `build_fut_settings_response()`

---

## 6. How the HTTP routes work

The HTTP server is effectively a route table plus a stateful backend.

Common patterns:

- `path_without_query` is extracted from `self.path`
- a route match is tested with `if ... and path_without_query == "..."`
- a response is generated either from a helper or from the identity store
- the response is sent with JSON content type and a no-store cache header
- the server emits a structured log event with `emit()`

Examples of routes handled by the server:

- `/ut/auth`
- `/ut/game/fifa14/user/accountinfo`
- `/ut/game/fifa14/phishing`
- `/ut/game/fifa14/settings`
- `/ut/game/fifa14/club`
- `/ut/game/fifa14/item`
- `/ut/game/fifa14/squad`
- `/ut/game/fifa14/transfermarket`
- `/ut/game/fifa14/trade`
- `/ut/game/fifa14/purchased/items`
- `/ut/game/fifa14/clubUser`
- tournament and offline season endpoints

The handler deliberately returns built-in synthetic data rather than failing 404s for most of the routes it has been proven to need.

---

## 7. Why it uses synthetic payloads instead of a true backend

This project is built around compatibility and observation, not a full authenticated online service.

The server tries to satisfy the client’s expectations by keeping the following true:

- response structure matches the expected game contract;
- IDs and nested fields look like real FUT data;
- state persists across sessions;
- the client can continue through onboarding, squads, packs, and market flows;
- logs make failures discoverable.

The server intentionally avoids pretending to be a real EA service. It is a local emulation/shim tuned to FIFA 14’s client-side expectations.

---

## 8. Logging and debugging

The code uses a central `emit()` helper that prints JSON logs to stdout.

This produces structured events like:

- `started`
- `http-probe`
- `blaze-request`
- `blaze-response`
- `fut-http-response`
- `fut-local-item-quicksell`
- `fut-squad-state-beta222`
- `fut-purchased-items-pack-purchased`

This is one of the most useful debugging features in the repo. If something breaks in the client, the logs usually show the exact path, request body, response name, and state transition that occurred.

---

## 9. Typical request path during gameplay

A normal local FUT session usually flows like this:

1. client connects to redirector / Blaze listeners
2. `OriginLogin` returns a session token and local persona info
3. config fetch runs and sets FUT bootstrap URLs
4. client calls `/ut/auth`
5. client calls `/ut/game/fifa14/user/accountinfo`
6. phishing and trusted-device flows resolve
7. club/user info and stored actions load
8. local club and squad are loaded
9. store/pack routines, item operations, market, and tournament routes are served from the identity store
10. all changes are persisted into SQLite

---

## 10. Important implementation notes

- This server is intentionally local-only and uses loopback addresses.
- It binds to specific ports and can enforce exclusive port ownership on Windows.
- It creates temporary certificate chains when redirector TLS compatibility is required.
- The project is tuned around observed FIFA 14 client behavior and reverse engineering data rather than a formal external spec.
- It is designed for development, local testing, and compatibility experimentation.

---

## 11. Files to look at first when debugging

If you need to understand or extend the server, read in this order:

1. `server/probe.py` — runtime setup and all protocol/HTTP handlers
2. `server/local_identity.py` — core persistent FUT identity and item logic
3. `server/beta_identity.py` — beta progression and richer FUT simulation
4. The generated SQLite DB under `artifacts/` — current persisted local state

---

## 12. Summary

The server is a local FIFA 14 compatibility runtime that intentionally fakes the requirements of the game’s network protocol and HTTP REST layer. It is not a generic web API; it is a reverse-engineered, stateful compatibility server built to satisfy FIFA 14’s expectations and persist local FUT state for experimentation and debugging.

If you are extending the project, start from the port setup in `main()`, then follow the router logic in `HttpProbe._handle()` and the Blaze logic in `BlazeProbe.handle()`.
