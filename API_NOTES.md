# API Notes: carburanti.mise.gov.it

This document summarizes the structure of the API endpoints for retrieving fuel price data.

## 1. Endpoint: Search by Zone

This endpoint finds fuel stations within a given radius from a central point if only one point is provided. You can also input a minimum of 3 points to a maximum of any number of points you want and the API will search within the zone delimited by these points, ignoring the radius parameter (default: 5).

- **URL:** `https://carburanti.mise.gov.it/ospzApi/search/zone`
- **Method:** `POST`
- **Request Body:** A JSON object specifying one or more points and a radius in kilometers.

  ```json
  {
    "points": [
      {
        "lat": 41.89058177533447,
        "lng": 12.492394261473839
      }, ...
    ],
    "radius": 5
  }
  ```

- **Response Body:** A JSON object containing a list of results.

  - `success`: `true` on success.
  - `center`: `lat` and `lng` that are the exact same as request values.
  - `results`: An array of station objects.

- **Example Response:**

  ```json
  {
    "success": true,
    "center": {
      "lat": 41.89058177533447,
      "lng": 12.492394261473839
    },
    "results": [
      {
        "id": 37021,
        "name": "agip ostiense",
        "fuels": [
          {
            "id": 104026931,
            "price": 1.784,
            "name": "Benzina",
            "fuelId": 1,
            "isSelf": false
          },
          {
            "id": 104090144,
            "price": 1.784,
            "name": "Benzina",
            "fuelId": 1,
            "isSelf": true
          },
          {
            "id": 104054582,
            "price": 1.764,
            "name": "Gasolio",
            "fuelId": 2,
            "isSelf": false
          },
          {
            "id": 104090143,
            "price": 1.764,
            "name": "Gasolio",
            "fuelId": 2,
            "isSelf": true
          },
          {
            "id": 104054581,
            "price": 1.864,
            "name": "Blue Diesel",
            "fuelId": 20,
            "isSelf": false
          },
          {
            "id": 104090142,
            "price": 1.864,
            "name": "Blue Diesel",
            "fuelId": 20,
            "isSelf": true
          }
        ],
        "location": {
          "lat": 41.86705120601779,
          "lng": 12.489146130162112
        },
        "insertDate": "2025-11-16T19:06:03+01:00",
        "address": null,
        "brand": "AgipEni",
        "distance": "2.646478387272458"
      }, ...
    ]
  }
  ```

## 2. Endpoint: Get Station Details

This endpoint retrieves detailed information for a specific station by its ID (5 numbers).

- **URL:** `https://carburanti.mise.gov.it/ospzApi/registry/servicearea/{id}`
- **Method:** `GET`
- **Response Body:** A JSON object with comprehensive details about the station.

