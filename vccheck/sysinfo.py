# Windows の設定の読み取り: レジストリ(読み取り専用。INV-11)・プロセス名の有無・CPU/メモリ。
# 外部コマンドを起動しない(INV-9)。Discord はプロセス名の有無だけを見て、中身に触れない(INV-4)。
# レジストリのサブキー名とパスは、結果にも理由の文にも残さない(INV-13)。
from __future__ import annotations

import ctypes
import functools
import time
import winreg
from collections.abc import Callable, Sequence
from ctypes import wintypes
from typing import Final

from vccheck.model import Measure, RawItem, failed, measured

CONSENT_PATH: Final = (
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager"
    r"\ConsentStore\microphone"
)
NONPACKAGED_PATH: Final = CONSENT_PATH + r"\NonPackaged"
AUDIO_PATH: Final = r"Software\Microsoft\Multimedia\Audio"
CONSENT_VALUE: Final = "Value"
DUCKING_VALUE: Final = "UserDuckingPreference"
DISCORD_ENTRY_SUFFIX: Final = "discord.exe"

REASON_NO_KEY: Final = "キーが無い"
REASON_NO_VALUE: Final = "キーはあるが Value が無い"
REASON_NOT_TEXT: Final = "Value が空でない文字列でない"
REASON_NO_DISCORD_ENTRY: Final = "Discord のエントリが無い"
REASON_DISCORD_NO_VALUE: Final = "Discord のエントリはあるが Value が無い"
REASON_DISCORD_CONFLICT: Final = "Discord のエントリが複数あり、Value が食い違っている"
REASON_NO_DUCKING: Final = "値が無い(Windows の既定が使われると推測されるが未確認)"
REASON_DUCKING_RANGE: Final = "0〜3 の整数でない値だった"
REASON_CPU_STALLED: Final = "CPU 時間の差が取れなかった"

_TH32CS_SNAPPROCESS: Final = 0x00000002
_INVALID_HANDLE: Final = ctypes.c_void_p(-1).value
_MAX_PATH: Final = 260


def _open(root: winreg.HKEYType | int, path: str) -> winreg.HKEYType:
    """読み取り専用でキーを開く。書き込みの権限は要求しない。"""
    return winreg.OpenKey(root, path, 0, winreg.KEY_READ)


def read_mic_consent(item_id: str, root: int) -> RawItem:
    """ConsentStore の microphone の Value(priv.mic_machine・priv.mic_user)。"""
    try:
        with _open(root, CONSENT_PATH) as key:
            try:
                value, _ = winreg.QueryValueEx(key, CONSENT_VALUE)
            except FileNotFoundError:
                return failed(item_id, REASON_NO_VALUE)
    except FileNotFoundError:
        return failed(item_id, REASON_NO_KEY)
    except OSError as error:
        return failed(item_id, type(error).__name__)
    if not isinstance(value, str) or not value:
        return failed(item_id, REASON_NOT_TEXT)
    return measured(item_id, value)


def _subkey_names(key: winreg.HKEYType) -> list[str]:
    names: list[str] = []
    while True:
        try:
            names.append(winreg.EnumKey(key, len(names)))
        except OSError:
            return names


def read_mic_consent_discord() -> RawItem:
    """NonPackaged 配下で、名前が discord.exe で終わるエントリの Value(priv.mic_discord)。"""
    item_id = "priv.mic_discord"
    entries: list[str] = []
    values: list[str] = []
    try:
        with _open(winreg.HKEY_CURRENT_USER, NONPACKAGED_PATH) as parent:
            entries = [n for n in _subkey_names(parent)
                       if n.casefold().endswith(DISCORD_ENTRY_SUFFIX)]
            for entry in entries:
                with _open(parent, entry) as key:
                    try:
                        value, _ = winreg.QueryValueEx(key, CONSENT_VALUE)
                    except FileNotFoundError:
                        continue
                    if isinstance(value, str) and value:
                        values.append(value)
    except FileNotFoundError:
        return failed(item_id, REASON_NO_KEY)
    except OSError as error:
        return failed(item_id, type(error).__name__)
    if not entries:
        return failed(item_id, REASON_NO_DISCORD_ENTRY)
    if not values:
        return failed(item_id, REASON_DISCORD_NO_VALUE)
    if len(set(values)) > 1:
        return failed(item_id, REASON_DISCORD_CONFLICT)
    return measured(item_id, values[0])


def read_ducking() -> RawItem:
    """通信時の音量調整の設定値 UserDuckingPreference(duck.pref。0〜3)。"""
    item_id = "duck.pref"
    try:
        with _open(winreg.HKEY_CURRENT_USER, AUDIO_PATH) as key:
            value, _ = winreg.QueryValueEx(key, DUCKING_VALUE)
    except FileNotFoundError:
        return failed(item_id, REASON_NO_DUCKING)
    except OSError as error:
        return failed(item_id, type(error).__name__)
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 3:
        return failed(item_id, REASON_DUCKING_RANGE)
    return measured(item_id, value)


class _ProcessEntry(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * _MAX_PATH),
    ]


