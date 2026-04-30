# Osservaprezzi Carburanti for Home Assistant

[🇮🇹 Read this in Italian / Leggi in Italiano](./README.it.md)

Integration for Home Assistant that retrieves fuel prices from the Osservaprezzi service provided by the Italian Ministry of Enterprises and Made in Italy (MIMIT).

## ✨ Features

📊 **Automatic Fuel Sensors**: Automatically creates a sensor for each fuel type available at the selected station.

⏰ **Scheduled Updates**: Data is updated daily at a user-configurable time using cron expressions (default is daily at 08:30).

🏷️ **Complete Station Information**: Sensors include detailed attributes such as station name, brand, address, and last update time.

📍 **Location Sensor**: Creates a diagnostic location sensor for the station with GPS coordinates.

🕐 **Opening Hours Sensors**: Provides sensors for open/closed status and next opening/closing time when available.

🛠️ **Service Binary Sensors**: Automatically creates binary sensors for services when available.

📞 **Contact Information Sensors**: Creates sensors for phone, email, and website when available.

## 🚀 Installation and Configuration

### Installation via HACS

1. **Install HACS** (if you don't have it already): [HACS Guide](https://hacs.xyz/docs/installation/installation/)
2. **Install the integration**:
   - Search for "Osservaprezzi Carburanti" in HACS.
   - Click "Download".
   - Restart Home Assistant.

### Configuration

To configure the integration, go to: "Settings" -> "Devices & Services" -> "+ Add Integration", search for "Osservaprezzi Carburanti" and follow the instructions.

You can also use the following My Home Assistant link (requires the integration to be already installed):

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=osservaprezzi_carburanti)

## 📊 Created Sensors

When you configure a station, the integration automatically creates the following sensors:

### Fuel Price Sensors

- One sensor for each fuel type available at the station (both self-service and served)
- Shows current price in €/L
- Includes attributes for fuel name, service type, last update time, and validity date

### Station Information Sensors

- **Station Name**: The name of the fuel station
- **Station ID**: The Osservaprezzi identifier
- **Address**: Full street address
- **Brand**: Fuel station brand (e.g., Eni, Shell, TotalEnergies)
- **Company**: Operating company name
- **Phone**: Contact phone number (if available)
- **Email**: Contact email (if available)
- **Website**: Official website (if available)
- **Location**: Map marker with GPS coordinates

### Operating Hours Sensors (when data is available)

- **Open/Closed Status**: Binary sensor indicating if the station is currently open
- **Next Change**: Shows when the station will next open or close with time

### Service Binary Sensors (when data is available)

Binary sensors are created for each available service at the station:

- 🍽️ **Food&Beverage**: Bar, restaurant or catering service
- 🔧 **Workshop**: Car repair and maintenance services
- 🚛 **RV/Truck Parking Area**: Designated parking area for campers and trucks
- 💧 **Camper Waste Disposal**: Black/grey water discharge point
- 🧒 **Children's Area**: Playground for children
- 💳 **ATM/Bancomat**: Cash machine availability
- ♿ **Disabled Access**: Accessibility services
- 📶 **Wi-Fi**: Internet connection availability
- 🛞 **Tire Service**: Tire fitting and repair services
- 🚗 **Car Wash**: Vehicle washing services
- 🔌 **Electric Charging**: Electric vehicle charging stations

### Cron Expression Configuration

The integration uses a cron expression to schedule automatic data updates. This can be configured after initial installation through the integration options.

**Default value**: `30 8 * * *` (daily at 8:30 AM)

You can modify this expression to customize when you want the data to be updated. You can use [Crontab Guru](https://crontab.guru) to help build and validate your cron expressions. This tool provides a helpful interface to understand when your scheduled updates will run.

Common examples:

- `0 8 * * *` - Daily at 8:00 AM
- `30 7,19 * * *` - Daily at 7:30 AM and 7:30 PM
- `0 */6 * * *` - Every 6 hours
- `0 8 * * 1-5` - Weekdays at 8:00 AM

### How to Find the Station ID

During configuration, you will be asked to enter the **Station ID** of the facility you want to monitor. To find the Station ID:

1. Go to https://carburanti.mise.gov.it/ospzSearch/zona
2. Search for your favorite station
3. Click on the station
4. In the URL (e.g: https://carburanti.mise.gov.it/ospzSearch/dettaglio/1111) copy the ID (1111)

## 📋 Supported Fuel Types

The integration will create sensors for every possible fuel type.

## 🧩 Dashboard Examples

### Battery State Card

You can display fuel prices with the custom [Battery State Card](https://github.com/maxwroc/battery-state-card). The following example shows diesel prices from all `osservaprezzi_carburanti` entities, sorted from the cheapest to the most expensive:

```yaml
grid_options:
  columns: full
  rows: auto
type: custom:battery-state-card
title: Diesel
secondary_info: "{attributes.station_brand} - {attributes.station_address}"
icon: mdi:fuel
filter:
  include:
    - and:
        - or:
            - name: attributes.fuel_type_name
              value: Gasolio
            - name: attributes.fuel_type_name
              value: Blue*
        - name: entity.platform
          value: osservaprezzi_carburanti
  exclude:
    - name: attributes.validity_date
      operator: ">"
      value: 24h
sort:
  - by: state
    desc: false
collapse: 6
colors:
  steps:
    - value: 1.6
      color: "#00ff00"
    - value: 2
      color: "#ffff00"
    - value: 2.4
      color: "#ff0000"
  gradient: true
tap_action:
  action: navigate
  navigation_path: /config/devices/device/{entity.device_id}
```

To show all fuel types and group them by fuel name, replace the `filter` section and add `group`:

```yaml
filter:
  include:
    - and:
        - or:
            - name: attributes.fuel_type_name
              value: "*"
        - name: entity.platform
          value: osservaprezzi_carburanti
group:
  - by: attributes.fuel_type_name
```

## 📞 Support

For issues or suggestions, open an issue on GitHub.

## 📄 License

This project is released under the MIT License.
