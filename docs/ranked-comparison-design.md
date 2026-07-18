# Ranked station comparison design

Status: design spike; no service contract changes are implemented here.

## Decision

Extend `osservaprezzi_carburanti.compare_stations` without changing its existing
`stations` member. Ranking is opt-in: a request containing `ranking` adds a
top-level `ranked_v1` object. The MVP ranks one exact fuel identity and one
service mode by ascending price, excludes data that cannot be compared safely,
and explains every exclusion. It does not estimate route distance, travel cost,
or total trip cost.

This is deliberately service-only. A derived entity would need configuration,
lifecycle, availability, and naming decisions that add no value until the
service contract has proved useful.

## Compatibility baseline

The current contract is frozen by
`custom_components/osservaprezzi_carburanti/__init__.py:250-274`, registered with
`SupportsResponse.ONLY` at lines 282-285, described without request fields in
`custom_components/osservaprezzi_carburanti/services.yaml:7-9`, and asserted in
`tests/test_init.py:336-400`.

### Current request

The service accepts no documented fields. The handler does not read
`ServiceCall.data`; therefore an ordinary call is logically `{}`.

### Current response

The response is the following JSON-compatible shape. `entry_id` and `fuel_key`
are dynamic object keys.

```json
{
  "stations": {
    "<entry_id>": {
      "station_name": "string",
      "station_id": "string | integer | null",
      "brand": "string | null",
      "address": "string | null",
      "fuels": {
        "<fuel_key>": {
          "price": "number | null",
          "previous_price": "number | null",
          "price_changed_at": "ISO-8601 string | null",
          "is_self": "boolean | null",
          "last_update": "ISO-8601 string | null"
        }
      }
    }
  }
}
```

Traceability:

| Response value | Source | Missing-data behavior |
|---|---|---|
| `stations` key | handler literal | Always present; `{}` if no loaded coordinator has truthy data. |
| station object key | config entry ID from `_iter_coordinators()` | Internal HA identifier, stable for the life of an entry but not a domain station ID. |
| `station_name` | `nomeImpianto`, then `name`, then entry ID | Always a JSON string if source data follows the coordinator contract. |
| `station_id`, `brand`, `address` | `coordinator.data.station_info` | Missing values become `null`. |
| `fuels` key | coordinator fuel key, currently `<upstream name>_<self|servito>` | Object may be empty; names and casing are upstream-derived. |
| five fuel values | matching coordinator fuel fields | Missing values become `null`; timestamps are strings, not parsed datetime objects. |

The test fixture proves string station IDs, numeric prices, nullable-compatible
metadata, the five-field fuel projection, and omission of coordinators with
empty data. There is no fixture for multiple included stations, missing fields,
or ordering.

Python currently preserves the insertion order of entries and fuels while
building the dictionaries, but JSON object order is not a public ordering
contract. Consumers must address `stations` and `fuels` by key. The README's
dashboard example (`README.md:112-171`) sorts sensor states independently; it
does not consume this service response.

Stable public behavior is the top-level `stations` member and the projected
station/fuel fields above. Entry IDs, fuel-key construction, iteration order,
and station-name fallback are observable implementation details and must not be
used as ranking identity or tie-break policy.

## Data prerequisites from completed dependency work

The upstream validation introduced by Plan 009 requires every fuel item to
contain `name`, `price`, `fuelId`, `isSelf`, and `serviceAreaId`; it does not yet
enforce their scalar types. Coordinator output includes `fuel_id`,
`validity_date`, latitude, and longitude, although the compatibility projection
does not expose them.

Plan 003 commit `0cda0b4` makes `previous_price` and `price_changed_at` transition
metadata durable across unchanged refreshes: the first observation has both
values null, a change records the former price and current refresh time, and an
unchanged later observation preserves both. Ranking must use the current
`price`, never `previous_price`; transition metadata is explanatory only.

Fuel identity is sufficiently stable for an exact-ID MVP because `fuelId` is an
upstream field and Plan 009 rejects items that omit it. Runtime implementation
must additionally reject null, boolean, non-integer, or non-positive IDs from
ranking. A display name is not identity. If real payload characterization shows
one semantic fuel changing IDs, or one ID representing different fuels, stop
implementation and revisit this decision.

## Proposed request contract

Keep `{}` valid forever. Add one optional `ranking` object; do not add loose
top-level flags whose combinations become ambiguous.

```json
{
  "ranking": {
    "fuel_id": 1,
    "service_mode": "self",
    "max_age_minutes": 1440,
    "station_ids": ["123", "456"],
    "origin": {"latitude": 45.4642, "longitude": 9.1900}
  }
}
```

