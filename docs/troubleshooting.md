# Troubleshooting

## Diagnostics

When you report a problem, please attach the diagnostics. Go to Settings, Devices & services, PowerOcean Single Phase, then the three dots, then Download diagnostics.

The file holds the last response of the EcoFlow cloud and the detected devices. Credentials, system name, location and time zone are removed. Serial numbers are replaced by placeholders (`SN_MAIN`, `SN_BP01` and so on).

## Login

If EcoFlow rejects your credentials, for example after you changed your password, Home Assistant asks you to sign in again. If the cloud cannot be reached, the integration retries on its own.

## Debug logging

For more detail, set the logging of this integration to debug. Remove it again when everything works:

```yaml
logger:
  default: warn
  logs:
    custom_components.powerocean_single_phase: debug
```

## Recording MQTT messages

To help decode a function of the EcoFlow app, for example a wallbox or heating rod setting, you can record what the app sends and how the system answers. You need a computer with a copy of this repository and its Python environment (`scripts/setup`).

1. Run `scripts/capture`. It asks for your serial number, e-mail and password, signs in like the EcoFlow app and only listens. It sends nothing to your system.
2. When it says "Verbunden" (connected), open the app and use the function. After each step, type a short note and press Enter, for example `LED 40 %`. The notes mark the moment in the recording.
3. Type `q` and press Enter to end the recording. You can attach the file `measurements/capture-….jsonl` to an issue or discussion.

Serial numbers keep their length but are replaced (the inverter's keeps its first four characters, the model). User ID, e-mail and account are removed. At the end, the file is checked for them and deleted if one is left. The recording still shows the power, battery and energy values of that time. Read your notes before you upload the file.

To avoid typing your credentials each time, put them in `~/.config/powerocean/developer.env`, outside the repository and readable only by you (`chmod 600`):

```
ECOFLOW_SN=…
ECOFLOW_EMAIL=…
ECOFLOW_PASSWORD=…
ECOFLOW_VARIANT=83
```
