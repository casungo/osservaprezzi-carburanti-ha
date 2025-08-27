# Osservaprezzi Carburanti

Integrazione per Home Assistant che recupera i prezzi dei carburanti dal servizio Osservaprezzi del MISE.

## Caratteristiche

- 📊 **Sensori Automatici**: Crea automaticamente sensori per ogni tipo di carburante disponibile
- 🎨 **Card Moderne**: Include card personalizzate con design moderno e funzionale
- 📱 **Responsive**: Interfaccia ottimizzata per desktop e mobile
- 🏆 **Analisi Prezzi**: Evidenzia automaticamente i prezzi migliori
- ⏰ **Aggiornamento Automatico**: Dati aggiornati ogni ora
- 🏷️ **Informazioni Complete**: Include dettagli su stazione, servizio e validità

## Installazione

### Tramite HACS (Raccomandato)

1. Assicurati di avere [HACS](https://hacs.xyz/) installato
2. Aggiungi questo repository come integrazione personalizzata
3. Cerca "Osservaprezzi Carburanti" in HACS
4. Clicca "Download"
5. Riavvia Home Assistant

### Configurazione

1. Vai su **Configurazione** → **Dispositivi e Servizi**
2. Clicca **Aggiungi Integrazione**
3. Cerca "Osservaprezzi Carburanti"
4. Inserisci l'ID della stazione di servizio
5. Conferma la configurazione

## Utilizzo

### Card Automatiche

Dopo l'installazione, le card sono disponibili automaticamente:

- **Card Base**: `custom:carburanti-card`
- **Card Avanzata**: `custom:carburanti-advanced-card`

### Configurazione Semplice

Le card si configurano automaticamente e trovano tutti i sensori carburante disponibili:

```yaml
# Configurazione minima
type: custom:carburanti-card
title: "Prezzi Carburanti"
```

### Configurazione Avanzata

```yaml
type: custom:carburanti-card
title: "Prezzi Carburanti Brescia"
subtitle: "Stazione 11745"
entities:
  - sensor.11745_brescia_benzina_self
  - sensor.11745_brescia_benzina_servito
```

## Funzionalità Card

### Card Base

- Organizzazione per categoria (Benzina, Diesel, GPL, etc.)
- Evidenziazione prezzi migliori
- Badge per tipo servizio (Self/Servito)
- Informazioni ultimo aggiornamento

### Card Avanzata

- Statistiche in tempo reale (media, min, max)
- Sistema di tab (Lista, Grafico, Analisi)
- Indicatori di variazione percentuale
- Analisi prezzi per categoria

## Supporto

Per problemi o suggerimenti:

- Apri una issue su GitHub
- Contatta l'autore

## Licenza

MIT License
