"""Shared base for all SolarAssistant entity platforms."""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo, Entity, EntityCategory

from .const import DOMAIN
from .coordinator import SolarAssistantCoordinator


def device_label_and_id(defn: dict[str, Any]) -> tuple[str, str]:
    """Map SA's lowercase device + 0-indexed number → human label + stable id.

    ``("inverters", 0)`` → label ``"Inverters 1"``, id ``"Inverters_1"``.
    ``("totals", None)`` → label ``"Totals"``, id ``"Totals"``.

    Stable id format matches the legacy registry shape so existing device
    entries are preserved across the SA backend's casing/indexing change.
    """
    device = (defn.get("device") or "default").title()
    number = defn.get("number")
    if number is None:
        return device, device
    label_num = number + 1
    return f"{device} {label_num}", f"{device}_{label_num}"


class SolarAssistantMetricEntity(Entity):
    """Common state/wiring for any entity backed by a SolarAssistant metric topic."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SolarAssistantCoordinator,
        entry: ConfigEntry,
        topic: str,
        defn: dict[str, Any],
    ) -> None:
        self._coordinator = coordinator
        self._topic = topic
        # Use entry.unique_id (e.g. "local:192.168.86.29" or "cloud:19489") so that
        # removing and re-adding the same unit reattaches to the existing registry
        # entries instead of creating new orphans. entry.entry_id is a per-creation
        # ULID and would change on every re-add.
        scope = entry.unique_id
        self._attr_unique_id = f"{scope}_{topic}"
        self._attr_name = defn.get("name") or topic
        # Settings are now first-class editable platforms (number/select/switch);
        # only Info stays in the Diagnostic section since those are static metadata.
        if defn.get("group") == "Info":
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

        device_label, device_id = device_label_and_id(defn)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{scope}_{device_id}")},
            name=device_label,
            manufacturer="SolarAssistant",
        )

    @property
    def available(self) -> bool:
        return self._coordinator.is_connected

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self._coordinator.signal_metric_update, self._on_update
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                self._coordinator.signal_connection_state,
                self.async_write_ha_state,
            )
        )

    @callback
    def _on_update(self, topic: str) -> None:
        if topic == self._topic:
            self.async_write_ha_state()
