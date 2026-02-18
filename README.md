# hikvision_isapi_events

Home Assistant custom integration that subscribes to Hikvision ISAPI `alertStream` and creates event-driven binary sensors for each discovered channel:

- `binary_sensor.hikvision_chX_motion`
- `binary_sensor.hikvision_chX_human`
- `binary_sensor.hikvision_chX_vehicle`

> No camera entities, no RTSP handling, and no snapshots are included.

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
   - optional multiline `per_channel_off_delay_overrides` using `channel=seconds`
   - `reconnect_delay_seconds`

Validation is done against `/ISAPI/System/deviceInfo`.

## Where to find entities

Entities are automatically created for discovered channels (or lazily as events arrive):

- `binary_sensor.hikvision_ch1_motion`
- `binary_sensor.hikvision_ch1_human`
- `binary_sensor.hikvision_ch1_vehicle`
- etc.

Each sensor includes attributes:

- `channel_id`
- `last_event_datetime`
- `last_event_state`
- `last_target_type`
- `last_event_type`

## Event behavior summary

- Only `eventType == VMD` is handled.
- `eventState == active`
  - motion turns ON
  - human turns ON when `targetType == human`
  - vehicle turns ON when `targetType == vehicle`
- `eventState == inactive`
  - motion/human/vehicle all turn OFF immediately

Per-channel off delay logic:

- delay `0`: no timer auto-off, waits for inactive event
- delay `> 0`: active events refresh a channel timer; timer expiry turns all three sensors off

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
