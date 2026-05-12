from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from fastapi import FastAPI
from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi_mail import FastMail, MessageSchema, ConnectionConfig
from starlette.responses import JSONResponse
from riot_integration import riot_service, RiotIntegrationError
from data_preparation_street import find_square, find_nearest_street, get_nearest_street
from finding_route import find_route_by_coord, create_graph
from models import  PlotDataRequestBody, RoutingCoordRequestBody, EmailSchema
import geopandas as gpd

import warnings

# dont show warnings in log
warnings.simplefilter(action='ignore', category=FutureWarning)


app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

conf = ConnectionConfig(
    MAIL_USERNAME="brno.waze@seznam.cz",
    MAIL_PASSWORD="WazeDataAnalys!s123",
    MAIL_FROM="brno.waze@seznam.cz",
    MAIL_PORT=587,
    MAIL_SERVER="smtp.seznam.cz",
    MAIL_STARTTLS=True,
    MAIL_SSL_TLS=False,
    USE_CREDENTIALS=True,
    VALIDATE_CERTS=True
)

# loading files needed in different APIs calls
grid_gdf = gpd.read_file("./datasets/streets_grid.geojson")
merged_gdf_streets = gpd.read_file("./datasets/streets_grid_coord.geojson")
streets_gdf = gpd.read_file("./datasets/streets_exploded.geojson")
routing_base = gpd.read_file("./datasets/new_routing_base.geojson")

# creating graph for finding a route
create_graph(routing_base)


@app.exception_handler(RiotIntegrationError)
async def riot_integration_exception_handler(request: Request, exc: RiotIntegrationError):
    return JSONResponse(status_code=503, content={"message": str(exc)})


def _effective_streets(streets):
    if not streets:
        return []
    return sorted({street for street in streets if street})


def _street_features(street_frame, colors_by_street):
    features = []
    for _, row in street_frame.iterrows():
        features.append(
            {
                "street": row["nazev"],
                "street_name": row["nazev"],
                "path": [[longitude, latitude] for latitude, longitude in row["geometry"].coords],
                "color": colors_by_street.get(row["nazev"], "green"),
            }
        )
    return features


def _build_street_response(street_names, from_time, to_time):
    colors = riot_service.load_street_severity(from_time, to_time, street_names)
    if street_names:
        frame = streets_gdf[streets_gdf["nazev"].isin(street_names)]
    else:
        frame = streets_gdf
    return _street_features(frame, colors)


def _empty_drawer_series(from_date, to_date):
    start = pd.to_datetime(from_date)
    end = pd.to_datetime(to_date) + pd.Timedelta(days=1)
    hours = pd.DataFrame(pd.date_range(start=start, end=end, freq="1H"), columns=["pubMillis"])
    hours["pubMillis_unix"] = hours["pubMillis"].astype(np.int64) / int(1e6)
    zeroes = [0] * len(hours)
    return zeroes, zeroes, hours["pubMillis_unix"].tolist(), zeroes, zeroes, zeroes, zeroes


