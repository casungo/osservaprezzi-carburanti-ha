# Nearby station discovery design

Status: decision-ready design spike; no implementation is authorized by this document.

## Decision summary

Build the MVP as a **local search over the official MIMIT daily active-station registry**, initialized through the integration-wide `CSVStationManager`. Use an explicitly selected location source, calculate distances inside Home Assistant, show at most 20 deterministic results, and keep the existing station-ID path available at every failure point.

Do not call `POST /ospzApi/search/zone` in the MVP. The endpoint is observed and described in `API_NOTES.md`, and MIMIT documents a public zone-search product, but the repository has no official machine-API contract, rate limits, or reuse terms for that endpoint. Its use is therefore a later opt-in enhancement, gated on written upstream permission or published API terms. The official CSV is different: MIMIT explicitly publishes it daily under IODL 2.0.

The MVP transmits no user location to MIMIT or any new third party. It stores neither the search origin nor radius after setup; the resulting config entry remains the existing `{station_id: string}` contract.

## Evidence and current constraints

Repository evidence:

- `custom_components/osservaprezzi_carburanti/config_flow.py` currently asks for a string station ID, validates it through the station-detail API, uses `station_<id>` as the unique ID, and stores only `station_id`.
- `custom_components/osservaprezzi_carburanti/csv_manager.py` downloads the official registry with Home Assistant's shared client, serializes initialization with an operation lock, parses outside the event loop, writes atomically, and caches station ID, operator, brand, type, name, address, municipality, province, latitude, and longitude. Rows without usable coordinates are excluded.
- `custom_components/osservaprezzi_carburanti/const.py` points `CSV_URL` at MIMIT's active-station CSV. `async_initialize()` loads a valid cache first and otherwise downloads; a stale cache currently triggers a refresh and initialization fails if that refresh fails.
- `tests/test_csv_manager.py` provides pipe/semicolon fixtures, Italian decimal coordinates, malformed rows, conditional HTTP metadata, and initialization concurrency examples.
- `README.md` and `README.it.md` currently send users to MIMIT's zone-search UI to discover an ID manually.
- `API_NOTES.md` records a single-point/radius request and sample response for `POST https://carburanti.mise.gov.it/ospzApi/search/zone`, but this is repository observation, not an official API guarantee.

Official upstream evidence (checked 18 July 2026):

