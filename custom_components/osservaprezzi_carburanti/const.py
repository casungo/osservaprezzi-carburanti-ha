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
    4: "GPL",
    5: "Metano",
    6: "E85",
    7: "H2"
}

DEFAULT_HEADERS = {
    "Accept": "application/json",
    "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "Pragma": "no-cache",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36",
    "sec-ch-ua": '"Not;A=Brand";v="99", "Google Chrome";v="139", "Chromium";v="139"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"'
}

ATTR_STATION_NAME = "station_name"
ATTR_STATION_ADDRESS = "station_address"
ATTR_STATION_BRAND = "station_brand"
ATTR_LAST_UPDATE = "last_update"
ATTR_VALIDITY_DATE = "validity_date"
ATTR_FUEL_TYPE = "fuel_type"
ATTR_IS_SELF = "is_self_service"