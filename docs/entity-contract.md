# Entity contract

What another integration can rely on when consuming the entities this integration creates. Examples use `10.0.0.10` as the unit's address.

**Identifying our devices.** Match on the registries:

- Every device we create carries `identifiers = {("solar_assistant", <id>)}` - the first element of the pair is the domain, e.g.
  `("solar_assistant", "local:10.0.0.10_Inverters_1")`.
- Every entity we create carries `platform == "solar_assistant"`.

Entity `unique_id`s are `{scope}_{topic}`, for example `local:10.0.0.10_inverter_1/load_power`. `scope` is an opaque internal string, so the
registries are the reliable match. The stable identifier for the unit itself is `system/site_id`.

**Topic naming.** Topics are derived at runtime from the metric name rather than drawn from a fixed list:

```
<device>/<metric name, lowercased, spaces replaced with underscores>

device: total | grid | weather | system | inverter_N | battery_N    (N is 1-based)
```

Only spaces and case are changed, so punctuation in a metric name survives into the topic: `Load power non-essential` on inverter 1 becomes
`inverter_1/load_power_non-essential`, and `Cell voltage - Imbalance` on battery 1 becomes `battery_1/cell_voltage_-_imbalance`.

Which topics exist depends on the inverter and battery drivers in use, and can change while the unit is running. A grid-tied string inverter
reports no `total/load_power` or `total/grid_power`; battery topics disappear if nothing supplies them.

From inside Home Assistant the entity registry already carries the mapping. Our config entry's `unique_id` is the `scope`, so the topic is the
remainder of the entity's `unique_id`:

```python
scope = entry.unique_id       # "local:10.0.0.10"
uid = entity.unique_id        # "local:10.0.0.10_inverter_1/load_power"
topic = uid[len(scope) + 1:]  # "inverter_1/load_power"
```

`device_class`, `state_class` and `unit_of_measurement` come from the entity registry entry, or from the state attributes at runtime; `number`
entities carry their `min`, `max` and `step` under `capabilities`.

That covers the topics currently enabled. For everything a unit can offer, including topics not exposed as entities, `GET /api/v1/metrics?discovery`
returns the full per-install catalog - see the [REST API](https://solar-assistant.io/help/integration/rest-api) and
[WebSocket API](https://solar-assistant.io/help/integration/websocket-api) docs.

**Sign conventions.** Values pass through untransformed, in SolarAssistant's convention:

| Metric                             | Positive      | Negative    |
| ---------------------------------- | ------------- | ----------- |
| `battery_power`, `battery_current` | Charging      | Discharging |
| `grid_power`                       | Importing     | Exporting   |
| `pv_power`, `load_power`           | Always `>= 0` | -           |

These hold together as `Load = PV + Grid - Battery`. Each inverter driver converges on this convention itself, so treat a flipped sign on a
specific model as a bug worth reporting rather than a variant to code around.

**Energy totals reset.** The cumulative `kWh` sensors are period-to-date, not lifetime counters - they reset on the unit's configured schedule
(weekly by default) and are exposed as `total_increasing` so the Energy Dashboard handles the reset. They are calculated from power over time,
not read from inverter energy registers.

**Staleness.** Entities currently follow the WebSocket connection only. If the cable between the SolarAssistant unit and an inverter or battery
is pulled, the connection stays up and the last values are re-broadcast, so a frozen reading is indistinguishable from a live one. Do not treat
a recent state change as proof of fresh data. Marking entities unavailable on device-level data loss is tracked in
[#14](https://github.com/Solar-Assistant/ha_solar_assistant/issues/14).
