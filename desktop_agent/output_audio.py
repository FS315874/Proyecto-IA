"""Volumen de salidas Windows mediante Core Audio, sin shell ni dependencias."""

from __future__ import annotations

import ctypes
import math
import os
import re
import unicodedata
import uuid
from collections.abc import Mapping
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Protocol

from desktop_agent.models import ToolResult

MAX_OUTPUT_VOLUME = 80
_DEVICE_STATE_ACTIVE = 0x00000001
_E_RENDER = 0
_CLSCTX_ALL = 0x17
_STGM_READ = 0
_VT_LPWSTR = 31
_COINIT_APARTMENTTHREADED = 0x2
_WINFUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
_GENERIC_DEVICE_WORDS = frozenset({
    "altavoz",
    "altavoces",
    "auricular",
    "auriculares",
    "de",
    "del",
    "el",
    "en",
    "la",
    "las",
    "los",
    "mi",
    "mis",
    "salida",
})


class OutputAudioError(RuntimeError):
    def __init__(self, message: str, code: str = "audio_backend_failure") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class OutputDevice:
    endpoint_id: str = field(repr=False)
    name: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.endpoint_id, str)
            or not self.endpoint_id
            or len(self.endpoint_id) > 2_048
            or not isinstance(self.name, str)
            or not self.name
            or self.name != self.name.strip()
            or len(self.name) > 200
        ):
            raise ValueError("El dispositivo de salida no es válido.")


@dataclass(frozen=True)
class OutputVolumeRequest:
    device: str
    percent: str

    def __post_init__(self) -> None:
        normalize_output_device_name(self.device)
        validate_output_percent(self.percent)


class OutputAudioBackend(Protocol):
    def list_devices(self) -> tuple[OutputDevice, ...]: ...

    def read_volume(self, endpoint_id: str) -> float: ...

    def set_volume(self, endpoint_id: str, scalar: float) -> float: ...


def normalize_output_device_name(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 200
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError("El nombre del dispositivo de salida no es válido.")
    folded = "".join(
        character
        for character in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(character)
    )
    normalized = "".join(character for character in folded if character.isalnum())
    if not normalized:
        raise ValueError("El nombre del dispositivo de salida no es válido.")
    return normalized


def _device_tokens(value: str) -> tuple[str, ...]:
    folded = "".join(
        character
        for character in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(character)
    )
    return tuple(re.findall(r"[^\W_]+", folded, flags=re.UNICODE))


def _query_tokens(value: str) -> tuple[str, ...]:
    tokens = _device_tokens(value)
    specific = tuple(token for token in tokens if token not in _GENERIC_DEVICE_WORDS)
    return specific or tokens


def validate_output_percent(value: str) -> int:
    if (
        not isinstance(value, str)
        or not value.isascii()
        or not value.isdigit()
        or value != str(int(value))
        or not 0 <= int(value) <= MAX_OUTPUT_VOLUME
    ):
        raise ValueError(
            f"El volumen debe ser un entero entre 0 y {MAX_OUTPUT_VOLUME}."
        )
    return int(value)


def validate_output_volume_target(value: object) -> OutputVolumeRequest:
    if isinstance(value, OutputVolumeRequest):
        return value
    if not isinstance(value, Mapping) or set(value) != {"device", "percent"}:
        raise ValueError("El destino de volumen de salida no es válido.")
    device = value["device"]
    percent = value["percent"]
    if not isinstance(device, str) or not isinstance(percent, str):
        raise ValueError("El destino de volumen de salida no es válido.")
    return OutputVolumeRequest(device, percent)


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def parse(cls, value: str) -> "_GUID":
        parsed = uuid.UUID(value)
        fields = parsed.fields
        data4 = (ctypes.c_ubyte * 8)(fields[3], fields[4], *fields[5].to_bytes(6, "big"))
        return cls(fields[0], fields[1], fields[2], data4)


class _PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", _GUID), ("pid", wintypes.DWORD)]


class _PROPVARIANT_VALUE(ctypes.Union):
    _fields_ = [
        ("pwszVal", ctypes.c_wchar_p),
        ("pointer", ctypes.c_void_p),
        ("number", ctypes.c_ulonglong),
    ]


class _PROPVARIANT(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = [
        ("vt", ctypes.c_ushort),
        ("wReserved1", ctypes.c_ushort),
        ("wReserved2", ctypes.c_ushort),
        ("wReserved3", ctypes.c_ushort),
        ("value", _PROPVARIANT_VALUE),
    ]


_CLSID_MM_DEVICE_ENUMERATOR = _GUID.parse("bcde0395-e52f-467c-8e3d-c4579291692e")
_IID_MM_DEVICE_ENUMERATOR = _GUID.parse("a95664d2-9614-4f35-a746-de8db63617e6")
_IID_AUDIO_ENDPOINT_VOLUME = _GUID.parse("5cdf2c82-841e-4546-9722-0cf74078229a")
_PKEY_DEVICE_FRIENDLY_NAME = _PROPERTYKEY(
    _GUID.parse("a45c254e-df1c-4efd-8020-67d146a850e0"),
    14,
)


def _method(pointer: ctypes.c_void_p, index: int, restype, *argtypes):
    if not pointer or not pointer.value:
        raise OutputAudioError("Windows devolvió un objeto de audio inválido.")
    table = ctypes.cast(
        pointer,
        ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)),
    ).contents
    return _WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(table[index])


