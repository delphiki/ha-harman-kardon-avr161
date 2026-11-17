"""Client asyncio du protocole réseau de l'AVR 161 (HTTP POST + XML sur TCP 10025).

Particularités de l'ampli, constatées sur le matériel :
- seules `heart-alive` et `get-status` (firmware custom) reçoivent une réponse ;
- à partir du firmware 1.1.0, l'ampli envoie aussi de lui-même un message `get-status` quand
  son état change, tant qu'un client lui a parlé dans les 90 dernières secondes ;
- à partir du firmware 1.1.0, `get-status` donne aussi le mode surround appliqué (6e champ)
  et la catégorie mémorisée pour la source (7e champ, 0 = Auto Select) ;
  `set-surround <code>` sélectionne un mode, `set-surround auto` revient à Auto Select ;
  l'ampli ignore un mode impossible avec le signal en cours ;
- la première requête d'une connexion neuve est souvent perdue : on envoie `heart-alive`
  jusqu'à obtenir une réponse avant toute commande, puis on garde la connexion ;
- une commande arrivant moins de ~0,5 s après la précédente est ignorée.
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass

PORT = 10025
COMMAND_GAP = 0.7        # délai minimal entre deux commandes (cooldown du dispatch)
CONNECT_TIMEOUT = 3.0
REPLY_TIMEOUT = 1.5
SETVOL_TRIES = 3
# Une requête envoyée dans les ~100 ms qui suivent la connexion est perdue une fois sur deux
# (mesuré sur l'ampli). Le port ne sert qu'UN client : une nouvelle connexion ferme l'ancienne.
CONNECT_SETTLE = 0.3

_NAME = re.compile(rb"<name>([^<]*)</name>")
_PARA = re.compile(rb"<para>([^<]*)</para>")


def split_messages(buffer: bytes) -> tuple[list[tuple[str, str]], bytes]:
    """Découpe ce que l'ampli a envoyé en messages (name, para) ; retourne aussi le reste non
    consommé. Le cadrage exact des réponses n'étant pas garanti, on s'appuie sur les balises :
    un <name>, puis son <para> s'il arrive avant le <name> suivant. Un `get-status` dont le
    <para> n'est pas encore arrivé est laissé dans le reste (message coupé en deux)."""
    messages: list[tuple[str, str]] = []
    pos = 0
    while (name := _NAME.search(buffer, pos)) is not None:
        following = _NAME.search(buffer, name.end())
        limit = following.start() if following else len(buffer)
        para = _PARA.search(buffer, name.end(), limit)
        if para is None and following is None and name.group(1) == b"get-status":
            return messages, buffer[name.start():]
        messages.append((name.group(1).decode(), para.group(1).decode() if para else ""))
        pos = para.end() if para else name.end()
    return messages, buffer[pos:][-512:]


class AvrError(Exception):
    """L'ampli est injoignable ou ne répond pas."""


@dataclass
class Status:
    volume: int          # 0..180, dB = -80 + 0,5 * volume
    muted: bool
    source: int          # code interne de la source
    firmware: str | None
    power: bool | None   # None si le firmware (< 1.0.5) ne donne pas l'alimentation
    surround: int | None = None   # code du mode surround appliqué ; None avant le firmware 1.1.0
    surround_auto: bool | None = None   # True si la source est en « Auto Select »
    standby: bool = False   # en veille, pas encore éteint (firmware >= 1.2.0 ; avant, jamais vrai)


def build_request(name: str, para: str = "") -> bytes:
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<harman>\n<avr>\n<common>\n<control>\n'
        f"<name>{name}</name>\n<zone>Main Zone</zone>\n<para>{para}</para>\n"
        "</control>\n</common>\n</avr>\n</harman>"
    ).encode()
    head = (
        "POST AVR HTTP/1.1\r\nHost: :10025\r\n"
        "User-Agent: Harman Kardon AVR Remote Controller /2.0\r\n"
        f"Content-Length: {len(xml)}\r\n\r\n"
    ).encode()
    return head + xml


def parse_status(para: str) -> Status:
    vol, mute, src, fw, pwr, sur, cat = (para.split(",") + [""] * 7)[:7]
    return Status(
        int(vol),
        bool(int(mute)),
        int(src),
        fw or None,
        (int(pwr) == 1) if pwr else None,   # 1 = allumé, 2 = en veille, 0 = éteint
        int(sur) if sur else None,
        (int(cat) == 0) if cat else None,
        standby=bool(pwr) and int(pwr) == 2,
    )


