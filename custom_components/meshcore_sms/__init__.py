"""MeshCore SMS Gateway Integration."""

import logging
from typing import Any, Dict

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DOMAIN
from .gateway import MeshCoreSMSGateway

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = []


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up MeshCore SMS Gateway from a config entry."""
    
    # Check if MeshCore integration is loaded
    if "meshcore" not in hass.config.integrations:
        _LOGGER.error("MeshCore integration is not loaded. Please install and configure it first.")
        raise ConfigEntryNotReady("MeshCore integration is required")
    
    # Initialize the gateway
    gateway = MeshCoreSMSGateway(hass, entry)
    
    # Set up the gateway
    if not await gateway.async_setup():
        return False
    
    # Store gateway instance
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = gateway
    
    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    # Register update listener
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    
    _LOGGER.info(
        "MeshCore SMS Gateway initialized for %s",
        entry.data.get("from_number")
    )
    
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Unload platforms
    if unload_ok := await hass.config_entries.async_unload_platforms(
        entry, PLATFORMS
    ):
        # Get gateway instance
        gateway = hass.data[DOMAIN].pop(entry.entry_id)
        
        # Unload gateway
        await gateway.async_unload()
    
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate old entry."""
    _LOGGER.debug("Migrating from version %s", config_entry.version)
    
    if config_entry.version == 1:
        # Future migrations go here
        pass
    
    _LOGGER.info("Migration to version %s successful", config_entry.version)
    
    return True