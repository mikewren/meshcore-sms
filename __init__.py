"""MeshCore SMS Gateway Integration."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

DOMAIN = "meshcore_sms"

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = []


async def async_setup(hass: HomeAssistant, config: dict):
    """Set up the MeshCore SMS component."""
    # Make sure the domain is registered
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up MeshCore SMS Gateway from a config entry."""
    
    # Check if MeshCore integration is configured
    meshcore_entries = hass.config_entries.async_entries("meshcore")
    if not meshcore_entries:
        _LOGGER.error("MeshCore integration is not configured. Please set it up first.")
        return False
    
    # Check if MeshCore integration is loaded in hass.data
    if "meshcore" not in hass.data:
        # MeshCore is configured but not loaded yet, wait for it
        _LOGGER.info("Waiting for MeshCore integration to load...")
        raise ConfigEntryNotReady("Waiting for MeshCore integration")
    
    # Get MeshCore entry
    meshcore_entries = hass.config_entries.async_entries("meshcore")
    if not meshcore_entries:
        _LOGGER.error("MeshCore integration was removed. SMS Gateway cannot function.")
        return False
    
    meshcore_entry = meshcore_entries[0]
    
    # Check if MeshCore is ready
    meshcore_data = hass.data["meshcore"].get(meshcore_entry.entry_id)
    if not meshcore_data or not meshcore_data.get("connected", False):
        _LOGGER.warning("MeshCore is not connected. SMS Gateway will start when MeshCore is ready.")
        # We'll still set up but functionality will be limited
    
    # Store basic config for now
    hass.data[DOMAIN][entry.entry_id] = {
        "config": entry.data,
        "meshcore_entry_id": meshcore_entry.entry_id,
        "bot_name": entry.data.get("bot_name", "sms_bot"),
    }
    
    # Register bot with MeshCore if possible
    await _register_bot_with_meshcore(hass, entry)
    
    _LOGGER.info(
        "MeshCore SMS Gateway initialized for %s with bot name '%s'",
        entry.data.get("from_number"),
        entry.data.get("bot_name")
    )
    
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    
    # Unregister bot from MeshCore
    await _unregister_bot_from_meshcore(hass, entry)
    
    # Remove our data
    if entry.entry_id in hass.data[DOMAIN]:
        hass.data[DOMAIN].pop(entry.entry_id)
    
    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)


async def _register_bot_with_meshcore(hass: HomeAssistant, entry: ConfigEntry):
    """Register our bot with MeshCore."""
    try:
        bot_name = entry.data.get("bot_name", "sms_bot")
        
        # Check if MeshCore has a registration service
        if hass.services.has_service("meshcore", "register_bot"):
            await hass.services.async_call(
                "meshcore",
                "register_bot",
                {
                    "name": bot_name,
                    "type": "sms_gateway",
                    "handler": DOMAIN,
                    "description": f"SMS Gateway ({entry.data.get('from_number')})",
                },
                blocking=True
            )
            _LOGGER.info("Registered bot '%s' with MeshCore", bot_name)
            
    except Exception as e:
        _LOGGER.warning("Could not register bot with MeshCore: %s", e)


async def _unregister_bot_from_meshcore(hass: HomeAssistant, entry: ConfigEntry):
    """Unregister our bot from MeshCore."""
    try:
        bot_name = entry.data.get("bot_name", "sms_bot")
        
        # Check if MeshCore has an unregistration service
        if hass.services.has_service("meshcore", "unregister_bot"):
            await hass.services.async_call(
                "meshcore",
                "unregister_bot",
                {"name": bot_name},
                blocking=True
            )
            _LOGGER.info("Unregistered bot '%s' from MeshCore", bot_name)
            
    except Exception as e:
        _LOGGER.debug("Could not unregister bot from MeshCore: %s", e)