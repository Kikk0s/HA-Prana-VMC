"""Switch platform for Prana Recuperator."""
from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Callable

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import PranaState
from .const import (
    DOMAIN,
    SWITCH_TYPE_AUTO,
    SWITCH_TYPE_AUTO_PLUS,
    SWITCH_TYPE_BOOST,
    SWITCH_TYPE_BOUND,
    SWITCH_TYPE_HEATER,
    SWITCH_TYPE_NIGHT,
    SWITCH_TYPE_WINTER,
)
from .coordinator import PranaCoordinator
from .entity import PranaEntity

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class PranaSwitchEntityDescription(SwitchEntityDescription):
    """Describes a Prana switch entity."""

    switch_type: str
    value_fn: Callable[[PranaState], bool]


SWITCH_DESCRIPTIONS: tuple[PranaSwitchEntityDescription, ...] = (
    PranaSwitchEntityDescription(
        key="bound",
        translation_key="bound",
        name="Bound Mode",
        icon="mdi:link",
        switch_type=SWITCH_TYPE_BOUND,
        value_fn=lambda state: state.bound,
    ),
    PranaSwitchEntityDescription(
        key="heater",
        translation_key="heater",
        name="Heater",
        icon="mdi:radiator",
        switch_type=SWITCH_TYPE_HEATER,
        value_fn=lambda state: state.heater,
    ),
    PranaSwitchEntityDescription(
        key="winter",
        translation_key="winter",
        name="Winter Mode",
        icon="mdi:snowflake",
        switch_type=SWITCH_TYPE_WINTER,
        value_fn=lambda state: state.winter,
    ),
    PranaSwitchEntityDescription(
        key="auto",
        translation_key="auto",
        name="Auto Mode",
        icon="mdi:auto-fix",
        switch_type=SWITCH_TYPE_AUTO,
        value_fn=lambda state: state.auto,
    ),
    PranaSwitchEntityDescription(
        key="auto_plus",
        translation_key="auto_plus",
        name="Auto+ Mode",
        icon="mdi:auto-mode",
        switch_type=SWITCH_TYPE_AUTO_PLUS,
        value_fn=lambda state: state.auto_plus,
    ),
    PranaSwitchEntityDescription(
        key="night",
        translation_key="night",
        name="Night Mode",
        icon="mdi:weather-night",
        switch_type=SWITCH_TYPE_NIGHT,
        value_fn=lambda state: state.night,
    ),
    PranaSwitchEntityDescription(
        key="boost",
        translation_key="boost",
        name="Boost Mode",
        icon="mdi:rocket-launch",
        switch_type=SWITCH_TYPE_BOOST,
        value_fn=lambda state: state.boost,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Prana switches from config entry."""
    coordinator: PranaCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SwitchEntity] = [PranaPowerSwitch(coordinator, entry.entry_id)]
    entities.extend(
        PranaSwitch(coordinator, entry.entry_id, description)
        for description in SWITCH_DESCRIPTIONS
    )

    async_add_entities(entities)


class PranaPowerSwitch(PranaEntity, SwitchEntity):
    """Virtual power switch to match the Prana WiFi integration."""

    _attr_translation_key = "power_on"

    def __init__(self, coordinator: PranaCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{coordinator.api.host}_power_on"
        self._attr_icon = "mdi:power"

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.bounded_is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_turn_unit_on()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_turn_unit_off()


class PranaSwitch(PranaEntity, SwitchEntity):
    """Representation of a Prana switch."""

    entity_description: PranaSwitchEntityDescription

    def __init__(
        self,
        coordinator: PranaCoordinator,
        entry_id: str,
        description: PranaSwitchEntityDescription,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, entry_id)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.api.host}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        """Return true if the switch is on."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.is_effective_switch_on(self.entity_description.switch_type) if self.entity_description.switch_type in (SWITCH_TYPE_AUTO, SWITCH_TYPE_AUTO_PLUS, SWITCH_TYPE_BOOST, SWITCH_TYPE_NIGHT) else self.entity_description.value_fn(self.coordinator.data)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        if self.coordinator.data and self.entity_description.value_fn(self.coordinator.data):
            _LOGGER.debug(
                "Switch %s already on, skipping update",
                self.entity_description.switch_type,
            )
            return
        await self.coordinator.async_set_switch(self.entity_description.switch_type, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        if self.coordinator.data and not self.entity_description.value_fn(self.coordinator.data):
            _LOGGER.debug(
                "Switch %s already off, skipping update",
                self.entity_description.switch_type,
            )
            return
        await self.coordinator.async_set_switch(self.entity_description.switch_type, False)
