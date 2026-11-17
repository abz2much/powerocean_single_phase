# Changelog

## 1.0.1

### Fixed

- **Impossible power values with the option "Extended connection (MQTT)" are
  ignored.** On a PowerOcean Plus, some live power reports showed values like
  592 491 967 730 423 000 000 000 000 000 000 W for a few seconds, in battery,
  grid, solar and house power and in the calculated energy flows. A live power
  report with a value that is not a number or is above 1 MW is now dropped as
  a whole, and the sensors keep their last values. Without the MQTT option
  nothing changes. Found by niltrip in
  [niltrip/powerocean#189](https://github.com/niltrip/powerocean/issues/189),
  fixed there in 2026.11.17.

## 1.0.0

### Breaking changes

- **New domain `powerocean_single_phase`.** This is a separate integration,
  not an update of `powerocean`. Install it, then remove the old entry and
  add this one with the same serial number and device name. Unique IDs
  (`{serial}_...`) are the same as before. The devices are new, so
  automations and dashboards that refer to a device ID need the new one.
  Areas, labels and renamed entity IDs are not carried over.
- **English only.** The German and French translations are removed. The
  attribute `product_description` of the sensors, for example "Hausnetz" or
  "Ladezyklen", and the attribute `error_details` of the error count are now
  English ("House grid", "Charge cycles").

### New

- **Scheduled tasks (with the MQTT option):** the "Scheduled tasks" of the
  EcoFlow app, as the app sends them. The integration reads the list every
  minute and after every command, because the system moves from one
  schedule to the next by itself and reports no change.
  - One **switch** for each schedule, named after what it does, for example
    "Charge 02:00-06:00 3000 W". The name follows the schedule; the entity ID
    ends in `_schedule_{id}` and does not. Attributes: `id`, `type`, `start`,
    `end`, `power`, `days`, `in_use`.
  - A sensor **Active schedule** with the schedule in use, or `none`.
  - The actions `powerocean_single_phase.add_schedule` (the field
    `device_id`: the inverter device), `update_schedule` and
    `delete_schedule` (target: a schedule switch). Times are in steps of 30
    minutes. Each command waits for the acknowledgement of the system and for
    its list to show the change, and gives an error if it does not.
- **Option "Max schedule power (W)":** a whole number from 1, with no
  default. Without it, `add_schedule` and `update_schedule` refuse. The
  power of a schedule can be at most this value.

Schedules may not overlap at any time of day (the days and on or off do not
matter; back to back is fine), and there can be at most 6, as in the app. The
inverter itself accepts overlaps, so the integration refuses them. Not known,
so not checked: which power the inverter allows.

### Changed

- The sensor **Active schedule** appears once the first list of schedules
  is read, and its entity ID uses the device name instead of the serial
  number.
- The licence names niltrip and abz2much; the README has a "Credit and
  licence" section. `scripts/deploy` copies the new folder. The comments,
  docstrings and the texts of the scripts are English.
- One CI workflow (`ci.yml`) runs ruff, pytest, hassfest and HACS validation.
  A release is published when a pull request that changes the version in
  `manifest.json` is merged and CI passes. The description of the pull request
  is the release notes. The German release notes template is removed.
- The README is short now. The details moved to pages in `docs/`, and the
  screenshots to `docs/images/`. The script `check_response.py` moved from
  `documentation/` to `scripts/`.

## 2026.11.14

### New

- **LED brightness of the inverter (with the MQTT option):** a slider from
  0 to 100 % in steps of 10, as in the EcoFlow app. The system confirms
  each change; without confirmation Home Assistant shows an error. 0 %
  switches the LEDs off.
- **Backup reserve (with the MQTT option):** a slider from 0 to 100 % in
  steps of 1, as in the EcoFlow app (`sysBatDsgDownLimit`). The app's
  command also carries the charge limit and `devSoc`; the integration sends
  their current values unchanged and does not send at all while it does not
  know them.
- **Surplus solar energy delivery (with the MQTT option):** a slider from 0
  to 100 % in steps of 1, as in the EcoFlow app (`devSoc`). Same command as
  the backup reserve; the other values are sent unchanged.
- **Battery save mode (with the MQTT option):** a switch, as in the EcoFlow
  app. The system reports the state only via MQTT; the switch is
  unavailable until the first report.
- **Live power values (with the MQTT option):** a second option asks the
  system for its power values every 20 s, as the EcoFlow app does while it
  is open. Solar, grid, battery and house power, the PV strings and the
  calculated energy flows then update every ~5 s in the existing sensors,
  without an open app. No new entities. Off by default; not yet for dual
  installations.
- **`scripts/capture`** records the MQTT messages of the system while you
  use the EcoFlow app, masked and ready to attach to an issue, to help decode
  further functions. It only listens and sends nothing to the system.

### Changed

- **Ready for Home Assistant 2027.8:** the integration no longer looks up
  its devices, nor links batteries and other devices to the inverter, in a
  way Home Assistant 2026.10 marks as deprecated and 2027.8 removes (log
  warning about `via_device`). Nothing changes for you; Home Assistant
  2025.8 or newer is still enough. The tests now run on Home Assistant
  2026.10.

## 2026.10.28

### Breaking changes

- **Battery firmware is shown on the device page.** Each battery pack shows
  its firmware version under *Firmware* on its device page, updated after a
  firmware update without a restart. The diagnostic sensor "Firmware
  version" (`moduleAplSwVer`) is removed automatically.
- **No serial number sensors.** The serial number of each battery pack,
  heating rod, wallbox and inverter is shown in its device info. The
  sensors `bpSn`, `hrSn`, `evSn` and `devSn` repeated it and are removed
  automatically.
- **Battery and wallbox states are now lowercase.** The states of
  `bmsChgDsgSta`, `bmsRunSta` and `bpSysState` change, for example from
  `PB_CHG_STATE` to `pb_chg_state`, and the charging status of the
  PowerPulse from `EV_CHG_STS_AVAILABLE` to `ev_chg_sts_available`, so Home
  Assistant can show them translated ("Charging", "Laden"; "Available",
  "Verfügbar"). Update automations and templates that compare these states
  with the old uppercase values. The history keeps the old values up to the
  update.
- **Wallbox `orderStartTimestamp` is a timestamp.** It was shown as a current
  in A by mistake. Home Assistant reports under *Settings → Repairs* that
  the stored statistics no longer match and offers to delete them.
- **Battery `bpRemainWatth` ("Remaining energy") is a stored energy.** It
  is a fill level that rises and falls like the state of charge, not an
  energy meter. Its long-term statistics now keep mean, minimum and maximum
  instead of a sum, and the energy dashboard no longer offers it. Home
  Assistant reports under *Developer tools → Statistics* that the state
  class changed and offers to fix the old statistics.
- **Some sensors moved to the "Diagnostic" section** of their device page:
  values per phase, voltage and current of the PV strings, battery voltage,
  current and temperatures, and values that are also shown as calculated
  energy flow (`sysLoadPwr`, `sysGridPwr`, `mpptPwr`, `pcsMeterPower`, …).
  Entity IDs, history, statistics, automations and your own dashboards are
  not affected. Only the automatically generated default dashboard does not
  show diagnostic entities.

### New

- **More datapoints:** operating mode, inverter state, grid frequency, current
  power limits, feed-in limit, backup reserve, charge and discharge limit,
  §14a EnWG, SG Ready, connection state, error and warning codes, counters of
  grid protection trips, load management of the house connection, and per
  battery pack the highest and lowest cell voltage.
- **Smart meter** as its own device with the power of each phase and the
  total (three-phase meters of type 11 and 14).
- **PowerPulse 2 wallbox:** charging current settings (custom, maximum,
  solar minimum) in A, energy and duration of the last charging session,
  phase setting. These values were in the response but not read before.
  Value mappings and units from the diagnostics and the
  [PowerGlow](https://github.com/Xygen/EcoFlow-PowerGlow-for-Home-Assistant)
  and
  [PowerPulse 2](https://github.com/Xygen/EcoFlow-PowerPulse-2-for-Home-Assistant)
  integrations by Xygen (discussion #132) – thanks!
- **Energy management per device:** wallbox and heating rod each show their
  maximum power, priority and allocated power, and their error and warning
  code of the energy management ("(energy management)"). Before, only the
  first device got these values. The values of the whole system from the
  same report (`socCur`, `feedPwrCap`, `pclPwrBase`, `startState`) moved
  from the wallbox to the inverter, disabled by default; the old wallbox
  entities are removed automatically.
- **Time of the last values** per battery pack and per report of the
  inverter (diagnostic). Shows when the data from the EcoFlow cloud is out
  of date.
- **New devices and datapoints appear without a restart**, e.g. a battery
  pack that was offline when Home Assistant started.
- **Translated states** (English, German) for operating mode, feed-in mode,
  grid state (on-grid/off-grid), inverter and battery states, on/off
  values, mode and state of the PowerGlow heating rod, charging mode and
  charging status of the PowerPulse wallbox, as far as possible with the
  texts of the EcoFlow web portal. The operating mode also knows AI mode,
  backup, schedule, battery calibration and charging from grid, the feed-in
  mode "off" (thanks to shuette42/ecoflow-energy-ha for the value lists).
  Unknown values are shown as they are.
- **EMS work state** (diagnostic, disabled by default): what the system is
  doing right now – idle, starting (battery, solar, grid), running, stopped,
  maintenance.
- **Firmware of the inverter and the smart meter (optional, MQTT).** The new
  option *Extended connection (MQTT)*, off by default, connects like the
  EcoFlow app and shows the firmware of the energy management on the
  inverter and the firmware of the smart meter on their device pages. It
  updates after a firmware update. All other modules are in the
  diagnostics. The values still come from the regular polling.
- **Link to the EcoFlow web portal** on every device page.
- **Readable entity names** in English and German, e.g. "State of charge"
  instead of `bpSoc`. Entity IDs stay the same, also for new installations,
  and names you set yourself are kept. Values whose meaning is unknown keep
  their key as name. Values the cloud only refreshes while the EcoFlow app
  (or a PowerInsight display) is open end in "(cloud)"; use the calculated
  values without suffix instead. In dual systems, the values per inverter
  from the parallel report end in "(energy flow)". This includes the
  PowerGlow heating rod and the PowerPulse wallbox; heating rod values from
  its energy stream report end in "(cloud)" as well.
- **Devices that are gone can be deleted**, e.g. a replaced battery pack:
  *device page → ⋮ → Delete*. Only possible while the device sends no
  values. If it returns, it is added again.

### Changed

- **Backup reserve has the right name.** The backup reserve you set in the
  EcoFlow app is `sysBatDsgDownLimit`: the sensor "Discharge limit" is now
  called "Backup reserve (discharge limit)". The sensor that was called
  "Backup reserve" (`sysBatBackupRatio`, disabled by default) does not follow
  the app setting and is now called "Backup ratio (not in the app)". Entity
  IDs stay the same; names you changed yourself are kept.
- **Fewer diagnostic sensors.** Rarely needed sensors are disabled by default
  (connection codes, error and warning codes of inverter and PV strings,
  protection counters, states without known meaning, internal battery
  temperatures, settings that hardly ever change, reactive and apparent power
  per phase, PV strings the system does not have). You can enable them under
  *Settings → Entities*.
  - **If you update:** sensors you already have stay as they are, nothing
    disappears. Sensors added with this version arrive disabled.
  - **New installations:** all of these sensors start disabled.
- Percentages and litres are shown without decimals, currents and
  insulation resistance with one; the duration of the last charging session
  of the PowerPulse in minutes. Stored values keep their full precision.
- **Heating rod and wallbox have one name each:** "PowerGlow" and
  "PowerPulse". Before, depending on which report arrived first, they were
  also called "Heating Rod" or "Charger". Device pages may show the new
  name; entity IDs you already have stay as they are.
- **Units for heating rod and wallbox:** the charging current setting of the
  PowerPulse is shown in A (6.0 A instead of 60), the energy target of smart
  charging in Wh, the planned departure as a time (unknown if none is set).
  The self-check of the PowerGlow no longer shows a battery icon.
- Battery state of charge shows the battery level icon.
- Sensors without a current value, e.g. of a battery pack that is offline,
  are shown as *unavailable* instead of *unknown*.
- The debug log no longer contains the complete response of the EcoFlow
  cloud. It is in the diagnostics, with serial numbers replaced.