| Field | Required | Type and validation | Meaning |
|---|---|---|---|
| `ranking` | no | object, no unknown keys | Its presence opts into `ranked_v1`. |
| `fuel_id` | yes within `ranking` | integer, not boolean, `> 0` | Exact upstream fuel identity. Names are returned only for display. |
| `service_mode` | yes within `ranking` | enum `self`, `served` | Exact mode. `served` maps to coordinator `is_self == false`; avoid exposing the internal Italian key suffix. |
| `max_age_minutes` | no | integer, not boolean, `1..10080`; default `1440` | Maximum age at the service evaluation instant. |
| `station_ids` | no | array of 1..100 unique non-empty strings | Restricts candidates by domain station ID after string conversion, not entry ID. Omission means every active entry. |
| `origin` | no | object with only numeric `latitude` in `[-90,90]` and `longitude` in `[-180,180]`; booleans invalid | Adds straight-line distance as a secondary sort key and output fact. It never affects price eligibility. |

There is intentionally no `fuel_name` input in v1. Names are not stable
identity: casing, spelling, and branding aliases can differ. A later discovery
endpoint may expose available `(fuel_id, display_name)` pairs; silently resolving
a name would make results non-deterministic.

Unknown request fields, invalid types, bounds violations, duplicate station
IDs, or an incomplete origin raise Home Assistant's service-validation error
before the handler runs. A valid request with no matches is successful and
returns empty `items` plus exclusion counts; absence is not a validation error.

## Eligibility, freshness, and ordering policy

Capture one timezone-aware UTC `evaluated_at` at handler entry. Parse each
candidate's `last_update` as an ISO-8601 timestamp. An offset is mandatory;
`Z` means UTC. Convert parsed values to UTC before comparison. A missing,
unparseable, or offset-naive timestamp is `missing_timestamp` and ineligible.
No Home Assistant local-time assumption is allowed.

A fuel observation is eligible only when all of these hold:

1. its `fuel_id` is the requested positive integer;
2. `is_self` is exactly the boolean represented by `service_mode`;
3. `price` is a finite number greater than zero (booleans are not numbers here);
4. `last_update` is valid and `0 <= evaluated_at - last_update <= max_age`;
5. its station ID is non-null and, if a station filter exists, is selected.

A future timestamp is excluded as `future_timestamp`, rather than clamped. If a
station exposes duplicate eligible observations for the same fuel ID and mode,
exclude that station as `ambiguous_observation`; never choose by fuel-key order.

Eligible rows sort by this total ordering:

1. `price` ascending;
2. if `origin` is present and both station coordinates are valid, straight-line
   `distance_km` ascending; missing distance sorts after known distance;
3. freshness (`last_update`) descending;
4. station ID lexicographically after conversion to string;
5. entry ID lexicographically as the final uniqueness fallback.

Ranks are one-based positions in that total ordering. Equal prices are not
assigned equal ranks because deterministic array order is the contract. Each
item includes the sort facts, so a consumer can explain the result.

When requested, `distance_km` is the Haversine great-circle distance between
the supplied origin and registry coordinates, rounded to three decimal places
for display after sorting on the unrounded value. It is labeled
`straight_line_distance_km`. It is not road distance, route duration, fuel
consumption, or travel cost. Do not introduce any "nearest", "trip", or
"savings after travel" wording without a routing source and a new contract.

### Decision table

First matching exclusion wins, in this order, so counts remain reproducible.

| Case | Eligible? | Result/reason |
|---|---:|---|
| Coordinator has no data | no | Existing `stations` omission remains; ranking reason `station_unavailable`. |
| Station ID missing | no | `missing_station_id`. |
| Station not in requested subset | no | `station_not_selected`. |
| No observation with requested fuel ID | no | `fuel_not_available`; same-name other IDs do not match. |
| Matching ID appears more than once for requested mode | no | `ambiguous_observation`. |
| Matching ID exists only in the other service mode | no | `service_mode_not_available`; self and served never mix. |
| `price` null, boolean, nonnumeric, nonfinite, or `<= 0` | no | `invalid_price`. |
| Timestamp null/unparseable/has no offset | no | `missing_timestamp`. |
| Timestamp is after `evaluated_at` | no | `future_timestamp`. |
| Age equals `max_age_minutes` | yes | Inclusive boundary. |
| Age exceeds `max_age_minutes` | no | `stale`. |
| Same price, no origin | yes | Newer update, station ID, then entry ID break ties. |
| Same price, origin supplied, one coordinate missing | yes | Known distance first; missing distance stays eligible with null distance. |
| Same fuel name but different fuel IDs | only exact ID | Name is display metadata, never a match key. |

## Proposed response contract

The original `stations` value is built exactly as today, including omissions and
without reordering it for rank. A ranked call adds the named and versioned
collection below.

```json
{
  "stations": {"<unchanged legacy station map>": {}},
  "ranked_v1": {
    "schema_version": 1,
    "evaluated_at": "2026-07-18T01:30:00+00:00",
    "criteria": {
      "fuel_id": 1,
      "service_mode": "self",
      "max_age_minutes": 1440,
      "station_ids": null,
      "origin": null
    },
    "items": [
      {
        "rank": 1,
        "entry_id": "entry_1",
        "station_id": "123",
        "station_name": "Station Display",
        "brand": "Brand",
        "address": "Street",
        "fuel_id": 1,
        "fuel_name": "Benzina",
        "service_mode": "self",
        "price": 1.8,
        "last_update": "2026-07-18T00:30:00+00:00",
        "age_minutes": 60,
        "previous_price": 1.7,
        "price_changed_at": "2026-07-17T12:00:00+00:00",
        "straight_line_distance_km": null
      }
    ],
    "excluded": {
      "total": 1,
      "by_reason": {"stale": 1}
    }
  }
}
```