def _load_drawer_series(from_date, to_date, streets):
    effective_from, effective_to = riot_service.clamp_date_range(from_date, to_date)
    data = riot_service.load_jam_records(from_date, to_date, streets)
    if data.empty:
        return _empty_drawer_series(
            effective_from.date().isoformat(),
            (effective_to - timedelta(days=1)).date().isoformat(),
        )

    hourly = data.groupby(pd.Grouper(key="pubMillis", freq="1H")).agg(
        count_jams=("street", "size"),
        length=("length", "sum"),
        level=("level", "mean"),
        delay=("delay", "sum"),
        speedKMH=("speedKMH", "mean"),
    ).reset_index()

    start = pd.to_datetime(effective_from.date().isoformat())
    end = pd.to_datetime(effective_to.date().isoformat())
    hours = pd.DataFrame(pd.date_range(start=start, end=end, freq="1H"), columns=["pubMillis"])
    merged = hours.merge(hourly, on="pubMillis", how="left")

    merged["count_jams"].fillna(0, inplace=True)
    merged["length"].fillna(0, inplace=True)
    merged["level"].fillna(0, inplace=True)
    merged["delay"].fillna(0, inplace=True)
    merged["speedKMH"].fillna(35, inplace=True)

    merged["delay"] = round(merged["delay"] / 60, 2)
    merged["length"] = round(merged["length"] / 1000, 2)
    merged["level"] = round(merged["level"], 2)
    merged["speedKMH"] = round(merged["speedKMH"], 2)

    filtered = merged[merged["pubMillis"] <= datetime.now()]
    filtered["pubMillis_unix"] = filtered["pubMillis"].astype(np.int64) / int(1e6)

    count_jams = filtered["count_jams"].astype(int).tolist()
    count_alerts = [0] * len(filtered)
    pub_millis = filtered["pubMillis_unix"].tolist()
    speed_kmh = filtered["speedKMH"].tolist()
    delay = filtered["delay"].tolist()
    level = filtered["level"].tolist()
    length = filtered["length"].tolist()
    return count_jams, count_alerts, pub_millis, speed_kmh, delay, level, length


def _top_jam_streets(from_date, to_date, streets):
    data = riot_service.load_jam_records(from_date, to_date, streets)
    if data.empty:
        return [], []

    grouped = data.groupby("street").size().reset_index(name="count").sort_values(by="count", ascending=False)
    grouped = grouped[grouped["street"].str.len() > 0].head(10)
    return grouped["street"].tolist(), grouped["count"].astype(int).tolist()


@app.on_event("startup")
def initialize_riot_integration():
    try:
        riot_service.warm_up()
    except Exception as exc:
        print(f"RIoT warm-up failed: {exc}")


@app.get("/data_availability/")
def get_data_availability():
    return riot_service.get_data_availability()


@app.get("/recount_data/")
def recount_data():
    return {"message": "Direct Waze source is disabled. Data are read from RIoT on demand."}

@app.get("/reverse_geocode/street/")
def get_street(longitude: float, latitude: float, fromTime: str, toTime: str):
    """
    From one coordinate returns whole street to drawn. With calculated delays on it.
    :param longitude: longitude (float type)
    :param latitude: latitude (float type)
    :param fromTime: string format, from what date calculate delays
    :param toTime: string format, to what date calculate delays
    :return: dictionary of street
    """
    coordinates = (float(longitude), float(latitude))
    square_index = find_square(coordinates, grid_gdf)
    streets_in_square = merged_gdf_streets[merged_gdf_streets['grid_squares'].apply(lambda x: str(square_index) in x)]
    nearest_street = find_nearest_street(coordinates, streets_in_square, streets_gdf)
    streets_dict = _build_street_response([nearest_street], fromTime, toTime)
    return {"streets": streets_dict}


@app.get("/street_coord/")
def get_street_coord(street: str, fromTime: str, toTime: str):
    """
    Function returns to given street its coordinates
    :param street: name of the street
    :param fromTime: string format, from what date calculate delays
    :param toTime: string format, to what date calculate delays
    :return: calculated street
    """
    streets_dict = _build_street_response([street], fromTime, toTime)
    return {"streets": streets_dict}


@app.post("/all_delays/")
def get_all_delays(body: PlotDataRequestBody):
    """
    function return all delays
    """
    return _build_street_response(None, body.from_date, body.to_date)


@app.post("/find_route_by_coord/")
def find_route_coord(body: RoutingCoordRequestBody):
    """
    Function returns calculated route
    :param body: Information needed for calculation of route as one object (contains start and end point, time from/to)
    :return: Calculated route
    """
    route, streets_dict, src_street, dst_street = find_route_by_coord(body.src_coord, body.dst_coord,
                                                                      body.from_time, body.to_time,
                                                                      streets_gdf, grid_gdf, merged_gdf_streets)
    if not streets_dict:
        return {'streets_coord': []}

    route_colors = riot_service.load_street_severity(
        body.from_time,
        body.to_time,
        [street["street_name"] for street in streets_dict],
    )
    for street in streets_dict:
        street["color"] = route_colors.get(street["street_name"], "green")

    return {"streets_coord": streets_dict,
            "route": list(route.coords),
            "src_street": src_street,
            "dst_street": dst_street}


