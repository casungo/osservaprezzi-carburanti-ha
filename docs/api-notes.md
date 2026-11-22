# MIMIT data sources

## Active station registry

The integration downloads the [active stations CSV](https://www.mimit.gov.it/images/exportCSV/anagrafica_impianti_attivi.csv) to discover stations locally. The first line contains the extraction date, the second contains column headers, and subsequent lines contain station records. The header determines the separator: pipe (`|`) or the legacy semicolon (`;`).

| CSV column | Internal field |
| --- | --- |
| `idImpianto` | `id` |
| `Gestore` | `operator` |
| `Bandiera` | `brand` |
| `Tipo Impianto` | `station_type` |
| `Nome Impianto` | `name` |
| `Indirizzo` | `address` |
| `Comune` | `municipality` |
| `Provincia` | `province` |
| `Latitudine` | `latitude` |
| `Longitudine` | `longitude` |

Coordinates support Italian decimal commas and are validated against latitude and longitude ranges. The station ID and valid coordinates are required for a record.

The integration stores the response's `ETag` and `Last-Modified` validators when available and sends `If-None-Match` and `If-Modified-Since` on eligible refreshes. A `304 Not Modified` response retains the cached registry.

## Station details

`GET https://carburanti.mise.gov.it/ospzApi/registry/servicearea/{station_id}` returns station details and prices. A compact representative response is:

```json
{
  "id": 37021,
  "name": "eni ostiense",
  "nomeImpianto": "agip ostiense",
  "brand": "AgipEni",
  "address": "VIA CIRCONVALLAZIONE OSTIENSE 230 - ROMA",
  "fuels": [{
    "name": "Benzina",
    "price": 1.784,
    "fuelId": 1,
    "isSelf": true,
    "serviceAreaId": 37021,
    "insertDate": "2025-11-16T18:06:03Z",
    "validityDate": "2025-11-16T18:06:03Z"
  }],
  "services": [{"id": "6", "description": "Bancomat"}],
  "orariapertura": [{
    "giornoSettimanaId": 1,
    "oraAperturaMattina": "07:00",
    "oraChiusuraMattina": "13:00",
    "oraAperturaPomeriggio": "15:00",
    "oraChiusuraPomeriggio": "19:30",
    "flagOrarioContinuato": false,
    "flagH24": false,
    "flagChiusura": false,
    "flagNonComunicato": false,
    "flagServito": false,
    "flagSelf": true
  }]
}
```

The response ID must match the requested station and `name` must be nonempty. Missing or null `fuels`, `services` and `orariapertura` are normalized to empty lists. Each fuel must include `name`, `price`, `fuelId`, `isSelf` and `serviceAreaId`; timestamps are optional. Opening hours can also use `oraAperturaOrarioContinuato` and `oraChiusuraOrarioContinuato` for continuous hours.

## Local discovery

The integration intentionally does not use `POST /ospzApi/search/zone`: it has no published machine-API contract or SLA. Discovery runs locally over the registry CSV, published under the IODL 2.0 license.
