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
from homeassistant.helpers import entity_registry as er

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

async def lookup_meshcore_display_name(hass, pubkey_prefix):
    """NEW FUNCTION: Lookup human-readable name from MeshCore entities."""
    try:
        _LOGGER.error(f"=== LOOKING UP NAME FOR PUBKEY: {pubkey_prefix} ===")
        
        # Get entity registry
        entity_registry = er.async_get(hass)
        
        # Find MeshCore contact entities
        meshcore_contacts = [
            entity for entity in entity_registry.entities.values()
            if entity.platform == "meshcore" and "_contact" in entity.entity_id
        ]
        
        _LOGGER.error(f"Found {len(meshcore_contacts)} MeshCore contact entities")
        
        # Check each contact for matching pubkey
        for contact_entity in meshcore_contacts:
            state = hass.states.get(contact_entity.entity_id)
            if state and state.attributes:
                contact_pubkey = state.attributes.get('public_key', '')
                contact_name = state.attributes.get('adv_name', '') or state.name or "Unknown"
                
                _LOGGER.error(f"Checking: {contact_entity.entity_id}")
                _LOGGER.error(f"  Name: '{contact_name}'")
                _LOGGER.error(f"  Pubkey: '{contact_pubkey[:12]}...'")
                
                # Match if pubkey starts with our prefix
                if contact_pubkey and contact_pubkey.startswith(pubkey_prefix):
                    _LOGGER.error(f"*** MATCH FOUND! Using name: '{contact_name}' ***")
                    return contact_name
        
        # No match found - return truncated pubkey
        fallback_name = f"{pubkey_prefix[:8]}"
        _LOGGER.error(f"No name found, using fallback: {fallback_name}")
        return fallback_name
        
    except Exception as e:
        _LOGGER.error(f"Error in name lookup: {e}")
        return f"{pubkey_prefix[:8]}"

async def send_sms_to_meshcore_enhanced(hass, target_user, message, from_sms):
    """ENHANCED: Send SMS message to MeshCore with better error handling."""
    try:
        # Format message with SMS origin
        formatted_message = f"SMS from ***{from_sms[-4:]}: {message}"
        
        _LOGGER.info(f"Sending SMS→MeshCore to {target_user}: {formatted_message}")
        
        # Determine service data based on target format
        service_data = {"message": formatted_message}
        
        # Hex string (6+ chars) = pubkey_prefix, otherwise = node_id
        if len(target_user) >= 6 and all(c in '0123456789abcdefABCDEF' for c in target_user):
            service_data["pubkey_prefix"] = target_user.lower()
            _LOGGER.debug(f"Using pubkey_prefix: {target_user}")
        else:
            service_data["node_id"] = target_user
            _LOGGER.debug(f"Using node_id: {target_user}")
        
        # Call MeshCore service with timeout for ACK detection
        try:
            response = await hass.services.async_call(
                "meshcore",
                "send_message", 
                service_data,
                blocking=True,
                timeout=30  # 30 second timeout for ACK detection
            )
            
            _LOGGER.info(f"MeshCore service response: {response}")
            return {"success": True, "message": "delivered"}
            
        except Exception as service_error:
            error_msg = str(service_error).lower()
            
            # Parse specific error conditions for detailed user feedback
            if "not found" in error_msg or "unknown" in error_msg or "invalid" in error_msg:
                return {
                    "success": False,
                    "error": "user_not_found", 
                    "message": f"User @[{target_user}] not found on MeshCore network"
                }
            elif "timeout" in error_msg or "no response" in error_msg or "ack" in error_msg:
                return {
                    "success": False,
                    "error": "no_delivery_confirmation",
                    "message": f"Message sent but no delivery confirmation from @[{target_user}]"
                }
            elif "offline" in error_msg or "unreachable" in error_msg:
                return {
                    "success": False,
                    "error": "user_offline",
                    "message": f"@[{target_user}] is offline or unreachable"
                }
            elif "meshcore" in error_msg and ("not" in error_msg or "unavailable" in error_msg):
                return {
                    "success": False,
                    "error": "meshcore_disconnected",
                    "message": "MeshCore integration is not available or disconnected"
                }
            else:
                return {
                    "success": False,
                    "error": "unknown_error",
                    "message": f"Failed to send: {str(service_error)}"
                }
        
    except Exception as e:
        _LOGGER.error(f"Critical error sending SMS→MeshCore to {target_user}: {e}")
        return {
            "success": False,
            "error": "system_error",
            "message": f"System error: {str(e)}"
        }

