# Osservaprezzi Carburanti per Home Assistant

Integrazione per Home Assistant che recupera i prezzi dei carburanti dal servizio Osservaprezzi del MISE, con card moderne e funzionali.

## 🚀 Installazione Semplificata

### ⚡ Installazione Rapida

1. **Scarica il repository**:

   ```bash
   git clone https://github.com/casungo/osservaprezzi-carburanti-ha.git
   ```

2. **Copia l'integrazione**:

   ```bash
   cp -r osservaprezzi-carburanti-ha/custom_components /path/to/homeassistant/config/
   ```

3. **Riavvia Home Assistant** e configura l'integrazione

### Tramite HACS (Raccomandato)

1. **Installa HACS** (se non l'hai già): [Guida HACS](https://hacs.xyz/docs/installation/installation/)
2. **Aggiungi questo repository** in HACS:
   - Vai su HACS → Integrazioni
   - Clicca sui 3 punti in alto a destra
   - Seleziona "Repository personalizzati"
   - Aggiungi: `casungo/osservaprezzi-carburanti-ha`
   - Categoria: Integrazione
3. **Installa l'integrazione**:
   - Cerca "Osservaprezzi Carburanti" in HACS
   - Clicca "Download"
   - Riavvia Home Assistant
   - L'integrazione verrà installata automaticamente in `config/custom_components/osservaprezzi_carburanti/`

## Configuration

### Config flow

To configure this integration go to: `Configurations` -> `Integrations` -> `ADD INTEGRATIONS` button, search for `Osservaprezzi Carburanti` and configure the component.

You can also use following [My Home Assistant](http://my.home-assistant.io/) link (requires integration to be installed first):

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=osservaprezzi_carburanti)

**Note**: The My Home Assistant link works only after the integration is installed. For first-time setup, use the manual configuration method below.

### Installation Structure

The integration folder should be placed in your Home Assistant `config/custom_components/` directory. The structure should be:

```
config/
└── custom_components/
    └── osservaprezzi_carburanti/
        ├── __init__.py
        ├── manifest.json
        ├── config_flow.py
        ├── const.py
        ├── sensor.py
        ├── coordinator.py
        ├── strings.json
        └── translations/
```

### Manual Configuration

1. **Installa l'integrazione**:
   - Copia la cartella `custom_components` nella directory `config` di Home Assistant
   - Riavvia Home Assistant
2. **Configura l'integrazione**:
   - Vai su **Configurazione** → **Dispositivi e Servizi**
   - Clicca **Aggiungi Integrazione**
   - Cerca "Osservaprezzi Carburanti"
   - Inserisci l'ID della stazione di servizio
   - Conferma la configurazione

### 🎁 Card Automatiche

Dopo la configurazione, l'integrazione crea automaticamente **3 card pronte all'uso**:

1. **Card Base** - Mostra tutti i prezzi della stazione
2. **Card Avanzata** - Analisi e statistiche dei prezzi
3. **Card Sensori** - Monitoraggio ultimo aggiornamento

Le card vengono salvate in un file YAML che puoi copiare direttamente nel tuo dashboard Lovelace!

## 🎨 Card Moderne

Dopo l'installazione, hai automaticamente accesso a due card moderne:

### Card Base

```yaml
type: custom:carburanti-card
title: "Prezzi Carburanti"
```

### Card Avanzata

```yaml
type: custom:carburanti-advanced-card
title: "Analisi Prezzi Carburanti"
```

**Le card si configurano automaticamente** e trovano tutti i sensori carburante disponibili!

## ✨ Caratteristiche

### Integrazione

- 📊 **Sensori Automatici**: Crea automaticamente sensori per ogni tipo di carburante
- ⏰ **Aggiornamento Automatico**: Dati aggiornati ogni ora
- 🏷️ **Informazioni Complete**: Include dettagli su stazione, servizio e validità
- 🔧 **Configurazione Semplice**: Setup guidato tramite UI

### Card Moderne

- 🎨 **Design Elegante**: Interfaccia moderna con gradiente e animazioni
- 📊 **Organizzazione Intelligente**: Carburanti raggruppati per categoria
- 🏆 **Evidenziazione Prezzi**: Mostra automaticamente i prezzi migliori
- 📱 **Responsive**: Ottimizzata per desktop e mobile
- 🎯 **Icone Specifiche**: Icone appropriate per ogni tipo di carburante

### Card Avanzata

- 📈 **Statistiche in Tempo Reale**: Prezzo medio, minimo e massimo
- 📊 **Sistema di Tab**: Lista, Grafico e Analisi
- 📉 **Indicatori di Variazione**: Confronto con medie di categoria
- 🔍 **Analisi Dettagliate**: Informazioni approfondite sui prezzi

## 🛠️ Utilizzo

### Configurazione Minima

Le card funzionano senza configurazione aggiuntiva:

```yaml
# Card base - configurazione minima
type: custom:carburanti-card
title: "Prezzi Carburanti"

# Card avanzata - configurazione minima
type: custom:carburanti-advanced-card
title: "Analisi Prezzi"
```

### Configurazione Personalizzata

```yaml
type: custom:carburanti-card
title: "Prezzi Carburanti Brescia"
subtitle: "Stazione 11745 - Aggiornamento in tempo reale"
```

### Configurazione con Entità Specifiche

```yaml
type: custom:carburanti-card
title: "Solo Benzina e Diesel"
entities:
  - sensor.11745_brescia_benzina_self
  - sensor.11745_brescia_benzina_servito
  - sensor.11745_brescia_gasolio_self
  - sensor.11745_brescia_gasolio_servito
```

## 📋 Tipi di Carburante Supportati

- **Benzina**: Benzina Self/Servito
- **Diesel**: Gasolio, Blue Diesel, HVOlution
- **GPL**: GPL Servito
- **Metano**: Metano Servito
- **Biocarburanti**: E85, HVOlution
- **Idrogeno**: H2

## 🔧 Opzioni di Configurazione

| Parametro  | Tipo   | Obbligatorio | Descrizione                                                                                      |
| ---------- | ------ | ------------ | ------------------------------------------------------------------------------------------------ |
| `title`    | string | No           | Titolo della card                                                                                |
| `subtitle` | string | No           | Sottotitolo della card                                                                           |
| `entities` | list   | No           | Lista specifica di entità (se non specificato, trova automaticamente tutti i sensori carburante) |

## 🎯 Funzionalità Avanzate

### Raggruppamento Automatico

I carburanti vengono automaticamente raggruppati per categoria e ordinati per prezzo.

### Evidenziazione Prezzi Migliori

- Bordo verde per i prezzi più bassi
- Badge "Migliore" in alto a destra
- Sfondo leggermente colorato

### Informazioni Dettagliate

Ogni carburante mostra:

- Nome del carburante
- Tipo di servizio (Self/Servito)
- Nome della stazione
- Prezzo in €/L
- Icona specifica
- Ultimo aggiornamento

## 🚨 Risoluzione Problemi

### Card non si carica

1. Verifica che l'integrazione sia installata correttamente
2. Controlla che ci siano sensori carburante disponibili
3. Riavvia Home Assistant

### Integrazione non appare

1. Verifica che la struttura delle cartelle sia corretta:
   ```
   config/custom_components/osservaprezzi_carburanti/
   ```
2. Controlla che tutti i file siano presenti (**init**.py, manifest.json, etc.)
3. Verifica i log di Home Assistant per errori di importazione
4. Assicurati che il dominio nel manifest.json sia `osservaprezzi_carburanti`

### Nessun dato visualizzato

1. Verifica che l'integrazione sia configurata
2. Controlla che i sensori abbiano uno stato valido
3. Assicurati che l'ID della stazione sia corretto

### Errori di stile

1. Verifica che il tema di Home Assistant sia compatibile
2. Controlla la console del browser per errori JavaScript

## 📞 Supporto

Per problemi o suggerimenti:

- Apri una issue su GitHub
- Contatta l'autore

## 📄 Licenza

Questo progetto è rilasciato sotto licenza MIT.

## 🙏 Ringraziamenti

- Servizio Osservaprezzi del MISE per i dati
- Home Assistant per la piattaforma
- HACS per la distribuzione semplificata
