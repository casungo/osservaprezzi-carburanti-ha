# Osservaprezzi Carburanti per Home Assistant

[🇬🇧 Read this in English / Leggi in Inglese](./README.md)

Integrazione per Home Assistant che recupera i prezzi dei carburanti dal servizio Osservaprezzi del Ministero delle Imprese e del Made in Italy (MIMIT).

## ✨ Caratteristiche

📊 **Sensori Automatici del Carburante**: Crea automaticamente un sensore per ogni tipo di carburante disponibile nella stazione selezionata.

⏰ **Aggiornamento Programmato**: I dati vengono aggiornati ogni giorno a un orario configurabile dall'utente utilizzando espressioni cron (default è ogni giorno alle 08:30).

🏷️ **Informazioni Complete sulla Stazione**: I sensori includono attributi dettagliati come nome della stazione, marchio, indirizzo e data dell'ultimo aggiornamento.

📍 **Sensore di Posizione**: Crea un sensore diagnostico di posizione per la stazione con coordinate GPS.

🕐 **Sensori degli Orari di Apertura**: Fornisce sensori per stato aperto/chiuso e prossimo orario di apertura/chiusura quando disponibili.

🛠️ **Sensori Binari dei Servizi**: Crea automaticamente sensori binari per i servizi quando disponibili

📞 **Sensori Informazioni di Contatto**: Crea sensori per telefono, email e sito web quando disponibili.

## 🚀 Installazione e configurazione

### Installazione tramite HACS

