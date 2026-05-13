# RIoT Apps

[![RIoT Platform: organization](https://img.shields.io/badge/RIoT_Platform-organization-blue?logo=github)](https://github.com/RIoT-Platform)

RIoT Apps je doplňkový repozitář s upravenými externími aplikacemi použitými pro ověření integrace platformy RIoT. Nejde o plnohodnotné distribuce původních aplikací, ale o funkcionalitami omezené varianty určené hlavně pro testování toho, že RIoT dokáže poskytovat data a KPI výsledky aplikacím s vlastním frontendem, backendem a doménovým modelem.

Repozitář vznikl v rámci bakalářské práce **Vojtěcha Hubáčka**. Integrace navazuje na platformu RIoT **Michala Bureše** a na dopravní rozšíření RTAlerts **Dominika Vondrušky**. Použité externí aplikace jsou:

- Analyticity od **Magdalény Ondruškové**: analytická aplikace nad Waze daty, původně dostupná v organizaci [analyticity](https://github.com/analyticity) a jako [waze-data-analysis](https://github.com/MagdalenaOndruskova/waze-data-analysis).
- Lissy od **Juraje Lazúra**: aplikace pro analýzu městské hromadné dopravy, původně dostupná jako [Jorgen98/Lissy](https://github.com/Jorgen98/Lissy).

## Moduly

- [Analyticity Backend](AnalyticityBE/) ([README](AnalyticityBE/README.md)) - zachovává API pro frontend Analyticity, ale data Waze načítá přes WebSocket API RIoT.
- [Analyticity Frontend](AnalyticityFE/) ([README](AnalyticityFE/README.md)) - původní uživatelské rozhraní Analyticity, které používá backend upravený pro RIoT.
- [Lissy](Lissy/) ([README](Lissy/README.md)) - aplikace pro analýzu MHD, jejíž dynamická data o zpoždění jsou získávána přes REST API RIoT.

## Spuštění

Pro běžné spuštění stačí Docker, Docker Compose a hlavní `Makefile`. Analyticity Frontend se spouští jako lokální Vite proces přes npm, protože v tomto repozitáři nemá samostatný Docker Compose.

Spuštění Analyticity:

```bash
make run-analyticity
```

Zastavení Analyticity:

```bash
make stop-analyticity
```

Spuštění Lissy:

```bash
make run-lissy
```

Zastavení Lissy:

```bash
make stop-lissy
```

Všechny dostupné cíle vypíše:

```bash
make help
```

## Napojení Na RIoT

Obě aplikace očekávají běžící RIoT s odpovídajícími historickými daty a API klíčem.

Před spuštěním je nutné doplnit platný RIoT API klíč do konfiguračních souborů jednotlivých aplikací:

- Analyticity Backend: [AnalyticityBE/docker-compose.yaml](AnalyticityBE/docker-compose.yaml), proměnná `RIOT_API_KEY`.
- Lissy: [Lissy/env/.env](Lissy/env/.env), proměnná `RIOT_API_KEY`.

Analyticity používá WebSocket API RIoT:

```text
ws://localhost:9090/ws
```

Lissy používá REST API RIoT:

```text
http://localhost:9090/rest
```

Výchozí Docker konfigurace používá z kontejnerů adresu `host.docker.internal`, aby se aplikace mohly připojit k RIoT běžícímu na hostitelském stroji.

## Porty

| Aplikace | Adresa | Poznámka |
| --- | --- | --- |
| Analyticity Frontend | <http://localhost:5173/waze-data-analysis/> | Vite dev server |
| Analyticity Backend | <http://localhost:8002> | FastAPI backend |
| Lissy | <http://localhost:8082/lissy> | Nginx proxy podle `LISSY_HTTP_PORT` |

## Vývojové Závislosti

Pro spuštění sestav jsou potřeba:

- Docker a Docker Compose,
- Node.js a npm pro Analyticity Frontend,
- běžící RIoT s naplněnými daty Waze nebo MHD podle testované aplikace.

Při samostatném lokálním vývoji se dále používá Python pro Analyticity Backend a Node.js/npm pro Lissy backend i frontend.

## Licence

Analyticity Backend a Analyticity Frontend jsou licencované pod MIT licencí. Lissy je licencovaná pod GNU GPL v3. Konkrétní licenční texty jsou uvedené v jednotlivých modulech.
