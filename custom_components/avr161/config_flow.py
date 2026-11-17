"""Ajout de l'ampli depuis l'interface (Paramètres > Appareils et services)."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import selector

from .client import AvrClient, AvrError
from .const import (
    CONF_MAX_VOLUME_PERCENT,
    DEFAULT_MAX_VOLUME_PERCENT,
    DEFAULT_NAME,
    DOMAIN,
    max_volume_percent,
)

_MAX_VOLUME = vol.All(
    selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=1, max=100, step=1, unit_of_measurement="%",
            mode=selector.NumberSelectorMode.SLIDER,
        )
    ),
    vol.Coerce(int),
)


class Avr161ConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            await self.async_set_unique_id(host)
            self._abort_if_unique_id_configured()
            client = AvrClient(host)
            try:
                await client.status()
            except AvrError as err:
                # Joignable mais muet sur get-status = firmware d'origine.
                errors["base"] = (
                    "no_custom_firmware" if "get-status" in str(err) else "cannot_connect"
                )
            finally:
                await client.close()
            if not errors:
                return self.async_create_entry(
                    title=user_input[CONF_NAME],
                    data={CONF_HOST: host, CONF_NAME: user_input[CONF_NAME]},
                    options={CONF_MAX_VOLUME_PERCENT: user_input[CONF_MAX_VOLUME_PERCENT]},
                )
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
                vol.Required(
                    CONF_MAX_VOLUME_PERCENT, default=DEFAULT_MAX_VOLUME_PERCENT
                ): _MAX_VOLUME,
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or {}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return Avr161OptionsFlow()


class Avr161OptionsFlow(OptionsFlow):
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        current = max_volume_percent(self.config_entry.options)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {vol.Required(CONF_MAX_VOLUME_PERCENT, default=current): _MAX_VOLUME}
            ),
        )
