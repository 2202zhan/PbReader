"""
Минимальный клиент SNMP v2c — без внешних зависимостей.

Зачем свой, а не готовая библиотека: аппарат стоит и работает годами, а нам
нужен десяток числовых значений по фиксированным адресам. Готовые пакеты для
SNMP в Python за последние годы несколько раз меняли имя и владельца, и тащить
такую зависимость в киоск ради GET по известному OID — плохой размен. Здесь
ровно то, что нужно: GET, GETNEXT и обход поддерева.

Кодирование — BER, как оно описано в RFC 3416. Всё, что не нужно (SET, v3,
шифрование), намеренно не поддерживается: писать в принтер мы не собираемся, а
меньше кода — меньше мест, где ошибиться.
"""

from __future__ import annotations

import logging
import random
import socket
from dataclasses import dataclass
from typing import Any, Iterator

logger = logging.getLogger(__name__)

SNMP_PORT = 161
DEFAULT_COMMUNITY = "public"
DEFAULT_TIMEOUT = 2.0
DEFAULT_RETRIES = 2

# Теги BER
TAG_INTEGER = 0x02
TAG_OCTET_STRING = 0x04
TAG_NULL = 0x05
TAG_OID = 0x06
TAG_SEQUENCE = 0x30
TAG_IP_ADDRESS = 0x40
TAG_COUNTER32 = 0x41
TAG_GAUGE32 = 0x42
TAG_TIMETICKS = 0x43
TAG_COUNTER64 = 0x46
TAG_NO_SUCH_OBJECT = 0x80
TAG_NO_SUCH_INSTANCE = 0x81
TAG_END_OF_MIB = 0x82

PDU_GET = 0xA0
PDU_GET_NEXT = 0xA1
PDU_RESPONSE = 0xA2

_ERROR_STATUS = {
    0: "",
    1: "ответ не поместился в пакет",
    2: "такого значения у устройства нет",
    3: "неверный тип значения",
    4: "значение только для чтения",
    5: "устройство не смогло ответить",
}


class SnmpError(RuntimeError):
    """Устройство не ответило или ответило непонятным."""


class SnmpTimeout(SnmpError):
    """Устройство молчит: не в сети, закрыт порт или другой community."""


# --- кодирование BER ---------------------------------------------------------


