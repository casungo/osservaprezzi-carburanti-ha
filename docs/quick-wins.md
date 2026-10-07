# Interventi dell'audit

I numeri corrispondono ai 50 interventi autorizzati. Le prime sei correzioni UX, già presenti
nel checkout, sono mantenute. Le modifiche non richiedono migrazioni delle configurazioni e
mantengono gli ID delle entità.

| N. | Intervento | Area |
| --- | --- | --- |
| 1 | Il servizio controlla l'esito del coordinator dopo ogni refresh | Servizi |
| 2 | Il fallback alla cache è esplicito e l'aggiornamento manuale segnala il mancato recupero | Coordinator, pulsante |
| 3 | Il ripristino verifica struttura e ID della stazione configurata | Coordinator |
| 4 | `allow_stale=False` rifiuta anche una cache inizializzata ma scaduta | Registro |
| 5 | I prezzi devono essere numerici, finiti e non negativi | API |
| 6 | `isSelf` deve essere booleano | API |
| 7 | Una data invalida restituisce `None` | Coordinator |
| 8 | Le date senza fuso usano il fuso di Home Assistant | Coordinator |
| 9 | `Retry-After` supporta secondi e date HTTP | Coordinator |
| 10 | Gli ID selezionati vengono deduplicati preservando l'ordine | Config flow |
| 11 | Registro aggiornato e prossimo refresh mostrano date locali leggibili | Config flow |
| 12 | Le coordinate usano campi numerici con limiti geografici | Config flow |
| 13 | La posizione Home deve rispettare gli stessi limiti geografici | Config flow |
| 14 | Gli errori dell'ID sono associati al campo | Config flow |
| 15 | La selezione multipla identifica la stazione che non si riesce a verificare | Config flow |
| 16 | Il rate limit ha un messaggio dedicato | Config flow |
| 17 | Gli errori dei servizi sono traducibili | Traduzioni |
| 18 | Nomi e descrizioni dei campi dei servizi sono tradotti | Traduzioni |
| 19 | Le entità statiche usano chiavi di traduzione | Entità |
| 20 | Le sigle dei carburanti conservano le maiuscole | Entità |
| 21 | I prezzi suggeriscono tre decimali di visualizzazione | Sensori |
| 22 | La ricerca segnala quando raggiunge il limite dei risultati | Config flow |
| 23 | I tipi di impianto presenti nel registro sono suggeriti, con testo libero | Config flow |
| 24 | Il dispositivo collega la pagina pubblica della stazione | Entità |
| 25 | Il confronto include variazione ed età dei prezzi | Servizi |
| 26 | Rimossi gli `except` API che si limitavano a rilanciare | API |
| 27 | Campi obbligatori del carburante definiti una volta | API |
| 28 | Validazione delle coordinate condivisa | Helper |
| 29 | Scelta del nome della stazione condivisa | Helper |
| 30 | Etichetta del carburante e modalità preparate nel costruttore | Sensori |
| 31 | Contratti dei payload espliciti con `TypedDict` | Modelli |
| 32 | Gestori dei servizi separati dal ciclo di vita dell'integrazione | Servizi |
| 33 | Registrazione e rimozione usano lo stesso elenco di servizi | Servizi |
| 34 | Setup usa la factory condivisa del registro | Setup |
| 35 | Eliminato il vecchio helper inutilizzato degli orari | Sensori |
| 36 | Corretta l'indentazione del caricamento cache | Registro |
| 37 | Il refresh periodico restituisce direttamente l'esito | Registro |
| 38 | Ritardi di retry definiti in una tupla | Coordinator |
| 39 | Stato dei flussi inizializzato esplicitamente | Config flow |
| 40 | La riconfigurazione verifica i duplicati prima di contattare l'API | Config flow |
| 41 | Gli ID disponibili vengono raccolti una volta per validazione | Config flow |
| 42 | Filtri normalizzati una volta per ricerca | Discovery |
| 43 | Le entità già note non vengono ricostruite | Piattaforme |
| 44 | Snapshot immutabile riusato per versione del registro | Registro |
| 45 | Il prossimo cambio di orario viene calcolato agli aggiornamenti e ai tick | Sensori |
| 46 | Il fallback non riscrive la stessa cache della stazione | Coordinator |
| 47 | JSON della cache compatto, mantenendo la scrittura atomica | Registro |
| 48 | CI esegue la suite una volta sotto coverage | CI |
| 49 | Cache pip nei job Python | CI |
| 50 | Comandi locali `make` per test e verifiche | Sviluppo |

## Misure del registro

Benchmark sintetico con 20.000 stazioni e nove ripetizioni, mediana sullo stesso host:

| Operazione | Risultato |
| --- | --- |
| Copiare lo snapshot | 12,518 ms |
| Riusare lo snapshot | 0,000113 ms |
| JSON formattato | 5.975.648 byte, 44,761 ms |
| JSON compatto | 4.155.632 byte, 41,616 ms |

Il JSON compatto occupa il 30,5% in meno. Il benchmark verifica che i due formati decodifichino
agli stessi dati. Lo snapshot viene ricreato dopo la sostituzione del registro e dopo la pulizia
della cache; una copia delle informazioni della singola stazione protegge i dati interni.
Le misure sono riproducibili con il comando documentato in [testing.md](testing.md).

Per il prossimo cambio di orario, nove ripetizioni di 1.000 coppie di letture sullo stesso
host hanno misurato 106,759 ms ricalcolando entrambe le proprietà e 3,861 ms leggendo il risultato
preparato all'aggiornamento. Il calcolo agli aggiornamenti resta necessario. Il timer aggiorna
il risultato ogni minuto e i test reali verificano sia il tick sia la modifica degli orari.

```bash
PYTHONPATH=. .venv-ha/bin/python scripts/benchmark_schedule.py
```

## Verifiche

- Suite leggera: 481 test passati, 7 saltati; coverage del codice dell'integrazione al 100%.
- Home Assistant reale: 15 test passati, compresi selezione con risultati limitati e tick nel fuso Europe/Rome.
- Ruff, mypy, hassfest e controllo del diff: passati.
- Docker con API ufficiale: passati i profili fresh, upgrade, lived, outage e recovery.
- HACS locale non eseguito: il CLI non è installato e il target del progetto valida il ref GitHub remoto.

Le modifiche rimangono locali, senza commit, push, release o deploy.
