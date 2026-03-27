"""DataUpdateCoordinator for Prana Recuperator."""
from __future__ import annotations

import asyncio
from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import PranaApiClient, PranaApiError, PranaConnectionError, PranaState
from .const import (
    DOMAIN,
    FAN_TYPE_BOUNDED,
    FAN_TYPE_EXTRACT,
    FAN_TYPE_SUPPLY,
    MIN_SPEED,
    SWITCH_TYPE_AUTO,
    SWITCH_TYPE_AUTO_PLUS,
    SWITCH_TYPE_BOOST,
    SWITCH_TYPE_NIGHT,
)

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=15)
POST_COMMAND_DELAY = 1.2
MAX_RETRIES = 3
RETRY_DELAY = 1.0
PRESET_SWITCHES = (
    SWITCH_TYPE_BOOST,
    SWITCH_TYPE_AUTO,
    SWITCH_TYPE_AUTO_PLUS,
    SWITCH_TYPE_NIGHT,
)


class PranaCoordinator(DataUpdateCoordinator[PranaState]):
    """Prana data update coordinator."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        api: PranaApiClient,
        device_name: str,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} ({device_name})",
            update_interval=SCAN_INTERVAL,
        )
        self.api = api
        self.device_name = device_name
        self._command_lock = asyncio.Lock()
        self._preset_override: str | None = None
        self._suppress_night = False

    async def _async_update_data(self) -> PranaState:
        """Fetch data from API."""
        try:
            state = await self.api.get_state()
            if self._suppress_night and not state.night:
                self._suppress_night = False
            return state
        except PranaConnectionError as err:
            raise UpdateFailed(f"Error communicating with device: {err}") from err
        except PranaApiError as err:
            raise UpdateFailed(f"Error fetching data: {err}") from err

    async def _execute_command_with_retry(self, command_func, *args, **kwargs) -> None:
        """Execute a command with retry logic."""
        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                await command_func(*args, **kwargs)
                return
            except PranaApiError as err:
                last_error = err
                _LOGGER.warning(
                    "Command failed (attempt %d/%d): %s",
                    attempt + 1,
                    MAX_RETRIES,
                    err,
                )
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_DELAY)
        raise last_error

    def _push_state(self) -> None:
        """Push optimistic state updates to Home Assistant."""
        self.async_update_listeners()

    def _apply_speed_locally(self, speed: int, fan_type: str) -> None:
        """Optimistically update the local state for speed changes."""
        if self.data is None:
            return
        if fan_type == FAN_TYPE_SUPPLY:
            self.data.supply_speed = speed
            self.data.supply_is_on = speed > 0
        elif fan_type == FAN_TYPE_EXTRACT:
            self.data.extract_speed = speed
            self.data.extract_is_on = speed > 0
        else:
            self.data.bounded_speed = speed
            self.data.bounded_is_on = speed > 0
            self.data.bound = True

    def _apply_fan_power_locally(self, value: bool, fan_type: str) -> None:
        """Optimistically update the local state for fan power changes."""
        if self.data is None:
            return
        if fan_type == FAN_TYPE_SUPPLY:
            self.data.supply_is_on = value
            if not value:
                self.data.supply_speed = 0
        elif fan_type == FAN_TYPE_EXTRACT:
            self.data.extract_is_on = value
            if not value:
                self.data.extract_speed = 0
        else:
            self.data.bounded_is_on = value
            if not value:
                self.data.bounded_speed = 0

    def _apply_switch_locally(self, switch_type: str, value: bool) -> None:
        """Optimistically update the local state for switches."""
        if self.data is None:
            return
        if hasattr(self.data, switch_type):
            setattr(self.data, switch_type, value)

    def _clear_presets_locally(self) -> None:
        """Clear mutually-exclusive presets locally."""
        if self.data is None:
            return
        self.data.boost = False
        self.data.auto = False
        self.data.auto_plus = False
        self.data.night = False

    async def _refresh_after_command(self) -> None:
        """Refresh data after a command with a small delay."""
        await asyncio.sleep(POST_COMMAND_DELAY)
        await self.async_refresh()

    def get_effective_preset_mode(self) -> str | None:
        """Return the preset mode to expose in the UI.

        The local API sometimes reports `night=true` together with `auto`, or when the
        bounded speed is set to level 1. To keep the UI aligned with Prana WiFi, night is
        masked unless it was explicitly requested and no higher-priority preset is active.
        """
        if self._preset_override in PRESET_SWITCHES:
            return self._preset_override
        if self.data is None:
            return None
        if self.data.boost:
            return SWITCH_TYPE_BOOST
        if self.data.auto:
            return SWITCH_TYPE_AUTO
        if self.data.auto_plus:
            return SWITCH_TYPE_AUTO_PLUS
        if self.data.night and not self._suppress_night:
            return SWITCH_TYPE_NIGHT
        return None

    def is_effective_switch_on(self, switch_type: str) -> bool:
        """Return the effective state for a switch shown in the UI."""
        if self.data is None:
            return False
        if switch_type in PRESET_SWITCHES:
            return self.get_effective_preset_mode() == switch_type
        return bool(getattr(self.data, switch_type, False))

    async def async_set_speed(self, speed: int, fan_type: str) -> None:
        """Set fan speed and refresh data."""
        async with self._command_lock:
            try:
                current_state = await self.api.get_state()
                _LOGGER.debug(
                    "Setting %s speed to %d (current: %s)",
                    fan_type,
                    speed,
                    current_state.raw_data,
                )
                await self._execute_command_with_retry(self.api.set_speed, speed, fan_type)
                self._apply_speed_locally(speed, fan_type)
                if fan_type == FAN_TYPE_BOUNDED:
                    self._preset_override = None
                    self._suppress_night = True
                    self._clear_presets_locally()
                self._push_state()
                await self._refresh_after_command()
            except PranaApiError as err:
                _LOGGER.error("Failed to set speed: %s", err)
                await self.async_refresh()
                raise

    async def async_set_fan_on(self, value: bool, fan_type: str) -> None:
        """Turn fan on/off and refresh data."""
        async with self._command_lock:
            try:
                current_state = await self.api.get_state()
                _LOGGER.debug(
                    "Setting %s fan to %s (current is_on: %s)",
                    fan_type,
                    value,
                    current_state.is_fan_on(fan_type),
                )
                await self._execute_command_with_retry(self.api.set_speed_is_on, value, fan_type)
                self._apply_fan_power_locally(value, fan_type)
                if fan_type == FAN_TYPE_BOUNDED and not value:
                    self._preset_override = None
                    self._suppress_night = True
                    self._clear_presets_locally()
                self._push_state()
                await self._refresh_after_command()
            except PranaApiError as err:
                _LOGGER.error("Failed to set fan state: %s", err)
                await self.async_refresh()
                raise

    async def async_set_switch(self, switch_type: str, value: bool) -> None:
        """Set switch state and refresh data."""
        async with self._command_lock:
            try:
                current_state = await self.api.get_state()
                _LOGGER.debug(
                    "Setting switch %s to %s (current state: %s)",
                    switch_type,
                    value,
                    getattr(current_state, switch_type, None),
                )
                await self._execute_command_with_retry(self.api.set_switch, switch_type, value)
                self._apply_switch_locally(switch_type, value)
                if switch_type in PRESET_SWITCHES:
                    if value:
                        self._clear_presets_locally()
                        self._apply_switch_locally(switch_type, True)
                        self._preset_override = switch_type
                        self._suppress_night = False
                    elif self._preset_override == switch_type:
                        self._preset_override = None
                        if switch_type == SWITCH_TYPE_NIGHT:
                            self._suppress_night = True
                self._push_state()
                await self._refresh_after_command()
            except PranaApiError as err:
                _LOGGER.error("Failed to set switch: %s", err)
                await self.async_refresh()
                raise

    async def async_set_brightness(self, brightness: int) -> None:
        """Set brightness and refresh data."""
        async with self._command_lock:
            try:
                current_state = await self.api.get_state()
                _LOGGER.debug(
                    "Setting brightness to %d (current: %d)",
                    brightness,
                    current_state.brightness,
                )
                await self._execute_command_with_retry(self.api.set_brightness, brightness)
                if self.data is not None:
                    self.data.brightness = brightness
                    self._push_state()
                await self._refresh_after_command()
            except PranaApiError as err:
                _LOGGER.error("Failed to set brightness: %s", err)
                await self.async_refresh()
                raise

    async def async_turn_unit_on(self) -> None:
        """Turn the virtual unit on using the bounded fan."""
        async with self._command_lock:
            try:
                current_state = await self.api.get_state()
                if current_state.bounded_speed <= 0:
                    await self._execute_command_with_retry(
                        self.api.set_speed,
                        MIN_SPEED,
                        FAN_TYPE_BOUNDED,
                    )
                    self._apply_speed_locally(MIN_SPEED, FAN_TYPE_BOUNDED)
                if not current_state.bounded_is_on:
                    await self._execute_command_with_retry(
                        self.api.set_speed_is_on,
                        True,
                        FAN_TYPE_BOUNDED,
                    )
                    self._apply_fan_power_locally(True, FAN_TYPE_BOUNDED)
                self._push_state()
                await self._refresh_after_command()
            except PranaApiError as err:
                _LOGGER.error("Failed to turn unit on: %s", err)
                await self.async_refresh()
                raise

    async def async_turn_unit_off(self) -> None:
        """Turn the virtual unit off using the bounded fan."""
        async with self._command_lock:
            try:
                current_state = await self.api.get_state()
                if current_state.bounded_is_on:
                    await self._execute_command_with_retry(
                        self.api.set_speed_is_on,
                        False,
                        FAN_TYPE_BOUNDED,
                    )
                self._apply_fan_power_locally(False, FAN_TYPE_BOUNDED)
                self._preset_override = None
                self._suppress_night = True
                self._clear_presets_locally()
                self._push_state()
                await self._refresh_after_command()
            except PranaApiError as err:
                _LOGGER.error("Failed to turn unit off: %s", err)
                await self.async_refresh()
                raise

    async def async_set_preset_mode(self, preset_mode: str | None) -> None:
        """Set the active preset mode.

        Only one of boost/auto/auto_plus/night is kept active at a time to mimic
        the Prana WiFi climate card behavior.
        """
        if preset_mode is not None and preset_mode not in PRESET_SWITCHES:
            raise ValueError(f"Invalid preset mode: {preset_mode}")

        async with self._command_lock:
            try:
                current_state = await self.api.get_state()

                changes_required = False
                for switch_name in PRESET_SWITCHES:
                    current_value = getattr(current_state, switch_name)
                    target_value = switch_name == preset_mode if preset_mode is not None else False
                    if current_value != target_value:
                        await self._execute_command_with_retry(
                            self.api.set_switch,
                            switch_name,
                            target_value,
                        )
                        changes_required = True

                if preset_mode is not None:
                    if current_state.bounded_speed <= 0:
                        await self._execute_command_with_retry(
                            self.api.set_speed,
                            MIN_SPEED,
                            FAN_TYPE_BOUNDED,
                        )
                        self._apply_speed_locally(MIN_SPEED, FAN_TYPE_BOUNDED)
                        changes_required = True
                    if not current_state.bounded_is_on:
                        await self._execute_command_with_retry(
                            self.api.set_speed_is_on,
                            True,
                            FAN_TYPE_BOUNDED,
                        )
                        self._apply_fan_power_locally(True, FAN_TYPE_BOUNDED)
                        changes_required = True

                self._clear_presets_locally()
                if preset_mode is not None:
                    self._apply_switch_locally(preset_mode, True)
                self._preset_override = preset_mode
                self._suppress_night = preset_mode != SWITCH_TYPE_NIGHT
                self._push_state()

                if changes_required:
                    await self._refresh_after_command()
                else:
                    await self.async_refresh()
            except PranaApiError as err:
                _LOGGER.error("Failed to set preset mode: %s", err)
                await self.async_refresh()
                raise

    async def async_force_refresh(self) -> None:
        """Force an immediate refresh of the data."""
        async with self._command_lock:
            await self.async_refresh()
