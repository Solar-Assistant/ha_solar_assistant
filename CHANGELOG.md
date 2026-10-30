## v0.3.0 (2026-10-30)

### Feat

- **options**: make the unit's status metrics selectable
- **coordinator**: take the unit's system metrics from the metrics stream

### Fix

- **config-flow**: keep the unit's system metrics out of the options flow
- **coordinator**: trust SA's device_class on system metrics

## v0.2.0 (2026-07-02)

### Feat

- **recovery**: complete LAN IP-change recovery for local entries
- **sensor**: expose the unit's system metrics as diagnostics

## v0.1.0 (2026-06-12)

### Feat

- **config-flow**: name search for the cloud site picker

### Fix

- **sensor**: only create sensors for enabled metrics
- **binary_sensor**: name the connection entity via translation_key
- **discovery**: pass site_id/site_key on cloud REST discovery
