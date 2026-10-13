# 画面と実行層(runner.py)の継ぎ目。画面は Backend を通してだけ、設定・デバイス・測定・保存に触れる。
# テストと画像の作成では Backend を合成の物に差し替える(実機のマイクとスピーカーを使わない)。
# QtMultimedia はここでだけ読み込む。読み込めなければ SCR-05 になる(仕様書 §10)。
from __future__ import annotations

import importlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from vccheck.io_safe import OutputError, OutputExistsError
from vccheck.model import Measure
from vccheck.runner import (
    Capabilities,
    Config,
    RunHooks,
    RunOutput,
    RunRequest,
    capabilities,
    default_config_dir,
    load_config,
    output_stem,
    perform_run,
    resolve_results_dir,
    save_run,
)


@dataclass(frozen=True)
class Settings:
    """画面が使う設定。較正の件数は thresholds.json から数えた値(画面に直書きしない)。"""

    measure: Measure
    calibrated: int
    calibrated_total: int
    capabilities: Capabilities


@dataclass(frozen=True)
class DeviceList:
    """入力と出力のデバイスの名前。default_* は Qt の既定のデバイスの位置(無ければ None)。"""

    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    default_input: int | None = None
    default_output: int | None = None


@dataclass(frozen=True)
class SaveReport:
    """保存の結果。problem が None なら保存できた。exists は同名があって保存しなかった場合。"""

    stem: str
    folder: Path | None
    problem: str | None = None
    exists: bool = False


class MultimediaUnavailableError(Exception):
    """QtMultimedia を読み込めない(MSG-19)。"""


class Backend(Protocol):
    def load(self) -> Settings: ...

    def devices(self) -> DeviceList: ...

    def run(self, request: RunRequest, hooks: RunHooks) -> RunOutput: ...

    def save(self, output: RunOutput) -> SaveReport: ...

    def open_folder(self, folder: Path) -> None: ...


def _default_index(devices: list[Any]) -> int | None:
    for index, device in enumerate(devices):
        if device.isDefault():
            return index
    return None


def list_devices() -> DeviceList:
    """QMediaDevices から名前の一覧を取る。デバイスの ID は持ち出さない(INV-13)。"""
    # QtMultimedia は PySide6-Addons に入っていて、無い環境がある。名前で読み込んで確かめる。
    try:
        multimedia = importlib.import_module("PySide6.QtMultimedia")
    except ImportError as error:
        raise MultimediaUnavailableError(type(error).__name__) from error
    media: Any = multimedia.QMediaDevices
    inputs: list[Any] = list(media.audioInputs())
    outputs: list[Any] = list(media.audioOutputs())
    return DeviceList(
        tuple(str(device.description()) for device in inputs),
        tuple(str(device.description()) for device in outputs),
        _default_index(inputs),
        _default_index(outputs),
    )


def stem_of(output: RunOutput) -> str:
    """結果の JSON の測定日時から、ファイル名の「日時」の部分を作る。"""
    run = output.obj.get("run")
    stamp = run.get("measured_at") if isinstance(run, dict) else None
    if not isinstance(stamp, str):
        raise ValueError("結果に測定日時が無い")
    return output_stem(datetime.fromisoformat(stamp))


class DefaultBackend:
    """本物の実行層につなぐ。CLI の run と同じ perform_run を呼ぶ。"""

    def __init__(self, config_dir: Path | None = None, out_dir: Path | None = None) -> None:
        self._config_dir = default_config_dir() if config_dir is None else config_dir
        self._out_dir = out_dir
        self._config: Config | None = None

    def _loaded(self) -> Config:
        if self._config is None:
            self._config = load_config(self._config_dir)
        return self._config

    def load(self) -> Settings:
        config = self._loaded()
        done, total = config.thresholds.calibrated_counts()
        return Settings(config.measure, done, total, capabilities())

    def devices(self) -> DeviceList:
        return list_devices()

    def run(self, request: RunRequest, hooks: RunHooks) -> RunOutput:
        return perform_run(request, self._loaded(), measured_at=datetime.now(), hooks=hooks)

    def save(self, output: RunOutput) -> SaveReport:
        stem = stem_of(output)
        try:
            folder = self._out_dir or resolve_results_dir(self._loaded().measure.results_dir)
        except OutputError as error:
            return SaveReport(stem, None, str(error))
        folder = folder.resolve()
        try:
            save_run(output, folder, stem)
        except OutputExistsError as error:
            return SaveReport(stem, folder, str(error), exists=True)
        except OutputError as error:
            return SaveReport(stem, folder, str(error))
        return SaveReport(stem, folder)

    def open_folder(self, folder: Path) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
