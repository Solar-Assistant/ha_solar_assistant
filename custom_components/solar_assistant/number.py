"""SolarAssistant numeric Settings entities (e.g. Max charge current, Max solar power)."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import SolarAssistantCoordinator
from .entity import SolarAssistantMetricEntity

_LOGGER = logging.getLogger(__name__)


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
        if defn is None or defn.get("platform") != "number":
            return
        known.add(topic)
        async_add_entities([SolarAssistantNumber(coordinator, entry, topic, defn)])

    entry.async_on_unload(
        async_dispatcher_connect(hass, coordinator.signal_new_metric, _on_new_metric)
    )

    for topic, defn in list(coordinator.definitions.items()):
        if defn.get("platform") != "number":
            continue
        if topic not in known:
            known.add(topic)
            async_add_entities([SolarAssistantNumber(coordinator, entry, topic, defn)])


class SolarAssistantNumber(SolarAssistantMetricEntity, NumberEntity):
    def __init__(
        self,
        coordinator: SolarAssistantCoordinator,
        entry: ConfigEntry,
        topic: str,
        defn: dict[str, Any],
    ) -> None:
        super().__init__(coordinator, entry, topic, defn)
        self._attr_device_class = defn.get("device_class")
        self._attr_native_unit_of_measurement = (
            defn.get("unit_of_measurement") or defn.get("unit") or None
        )
        if defn.get("min") is not None:
            self._attr_native_min_value = float(defn["min"])
        if defn.get("max") is not None:
            self._attr_native_max_value = float(defn["max"])

    @property
    def native_value(self) -> float | None:
        v = self._coordinator.data.get(self._topic)
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        await self._coordinator.set_setting(self._topic, value)