1.  **Installa HACS** (se non l'hai già fatto): [Guida HACS](https://hacs.xyz/docs/installation/installation/)
2.  **Installa l'integrazione**:
    - Cerca "Osservaprezzi Carburanti" in HACS.
    - Clicca "Download".
    - Riavvia Home Assistant.

### Configurazione

Per configurare l'integrazione, vai su: "Impostazioni" -> "Dispositivi e Servizi" -> "+ Aggiungi integrazione", cerca "Osservaprezzi Carburanti" e segui le istruzioni.

Puoi anche utilizzare il seguente link My Home Assistant (richiede che l'integrazione sia già installata):

[![Apri la tua istanza di Home Assistant e avvia la configurazione di una nuova integrazione.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=osservaprezzi_carburanti)

## 📊 Sensori Creati

Quando configuri una stazione, l'integrazione crea automaticamente i seguenti sensori:

### Sensori dei Prezzi dei Carburanti

- Un sensore per ogni tipo di carburante disponibile presso la stazione (sia self-service che servito)
- Mostra il prezzo attuale in €/L
- Include attributi per nome del carburante, tipo di servizio, ora dell'ultimo aggiornamento e data di validità

### Sensori Informazioni sulla Stazione

- **Nome Stazione**: Il nome dell'impianto di carburante
- **ID Stazione**: L'identificatore Osservaprezzi
- **Indirizzo**: Indirizzo stradale completo
- **Marchio**: Marchio della stazione di carburante (es. Eni, Shell, TotalEnergies)
- **Società**: Nome della società operativa
- **Telefono**: Numero di telefono di contatto (se disponibile)
- **Email**: Email di contatto (se disponibile)
- **Sito Web**: Sito web ufficiale (se disponibile)
- **Posizione**: Marcatore sulla mappa con coordinate GPS

### Sensori Orari di Funzionamento (quando i dati sono disponibili)

- **Stato Aperto/Chiuso**: Sensore binario che indica se la stazione è attualmente aperta
- **Prossimo Cambio**: Mostra quando la stazione aprirà o chiuderà successivamente con l'orario

### Sensori Binari dei Servizi (quando i dati sono disponibili)

Vengono creati sensori binari per ogni servizio disponibile presso la stazione:

- 🍽️ **Food&Beverage**: Bar, ristorante o punto ristoro
- 🔧 **Officina**: Servizi di riparazione e manutenzione auto
- 🚛 **Area Sosta Camper/Tir**: Area di parcheggio designata per camper e autocarri
- 💧 **Scarico Camper**: Punto di scarico acque nere/grigie
- 🧒 **Area Bambini**: Area giochi per bambini
- 💳 **ATM/Bancomat**: Disponibilità sportello automatico
- ♿ **Accesso Disabili**: Servizi di accessibilità
- 📶 **Wi-Fi**: Disponibilità connessione internet
- 🛞 **Servizio Gomme**: Servizi di gommista e pneumatici
- 🚗 **Autolavaggio**: Servizi di lavaggio veicoli
- 🔌 **Ricarica Elettrica**: Stazioni di ricarica veicoli elettrici

### Configurazione dell'Espressione Cron

L'integrazione utilizza un'espressione cron per programmare gli aggiornamenti automatici dei dati. Questo può essere configurato dopo l'installazione iniziale attraverso le opzioni dell'integrazione.

**Valore predefinito**: `30 8 * * *` (ogni giorno alle 8:30)

Puoi modificare questa espressione per personalizzare quando desideri che vengano aggiornati i dati. Puoi usare [Crontab Guru](https://crontab.guru) per aiutarti a costruire e validare le tue espressioni cron. Questo strumento fornisce un'interfaccia utile per capire quando verranno eseguiti i tuoi aggiornamenti programmati.

Esempi comuni:

- `0 8 * * *` - Ogni giorno alle 8:00
- `30 7,19 * * *` - Ogni giorno alle 7:30 e 19:30
- `0 */6 * * *` - Ogni 6 ore
- `0 8 * * 1-5` - Giorni feriali alle 8:00

### Come trovare l'ID della Stazione

Durante la configurazione, ti verrà richiesto di inserire l'**ID Stazione** dell'impianto che desideri monitorare. Per trovare l'ID della Stazione:

1. Vai a https://carburanti.mise.gov.it/ospzSearch/zona
2. Cerca la tua stazione preferita
3. Clicca sulla stazione
4. Nell'URL (es: https://carburanti.mise.gov.it/ospzSearch/dettaglio/1111) copia l'ID (1111)

## 📋 Tipi di Carburante Supportati

L'integrazione creerà sensori per ogni possibile carburante.

## 🧩 Esempi Dashboard

### Battery State Card

Puoi mostrare i prezzi dei carburanti con la custom card [Battery State Card](https://github.com/maxwroc/battery-state-card). L'esempio seguente mostra i prezzi del gasolio di tutte le entità `osservaprezzi_carburanti`, ordinati dal più economico al più costoso:

```yaml
grid_options:
  columns: full
  rows: auto
type: custom:battery-state-card
title: Diesel
secondary_info: "{attributes.station_brand} - {attributes.station_address}"
icon: mdi:fuel
filter:
  include:
    - and:
        - or:
            - name: attributes.fuel_type_name
              value: Gasolio
            - name: attributes.fuel_type_name
              value: Blue*
        - name: entity.platform
          value: osservaprezzi_carburanti
  exclude:
    - name: attributes.validity_date
      operator: ">"
      value: 24h
sort:
  - by: state
    desc: false
collapse: 6
colors:
  steps:
    - value: 1.6
      color: "#00ff00"
    - value: 2
      color: "#ffff00"
    - value: 2.4
      color: "#ff0000"
  gradient: true
tap_action:
  action: navigate
  navigation_path: /config/devices/device/{entity.device_id}
```

Per mostrare tutti i carburanti e raggrupparli per nome carburante, sostituisci la sezione `filter` e aggiungi `group`:

```yaml
filter:
  include:
    - and:
        - or:
            - name: attributes.fuel_type_name
              value: "*"
        - name: entity.platform
          value: osservaprezzi_carburanti
group:
  - by: attributes.fuel_type_name
```

## 📞 Supporto

Per problemi o suggerimenti apri una issue su GitHub.

## 📄 Licenza

Questo progetto è rilasciato sotto licenza MIT.
