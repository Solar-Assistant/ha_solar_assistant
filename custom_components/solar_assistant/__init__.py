"""SolarAssistant — Home Assistant integration entry points."""
from __future__ import annotations

import fnmatch

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import CONF_ENABLED_TOPICS, DOMAIN
from .coordinator import SolarAssistantCoordinator

PLATFORMS = ["binary_sensor", "number", "select", "sensor", "switch"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = SolarAssistantCoordinator(hass, entry)
    await coordinator.async_start()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator: SolarAssistantCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_stop()
    return unloaded


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Purge entities whose topic matches no enabled glob, then reload the entry."""
    enabled = entry.options.get(CONF_ENABLED_TOPICS)
    if enabled is not None:
        registry = er.async_get(hass)
        prefix = f"{entry.unique_id}_"
        for ent in list(er.async_entries_for_config_entry(registry, entry.entry_id)):
            if not ent.unique_id.startswith(prefix):
                continue
            topic = ent.unique_id[len(prefix):]
            if not any(fnmatch.fnmatchcase(topic, g) for g in enabled):
                registry.async_remove(ent.entity_id)
    await hass.config_entries.async_reload(entry.entry_id)