class AvrClient:
    """Une connexion persistante, rouverte à la demande. Une tâche de lecture reçoit tout ce
    que l'ampli envoie : réponses à nos requêtes ET états poussés spontanément (firmware
    >= 1.1.0), transmis aux écouteurs. Un verrou sérialise nos envois."""

    def __init__(self, host: str, port: int = PORT) -> None:
        self._host = host
        self._port = port
        self._writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._last_command = 0.0
        self._waiters: dict[str, asyncio.Future] = {}
        self._listeners: list = []

    def add_listener(self, callback) -> None:
        """callback(Status) à chaque état reçu, sollicité ou non."""
        self._listeners.append(callback)

    # --- réception -------------------------------------------------------------------

    async def _read_loop(self, reader: asyncio.StreamReader) -> None:
        buffer = b""
        try:
            while True:
                data = await reader.read(8192)
                if not data:
                    break
                messages, buffer = split_messages(buffer + data)
                for name, para in messages:
                    self._on_message(name, para)
        except OSError:
            pass
        finally:
            for future in self._waiters.values():
                if not future.done():
                    future.set_exception(ConnectionResetError("connexion fermée par l'ampli"))

    def _on_message(self, name: str, para: str) -> None:
        result: object = para
        if name == "get-status":
            try:
                result = parse_status(para)
            except (ValueError, IndexError):
                return
            for callback in self._listeners:
                callback(result)
        future = self._waiters.get(name)
        if future is not None and not future.done():
            future.set_result(result)

    # --- connexion -------------------------------------------------------------------

    async def _close(self) -> None:
        writer, self._writer = self._writer, None
        task, self._reader_task = self._reader_task, None
        if task is not None:
            task.cancel()
        if writer is not None:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), 1.0)
            except (OSError, asyncio.TimeoutError):
                pass

    async def close(self) -> None:
        async with self._lock:
            await self._close()

    async def _request(self, name: str, para: str = "", tries: int = 4):
        """Envoie une requête qui attend une réponse ; None si l'ampli reste muet."""
        assert self._writer is not None
        for _ in range(tries):
            future = asyncio.get_running_loop().create_future()
            self._waiters[name] = future
            try:
                self._writer.write(build_request(name, para))
                await self._writer.drain()
                return await asyncio.wait_for(future, REPLY_TIMEOUT)
            except asyncio.TimeoutError:
                continue
            finally:
                self._waiters.pop(name, None)
        return None

    async def _ensure(self) -> None:
        if self._writer is not None and self._reader_task is not None:
            if not self._reader_task.done():
                return
            await self._close()
        try:
            reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port), CONNECT_TIMEOUT
            )
        except (OSError, asyncio.TimeoutError) as err:
            raise AvrError(f"connexion impossible : {err!r}") from err
        self._reader_task = asyncio.create_task(self._read_loop(reader))
        await asyncio.sleep(CONNECT_SETTLE)
        if await self._request("heart-alive") is None:
            await self._close()
            raise AvrError("l'ampli accepte la connexion mais ne répond pas")

    async def _run(self, func):
        """Exécute func() sur la connexion ; une reconnexion puis un nouvel essai si elle casse."""
        for attempt in (0, 1):
            try:
                await self._ensure()
                return await func()
            except (OSError, asyncio.TimeoutError) as err:
                await self._close()
                if attempt:
                    raise AvrError(f"échange interrompu : {err!r}") from err
        raise AvrError("inatteignable")

    # --- opérations ------------------------------------------------------------------

    async def _status(self) -> Status:
        status = await self._request("get-status", tries=6)
        if status is None:
            await self._close()
            raise AvrError("get-status sans réponse (firmware custom requis)")
        return status

    async def _command(self, name: str, para: str = "") -> None:
        wait = self._last_command + COMMAND_GAP - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        assert self._writer is not None
        self._writer.write(build_request(name, para))
        await self._writer.drain()
        self._last_command = time.monotonic()

    async def status(self) -> Status:
        async with self._lock:
            return await self._run(self._status)

    async def command(self, name: str, para: str = "") -> None:
        async with self._lock:
            await self._run(lambda: self._command(name, para))

    async def set_volume(self, target: int) -> Status:
        """Volume absolu via la commande firmware `set-volume`, renvoyée si elle est ignorée."""
        target = max(0, min(180, int(target)))

        async def go() -> Status:
            status = await self._status()
            for _ in range(SETVOL_TRIES):
                if status.volume == target:
                    break
                await self._command("set-volume", str(target))
                await asyncio.sleep(0.3)
                status = await self._status()
            return status

        async with self._lock:
            return await self._run(go)

    async def set_surround(self, codes: list[int] | None) -> Status:
        """Sélectionne un mode surround. `codes` : les codes qui portent le nom voulu (variantes
        selon le signal), essayés dans l'ordre jusqu'à ce que l'ampli en applique un ; None =
        retour à « Auto Select ». Retourne l'état final : à l'appelant de voir si c'est accepté."""

        def reached(status: Status) -> bool:
            if codes is None:
                return bool(status.surround_auto)
            return status.surround in codes and not status.surround_auto

        async def go() -> Status:
            status = await self._status()
            for para in ["auto"] if codes is None else [str(code) for code in codes]:
                if reached(status):
                    break
                await self._command("set-surround", para)
                await asyncio.sleep(0.5)
                status = await self._status()
            return status

        async with self._lock:
            return await self._run(go)
