# Osservaprezzi Carburanti for Home Assistant

[🇮🇹 Read this in Italian / Leggi in Italiano](./README.it.md)

An integration for Home Assistant that retrieves fuel prices from the "Osservaprezzi" service provided by the Italian Ministry of Enterprises and Made in Italy (MISE).

## ✨ Features

- 📊 **Automatic Sensors**: Automatically creates a sensor for each available fuel type at the selected station.
- ⏰ **Scheduled Updates**: Data is refreshed daily at a user-configured time.
- 🏷️ **Complete Information**: Sensors include detailed attributes such as station name, brand, address, and last update time.
- 🔧 **Simple Configuration**: Guided setup through the Home Assistant UI.

## 🚀 Installation

### Through HACS (Recommended)

1.  **Install HACS** (if you don't have it already): [HACS Guide](https://hacs.xyz/docs/installation/installation/)
2.  **Add this repository** in HACS:
    - Go to HACS → Integrations.
    - Click the 3 dots in the top right corner.
    - Select "Custom repositories".
    - Add: `casungo/osservaprezzi-carburanti-ha`
    - Category: Integration
3.  **Install the integration**:
    - Search for "Osservaprezzi Carburanti" in HACS.
    - Click "Download".
    - Restart Home Assistant.
    - The integration will be automatically installed in `config/custom_components/osservaprezzi_carburanti/`.

### Manual Installation

1.  **Download the repository**:
    ```bash
    git clone https://github.com/casungo/osservaprezzi-carburanti-ha.git
    ```
2.  **Copy the integration**:
    Copy the `custom_components/osservaprezzi_carburanti` folder into your Home Assistant `config` directory.
3.  **Verify the structure**:
    ```
    config/
    └── custom_components/
        └── osservaprezzi_carburanti/
            ├── __init__.py
            ├── manifest.json
            ├── sensor.py
            └── ...
    ```
4.  **Restart Home Assistant**.

## ⚙️ Configuration

To configure this integration, go to: `Settings` -> `Devices & Services` -> `ADD INTEGRATION`, search for `Osservaprezzi Carburanti`, and follow the on-screen instructions.

You can also use the following My Home Assistant link (requires the integration to be installed first):

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=osservaprezzi_carburanti)

During the setup, you will be asked to provide the **Station ID** for the fuel station you want to monitor.

## 📋 Supported Fuel Types

The integration will create sensors for any of the following fuels reported by the station:

- **Gasoline**: Benzina Self/Servito
- **Diesel**: Gasolio, Blue Diesel, etc.
- **LPG**: GPL Servito
- **Methane**: Metano Servito
- **Biofuels**: E85
- **Hydrogen**: H2

## 🚨 Troubleshooting

### Integration not found

1.  Verify the folder structure is correct: `config/custom_components/osservaprezzi_carburanti/`.
2.  Check that all essential files are present (`__init__.py`, `manifest.json`, etc.).
3.  Review the Home Assistant logs for any import errors related to the integration.
4.  Ensure the domain in `manifest.json` is `osservaprezzi_carburanti`.

### No data is displayed

1.  Ensure the integration has been configured correctly from the UI.
2.  Check that the sensors have a valid state in Developer Tools.
3.  Verify that the Station ID is correct and the station is actively reporting prices.

## 📞 Support

For issues or suggestions:

- Open an issue on GitHub.
- Contact the author.

## 📄 License

This project is released under the MIT License.

## 🙏 Acknowledgements

- The "Osservaprezzi Carburanti" service for providing the data.
- The Home Assistant team for the platform.
- The HACS team for making distribution simple.
