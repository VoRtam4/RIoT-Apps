# Lissy

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)

Lissy je analytická aplikace pro dlouhodobou analýzu městské hromadné dopravy, v tomto repozitáři funkcionalitami omezená pro potřeby integračního testování platformy RIoT. Původní aplikace pracuje s modelem dopravního systému, linkami, trasami, zastávkami, spoji a jejich vývojem v čase. Autorem původní aplikace je **Juraj Lazúr** a původní repozitář je dostupný jako [Jorgen98/Lissy](https://github.com/Jorgen98/Lissy).

Tato upravená varianta zachovává vlastní model Lissy pro statická a referenční data, ale část pracující s dynamickými provozními daty je napojená na RIoT. Slouží hlavně k ověření, že RIoT může nahradit zdroj provozních dat v aplikaci, která má vlastní databáze, vlastní API i vlastní frontend.

## Úloha RIoT

Lissy si nadále spravuje statická GTFS data, síťové podklady a interní reprezentaci linek, tras, zastávek a spojů. RIoT zde nahrazuje dynamickou část datové vrstvy, především historická data o zpoždění spojů MHD. Backend při inicializaci dohledá typ `MHD_TRIP`, ověří parametr `delay` a případně založí KPI pro intervaly zpoždění:

- `Lissy MHD Delay under 3 minutes`: `delay < 3`.
- `Lissy MHD Delay 3 to 5 minutes`: `delay >= 3 AND delay < 5`.
- `Lissy MHD Delay 5 to 10 minutes`: `delay >= 5 AND delay < 10`.
- `Lissy MHD Delay 10 minutes and more`: `delay >= 10`.

Při dotazu na konkrétní spoj se historická data filtrují podle `service_date`, `departure_time` a případně `route_id`. Časové okno začíná deset minut před plánovaným odjezdem a pokrývá zhruba dvacet hodin průběhu. Pokud jsou dostupná KPI i surová data, backend prochází surové body v čase, udržuje stav splnění KPI a promítá nejhorší aktivní úroveň na segment mezi zastávkami podle `segment_from_stop_id` a `segment_to_stop_id`. Pokud má jen KPI data, použije nejhorší aktivní úroveň pro všechny segmenty. Pokud má jen surová data, vezme nejvyšší `delay` na segmentu.

Pro původní datový tvar Lissy se KPI intervaly převádějí na reprezentativní hodnoty podle úrovně zpoždění:

- `green`: zpoždění je menší než 3 minuty, reprezentativně přibližně `1` minuta.
- `yellow`: zpoždění je od 3 do 5 minut, reprezentativně střed intervalu, tedy přibližně `4` minuty.
- `red`: zpoždění je od 5 do 10 minut, reprezentativně střed intervalu, tedy přibližně `7,5` minuty.
- `blue`: zpoždění je 10 minut a více, reprezentativně hodnota nad 10 minut.

RIoT tedy nenahrazuje celou aplikaci Lissy, ale jen dynamickou integrační vrstvu pro provozní data.

## Spuštění

Aplikace se spouští přes Docker Compose. Výchozí konfigurace je v [env/.env](env/.env). Pro lokální testování je potřeba mít dostupný běžící RIoT a v konfiguraci nastavit hlavně:

- `RIOT_BACKEND_URL`: REST adresa RIoT, výchozí je `http://host.docker.internal:9090/rest`.
- `RIOT_API_KEY`: API klíč do RIoT.
- `RIOT_MHD_SD_TYPE_UID`: UID typu MHD dat, výchozí je `MHD_TRIP`.
- `LISSY_HTTP_PORT`: lokální HTTP port aplikace, v dodané konfiguraci `8082`.

Před spuštěním je nutné do souboru [env/.env](env/.env) doplnit platný API klíč do proměnné `RIOT_API_KEY`. Bez platného klíče backend Lissy nenačte z RIoT historická KPI vyhodnocení ani surová provozní data.

Samostatné spuštění Lissy:

```bash
docker compose --env-file ./env/.env up --build -d
```

Zastavení běžící sestavy:

```bash
docker compose --env-file ./env/.env down
```

Po spuštění je aplikace dostupná na adrese:

```text
http://localhost:8082/lissy
```

## Podklady

Aplikace potřebuje síťové podklady ve vlastním formátu. Ukázkové soubory jsou v adresáři [backend/transport_networks](backend/transport_networks). Původní Lissy předpokládá jejich vložení do adresáře `backend/backups`. V této testovací variantě je potřeba zachovat požadavky konkrétní běhové konfigurace.

## Licence

Původní aplikace Lissy je licencovaná pod GNU GPL v3. Licence je uvedena v souboru [LICENSE](LICENSE).
