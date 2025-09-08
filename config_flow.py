"""Config flow for MeshCore SMS Gateway - MeshCore aware version."""

import logging
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.exceptions import HomeAssistantError

DOMAIN = "meshcore_sms"

_LOGGER = logging.getLogger(__name__)

# Constants for configuration
CONF_ACCOUNT_SID = "account_sid"
CONF_AUTH_TOKEN = "auth_token"
CONF_FROM_NUMBER = "from_number"
CONF_BOT_NAME = "bot_name"
CONF_DAILY_LIMIT = "daily_limit"
CONF_ENABLE_BROADCAST = "enable_broadcast"
CONF_DELIVERY_CONFIRMATION = "delivery_confirmation"

# Defaults
DEFAULT_DAILY_LIMIT = 50
DEFAULT_ENABLE_BROADCAST = False
DEFAULT_DELIVERY_CONFIRMATION = True


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for MeshCore SMS Gateway."""

    VERSION = 1

    def __init__(self):
        """Initialize config flow."""
        self._data = {}
        self._errors = {}
        self._meshcore_info = {}

    async def async_step_user(self, user_input=None):
        """Handle the initial step - Check MeshCore and get Twilio credentials."""
        errors = {}
        
        # Check if MeshCore integration is configured
        meshcore_entries = self.hass.config_entries.async_entries("meshcore")
        if not meshcore_entries:
            return self.async_abort(reason="meshcore_not_configured")
        
        # Get MeshCore integration data
        if not self._meshcore_info:
            await self._get_meshcore_info()
        
        if user_input is not None:
            # Basic validation
            if not user_input[CONF_ACCOUNT_SID].startswith("AC"):
                errors["base"] = "invalid_auth"
            elif not user_input[CONF_FROM_NUMBER]:
                errors["base"] = "invalid_phone"
            else:
                # Store the data and move to next step
                self._data = user_input
                _LOGGER.info("Twilio credentials validated, moving to gateway settings")
                return await self.async_step_gateway_settings()

        # Show current MeshCore configuration info
        description_placeholders = {
            "meshcore_status": "✅ Connected" if self._meshcore_info.get("connected") else "⚠️ Not connected",
            "meshcore_device": self._meshcore_info.get("device_name", "Unknown"),
        }

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_ACCOUNT_SID): str,
                vol.Required(CONF_AUTH_TOKEN): str,
                vol.Required(CONF_FROM_NUMBER): str,
            }),
            errors=errors,
            description_placeholders=description_placeholders,
        )

    async def async_step_gateway_settings(self, user_input=None):
        """Configure gateway settings."""
        if user_input is not None:
            # Combine all data
            self._data.update(user_input)
            
            # Create unique ID based on phone number
            await self.async_set_unique_id(
                f"{DOMAIN}_{self._data[CONF_FROM_NUMBER]}"
            )
            self._abort_if_unique_id_configured()
            
            # Create the entry
            return self.async_create_entry(
                title=f"SMS Gateway ({self._data[CONF_FROM_NUMBER]})",
                data=self._data,
            )

        # Get bot name suggestions from MeshCore
        bot_suggestions = await self._get_bot_name_suggestions()
        
        # Create schema with bot name selector if we have suggestions
        if bot_suggestions:
            # If MeshCore provides available bot names, let user select
            data_schema = vol.Schema({
                vol.Required(CONF_BOT_NAME): vol.In(bot_suggestions),
                vol.Optional(
                    CONF_DAILY_LIMIT, default=DEFAULT_DAILY_LIMIT
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=1000)),
                vol.Optional(
                    CONF_ENABLE_BROADCAST, default=DEFAULT_ENABLE_BROADCAST
                ): cv.boolean,
                vol.Optional(
                    CONF_DELIVERY_CONFIRMATION, 
                    default=DEFAULT_DELIVERY_CONFIRMATION
                ): cv.boolean,
            })
        else:
            # Fallback to text input with suggestion
            suggested_name = await self._suggest_bot_name()
            data_schema = vol.Schema({
                vol.Required(CONF_BOT_NAME, default=suggested_name): str,
                vol.Optional(
                    CONF_DAILY_LIMIT, default=DEFAULT_DAILY_LIMIT
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=1000)),
                vol.Optional(
                    CONF_ENABLE_BROADCAST, default=DEFAULT_ENABLE_BROADCAST
                ): cv.boolean,
                vol.Optional(
                    CONF_DELIVERY_CONFIRMATION, 
                    default=DEFAULT_DELIVERY_CONFIRMATION
                ): cv.boolean,
            })

        return self.async_show_form(
            step_id="gateway_settings",
            data_schema=data_schema,
            description_placeholders={
                "phone_number": self._data[CONF_FROM_NUMBER],
                "meshcore_info": f"MeshCore Device: {self._meshcore_info.get('device_name', 'Unknown')}",
            }
        )

    async def _get_meshcore_info(self):
        """Get information from MeshCore integration."""
        try:
            # Try to get MeshCore integration data
            meshcore_entries = self.hass.config_entries.async_entries("meshcore")
            if meshcore_entries:
                meshcore_entry = meshcore_entries[0]
                
                # Get device info from MeshCore
                if "meshcore" in self.hass.data:
                    meshcore_data = self.hass.data["meshcore"].get(meshcore_entry.entry_id, {})
                    
                    # Extract useful information
                    self._meshcore_info = {
                        "connected": meshcore_data.get("connected", False),
                        "device_name": meshcore_data.get("device_name") or meshcore_entry.data.get("device_name", "MeshCore Device"),
                        "node_id": meshcore_data.get("node_id") or meshcore_entry.data.get("node_id"),
                        "entry_id": meshcore_entry.entry_id,
                    }
                else:
                    # MeshCore integration exists but no runtime data yet
                    self._meshcore_info = {
                        "connected": False,
                        "device_name": meshcore_entry.data.get("device_name", "MeshCore Device"),
                        "node_id": meshcore_entry.data.get("node_id"),
                        "entry_id": meshcore_entry.entry_id,
                    }
                    
                _LOGGER.debug("MeshCore info: %s", self._meshcore_info)
                
        except Exception as e:
            _LOGGER.warning("Could not get MeshCore info: %s", e)
            self._meshcore_info = {"connected": False}

    async def _get_bot_name_suggestions(self):
        """Get available bot names from MeshCore."""
        try:
            # Check if MeshCore has a service to list available bot names
            if self.hass.services.has_service("meshcore", "list_bot_names"):
                result = await self.hass.services.async_call(
                    "meshcore", 
                    "list_bot_names",
                    blocking=True,
                    return_response=True
                )
                return result.get("bot_names", [])
            
            # Check if MeshCore stores bot configuration
            if "meshcore" in self.hass.data:
                meshcore_entries = self.hass.config_entries.async_entries("meshcore")
                if meshcore_entries:
                    meshcore_data = self.hass.data["meshcore"].get(meshcore_entries[0].entry_id, {})
                    configured_bots = meshcore_data.get("configured_bots", [])
                    if configured_bots:
                        return configured_bots
                        
        except Exception as e:
            _LOGGER.debug("Could not get bot name suggestions: %s", e)
        
        return []

    async def _suggest_bot_name(self):
        """Suggest a bot name based on MeshCore configuration."""
        # Try to generate a unique bot name
        base_name = "sms_bot"
        
        # Check if this name is already in use
        try:
            if self.hass.services.has_service("meshcore", "check_bot_name"):
                counter = 0
                while counter < 10:
                    test_name = base_name if counter == 0 else f"{base_name}_{counter}"
                    result = await self.hass.services.async_call(
                        "meshcore",
                        "check_bot_name",
                        {"name": test_name},
                        blocking=True,
                        return_response=True
                    )
                    if not result.get("exists", True):
                        return test_name
                    counter += 1
        except Exception:
            pass
        
        # Fallback to device-specific name
        if self._meshcore_info.get("node_id"):
            return f"sms_{self._meshcore_info['node_id'][-4:]}"
        
        return base_name

    @staticmethod
    def async_get_options_flow(config_entry):
        """Get options flow."""
        return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow."""

    def __init__(self, config_entry):
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Manage options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        # Don't allow changing bot name in options - would break existing connections
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(
                    CONF_DAILY_LIMIT,
                    default=self.config_entry.data.get(CONF_DAILY_LIMIT, DEFAULT_DAILY_LIMIT),
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=1000)),
                vol.Optional(
                    CONF_ENABLE_BROADCAST,
                    default=self.config_entry.data.get(CONF_ENABLE_BROADCAST, DEFAULT_ENABLE_BROADCAST),
                ): cv.boolean,
                vol.Optional(
                    CONF_DELIVERY_CONFIRMATION,
                    default=self.config_entry.data.get(CONF_DELIVERY_CONFIRMATION, DEFAULT_DELIVERY_CONFIRMATION),
                ): cv.boolean,
            }),
            description_placeholders={
                "current_bot": self.config_entry.data.get(CONF_BOT_NAME, "unknown"),
            }
        )