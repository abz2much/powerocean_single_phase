# Which values update

The EcoFlow cloud does not refresh every value at the same rate. Some keep returning the same number for hours. The integration reports what the API hands it, so a flat line here is not a bug in this integration.

## Measurements

These were taken on 2026-10-25 with `scripts/interval`, one request every 10 seconds, on a three-phase PowerOcean with two batteries. First two hours with the app closed and the PowerInsight display switched off, then seven minutes with the PowerInsight display on.

| Group | Reports | Changes in 2 h | Interval between changes |
|---|---|---|---|
| Live measurements: grid, solar, battery and phase power | `EMS_HEARTBEAT` | 118 | 40 to 80 s, typically 50 s |
| State, settings and counters of the system | `EMS_CHANGE_REPORT` | 289 | 20 to 30 s |
| Power distribution to wallbox and heating rod | `EDEV_SYS_REPORT` | 98 | Irregular, 20 s to 8.5 min, typically 40 s |
| Battery packs | `BP_STA_REPORT` | 28 | About 5 min |
| Cloud values | `data`, `ENERGY_STREAM_REPORT`, `EMS_PV_INV_ENERGY_STREAM_REPORT` | 7 to 9 | See below |
| Parameters and errors | `EMS_PARAM_CHANGE_REPORT`, `ERROR_CHANGE_REPORT`, `LOGY_DEV_REPORT` | 0 | Content unchanged (no errors during the measurement). Only `updateTime` is renewed every 15 min |
| Configuration: schedules, time-of-use tasks, device lists, priorities | `TOU_ALL_TASK_REPORT`, `*_TIMER_TASK_REPORT`, `*_BIND_LIST_REPORT`, `EDEV_PRIORITY_LIST_REPORT`, `ERROR_CODE_MASK_REPORT` | 0 | Change only when a setting changes |

The cloud values, with the app closed and the display off, changed once, for 2.5 minutes, every 25 seconds (cause unknown). Otherwise they stayed the same. The two other changes of `data` came about an hour apart (energy totals). With the PowerInsight display on, they changed every 20 to 30 seconds.

## What this means

- The values that matter for a dashboard (`EMS_HEARTBEAT` and `EMS_CHANGE_REPORT`) arrive every 20 to 80 seconds on their own. The default scan interval of 10 seconds catches each change. A shorter one brings no fresher data.
- The values from `data` and `ENERGY_STREAM_REPORT` stay old while nothing asks the cloud for them. An open app or a PowerInsight display that is switched on makes the cloud refresh them every 20 to 30 seconds. With the option Live power values (MQTT), the integration asks for them itself and gets the power values about every 5 seconds. See [Extended connection](extended-connection.md).
- Over 72 hours, @Kantripal saw `mpptPwr` and `pv1Pwr` stuck at 0 W while `mpptPv_pwrTotal` from the heartbeat changed more than 2100 times. On 2026-08-21 at 13:16, the same response held 0 W and 4238 W for the same solar power.
- Battery pack values (cell voltages, temperatures, state of health) lag by up to 5 minutes. For the battery's state of charge and power, use the system values from `EMS_CHANGE_REPORT` and `EMS_HEARTBEAT`.
- `updateTime` in the reports is in UTC+8 (China time). The integration converts it for the "time of the last values" sensors.

## Which sensors to use

For a dashboard, take the values from `EMS_HEARTBEAT`:

| For | Use | Instead of |
|---|---|---|
| Grid | `pcsMeterPower` (positive is import, negative is export) | `sysGridPwr` |
| Solar | `mpptPv_pwrTotal` (the sum of the PV strings) | `mpptPwr`, `pv1Pwr` or `pvInvPwr` |
| Battery | `emsBpPower` | `bpPwr` |
| House | `housePower`, calculated from the three values above | |

The friendly names help. Values from `data` and `ENERGY_STREAM_REPORT` end in "(cloud)", for example "House consumption (cloud)". The calculated values have no suffix, for example "House consumption".

In systems with a master and a slave inverter, the values from `PARALLEL_ENERGY_STREAM_REPORT` end in "(energy flow)". According to issue #23 of the original repository, they are updated regularly, unlike those from `data`.
