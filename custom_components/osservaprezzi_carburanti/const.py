DOMAIN = "osservaprezzi_carburanti"

# Station config
CONF_STATION_ID = "station_id"

# Options
CONF_CRON_EXPRESSION = "cron_expression"
DEFAULT_CRON_EXPRESSION = "30 8 * * *"  # Daily at 07:30

# API
BASE_URL = "https://carburanti.mise.gov.it/ospzApi"
STATION_ENDPOINT = "/registry/servicearea/{station_id}"
API_REQUEST_INTERVAL_SECONDS = 2

# CSV data source
CSV_URL = "https://www.mimit.gov.it/images/exportCSV/anagrafica_impianti_attivi.csv"
CSV_UPDATE_INTERVAL = 24  # hours

# Additional services mapping
ADDITIONAL_SERVICES = {
    "1": {
        "name": "Food&Beverage",
        "icon": "mdi:food",
        "description": "Bar, restaurant or refreshment point",
        "image_url": "https://carburanti.mise.gov.it/ospzSearch/assets/servizi/1.gif"
    },
    "2": {
        "name": "Officina",
        "icon": "mdi:car-wrench",
        "description": "Repair and maintenance service",
        "image_url": "https://carburanti.mise.gov.it/ospzSearch/assets/servizi/2.gif"
    },
    "3": {
        "name": "Sosta Camper/Tir",
        "icon": "mdi:truck",
        "description": "Parking area for campers and trucks",
        "image_url": "https://carburanti.mise.gov.it/ospzSearch/assets/servizi/3.gif"
    },
    "4": {
        "name": "Scarico per camper",
        "icon": "mdi:water-pump",
        "description": "Black/gray water dump point",
        "image_url": "https://carburanti.mise.gov.it/ospzSearch/assets/servizi/4.gif"
    },
    "5": {
        "name": "Area bambini",
        "icon": "mdi:human-child",
        "description": "Playground for children",
        "image_url": "https://carburanti.mise.gov.it/ospzSearch/assets/servizi/5.gif"
    },
    "6": {
        "name": "Bancomat",
        "icon": "mdi:cash-multiple",
        "description": "Automatic teller machine",
        "image_url": "https://carburanti.mise.gov.it/ospzSearch/assets/servizi/6.gif"
    },
    "7": {
        "name": "Servizi per disabili",
        "icon": "mdi:wheelchair-accessibility",
        "description": "Services accessible for disabled people",
        "image_url": "https://carburanti.mise.gov.it/ospzSearch/assets/servizi/7.gif"
    },
    "8": {
        "name": "Wi-Fi",
        "icon": "mdi:wifi",
        "description": "Free or paid Wi-Fi connection",
        "image_url": "https://carburanti.mise.gov.it/ospzSearch/assets/servizi/8.gif"
    },
    "9": {
        "name": "Gommista",
        "icon": "mdi:tire",
        "description": "Tire and tire service",
        "image_url": "https://carburanti.mise.gov.it/ospzSearch/assets/servizi/9.gif"
    },
    "10": {
        "name": "Autolavaggio",
        "icon": "mdi:car-wash",
        "description": "Car washing service",
        "image_url": "https://carburanti.mise.gov.it/ospzSearch/assets/servizi/10.gif"
    },
    "11": {
        "name": "Ricarica elettrica",
        "icon": "mdi:ev-station",
        "description": "Charging stations for electric vehicles",
        "image_url": "https://carburanti.mise.gov.it/ospzSearch/assets/servizi/11.gif"
    }
}

# Map service IDs to translation keys for entity localization
SERVICE_ID_TO_TRANSLATION_KEY = {
    "1": "food_beverage",
    "2": "workshop",
    "3": "camper_truck_parking",
    "4": "camper_dump",
    "5": "play_area",
    "6": "atm",
    "7": "disabled_access",
    "8": "wifi",
    "9": "tire_service",
    "10": "car_wash",
    "11": "ev_charging"
}

# Headers for API calls
DEFAULT_HEADERS = {
    "Accept": "application/json",
    "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
    "Connection": "keep-alive",
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
ATTR_LATITUDE = "latitude"
ATTR_LONGITUDE = "longitude"
ATTR_PREVIOUS_PRICE = "previous_price"
ATTR_PRICE_CHANGED_AT = "price_changed_at"

SERVICE_FORCE_CSV_UPDATE = "force_csv_update"
SERVICE_CLEAR_CACHE = "clear_cache"
SERVICE_COMPARE_STATIONS = "compare_stations"
