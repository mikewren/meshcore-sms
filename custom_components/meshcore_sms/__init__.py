from __future__ import annotations
import logging
from aiohttp import web
from datetime import datetime, timezone, timedelta
from collections import deque
from typing import Deque, Callable, Optional
from urllib.parse import parse_qs
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.components import webhook
from homeassistant.exceptions import ConfigEntryNotReady

DOMAIN = "meshcore_sms"
_LOGGER = logging.getLogger(__name__)

class State:
    """Holds all resources that must be cleaned up on unload."""
    
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        self.hass = hass
        self.entry = entry
        self.webhook_id: Optional[str] = None
        self._unsubs: list[Callable[[], None]] = []
        self._services: list[str] = []
        self.command_handler = None
        self.msg_times: Deque[datetime] = deque(maxlen=2000)
        self.tz = timezone.utc
        
    def track(self, unsub: Callable[[], None]) -> None:
        """Track a subscription for later cleanup."""
        self._unsubs.append(unsub)
        
    def track_service(self, service_name: str) -> None:
        """Track a registered service for later cleanup."""
        self._services.append(service_name)
    
    def close(self) -> None:
        """Clean up all tracked resources."""
        _LOGGER.debug(f"Cleaning up state for entry {self.entry.entry_id}")
        
        for unsub in self._unsubs:
            try:
                unsub()
            except Exception as e:
                _LOGGER.warning(f"Error unsubscribing listener: {e}")
        self._unsubs.clear()
        
        if self.webhook_id:
            try:
                webhook.async_unregister(self.hass, self.webhook_id)
                _LOGGER.debug(f"Unregistered webhook: {self.webhook_id}")
            except Exception as e:
                _LOGGER.warning(f"Error unregistering webhook {self.webhook_id}: {e}")
            self.webhook_id = None
            
        remaining_entries = [
            e for e in self.hass.config_entries.async_entries(DOMAIN)
            if e.entry_id != self.entry.entry_id
        ]
        
        if not remaining_entries:
            for service_name in self._services:
                try:
                    if self.hass.services.has_service(DOMAIN, service_name):
                        self.hass.services.async_remove(DOMAIN, service_name)
                        _LOGGER.debug(f"Removed service: {DOMAIN}.{service_name}")
                except Exception as e:
                    _LOGGER.warning(f"Error removing service {service_name}: {e}")
        
        self._services.clear()

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up MeshCore SMS from a config entry."""
    _LOGGER.error("=== PLAIN TEXT VERSION LOADING ===")
    _LOGGER.error("=== NO MORE TWIML - PLAIN TEXT RESPONSES ===")
    
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}
    
    st = State(hass, entry)
    hass.data[DOMAIN][entry.entry_id] = st
    
    try:
        # Track MeshCore events and handle outbound SMS
        @callback
        def on_meshcore_message(event):
            """Handle incoming MeshCore messages - check for SMS routing."""
            st.msg_times.append(datetime.now(st.tz))
            
            # Check if this is a message to route to SMS
            try:
                message_data = event.data
                sender = message_data.get('sender', 'Unknown')
                message_text = message_data.get('message', '')
                
                # Check if message starts with a phone number pattern
                import re
                phone_pattern = r'^\+?1?[0-9]{10,15}\s+'
                phone_match = re.match(phone_pattern, message_text)
                
                if phone_match:
                    # Extract phone number and message
                    phone_number = phone_match.group().strip()
                    sms_message = message_text[len(phone_match.group()):].strip()
                    
                    if sms_message:
                        # Send SMS
                        hass.async_create_task(
                            send_meshcore_to_sms(hass, st, phone_number, sms_message, sender)
                        )
                        
            except Exception as e:
                _LOGGER.error(f"Error processing MeshCore message for SMS routing: {e}")
        
        async def send_meshcore_to_sms(hass, state, phone_number, message, sender):
            """Send SMS from MeshCore message."""
            try:
                # Format the message with sender info
                formatted_message = f"@[{sender}]: {message}"
                
                # Get Twilio config
                config_data = state.entry.data
                account_sid = config_data.get("account_sid", "")
                auth_token = config_data.get("auth_token", "")
                from_number = config_data.get("from_number", "")
                
                # Send SMS using thread pool
                import asyncio
                
                def send_twilio_sms():
                    from twilio.rest import Client as TwilioClient
                    client = TwilioClient(account_sid, auth_token)
                    return client.messages.create(
                        body=formatted_message,
                        from_=from_number,
                        to=phone_number
                    )
                
                loop = asyncio.get_event_loop()
                twilio_message = await loop.run_in_executor(None, send_twilio_sms)
                
                _LOGGER.info(f"MeshCore→SMS sent to {phone_number}: {twilio_message.sid}")
                
            except Exception as e:
                _LOGGER.error(f"Error sending MeshCore→SMS to {phone_number}: {e}")
        
        unsub = hass.bus.async_listen("meshcore_message", on_meshcore_message)
        st.track(unsub)
        
        # Get webhook ID
        webhook_id = entry.options.get("webhook_id") or entry.data.get("webhook_id")
        if not webhook_id:
            webhook_id = f"{DOMAIN}_{entry.entry_id}"
            
        # PLAIN TEXT WEBHOOK HANDLER
        async def handle_sms(hass, webhook_id, request):
            """Handle SMS webhook - PLAIN TEXT RESPONSES."""
            try:
                _LOGGER.error("=== WEBHOOK CALLED - PLAIN TEXT VERSION ===")
                
                # Get form data from Twilio
                form_data = await request.post()
                from_number = form_data.get('From', '')
                message_body = form_data.get('Body', '').strip()
                
                _LOGGER.error(f"SMS from {from_number}: '{message_body}'")
                
                # Process commands
                if message_body.lower() in ['commands', 'cmd', '?']:
                    response = (
                        "MeshCore SMS Gateway Commands:\n\n"
                        "commands - Show this help\n"
                        "status - SMS Gateway status\n"
                        "@[MeshCore User] message - Send to user\n"
                        "@[abcdef] your message - Send to public key\n\n"
                        "Example:\n@[Mike Tdeck] Heynow!"
                    )
                elif message_body.lower() == 'status':
                    # Track this message
                    st.msg_times.append(datetime.now(st.tz))
                    
                    # Get recent activity
                    cutoff = datetime.now(st.tz) - timedelta(minutes=30)
                    recent_messages = [m for m in st.msg_times if m > cutoff]
                    
                    response = (
                        f"MeshCore SMS Gateway Status:\n"
                        f"Last 30min: {len(recent_messages)} msgs\n"
                        f"Total: {len(st.msg_times)} msgs\n"
                        f"Operational - {datetime.now(st.tz).strftime('%H:%M UTC')}"
                    )
                elif message_body.startswith('@[') and ']' in message_body:
                    # Parse @[username] message format
                    bracket_end = message_body.find(']')
                    target_user = message_body[2:bracket_end]
                    user_message = message_body[bracket_end + 1:].strip()
                    
                    if target_user and user_message:
                        # Track message
                        st.msg_times.append(datetime.now(st.tz))
                        # TODO: Actually send to MeshCore here
                        response = f"✅ Message sent to @[{target_user}]: {user_message}"
                    else:
                        response = "❌ Format: @[username] Hello there!"
                else:
                    response = (
                        "Unknown command. Send 'commands' for help.\n\n"
                        "Quick commands:\n"
                        "• commands - Show help\n"
                        "• status - MeshCore SMS Gateway status\n" 
                        "• @[Meshcore User] your message - Send to MeshCore user"
                    )
                
                _LOGGER.error(f"PLAIN TEXT Response: '{response}'")
                
                # Return PLAIN TEXT - no TwiML!
                return web.Response(
                    text=response,
                    content_type="text/plain",
                    status=200
                )
                
            except Exception as e:
                _LOGGER.error(f"Webhook error: {e}")
                return web.Response(
                    text="Error processing message",
                    content_type="text/plain",
                    status=200
                )
        
        # Register webhook
        webhook.async_register(
            hass,
            DOMAIN,
            "MeshCore SMS Webhook",
            webhook_id,
            handle_sms,
            allowed_methods=["POST"],
        )
        st.webhook_id = webhook_id
        _LOGGER.error(f"Registered PLAIN TEXT webhook: {webhook_id}")
        
        # Register SMS sending service
        async def send_sms_service(call):
            """Send an SMS message."""
            phone_number = call.data.get("phone_number", "")
            message = call.data.get("message", "")
            
            _LOGGER.info(f"SMS service called: to={phone_number}")
            
            config_data = st.entry.data
            account_sid = config_data.get("account_sid", "")
            auth_token = config_data.get("auth_token", "")
            from_number = config_data.get("from_number", "")
            
            try:
                import asyncio
                
                def send_twilio_sms():
                    from twilio.rest import Client as TwilioClient
                    client = TwilioClient(account_sid, auth_token)
                    return client.messages.create(
                        body=message,
                        from_=from_number,
                        to=phone_number
                    )
                
                loop = asyncio.get_event_loop()
                twilio_message = await loop.run_in_executor(None, send_twilio_sms)
                _LOGGER.info(f"SMS sent successfully: {twilio_message.sid}")
                
            except Exception as e:
                _LOGGER.error(f"Error sending SMS: {e}")
        
        hass.services.async_register(DOMAIN, "send_sms", send_sms_service)
        st.track_service("send_sms")
        _LOGGER.info("Registered send_sms service")
        
        # Debug service to show webhook URL
        async def debug_info_service(call):
            """Show debug information including webhook URL."""
            _LOGGER.error("=== DEBUG INFO ===")
            _LOGGER.error(f"Webhook ID: {st.webhook_id}")
            _LOGGER.error(f"Webhook URL: https://YOUR-DOMAIN.COM/api/webhook/{st.webhook_id}")
            _LOGGER.error("Use this URL in Twilio console!")
            _LOGGER.error("================")
        
        hass.services.async_register(DOMAIN, "debug_info", debug_info_service)
        st.track_service("debug_info")
        
        # Test service
        def simple_test_service(call):
            _LOGGER.error("TEST SERVICE CALLED!")
        
        hass.services.async_register(DOMAIN, "simple_test", simple_test_service)
        st.track_service("simple_test")
        
        _LOGGER.error("=== PLAIN TEXT VERSION SETUP COMPLETE ===")
        return True
        
    except Exception as e:
        _LOGGER.error(f"Error setting up MeshCore SMS entry {entry.entry_id}: {e}")
        st.close()
        hass.data[DOMAIN].pop(entry.entry_id, None)
        raise ConfigEntryNotReady(f"Failed to set up MeshCore SMS: {e}") from e

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry cleanly."""
    _LOGGER.info(f"Unloading MeshCore SMS entry: {entry.entry_id}")
    
    st: State | None = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if st:
        st.close()
        
    if DOMAIN in hass.data and not hass.data[DOMAIN]:
        hass.data.pop(DOMAIN)
    
    _LOGGER.info(f"Successfully unloaded MeshCore SMS entry: {entry.entry_id}")
    return True

async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload entry when options change."""
    _LOGGER.info(f"Reloading MeshCore SMS entry: {entry.entry_id}")
    await hass.config_entries.async_reload(entry.entry_id)