- [MIMIT's open-data page](https://www.mimit.gov.it/index.php/it/component/content/article?id=2032336%3Acarburanti-prezzi-praticati-e-anagrafica-degli-impianti) states that the active-station registry is experimental, published daily, contains the information in force at 08:00 on the preceding day, and is licensed IODL 2.0. It also documents the pipe separator introduced on 10 February 2026.
- [MIMIT's Osservaprezzi description](https://www.mimit.gov.it/it/mercato-e-consumatori/prezzi/mercati-dei-carburanti/osservatorio-carburanti) describes a public consumer search covering stations nationally and real-time prices.
- MIMIT's official service inventory, `Allegato_-_Ricognizione_dei_servizi_direttamente_erogati_allutenza_2.pdf`, pp. 28–29, says citizens can search by a point on a map, geographic area, route, motorway segment, or station, and that submitted prices appear immediately.
- [The portal privacy notice](https://carburanti.mise.gov.it/documenti/Privacy.pdf), pp. 9–10, says browser geolocation is voluntary and consent-based, manual address or municipality remains possible, coordinates/IP are used temporarily and not stored, and Google Maps may receive necessary technical data. That notice governs MIMIT's portal, not this integration; it is a useful minimization baseline, not permission for this integration to send HA location anywhere.

## Data-source comparison

| Property | Cached official registry | MIMIT zone endpoint | Hybrid |
|---|---|---|---|
| Input | Search origin, radius, optional text/fuel-like metadata filter | Exact point(s), radius; repository sample uses latitude/longitude | Same origin plus cache state; endpoint only on cache miss/staleness |
| Output | Active stations with registry metadata and coordinates; distance computed locally | Sample includes ID, name, fuels/prices, coordinates, brand, distance and insert date | Cache supplies stable identity/details; endpoint may supply fresher prices/ranking |
| Freshness | Daily snapshot: preceding day's state at 08:00 | Product described by MIMIT as real time; machine endpoint has no official SLA | Mixed and potentially confusing unless each field carries source/time |
| Coverage | National active registry; rows without coordinates are excluded by current parser | Public product is national; exact endpoint completeness is undocumented | Best theoretical coverage, but inherits cache coordinate gaps and endpoint uncertainty |
| Latency/load | One shared conditional CSV refresh, then local O(n) scan; no per-search upstream call | One network call per search/retry, plus possible station-detail validation | Highest complexity and upstream load |
| Limits/errors | Existing 60 s timeout, conditional ETag/Last-Modified, atomic cache; no published file SLA | No documented authentication, rate limit, retry, error schema, or stability contract | Two independent failure domains |
| Offline | Works with a previously loaded cache; current initialization policy needs adjustment to permit stale-on-refresh-failure | Does not work | Can fall back to cache only if semantics are explicit |
| Setup before cache | Must safely initialize the shared manager; first setup needs network and may download a national CSV | Yes, if endpoint remains reachable | Yes, but at privacy/contract cost |
| Reuse status | Explicit IODL 2.0 | Public UI is documented; direct API reuse is not | CSV portion clear, API portion unresolved |

The registry is safe to use only through the single integration-wide owner established by Plan 006. Config flow must not create an untracked manager per flow or race config-entry setup, clear, or refresh operations. If that owner cannot expose a bounded `async_ensure_registry()` operation usable before the first config entry, implementation must stop and revisit ownership.

## Location and privacy model

All coordinates are sensitive configuration-time input even if they identify a home already stored in HA. "Local" below means inside the HA process; frontend-to-HA transport remains protected by the user's HA connection.

| Origin option | User action and precision | Transmitted outside HA | Persisted by integration | Rounding | Logging | Retention/deletion |
|---|---|---|---|---|---|---|
| HA home (recommended default) | Explicitly choose “Use Home location”; typically precise | Nothing in MVP | Nothing | Use precise value in memory; do not round because no external transfer/storage occurs | Never coordinates; log only source=`home` and coarse outcome count at debug | Discard when flow ends/aborts; changing HA home affects only a later new search |
| Map/current location | Explicit consent in frontend; device precision may be high | Nothing in MVP | Nothing | Same as above | Source=`current` only | Discard at flow end; no history |
| Manual coordinates | User types lat/lon | Nothing in MVP | Nothing | Validate bounds; use in memory only | Source=`manual_coordinates` only | Discard at flow end |
| Municipality/province | User types/selects a coarse public area | Nothing if matched locally against registry strings | Nothing | Not applicable | Normalized area category, never free text | Discard at flow end |
| Address/geocoding | Exact address may expose home to a provider | **Not allowed in MVP** | None | Future provider request should be rounded only if provider supports useful results; otherwise require explicit disclosure | Never address/query | Future provider-specific policy required before implementation |

The config flow must display before location selection: “Your chosen location is used temporarily inside Home Assistant to rank stations. This integration does not store it or send it to MIMIT or another provider.” Choosing HA home location is not implicit consent: the user selects it. Denying/omitting location never blocks manual ID setup.

No location, address, distance linked to a location, raw search text, IP address, or map viewport enters logs, diagnostics, analytics, issue payloads, or config-entry data. Registry station coordinates are public-source cache data and follow the existing cache lifecycle. Clear-cache removes both registry files; search-origin memory is released when the flow object ends. Home Assistant may retain flow metadata according to Core behavior, so implementation must keep sensitive values out of form errors and titles.

## Config-flow journey

### Entry and controls

The initial step offers two actions in this order:

1. **Find nearby stations** — explanatory privacy copy and source choice.
2. **Enter station ID** — the current form, unchanged as the universal fallback.

Discovery source choices are HA home, current/map location when the frontend supports it, municipality/province, and manual coordinates. Default radius is 5 km; selectable values are 2, 5, 10, 20, and 50 km. MVP filters are optional case-insensitive brand/name text and station type when present. Do not promise fuel availability from the registry because current registry fields do not contain fuels. Fuel/price filtering belongs to a later live-data phase.

Search locally, calculate great-circle distance, sort by `(distance_meters, normalized_name, numeric_or_string_station_id)`, and cap at 20 results. Show a specific “20 closest shown; narrow the radius or filter” message instead of pagination in MVP.

Each result card/selector label conveys, without color alone:

- station name and brand (with “Unknown brand” fallback);
- distance rounded for display only (100 m below 10 km, 1 km thereafter);
- address, municipality and province when present;
- station type when present;
- station ID as secondary text for disambiguation.

Selection still calls the existing station-detail validation before creating the entry. Final persisted data and unique ID remain `{station_id}` and `station_<id>`. The discovery origin, radius, filters, rank, and cache timestamp are not stored.

### Required paths

| Path | Required behavior |
|---|---|
| Happy | Explain privacy → select source → radius/filter → initialize shared registry → show results → select → validate detail → create existing config entry. |
| Empty | Explain whether no coordinate-bearing stations matched; offer change radius/filter, change source, retry registry, and manual ID. Never auto-expand beyond the chosen radius. |
| Ambiguous/capped | Show deterministic closest 20, explicit cap, station IDs and addresses; allow refining or manual ID. Never auto-select. |
| Location denied | Return to source selection with non-blaming copy; offer municipality, manual coordinates, and manual ID. Do not repeatedly request browser permission. |
| Network failure, no cache | Explain the official registry is unavailable; offer retry and manual ID. Manual ID validation may also fail and must retain the existing `cannot_connect` behavior. |
| Network failure, valid cache | Use cache and label results “Registry last updated <local date/time>”; do not block solely because refresh failed. This stale-cache behavior requires an explicit manager contract rather than reaching into private state. |
| Corrupt/empty cache | Quarantine/replace via manager policy; attempt one download; if unavailable, show cache failure and manual ID. Never search partially parsed state. |
| Duplicate | Call `async_set_unique_id` before final validation as today; abort with the standard already-configured result. The discovery UI may mark IDs already configured, but Core remains authoritative. |
| Selected station disappears | If detail validation returns 404, return to results with “Station no longer available”, remove it from the in-flow list, and offer refresh/manual ID. |

Back preserves entered radius and non-sensitive filters in flow memory but not precise origin after leaving discovery. Cancel/abort clears all flow-local data.

### Accessibility and mobile

- All actions and results must be keyboard reachable and screen-reader named; focus moves to the step heading or error summary after each submit.
- Use standard HA selectors and forms, not a custom map, in MVP. Never encode brand/type/selection with color alone.
- Result labels must remain distinguishable when truncated; put distance and station ID early enough for narrow screens, and expose the full address as supporting text.
- Touch targets follow HA frontend components; no hover-only information. Announce result count and cap through normal form description/error semantics.
- Validate latitude/longitude with localized inline errors and accept decimal input independent of the registry's Italian decimal representation.

## Recommended minimal architecture

1. Extend the Plan 006 integration-wide registry owner with a public, serialized setup-time contract: `async_ensure_registry(allow_stale=True) -> RegistrySnapshot`. The snapshot is immutable/read-only for the caller and contains stations plus `updated_at` and `is_stale`; it never exposes cache paths or mutable manager internals.
2. Add pure discovery helpers in a small module: input validation, Haversine distance, normalized metadata filters, deterministic sorting, and cap. Helpers accept a station iterable and return new result records; they perform no I/O and mutate no station dictionaries.
3. Keep config-flow state ephemeral. Only the selected station ID crosses into the existing `_validate_station` and entry-creation contract.
4. Reuse Home Assistant's config-flow selectors and translations. Do not add geocoder, map SDK, spatial database, background index, or new dependency.

### Contracts

Conceptual contracts (names may be adapted to repository conventions during implementation):

```python
@dataclass(frozen=True)
class RegistrySnapshot:
    stations: tuple[Mapping[str, object], ...]
    updated_at: datetime | None
    is_stale: bool

@dataclass(frozen=True)
class DiscoveryQuery:
    latitude: float
    longitude: float
    radius_km: float
    text_filter: str | None
    station_type: str | None
    limit: int = 20

@dataclass(frozen=True)
class StationCandidate:
    station_id: str
    name: str
    brand: str | None
    address: str | None
    municipality: str | None
    province: str | None
    station_type: str | None
    distance_meters: int
```

Errors must distinguish `registry_unavailable`, `registry_invalid`, `location_denied`, `invalid_location`, and the existing station validation errors. No exception or form placeholder may contain coordinates or address input.

## Rejected alternatives

- **Zone endpoint only:** fresher and avoids a full registry download, but direct API reuse terms, limits, schema and stability are not published in the evidence found. It also sends a precise search point to MIMIT on every query. Reconsider only after upstream authorization and an explicit integration privacy disclosure.
- **Hybrid MVP:** adds two sources, source reconciliation, timestamps, failure modes, and location transmission before local discovery has been validated. Its marginal value does not justify the privacy and maintenance cost.
- **External geocoder/map:** would disclose user input to a new provider and require provider selection, terms, attribution, retention, and consent decisions. Municipality matching and manual coordinates cover the MVP without it.
- **Persisting home/radius for recurring recommendations:** this feature configures one station; retaining a search profile is unnecessary and expands sensitive state.
- **Spatial index/database:** a one-off O(n) scan over a daily in-memory national registry is simpler. Benchmark before considering an index; do not speculate.
- **Automatic HA-home use:** convenient but violates explicit choice and makes location processing surprising.

## Rollout

### MVP

1. Land Plan 006 and prove one safe shared manager can initialize during config flow.
2. Add stale-readable snapshot semantics and pure bounded search helpers.
3. Add translated source/radius/filter/result steps and manual-ID escape routes.
4. Release behind an internal feature constant for one beta cycle if maintainers want rapid rollback; otherwise expose as an optional first-step action while preserving ID setup.
5. Document local-only location processing and cache timestamp semantics.

### Later, only with evidence

- Municipality autocomplete from local registry values.
- Fuel/price ranking using bounded detail fetches or a licensed bulk price dataset, with clear timestamps and upstream-load budgets.
- Map display/current-device location if HA frontend contracts support it accessibly.
- Zone API/hybrid fallback after published terms or written permission, schema fixtures, rate policy, explicit opt-in privacy copy, and rounded-location feasibility analysis.
- Multi-select/batch entry creation only after Core config-flow UX and partial-failure behavior are designed.

No existing entry migration is required: entries keep the current config-flow version and `station_id` data. Adding discovery changes only new setup. If a later release persists discovery preferences, that requires a new version and separate migration/privacy review.

## Verification strategy and acceptance criteria

### Automated tests

- Unit-test coordinate bounds, Haversine fixtures, exact-radius inclusion, missing/invalid station coordinates, accent/case normalization, filters, stable tie-breaking, cap=20, and proof that source mappings are not mutated.
- Config-flow tests cover happy, empty, capped/ambiguous, denied location, invalid manual coordinates, registry unavailable, stale-cache success, corrupt-cache failure, selected-station 404, validation network error, duplicate entry, back/cancel cleanup, and manual-ID fallback from every error step.
- Registry tests prove concurrent setup/config-entry initialization downloads once, stale snapshot survives refresh failure, empty/corrupt state is never returned as valid, and clear/update/search serialization is safe.
- Real-HA lifecycle coverage loads no entry before discovery, completes an entry, reloads/unloads it, and confirms normal HA state semantics.
- Keep bounded, sanitized fixtures for the official CSV metadata format. A zone response fixture may document future investigation but must not make MVP tests depend on the unsupported endpoint.

### Machine-checkable gates

- `python -m pytest tests/test_discovery.py tests/test_config_flow.py tests/test_csv_manager.py -q` exits 0.
- `python -m pytest -q` exits 0.
- Optional when installed: `hassfest --action validate --path .` and `hacs validate integration custom_components/osservaprezzi_carburanti` exit 0.
- `git diff --check` exits 0.
- A network-spy test proves a discovery search with every MVP origin source makes no request containing coordinates or address text; only registry initialization and final station-ID validation may access MIMIT.
- A config-entry assertion proves successful discovery stores exactly the existing station-ID field and no origin, radius, filter, distance, or cache metadata.

### Privacy-safe success signals

The default is no telemetry. Maintainers can evaluate adoption from opt-in, aggregate issue/release feedback and local debug counters that are never exported: discovery started/completed/fell back, result-count bucket (`0`, `1–5`, `6–20`, `capped`), cache path (`fresh`, `stale`, `downloaded`, `failed`), and elapsed-time bucket. Do not record source coordinates, municipality/address, radius, station result IDs, chosen station, query text, exact result count, IP, device ID, or combinations that permit inference. Logs use event names and coarse buckets only; debug counters reset on restart and have no retention beyond process memory.

Success for the first release means: no location leakage in automated tests; no regression in manual-ID completion; discovery completes from a warm cache without network search calls; at least 95% of synthetic nationwide coordinate fixtures return deterministically within 500 ms on supported hardware; and zero unhandled config-flow exceptions in maintainer testing. Do not set adoption targets until an opt-in, privacy-reviewed measurement mechanism exists.

## Open owner decisions

| Decision | Owner | Blocks | Recommendation |
|---|---|---|---|
| Can the Plan 006 registry owner safely initialize before any config entry and return stale data after refresh failure? | Integration maintainer | MVP implementation | Require this contract; stop rather than instantiate a second manager. |
| Which HA-supported selector/API can obtain current frontend location with explicit consent and accessible fallback? | HA frontend/config-flow maintainer | Current-location option only | Ship HA home, municipality, and manual coordinates first if uncertain. |
| Should municipality matching be free text or a local selector? | UX/translation maintainer | Municipality path | Start normalized free text; add autocomplete later if payload size is acceptable. |
| Is station type useful and consistently populated enough to expose? | Product maintainer, using registry sample metrics | Type filter | Hide the filter if missing/unknown exceeds an agreed threshold; never block discovery. |
| Are direct zone-endpoint calls authorized, rate-limited, and stable for third-party HA integrations? | Maintainer contacting MIMIT/Infocamere | Zone/hybrid only | Treat as unsupported until answered in writing or official docs are published. |
| What is the minimum supported Core version for any location selector used? | Release maintainer | Selector choice | Use only APIs available at the integration's declared minimum HA version. |

Only the first decision blocks the cache-only MVP. Unanswered zone/API and current-location questions do not block shipping the local registry path.
