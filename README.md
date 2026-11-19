# ESP8266 Bulb Control and Power Consumption

This project provides a multi-user website for live DHT11 temperature and humidity, LDR natural-light readings, and ON/OFF control of a single-channel relay on each NodeMCU V3 (ESP8266). Each account can add multiple appliances, with one NodeMCU and single-channel relay per independently controlled bulb. Sensor readings and relay state travel from each device to the Render website over Wi-Fi and HTTPS.

## Deploy to Render

1. Push this project folder to GitHub. If it is inside a larger repository, set Render's **Root Directory** to `outputs/HomeSense-IoT/iot-home-control`.
2. Create a Render Blueprint from the repository using `render.yaml`.
3. The blueprint binds the app to Render's `PORT`, enables secure session cookies, and mounts persistent storage at `/var/data` for SQLite.
4. The configured `starter` service and persistent disk are paid Render resources. The disk keeps account and readings data across deploys.
5. Wait until `/healthz` is healthy, then open the HTTPS URL Render assigns.
6. Register or sign in. Choose **Add appliance** in the left sidebar (or open Connections), enter the NodeMCU's 2.4 GHz Wi-Fi name and password, then download the generated Arduino sketch. Use **Add appliance** again for each additional appliance; each one needs its own NodeMCU and single-channel relay.

The generated sketch has that device's Wi-Fi settings, the deployed website URL, and its private device token. Keep it private. Each setup creates a separate appliance entry; it does not overwrite existing appliances. If replacing a board, remove its old entry before adding its replacement.

## Arduino IDE

1. Install ESP8266 board support and the **DHT sensor library by Adafruit** from Arduino IDE's Library Manager.
2. Use **Connections → Add appliance → Prepare Arduino sketch** to download a sketch preconfigured for the deployed website and Wi-Fi. Do not upload the unconfigured template directly.
3. In Arduino IDE, select **NodeMCU 1.0 (ESP-12E Module)** and the correct COM port, then upload over USB.
4. After upload, the NodeMCU connects to Wi-Fi, verifies HTTPS, and sends DHT11, LDR, and relay readings every three seconds. It checks for website relay commands on the same cycle.
5. The site refreshes sensor values automatically. ON/OFF commands are queued for the matching device and acknowledged by that NodeMCU.

The sketch template is `firmware/homesense_esp8266/homesense_esp8266.ino`; it needs the generated Wi-Fi credentials and device token to connect to an account.

## Hardware connections

| Part | NodeMCU connection |
| --- | --- |
| DHT11 VCC | 3V3 |
| DHT11 GND | GND |
| DHT11 DATA | D4 / GPIO2 |
| Relay IN | D1 / GPIO5 |
| Relay GND | GND (shared ground) |
| Relay VCC | Relay module's required supply |
| LDR divider midpoint | A0 |
| LDR divider | 3V3 → LDR → A0; A0 → 10 kΩ resistor → GND |

For a bare four-pin DHT11, add a 10 kΩ pull-up between DATA and 3V3; many three-pin modules already include it. Use a relay module whose input accepts 3.3 V logic. A relay board that requires 5 V coil power needs a separate suitable supply and shared ground; do not try to power its coil from the NodeMCU 3V3 pin or GPIO. The sketch assumes an active-low relay. Set `RELAY_ACTIVE_LOW` to `false` only for an active-high module.

For a low-voltage demo bulb, connect supply positive through relay **COM** and **NO** to bulb positive; connect bulb negative to supply negative. Leave **NC** unused. Mains-powered bulbs require a suitably rated enclosed relay and wiring by a qualified electrician.

## Energy and costs

There is no power meter in this hardware setup. Energy and costs are estimates using each bulb's default 60 W rating, recorded ON time, and the user's electricity rate. A compatible energy meter is required for measured consumption. The site supports day, week, month, and year reports and printing/sharing reports.

## Project files

- `server.py` — web app, account APIs, device pairing, telemetry, and relay command queue
- `web/` — dashboard, Home comfort sensor display, appliance controls, and Add appliance flow
- `firmware/homesense_esp8266/homesense_esp8266.ino` — ESP8266 Wi-Fi firmware for DHT11, LDR, and relay
- `render.yaml` — Render service, health check, and persistent disk configuration