async def send_meshcore_to_sms_enhanced(hass, state, phone_number, message, sender_pubkey):
    """ENHANCED: Send SMS from MeshCore with name lookup."""
    try:
        # NEW: Lookup human-readable name from pubkey
        display_name = await lookup_meshcore_display_name(hass, sender_pubkey)
        
        # Format message with sender info
        formatted_message = f"@[{display_name}]: {message}"
        
        _LOGGER.error(f"=== SENDING SMS ===")
        _LOGGER.error(f"Original pubkey: {sender_pubkey}")
        _LOGGER.error(f"Display name: {display_name}")
        _LOGGER.error(f"Final message: {formatted_message}")
        
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
        
        _LOGGER.error(f"SMS sent successfully: {twilio_message.sid}")
        
    except Exception as e:
        _LOGGER.error(f"Error sending MeshCore→SMS to {phone_number}: {e}")

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up MeshCore SMS with USERNAME LOOKUP VERSION."""
    _LOGGER.error("=== USERNAME LOOKUP VERSION LOADING ===")
    _LOGGER.error("=== THIS VERSION SHOWS NAMES INSTEAD OF PUBKEYS ===")
    
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}
    
    st = State(hass, entry)
    hass.data[DOMAIN][entry.entry_id] = st
    
    try:
        # Enhanced MeshCore event listener
        @callback
        def on_meshcore_event_enhanced(event):
            """ENHANCED: Handle MeshCore events with name lookup."""
            st.msg_times.append(datetime.now(st.tz))
            
            try:
                _LOGGER.error("=== ENHANCED MESHCORE EVENT ===")
                event_data = event.data
                event_type = event_data.get('event_type', 'NO_TYPE')
                payload = event_data.get('payload', {})
                
                _LOGGER.error(f"Event type: {event_type}")
                _LOGGER.error(f"Payload keys: {list(payload.keys())}")
                
                # Look for message events
                if any(keyword in str(event_type).upper() for keyword in ['MSG', 'MESSAGE', 'TEXT', 'RECEIVE']):
                    _LOGGER.error("MESSAGE EVENT DETECTED")
                    
                    # Extract sender (prioritize pubkey_prefix)
                    sender = 'unknown'
                    if 'pubkey_prefix' in payload:
                        sender = payload['pubkey_prefix']
                        _LOGGER.error(f"Got sender from pubkey_prefix: {sender}")
                    elif 'sender' in payload:
                        sender = payload['sender']
                        _LOGGER.error(f"Got sender from sender field: {sender}")
                    
                    # Extract message (prioritize 'text' field)
                    message_text = ''
                    if 'text' in payload:
                        message_text = payload['text']
                        _LOGGER.error(f"Got message from text field: {message_text}")
                    elif 'message' in payload:
                        message_text = payload['message']
                        _LOGGER.error(f"Got message from message field: {message_text}")
                    
                    if message_text:
                        # Check for phone number pattern
                        import re
                        phone_pattern = r'^\+?1?[0-9]{10,15}\s+'
                        phone_match = re.match(phone_pattern, str(message_text))
                        
                        if phone_match:
                            phone_number = phone_match.group().strip()
                            sms_message = str(message_text)[len(phone_match.group()):].strip()
                            
                            _LOGGER.error(f"PHONE PATTERN MATCHED!")
                            _LOGGER.error(f"Phone: {phone_number}")
                            _LOGGER.error(f"SMS Message: {sms_message}")
                            _LOGGER.error(f"Sender: {sender}")
                            
                            if sms_message:
                                # Route to SMS with enhanced name lookup
                                hass.async_create_task(
                                    send_meshcore_to_sms_enhanced(hass, st, phone_number, sms_message, sender)
                                )
                        
            except Exception as e:
                _LOGGER.error(f"Error in enhanced event handler: {e}")
        
        # Listen for MeshCore events
        unsub = hass.bus.async_listen("meshcore_raw_event", on_meshcore_event_enhanced)
        st.track(unsub)
        
        # Get webhook ID
        webhook_id = entry.options.get("webhook_id") or entry.data.get("webhook_id")
        if not webhook_id:
            webhook_id = f"{DOMAIN}_{entry.entry_id}"
            
        # Enhanced webhook handler
        async def handle_sms_enhanced(hass, webhook_id, request):
            """ENHANCED: Handle SMS with detailed error feedback."""
            try:
                _LOGGER.error("=== ENHANCED WEBHOOK HANDLER ===")
                
                # Parse Twilio data
                form_data = await request.post()
                from_number = form_data.get('From', '')
                message_body = form_data.get('Body', '').strip()
                
                _LOGGER.error(f"SMS from {from_number}: '{message_body}'")
                
                # Process commands
                if message_body.lower() in ['commands', 'cmd', '?']:
                    response = (
                        "📡 MeshCore SMS Commands:\n\n"
                        "COMMANDS - Show this help\n"
                        "STATUS - Gateway status & activity\n"
                        "@[username] [msg] - Send to MeshCore user\n"
                        "@[abcdef] [msg] - Send using pubkey prefix\n\n"
                        "Examples:\n"
                        "• @[john] Hello there!\n"
                        "• @[a1b2c3] Weather update"
                    )
                    
                elif message_body.lower() == 'status':
                    st.msg_times.append(datetime.now(st.tz))
                    cutoff = datetime.now(st.tz) - timedelta(minutes=30)
                    recent = [m for m in st.msg_times if m > cutoff]
                    
                    response = (
                        f"📊 Gateway Status:\n"
                        f"🕐 Last 30min: {len(recent)} msgs\n"
                        f"📅 Total: {len(st.msg_times)} msgs\n"
                        f"✅ Operational - {datetime.now(st.tz).strftime('%H:%M UTC')}"
                    )
                    
                elif message_body.startswith('@[') and ']' in message_body:
                    # Enhanced @[username] handling with detailed errors
                    bracket_end = message_body.find(']')
                    target_user = message_body[2:bracket_end]
                    user_message = message_body[bracket_end + 1:].strip()
                    
                    if target_user and user_message:
                        st.msg_times.append(datetime.now(st.tz))
                        
                        # Send with enhanced error handling
                        result = await send_sms_to_meshcore_enhanced(hass, target_user, user_message, from_number)
                        
                        if result["success"]:
                            response = f"✅ Message delivered to @[{target_user}]"
                        else:
                            # Specific error responses
                            error_type = result["error"]
                            if error_type == "user_not_found":
                                response = f"❌ @[{target_user}] not found. Check spelling or try 6+ hex chars for pubkey."
                            elif error_type == "no_delivery_confirmation":
                                response = f"⚠️ Sent to @[{target_user}] but no delivery confirmation. User may be offline/out of range."
                            elif error_type == "user_offline":
                                response = f"📴 @[{target_user}] is offline or unreachable."
                            elif error_type == "meshcore_disconnected":
                                response = "🔌 MeshCore integration disconnected. Check device connection."
                            else:
                                response = f"❌ Send failed: {result['message']}"
                    else:
                        response = "❌ Format: @[username] Hello there!\nTip: Use @[abcdef] for pubkey"
                        
                else:
                    response = (
                        "Unknown command. Send 'COMMANDS' for help.\n\n"
                        "Quick commands:\n"
                        "• COMMANDS - Show help\n"
                        "• STATUS - Gateway status\n" 
                        "• @[username] [msg] - Send to user"
                    )
                
                _LOGGER.error(f"Response: {response}")
                
                return web.Response(
                    text=response,
                    content_type="text/plain",
                    status=200
                )
                
            except Exception as e:
                _LOGGER.error(f"Enhanced webhook error: {e}")
                return web.Response(
                    text="Error processing SMS. Please try again.",
                    content_type="text/plain",
                    status=200
                )
        
        # Register enhanced webhook
        webhook.async_register(
            hass,
            DOMAIN,
            "MeshCore SMS Webhook",
            webhook_id,
            handle_sms_enhanced,
            allowed_methods=["POST"],
        )
        st.webhook_id = webhook_id
        _LOGGER.error(f"Registered ENHANCED webhook: {webhook_id}")
        
        # SMS sending service
        async def send_sms_service(call):
            """Send SMS via Twilio."""
            phone_number = call.data.get("phone_number", "")
            message = call.data.get("message", "")
            
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
                _LOGGER.info(f"SMS sent: {twilio_message.sid}")
                
            except Exception as e:
                _LOGGER.error(f"SMS send error: {e}")
        
        hass.services.async_register(DOMAIN, "send_sms", send_sms_service)
        st.track_service("send_sms")
        
        # Debug service
        async def debug_info_service(call):
            """Show debug info."""
            _LOGGER.error("=== DEBUG INFO ===")
            _LOGGER.error(f"Webhook: {st.webhook_id}")
            _LOGGER.error(f"URL: https://YOUR-DOMAIN/api/webhook/{st.webhook_id}")
            _LOGGER.error("==================")
        
        hass.services.async_register(DOMAIN, "debug_info", debug_info_service)
        st.track_service("debug_info")
        
        _LOGGER.error("=== USERNAME LOOKUP VERSION READY ===")
        return True
        
    except Exception as e:
        _LOGGER.error(f"Setup error: {e}")
        st.close()
        hass.data[DOMAIN].pop(entry.entry_id, None)
        raise ConfigEntryNotReady(f"Setup failed: {e}") from e

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload entry."""
    st: State | None = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if st:
        st.close()
    if DOMAIN in hass.data and not hass.data[DOMAIN]:
        hass.data.pop(DOMAIN)
    return True

async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload entry."""
    await hass.config_entries.async_reload(entry.entry_id)