def _check_hresult(value: int) -> None:
    if value < 0:
        raise OutputAudioError("Windows Core Audio no completó la operación.")


def _release(pointer: ctypes.c_void_p | None) -> None:
    if pointer is None or not pointer.value:
        return
    try:
        _method(pointer, 2, wintypes.ULONG)(pointer)
    except (OSError, ValueError, OutputAudioError):
        pass


class _CoreAudioSession:
    def __init__(self) -> None:
        self.ole32 = None
        self.initialized = False

    def __enter__(self) -> "_CoreAudioSession":
        if os.name != "nt":
            raise OutputAudioError(
                "El control de volumen requiere Windows.",
                "audio_unavailable",
            )
        try:
            self.ole32 = ctypes.WinDLL("ole32", use_last_error=True)
            self.ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
            self.ole32.CoInitializeEx.restype = ctypes.c_long
            result = self.ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED)
            _check_hresult(result)
            self.initialized = True
            self.ole32.CoCreateInstance.argtypes = [
                ctypes.POINTER(_GUID),
                ctypes.c_void_p,
                wintypes.DWORD,
                ctypes.POINTER(_GUID),
                ctypes.POINTER(ctypes.c_void_p),
            ]
            self.ole32.CoCreateInstance.restype = ctypes.c_long
            self.ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
            self.ole32.CoTaskMemFree.restype = None
            self.ole32.PropVariantClear.argtypes = [ctypes.POINTER(_PROPVARIANT)]
            self.ole32.PropVariantClear.restype = ctypes.c_long
            self.ole32.CoUninitialize.restype = None
            return self
        except OutputAudioError:
            self.__exit__(None, None, None)
            raise
        except (AttributeError, OSError, TypeError, ValueError):
            self.__exit__(None, None, None)
            raise OutputAudioError(
                "Windows Core Audio no está disponible.",
                "audio_unavailable",
            ) from None

    def __exit__(self, *_args) -> None:
        if self.initialized and self.ole32 is not None:
            self.ole32.CoUninitialize()
        self.initialized = False

    def create_enumerator(self) -> ctypes.c_void_p:
        assert self.ole32 is not None
        pointer = ctypes.c_void_p()
        _check_hresult(
            self.ole32.CoCreateInstance(
                ctypes.byref(_CLSID_MM_DEVICE_ENUMERATOR),
                None,
                _CLSCTX_ALL,
                ctypes.byref(_IID_MM_DEVICE_ENUMERATOR),
                ctypes.byref(pointer),
            )
        )
        return pointer


