"""SolarAssistant boolean Settings entities (e.g. Grid charge, Sell points)."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
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
        if defn is None or defn.get("platform") != "switch":
            return
        known.add(topic)
        async_add_entities([SolarAssistantSwitch(coordinator, entry, topic, defn)])

    entry.async_on_unload(
        async_dispatcher_connect(hass, coordinator.signal_new_metric, _on_new_metric)
    )

    for topic, defn in list(coordinator.definitions.items()):
        if defn.get("platform") != "switch":
            continue
        if topic not in known:
            known.add(topic)
            async_add_entities([SolarAssistantSwitch(coordinator, entry, topic, defn)])


class SolarAssistantSwitch(SolarAssistantMetricEntity, SwitchEntity):
    def __init__(
        self,
        coordinator: SolarAssistantCoordinator,
        entry: ConfigEntry,
        topic: str,
        defn: dict[str, Any],
    ) -> None:
        super().__init__(coordinator, entry, topic, defn)
        self._payload_on: str = str(defn.get("payload_on") or "1")
        self._payload_off: str = str(defn.get("payload_off") or "0")

    @property
    def is_on(self) -> bool | None:
        v = self._coordinator.data.get(self._topic)
        if v is None:
            return None
        # Server may report the payload value, a bool, or a string like "Enabled"/"Disabled".
        if isinstance(v, bool):
            return v
        s = str(v).strip().lower()
        if s == self._payload_on.lower() or s in ("true", "1", "on", "enabled", "yes"):
            return True
        if s == self._payload_off.lower() or s in ("false", "0", "off", "disabled", "no"):
            return False
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._coordinator.set_setting(self._topic, self._payload_on)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._coordinator.set_setting(self._topic, self._payload_off)
