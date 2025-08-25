# Integrazione Home Assistant - Osservaprezzi Carburanti

Questa integrazione per Home Assistant monitora i prezzi dei carburanti dalle stazioni di servizio italiane.

## Caratteristiche

- ✅ Monitoraggio prezzi in tempo reale
- ✅ Supporto per tutti i tipi di carburante (Benzina, Gasolio, GPL, Metano, E85, H2)
- ✅ Distinzione tra servizio self-service e servito
- ✅ Aggiornamento automatico ogni ora
- ✅ Informazioni complete sulla stazione (indirizzo, brand, orari, servizi)
- ✅ Interfaccia utente nativa di Home Assistant
- ✅ Configurazione tramite UI
- ✅ **Validazione automatica dell'ID stazione**

## Installazione

### Metodo 1: Installazione Manuale

1. Copia la cartella `custom_components/carburanti_mise` nella directory `config/custom_components/` del tuo Home Assistant
2. Riavvia Home Assistant
3. Vai in **Configurazione > Integrazioni**
4. Cerca "Osservaprezzi Carburanti" e clicca su **Aggiungi integrazione**
5. Inserisci l'ID della stazione di servizio che vuoi monitorare

### Metodo 2: HACS (Home Assistant Community Store)

_Prossimamente disponibile su HACS_

## Configurazione

### Trovare l'ID della Stazione

Per trovare l'ID di una stazione di servizio:

1. Vai su [carburanti.mise.gov.it](https://carburanti.mise.gov.it)
2. Cerca la stazione di tuo interesse
3. Apri la pagina di dettaglio della stazione
4. L'ID è visibile nell'URL: `https://carburanti.mise.gov.it/ospzSearch/dettaglio/{ID}`

### Configurazione tramite UI

1. **Configurazione > Integrazioni**
2. Cerca "Osservaprezzi Carburanti"
3. Inserisci l'**ID Stazione**: L'ID numerico della stazione
4. Il nome della stazione verrà recuperato automaticamente

### Configurazione YAML (Alternativa)

```yaml
# configuration.yaml
carburanti_mise:
  - station_id: "58706"
```

## Sensori Creati

L'integrazione crea automaticamente un sensore per ogni tipo di carburante disponibile:

- **Benzina Self**: Prezzo benzina self-service
- **Benzina Servito**: Prezzo benzina servito
- **Gasolio Self**: Prezzo gasolio self-service
- **Gasolio Servito**: Prezzo gasolio servito
- **GPL**: Prezzo GPL
- **Metano**: Prezzo metano
- **E85**: Prezzo E85
- **H2**: Prezzo idrogeno

### Attributi dei Sensori

Ogni sensore include i seguenti attributi:

- `station_name`: Nome della stazione
- `station_address`: Indirizzo completo
- `station_brand`: Marchio della stazione
- `fuel_type`: Tipo di carburante
- `is_self_service`: Se è self-service (true/false)
- `last_update`: Data/ora ultimo aggiornamento
- `validity_date`: Data di validità del prezzo
- `company`: Nome della società
- `phone`: Numero di telefono
- `email`: Email di contatto
- `website`: Sito web

## Esempio di Utilizzo

### Dashboard Lovelace

```yaml
type: entities
title: Prezzi Carburanti
entities:
  - entity: sensor.keropetrol_calcinato_benzina_self
    name: Benzina Self
  - entity: sensor.keropetrol_calcinato_benzina_servito
    name: Benzina Servito
  - entity: sensor.keropetrol_calcinato_gasolio_self
    name: Gasolio Self
  - entity: sensor.keropetrol_calcinato_gasolio_servito
    name: Gasolio Servito
  - entity: sensor.keropetrol_calcinato_gpl_servito
    name: GPL
```

### Automazioni

```yaml
# Notifica quando il prezzo della benzina scende sotto 1.60€/L
automation:
  - alias: "Notifica Prezzo Benzina Basso"
    trigger:
      platform: numeric_state
      entity_id: sensor.keropetrol_calcinato_benzina_self
      below: 1.60
    action:
      - service: notify.mobile_app
        data:
          title: "Prezzo Benzina Conveniente!"
          message: "Il prezzo della benzina è sceso a {{ states('sensor.keropetrol_calcinato_benzina_self') }}€/L"
```

## Risoluzione Problemi

### L'integrazione non trova la stazione

- Verifica che l'ID della stazione sia corretto
- Controlla che la stazione sia attiva sul sito
- Verifica la connessione internet

### I prezzi non si aggiornano

- Controlla i log di Home Assistant per errori
- Verifica che il servizio sia accessibile
- Prova a riavviare l'integrazione

### Errore di configurazione

- Assicurati che tutti i file siano nella directory corretta
- Verifica che Home Assistant sia stato riavviato dopo l'installazione
- Controlla che non ci siano errori di sintassi nei file

## Log

Per abilitare i log dettagliati, aggiungi al tuo `configuration.yaml`:

```yaml
logger:
  default: info
  logs:
    custom_components.carburanti_mise: debug
```

## Sviluppo

### Struttura del Progetto

```
custom_components/carburanti_mise/
├── __init__.py          # Inizializzazione integrazione
├── manifest.json        # Metadati integrazione
├── const.py            # Costanti e configurazioni
├── config_flow.py      # Configurazione UI
├── coordinator.py      # Gestione dati
├── sensor.py           # Entità sensori
├── strings.json        # Stringhe UI
└── translations/       # Traduzioni
    ├── en.json
    └── it.json
```

### Contribuire

1. Fork del repository
2. Crea un branch per la tua feature
3. Committa le modifiche
4. Crea una Pull Request

## Licenza

Questo progetto è rilasciato sotto licenza MIT.

## Supporto

Per supporto e domande:

- Apri una issue su GitHub
- Contatta l'autore: @casungo

## Autore

Creato da **casungo**

## Changelog

### v1.0.0

- Prima release
- Supporto base per monitoraggio prezzi carburanti
- Configurazione tramite UI
- Sensori per tutti i tipi di carburante
