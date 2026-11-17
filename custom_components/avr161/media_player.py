"""Entité media_player pour le Harman Kardon AVR 161 (firmware custom >= 1.0.4).
Le mode surround (lecture et sélection) n'apparaît qu'avec un firmware qui le donne dans
get-status (>= 1.1.0)."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .client import AvrClient, AvrError
from .const import DOMAIN, VOLUME_STEPS, max_volume_percent

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=10)
UNAVAILABLE_AFTER = 2   # échecs consécutifs avant de déclarer l'ampli indisponible
PARALLEL_UPDATES = 1

# Code interne de la source (3e champ de get-status) -> libellé. Vérifié sur l'ampli le
# 2026-11-13 en sélectionnant chaque source (16 = Home Network DMR : décompilage seul).
SOURCE_CODES = {
    0: "Cable/Sat", 1: "Disc", 2: "MHL", 3: "AUX", 4: "Game", 5: "Set-Top Box",
    6: "Audio", 7: "TV", 8: "AM", 9: "FM", 10: "USB", 11: "iPod",
    12: "Home Network", 13: "vTuner", 16: "Home Network", 17: "Bluetooth",
}
# Libellé -> valeur `para` de la commande source-selection.
SOURCE_PARAMS = {
    "Cable/Sat": "Cable Sat", "Disc": "Disc", "MHL": "Media Server", "AUX": "AUX",
    "Game": "Game", "Set-Top Box": "STB", "Audio": "Audio", "TV": "TV", "FM": "FM", "AM": "AM",
    "USB": "USB", "iPod": "iPod", "Home Network": "Home Network", "vTuner": "vTuner",
    "Bluetooth": "Bluetooth",
}

# Code du mode surround (6e champ de get-status, para de set-surround) -> nom affiché par
# l'ampli (routine d'affichage du firmware). Plusieurs codes portent le même nom : ce sont des
# variantes selon le signal d'entrée et le nombre d'enceintes.
SOUND_MODE_CODES = {
    0: "Virtual Surround", 1: "Virtual Surr.2", 2: "2CH Stereo", 3: "2CH Stereo",
    4: "2CH Stereo", 5: "2CH Stereo", 6: "Dolby Digital", 7: "DTS", 8: "DTS 96/24",
    9: "5CH Stereo", 10: "Dolby Digital EX", 11: "DTS-ES Matrix", 12: "DTS-ES Discrete",
    13: "DTS Neo:6 Cinema", 14: "7CH Stereo", 15: "Logic 7 Movie", 16: "Dolby PLII Movie",
    17: "DTS Neo:6 Cinema", 18: "Logic 7 Movie", 19: "Dolby PLIIx Movie",
    20: "DTS Neo:6 Cinema", 21: "Logic 7 Music", 22: "Dolby PLII Music",
    23: "DTS Neo:6 Music", 24: "Logic 7 Music", 25: "Dolby PLIIx Music",
    26: "DTS Neo:6 Music", 27: "Logic 7 Game", 28: "Dolby PLII Game", 29: "Logic 7 Game",
    30: "Dolby PLIIx Game", 31: "Dolby PLIIz", 32: "Dolby PLIIz", 33: "Dolby PLIIz",
    37: "Dolby TrueHD", 38: "Dolby Digital Plus", 39: "DTS-HD Master", 40: "DTS-HD Hi Res",
    41: "DTS-HD LBR", 42: "Headphone Bypass", 43: "Harman Headphone", 44: "Multi-CH PCM",
    45: "Harman NSP",
}
# Modes proposés au choix : ceux que le défilement du firmware offre sur un ampli 5.1 (fonctions
# « mode suivant » de chaque catégorie), précédés de « Auto Select ». Nom -> codes à essayer
# dans l'ordre (variantes selon le signal) ; None = commande `set-surround auto`.
SOUND_MODE_AUTO = "Auto Select"
SOUND_MODE_PARAMS: dict[str, list[int] | None] = {
    SOUND_MODE_AUTO: None,
    "Virtual Surround": [0], "2CH Stereo": [2, 3, 5], "5CH Stereo": [9], "Harman NSP": [45],
    "Logic 7 Movie": [15, 18], "Dolby PLII Movie": [16], "DTS Neo:6 Cinema": [17, 13, 20],
    "Logic 7 Music": [21, 24], "Dolby PLII Music": [22], "DTS Neo:6 Music": [23, 26],
    "Logic 7 Game": [27, 29], "Dolby PLII Game": [28],
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities(
        [
            Avr161(
                entry.runtime_data,
                entry.entry_id,
                entry.title,
                max_volume_percent(entry.options),
            )
        ],
        update_before_add=True,
    )


class Avr161(MediaPlayerEntity, RestoreEntity):
    """L'ampli. Avec le firmware >= 1.0.5, get-status donne l'état d'alimentation (allumé /
    sleep). Avant, il est déduit des commandes envoyées par Home Assistant (et restauré au
    redémarrage) : l'entité est alors en `assumed_state`."""

    _attr_device_class = MediaPlayerDeviceClass.RECEIVER
    _BASE_FEATURES = (
        MediaPlayerEntityFeature.TURN_ON
        | MediaPlayerEntityFeature.TURN_OFF
        | MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.VOLUME_STEP
        | MediaPlayerEntityFeature.VOLUME_MUTE
        | MediaPlayerEntityFeature.SELECT_SOURCE
        | MediaPlayerEntityFeature.PLAY
        | MediaPlayerEntityFeature.PAUSE
        | MediaPlayerEntityFeature.NEXT_TRACK
        | MediaPlayerEntityFeature.PREVIOUS_TRACK
    )

    _attr_has_entity_name = True
    _attr_name = None            # l'entité porte le nom de l'appareil

    def __init__(self, client: AvrClient, entry_id: str, name: str, max_percent: int) -> None:
        self._client = client
        self._max_volume = max(1, round(VOLUME_STEPS * max_percent / 100))
        self._power = True
        self._power_reported = False
        self._standby = False
        self._firmware: str | None = None
        self._surround_reported = False
        self._decoding: str | None = None
        self._attr_sound_mode_list = list(SOUND_MODE_PARAMS)
        self._volume: int | None = None
        self._attr_unique_id = entry_id
        self._attr_source_list = list(SOURCE_PARAMS)
        self._attr_available = False
        self._failures = 0
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name=name,
            manufacturer="Harman Kardon",
            model="AVR 161",
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Firmware >= 1.1.0 : l'ampli pousse son état à chaque changement ; l'interrogation
        # périodique reste en secours et sert de signe de vie (le push s'arrête après 90 s
        # sans commande reçue).
        self._client.add_listener(self._on_status)
        last = await self.async_get_last_state()
        if last is not None and last.state in (MediaPlayerState.OFF, MediaPlayerState.STANDBY):
            self._power = False
            self._standby = last.state == MediaPlayerState.STANDBY

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        if self._surround_reported:
            return self._BASE_FEATURES | MediaPlayerEntityFeature.SELECT_SOUND_MODE
        return self._BASE_FEATURES

    @property
    def assumed_state(self) -> bool:
        return not self._power_reported

    @property
    def state(self) -> MediaPlayerState:
        if self._power:
            return MediaPlayerState.ON
        return MediaPlayerState.STANDBY if self._standby else MediaPlayerState.OFF

    @property
    def extra_state_attributes(self) -> dict:
        attrs: dict = {}
        if self._volume is not None:
            attrs["volume_db"] = -80 + 0.5 * self._volume
            attrs["volume_raw"] = self._volume
            attrs["volume_percent_of_range"] = round(self._volume * 100 / VOLUME_STEPS)
        if self._decoding:
            attrs["sound_decoding"] = self._decoding    # mode réellement appliqué par l'ampli
        if self._firmware:
            attrs["firmware"] = self._firmware
        return attrs

    def _on_status(self, status) -> None:
        self._apply(status)
        if self.hass is not None:
            self.async_write_ha_state()

    def _apply(self, status) -> None:
        self._volume = status.volume
        self._firmware = status.firmware
        self._power_reported = status.power is not None
        if status.power is not None:
            self._power = status.power
        self._standby = status.standby
        self._attr_volume_level = min(1.0, status.volume / self._max_volume)
        self._attr_is_volume_muted = status.muted
        self._attr_source = SOURCE_CODES.get(status.source, f"Source {status.source}")
        self._surround_reported = status.surround is not None
        if status.surround is not None:
            self._decoding = SOUND_MODE_CODES.get(status.surround, f"Mode {status.surround}")
            self._attr_sound_mode = SOUND_MODE_AUTO if status.surround_auto else self._decoding
        else:
            self._decoding = None
            self._attr_sound_mode = None
        self._attr_available = True
        self._failures = 0

    def _unreachable(self) -> None:
        """Un échec isolé est toléré : à chaque allumage depuis l'éteint, l'ampli relance son
        interface réseau une trentaine de secondes plus tard et coupe la connexion quelques
        secondes. Indisponible seulement au deuxième échec d'affilée."""
        self._failures += 1
        if self._failures >= UNAVAILABLE_AFTER:
            self._attr_available = False

    async def async_update(self) -> None:
        try:
            self._apply(await self._client.status())
        except AvrError as err:
            if self._attr_available:
                _LOGGER.info("Ampli injoignable : %s", err)
            self._unreachable()

    async def _send(self, name: str, para: str = "", settle: float = 0.4) -> bool:
        try:
            await self._client.command(name, para)
            await asyncio.sleep(settle)
            self._apply(await self._client.status())
        except AvrError as err:
            self._unreachable()
            _LOGGER.warning("Commande %s non envoyée : %s", name, err)
            return False
        return True

    async def async_turn_on(self) -> None:
        if await self._send("power-on", settle=2.0) and not self._power_reported:
            self._power = True

    async def async_turn_off(self) -> None:
        if await self._send("power-off", settle=2.0) and not self._power_reported:
            self._power = False

    async def async_volume_up(self) -> None:
        await self._send("volume-up")

    async def async_volume_down(self) -> None:
        await self._send("volume-down")

    async def async_mute_volume(self, mute: bool) -> None:
        await self._send("mute-on" if mute else "mute-off")

    async def async_set_volume_level(self, volume: float) -> None:
        target = round(max(0.0, min(1.0, volume)) * self._max_volume)
        try:
            status = await self._client.set_volume(target)
        except AvrError as err:
            self._unreachable()
            _LOGGER.warning("Volume non réglé : %s", err)
            return
        self._apply(status)
        if status.volume != target:
            _LOGGER.warning("Volume demandé %s, obtenu %s", target, status.volume)

    async def async_select_source(self, source: str) -> None:
        await self._send("source-selection", SOURCE_PARAMS.get(source, source), settle=1.5)

    async def async_select_sound_mode(self, sound_mode: str) -> None:
        if sound_mode not in SOUND_MODE_PARAMS:
            _LOGGER.warning("Mode surround inconnu : %s", sound_mode)
            return
        codes = SOUND_MODE_PARAMS[sound_mode]
        try:
            status = await self._client.set_surround(codes)
        except AvrError as err:
            self._unreachable()
            _LOGGER.warning("Mode surround non réglé : %s", err)
            return
        self._apply(status)
        if self._attr_sound_mode != sound_mode:
            _LOGGER.warning(
                "Mode surround %s refusé par l'ampli (impossible avec le signal en cours ?), "
                "mode actuel : %s",
                sound_mode,
                self._attr_sound_mode,
            )

    async def async_media_play(self) -> None:
        await self._send("play")

    async def async_media_pause(self) -> None:
        await self._send("pause")

    async def async_media_next_track(self) -> None:
        await self._send("next")

    async def async_media_previous_track(self) -> None:
        await self._send("previous")
