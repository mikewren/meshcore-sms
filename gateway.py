"""MeshCore SMS Gateway core functionality."""

import asyncio
import logging
import re
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from twilio.rest import Client
from twilio.base.exceptions import TwilioException

from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import storage
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    CONF_ACCOUNT_SID,
    CONF_AUTH_TOKEN,
    CONF_FROM_NUMBER,
    CONF_DAILY_LIMIT,
    CONF_BOT_NAME,
    CONF_ENABLE_BROADCAST,
    CONF_DELIVERY_CONFIRMATION,
    EVENT_SMS_RECEIVED,
    EVENT_SMS_SENT,
    ATTR_PHONE_NUMBER,
    ATTR_MESSAGE,
    ATTR_SENDER,
    ATTR_RECIPIENT,
    ATTR_TIMESTAMP,
    STORAGE_KEY,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)

# Regex for phone number validation
PHONE_REGEX = re.compile(r'^\+?[1-9]\d{1,14}$')

# Commands
COMMANDS = {
    "HELP": "Show available commands",
    "SMS": "Send SMS: SMS <phone> <message>",
    "STATUS": "Check gateway status",
    "LIST": "Show recent messages",
    "REGISTER": "Link your phone number",
    "STOP": "Unsubscribe from broadcasts",
}


class MeshCoreSMSGateway:
    """MeshCore SMS Gateway."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry,
    ) -> None:
        """Initialize the gateway."""
        self.hass = hass
        self.config_entry = config_entry
        self.config = config_entry.data
        
        # Twilio client
        self.twilio_client = None
        
        # Rate limiting
        self.daily_counts = defaultdict(int)
        self.last_reset = dt_util.now()
        
        # Message history
        self.message_history = []
        self.max_history = 50
        
        # Storage
        self._store = storage.Store(
            hass, STORAGE_VERSION, f"{STORAGE_KEY}_{config_entry.entry_id}"
        )
        self._stored_data = {}
        
        # Webhooks
        self.webhook_id = None
        
        # Cleanup tasks
        self._cleanup_task = None

    async def async_setup(self) -> bool:
        """Set up the gateway."""
        try:
            # Initialize Twilio client
            await self._async_init_twilio()
            
            # Load stored data
            await self._async_load_data()
            
            # Register services
            await self._async_register_services()
            
            # Set up webhook for incoming SMS
            await self._async_setup_webhook()
            
            # Subscribe to MeshCore events
            await self._async_subscribe_to_meshcore()
            
            # Set up daily reset
            self._cleanup_task = async_track_time_interval(
                self.hass, self._async_daily_reset, timedelta(days=1)
            )
            
            _LOGGER.info("MeshCore SMS Gateway initialized successfully")
            return True
            
        except Exception as err:
            _LOGGER.error("Failed to initialize gateway: %s", err)
            return False

    async def async_unload(self) -> bool:
        """Unload the gateway."""
        # Cancel cleanup task
        if self._cleanup_task:
            self._cleanup_task()
        
        # Remove webhook
        if self.webhook_id:
            self.hass.components.webhook.async_unregister(self.webhook_id)
        
        # Save data
        await self._async_save_data()
        
        return True

    async def _async_init_twilio(self) -> None:
        """Initialize Twilio client."""
        def init_client():
            return Client(
                self.config[CONF_ACCOUNT_SID],
                self.config[CONF_AUTH_TOKEN],
            )
        
        self.twilio_client = await self.hass.async_add_executor_job(init_client)

    async def _async_load_data(self) -> None:
        """Load stored data."""
        data = await self._store.async_load()
        if data:
            self._stored_data = data
            self.daily_counts = defaultdict(int, data.get("daily_counts", {}))
            self.message_history = data.get("message_history", [])
            
            # Check if we need to reset daily counts
            last_reset = data.get("last_reset")
            if last_reset:
                last_reset_dt = dt_util.parse_datetime(last_reset)
                if dt_util.now().date() > last_reset_dt.date():
                    self.daily_counts.clear()
                    self.last_reset = dt_util.now()

    async def _async_save_data(self) -> None:
        """Save data to storage."""
        data = {
            "daily_counts": dict(self.daily_counts),
            "message_history": self.message_history[-self.max_history:],
            "last_reset": self.last_reset.isoformat(),
        }
        await self._store.async_save(data)

    async def _async_register_services(self) -> None:
        """Register services."""
        
        async def handle_send_sms(call: ServiceCall) -> None:
            """Handle send SMS service."""
            phone = call.data[ATTR_PHONE_NUMBER]
            message = call.data[ATTR_MESSAGE]
            await self.send_sms(phone, message, "service_call")
        
        self.hass.services.async_register(
            DOMAIN, "send_sms", handle_send_sms
        )

    async def _async_setup_webhook(self) -> None:
        """Set up webhook for incoming SMS."""
        self.webhook_id = f"{DOMAIN}_{self.config_entry.entry_id}"
        
        self.hass.components.webhook.async_register(
            DOMAIN,
            "MeshCore SMS",
            self.webhook_id,
            self._handle_webhook,
        )
        
        # Log the webhook URL for Twilio configuration
        webhook_url = self.hass.components.webhook.async_generate_url(
            self.webhook_id
        )
        _LOGGER.info(
            "Webhook URL for Twilio: %s (configure this in Twilio console)",
            webhook_url
        )

    async def _async_subscribe_to_meshcore(self) -> None:
        """Subscribe to MeshCore events."""
        
        @callback
        def handle_meshcore_message(event):
            """Handle incoming MeshCore message."""
            asyncio.create_task(self._process_meshcore_message(event.data))
        
        # Subscribe to MeshCore message events
        self.hass.bus.async_listen(
            "meshcore.message_received", handle_meshcore_message
        )

    async def _process_meshcore_message(self, data: Dict[str, Any]) -> None:
        """Process incoming MeshCore message."""
        # Check if message is directed to the SMS bot
        if data.get("recipient") != self.config[CONF_BOT_NAME]:
            return
        
        sender_id = data.get("sender_id")
        message = data.get("text", "").strip()
        
        if not message:
            return
        
        # Parse command
        command_upper = message.upper().split()[0]
        
        if command_upper == "HELP" or command_upper == "?":
            await self._send_help(sender_id)
            
        elif command_upper == "STATUS":
            await self._send_status(sender_id)
            
        elif command_upper == "LIST":
            await self._send_history(sender_id)
            
        elif command_upper == "SMS":
            await self._handle_sms_command(sender_id, message)
            
        else:
            await self._send_meshcore_message(
                sender_id,
                f"Unknown command '{command_upper}'. Send HELP for commands."
            )

    async def _handle_sms_command(self, sender_id: str, message: str) -> None:
        """Handle SMS command from MeshCore."""
        # Parse SMS command: SMS <phone> <message>
        parts = message.split(None, 2)
        
        if len(parts) < 3:
            await self._send_meshcore_message(
                sender_id,
                "❌ Invalid format. Use: SMS <phone> <message>\n"
                "Example: SMS +1234567890 Hello world"
            )
            return
        
        _, phone_number, sms_content = parts
        
        # Validate phone number
        if not PHONE_REGEX.match(phone_number):
            await self._send_meshcore_message(
                sender_id,
                f"❌ Invalid phone number format: {phone_number}"
            )
            return
        
        # Check rate limit
        if not await self._check_rate_limit(sender_id):
            await self._send_meshcore_message(
                sender_id,
                f"⚠️ Daily limit reached ({self.config[CONF_DAILY_LIMIT]} messages)"
            )
            return
        
        # Send SMS
        success = await self.send_sms(phone_number, sms_content, sender_id)
        
        if success:
            # Update rate limit
            self.daily_counts[sender_id] += 1
            
            # Send confirmation
            masked_phone = f"{phone_number[:3]}***{phone_number[-4:]}"
            await self._send_meshcore_message(
                sender_id,
                f"✅ SMS sent to {masked_phone}"
            )
            
            # Save data
            await self._async_save_data()
        else:
            await self._send_meshcore_message(
                sender_id,
                "❌ Failed to send SMS. Please try again."
            )

    async def send_sms(
        self, phone_number: str, message: str, sender: str
    ) -> bool:
        """Send SMS via Twilio."""
        try:
            def send():
                return self.twilio_client.messages.create(
                    body=f"[MeshCore:{sender}] {message}",
                    from_=self.config[CONF_FROM_NUMBER],
                    to=phone_number,
                )
            
            result = await self.hass.async_add_executor_job(send)
            
            # Log to history
            self.message_history.append({
                "timestamp": dt_util.now().isoformat(),
                "direction": "outgoing",
                "phone": phone_number,
                "message": message,
                "sender": sender,
                "sid": result.sid,
            })
            
            # Fire event
            self.hass.bus.async_fire(EVENT_SMS_SENT, {
                ATTR_PHONE_NUMBER: phone_number,
                ATTR_MESSAGE: message,
                ATTR_SENDER: sender,
                ATTR_TIMESTAMP: dt_util.now().isoformat(),
            })
            
            return True
            
        except TwilioException as err:
            _LOGGER.error("Failed to send SMS: %s", err)
            return False

    @callback
    async def _handle_webhook(self, hass, webhook_id, request):
        """Handle incoming SMS webhook."""
        try:
            data = await request.post()
            
            from_number = data.get("From")
            message_body = data.get("Body")
            
            if not from_number or not message_body:
                return
            
            # Log to history
            self.message_history.append({
                "timestamp": dt_util.now().isoformat(),
                "direction": "incoming",
                "phone": from_number,
                "message": message_body,
            })
            
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
            
            # Send to MeshCore
            if recipient == "broadcast" and self.config[CONF_ENABLE_BROADCAST]:
                await self._broadcast_to_meshcore(
                    f"SMS from {from_number[-4:]}: {actual_message}"
                )
            else:
                await self._send_meshcore_message(
                    recipient,
                    f"SMS from {from_number[-4:]}: {actual_message}"
                )
            
            # Send delivery confirmation if enabled
            if self.config[CONF_DELIVERY_CONFIRMATION]:
                await self.send_sms(
                    from_number,
                    "Message delivered to MeshCore network",
                    "system"
                )
            
            # Fire event
            self.hass.bus.async_fire(EVENT_SMS_RECEIVED, {
                ATTR_PHONE_NUMBER: from_number,
                ATTR_MESSAGE: message_body,
                ATTR_RECIPIENT: recipient,
                ATTR_TIMESTAMP: dt_util.now().isoformat(),
            })
            
            # Save data
            await self._async_save_data()
            
        except Exception as err:
            _LOGGER.error("Error handling webhook: %s", err)

    async def _send_meshcore_message(self, recipient: str, message: str) -> None:
        """Send message to MeshCore user."""
        # Call MeshCore service to send message
        await self.hass.services.async_call(
            "meshcore",
            "send_message",
            {
                "recipient": recipient,
                "message": message,
            },
        )

    async def _broadcast_to_meshcore(self, message: str) -> None:
        """Broadcast message to all MeshCore users."""
        await self.hass.services.async_call(
            "meshcore",
            "broadcast",
            {
                "message": message,
            },
        )

    async def _send_help(self, sender_id: str) -> None:
        """Send help message."""
        help_text = "📱 SMS Gateway Commands:\n\n"
        for cmd, desc in COMMANDS.items():
            help_text += f"• {cmd}: {desc}\n"
        help_text += f"\nDaily limit: {self.config[CONF_DAILY_LIMIT]} messages"
        help_text += f"\nUsed today: {self.daily_counts[sender_id]}"
        
        await self._send_meshcore_message(sender_id, help_text)

    async def _send_status(self, sender_id: str) -> None:
        """Send status message."""
        status = (
            f"📊 Gateway Status\n"
            f"Status: ✅ Online\n"
            f"Phone: {self.config[CONF_FROM_NUMBER]}\n"
            f"Messages today: {sum(self.daily_counts.values())}\n"
            f"Your usage: {self.daily_counts[sender_id]}/{self.config[CONF_DAILY_LIMIT]}\n"
            f"Broadcast: {'✅' if self.config[CONF_ENABLE_BROADCAST] else '❌'}"
        )
        
        await self._send_meshcore_message(sender_id, status)

    async def _send_history(self, sender_id: str) -> None:
        """Send recent message history."""
        if not self.message_history:
            await self._send_meshcore_message(sender_id, "No recent messages")
            return
        
        history_text = "📜 Recent Messages (last 5):\n\n"
        for msg in self.message_history[-5:]:
            timestamp = dt_util.parse_datetime(msg["timestamp"])
            time_str = timestamp.strftime("%H:%M")
            direction = "→" if msg["direction"] == "outgoing" else "←"
            phone = msg["phone"][-4:]
            history_text += f"{time_str} {direction} {phone}: {msg['message'][:30]}...\n"
        
        await self._send_meshcore_message(sender_id, history_text)

    async def _check_rate_limit(self, sender_id: str) -> bool:
        """Check if sender is within rate limit."""
        return self.daily_counts[sender_id] < self.config[CONF_DAILY_LIMIT]

    async def _async_daily_reset(self, _) -> None:
        """Reset daily counters."""
        self.daily_counts.clear()
        self.last_reset = dt_util.now()
        await self._async_save_data()
        _LOGGER.info("Daily SMS counters reset")