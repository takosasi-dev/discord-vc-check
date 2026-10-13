# sysinfo.py のテスト(AC-14 ほか)。レジストリは差し替えた合成の物を使う。
# 実物に触れるテストは、読み取りだけで、値ではなく形だけを確かめる。
from __future__ import annotations

import os
import sys
import winreg

import pytest

from vccheck import sysinfo

HKCU = "HKCU"
HKLM = "HKLM"
ROOTS = {winreg.HKEY_CURRENT_USER: HKCU, winreg.HKEY_LOCAL_MACHINE: HKLM}
CONSENT = sysinfo.CONSENT_PATH
NONPACKAGED = sysinfo.NONPACKAGED_PATH
AUDIO = sysinfo.AUDIO_PATH
# 実物(仕様書 §8.2)と同じ形の、合成のサブキー名。
ENTRY = "X:#Somewhere#Discord#app-0.0.1#Discord.exe"


class FakeKey:
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeRegistry:
    """キーのパス → 値の辞書、で表した合成のレジストリ。"""

    def __init__(self, keys, denied=()):
        self.keys = keys
        self.denied = set(denied)
        self.calls = []

    def open(self, root, path, reserved=0, access=winreg.KEY_READ):
        self.calls.append((reserved, access))
        base = root.path if isinstance(root, FakeKey) else ROOTS[root]
        full = f"{base}\\{path}"
        if full in self.denied:
            raise PermissionError(f"アクセス拒否: {full}")
        if full not in self.keys:
            raise FileNotFoundError(full)
        return FakeKey(full)

    def query(self, key, name):
        values = self.keys[key.path]
        if name not in values:
            raise FileNotFoundError(name)
        value = values[name]
        return value, winreg.REG_DWORD if isinstance(value, int) else winreg.REG_SZ

    def enum(self, key, index):
        prefix = key.path + "\\"
        children = sorted(p[len(prefix):] for p in self.keys
                          if p.startswith(prefix) and "\\" not in p[len(prefix):])
        if index >= len(children):
            raise OSError("これ以上無い")
        return children[index]


@pytest.fixture
def registry(monkeypatch):
    def install(keys, denied=()):
        fake = FakeRegistry(keys, denied)
        monkeypatch.setattr(winreg, "OpenKey", fake.open)
        monkeypatch.setattr(winreg, "QueryValueEx", fake.query)
        monkeypatch.setattr(winreg, "EnumKey", fake.enum)
        return fake

    return install


def typical(discord_values=None, ducking=None):
    keys = {
        f"{HKLM}\\{CONSENT}": {"Value": "Allow"},
        f"{HKCU}\\{CONSENT}": {"Value": "Deny"},
        f"{HKCU}\\{NONPACKAGED}": {"Value": "Allow"},
        f"{HKCU}\\{NONPACKAGED}\\X:#Somewhere#Other#other.exe": {"Value": "Deny"},
        f"{HKCU}\\{AUDIO}": {} if ducking is None else {"UserDuckingPreference": ducking},
    }
    for index, values in enumerate(discord_values or []):
        keys[f"{HKCU}\\{NONPACKAGED}\\{ENTRY.replace('0.0.1', f'0.0.{index}')}"] = values
    return keys


def test_mic_consent_values(registry):
    registry(typical())
    machine = sysinfo.read_mic_consent("priv.mic_machine", winreg.HKEY_LOCAL_MACHINE)
    user = sysinfo.read_mic_consent("priv.mic_user", winreg.HKEY_CURRENT_USER)
    assert (machine.status, machine.value) == ("measured", "Allow")
    assert (user.status, user.value) == ("measured", "Deny")