- **Example Response:**

  ```json
  {
    "id": 37021,
    "name": "eni ostiense",
    "nomeImpianto": "agip ostiense",
    "address": "VIA CIRCONVALLAZIONE OSTIENSE 230 - 00154 ROMA (RM)",
    "brand": "AgipEni",
    "fuels": [
      {
        "id": 104026931,
        "price": 1.784,
        "name": "Benzina",
        "fuelId": 1,
        "isSelf": false,
        "serviceAreaId": 37021,
        "insertDate": "2025-11-15T05:56:26Z",
        "validityDate": "2025-11-15T05:56:26Z"
      },
      {
        "id": 104090144,
        "price": 1.784,
        "name": "Benzina",
        "fuelId": 1,
        "isSelf": true,
        "serviceAreaId": 37021,
        "insertDate": "2025-11-16T18:06:03Z",
        "validityDate": "2025-11-16T18:06:03Z"
      },
      {
        "id": 104054582,
        "price": 1.764,
        "name": "Gasolio",
        "fuelId": 2,
        "isSelf": false,
        "serviceAreaId": 37021,
        "insertDate": "2025-11-15T10:18:08Z",
        "validityDate": "2025-11-15T10:18:08Z"
      },
      {
        "id": 104090143,
        "price": 1.764,
        "name": "Gasolio",
        "fuelId": 2,
        "isSelf": true,
        "serviceAreaId": 37021,
        "insertDate": "2025-11-16T18:06:03Z",
        "validityDate": "2025-11-16T18:06:03Z"
      },
      {
        "id": 104054581,
        "price": 1.864,
        "name": "Blue Diesel",
        "fuelId": 20,
        "isSelf": false,
        "serviceAreaId": 37021,
        "insertDate": "2025-11-15T10:18:08Z",
        "validityDate": "2025-11-15T10:18:08Z"
      },
      {
        "id": 104090142,
        "price": 1.864,
        "name": "Blue Diesel",
        "fuelId": 20,
        "isSelf": true,
        "serviceAreaId": 37021,
        "insertDate": "2025-11-16T18:06:03Z",
        "validityDate": "2025-11-16T18:06:03Z"
      }
    ],
    "phoneNumber": "",
    "email": "",
    "website": "",
    "company": "AUTOSERVIZI FA.LE. SOCIETA' IN NOME COLLETTIVO DI FABIO FABIANI",
    "services": [
      {
        "id": "6",
        "description": "Bancomat"
      }
    ],
    "orariapertura": [
      {
        "orariAperturaId": 110698,
        "giornoSettimanaId": 1,
        "oraAperturaMattina": "07:00",
        "oraChiusuraMattina": "13:00",
        "oraAperturaPomeriggio": "15:00",
        "oraChiusuraPomeriggio": "19:30",
        "flagOrarioContinuato": false,
        "oraAperturaOrarioContinuato": null,
        "oraChiusuraOrarioContinuato": null,
        "flagH24": false,
        "flagChiusura": false,
        "flagNonComunicato": false,
        "flagServito": false,
        "flagSelf": true
      },
      {
        "orariAperturaId": 110699,
        "giornoSettimanaId": 2,
        "oraAperturaMattina": "07:00",
        "oraChiusuraMattina": "13:00",
        "oraAperturaPomeriggio": "15:00",
        "oraChiusuraPomeriggio": "19:30",
        "flagOrarioContinuato": false,
        "oraAperturaOrarioContinuato": null,
        "oraChiusuraOrarioContinuato": null,
        "flagH24": false,
        "flagChiusura": false,
        "flagNonComunicato": false,
        "flagServito": false,
        "flagSelf": true
      },
      {
        "orariAperturaId": 110700,
        "giornoSettimanaId": 3,
        "oraAperturaMattina": "07:00",
        "oraChiusuraMattina": "13:00",
        "oraAperturaPomeriggio": "15:00",
        "oraChiusuraPomeriggio": "19:30",
        "flagOrarioContinuato": false,
        "oraAperturaOrarioContinuato": null,
        "oraChiusuraOrarioContinuato": null,
        "flagH24": false,
        "flagChiusura": false,
        "flagNonComunicato": false,
        "flagServito": false,
        "flagSelf": true
      },
      {
        "orariAperturaId": 110701,
        "giornoSettimanaId": 4,
        "oraAperturaMattina": "07:00",
        "oraChiusuraMattina": "13:00",
        "oraAperturaPomeriggio": "15:00",
        "oraChiusuraPomeriggio": "19:30",
        "flagOrarioContinuato": false,
        "oraAperturaOrarioContinuato": null,
        "oraChiusuraOrarioContinuato": null,
        "flagH24": false,
        "flagChiusura": false,
        "flagNonComunicato": false,
        "flagServito": false,
        "flagSelf": true
      },
      {
        "orariAperturaId": 110702,
        "giornoSettimanaId": 5,
        "oraAperturaMattina": "07:00",
        "oraChiusuraMattina": "13:00",
        "oraAperturaPomeriggio": "15:00",
        "oraChiusuraPomeriggio": "19:30",
        "flagOrarioContinuato": false,
        "oraAperturaOrarioContinuato": null,
        "oraChiusuraOrarioContinuato": null,
        "flagH24": false,
        "flagChiusura": false,
        "flagNonComunicato": false,
        "flagServito": false,
        "flagSelf": true
      },
      {
        "orariAperturaId": 110703,
        "giornoSettimanaId": 6,
        "oraAperturaMattina": "07:00",
        "oraChiusuraMattina": "13:00",
        "oraAperturaPomeriggio": "15:00",
        "oraChiusuraPomeriggio": "19:30",
        "flagOrarioContinuato": false,
        "oraAperturaOrarioContinuato": null,
        "oraChiusuraOrarioContinuato": null,
        "flagH24": false,
        "flagChiusura": false,
        "flagNonComunicato": false,
        "flagServito": false,
        "flagSelf": true
      },
      {
        "orariAperturaId": 110704,
        "giornoSettimanaId": 7,
        "oraAperturaMattina": null,
        "oraChiusuraMattina": null,
        "oraAperturaPomeriggio": null,
        "oraChiusuraPomeriggio": null,
        "flagOrarioContinuato": false,
        "oraAperturaOrarioContinuato": null,
        "oraChiusuraOrarioContinuato": null,
        "flagH24": false,
        "flagChiusura": true,
        "flagNonComunicato": false,
        "flagServito": false,
        "flagSelf": true
      },
      {
        "orariAperturaId": 110705,
        "giornoSettimanaId": 8,
        "oraAperturaMattina": null,
        "oraChiusuraMattina": null,
        "oraAperturaPomeriggio": null,
        "oraChiusuraPomeriggio": null,
        "flagOrarioContinuato": false,
        "oraAperturaOrarioContinuato": null,
        "oraChiusuraOrarioContinuato": null,
        "flagH24": false,
        "flagChiusura": false,
        "flagNonComunicato": false,
        "flagServito": false,
        "flagSelf": true
      }
    ]
  }
  ```

