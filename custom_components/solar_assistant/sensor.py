"""SolarAssistant read-only sensor entities (Status + Info groups)."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import SolarAssistantCoordinator
from .entity import SolarAssistantMetricEntity

_LOGGER = logging.getLogger(__name__)


def _is_for_us(defn: dict[str, Any]) -> bool:
    """Sensor platform handles read-only metrics only.

    Anything with platform ∈ {number, select, switch} is owned by the
    matching writable platform. Older SA builds without a `platform`
    field (None) fall through to sensor as the safe default.
    """
    if defn.get("group") == "Ignore":
        return False
    platform = defn.get("platform")
    return platform is None or platform == "sensor"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: SolarAssistantCoordinator = hass.data[DOMAIN][entry.entry_id]
    known: set[str] = set()

    @callback
    def _on_new_metric(topic: str) -> None:
        if topic in known:
            return
        defn = coordinator.definitions.get(topic)
        if defn is None or not _is_for_us(defn):
            return
        known.add(topic)
        async_add_entities([SolarAssistantSensor(coordinator, entry, topic, defn)])

    from homeassistant.helpers.dispatcher import async_dispatcher_connect
    entry.async_on_unload(
        async_dispatcher_connect(hass, coordinator.signal_new_metric, _on_new_metric)
    )

    for topic, defn in list(coordinator.definitions.items()):
        if not _is_for_us(defn):
            continue
        if topic not in known:
            known.add(topic)
            async_add_entities([SolarAssistantSensor(coordinator, entry, topic, defn)])


class SolarAssistantSensor(SolarAssistantMetricEntity, SensorEntity):
    def __init__(
        self,
        coordinator: SolarAssistantCoordinator,
        entry: ConfigEntry,
        topic: str,
        defn: dict[str, Any],
    ) -> None:
        super().__init__(coordinator, entry, topic, defn)
        self._attr_native_unit_of_measurement = (
            defn.get("unit_of_measurement") or defn.get("unit") or None
        )
        self._attr_device_class = defn.get("device_class")
        self._attr_state_class = defn.get("state_class")

    @property
    def native_value(self) -> Any:
        return self._coordinator.data.get(self._topic)
