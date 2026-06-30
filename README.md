# SolarAssistant for Home Assistant

Official [Home Assistant](https://www.home-assistant.io) integration for [SolarAssistant](https://solar-assistant.io).

Connects your SolarAssistant unit to Home Assistant over the local network or through the SolarAssistant cloud. Exposes your solar system as
native entities: live telemetry sensors plus writable settings for inverter parameters - no separate MQTT broker required.

## Features

- **Live data**: PV power, battery state of charge, grid power, load power, voltage, current, temperature, and more, streamed in real time
  over WebSocket.
- **Writable settings**: max charge/discharge current, work mode schedules, stop/start capacities, and other inverter parameters exposed as
  `number`, `select`, and `switch` entities.
- **Local or cloud**: works directly on your LAN with your web password, or via the SolarAssistant cloud with an API key.
- **Multi-unit**: add one config entry per SolarAssistant unit.

## Requirements

- Home Assistant 2024.11 or later
- A [SolarAssistant unit](https://solar-assistant.io/shop) on your LAN, or a SolarAssistant cloud account
- SolarAssistant version **2026-06-12 or later** on the unit - the release that added Home Assistant integration support

## Installation

### Via Add-on (recommended)

1. On your Home Assistant, Go to **Settings → Apps → Install App → ⋮ → Repositories → Add**.
2. Provide `https://github.com/Solar-Assistant/ha_addons` for the URL and click **Add**. 
3. Find **SolarAssistant** in the app store, install it, and click **Start**.

Home Assistant will restart automatically and the integration is ready to configure.

### Manual

1. Download the [latest release](https://github.com/Solar-Assistant/ha_solar_assistant/releases/latest) and unzip it.
2. Copy the `custom_components/solar_assistant` folder into `/config/custom_components/` on your Home Assistant instance. The easiest way to
   reach `/config` without a terminal is via the **Samba share** app (Settings → Apps → Samba share). The **Terminal & SSH** app also works.
3. Restart Home Assistant.

## Configuration

After the installation, go to **Settings → Devices & Services → Add integration** and search for **SolarAssistant**.

### Local password

Use this if your SolarAssistant unit is on the same network as Home Assistant.

1. Choose **Local password** when prompted.
2. Enter the unit's hostname or IP address (try `solar-assistant.local` first) and your SolarAssistant web interface password.

### Cloud API key

Use this if you have a SolarAssistant cloud account, or if the unit is not directly reachable.

1. Generate an API key at [solar-assistant.io](https://solar-assistant.io) under your account settings. 
2. Choose **Cloud API key** and paste the key.
3. Select which site to add.

## Entities

Entity names and counts depend on your inverter model. A typical single-inverter setup includes:

| Domain          | Examples                                                                                                     |
| --------------- | ------------------------------------------------------------------------------------------------------------ |
| `sensor`        | PV power, battery state of charge, grid power, load power, voltage, current, temperature, inverter mode      |
| `number`        | Max charge/discharge current, max charge/discharge power, stop charge/discharge capacity, voltage thresholds |
| `select`        | Work mode slot priority, work mode start/end time, export limiter source                                     |
| `switch`        | Work mode slot enabled                                                                                       |
| `binary_sensor` | Connection status                                                                                            |

### Topic filtering

By default, the integration subscribes to a curated set of the most useful live metrics. To choose which entities appear, go to Settings →
Devices & Services → SolarAssistant → **Configure**.

## Contributing

Pull requests are welcome. The integration is a standard HA custom component under `custom_components/solar_assistant/`.

The Python client is the external [`py-solar-assistant`](https://pypi.org/project/py-solar-assistant/) package, pinned in
`manifest.json` (`requirements`) and installed by Home Assistant at setup. It is not vendored - to depend on a new client release, bump the
pinned version in `manifest.json` once it is published to PyPI.

To test an unreleased client against the integration on a running Home Assistant box, use [`scripts/release_local.sh`](scripts/release_local.sh),
which builds [`py_solar_assistant`](https://github.com/Solar-Assistant/py_solar_assistant) from a sibling checkout and deploys it alongside
the integration.

## Releasing

Maintainers cut releases from a developer machine with `scripts/release.sh`. See [RELEASING.md](RELEASING.md) for the full process.

## Roadmap

### Energy Dashboard support

The integration exposes only live **power** metrics (`W`, `state_class: measurement`), which the Home Assistant
[Energy Dashboard](https://www.home-assistant.io/docs/energy/) rejects - it needs cumulative **energy** sensors (`kWh`,
`state_class: total_increasing`). SolarAssistant tracks these totals (grid in/out, battery in/out, PV, load) but only publishes them on the
MQTT path, not the local REST/WebSocket API the integration reads from.

Fix: expose those energy totals through the metrics API. No integration change is needed - it already passes `device_class`, `state_class`,
and `unit_of_measurement` through untouched.

### Surface SA → inverter/battery link state

`binary_sensor.<entry>_connection` reflects whether HA can reach the SolarAssistant WebSocket, but not whether the inverter or battery is
actually connected to the SA unit. If the hardware cable is pulled, the WebSocket stays up and the sensor stays green while data silently
stops.

Fix: a client-side watchdog that flips the sensor unavailable if no `data` event arrives within ~60 s. Also add `last_data_at`,
`inverter_connected`, and `battery_connected` as state attributes so the user can see which leg is broken.

### Distribution

- **HACS default list** - submit the repo to `github.com/hacs/default` so users can find it by searching "SolarAssistant" without adding a
  custom repository URL.
- **Zeroconf discovery** - add a `zeroconf` entry to `manifest.json` and `async_step_zeroconf` to `config_flow.py` so HA auto-discovers
  units on the LAN and shows them in the **Discovered** card.

### Pre-release validation

Run [hassfest](https://developers.home-assistant.io/blog/2020/04/16/hassfest) as a `scripts/release.sh` preflight so manifest and integration
errors are caught before a release is cut. hassfest is not packaged for standalone use, so this runs against a local Home Assistant core
checkout.

## License

Apache 2.0 - see [LICENSE](LICENSE).

This licence covers the Home Assistant integration in this repository only. The SolarAssistant platform, including the downloadable device
software and cloud infrastructure, is proprietary and distributed under separate terms. See [NOTICE](NOTICE) for the copyright and scope
statement.

