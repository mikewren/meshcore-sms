import logging
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from typing import Any, Dict, Optional

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

class MeshCoreSMSConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for MeshCore SMS."""

    VERSION = 1

    def __init__(self):
        """Initialize the config flow."""
        self._errors = {}
        self._user_input = {}

    async def async_step_user(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Handle the initial step - Twilio credentials."""
        if user_input is not None:
            try:
                # Validate the Twilio credentials
                await self._validate_twilio_input(user_input)
                
                # Store the user input and move to next step
                self._user_input = user_input
                return await self.async_step_gateway_settings()
                
            except Exception as exception:
                _LOGGER.error("Unexpected exception: %s", exception)
                self._errors["base"] = "unknown"

        # Build the schema for Twilio credentials only
        data_schema = vol.Schema({
            vol.Required("account_sid"): str,
            vol.Required("auth_token"): str,
            vol.Required("from_number"): str,
        })

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=self._errors,
            description_placeholders={
                "twilio_info": "Enter your Twilio account credentials"
            }
        )

    async def async_step_gateway_settings(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Handle the gateway settings step."""
        if user_input is not None:
            try:
                # Validate gateway settings
                await self._validate_gateway_input(user_input)
                
                # Combine both steps' data
                combined_data = {**self._user_input, **user_input}
                # Always use channel 0 - not exposed to user
                combined_data["meshcore_channel"] = "0"
                
                # Create the config entry
                return self.async_create_entry(
                    title=f"SMS Gateway ({combined_data['from_number']})",
                    data=combined_data,
                )
            except Exception as exception:
                _LOGGER.error("Unexpected exception: %s", exception)
                self._errors["base"] = "unknown"

        # Build the schema for gateway settings
        data_schema = vol.Schema({
            vol.Optional("bot_name", default="SMS Bot"): str,
            vol.Optional("daily_limit", default=100): vol.Coerce(int),
            vol.Optional("enable_broadcast", default=True): bool,
            vol.Optional("delivery_confirmation", default=False): bool,
        })

        return self.async_show_form(
            step_id="gateway_settings",
            data_schema=data_schema,
            errors=self._errors,
            description_placeholders={
                "phone_number": self._user_input.get("from_number", ""),
                "settings_info": "Configure gateway settings for your SMS integration"
            }
        )

    async def _validate_twilio_input(self, user_input: Dict[str, Any]) -> None:
        """Validate the Twilio credentials."""
        errors = {}

        # Validate Twilio credentials format
        account_sid = user_input.get("account_sid", "")
        if not account_sid.startswith("AC") or len(account_sid) != 34:
            errors["account_sid"] = "invalid_account_sid"

        # Validate phone number format
        from_number = user_input.get("from_number", "")
        if not from_number.startswith("+") or len(from_number) < 10:
            errors["from_number"] = "invalid_phone_number"

        # Validate auth token
        auth_token = user_input.get("auth_token", "")
        if len(auth_token) != 32:
            errors["auth_token"] = "invalid_auth_token"

        if errors:
            self._errors.update(errors)
            raise InvalidConfigError(errors)

    async def _validate_gateway_input(self, user_input: Dict[str, Any]) -> None:
        """Validate the gateway settings."""
        errors = {}

        # Validate daily limit
        daily_limit = user_input.get("daily_limit", 100)
        if daily_limit < 1 or daily_limit > 1000:
            errors["daily_limit"] = "invalid_daily_limit"

        # Validate bot name
        bot_name = user_input.get("bot_name", "").strip()
        if len(bot_name) < 1 or len(bot_name) > 50:
            errors["bot_name"] = "invalid_bot_name"

        if errors:
            self._errors.update(errors)
            raise InvalidConfigError(errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return MeshCoreSMSOptionsFlow(config_entry)


class MeshCoreSMSOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for MeshCore SMS."""

    def __init__(self, config_entry):
        """Initialize options flow."""
        self.config_entry = config_entry
        self._errors = {}

    async def async_step_init(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            try:
                # Validate the options
                await self._validate_options(user_input)
                return self.async_create_entry(title="", data=user_input)
            except Exception as exception:
                _LOGGER.error("Error updating options: %s", exception)
                self._errors["base"] = "unknown"

        options_schema = vol.Schema({
            vol.Optional(
                "bot_name", 
                default=self.config_entry.options.get(
                    "bot_name", 
                    self.config_entry.data.get("bot_name", "SMS Bot")
                )
            ): str,
            vol.Optional(
                "daily_limit", 
                default=self.config_entry.options.get(
                    "daily_limit", 
                    self.config_entry.data.get("daily_limit", 100)
                )
            ): vol.Coerce(int),
            vol.Optional(
                "enable_broadcast", 
                default=self.config_entry.options.get(
                    "enable_broadcast", 
                    self.config_entry.data.get("enable_broadcast", True)
                )
            ): bool,
            vol.Optional(
                "delivery_confirmation", 
                default=self.config_entry.options.get(
                    "delivery_confirmation", 
                    self.config_entry.data.get("delivery_confirmation", False)
                )
            ): bool,
        })

        return self.async_show_form(
            step_id="init",
            data_schema=options_schema,
            errors=self._errors,
            description_placeholders={
                "phone_number": self.config_entry.data.get("from_number", ""),
                "options_info": "Update gateway settings (Channel 0 - Public)"
            }
        )

    async def _validate_options(self, user_input: Dict[str, Any]) -> None:
        """Validate the options."""
        errors = {}

        # Validate daily limit
        daily_limit = user_input.get("daily_limit", 100)
        if daily_limit < 1 or daily_limit > 1000:
            errors["daily_limit"] = "invalid_daily_limit"

        # Validate bot name
        bot_name = user_input.get("bot_name", "").strip()
        if len(bot_name) < 1 or len(bot_name) > 50:
            errors["bot_name"] = "invalid_bot_name"

        if errors:
            self._errors.update(errors)
            raise InvalidConfigError(errors)


class InvalidConfigError(HomeAssistantError):
    """Error to indicate there is invalid config."""