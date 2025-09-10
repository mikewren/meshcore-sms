"""Constants for MeshCore SMS Gateway."""

DOMAIN = "meshcore-sms"

# Configuration keys
CONF_ACCOUNT_SID = "account_sid"
CONF_AUTH_TOKEN = "auth_token"
CONF_FROM_NUMBER = "from_number"
CONF_DAILY_LIMIT = "daily_limit"
CONF_BOT_NAME = "bot_name"
CONF_ENABLE_BROADCAST = "enable_broadcast"
CONF_DELIVERY_CONFIRMATION = "delivery_confirmation"

# Default values
DEFAULT_BOT_NAME = "sms_bot"
DEFAULT_DAILY_LIMIT = 50
DEFAULT_ENABLE_BROADCAST = True
DEFAULT_DELIVERY_CONFIRMATION = False

# Service names
SERVICE_SEND_SMS = "send_sms"
SERVICE_BROADCAST_TO_MESH = "broadcast_to_mesh"

# Events
EVENT_SMS_RECEIVED = f"{DOMAIN}_sms_received"
EVENT_SMS_SENT = f"{DOMAIN}_sms_sent"

# Attributes
ATTR_PHONE_NUMBER = "phone_number"
ATTR_MESSAGE = "message"
ATTR_SENDER = "sender"
ATTR_RECIPIENT = "recipient"
ATTR_TIMESTAMP = "timestamp"

# Rate limiting
STORAGE_KEY = f"{DOMAIN}_data"
STORAGE_VERSION = 1