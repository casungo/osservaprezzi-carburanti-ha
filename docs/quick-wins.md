# Cambiamenti inclusi in v2.7.0-beta.1

Questa beta raccoglie tutti i 50 interventi concordati. Le modifiche mantengono gli ID delle
entità e non richiedono migrazioni. Insieme, fanno emergere gli errori invece di presentarli come
aggiornamenti riusciti, migliorano i flussi di configurazione e riducono il lavoro ripetuto nelle
letture delle entità.

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

## Confronto prima e dopo

Confronto tra il genitore `96dbdd7` e `cc7dd69`, eseguito sullo stesso host e negli stessi
ambienti: Python 3.11 per i test unitari, Python 3.14 per i test Home Assistant e i benchmark.

| Suite | Prima | Dopo |
| --- | --- | --- |
| Test unitari | 389 passati, 7 saltati | 481 passati, 7 saltati |
| Coverage integrazione | 100% su 1.759 statement | 100% su 2.051 statement |
| Test con Home Assistant reale | 6 passati | 15 passati |

La suite è cresciuta di 92 test senza un aumento misurabile del tempo di esecuzione. La coverage
è rimasta al 100% con 292 statement in più. La coverage misura l'esecuzione, non dimostra da sola
la correttezza di ogni comportamento.

## Benchmark ripetibili

Il benchmark della cache usa 20.000 record sintetici, 30 campioni dopo il riscaldamento e alterna
l'ordine delle codifiche. Confronta il JSON formattato, come nella versione precedente, con il JSON
compatto. Verifica anche che entrambi decodifichino negli stessi dati. Mediane e intervalli sono
stati misurati sullo stesso host:

| Codifica cache | Dimensione | Mediana per serializzazione | Intervallo dei 30 campioni |
| --- | ---: | ---: | ---: |
| JSON formattato | 5.975.648 byte | 40,779 ms | 37,629–46,485 ms |
| JSON compatto | 4.155.632 byte | 38,435 ms | 36,827–43,657 ms |

Il JSON compatto occupa il 30,5% in meno e in questa prova ha ridotto il tempo mediano del 5,7%.
Il risultato principale è il risparmio di spazio; i tempi variano con la macchina e i due
intervalli si sovrappongono.

Per il sensore del prossimo cambio orario, ogni campione esegue 1.000 coppie di letture. Sono
stati raccolti 30 campioni alternando l'ordine tra il ricalcolo completo e la lettura delle
proprietà preparate all'aggiornamento:

| Lettura | Mediana per 1.000 coppie | Intervallo dei 30 campioni |
| --- | ---: | ---: |
| Ricalcolare entrambe le proprietà | 103,258 ms | 101,440–119,606 ms |
| Leggere il valore preparato | 3,823 ms | 3,669–4,606 ms |

In questo carico sintetico le letture preparate sono 27,0 volte più veloci. Il calcolo resta
necessario quando cambiano i dati o al tick del timer, non a ogni lettura dello stato.

Per ripetere entrambe le prove:

```bash
PYTHONPATH=. .venv-ha/bin/python scripts/benchmark_registry.py --stations 20000 --repeats 30
PYTHONPATH=. .venv-ha/bin/python scripts/benchmark_schedule.py --read-pairs 1000 --repeats 30
```

Sono microbenchmark sintetici: non misurano l'avvio completo di Home Assistant né la latenza dei
server MIMIT. La regressione d'integrazione reale è un controllo separato.

## Verifiche

- 481 test unitari passati, 7 saltati; coverage dell'integrazione al 100%.
- 15 test con Home Assistant reale e 1 contratto live MIMIT passati.
- Ruff, mypy e hassfest passati.
- Docker con API ufficiale passato nei profili `fresh`, `lived`, `upgrade`, `outage` e `recovery`.
- Il profilo persistente ha ripristinato 4 config entry, 53 entity-registry entry, 23.766 stazioni
  in cache e 14 giorni sintetici di Recorder. L'upgrade da `v2.6.0-beta.1` è passato.
- CI e HACS sul commit beta fanno parte della verifica remota successiva al push.
