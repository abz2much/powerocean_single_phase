# Extended connection (MQTT)

This option is off by default. To turn it on, go to Settings, Devices & services, PowerOcean Single Phase, then Configure, and choose Extended connection (MQTT).

The integration then also connects to the EcoFlow cloud the way the EcoFlow app does, using the same account, and asks the system for its full state. It does this after connecting, after each reconnect and every 6 hours.

## Firmware

From the answer, the integration reads the firmware of the modules:

- Inverter: the firmware of the energy management system (EMS), for example 5.1.38.5. The "system version" in the app, for example 5.1.37.13, is the name of the whole update package and is not sent.
- Smart meter: its firmware.
- The other modules (the inverter power stage and Wi-Fi) are listed in the diagnostics.

## Settings you can change

The integration sends the same commands as the app and waits for the system to confirm. If the system does not confirm within 10 seconds, Home Assistant shows an error. A change made in the app shows up right away.

- LED brightness: a slider from 0 to 100 % in steps of 10, under Configuration on the device page. 0 % switches the LEDs off.
- Backup reserve: a slider from 0 to 100 % in steps of 1. The system keeps it as the lowest state of charge for discharging (`sysBatDsgDownLimit`, shown as the sensor "Backup reserve (discharge limit)"). The app's command also holds the charge limit and the surplus solar energy delivery (`devSoc`). The integration sends their current values unchanged, so only the backup reserve changes. Until it knows them, for example right after a start, the slider is unavailable.
- Surplus solar energy delivery: a slider from 0 to 100 % in steps of 1 that sets `devSoc`. It sends the charge limit and the backup reserve unchanged.
- Battery save mode: a switch for the app's battery save mode. The system reports this setting only over MQTT, so the switch is unavailable until the first report after connecting. The command also holds the time zone of the system (`sysZone`), which the integration sends unchanged.
- Scheduled tasks: see [Scheduled tasks](scheduled-tasks.md).

These are the only settings the integration changes. All other values come from the regular polling.

The cloud shows a changed setting only 20 to 60 seconds later. Until then, for at most 2 minutes, the integration keeps the value the system confirmed over MQTT, so a slider does not jump back.

If the connection fails, the integration keeps working and logs a warning. The sliders and switches are unavailable until it connects again. When you turn the option off, the sliders, the switches and the firmware of the inverter and the smart meter are removed.

## Live power values

A second option, Live power values (MQTT), is also off by default and needs the extended connection.

With it on, the integration asks the system for its power values every 20 seconds, as the app does while it is open. The system answers about every 5 seconds with solar, grid, battery and house power, the power of the PV strings and the state of charge.

- The existing sensors show these values right away. You do not need the app or a PowerInsight display open. No new entities are added.
- This covers house load, grid power, solar power and battery power (`sysLoadPwr`, `sysGridPwr`, `mpptPwr`, `bpPwr`), the PV strings and the calculated energy flows.
- Between two answers, polling shows the older values from the cloud. For 30 seconds after an answer, the integration keeps the live values.
- Systems with two inverters are not supported yet. There the option has no effect.
- It is the only request the integration sends regularly, and it changes nothing on the system.
- A polling interval of 20 to 30 seconds is enough with this option. The other values change in the cloud only every 20 to 60 seconds anyway.