class WindowsCoreAudioBackend:
    """Enumera endpoints de reproducción activos y verifica el volumen escrito."""

    def list_devices(self) -> tuple[OutputDevice, ...]:
        with _CoreAudioSession() as session:
            enumerator = session.create_enumerator()
            collection = ctypes.c_void_p()
            try:
                _check_hresult(
                    _method(
                        enumerator,
                        3,
                        ctypes.c_long,
                        ctypes.c_int,
                        wintypes.DWORD,
                        ctypes.POINTER(ctypes.c_void_p),
                    )(
                        enumerator,
                        _E_RENDER,
                        _DEVICE_STATE_ACTIVE,
                        ctypes.byref(collection),
                    )
                )
                count = wintypes.UINT()
                _check_hresult(
                    _method(
                        collection,
                        3,
                        ctypes.c_long,
                        ctypes.POINTER(wintypes.UINT),
                    )(collection, ctypes.byref(count))
                )
                devices = []
                for index in range(count.value):
                    device = ctypes.c_void_p()
                    try:
                        _check_hresult(
                            _method(
                                collection,
                                4,
                                ctypes.c_long,
                                wintypes.UINT,
                                ctypes.POINTER(ctypes.c_void_p),
                            )(collection, index, ctypes.byref(device))
                        )
                        endpoint_id = self._device_id(session, device)
                        name = self._friendly_name(session, device)
                        devices.append(OutputDevice(endpoint_id, name))
                    finally:
                        _release(device)
                return tuple(devices)
            finally:
                _release(collection)
                _release(enumerator)

    @staticmethod
    def _validate_endpoint_id(endpoint_id: str) -> None:
        if (
            not isinstance(endpoint_id, str)
            or not endpoint_id
            or len(endpoint_id) > 2_048
            or any(ord(character) < 32 for character in endpoint_id)
        ):
            raise OutputAudioError(
                "El endpoint de audio no cumple el contrato local.",
                "audio_invalid_input",
            )

    @classmethod
    def _open_endpoint(
        cls,
        session: _CoreAudioSession,
        endpoint_id: str,
    ) -> tuple[ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]:
        cls._validate_endpoint_id(endpoint_id)
        enumerator = session.create_enumerator()
        device = ctypes.c_void_p()
        endpoint = ctypes.c_void_p()
        try:
            _check_hresult(
                _method(
                    enumerator,
                    5,
                    ctypes.c_long,
                    ctypes.c_wchar_p,
                    ctypes.POINTER(ctypes.c_void_p),
                )(enumerator, endpoint_id, ctypes.byref(device))
            )
            _check_hresult(
                _method(
                    device,
                    3,
                    ctypes.c_long,
                    ctypes.POINTER(_GUID),
                    wintypes.DWORD,
                    ctypes.c_void_p,
                    ctypes.POINTER(ctypes.c_void_p),
                )(
                    device,
                    ctypes.byref(_IID_AUDIO_ENDPOINT_VOLUME),
                    _CLSCTX_ALL,
                    None,
                    ctypes.byref(endpoint),
                )
            )
            return enumerator, device, endpoint
        except Exception:
            _release(endpoint)
            _release(device)
            _release(enumerator)
            raise

    def read_volume(self, endpoint_id: str) -> float:
        with _CoreAudioSession() as session:
            enumerator, device, endpoint = self._open_endpoint(
                session,
                endpoint_id,
            )
            try:
                observed = ctypes.c_float()
                _check_hresult(
                    _method(
                        endpoint,
                        9,
                        ctypes.c_long,
                        ctypes.POINTER(ctypes.c_float),
                    )(endpoint, ctypes.byref(observed))
                )
                return float(observed.value)
            finally:
                _release(endpoint)
                _release(device)
                _release(enumerator)

    def set_volume(self, endpoint_id: str, scalar: float) -> float:
        self._validate_endpoint_id(endpoint_id)
        if (
            type(scalar) is not float
            or not math.isfinite(scalar)
            or not 0 <= scalar <= MAX_OUTPUT_VOLUME / 100
        ):
            raise OutputAudioError(
                "El cambio de volumen no cumple el contrato local.",
                "audio_invalid_input",
            )
        with _CoreAudioSession() as session:
            enumerator, device, endpoint = self._open_endpoint(
                session,
                endpoint_id,
            )
            try:
                _check_hresult(
                    _method(
                        endpoint,
                        7,
                        ctypes.c_long,
                        ctypes.c_float,
                        ctypes.c_void_p,
                    )(endpoint, ctypes.c_float(scalar), None)
                )
                observed = ctypes.c_float()
                _check_hresult(
                    _method(
                        endpoint,
                        9,
                        ctypes.c_long,
                        ctypes.POINTER(ctypes.c_float),
                    )(endpoint, ctypes.byref(observed))
                )
                return float(observed.value)
            finally:
                _release(endpoint)
                _release(device)
                _release(enumerator)

    @staticmethod
    def _device_id(session: _CoreAudioSession, device: ctypes.c_void_p) -> str:
        identifier = ctypes.c_wchar_p()
        _check_hresult(
            _method(
                device,
                5,
                ctypes.c_long,
                ctypes.POINTER(ctypes.c_wchar_p),
            )(device, ctypes.byref(identifier))
        )
        try:
            if not identifier.value:
                raise OutputAudioError("Windows devolvió una salida sin identificador.")
            return identifier.value
        finally:
            assert session.ole32 is not None
            session.ole32.CoTaskMemFree(ctypes.cast(identifier, ctypes.c_void_p))

    @staticmethod
    def _friendly_name(session: _CoreAudioSession, device: ctypes.c_void_p) -> str:
        store = ctypes.c_void_p()
        value = _PROPVARIANT()
        try:
            _check_hresult(
                _method(
                    device,
                    4,
                    ctypes.c_long,
                    wintypes.DWORD,
                    ctypes.POINTER(ctypes.c_void_p),
                )(device, _STGM_READ, ctypes.byref(store))
            )
            _check_hresult(
                _method(
                    store,
                    5,
                    ctypes.c_long,
                    ctypes.POINTER(_PROPERTYKEY),
                    ctypes.POINTER(_PROPVARIANT),
                )(
                    store,
                    ctypes.byref(_PKEY_DEVICE_FRIENDLY_NAME),
                    ctypes.byref(value),
                )
            )
            if value.vt != _VT_LPWSTR or not value.pwszVal:
                raise OutputAudioError("Windows devolvió una salida sin nombre.")
            return value.pwszVal.strip()
        finally:
            if session.ole32 is not None:
                session.ole32.PropVariantClear(ctypes.byref(value))
            _release(store)


