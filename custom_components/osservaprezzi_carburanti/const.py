from datetime import timedelta

DOMAIN = "osservaprezzi_carburanti"
CONF_STATION_ID = "station_id"
CONF_UPDATE_TIME = "update_time"
DEFAULT_UPDATE_TIME = "07:30"

DEFAULT_NAME = "Osservaprezzi Carburanti"
BASE_URL = "https://carburanti.mise.gov.it/ospzApi"
STATION_ENDPOINT = "/registry/servicearea/{station_id}"

FUEL_TYPES = {
    1: "Benzina",
    2: "Gasolio",
    3: "Metano",
    4: "GPL",
    5: "Blue Super",
    6: "Hi-Q Diesel",
    7: "Benzina WR 100",
    8: "Benzina Shell V Power",
    9: "Diesel Shell V Power",
    10: "Gasolio Premium",
    11: "Gasolio artico", # Retained original entry
    12: "Benzina Plus 98",
    13: "Gasolio Oro Diesel",
    19: "Gasolio artico", # Added new entry
    20: "Blue Diesel",
    26: "Benzina speciale",
    27: "Gasolio speciale",
    28: "HiQ Perform+",
    231: "DieselMax",
    308: "S-Diesel",
    323: "L-GNC",
    324: "GNL",
    327: "Supreme Diesel",
    328: "E-DIESEL",
    341: "Excellium Diesel",
    394: "HVOlution",
    401: "HVO100",
    404: "HVO",
    406: "HVO",
    410: "BCHVO",
    424: "HVO",
    435: "HVO",
    452: "Diesel HVO",
    473: "HVO"
}

DEFAULT_HEADERS = {
    "Accept": "application/json,text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "Pragma": "no-cache",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36",
}

ATTR_STATION_NAME = "station_name"
ATTR_STATION_ADDRESS = "station_address"
ATTR_STATION_BRAND = "station_brand"
ATTR_LAST_UPDATE = "last_update"
ATTR_VALIDITY_DATE = "validity_date"
ATTR_FUEL_TYPE = "fuel_type"
ATTR_IS_SELF = "is_self_service"