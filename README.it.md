# Osservaprezzi Carburanti per Home Assistant

[🇬🇧 Read this in English / Leggi in Inglese](./README.md)

Integrazione per Home Assistant che recupera i prezzi dei carburanti dal servizio Osservaprezzi del Ministero delle Imprese e del Made in Italy (MISE).

## ✨ Caratteristiche

- 📊 **Sensori Automatici**: Crea automaticamente un sensore per ogni tipo di carburante disponibile nella stazione selezionata.
- ⏰ **Aggiornamento Programmato**: I dati vengono aggiornati ogni giorno a un orario configurabile dall'utente.
- 🏷️ **Informazioni Complete**: I sensori includono attributi dettagliati come nome della stazione, marchio, indirizzo e data dell'ultimo aggiornamento.
- 🔧 **Configurazione Semplice**: Installazione guidata tramite l'interfaccia utente di Home Assistant.

## 🚀 Installazione

### Tramite HACS (Raccomandato)

1.  **Installa HACS** (se non l'hai già fatto): [Guida HACS](https://hacs.xyz/docs/installation/installation/)
2.  **Aggiungi questo repository** in HACS:
    - Vai su HACS → Integrazioni.
    - Clicca sui 3 punti in alto a destra.
    - Seleziona "Repository personalizzati".
    - Aggiungi: `casungo/osservaprezzi-carburanti-ha`
    - Categoria: Integrazione
3.  **Installa l'integrazione**:
    - Cerca "Osservaprezzi Carburanti" in HACS.
    - Clicca "Download".
    - Riavvia Home Assistant.
    - L'integrazione verrà installata automaticamente in `config/custom_components/osservaprezzi_carburanti/`.

### Installazione Manuale

1.  **Scarica il repository**:
    ```bash
    git clone https://github.com/casungo/osservaprezzi-carburanti-ha.git
    ```
2.  **Copia l'integrazione**:
    Copia la cartella `custom_components/osservaprezzi_carburanti` nella tua directory `config` di Home Assistant.
3.  **Verifica la struttura**:
    ```
    config/
    └── custom_components/
        └── osservaprezzi_carburanti/
            ├── __init__.py
            ├── manifest.json
            ├── sensor.py
            └── ...
    ```
4.  **Riavvia Home Assistant**.

## ⚙️ Configurazione

Per configurare l'integrazione, vai su: `Impostazioni` -> `Dispositivi e Servizi` -> `AGGIUNGI INTEGRAZIONE`, cerca `Osservaprezzi Carburanti` e segui le istruzioni.

Puoi anche utilizzare il seguente link My Home Assistant (richiede che l'integrazione sia già installata):

[![Apri la tua istanza di Home Assistant e avvia la configurazione di una nuova integrazione.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=osservaprezzi_carburanti)

### Come trovare l'ID della Stazione

Durante la configurazione, ti verrà richiesto di inserire l'**ID Stazione** dell'impianto che desideri monitorare. Per trovare l'ID della Stazione:

1. Vai a https://carburanti.mise.gov.it/ospzSearch/zona
2. Cerca la tua stazione preferita
3. Clicca sulla stazione
4. Nell'URL (es: https://carburanti.mise.gov.it/ospzSearch/dettaglio/1111) copia l'ID (1111)

## 📋 Tipi di Carburante Supportati

L'integrazione creerà sensori per ognuno dei seguenti carburanti, se disponibili presso la stazione:

- **Benzina**: Benzina Self/Servito
- **Diesel**: Gasolio, Blue Diesel, etc.
- **GPL**: GPL Servito
- **Metano**: Metano Servito
- **Biocarburanti**: E85
- **Idrogeno**: H2

## 🚨 Risoluzione Problemi

### L'integrazione non viene trovata

1.  Verifica che la struttura della cartella sia corretta: `config/custom_components/osservaprezzi_carburanti/`.
2.  Controlla che tutti i file essenziali siano presenti (`__init__.py`, `manifest.json`, etc.).
3.  Controlla i log di Home Assistant per eventuali errori di importazione relativi all'integrazione.
4.  Assicurati che il dominio nel file `manifest.json` sia `osservaprezzi_carburanti`.

### Nessun dato visualizzato

1.  Assicurati che l'integrazione sia stata configurata correttamente dall'interfaccia utente.
2.  Controlla che i sensori abbiano uno stato valido negli Strumenti per Sviluppatori.
3.  Verifica che l'ID della stazione sia corretto e che la stazione stia comunicando i prezzi.

## 📞 Supporto

Per problemi o suggerimenti:

- Apri una issue su GitHub.
- Contatta l'autore.

## 📄 Licenza

Questo progetto è rilasciato sotto licenza MIT.

## 🙏 Ringraziamenti

- Il servizio Osservaprezzi Carburanti per la fornitura dei dati.
- Il team di Home Assistant per la piattaforma.
- Il team di HACS per aver semplificato la distribuzione.