Normative JSON types:

- `schema_version` and `rank` are integers; `evaluated_at` is an offset-aware
  UTC ISO-8601 string; `items` is always an array.
- IDs in ranked output are canonical strings except `fuel_id`, which is an
  integer. `station_name` is a string; `brand`, `address`, `fuel_name`,
  `previous_price`, `price_changed_at`, and distance may be null.
- `price` is a finite JSON number. `age_minutes` is a non-negative integer
  floored from elapsed seconds. Distance is a non-negative finite number or null.
- `excluded.total` is the number of active config entries not represented by an
  item. `by_reason` contains only known reason keys with positive integer counts;
  it contains aggregate counts, not entry IDs, to avoid turning diagnostics into
  another consumer contract.

An unranked call returns exactly `{"stations": ...}` with no `ranked_v1` key.
Existing calls, response templates, and consumers therefore remain valid. The
`ranked_v1` name and `schema_version: 1` allow a future incompatible policy to
be added as `ranked_v2`; v1 must not silently change identity, eligibility, or
ordering semantics. Additive nullable item fields are permitted, but consumers
must ignore unknown fields.

## Delivery phases

1. **Characterize data.** Add pure fixtures from sanitized real payloads proving
   fuel-ID scalar types, duplicate behavior, timestamp offsets, and station
   coordinates. Stop if fuel identity violates the prerequisite above.
2. **Pure policy.** Implement a side-effect-free ranking helper with a supplied
   `evaluated_at`; implement straight-line distance locally with the Python
   standard library. No dependency or entity is needed.
3. **Service extension.** Add the nested voluptuous schema, read `call.data`,
   preserve the legacy projection byte-for-byte at the object-value level, and
   add `ranked_v1` only on opt-in calls.
4. **Documentation.** Document request examples, response use in HA action
   responses, freshness exclusions, exact service-mode semantics, and the
   straight-line distance limitation. Do not market travel savings.

No deprecation is proposed. The legacy `stations` member stays present in all
versions. If response size becomes a measured problem, any option to omit it
requires a separate additive request field and release note.

## Implementation test matrix

| Layer | Required cases |
|---|---|
| Pure ranking | exact ID despite same names; mixed IDs; self versus served; null/invalid/nonfinite/zero price; null/naive/malformed/future timestamp; just-fresh/equal-boundary/just-stale; duplicate observation; all tie-break levels; missing coordinates; Haversine reference value; stable result across input permutations. |
| Validation | `{}` accepted; each required ranking field; unknown keys; boolean-as-integer rejection; bounds; duplicate/empty station IDs; partial/out-of-range origin; valid default `max_age_minutes`. |
| Service response | legacy fixture unchanged for `{}` and ranked calls; `ranked_v1` absent without opt-in; full schema JSON serializable; empty matches successful; exclusion counts deterministic. |
| Multi-entry | unavailable coordinator; station subset by station ID; two entries for one station ID resolved by final entry-ID tie-break; insertion order does not affect items. |
| Transition metadata | apply Plan 003 first; unchanged refresh retains `previous_price` and `price_changed_at`; ranking still uses current price. |
| Real HA | `SupportsResponse.ONLY` action returns both keys; service schema appears in developer tools; response passes `json.dumps` without custom encoders. |
| Compatibility | freeze the current `tests/test_init.py:382-400` value as a fixture and assert equality of `response["stations"]` before and after extension. |

Repository gates for implementation are `python -m pytest -q` and
`git diff --check`; run focused service and ranking tests first. Run `hassfest`
and HACS validation before release when installed.

## Success signals

Use telemetry-free signals only:

- no compatibility-test changes to the legacy station payload;
- all ranking policies covered by deterministic pure tests, including input
  permutation tests;
- no bug reports showing stale, mixed-mode, or wrong-fuel comparisons during
  one release cycle;
- documentation examples can render a cheapest-first list without template-side
  filtering or sorting;
- maintainer feedback shows demand before any derived entity is designed.

## Open product questions

- Is a 24-hour default freshness limit appropriate for every fuel, or should
  callers always provide it? The proposed default matches the existing README's
  24-hour presentation filter but needs maintainer confirmation.
- Should a separate discovery response expose available fuel IDs and display
  names? Do not add name matching to ranking as a shortcut.
- Is aggregate exclusion reporting sufficient, or do users need opt-in per-entry
  diagnostics? Start aggregate-only to keep the contract and privacy surface
  small.
- Is origin-based straight-line distance useful enough for MVP? It is safe and
  accurately labeled, but may be omitted from phase one without changing the
  core ranking contract.

## Review stop conditions

Stop rather than improvising if payload characterization shows unstable fuel
identity, if product language implies road distance or travel-cost accuracy, or
if implementation cannot preserve `stations` exactly for old calls. The latter
requires explicit versioning and deprecation approval before code changes.