@app.post("/draw_alerts/")
def get_points_alerts(body: PlotDataRequestBody):
    """
    Function returns alerts from waze
    :param body: One object, containing data about time interval (from date, to date) and list of streets or concrete route
    :return: Found points
    """
    return []


@app.post("/data_for_plot_drawer/")
def get_data_for_plot_drawer(body: PlotDataRequestBody):
    """
    Function returns basic statistics about traffic situation
    :param body: One object, containing data about time interval (from date, to date) and list of streets or concrete route
    :return: Calculated statistics
    """
    streets = _effective_streets(body.streets)
    data_jams, data_alerts, time, speedKMH, delay, level, length = _load_drawer_series(
        body.from_date,
        body.to_date,
        streets,
    )

    return {"jams": data_jams,
            "alerts": data_alerts,
            "speedKMH": speedKMH,
            "delay": delay,
            "level": level,
            "length": length,
            "xaxis": time}


@app.post("/data_for_plot_streets/")
def get_data_for_plot_bar(body: PlotDataRequestBody):
    """
    Function returns data needed for bar charts (critical streets)

    :param body: One object, containing data about time interval (from date, to date) and list of streets or concrete route
    :return: Data for visualization in bar charts
    """
    streets = _effective_streets(body.streets)
    streets_jams, values_jams = _top_jam_streets(body.from_date, body.to_date, streets)
    return {"streets_jams": streets_jams,
            "values_jams": values_jams,
            "streets_alerts": [],
            "values_alerts": []}


@app.post("/data_for_plot_alerts/")
def get_data_for_plot_pies(body: PlotDataRequestBody):
    """
    Function returns data needed for visualization of count of different alert types

    :param body: One object, containing data about time interval (from date, to date) and list of streets or concrete route
    :return:  Data for visualization
    """
    return {
        "basic_types_values": [],
        "basic_types_labels": [],
    }


@app.post("/data_for_plot_critical_streets/")
def get_data_for_plot_critical_streets(body: PlotDataRequestBody):
    """
    Function returns calculated critical streets statistics

    :param body: One object, containing data about time interval (from date, to date) and list of streets or concrete route
    :return: Data
    """
    streets = _effective_streets(body.streets)
    street_names, values = _top_jam_streets(body.from_date, body.to_date, streets)
    return {
        "streets": street_names,
        "values": values
    }


@app.post("/send_mail/")
async def send_mail(email: EmailSchema):
    """
    Function sends email with received data. Used for sending email from application with user suggestions etc.

    :param email: content of email
    :return: OK, if email was succesfully sended
    """
    template = f"""
        <html>
        <body>

        <h2>{email.subject}!</h2>
        
        <p>{email.body}</p>
        
        <br><br>
        <p>Kontakt na odosielatela: {email.from_email}</p>
        </body>
        </html>
        """

    message = MessageSchema(
        subject=email.subject,
        recipients=['brno.waze@seznam.cz'],  # List of recipients, as many as you can pass
        body=template,
        subtype="html"
    )

    fm = FastMail(conf)
    await fm.send_message(message)
    print(message)

    return JSONResponse(status_code=200, content={"message": "email has been sent"})


@app.get("/full_data/")
def get_full_data():
    """
    Function used for returning all data in precalculated dataset

    :return:  all data of alerts and jams
    """
    from_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    to_date = datetime.now().strftime("%Y-%m-%d")
    jams, alerts, xaxis, _, _, _, _ = _load_drawer_series(from_date, to_date, [])
    return {
        "jams": jams,
        "alerts": alerts,
        "xaxis": xaxis,
    }
