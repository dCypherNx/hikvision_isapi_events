"""Constants for Hikvision ISAPI Events."""

from __future__ import annotations

DOMAIN = "hikvision_isapi_events"
PLATFORMS = ["binary_sensor", "number"]

CONF_USE_SSL = "use_ssl"
CONF_DEFAULT_OFF_DELAY_SECONDS = "default_off_delay_seconds"
CONF_PER_CHANNEL_OFF_DELAY_OVERRIDES = "per_channel_off_delay_overrides"
CONF_RECONNECT_DELAY_SECONDS = "reconnect_delay_seconds"

DEFAULT_PORT = 80
DEFAULT_USE_SSL = False
DEFAULT_OFF_DELAY_SECONDS = 30
DEFAULT_RECONNECT_DELAY_SECONDS = 5

MIN_OFF_DELAY_SECONDS = 0
MAX_OFF_DELAY_SECONDS = 1800

SENSOR_TYPES = ("motion", "human", "vehicle")
EVENT_TYPE_VMD = "VMD"

ALERT_STREAM_PATH = "/ISAPI/Event/notification/alertStream"
DEVICE_INFO_PATH = "/ISAPI/System/deviceInfo"
CHANNEL_DISCOVERY_PATHS = (
    "/ISAPI/System/Video/inputs/channels",
    "/ISAPI/ContentMgmt/InputProxy/channels",
)

DATA_RUNTIME = "runtime"

ATTR_CHANNEL_ID = "channel_id"
ATTR_LAST_EVENT_DATETIME = "last_event_datetime"
ATTR_LAST_EVENT_STATE = "last_event_state"
ATTR_LAST_TARGET_TYPE = "last_target_type"
ATTR_LAST_EVENT_TYPE = "last_event_type"

DVR_DEVICE_KEY = "dvr"
STORAGE_VERSION = 1
