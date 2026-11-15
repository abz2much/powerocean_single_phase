# Scheduled tasks

With the extended connection (MQTT) on, the integration shows the "Scheduled tasks" of the EcoFlow app and changes them the same way the app does. See [Extended connection](extended-connection.md) for how to turn it on.

The system keeps the schedules itself and moves from one to the next without reporting a change. So the integration reads the list every minute, and again after every command.

## What appears in Home Assistant

- A switch for each schedule, named after what it does, for example "Charge 02:00-06:00 3000 W". The name follows the schedule. The entity ID, `switch.{device_name}_schedule_{id}`, does not. Switching it sends the same command as the app.
- A sensor called Active schedule. It shows what the schedule in use does, or `none`.
- The option Max schedule power (W). It has no default.

The switches and the sensor have these attributes: `id`, `type` (`charge` or `use_battery`), `start`, `end`, `power` (W), `days` (`daily` or a list from `mon` to `sun`) and `in_use`.

## Actions

| Action | Target | What it does |
|---|---|---|
| `powerocean_single_phase.add_schedule` | the inverter device | Adds a schedule |
| `powerocean_single_phase.update_schedule` | a schedule switch | Changes a schedule |
| `powerocean_single_phase.delete_schedule` | a schedule switch | Deletes a schedule |

```yaml
action: powerocean_single_phase.add_schedule
target:
  device_id: <the inverter device>
data:
  type: charge          # or use_battery
  power: 3000
  start: "02:00:00"
  end: "06:00:00"
  days: [mon, tue, wed, thu, fri]   # leave out for every day
```

`update_schedule` sends the whole schedule and keeps what you leave out. `daily: true` repeats the schedule every day. Do not use it together with `days`.

## Rules

- Without the option Max schedule power (W), `add_schedule` and `update_schedule` refuse. The power must be a whole number from 1 up to that value.
- Times are in steps of 30 minutes, from 00:00 to 23:30. The end may be before the start, which means the schedule runs over midnight. It may not be equal to the start.
- Schedules may not overlap at any time of day, whatever their days are and whether they are on or off. Back to back is fine.
- There can be at most six schedules, as in the app.
- A new schedule gets the next free number, as in the app.
- Each command waits until the system's list shows the change. If the system does not confirm it, Home Assistant shows an error.

The system itself accepts overlapping schedules, which is why the integration refuses them. The app has no type for exporting to the grid. The system exports by itself when the battery is full.
