# Harman Kardon AVR 161 for Home Assistant

[![Built with Claude Code](https://img.shields.io/badge/built%20with-Claude%20Code-D97757)](https://claude.com/claude-code)

A Home Assistant integration for the **Harman Kardon AVR 161 / AVR 1610** receiver, controlled
over the local network.

> [!IMPORTANT]
> This integration needs the **custom firmware** from
> [delphiki/avr161-custom-fw](https://github.com/delphiki/avr161-custom-fw) (v1.0.4 or later,
> **v1.2.0 recommended**). The stock firmware cannot report its state, so the integration
> refuses to set up a receiver that runs it.

> [!NOTE]
> **This project is 100% vibe-coded**: it was produced by an AI coding assistant, steered by
> the owner and checked against one real receiver.

## What you get

One `media_player` entity per receiver:

| | |
|---|---|
| **Power** | Turn on, turn off (the receiver goes to sleep). States: on, standby, off |
| **Volume** | Absolute level, step up / down, mute |
| **Source** | Cable/Sat, Disc, MHL, AUX, Game, Set-Top Box, Audio, TV, FM, AM, USB, iPod, Home Network, vTuner, Bluetooth |
| **Sound mode** | Auto Select, stereo, Dolby PLII, DTS Neo:6, Logic 7, Harman NSP… (firmware 1.1.0+) |
| **Transport** | Play, pause, next, previous |
| **Attributes** | Volume in dB, raw volume, the decoding actually applied, firmware version |

State changes made with the remote or the front panel show up within a couple of seconds:
the receiver pushes them (firmware 1.1.0+), and the integration also polls every 10 seconds.

A **maximum volume** option maps the 100% of the Home Assistant slider to a share of the
receiver's range, so a slip of the finger cannot go to full power. Default: 78%, that is −10 dB.

## Installation

### HACS (recommended)

[![Open your Home Assistant instance and open this repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=delphiki&repository=ha-harman-kardon-avr161&category=integration)

1. Click the button above, or in HACS open the ⋮ menu → **Custom repositories**, add
   `https://github.com/delphiki/ha-harman-kardon-avr161` with the type **Integration**.
2. Open **Harman Kardon AVR 161 (custom firmware)** in HACS and click **Download**.
3. Restart Home Assistant.

### Manual

Copy the `custom_components/avr161` folder into the `custom_components` folder of your Home
Assistant configuration, then restart Home Assistant.

## Setup

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=avr161)

1. Click the button above, or go to **Settings → Devices & services → Add integration** and
   search for **Harman Kardon AVR 161**.
2. Enter the receiver's **IP address** (give it a fixed address on your router), a name, and
   the maximum volume.

The receiver must be on, or in sleep, when you add it. The maximum volume can be changed later
with **Configure**.

## Good to know

- **One network client at a time.** The receiver serves a single connection and the
  integration keeps it open. While Home Assistant is connected, other clients (the official
  app, `ampli.py`) are refused. Disable the integration to use them.
- **"Turn off" puts the receiver to sleep.** With firmware 1.2.0 it then turns itself off
  after an hour and stays reachable, so it can still be turned on from Home Assistant.
- **Standby vs off** needs firmware 1.2.0. Older firmware reports both as off.
- After a mains power cut, the receiver needs about a minute before it answers again.
- One failed exchange is tolerated: the entity only becomes unavailable after two in a row.

## Troubleshooting

- *"The receiver answers but not to get-status"*: the custom firmware is not installed.
- *"Receiver unreachable"*: check the address, and that no other client is connected. With a
  firmware older than 1.2.0, a receiver that is fully off is not on the network.
- A sound mode is refused: the receiver only accepts the modes that are possible with the
  current input signal.
