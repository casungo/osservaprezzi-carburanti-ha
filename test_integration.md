# Test Integrazione Osservaprezzi Carburanti

## Passi per testare l'integrazione:

### 1. Installazione

1. **Copia la cartella `custom_components`** nella directory `config` di Home Assistant
2. **Verifica la struttura**:
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
3. **Riavvia Home Assistant**
4. **Vai su Configurazione** → **Dispositivi e Servizi**

### 2. Verifica Config Flow

1. Clicca **Aggiungi Integrazione**
2. Cerca "Osservaprezzi Carburanti"
3. Inserisci un ID stazione valido (es: 11745)
4. Conferma la configurazione

### 3. Test My Home Assistant Link

Dopo l'installazione, il link dovrebbe funzionare:

```
https://my.home-assistant.io/redirect/config_flow_start/?domain=osservaprezzi_carburanti
```

### 4. Verifica Card Automatiche

1. Controlla che sia stata creata una notifica
2. Verifica che sia stato creato il file YAML con le card
3. Copia le card nel dashboard Lovelace

## ID Stazioni di Test

- 11745 (Brescia)
- 10001 (Milano)
- 20001 (Roma)

## Troubleshooting

### Link My Home Assistant non funziona

1. Verifica che l'integrazione sia installata
2. Controlla i log di Home Assistant per errori
3. Assicurati che il config flow sia registrato correttamente

### Integrazione non appare in Home Assistant

1. **Verifica la struttura delle cartelle**:

   ```
   config/custom_components/osservaprezzi_carburanti/
   ```

2. **Controlla i file essenziali**:

   - `__init__.py` - Inizializzazione dell'integrazione
   - `manifest.json` - Metadati e configurazione
   - `config_flow.py` - Configurazione guidata
   - `const.py` - Costanti e configurazioni

3. **Verifica i log di Home Assistant**:

   - Cerca errori di importazione
   - Controlla messaggi di warning per integrazioni custom

4. **Riavvia Home Assistant** dopo ogni modifica