## 3. Endpoint: Get All Logos

This endpoint retrieves all available fuel station brand logos.

- **URL:** `https://carburanti.mise.gov.it/ospzApi/registry/alllogos`
- **Method:** `GET`
- **Response Body:** An array of brand objects, each containing brand information and associated logos.

- **Example Response:**

  ```json
  [
    {
      "bandieraId": 0,
      "bandiera": "Bandiera non selezionata",
      "isEliminabile": null,
      "carburantiList": null,
      "logoMarkerList": []
    },
    {
      "bandieraId": 1,
      "bandiera": "Agip Eni",
      "isEliminabile": null,
      "carburantiList": null,
      "logoMarkerList": [
        {
          "tipoFile": "logo",
          "estensione": "png",
          "content": "[base64 image of the brand of the gas station]"
        }
      ]
    }
  ]
  ```

## 4. Endpoint: Get Fuel Types

This endpoint retrieves all available fuel types with their IDs and descriptions.

- **URL:** `https://carburanti.mise.gov.it/ospzApi/registry/fuels`
- **Method:** `GET`
- **Response Body:** A JSON object containing a results array with fuel type information.

- **Example Response:**

  ```json
  {
    "results": [
      {
        "id": "1-x",
        "description": "Benzina"
      },
      {
        "id": "1-1",
        "description": "Benzina (Self)"
      },
      {
        "id": "1-0",
        "description": "Benzina (Servito)"
      },
      {
        "id": "2-x",
        "description": "Gasolio"
      },
      {
        "id": "2-1",
        "description": "Gasolio (Self)"
      },
      {
        "id": "2-0",
        "description": "Gasolio (Servito)"
      },
      {
        "id": "3-x",
        "description": "Metano"
      },
      {
        "id": "3-1",
        "description": "Metano (Self)"
      },
      {
        "id": "3-0",
        "description": "Metano (Servito)"
      },
      {
        "id": "4-x",
        "description": "GPL"
      },
      {
        "id": "4-1",
        "description": "GPL (Self)"
      },
      {
        "id": "4-0",
        "description": "GPL (Servito)"
      },
      {
        "id": "323-x",
        "description": "L-GNC"
      },
      {
        "id": "323-1",
        "description": "L-GNC (Self)"
      },
      {
        "id": "323-0",
        "description": "L-GNC (Servito)"
      },
      {
        "id": "324-x",
        "description": "GNL"
      },
      {
        "id": "324-1",
        "description": "GNL (Self)"
      },
      {
        "id": "324-0",
        "description": "GNL (Servito)"
      }
    ]
  }
  ```

## 5. Key Observations

- **Self vs. Served:** The `isSelf` boolean flag is critical. It differentiates between self-service (`true`) and served (`false`) prices, which are often different.
- **Workflow:** A typical workflow would be to use the `search/zone` endpoint to discover nearby stations, then use the `registry/servicearea/{id}` endpoint to get more detailed information (like the full address or services) for a specific station.
