"""SolarAssistant connectivity diagnostic entity."""
from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import SolarAssistantCoordinator
from .entity import unit_device_info


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: SolarAssistantCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([SolarAssistantConnectionSensor(coordinator, entry)])


class SolarAssistantConnectionSensor(BinarySensorEntity):
    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "connection"

    def __init__(
        self,
        coordinator: SolarAssistantCoordinator,
        entry: ConfigEntry,
    ) -> None:
        self._coordinator = coordinator
        # See entity.py for why we key on entry.unique_id rather than entry.entry_id.
        self._attr_unique_id = f"{entry.unique_id}_connection"
        self._attr_device_info = unit_device_info(entry)

    @property
    def is_on(self) -> bool:
        return self._coordinator.is_connected

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        ts = self._coordinator.last_connected_at
        return {
            "connected_host": self._coordinator.connected_host,
            "last_connected_at": ts.isoformat() if ts else None,
            "last_error": self._coordinator.last_error,
        }

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                self._coordinator.signal_connection_state,
                self.async_write_ha_state,
            )
        )