def test_mic_consent_missing_key_value_and_denied(registry):
    registry({})
    missing = sysinfo.read_mic_consent("priv.mic_user", winreg.HKEY_CURRENT_USER)
    assert (missing.status, missing.value, missing.reason) == ("failed", None, "キーが無い")
    registry({f"{HKCU}\\{CONSENT}": {}})
    no_value = sysinfo.read_mic_consent("priv.mic_user", winreg.HKEY_CURRENT_USER)
    assert (no_value.status, no_value.reason) == ("failed", "キーはあるが Value が無い")
    registry({f"{HKCU}\\{CONSENT}": {"Value": 1}})
    assert sysinfo.read_mic_consent("priv.mic_user", winreg.HKEY_CURRENT_USER).status == "failed"
    registry(typical(), denied=[f"{HKLM}\\{CONSENT}"])
    denied = sysinfo.read_mic_consent("priv.mic_machine", winreg.HKEY_LOCAL_MACHINE)
    # 拒否されたときの理由は例外の種類の名前だけ。パスを含めない。
    assert (denied.status, denied.value, denied.reason) == ("failed", None, "PermissionError")


def test_discord_entry_with_value(registry):
    registry(typical([{"Value": "Allow"}]))
    item = sysinfo.read_mic_consent_discord()
    assert (item.status, item.value) == ("measured", "Allow")
    registry(typical([{"Value": "Deny"}, {"Value": "Deny"}, {"LastUsedTimeStop": 5}]))
    assert sysinfo.read_mic_consent_discord().value == "Deny"


def test_discord_entries_without_value_like_the_real_registry(registry):
    """実物(§8.2)の形: エントリは版ごとに複数あり、Value を持たない。測れなかったにする。"""
    registry(typical([{"LastUsedTimeStart": 1, "LastUsedTimeStop": 2}] * 5))
    item = sysinfo.read_mic_consent_discord()
    assert (item.status, item.value) == ("failed", None)
    assert item.reason == "Discord のエントリはあるが Value が無い"


def test_discord_entry_edge_cases(registry):
    registry(typical())
    assert sysinfo.read_mic_consent_discord().reason == "Discord のエントリが無い"
    registry(typical([{"Value": "Allow"}, {"Value": "Deny"}]))
    conflict = sysinfo.read_mic_consent_discord()
    assert (conflict.status, conflict.value) == ("failed", None)
    assert conflict.reason == "Discord のエントリが複数あり、Value が食い違っている"
    registry({})
    assert sysinfo.read_mic_consent_discord().reason == "キーが無い"
    registry(typical([{"Value": "Allow"}]), denied=[f"{HKCU}\\{NONPACKAGED}"])
    assert sysinfo.read_mic_consent_discord().reason == "PermissionError"
    keys = typical()
    keys[f"{HKCU}\\{NONPACKAGED}\\X:#Somewhere#DISCORD.EXE"] = {"Value": "Allow"}
    registry(keys)
    assert sysinfo.read_mic_consent_discord().value == "Allow"


def test_inv13_no_subkey_name_in_results(registry):
    for discord_values in ([{"Value": "Allow"}], [{}], [{"Value": "Allow"}, {"Value": "Deny"}]):
        registry(typical(discord_values))
        item = sysinfo.read_mic_consent_discord()
        text = repr(item)
        assert "Somewhere" not in text
        assert "#" not in text
        assert "\\" not in text


def test_ducking(registry):
    for value in (0, 1, 2, 3):
        registry(typical(ducking=value))
        item = sysinfo.read_ducking()
        assert (item.status, item.value) == ("measured", value)
        assert type(item.value) is int
    expected = "値が無い(Windows の既定が使われると推測されるが未確認)"
    registry(typical())
    absent = sysinfo.read_ducking()
    assert (absent.status, absent.value, absent.reason) == ("failed", None, expected)
    registry({})
    assert sysinfo.read_ducking().reason == expected
    registry(typical(ducking=7))
    out_of_range = sysinfo.read_ducking()
    assert (out_of_range.status, out_of_range.value) == ("failed", None)
    registry(typical(ducking=1), denied=[f"{HKCU}\\{AUDIO}"])
    assert sysinfo.read_ducking().reason == "PermissionError"


def test_ac14_every_open_is_read_only(registry):
    fake = registry(typical([{"Value": "Allow"}, {}], ducking=1))
    sysinfo.read_mic_consent("priv.mic_machine", winreg.HKEY_LOCAL_MACHINE)
    sysinfo.read_mic_consent("priv.mic_user", winreg.HKEY_CURRENT_USER)
    sysinfo.read_mic_consent_discord()
    sysinfo.read_ducking()
    assert len(fake.calls) == 6
    assert all(call == (0, winreg.KEY_READ) for call in fake.calls)


