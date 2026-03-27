"""Climate platform for Prana Recuperator."""
from __future__ import annotations

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SWITCH_TYPE_AUTO, SWITCH_TYPE_AUTO_PLUS, SWITCH_TYPE_BOOST, SWITCH_TYPE_NIGHT
from .coordinator import PranaCoordinator
from .entity import PranaEntity

PRESET_MODES = [
    SWITCH_TYPE_BOOST,
    SWITCH_TYPE_AUTO,
    SWITCH_TYPE_AUTO_PLUS,
    SWITCH_TYPE_NIGHT,
]
FAN_MODES = ["0", "1", "2", "3", "4", "5", "6"]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Prana climate entity from a config entry."""
    coordinator: PranaCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([PranaClimate(coordinator, entry.entry_id)])


class PranaClimate(PranaEntity, ClimateEntity):
    """Virtual climate entity mirroring the Prana WiFi card."""

    _attr_name = None
    _attr_translation_key = "prana_climate"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.FAN_ONLY]
    _attr_fan_modes = FAN_MODES
    _attr_preset_modes = PRESET_MODES
    _attr_supported_features = (
        ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_OFF
        | ClimateEntityFeature.TURN_ON
    )
    _enable_turn_on_off_backwards_compat = False

    def __init__(self, coordinator: PranaCoordinator, entry_id: str) -> None:
        """Initialize the climate entity."""
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{coordinator.api.host}_climate"

    @property
    def current_temperature(self) -> float | None:
        """Return the current inside temperature."""
        if self.coordinator.data is None:
            return None
        return (
            self.coordinator.data.inside_temperature
            if self.coordinator.data.inside_temperature is not None
            else self.coordinator.data.inside_temperature_2
        )

    @property
    def current_humidity(self) -> float | None:
        """Return the current humidity."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.humidity

    @property
    def hvac_mode(self) -> HVACMode:
        """Return the current HVAC mode."""
        if self.coordinator.data is None or not self.coordinator.data.bounded_is_on:
            return HVACMode.OFF
        return HVACMode.FAN_ONLY

    @property
    def hvac_action(self) -> HVACAction:
        """Return the current HVAC action."""
        if self.coordinator.data is None or not self.coordinator.data.bounded_is_on:
            return HVACAction.OFF
        if self.coordinator.data.winter:
            return HVACAction.DEFROSTING
        if self.coordinator.data.heater:
            return HVACAction.PREHEATING
        return HVACAction.FAN

    @property
    def fan_mode(self) -> str | None:
        """Return the current bounded fan speed."""
        if self.coordinator.data is None:
            return None
        speed = self.coordinator.data.bounded_speed // 10 if self.coordinator.data.bounded_speed else 0
        return str(speed)

    @property
    def preset_mode(self) -> str | None:
        """Return the active preset mode."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.get_effective_preset_mode()

    @property
    def extra_state_attributes(self) -> dict[str, bool | int | None]:
        """Expose extra device state on the climate entity."""
        if self.coordinator.data is None:
            return {}
        return {
            "heater": self.coordinator.data.heater,
            "winter": self.coordinator.data.winter,
            "bound": self.coordinator.data.bound,
            "supply_speed": self.coordinator.data.supply_speed,
            "extract_speed": self.coordinator.data.extract_speed,
            "bounded_speed": self.coordinator.data.bounded_speed,
            "co2": self.coordinator.data.co2,
            "voc": self.coordinator.data.voc,
        }

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode."""
        if hvac_mode == HVACMode.OFF:
            await self.coordinator.async_turn_unit_off()
        elif hvac_mode == HVACMode.FAN_ONLY:
            await self.coordinator.async_turn_unit_on()

    async def async_turn_on(self) -> None:
        """Turn on the climate entity."""
        await self.coordinator.async_turn_unit_on()

    async def async_turn_off(self) -> None:
        """Turn off the climate entity."""
        await self.coordinator.async_turn_unit_off()

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set the bounded fan speed."""
        speed_level = int(fan_mode)
        if speed_level <= 0:
            await self.coordinator.async_turn_unit_off()
            return
        await self.coordinator.async_set_speed(speed_level * 10, "bounded")
        if self.coordinator.data is None or not self.coordinator.data.bounded_is_on:
            await self.coordinator.async_turn_unit_on()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set preset mode."""
        await self.coordinator.async_set_preset_mode(preset_mode)
