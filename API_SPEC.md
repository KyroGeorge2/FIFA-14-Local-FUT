# FIFA 14 Local FUT API Specification

This file describes the practical API surface exposed by the local FUT server.

The server is not a normal REST API in the sense of a framework router. It is a route-dispatching HTTP layer in `server/probe.py` that calls methods on the active identity store instance (`LocalIdentityStore` or `BetaIdentityStore`).

---

## 1. Runtime mode selection

The mode is selected in the `main()` function of `server/probe.py`.

### Startup flags

- `--beta-mode`
  - If present, the server uses `BetaIdentityStore`.
  - Otherwise it uses `LocalIdentityStore`.

- `--fut-account-mode`
  - Choices: `new` or `existing`
  - This affects the initial identity state and whether the account is treated as first-use or returning-user.

The exact selection is here:

- `server/probe.py` in `main()`

Code path:

```python
identity_store = (
    BetaIdentityStore(str(identity_db), args.fut_account_mode)
    if args.beta_mode
    else LocalIdentityStore(identity_db, args.fut_account_mode)
)
```

Then the instance is assigned to the HTTP and Blaze servers:

```python
fut_http.identity_store = identity_store
main_blaze.identity_store = identity_store
```

So the effective “local” or “beta” progression mode is not a string value stored in a config file. It is selected at process startup and carried through as an object instance.

---

## 2. Route-to-function mapping

There is no separate route registry file. The mapping is implemented as a long `if/elif` chain inside `HttpProbe._handle()` in `server/probe.py`.

The pattern is:

1. read `path_without_query = self.path.partition("?")[0]`
2. compare it against known route strings
3. call methods on the current `identity_store`
4. send the result back as JSON/XML

### Example mappings

| HTTP route | Method | Identity method called |
| --- | --- | --- |
| `/ut/auth` | POST/GET | `start_session()` |
| `/ut/game/fifa14/user/accountinfo` | GET | `account_info()` |
| `/ut/game/fifa14/phishing` | GET | `phishing_question()` |
| `/ut/game/fifa14/phishing/validate` | POST | `validate_phishing_answer()` |
| `/ut/game/fifa14/user` | GET/POST | `ensure_fut_user()` / `update_club_profile()` |
| `/ut/game/fifa14/user/action` | GET | `user_actions()` |
| `/ut/game/fifa14/user/action/{name}` | POST/DELETE | `update_user_action()` |
| `/ut/game/fifa14/item` | GET/POST/DELETE | `club_items()`, `move_items()`, `quick_sell()` |
| `/ut/game/fifa14/squad` | GET/POST | `squad_list()`, `save_squad()` |
| `/ut/game/fifa14/transfermarket` | GET | `market_search()` |
| `/ut/game/fifa14/trade` | POST | `list_for_sale()` |
| `/ut/game/fifa14/purchased/items` | POST | `purchase_pack()` |
| `/ut/game/fifa14/club` | GET/POST | `club_items()`, `update_club_profile()` |
| `/ut/game/fifa14/...` | various | whichever branch in `_handle()` matches |

The actual route dispatch is all in a single file:

- `server/probe.py` → `HttpProbe._handle()`

This is the main “path to function” map.

---

## 3. Identity store responsibilities

### `LocalIdentityStore`

Defined in `server/local_identity.py`.

This is the base persistence layer for progress, club, squad, items, market, and user actions.

Key methods include:

- `account_info()`
- `start_session()`
- `ensure_fut_user()`
- `user_actions()`
- `update_user_action()`
- `phishing_question()`
- `validate_phishing_answer()`
- `purchase_pack()`
- `club_items()`
- `save_squad()`
- `market_search()`
- `trade_pile()`
- `list_for_sale()`
- `quick_sell()`
- `view_items()`
- `move_items()`
- `apply_consumable()`

### `BetaIdentityStore`

Defined in `server/beta_identity.py`.

This inherits from `LocalIdentityStore` and adds richer progression logic, such as:

- offline seasons
- tournaments
- match settlement
- coin wallet logic
- beta-only progression state

It is selected when `--beta-mode` is active.

---

## 4. Database location and persistence

The default SQLite database path is set in the `main()` function of `server/probe.py`.

```python
identity_db = (
    Path(args.identity_db).resolve()
    if args.identity_db
    else (SERVER_DIRECTORY.parent / "artifacts" / "local-fut.sqlite3")
)
```

So the default location is:

- repo root / `artifacts` / `local-fut.sqlite3`

On this machine that resolves to:

- `c:\Users\Madatek\source\repos\f14local\FIFA-14-Local-FUT\artifacts\local-fut.sqlite3`

This is created at runtime because the IdentityStore constructor calls:

```python
self.database.parent.mkdir(parents=True, exist_ok=True)
```

and then opens SQLite with:

```python
sqlite3.connect(self.database, timeout=10)
```

The database holds the actual progression state, including:

- identity and session records
- club data
- FUT user state
- squad records
- item inventory
- pack records
- market listings
- client data blobs
- user actions

### Important note

If you start the server without passing `--identity-db`, it will use the default path above. If you specify a custom path, it will use that instead.

---

## 5. Request/response conventions

### HTTP responses

Most FUT responses are JSON and are built via:

```python
build_fut_json_payload(document)
```

This serializes Python dicts as compact JSON with a trailing newline.

### Content type

The common response pattern is:

```python
self.send_header("content-type", "application/json; charset=utf-8")
self.send_header("cache-control", "no-store")
```

### Structured logging

All substantive traffic is logged through:

```python
def emit(kind: str, **fields) -> None:
    print(json.dumps({"time": ..., "kind": kind, **fields}), flush=True)
```

This gives debugging output such as:

- `http-probe`
- `blaze-request`
- `blaze-response`
- `fut-http-response`
- `fut-squad-state-beta222`
- `fut-purchased-items-pack-purchased`

---

## 6. Typical server lifecycle

1. `main()` sets up listeners and chooses an identity store.
2. `BlazeProbe` handles login/bootstrap traffic.
3. `HttpProbe` handles FUT HTTP routes.
4. `LocalIdentityStore` or `BetaIdentityStore` persists state to SQLite.
5. The client requests more data, and the server responds from database-backed state.

---

## 7. Useful entry points

If you are tracing a route, start here:

- `server/probe.py` → `main()`
- `server/probe.py` → `HttpProbe._handle()`
- `server/local_identity.py` → `LocalIdentityStore`
- `server/beta_identity.py` → `BetaIdentityStore`

These are the primary places where state, routing, and persistence intersect.