class OutputVolumeTool:
    def __init__(self, backend: OutputAudioBackend | None = None) -> None:
        self._backend = backend or WindowsCoreAudioBackend()

    def __call__(self, device: str, percent: str) -> ToolResult:
        try:
            query = normalize_output_device_name(device)
            value = validate_output_percent(percent)
            devices = self._backend.list_devices()
            requested_tokens = _query_tokens(device)
            exact = [
                item for item in devices
                if normalize_output_device_name(item.name) == query
            ]
            prefix = [
                item for item in devices
                if normalize_output_device_name(item.name).startswith(query)
            ]
            matches = exact or prefix or [
                item for item in devices
                if len(query) >= 2
                and all(token in _device_tokens(item.name) for token in requested_tokens)
            ]
            if not matches:
                return ToolResult(
                    False,
                    "No hay una salida de audio activa con ese nombre.",
                    "audio_device_missing",
                    "audio_device",
                )
            if len(matches) > 1:
                return ToolResult(
                    False,
                    "Más de una salida coincide; usá un nombre más completo.",
                    "audio_device_ambiguous",
                    "audio_device",
                )
            selected = matches[0]
            requested = value / 100
            current = self._backend.read_volume(selected.endpoint_id)
            if (
                type(current) is not float
                or not math.isfinite(current)
                or not 0 <= current <= 1
            ):
                return ToolResult(
                    False,
                    "Windows no devolvió un volumen actual válido.",
                    "audio_volume_not_confirmed",
                    "audio_verify",
                )
            if abs(current - requested) <= 0.011:
                return ToolResult(
                    True,
                    f"Volumen de {selected.name}: {round(current * 100)} %; "
                    "ya estaba en el nivel solicitado. El estado de silencio no se modificó.",
                )
            observed = self._backend.set_volume(
                selected.endpoint_id,
                requested,
            )
            if (
                type(observed) is not float
                or not math.isfinite(observed)
                or not 0 <= observed <= 1
                or abs(observed - requested) > 0.011
            ):
                return ToolResult(
                    False,
                    "Windows no confirmó el volumen solicitado.",
                    "audio_volume_not_confirmed",
                    "audio_verify",
                )
            return ToolResult(
                True,
                f"Volumen de {selected.name}: {round(observed * 100)} %. "
                "El estado de silencio no se modificó.",
            )
        except ValueError as error:
            return ToolResult(
                False,
                str(error),
                "audio_invalid_input",
                "audio_device",
            )
        except OutputAudioError as error:
            return ToolResult(False, str(error), error.code, "audio_backend")
        except (AttributeError, OSError, TypeError):
            return ToolResult(
                False,
                "Windows Core Audio no completó la operación.",
                "audio_backend_failure",
                "audio_backend",
            )
