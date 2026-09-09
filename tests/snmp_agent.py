"""
Поддельный принтер, отвечающий по SNMP.

Нужен, чтобы клиент и разбор Printer-MIB проверялись целиком, вместе с UDP, а
не по частям: именно на стыке кодирования и сети обычно и прячутся ошибки.
"""

from __future__ import annotations

import socket
import threading
from typing import Any

from pbreader.snmp import (
    PDU_GET_NEXT,
    PDU_RESPONSE,
    TAG_COUNTER32,
    TAG_END_OF_MIB,
    TAG_GAUGE32,
    TAG_INTEGER,
    TAG_NO_SUCH_OBJECT,
    TAG_SEQUENCE,
    _read_tlv,
    _tlv,
    decode_oid,
    encode_integer,
    encode_octet_string,
    encode_oid,
)


def _oid_key(oid: str) -> tuple[int, ...]:
    return tuple(int(part) for part in oid.split("."))


class FakePrinter:
    """Отдаёт заранее заданные значения. Запускается на случайном порту."""

    def __init__(self, values: dict[str, Any], community: str = "public") -> None:
        self.values = dict(values)
        self.community = community
        self.requests = 0
        self.silent = False  # включается, чтобы проверить поведение при молчании
        self.error_status = 0
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.bind(("127.0.0.1", 0))
        self._socket.settimeout(0.2)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    @property
    def port(self) -> int:
        return self._socket.getsockname()[1]

    def __enter__(self) -> "FakePrinter":
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        self._socket.close()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                data, address = self._socket.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                return
            self.requests += 1
            if self.silent:
                continue
            try:
                self._socket.sendto(self._answer(data), address)
            except OSError:
                return

    def _answer(self, data: bytes) -> bytes:
        _, body, _ = _read_tlv(data, 0)
        _, _, offset = _read_tlv(body, 0)
        _, community, offset = _read_tlv(body, offset)
        pdu_tag, pdu, _ = _read_tlv(body, offset)

        _, request_id_raw, offset = _read_tlv(pdu, 0)
        _, _, offset = _read_tlv(pdu, offset)
        _, _, offset = _read_tlv(pdu, offset)
        _, bindings_body, _ = _read_tlv(pdu, offset)

        wrong_community = community.decode() != self.community
        answers = []
        position = 0
        while position < len(bindings_body):
            _, binding, position = _read_tlv(bindings_body, position)
            _, oid_payload, _ = _read_tlv(binding, 0)
            oid = decode_oid(oid_payload)
            answers.append(self._value_for(pdu_tag, oid, wrong_community))

        status = 5 if wrong_community else self.error_status
        response = _tlv(
            PDU_RESPONSE,
            _tlv(TAG_INTEGER, request_id_raw) + encode_integer(status) + encode_integer(0)
            + _tlv(TAG_SEQUENCE, b"".join(answers)),
        )
        return _tlv(TAG_SEQUENCE, encode_integer(1) + encode_octet_string(self.community) + response)

    def _value_for(self, pdu_tag: int, oid: str, wrong_community: bool) -> bytes:
        if wrong_community:
            return _tlv(TAG_SEQUENCE, encode_oid(oid) + _tlv(TAG_NO_SUCH_OBJECT, b""))

        if pdu_tag == PDU_GET_NEXT:
            following = sorted(
                (key for key in self.values if _oid_key(key) > _oid_key(oid)), key=_oid_key
            )
            if not following:
                return _tlv(TAG_SEQUENCE, encode_oid(oid) + _tlv(TAG_END_OF_MIB, b""))
            oid = following[0]
        elif oid not in self.values:
            return _tlv(TAG_SEQUENCE, encode_oid(oid) + _tlv(TAG_NO_SUCH_OBJECT, b""))

        return _tlv(TAG_SEQUENCE, encode_oid(oid) + _encode(self.values[oid]))


def _encode(value: Any) -> bytes:
    if isinstance(value, bool):
        return encode_integer(int(value))
    if isinstance(value, int):
        return encode_integer(value)
    if isinstance(value, tuple) and len(value) == 2:
        kind, raw = value
        tag = {"counter": TAG_COUNTER32, "gauge": TAG_GAUGE32}[kind]
        return _tlv(tag, int(raw).to_bytes(max(1, (int(raw).bit_length() + 7) // 8), "big"))
    return encode_octet_string(str(value))