def test_ac14_real_registry_is_opened_read_only(monkeypatch):
    """実物のレジストリを読むだけ。開き方がすべて KEY_READ で、形が崩れないことを確かめる。"""
    real_open = winreg.OpenKey
    accesses = []

    def recording_open(key, sub_key, reserved=0, access=winreg.KEY_READ):
        accesses.append(access)
        return real_open(key, sub_key, reserved, access)

    monkeypatch.setattr(winreg, "OpenKey", recording_open)
    items = [
        sysinfo.read_mic_consent("priv.mic_machine", winreg.HKEY_LOCAL_MACHINE),
        sysinfo.read_mic_consent("priv.mic_user", winreg.HKEY_CURRENT_USER),
        sysinfo.read_mic_consent_discord(),
        sysinfo.read_ducking(),
    ]
    assert accesses
    assert all(access == winreg.KEY_READ for access in accesses)
    for item in items:
        assert item.status in ("measured", "failed")
        assert (item.value is None) == (item.status == "failed")
        assert "\\" not in repr(item) and "#" not in repr(item)


def test_process_names_include_this_python():
    names = [n.casefold() for n in sysinfo.list_process_names()]
    assert os.path.basename(sys.executable).casefold() in names
    assert all(names)


def test_discord_running_by_name_only():
    def lister():
        return ["System", "discord.EXE", "other.exe"]

    found = sysinfo.read_discord_running(["Discord.exe"], lister)
    assert (found.status, found.value) == ("measured", True)
    assert found.note == "確認した名前: Discord.exe"
    absent = sysinfo.read_discord_running(["DiscordCanary.exe", "DiscordPTB.exe"], lister)
    assert (absent.status, absent.value) == ("measured", False)
    assert absent.note == "確認した名前: DiscordCanary.exe, DiscordPTB.exe"

    def broken():
        raise PermissionError("合成の失敗")

    failed = sysinfo.read_discord_running(["Discord.exe"], broken)
    assert (failed.status, failed.value, failed.reason) == ("failed", None, "PermissionError")


def test_sample_load_with_fake_clock():
    ticks = iter([(0, 0), (50, 100), (120, 200), (200, 300)])
    memory = iter([40, 55, 50])
    slept = []
    items = sysinfo.sample_load(3, 1000, times=lambda: next(ticks), memory=lambda: next(memory),
                                sleep=slept.append)
    assert slept == [1.0, 1.0, 1.0]
    assert [(i.id, i.status, i.value) for i in items] == [
        ("sys.cpu_avg_pct", "measured", 33.3),
        ("sys.cpu_max_pct", "measured", 50.0),
        ("sys.mem_load_pct", "measured", 55),
    ]


def test_sample_load_failures():
    def broken():
        raise OSError("合成の失敗")

    items = sysinfo.sample_load(2, 1000, times=broken, memory=lambda: 10, sleep=lambda _: None)
    assert [(i.status, i.value, i.reason) for i in items] == [
        ("failed", None, "OSError"), ("failed", None, "OSError"), ("measured", 10, None)]
    items = sysinfo.sample_load(2, 1000, times=lambda: (0, 0), memory=broken,
                                sleep=lambda _: None)
    assert [(i.status, i.value) for i in items] == [("failed", None)] * 3
    assert items[0].reason == "CPU 時間の差が取れなかった"
    assert items[2].reason == "OSError"


def test_real_load_readings_are_in_range():
    idle, total = sysinfo.system_times()
    assert 0 <= idle <= total
    assert 0 <= sysinfo.memory_load() <= 100
    items = sysinfo.sample_load(0.1, 50)
    assert [i.id for i in items] == ["sys.cpu_avg_pct", "sys.cpu_max_pct", "sys.mem_load_pct"]
    for item in items:
        if item.status == "measured":
            assert 0 <= item.value <= 100
