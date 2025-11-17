from datetime import timedelta

DOMAIN = "osservaprezzi_carburanti"

# Configuration types
CONF_CONFIG_TYPE = "config_type"
CONF_TYPE_STATION = "station"
CONF_TYPE_ZONE = "zone"

# Station config
CONF_STATION_ID = "station_id"

# Zone config
CONF_LATITUDE = "latitude"
CONF_LONGITUDE = "longitude"
CONF_RADIUS = "radius"
CONF_FUEL_TYPE = "fuel_type"
CONF_IS_SELF = "is_self"
CONF_POINTS = "points"  # Support for multiple points in zone search

# Options
CONF_UPDATE_TIME = "update_time"
DEFAULT_UPDATE_TIME = "07:30"

# API
DEFAULT_NAME = "Osservaprezzi Carburanti"
BASE_URL = "https://carburanti.mise.gov.it/ospzApi"
STATION_ENDPOINT = "/registry/servicearea/{station_id}"
ZONE_ENDPOINT = "/search/zone"

# Additional API endpoints
FUELS_ENDPOINT = "/registry/fuels"
LOGOS_ENDPOINT = "/registry/alllogos"

# Service type mapping for API fuel ID format
SERVICE_TYPES = {
    "x": "any",
    "1": "self",
    "0": "servito"
}

# Fuel types mapping
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

# Headers for API calls
DEFAULT_HEADERS = {
    "Accept": "application/json",
    "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "Content-Type": "application/json",
    "Pragma": "no-cache",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36",
}

# Sensor attributes
ATTR_STATION_NAME = "station_name"
ATTR_STATION_ADDRESS = "station_address"
ATTR_STATION_BRAND = "station_brand"
ATTR_LAST_UPDATE = "last_update"
ATTR_VALIDITY_DATE = "validity_date"
ATTR_FUEL_TYPE_NAME = "fuel_type_name"
ATTR_IS_SELF = "is_self_service"
ATTR_DISTANCE = "distance"
ATTR_LATITUDE = "latitude"
ATTR_LONGITUDE = "longitude"