# hikvision_isapi_events

Home Assistant custom integration that subscribes to Hikvision ISAPI `alertStream` and creates event-driven entities grouped by **device-per-channel**.

> Events only: no camera, stream, snapshot, or go2rtc management entities are created.

## Device model in Home Assistant

For each config entry:

- One parent DVR device is created.
- One child device is created per channel, named like:
  - `Hikvision CH1`
  - `Hikvision CH7`

Each channel device contains 4 entities:

- `binary_sensor.hikvision_chX_motion`
- `binary_sensor.hikvision_chX_human`
- `binary_sensor.hikvision_chX_vehicle`
- `number.hikvision_chX_off_timeout`

Channel devices are created from discovered channels and also lazily whenever a new `channelID` appears in incoming events.

## Install

1. Copy the folder `custom_components/hikvision_isapi_events` into your Home Assistant config directory under:
   - `<config>/custom_components/hikvision_isapi_events`
2. Restart Home Assistant.

## Add integration

1. Go to **Settings → Devices & Services → Add Integration**.
2. Search for **Hikvision ISAPI Events**.
3. Fill in:
   - host, port, SSL
   - username and password
   - `default_off_delay_seconds` (0..1800)
   - `reconnect_delay_seconds`

Validation is done against `/ISAPI/System/deviceInfo`.

## Per-channel timeout number

`number.hikvision_chX_off_timeout` controls auto-off timeout per channel with:

- Min: `0`
- Max: `1800`
- Step: `1`

Behavior:

- `0` = no timer-based auto-off (wait for explicit inactive event)
- `> 0` = active events refresh timer; timer expiry turns all 3 binary sensors off

Changes apply immediately and persist across restarts.

## Event behavior summary

- Only `eventType == VMD` is handled.
- `eventState == active`
  - motion turns ON
  - human turns ON when `targetType == human`
  - vehicle turns ON when `targetType == vehicle`
  - OFF timer is scheduled/refreshed using the channel number value at event time
- `eventState == inactive`
  - motion/human/vehicle all turn OFF immediately
  - channel timer is canceled

## Migration notes

Existing config entries are preserved. If older entries used `per_channel_off_delay_overrides`, those values are migrated into per-channel persisted timeout storage on startup.

## Verify events with curl

You can verify the raw stream with curl Digest auth:

```bash
curl --digest -u 'USERNAME:PASSWORD' \
  'http://DEVICE_IP/ISAPI/Event/notification/alertStream'
```

Or device info validation endpoint:

```bash
curl --digest -u 'USERNAME:PASSWORD' \
  'http://DEVICE_IP/ISAPI/System/deviceInfo'
```