def _length(size: int) -> bytes:
    if size < 0x80:
        return bytes((size,))
    raw = size.to_bytes((size.bit_length() + 7) // 8, "big")
    return bytes((0x80 | len(raw),)) + raw


def _tlv(tag: int, payload: bytes) -> bytes:
    return bytes((tag,)) + _length(len(payload)) + payload


def encode_integer(value: int) -> bytes:
    size = max(1, (value.bit_length() + 8) // 8)
    return _tlv(TAG_INTEGER, value.to_bytes(size, "big", signed=True))


def encode_octet_string(value: str | bytes) -> bytes:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return _tlv(TAG_OCTET_STRING, raw)


def encode_oid(oid: str) -> bytes:
    """Кодирует «1.3.6.1.2.1.43.10.2.1.4.1.1» в BER.

    Первые два числа по стандарту пакуются в один байт: 40*a + b.
    """
    parts = [int(part) for part in oid.strip().lstrip(".").split(".")]
    if len(parts) < 2:
        raise ValueError(f"Слишком короткий OID: {oid!r}")

    payload = bytearray((40 * parts[0] + parts[1],))
    for number in parts[2:]:
        if number < 0x80:
            payload.append(number)
            continue
        chunks = []
        while number:
            chunks.append(number & 0x7F)
            number >>= 7
        chunks.reverse()
        payload.extend(chunk | 0x80 for chunk in chunks[:-1])
        payload.append(chunks[-1])
    return _tlv(TAG_OID, bytes(payload))


# --- разбор BER --------------------------------------------------------------


def _read_tlv(data: bytes, offset: int) -> tuple[int, bytes, int]:
    """Возвращает (тег, содержимое, следующее смещение)."""
    if offset + 2 > len(data):
        raise SnmpError("Ответ оборван")
    tag = data[offset]
    size = data[offset + 1]
    offset += 2
    if size & 0x80:
        count = size & 0x7F
        if count == 0 or offset + count > len(data):
            raise SnmpError("Неверная длина в ответе")
        size = int.from_bytes(data[offset:offset + count], "big")
        offset += count
    end = offset + size
    if end > len(data):
        raise SnmpError("Длина в ответе больше самого ответа")
    return tag, data[offset:end], end


def decode_oid(payload: bytes) -> str:
    if not payload:
        return ""
    first = payload[0]
    parts = [first // 40, first % 40]
    number = 0
    for byte in payload[1:]:
        number = (number << 7) | (byte & 0x7F)
        if not byte & 0x80:
            parts.append(number)
            number = 0
    return ".".join(str(part) for part in parts)


def _decode_value(tag: int, payload: bytes) -> Any:
    if tag == TAG_INTEGER:
        return int.from_bytes(payload, "big", signed=True) if payload else 0
    if tag in (TAG_COUNTER32, TAG_GAUGE32, TAG_TIMETICKS, TAG_COUNTER64):
        return int.from_bytes(payload, "big") if payload else 0
    if tag == TAG_OCTET_STRING:
        # Возвращаем БАЙТЫ, а не строку. В SNMP один и тот же тип несёт и
        # название картриджа, и битовую маску неполадок — на проводе они
        # неразличимы. Декодировать всё как текст значит незаметно портить
        # двоичные значения: маска 0x20 («мало тонера») после обрезки
        # пробелов превращается в пустую строку, и неполадка исчезает.
        # Текст достаёт as_text() — там, где он действительно ожидается.
        return payload
    if tag == TAG_OID:
        return decode_oid(payload)
    if tag == TAG_IP_ADDRESS:
        return ".".join(str(byte) for byte in payload)
    if tag in (TAG_NULL, TAG_NO_SUCH_OBJECT, TAG_NO_SUCH_INSTANCE, TAG_END_OF_MIB):
        return None
    return payload


def as_text(value: Any, default: str = "") -> str:
    """Достаёт из значения строку — там, где по смыслу ожидается текст.

    Названия лотков и картриджей аппараты отдают в разных кодировках и любят
    дополнять нулями и пробелами; непереводимые байты не повод потерять всё
    значение целиком.
    """
    if value is None:
        return default
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "replace").strip("\x00").strip() or default
    return str(value).strip() or default


@dataclass(frozen=True)
class VarBind:
    oid: str
    value: Any
    #: True, если устройство сообщило, что такого значения у него нет.
    missing: bool = False


# --- клиент ------------------------------------------------------------------


class SnmpClient:
    """SNMP v2c поверх UDP. Только чтение."""

    def __init__(
        self,
        host: str,
        community: str = DEFAULT_COMMUNITY,
        port: int = SNMP_PORT,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
    ) -> None:
        self.host = host
        self.community = community
        self.port = port
        self.timeout = timeout
        self.retries = retries

    def _request(self, pdu_type: int, oids: list[str]) -> list[VarBind]:
        request_id = random.randint(1, 0x7FFFFFFF)
        bindings = b"".join(_tlv(TAG_SEQUENCE, encode_oid(oid) + _tlv(TAG_NULL, b"")) for oid in oids)
        pdu = _tlv(
            pdu_type,
            encode_integer(request_id) + encode_integer(0) + encode_integer(0)
            + _tlv(TAG_SEQUENCE, bindings),
        )
        message = _tlv(
            TAG_SEQUENCE,
            encode_integer(1) + encode_octet_string(self.community) + pdu,  # версия 1 = v2c
        )

        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                return self._exchange(message, request_id)
            except SnmpTimeout as exc:
                last = exc
                logger.debug("SNMP %s: попытка %d без ответа", self.host, attempt + 1)
        raise last or SnmpTimeout(f"{self.host} не отвечает по SNMP")

    def _exchange(self, message: bytes, request_id: int) -> list[VarBind]:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(self.timeout)
            try:
                sock.sendto(message, (self.host, self.port))
                data, _ = sock.recvfrom(65535)
            except socket.timeout as exc:
                raise SnmpTimeout(f"{self.host}:{self.port} не ответил за {self.timeout} с") from exc
            except OSError as exc:
                raise SnmpError(f"Не удалось обратиться к {self.host}: {exc}") from exc
        return _parse_response(data, request_id)

    def get(self, *oids: str) -> dict[str, Any]:
        """Читает значения по точным адресам. Отсутствующие не попадают в ответ."""
        return {b.oid: b.value for b in self._request(PDU_GET, list(oids)) if not b.missing}

    def get_one(self, oid: str, default: Any = None) -> Any:
        try:
            return self.get(oid).get(oid, default)
        except SnmpError as exc:
            logger.debug("SNMP %s: %s не прочитан (%s)", self.host, oid, exc)
            return default

    def walk(self, base_oid: str, limit: int = 64) -> Iterator[tuple[str, Any]]:
        """Обходит поддерево. Нужен для таблиц: лотки и картриджи.

        `limit` не даёт зациклиться на устройстве, которое отвечает не тем, что
        у него спросили: у киоска нет права зависнуть на опросе принтера.
        """
        prefix = base_oid.strip().lstrip(".")
        current = prefix
        for _ in range(limit):
            bindings = self._request(PDU_GET_NEXT, [current])
            if not bindings:
                return
            binding = bindings[0]
            if binding.missing or not binding.oid.startswith(prefix + "."):
                return
            yield binding.oid, binding.value
            if binding.oid == current:
                return  # устройство не сдвинулось — дальше идти некуда
            current = binding.oid

    def alive(self) -> bool:
        """Отвечает ли устройство вообще. sysDescr есть у всего, что умеет SNMP."""
        try:
            self.get("1.3.6.1.2.1.1.1.0")
            return True
        except SnmpError:
            return False


def _parse_response(data: bytes, expected_id: int) -> list[VarBind]:
    tag, body, _ = _read_tlv(data, 0)
    if tag != TAG_SEQUENCE:
        raise SnmpError("Ответ не похож на SNMP")

    _, _, offset = _read_tlv(body, 0)            # версия
    _, _, offset = _read_tlv(body, offset)       # community
    pdu_tag, pdu, _ = _read_tlv(body, offset)
    if pdu_tag != PDU_RESPONSE:
        raise SnmpError(f"Ожидался ответ, получен блок 0x{pdu_tag:02X}")

    tag, payload, offset = _read_tlv(pdu, 0)
    request_id = int.from_bytes(payload, "big", signed=True)
    if request_id != expected_id:
        raise SnmpError("Ответ на чужой запрос — расходятся номера")

    tag, payload, offset = _read_tlv(pdu, offset)
    error_status = int.from_bytes(payload, "big", signed=True)
    _, _, offset = _read_tlv(pdu, offset)        # error-index
    if error_status:
        raise SnmpError(_ERROR_STATUS.get(error_status, f"устройство вернуло ошибку {error_status}"))

    _, bindings_body, _ = _read_tlv(pdu, offset)

    result: list[VarBind] = []
    position = 0
    while position < len(bindings_body):
        _, binding, position = _read_tlv(bindings_body, position)
        _, oid_payload, inner = _read_tlv(binding, 0)
        value_tag, value_payload, _ = _read_tlv(binding, inner)
        result.append(
            VarBind(
                oid=decode_oid(oid_payload),
                value=_decode_value(value_tag, value_payload),
                missing=value_tag in (TAG_NO_SUCH_OBJECT, TAG_NO_SUCH_INSTANCE, TAG_END_OF_MIB),
            )
        )
    return result
