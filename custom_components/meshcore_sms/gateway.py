"""MeshCore SMS Gateway - Simple working version."""

import logging
import re

from twilio.rest import Client
from twilio.base.exceptions import TwilioException

from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.components import webhook

DOMAIN = "meshcore_sms"

_LOGGER = logging.getLogger(__name__)

# Phone number regex
PHONE_REGEX = re.compile(r'^\+?[1-9]\d{1,14}$')

CONFIG_SCHEMA = {
    "twilio_config": {
        "title": "Twilio Configuration", 
        "description": "Enter your Twilio credentials",
        "data": {
            "account_sid": "Your Twilio Account SID (starts with AC)",
            "auth_token": "Your Twilio Auth Token", 
            "from_number": "Your Twilio phone number with country code (e.g., +1234567890)"
        }
    },
    "gateway_settings": {
        "title": "Gateway Settings",
        "description": "Configure the SMS gateway for phone number {phone_number}",
        "data": {
            "bot_name": "Bot Name",
            "daily_limit": "Daily SMS Limit", 
            "meshcore_channel": "MeshCore Channel",
            "enable_broadcast": "Enable SMS Broadcast",
            "delivery_confirmation": "Send Delivery Confirmations"
        }
    }
}

class MeshCoreSMSGateway:
    """MeshCore SMS Gateway - Simple version."""

    def __init__(self, hass: HomeAssistant, config_entry) -> None:
        """Initialize the gateway."""
        self.hass = hass
        self.config_entry = config_entry
        self.config = config_entry.data
        
        # Twilio client
        self.twilio_client = None
        
        # Stats
        self.messages_sent = 0
        self.messages_received = 0
        
        # Bot name
        self.bot_name = self.config.get("bot_name", "sms_bot")
        
        # Webhook
        self.webhook_id = None
        
        # Event listeners
        self._listeners = []

    async def async_setup(self) -> bool:
        """Set up the gateway."""
        try:
            _LOGGER.info("🚀 Starting gateway setup...")
            
            # Initialize Twilio client
            await self._async_init_twilio()
            
            # Register services
            await self._async_register_services()
            
            # Set up webhook for incoming SMS
            await self._async_setup_webhook()
            
            # List available MeshCore services
            await self._list_meshcore_services()
            
            # Subscribe to MeshCore events
            await self._async_subscribe_to_meshcore()
            
            _LOGGER.info(
                "✅ MeshCore SMS Gateway ready! Bot: '%s', Phone: %s",
                self.bot_name,
                self.config.get("from_number")
            )
            
            _LOGGER.info(
                "📝 Instructions: Send a DM to '%s' in MeshCore, or call service meshcore_sms.send_sms",
                self.bot_name
            )
            
            return True
            
        except Exception as err:
            _LOGGER.error("❌ Failed to initialize gateway: %s", err)
            import traceback
            _LOGGER.error("Traceback: %s", traceback.format_exc())
            return False

    async def _async_init_twilio(self) -> None:
        """Initialize Twilio client."""
        def init_client():
            return Client(
                self.config.get("account_sid"),
                self.config.get("auth_token"),
            )
        
        self.twilio_client = await self.hass.async_add_executor_job(init_client)
        _LOGGER.info("✅ Twilio client initialized")

    async def _async_register_services(self) -> None:
        """Register services."""
        
        async def handle_send_sms(call: ServiceCall) -> None:
            """Handle send SMS service."""
            phone = call.data.get("phone_number")
            message = call.data.get("message")
            
            if not PHONE_REGEX.match(phone):
                _LOGGER.error("Invalid phone number: %s", phone)
                return
                
            success = await self.send_sms(phone, message, "service_call")
            if success:
                _LOGGER.info("✅ SMS sent to %s", phone)
            else:
                _LOGGER.error("❌ Failed to send SMS to %s", phone)
        
        async def handle_test_meshcore(call: ServiceCall) -> None:
            """Test sending message to MeshCore."""
            recipient = call.data.get("recipient", "test")
            message = call.data.get("message", "Test message from SMS Gateway")
            
            # Check if recipient is "all" or "broadcast" for channel message
            if recipient.lower() in ["all", "broadcast", "channel"]:
                await self._broadcast_to_meshcore(message)
                _LOGGER.info("✅ Test broadcast sent to channel")
                return {"status": "success", "type": "channel"}
            else:
                await self._send_meshcore_message(recipient, message)
                _LOGGER.info("✅ Test message sent to node: %s", recipient)
                return {"status": "success", "type": "direct", "recipient": recipient}
        
        self.hass.services.async_register(DOMAIN, "send_sms", handle_send_sms)
        self.hass.services.async_register(DOMAIN, "test_meshcore", handle_test_meshcore)
        _LOGGER.info("✅ Services registered: send_sms, test_meshcore")

    async def _async_setup_webhook(self) -> None:
        """Set up webhook for incoming SMS."""
        self.webhook_id = f"{DOMAIN}_{self.config_entry.entry_id}"
        
        webhook.async_register(
            self.hass,
            DOMAIN,
            "MeshCore SMS",
            self.webhook_id,
            self._handle_webhook,
        )
        
        webhook_url = webhook.async_generate_url(self.hass, self.webhook_id)
        _LOGGER.info(
            "📱 IMPORTANT: Configure this webhook URL in Twilio:\n    %s",
            webhook_url
        )

    async def _list_meshcore_services(self) -> None:
        """List all available MeshCore services."""
        services = self.hass.services.async_services().get("meshcore", {})
        if services:
            _LOGGER.info(
                "📋 Available MeshCore services:\n%s",
                "\n".join(f"  - meshcore.{service}" for service in services.keys())
            )
            # Store available services for later use
            self.meshcore_services = list(services.keys())
        else:
            _LOGGER.warning("⚠️ No MeshCore services found! Is MeshCore running?")
            self.meshcore_services = []

    async def _async_subscribe_to_meshcore(self) -> None:
        """Subscribe to MeshCore events."""
        
        @callback
        def handle_meshcore_event(event):
            """Handle any MeshCore event."""
            _LOGGER.info("📨 Received event %s with data: %s", event.event_type, event.data)
            
            # Check if this is a message for our bot
            data = event.data
            recipient = data.get("recipient") or data.get("to") or data.get("target")
            
            if recipient == self.bot_name:
                _LOGGER.info("🎯 Message is for our bot!")
                self.hass.async_create_task(self._process_meshcore_message(data))
        
        # Subscribe to various possible event types
        event_types = [
            "meshcore_message_received",
            "meshcore.message_received",
            "meshcore_message",
            "meshcore.message",
        ]
        
        for event_type in event_types:
            unsub = self.hass.bus.async_listen(event_type, handle_meshcore_event)
            self._listeners.append(unsub)
            _LOGGER.info("📡 Listening for event: %s", event_type)
        
        # Also listen for a generic pattern
        @callback
        def handle_any_meshcore_event(event):
            """Handle any event that might be from MeshCore."""
            if "meshcore" in event.event_type.lower():
                _LOGGER.debug("🔍 MeshCore event detected: %s", event.event_type)
                handle_meshcore_event(event)
        
        # Listen for any event with meshcore in the name
        unsub = self.hass.bus.async_listen("*", handle_any_meshcore_event)
        self._listeners.append(unsub)

    async def _process_meshcore_message(self, data: dict) -> None:
        """Process incoming MeshCore message."""
        sender = data.get("sender") or data.get("from") or data.get("sender_id") or "unknown"
        message = data.get("message") or data.get("text") or data.get("content") or ""
        
        _LOGGER.info("💬 Processing message from %s: %s", sender, message)
        
        if not message:
            return
        
        # Parse command
        cmd = message.strip().upper().split()[0]
        
        if cmd in ["HELP", "?"]:
            response = (
                "📱 SMS Gateway Commands:\n"
                "• HELP - Show this message\n"
                "• STATUS - Check gateway status\n"
                "• SMS <phone> <message> - Send SMS\n"
                f"Example: SMS +1234567890 Hello world"
            )
            await self._send_meshcore_message(sender, response)
            
        elif cmd == "STATUS":
            response = (
                f"✅ Gateway Online\n"
                f"Phone: {self.config.get('from_number')}\n"
                f"Bot: {self.bot_name}\n"
                f"Messages sent: {self.messages_sent}\n"
                f"Messages received: {self.messages_received}\n"
                f"Daily limit: {self.config.get('daily_limit', 50)}\n"
                f"Broadcast: {'✅ Enabled' if self.config.get('enable_broadcast', True) else '❌ Disabled'}\n"
                f"Confirmations: {'✅ On' if self.config.get('delivery_confirmation', False) else '❌ Off'}"
            )
            await self._send_meshcore_message(sender, response)
            
        elif cmd == "SMS":
            parts = message.split(None, 2)
            if len(parts) < 3:
                await self._send_meshcore_message(
                    sender,
                    "❌ Format: SMS <phone> <message>"
                )
                return
                
            _, phone, sms_text = parts
            
            if not PHONE_REGEX.match(phone):
                await self._send_meshcore_message(sender, f"❌ Invalid phone: {phone}")
                return
                
            if await self.send_sms(phone, sms_text, sender):
                self.messages_sent += 1
                await self._send_meshcore_message(sender, f"✅ SMS sent to {phone[-4:]}")
            else:
                await self._send_meshcore_message(sender, "❌ Failed to send SMS")
        else:
            await self._send_meshcore_message(sender, f"❓ Unknown command: {cmd}")

    async def send_sms(self, phone: str, message: str, sender: str) -> bool:
        """Send SMS via Twilio."""
        try:
            # Format sender name nicely
            if sender == "service_call":
                sender_prefix = "HA"
            elif sender == "system":
                sender_prefix = "System"
            elif sender.startswith("@"):
                sender_prefix = sender[1:]  # Remove @ from username
            else:
                sender_prefix = sender[:10]  # Limit length
            
            def send():
                return self.twilio_client.messages.create(
                    body=f"[{sender_prefix}] {message}",
                    from_=self.config.get("from_number"),
                    to=phone,
                )
            
            result = await self.hass.async_add_executor_job(send)
            _LOGGER.info("📱 SMS sent to %s (SID: %s)", phone, result.sid)
            return True
            
        except TwilioException as err:
            _LOGGER.error("❌ Twilio error: %s", err)
            return False

    @callback
    async def _handle_webhook(self, hass, webhook_id, request):
        """Handle incoming SMS webhook."""
        try:
            data = await request.post()
            from_number = data.get("From")
            message_body = data.get("Body")
            
            _LOGGER.info("📲 Received SMS from %s: %s", from_number, message_body)
            
            if not from_number or not message_body:
                return
            
            self.messages_received += 1
            
            # Parse recipient if message starts with @
            if message_body.startswith("@"):
                parts = message_body.split(None, 1)
                if len(parts) == 2:
                    recipient = parts[0][1:]  # Remove @
                    actual_message = parts[1]
                else:
                    recipient = "broadcast"
                    actual_message = message_body
            else:
                recipient = "broadcast"
                actual_message = message_body
            
            # Check if broadcast is enabled
            if recipient == "broadcast":
                if self.config.get("enable_broadcast", True):
                    await self._broadcast_to_meshcore(
                        f"SMS from {from_number[-4:]}: {actual_message}"
                    )
                else:
                    _LOGGER.info("📵 Broadcast disabled, ignoring SMS without @recipient")
            else:
                await self._send_meshcore_message(
                    recipient,
                    f"SMS from {from_number[-4:]}: {actual_message}"
                )
            
            # Send delivery confirmation if enabled
            if self.config.get("delivery_confirmation", False):
                await self.send_sms(
                    from_number,
                    "Message delivered to MeshCore network",
                    "system"
                )
            
        except Exception as err:
            _LOGGER.error("❌ Webhook error: %s", err)

    async def _send_meshcore_message(self, recipient: str, message: str) -> None:
        """Send message to MeshCore user."""
        # MeshCore uses send_message service with node_id or pubkey_prefix
        if self.hass.services.has_service("meshcore", "send_message"):
            try:
                # Try sending by node_id (name) first
                await self.hass.services.async_call(
                    "meshcore", 
                    "send_message",
                    {
                        "node_id": recipient,
                        "message": message
                    }
                )
                _LOGGER.info("✅ Sent to %s via meshcore.send_message", recipient)
                return
            except Exception as e:
                _LOGGER.debug("Failed to send by node_id: %s", e)
                
                # If node_id fails and recipient looks like a pubkey, try pubkey_prefix
                if len(recipient) >= 6 and all(c in '0123456789abcdef' for c in recipient.lower()):
                    try:
                        await self.hass.services.async_call(
                            "meshcore",
                            "send_message",
                            {
                                "pubkey_prefix": recipient[:6] if len(recipient) > 6 else recipient,
                                "message": message
                            }
                        )
                        _LOGGER.info("✅ Sent to %s via pubkey_prefix", recipient)
                        return
                    except Exception as e:
                        _LOGGER.error("❌ Failed to send by pubkey_prefix: %s", e)
                else:
                    _LOGGER.error("❌ Could not send to %s - node_id not found", recipient)
        else:
            _LOGGER.error("❌ meshcore.send_message service not found")

    async def _broadcast_to_meshcore(self, message: str) -> None:
        """Broadcast message to MeshCore channel."""
        # Use send_channel_message for broadcasts
        if self.hass.services.has_service("meshcore", "send_channel_message"):
            try:
                # Default to channel 0 (usually the primary/general channel)
                # Get channel from config, default to 0
                channel_idx = self.config.get("meshcore_channel", 0)
                
                await self.hass.services.async_call(
                    "meshcore",
                    "send_channel_message",
                    {
                        "channel_idx": channel_idx,
                        "message": message
                    }
                )
                _LOGGER.info("✅ Broadcast sent to channel %s via meshcore.send_channel_message", channel_idx)
                return
            except Exception as e:
                _LOGGER.error("❌ Failed to send channel message: %s", e)
                
                # Create notification about the error
                await self.hass.services.async_call(
                    "persistent_notification",
                    "create",
                    {
                        "title": "SMS Broadcast Failed",
                        "message": (
                            f"Could not broadcast SMS to MeshCore channel {channel_idx}.\n"
                            f"Error: {e}\n\n"
                            f"**Message:** {message}\n\n"
                            f"Try using @node_name format to send to specific nodes."
                        ),
                        "notification_id": f"meshcore_sms_broadcast_error_{self.messages_received}",
                    }
                )
        else:
            _LOGGER.error("❌ meshcore.send_channel_message service not found")

    async def async_unload(self) -> bool:
        """Unload the gateway."""
        # Remove event listeners
        for unsub in self._listeners:
            unsub()
        self._listeners.clear()
        
        # Remove webhook
        if self.webhook_id:
            webhook.async_unregister(self.hass, self.webhook_id)
        
        # Remove services
        self.hass.services.async_remove(DOMAIN, "send_sms")
        self.hass.services.async_remove(DOMAIN, "test_meshcore")
        
        _LOGGER.info("👋 Gateway unloaded")
        return True