class _MemoryStatus(ctypes.Structure):
    _fields_ = [
        ("dwLength", wintypes.DWORD),
        ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


@functools.cache
def _kernel32() -> ctypes.WinDLL:
    """Win32 の関数の引数と戻り値の型を決める(§8.2 の spike で動作を確かめた呼び方)。"""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)]
    k32.Process32FirstW.restype = wintypes.BOOL
    k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)]
    k32.Process32NextW.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL
    k32.GetSystemTimes.argtypes = [ctypes.POINTER(wintypes.FILETIME)] * 3
    k32.GetSystemTimes.restype = wintypes.BOOL
    k32.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(_MemoryStatus)]
    k32.GlobalMemoryStatusEx.restype = wintypes.BOOL
    return k32


def list_process_names() -> list[str]:
    """動いているプロセスの実行ファイル名の一覧(Toolhelp32)。名前のほかは読まない。"""
    k32 = _kernel32()
    snapshot = k32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if snapshot is None or snapshot == _INVALID_HANDLE:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = _ProcessEntry()
        entry.dwSize = ctypes.sizeof(_ProcessEntry)
        if not k32.Process32FirstW(snapshot, ctypes.byref(entry)):
            raise ctypes.WinError(ctypes.get_last_error())
        names = [str(entry.szExeFile)]
        while k32.Process32NextW(snapshot, ctypes.byref(entry)):
            names.append(str(entry.szExeFile))
        return names
    finally:
        k32.CloseHandle(snapshot)


def read_discord_running(wanted: Sequence[str],
                         lister: Callable[[], list[str]] = list_process_names) -> RawItem:
    """wanted のどれかの名前のプロセスがあるか(env.discord_running)。"""
    note = "確認した名前: " + ", ".join(wanted)
    try:
        running = {name.casefold() for name in lister()}
    except OSError as error:
        return failed("env.discord_running", type(error).__name__, note)
    return measured("env.discord_running", any(w.casefold() in running for w in wanted), note)


def _filetime(value: wintypes.FILETIME) -> int:
    return (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)


def system_times() -> tuple[int, int]:
    """起動からの (アイドル時間, 全体の時間)。単位は 100 ナノ秒。"""
    idle, kernel, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
    if not _kernel32().GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel),
                                      ctypes.byref(user)):
        raise ctypes.WinError(ctypes.get_last_error())
    # カーネル時間はアイドル時間を含む。
    return _filetime(idle), _filetime(kernel) + _filetime(user)


def memory_load() -> int:
    """物理メモリの使用率(%)。"""
    status = _MemoryStatus()
    status.dwLength = ctypes.sizeof(_MemoryStatus)
    if not _kernel32().GlobalMemoryStatusEx(ctypes.byref(status)):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(status.dwMemoryLoad)


def sample_load(duration_s: float, interval_ms: float, *,
                times: Callable[[], tuple[int, int]] = system_times,
                memory: Callable[[], int] = memory_load,
                sleep: Callable[[float], None] = time.sleep) -> list[RawItem]:
    """duration_s 秒のあいだ interval_ms ごとに測り、sys.* の3項目を作る(FR-8)。"""
    count = max(1, round(duration_s * 1000.0 / interval_ms))
    cpu: list[float] = []
    mem: list[int] = []
    cpu_error: str | None = None
    mem_error: str | None = None
    previous: tuple[int, int] | None = None
    try:
        previous = times()
    except OSError as error:
        cpu_error = type(error).__name__
    for _ in range(count):
        sleep(interval_ms / 1000.0)
        if cpu_error is None and previous is not None:
            try:
                current = times()
            except OSError as error:
                cpu_error = type(error).__name__
            else:
                idle, total = current[0] - previous[0], current[1] - previous[1]
                if total > 0:
                    cpu.append(min(100.0, max(0.0, (1.0 - idle / total) * 100.0)))
                previous = current
        if mem_error is None:
            try:
                mem.append(memory())
            except OSError as error:
                mem_error = type(error).__name__
    if cpu_error is None and not cpu:
        cpu_error = REASON_CPU_STALLED
    if cpu_error is not None:
        cpu_items = [failed("sys.cpu_avg_pct", cpu_error), failed("sys.cpu_max_pct", cpu_error)]
    else:
        cpu_items = [
            measured("sys.cpu_avg_pct", round(sum(cpu) / len(cpu), 1) + 0.0),
            measured("sys.cpu_max_pct", round(max(cpu), 1) + 0.0),
        ]
    if mem_error is not None:
        return [*cpu_items, failed("sys.mem_load_pct", mem_error)]
    return [*cpu_items, measured("sys.mem_load_pct", max(mem))]


def collect(measure: Measure) -> list[RawItem]:
    """Windows の設定の5項目と、負荷の3項目を読む。何も書き換えない。"""
    return [
        read_discord_running(measure.discord_process_names),
        read_mic_consent("priv.mic_machine", winreg.HKEY_LOCAL_MACHINE),
        read_mic_consent("priv.mic_user", winreg.HKEY_CURRENT_USER),
        read_mic_consent_discord(),
        read_ducking(),
        *sample_load(measure.silent_s + measure.speech_s, measure.sys_interval_ms),
    ]
