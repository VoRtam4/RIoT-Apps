# WAZE DATA ANALYSIS (backend application)

This project was created as Master's thesis at the Faculty of Information Technology, 
Brno university of Technology. 

The main purpose of this project was to analyze and visualize traffic 
data collected from users using navigation application Waze. 
This application was created in cooperation with [data.brno.cz](https://data.brno.cz/)


In this repository you can find a code for running backend application.
This application now supports running against **RIoT** as the primary source of Waze jam data.
The backend keeps the original HTTP API for the frontend, but Waze jam queries are resolved through
the **RIoT websocket API** instead of direct calls to the city Waze feed.

Datasets can be found at: [Traffic delays](https://data.brno.cz/datasets/mestobrno::plynulost-dopravy-traffic-delays/about) and [Traffic events](https://data.brno.cz/datasets/mestobrno::ud%C3%A1losti-na-cest%C3%A1ch-traffic-events/about). 

Repository for frontend application can be found here [waze-data-analysis](https://github.com/MagdalenaOndruskova/waze-data-analysis).

Final application is available at: 
- localhost under address `localhost/waze-data-analysis/`
- testing deployment at [data.brno](https://data.brno.cz/apps/70b6c168c69e4955a354622b3e92dd49/explore)

__________________________________
### Usage
Set the required RIoT connection variables before starting the backend:

```bash
export RIOT_API_KEY="<api-key-with-sd_types.read, sd_instances.read, kpi_definitions.read/create, time_series.read>"
export RIOT_WS_URL="ws://localhost:9090/ws"
export RIOT_WS_ORIGIN="http://localhost:8080"
```

Optional tuning:

```bash
export RIOT_QUERY_CHUNK_DAYS="7"
```

Then run:

```bash
docker compose up --build
```

Notes:
- The backend auto-discovers the RIoT SDType `WAZE_JAM_LOCATION`.
- It auto-creates two Analyticity-specific KPI definitions if they do not exist yet:
  - `delay != 0 and jamCount == 1`
  - `delay != 0 and jamCount > 2`
- Street severity is then computed aplikačně v tomto backendu, ne ve Waze preprocessoru.
- Thresholds follow the original Analyticity rule for the selected period:
  - `green`: count `< days * 3`
  - `orange`: count `< days * 7`
  - `red`: count `>= days * 7`
- Alert-specific endpoints no longer call the external Waze alert source; they currently return empty datasets unless another source is added locally.

___________________________________
### License 
This project is licensed under MIT License.
____________________________________

