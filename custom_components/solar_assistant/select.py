"""SolarAssistant enum Settings entities (e.g. Battery type, Work mode, Time points)."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
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
        if defn is None or defn.get("platform") != "select":
            return
        known.add(topic)
        async_add_entities([SolarAssistantSelect(coordinator, entry, topic, defn)])

    entry.async_on_unload(
        async_dispatcher_connect(hass, coordinator.signal_new_metric, _on_new_metric)
    )

    for topic, defn in list(coordinator.definitions.items()):
        if defn.get("platform") != "select":
            continue
        if topic not in known:
            known.add(topic)
            async_add_entities([SolarAssistantSelect(coordinator, entry, topic, defn)])


class SolarAssistantSelect(SolarAssistantMetricEntity, SelectEntity):
    def __init__(
        self,
        coordinator: SolarAssistantCoordinator,
        entry: ConfigEntry,
        topic: str,
        defn: dict[str, Any],
    ) -> None:
        super().__init__(coordinator, entry, topic, defn)
        self._attr_options = list(defn.get("options") or [])

    @property
    def current_option(self) -> str | None:
        v = self._coordinator.data.get(self._topic)
        return None if v is None else str(v)

    async def async_select_option(self, option: str) -> None:
        await self._coordinator.set_setting(self._topic, option)
