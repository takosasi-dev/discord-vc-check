# GUI のテスト(画面設計書 §12 の AC-G1〜G7・G9・G11・G12 と、DEV-90 の逸脱)。
# 測定部は合成のスタブ(gui_stub.py)に差し替え、実機のマイクとスピーカーは使わない。
# PySide6 が無い環境では、このファイルを丸ごと飛ばす。
from __future__ import annotations

import json
import shutil
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QAbstractButton,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QLabel,
    QListWidget,
    QPushButton,
    QRadioButton,
    QScrollBar,
    QTableWidget,
    QWidget,
)

from tests.fixtures import synth  # noqa: E402
from tests.fixtures.gui_stub import (  # noqa: E402
    ALL_CAPABILITIES,
    DEVICES,
    FIXTURES,
    NO_CAPABILITIES,
    StubBackend,
    fixture_request,
    make_output,
)
from vccheck import report_md, rules, runner, sysinfo  # noqa: E402
from vccheck.gui import texts  # noqa: E402
from vccheck.gui.app import run_gui  # noqa: E402
from vccheck.gui.backend import DefaultBackend, DeviceList, SaveReport  # noqa: E402
from vccheck.gui.dialogs import (  # noqa: E402
    ConfigErrorDialog,
    ToneConfirmDialog,
    ToneHeardDialog,
)
from vccheck.gui.result_screen import ResultScreen  # noqa: E402
from vccheck.gui.screens import estimate_seconds  # noqa: E402
from vccheck.gui.window import MainWindow  # noqa: E402
from vccheck.model import (  # noqa: E402
    REASON_S5,
    REASON_S6,
    REASON_TONE_NOT_DECLARED,
    WORDS,
    check_result,
    failed,
    measured,
)
from vccheck.runner import Capabilities  # noqa: E402

FORBIDDEN_WORDS = ("問題なし", "合格", "大丈夫")
SCR01_IDS = ["STEP-01", *(f"SYM-{n:02d}" for n in range(1, 15))]
SCR02_IDS = ["STEP-01", *(f"DEV-{n:02d}" for n in range(1, 13))]
SCR03_IDS = ["STEP-01", *(f"RUN-{n:02d}" for n in range(1, 5))]
SCR04_IDS = [f"RES-{n:02d}" for n in range(1, 15)]
SCR05_IDS = ["ERR-01", "ERR-02"]


@pytest.fixture(scope="session")
def app() -> QApplication:
    existing = QApplication.instance()
    return existing if isinstance(existing, QApplication) else QApplication([])


def wait_for(condition: Callable[[], bool], timeout_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not condition():
        assert time.monotonic() < deadline, "時間内に条件が成り立たなかった"
        QTest.qWait(10)


def names(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir()) if folder.exists() else []


def child(root: QWidget, name: str) -> QWidget:
    found = root.findChild(QWidget, name)
    assert found is not None, f"{root.objectName()} に {name} が無い"
    return found


def button(root: QWidget, name: str) -> QAbstractButton:
    found = child(root, name)
    assert isinstance(found, QAbstractButton)
    return found


def label_text(root: QWidget, name: str) -> str:
    found = child(root, name)
    assert isinstance(found, QLabel)
    return found.text()


def all_texts(root: QWidget) -> list[str]:
    """ウィジェットツリー全体の文字列(AC-G4・AC-G5 の走査)。"""
    found = [root.windowTitle()]
    for widget in [root, *root.findChildren(QWidget)]:
        found += [widget.toolTip(), widget.windowTitle()]
        if isinstance(widget, (QLabel, QAbstractButton)):
            found.append(widget.text())
        elif isinstance(widget, QComboBox):
            found += [widget.itemText(i) for i in range(widget.count())]
        elif isinstance(widget, QListWidget):
            found += [widget.item(i).text() for i in range(widget.count())]
        elif isinstance(widget, QTableWidget):
            for column in range(widget.columnCount()):
                header = widget.horizontalHeaderItem(column)
                found.append(header.text() if header is not None else "")
                for row in range(widget.rowCount()):
                    cell = widget.item(row, column)
                    found.append(cell.text() if cell is not None else "")
    if isinstance(root, MainWindow):
        found.append(root.statusBar().currentMessage())
    return [text for text in found if text]


def table_rows(table: QWidget) -> list[list[str]]:
    assert isinstance(table, QTableWidget)
    rows = []
    for row in range(table.rowCount()):
        cells = [table.item(row, column) for column in range(table.columnCount())]
        rows.append([cell.text() if cell is not None else "" for cell in cells])
    return rows


@pytest.fixture
def make_window(app: QApplication) -> Iterator[Callable[..., MainWindow]]:
    opened: list[MainWindow] = []

    def make(backend: StubBackend | DefaultBackend) -> MainWindow:
        window = MainWindow(backend, backend.load())
        window.show()
        window.activateWindow()
        QTest.qWait(0)
        opened.append(window)
        return window

    yield make
    for window in opened:
        window.shutdown()
        window.close()
        window.deleteLater()
    QTest.qWait(0)


def go_to_devices(window: MainWindow) -> None:
    button(window.symptom_screen, "SYM-11").click()
    assert window.current_screen() is window.device_screen


def run_to_result(window: MainWindow) -> None:
    go_to_devices(window)
    button(window.device_screen, "DEV-11").click()
    wait_for(lambda: window.current_screen() is window.result_screen)


def press(key: Qt.Key, fallback: QWidget) -> None:
    """いまフォーカスのある部品にキーを送る(無ければ fallback)。"""
    target = QApplication.focusWidget() or fallback
    QTest.keyClick(target, key)


def find_dialog(window: MainWindow, name: str) -> QDialog:
    found = [d for d in window.findChildren(QDialog, name) if d.isVisible()]
    assert len(found) == 1, f"{name} が出ていない"
    # ダイアログがキー入力を受け取る状態になるまで待つ。
    wait_for(lambda: QApplication.activeWindow() is found[0])
    return found[0]


# ---- AC-G1: 通しと、要素 ID ----


def test_ac_g1_walkthrough_with_the_stub_and_every_element_id(make_window, tmp_path):
    backend = StubBackend(out_dir=tmp_path / "results")
    window = make_window(backend)
    assert window.windowTitle() == "Discord(VcCheck)"
    assert (window.minimumWidth(), window.minimumHeight()) == (720, 560)
    assert (window.width(), window.height()) == (880, 640)
    assert window.current_screen() is window.symptom_screen
    button(window.symptom_screen, "SYM-01").click()
    go_to_devices(window)
    button(window.device_screen, "DEV-11").click()
    assert window.current_screen() is window.run_screen
    wait_for(lambda: window.current_screen() is window.result_screen)
    assert [stage for stage, _ in backend.stages] == [
        "prepare", "mic_silent", "mic_speech", "net", "summary"]
    assert backend.calls[0].symptoms == ("crackle",)
    assert backend.calls[0].input_declared == synth.DEVICE_IN
    assert backend.calls[0].output_declared == synth.DEVICE_OUT
    assert backend.calls[0].input_default_used is True
    for screen, ids in ((window.symptom_screen, SCR01_IDS), (window.device_screen, SCR02_IDS),
                        (window.run_screen, SCR03_IDS), (window.result_screen, SCR04_IDS),
                        (window.blocked_screen, SCR05_IDS)):
        for name in ids:
            child(screen, name)
    assert [s.objectName() for s in (window.symptom_screen, window.device_screen,
                                     window.run_screen, window.result_screen,
                                     window.blocked_screen)] == [
        "SCR-01", "SCR-02", "SCR-03", "SCR-04", "SCR-05"]
    # STEP-01 は SCR-01〜03 にだけ出す。
    assert label_text(window.symptom_screen, "STEP-01") == "手順 1 / 3  症状"
    assert label_text(window.device_screen, "STEP-01") == "手順 2 / 3  デバイスと測定内容"
    assert label_text(window.run_screen, "STEP-01") == "手順 3 / 3  測定"
    assert window.result_screen.findChild(QWidget, "STEP-01") is None
    assert window.blocked_screen.findChild(QWidget, "STEP-01") is None


def test_ac_g1_dialog_element_ids(app):
    confirm = ToneConfirmDialog()
    heard = ToneHeardDialog()
    broken = ConfigErrorDialog("rules.json")
    assert [d.objectName() for d in (confirm, heard, broken)] == ["DLG-01", "DLG-02", "DLG-03"]
    for dialog, ids in ((confirm, ["DLG-01-1", "DLG-01-2", "DLG-01-3"]),
                        (heard, ["DLG-02-1", "DLG-02-2", "DLG-02-3"]),
                        (broken, ["DLG-03-1", "DLG-03-2"])):
        for name in ids:
            child(dialog, name)
    assert label_text(confirm, "DLG-01-1") == texts.MSG_16
    assert label_text(heard, "DLG-02-1") == texts.MSG_17
    assert [r.text() for r in heard.findChildren(QRadioButton)] == [
        "正常に聞こえた", "音が割れていた", "音が小さかった", "聞こえなかった"]
    assert label_text(broken, "DLG-03-1") == (
        "設定ファイル rules.json の形が壊れています。測定を始めません")
    assert button(broken, "DLG-03-2").text() == "閉じる (Esc・Enter)"


def test_ac_g1_walkthrough_with_the_real_run_function(make_window, tmp_path, monkeypatch):
    """本物の実行層(CLI の run と同じ perform_run)を通す。読み取りだけ合成の値にする。"""

    def fake_collect(_measure, **_hooks):
        return [
            measured("env.discord_running", True, "確認した名前: Discord.exe"),
            measured("priv.mic_machine", "Allow"),
            measured("priv.mic_user", "Deny"),
            failed("priv.mic_discord", "Discord のエントリが無い"),
            measured("duck.pref", 1),
            measured("sys.cpu_avg_pct", 12.3),
            measured("sys.cpu_max_pct", 45.6),
            measured("sys.mem_load_pct", 78),
        ]

    class Backend(DefaultBackend):
        def devices(self) -> DeviceList:
            return DEVICES

    monkeypatch.setattr(sysinfo, "collect", fake_collect)
    out = tmp_path / "results"
    window = make_window(Backend(synth.CONFIG_DIR, out))
    button(window.symptom_screen, "SYM-08").click()
    go_to_devices(window)
    combo = child(window.device_screen, "DEV-01")
    assert isinstance(combo, QComboBox)
    combo.setCurrentIndex(1)
    button(window.device_screen, "DEV-11").click()
    wait_for(lambda: window.current_screen() is window.result_screen)
    stages = child(window.run_screen, "RUN-03")
    assert isinstance(stages, QListWidget)
    assert [stages.item(i).text() for i in range(stages.count())] == [
        "[完了] 準備", "[飛ばした] マイク: 黙る", "[飛ばした] マイク: 話す", "[飛ばした] 回線",
        "[飛ばした] テスト音", "[実行中] まとめ"]
    written = names(out)
    assert len(written) == 3 and written[0].endswith(".json")
    stem = written[0][: -len(".json")]
    obj = json.loads((out / written[0]).read_text(encoding="utf-8"))
    assert check_result(obj) == []
    assert obj["run"]["symptoms"] == ["duck"]
    assert obj["run"]["conditions"]["input_declared"] == "Webcam Mic (TestUser-PC)"
    assert obj["run"]["conditions"]["output_declared"] == synth.DEVICE_OUT
    assert obj["run"]["conditions"]["input_default_used"] is False
    assert obj["run"]["conditions"]["channels_used"] is None
    by_id = {item["id"]: item for item in obj["items"]}
    assert by_id["mic.clip_events"]["status"] == "unchecked"
    assert by_id["net.gw.rtt_p95_ms"]["status"] == "unchecked"
    assert by_id["tone.heard"]["reason"] == "テスト音は確認なしでは再生しない"
    screen = window.result_screen
    assert label_text(screen, "RES-13") == f"保存先: {out.resolve() / (stem + '.json')} ほか 2 件"
    assert window.statusBar().currentMessage() == f"結果を保存しました: {stem}"
    assert label_text(screen, "RES-03") == (
        "閾値の較正: 13 項目中 0 項目(未較正の項目は「判定なし」と出ます)")
    assert "入力 Webcam Mic (TestUser-PC)(申告) / 出力 Speakers (TestUser-PC)(申告)" in (
        label_text(screen, "RES-02"))
    assert [row[1] for row in table_rows(child(screen, "RES-04"))] == [
        "[異常]", "[判定なし]", "[未チェック]", "[判定なし]", "[未チェック]", "[未チェック]"]
    assert "Windows の通信時の音量調整" in label_text(screen, "RES-05")


# ---- AC-G2: 症状 ----


def test_ac_g2_no_symptom_is_allowed_and_selection_is_reset(make_window, tmp_path):
    backend = StubBackend(out_dir=tmp_path / "results")
    window = make_window(backend)
    checks = [button(window.symptom_screen, f"SYM-{n:02d}") for n in range(1, 9)]
    assert [c.text() for c in checks] == [text for _, text in texts.SYMPTOM_LABELS]
    assert all(isinstance(c, QCheckBox) and not c.isChecked() for c in checks)
    assert button(window.symptom_screen, "SYM-11").isEnabled()
    assert child(window.symptom_screen, "SYM-14").isHidden()
    run_to_result(window)
    assert backend.calls[0].symptoms == ()
    assert label_text(window.result_screen, "RES-02").startswith("症状: 選ばなかった\n")
    button(window.result_screen, "RES-11").click()
    assert window.current_screen() is window.symptom_screen
    checks[1].click()
    checks[6].click()
    run_to_result(window)
    assert backend.calls[1].symptoms == ("quiet", "other_side")
    assert label_text(window.result_screen, "RES-02").startswith(
        "症状: 自分の声が小さい・聞こえにくいと言われた・相手の声が割れる・小さい\n")
    button(window.result_screen, "RES-11").click()
    assert not any(c.isChecked() for c in checks)
    assert window.statusBar().currentMessage() == ""


def test_symptom_screen_texts_and_calibration_counts_come_from_thresholds(make_window, tmp_path):
    window = make_window(StubBackend(fixture="uncalibrated"))
    screen = window.symptom_screen
    assert label_text(screen, "SYM-09") == texts.MSG_01
    assert label_text(screen, "SYM-10") == texts.MSG_02
    assert label_text(screen, "SYM-12") == (
        "閾値の較正: 13 項目中 0 項目(未較正の項目は「判定なし」と出ます)")
    assert button(screen, "SYM-11").text() == "次へ (Enter)"
    assert button(screen, "SYM-13").text() == "閉じる (Esc)"
    # thresholds.json の数値を2項目だけ埋めた config\ では、n が 2 になる。
    config = tmp_path / "config"
    shutil.copytree(synth.CONFIG_DIR, config)
    numbers = {"mic.clip_events": (0, 2), "sys.cpu_avg_pct": (70, 90), "mic.snr_db": (20, None)}
    (config / "thresholds.json").write_text(
        json.dumps(synth.thresholds_obj(numbers)), encoding="utf-8")
    settings = DefaultBackend(config).load()
    assert (settings.calibrated, settings.calibrated_total) == (2, 13)
    assert settings.capabilities == runner.capabilities()


# ---- AC-G3 と DEV-90 の逸脱: 測る内容 ----


def test_ac_g3_nothing_selected_disables_start_when_the_backend_can_measure(make_window):
    window = make_window(StubBackend(capabilities=ALL_CAPABILITIES))
    go_to_devices(window)
    screen = window.device_screen
    mic, net, tone = (button(screen, f"DEV-{n:02d}") for n in (4, 5, 6))
    start = button(screen, "DEV-11")
    assert (mic.isChecked(), net.isChecked(), tone.isChecked()) == (True, True, False)
    assert all(c.isEnabled() for c in (mic, net, tone))
    assert start.isEnabled() and child(screen, "DEV-12").isHidden()
    assert child(screen, "DEV-90").isHidden()
    mic.click()
    net.click()
    assert not start.isEnabled()
    assert not child(screen, "DEV-12").isHidden()
    assert child(screen, "DEV-12").isVisible()
    assert label_text(screen, "DEV-12") == "測定内容を1つ以上選んでください"
    assert child(screen, "DEV-12").font().bold()
    assert label_text(screen, "DEV-07").startswith("所要時間の目安: 約 0 秒")
    for check in (mic, net, tone):
        check.click()
        assert start.isEnabled() and child(screen, "DEV-12").isHidden()
        check.click()
        assert not start.isEnabled() and not child(screen, "DEV-12").isHidden()
    # 始められないときは、Enter でも始まらない。
    QTest.keyClick(screen, Qt.Key.Key_Return)
    assert window.current_screen() is screen
    assert child(screen, "DEV-90").isHidden()


def test_deviation_dev90_when_the_backend_can_measure_nothing(make_window, tmp_path):
    """本物の capabilities(すべて False)の世界。DEV-11 は有効のままで、DEV-90 を出す。"""
    assert runner.capabilities() == Capabilities(mic=False, net=False, tone=False)
    backend = StubBackend(capabilities=runner.capabilities(), out_dir=tmp_path / "results")
    window = make_window(backend)
    go_to_devices(window)
    screen = window.device_screen
    for name in ("DEV-04", "DEV-05", "DEV-06"):
        assert not button(screen, name).isChecked()
        assert not button(screen, name).isEnabled()
    assert button(screen, "DEV-11").isEnabled()
    assert child(screen, "DEV-12").isHidden()
    assert child(screen, "DEV-90").isVisible()
    assert label_text(screen, "DEV-90") == (
        "この版では、マイク・回線・テスト音の測定はまだ実装していません。"
        "Windows の設定と PC の負荷だけを測ります")
    assert label_text(screen, "DEV-07") == (
        "所要時間の目安: 約 0 秒(設定値の合計。準備と保存の時間は含みません)")
    button(screen, "DEV-11").click()
    wait_for(lambda: window.current_screen() is window.result_screen)
    request = backend.calls[0]
    assert (request.skip_mic, request.skip_net, request.confirm_tone) == (True, True, False)
    assert runner.unsupported_reason(request) is None
    stages = child(window.run_screen, "RUN-03")
    assert isinstance(stages, QListWidget)
    assert [stages.item(i).text().split(" ")[0] for i in range(1, 5)] == ["[飛ばした]"] * 4


def test_partial_capabilities_keep_the_design_rule(make_window):
    window = make_window(StubBackend(capabilities=Capabilities(mic=True, net=False, tone=False)))
    go_to_devices(window)
    screen = window.device_screen
    mic, net, tone = (button(screen, f"DEV-{n:02d}") for n in (4, 5, 6))
    assert (mic.isEnabled(), net.isEnabled(), tone.isEnabled()) == (True, False, False)
    assert (mic.isChecked(), net.isChecked(), tone.isChecked()) == (True, False, False)
    assert child(screen, "DEV-90").isHidden()
    mic.click()
    assert not button(screen, "DEV-11").isEnabled()
    assert not child(screen, "DEV-12").isHidden()


def test_device_screen_lists_devices_and_hidden_note(make_window):
    window = make_window(StubBackend())
    go_to_devices(window)
    screen = window.device_screen
    first, second = child(screen, "DEV-01"), child(screen, "DEV-02")
    assert isinstance(first, QComboBox) and isinstance(second, QComboBox)
    assert [first.itemText(i) for i in range(first.count())] == [
        "Headset (TestUser-PC) (既定)", "Webcam Mic (TestUser-PC)"]
    assert second.currentText() == "Speakers (TestUser-PC) (既定)"
    assert label_text(screen, "DEV-03") == texts.MSG_04
    assert label_text(screen, "DEV-08") == texts.MSG_06
    assert button(screen, "DEV-04").text() == "マイクを測る(黙る 3 秒 → 話す 7 秒)"
    assert button(screen, "DEV-05").text() == "回線を測る"
    assert button(screen, "DEV-06").text() == "出力のテスト音も確認する(音が鳴ります)"
    assert label_text(screen, "DEV-07").startswith("所要時間の目安: 約 21 秒")
    button(screen, "DEV-06").click()
    assert label_text(screen, "DEV-07").startswith("所要時間の目安: 約 23 秒")
    # DEV-09(MSG-08)は、仕様書 Q-13 が確認されるまで出さない。
    assert label_text(screen, "DEV-09") == texts.MSG_08
    assert child(screen, "DEV-09").isHidden()
    settings = StubBackend().load()
    assert estimate_seconds(settings, mic=True, net=False, tone=False) == 11
    assert estimate_seconds(settings, mic=False, net=True, tone=False) == 10
    assert estimate_seconds(settings, mic=False, net=False, tone=True) == 2


def test_no_output_device_disables_the_tone_check(make_window, tmp_path):
    backend = StubBackend(devices=DeviceList((synth.DEVICE_IN,), ()),
                          out_dir=tmp_path / "results")
    window = make_window(backend)
    go_to_devices(window)
    screen = window.device_screen
    combo = child(screen, "DEV-02")
    assert isinstance(combo, QComboBox) and combo.count() == 0
    assert not button(screen, "DEV-06").isEnabled()
    button(screen, "DEV-11").click()
    wait_for(lambda: window.current_screen() is window.result_screen)
    assert backend.calls[0].output_declared is None
    assert backend.calls[0].input_default_used is False
    assert "入力 Headset (TestUser-PC)(申告) / 出力 —" in label_text(
        window.result_screen, "RES-02")


# ---- SCR-05・DLG-03 ----


def test_no_input_device_shows_scr05(make_window):
    window = make_window(StubBackend(devices=DeviceList((), (synth.DEVICE_OUT,))))
    assert window.current_screen() is window.blocked_screen
    assert label_text(window.blocked_screen, "ERR-01") == texts.MSG_18
    assert button(window.blocked_screen, "ERR-02").text() == "閉じる (Esc・Enter)"
    assert window.exit_code == 2
    QTest.keyClick(window.blocked_screen, Qt.Key.Key_Return)
    assert not window.isVisible()


def test_missing_qtmultimedia_shows_scr05(make_window):
    window = make_window(StubBackend(devices=None))
    assert window.current_screen() is window.blocked_screen
    assert label_text(window.blocked_screen, "ERR-01") == (
        "QtMultimedia を読み込めないため、測定を始められません")
    assert window.exit_code == 2
    assert window.multimedia_loaded is False
    QTest.keyClick(window.blocked_screen, Qt.Key.Key_Escape)
    assert not window.isVisible()


def test_broken_config_ends_with_dlg03_and_exit_code_1(app, tmp_path, capsys):
    config = tmp_path / "config"
    shutil.copytree(synth.CONFIG_DIR, config)
    (config / "rules.json").write_text("{", encoding="utf-8")
    with pytest.raises(runner.ConfigFileError) as caught:
        DefaultBackend(config).load()
    assert caught.value.file_name == "rules.json"
    assert run_gui(config_dir=config, smoke=True) == 1
    assert "rules.json" in capsys.readouterr().out


def test_smoke_mode_builds_the_window_and_exits_zero(app, capsys):
    assert run_gui(smoke=True, backend=StubBackend()) == 0
    assert capsys.readouterr().out.strip() == (
        "gui smoke: QtMultimedia 読み込めた / 入力 2 台 / 出力 2 台 / 画面 SCR-01")


# ---- AC-G4・AC-G5: 文言 ----


def test_ac_g4_fixed_sentences_match_the_spec(make_window, tmp_path):
    assert texts.MSG_25 == rules.NO_CANDIDATE_TEXT
    assert texts.MSG_28 == report_md.FOOTER.removeprefix("- ")
    assert texts.MSG_26 == "S5: " + REASON_S5
    assert texts.MSG_27 == "S6: " + REASON_S6
    assert texts.MSG_31 == REASON_TONE_NOT_DECLARED
    window = make_window(StubBackend(fixture="normal", out_dir=tmp_path / "results"))
    run_to_result(window)
    found = all_texts(window)
    assert rules.NO_CANDIDATE_TEXT in found
    assert report_md.FOOTER.removeprefix("- ") in found
    assert [t for t in found if t.startswith("S5: ")] == ["S5: " + REASON_S5]
    assert [t for t in found if t.startswith("S6: ")] == ["S6: " + REASON_S6]
    assert label_text(window.result_screen, "RES-05") == rules.NO_CANDIDATE_TEXT
    assert label_text(window.result_screen, "RES-08") == report_md.FOOTER.removeprefix("- ")
    # 詳細の s5・s6 の理由も、結果の JSON の文のまま出る。
    rows = table_rows(child(window.result_screen, "RES-07-unchecked-body"))
    assert ["S5", "Discord の内部の状態", REASON_S5] in rows
    assert ["S6", "通話相手の環境", REASON_S6] in rows


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_ac_g5_every_fixture_draws_without_forbidden_words(app, name):
    assert len(FIXTURES) == 10
    output = make_output(name, fixture_request(name))
    screen = ResultScreen()
    screen.show_result(output.obj, SaveReport("2026-10-01_140500", None))
    found = all_texts(screen)
    for word in FORBIDDEN_WORDS:
        assert not [t for t in found if word in t]
    rows = table_rows(child(screen, "RES-04"))
    assert len(rows) == 6
    assert [row[0][:2] for row in rows] == ["S1", "S2", "S3", "S4", "S5", "S6"]
    assert rows[4][1] == "[未チェック]" and rows[5][1] == "[未チェック]"
    result = synth.RESULTS[name]
    assert [row[1] for row in rows] == [
        f"[{dict(WORDS)[seg.status]}]" for seg in result.segments]
    assert [row[2] for row in rows] == [f"{seg.unchecked_count} 件" for seg in result.segments]
    # 候補の文は「可能性」で終わる。0件なら固定文だけ。
    lines = label_text(screen, "RES-05").split("\n")
    if result.candidates:
        heads = [line for line in lines if not line.startswith("    ")]
        assert len(heads) == len(result.candidates) <= 3
        assert all(line.endswith("可能性") for line in heads)
        assert [line[4:].split(":")[0] for line in lines if line.startswith("    ")] == [
            "根拠", "次に見る場所"] * len(result.candidates)
    else:
        assert lines == [rules.NO_CANDIDATE_TEXT]
    # 7語の件数と、7つの節。件数は結果の JSON の値のまま。
    counts = label_text(screen, "RES-06")
    assert counts.replace("\n", "  ") == "  ".join(
        f"[{label}] {result.counts[key]}" for key, label in WORDS)
    headers = screen.section_headers()
    assert [h.text() for h in headers] == [
        f"[{label}] {result.counts[key]}件" for key, label in WORDS]
    for header, (key, _) in zip(headers, WORDS, strict=True):
        count = result.counts[key]
        assert header.is_open() == (key in ("bad", "warn", "failed") and count >= 1)
        body = child(screen, f"RES-07-{key}-body")
        assert body.isHidden() != header.is_open()
        if count == 0:
            assert isinstance(body, QLabel) and body.text() == "なし"
        else:
            assert len(table_rows(body)) == count
    measured_count = sum(1 for item in result.items if item.status == "measured")
    assert child(screen, "RES-14").isHidden() == (measured_count > 0)
    assert label_text(screen, "RES-14") == texts.MSG_29
    screen.deleteLater()


def test_result_values_follow_the_display_rules(app):
    output = make_output("mixed", fixture_request("mixed"))
    screen = ResultScreen()
    screen.show_result(output.obj, SaveReport("2026-10-01_140500", None))
    by_name: dict[str, list[str]] = {}
    for key, _ in WORDS:
        body = child(screen, f"RES-07-{key}-body")
        if isinstance(body, QTableWidget):
            by_name.update({row[1]: row for row in table_rows(body)})
    assert by_name["クリップの回数"][2:5] == ["0 回", "実測", "正常 (0 <= 0)"]
    assert by_name["メモリ使用率の最大"][2] == "50.0 %"
    assert by_name["Discord の起動"][2] == "はい"
    assert by_name["Discord の起動"][4] == "—"
    assert by_name["テスト音の聞こえ方"][2:5] == ["聞こえない", "申告", "異常 (none = bad)"]
    assert by_name["接続の失敗の割合(Discord 側)"][2] == "100.0 %"
    assert by_name["入力デバイスの形式"][2] == "48000 Hz / 1 ch / Int16"
    assert by_name["通信時の音量調整の設定値"] == [
        "S2", "通信時の音量調整の設定値", "値が無い"]
    assert label_text(screen, "RES-01") == "通話の音の切り分け  2026-10-01 14:05"
    assert label_text(screen, "RES-02") == (
        "症状: 相手の声が割れる・小さい・自分の声が小さい・聞こえにくいと言われた\n"
        "入力 Headset (TestUser-PC)(申告) / 出力 Speakers (TestUser-PC)(申告)")
    assert label_text(screen, "RES-03") == (
        "閾値の較正: 13 項目中 13 項目(未較正の項目は「判定なし」と出ます)")
    candidates = label_text(screen, "RES-05").split("\n")
    assert candidates[0] == "1. 出力デバイスの選択が違うか、音が出ていない可能性"
    assert candidates[1] == "    根拠: テスト音の聞こえ方 聞こえない"
    assert candidates[2] == "    次に見る場所: —"
    screen.deleteLater()


def test_default_device_is_marked_in_the_result(app):
    request = fixture_request("normal")
    output = make_output("normal", runner.RunRequest(
        symptoms=(), input_declared=request.input_declared,
        output_declared=request.output_declared, input_default_used=True))
    screen = ResultScreen()
    screen.show_result(output.obj, SaveReport("2026-10-01_140500", None))
    assert label_text(screen, "RES-02") == (
        "症状: 選ばなかった\n"
        "入力 Headset (TestUser-PC)(既定を使った)(申告) / 出力 Speakers (TestUser-PC)(申告)")
    assert not button(screen, "RES-10").isEnabled()
    screen.deleteLater()


# ---- AC-G6: 中止 ----


def test_ac_g6_cancel_stops_the_measurement_and_saves_nothing(make_window, tmp_path):
    out = tmp_path / "results"
    backend = StubBackend(out_dir=out, stage_seconds=60.0)
    window = make_window(backend)
    go_to_devices(window)
    button(window.device_screen, "DEV-11").click()
    wait_for(lambda: ("mic_silent", 3.3) in backend.stages)
    wait_for(lambda: label_text(window.run_screen, "RUN-01") == "黙ってください")
    # 測定中も画面は応答する(別のスレッドで測っている)。
    assert window.is_measuring()
    assert label_text(window.run_screen, "RUN-02") in ("4", "3")
    started = time.monotonic()
    button(window.run_screen, "RUN-04").click()
    wait_for(lambda: window.current_screen() is window.symptom_screen)
    assert time.monotonic() - started < 3.0
    assert not window.is_measuring()
    assert child(window.symptom_screen, "SYM-14").isVisible()
    assert label_text(window.symptom_screen, "SYM-14") == (
        "測定を中止しました(何も保存されていません)")
    assert names(out) == []
    assert [stage for stage, _ in backend.stages] == ["prepare", "mic_silent"]
    assert window.statusBar().currentMessage() == ""
    # 「次へ」を押すと MSG-09 は消える。
    button(window.symptom_screen, "SYM-11").click()
    assert child(window.symptom_screen, "SYM-14").isHidden()


def test_ac_g6_closing_the_window_and_esc_cancel_like_the_button(make_window, tmp_path):
    out = tmp_path / "results"
    backend = StubBackend(out_dir=out, stage_seconds=60.0)
    window = make_window(backend)
    for cancel in (window.close, lambda: QTest.keyClick(window.run_screen, Qt.Key.Key_Escape)):
        go_to_devices(window)
        button(window.device_screen, "DEV-11").click()
        wait_for(lambda: label_text(window.run_screen, "RUN-01") == "黙ってください")
        QTest.keyClick(window.run_screen, Qt.Key.Key_Return)
        QTest.keyClick(window.run_screen, Qt.Key.Key_Space)
        assert window.current_screen() is window.run_screen
        cancel()
        wait_for(lambda: window.current_screen() is window.symptom_screen)
        assert window.isVisible()
        assert child(window.symptom_screen, "SYM-14").isVisible()
    assert names(out) == []
    assert len(backend.calls) == 2


def test_ac_g6_cancel_reaches_the_real_run_function(make_window, tmp_path, monkeypatch):
    """本物の perform_run が、負荷を測る待ちの途中で中止の合図を受け取る。"""
    waits: list[float] = []

    def slow_collect(_measure, *, sleep):
        waits.append(0.0)
        sleep(60.0)
        raise AssertionError("中止の後に測定が続いた")

    class Backend(DefaultBackend):
        def devices(self) -> DeviceList:
            return DEVICES

    monkeypatch.setattr(sysinfo, "collect", slow_collect)
    out = tmp_path / "results"
    window = make_window(Backend(synth.CONFIG_DIR, out))
    go_to_devices(window)
    button(window.device_screen, "DEV-11").click()
    wait_for(lambda: bool(waits))
    assert label_text(window.run_screen, "RUN-01") == "測定を準備しています"
    assert child(window.run_screen, "RUN-02").isHidden()
    button(window.run_screen, "RUN-04").click()
    wait_for(lambda: window.current_screen() is window.symptom_screen, timeout_s=3.0)
    assert child(window.symptom_screen, "SYM-14").isVisible()
    assert names(out) == []


def test_ac_g6_cancel_right_after_the_result_is_ready_saves_nothing(make_window, tmp_path):
    """結果が出た直後(画面がまだ受け取っていない間)に中止を押しても、保存しない。"""
    out = tmp_path / "results"
    backend = StubBackend(out_dir=out)
    backend.hold_at = "summary"
    window = make_window(backend)
    go_to_devices(window)
    button(window.device_screen, "DEV-11").click()
    assert backend.reached.wait(5.0)
    backend.release.set()
    assert backend.returned.wait(5.0)
    time.sleep(0.1)
    assert window.current_screen() is window.run_screen
    button(window.run_screen, "RUN-04").click()
    wait_for(lambda: window.current_screen() is window.symptom_screen)
    assert child(window.symptom_screen, "SYM-14").isVisible()
    assert names(out) == []


def test_run_screen_shows_stage_marks_and_remaining_seconds(make_window, tmp_path):
    backend = StubBackend(out_dir=tmp_path / "results")
    backend.hold_at = "mic_speech"
    window = make_window(backend)
    go_to_devices(window)
    button(window.device_screen, "DEV-11").click()
    assert backend.reached.wait(5.0)
    wait_for(lambda: label_text(window.run_screen, "RUN-01") == "普通の声で話してください")
    stages = child(window.run_screen, "RUN-03")
    assert isinstance(stages, QListWidget)
    assert [stages.item(i).text() for i in range(stages.count())] == [
        "[完了] 準備", "[完了] マイク: 黙る", "[実行中] マイク: 話す", "[待ち] 回線",
        "[飛ばした] テスト音", "[待ち] まとめ"]
    assert label_text(window.run_screen, "RUN-02") == "7"
    assert child(window.run_screen, "RUN-02").isVisible()
    assert button(window.run_screen, "RUN-04").text() == "中止 (Esc)"
    backend.release.set()
    wait_for(lambda: window.current_screen() is window.result_screen)
    assert child(window.run_screen, "RUN-02").isHidden()
    assert label_text(window.run_screen, "RUN-01") == "結果をまとめています"


# ---- AC-G7: テスト音 ----


def test_ac_g7_tone_needs_confirmation_and_enter_does_not_play(make_window, tmp_path):
    backend = StubBackend(out_dir=tmp_path / "results")
    window = make_window(backend)
    go_to_devices(window)
    screen = window.device_screen
    button(screen, "DEV-06").click()
    for key in (Qt.Key.Key_Return, Qt.Key.Key_Escape):
        button(screen, "DEV-11").click()
        dialog = find_dialog(window, "DLG-01")
        assert dialog.isModal()
        refuse, play = button(dialog, "DLG-01-3"), button(dialog, "DLG-01-2")
        assert isinstance(refuse, QPushButton) and isinstance(play, QPushButton)
        assert refuse.text() == "鳴らさない" and play.text() == "鳴らす"
        assert refuse.isDefault() and not play.isDefault() and not play.autoDefault()
        press(key, dialog)
        wait_for(lambda dialog=dialog: not dialog.isVisible())
        assert window.current_screen() is screen
        assert button(screen, "DEV-06").isChecked()
    # 「鳴らす」にフォーカスがあっても、Enter では鳴らさない。
    button(screen, "DEV-11").click()
    dialog = find_dialog(window, "DLG-01")
    play = button(dialog, "DLG-01-2")
    play.setFocus()
    QTest.keyClick(play, Qt.Key.Key_Return)
    wait_for(lambda: not dialog.isVisible())
    assert window.current_screen() is screen
    # 測定(再生)は1度も始まっていない。
    assert backend.calls == [] and backend.stages == []
    assert not window.is_measuring()


def test_ac_g7_unanswered_tone_dialog_leaves_tone_heard_unchecked(make_window, tmp_path):
    out = tmp_path / "results"
    backend = StubBackend(fixture="normal", out_dir=out)
    window = make_window(backend)
    go_to_devices(window)
    button(window.device_screen, "DEV-06").click()
    button(window.device_screen, "DEV-11").click()
    confirm = find_dialog(window, "DLG-01")
    play = button(confirm, "DLG-01-2")
    play.setFocus()
    QTest.keyClick(play, Qt.Key.Key_Space)
    wait_for(lambda: bool(window.findChildren(QDialog, "DLG-02")))
    heard = find_dialog(window, "DLG-02")
    assert backend.calls[0].confirm_tone is True
    assert not button(heard, "DLG-02-3").isEnabled()
    assert not any(r.isChecked() for r in heard.findChildren(QRadioButton))
    stages = child(window.run_screen, "RUN-03")
    assert isinstance(stages, QListWidget)
    assert stages.item(4).text() == "[実行中] テスト音"
    QTest.keyClick(heard, Qt.Key.Key_Escape)
    wait_for(lambda: window.current_screen() is window.result_screen)
    obj = json.loads((out / "2026-10-01_140500.json").read_text(encoding="utf-8"))
    tone = {item["id"]: item for item in obj["items"]}["tone.heard"]
    assert (tone["status"], tone["value"], tone["reason"]) == (
        "unchecked", None, "テスト音の聞こえ方が申告されなかった")
    assert tone["reason"] == texts.MSG_31
    rows = table_rows(child(window.result_screen, "RES-07-unchecked-body"))
    assert ["S2", "テスト音の聞こえ方", texts.MSG_31] in rows


def test_tone_dialog_answer_is_passed_to_the_run(make_window, tmp_path):
    out = tmp_path / "results"
    backend = StubBackend(fixture="normal", out_dir=out)
    window = make_window(backend)
    go_to_devices(window)
    button(window.device_screen, "DEV-06").click()
    button(window.device_screen, "DEV-11").click()
    button(find_dialog(window, "DLG-01"), "DLG-01-2").click()
    wait_for(lambda: bool(window.findChildren(QDialog, "DLG-02")))
    heard = find_dialog(window, "DLG-02")
    button(heard, "DLG-02-2-crackle").click()
    assert button(heard, "DLG-02-3").isEnabled()
    button(heard, "DLG-02-3").click()
    wait_for(lambda: window.current_screen() is window.result_screen)
    obj = json.loads((out / "2026-10-01_140500.json").read_text(encoding="utf-8"))
    tone = {item["id"]: item for item in obj["items"]}["tone.heard"]
    assert (tone["status"], tone["value"], tone["verdict"]) == ("measured", "crackle", "bad")
    assert runner.tone_heard_raw("quiet").value == "quiet"
    with pytest.raises(ValueError):
        runner.tone_heard_raw("loud")


# ---- AC-G9: 最小サイズ ----


def inside(window: MainWindow, screen: QWidget) -> None:
    area = window.rect()
    for widget in screen.findChildren(QWidget):
        if isinstance(widget, (QAbstractButton, QComboBox, QLabel)) and widget.isVisible():
            top_left = widget.mapTo(window, widget.rect().topLeft())
            bottom_right = widget.mapTo(window, widget.rect().bottomRight())
            assert area.contains(top_left) and area.contains(bottom_right), widget.objectName()
            if isinstance(widget, QLabel):
                # 折り返した文が、ラベルの高さからはみ出して切れていない。
                assert widget.height() >= widget.heightForWidth(widget.width()), (
                    widget.objectName())
    bars = [bar for bar in screen.findChildren(QScrollBar) if bar.isVisible()]
    assert bars == [], screen.objectName()


@pytest.mark.parametrize("able", [ALL_CAPABILITIES, NO_CAPABILITIES], ids=["all", "none"])
def test_ac_g9_everything_fits_at_the_minimum_size(make_window, tmp_path, able):
    backend = StubBackend(capabilities=able, out_dir=tmp_path / "results", stage_seconds=60.0)
    backend.hold_at = "prepare"
    window = make_window(backend)
    window.resize(720, 560)
    QTest.qWait(20)
    assert (window.width(), window.height()) == (720, 560)
    window.symptom_screen.show_cancelled(True)
    QTest.qWait(20)
    inside(window, window.symptom_screen)
    go_to_devices(window)
    if able.any:
        for name in ("DEV-04", "DEV-05"):
            button(window.device_screen, name).click()
    QTest.qWait(20)
    assert child(window.device_screen, "DEV-12" if able.any else "DEV-90").isVisible()
    inside(window, window.device_screen)
    if able.any:
        button(window.device_screen, "DEV-04").click()
    button(window.device_screen, "DEV-11").click()
    assert backend.reached.wait(5.0)
    QTest.qWait(50)
    assert window.current_screen() is window.run_screen
    assert child(window.run_screen, "RUN-02").isHidden()
    inside(window, window.run_screen)
    if able.any:
        # 残り秒(RUN-02)が出ている段階でも収まる。
        backend.release.set()
        wait_for(lambda: child(window.run_screen, "RUN-02").isVisible())
        QTest.qWait(50)
        inside(window, window.run_screen)
    assert (window.width(), window.height()) == (720, 560)


# ---- AC-G11: 共有用のコピー ----


def test_ac_g11_copy_puts_the_share_markdown_on_the_clipboard(make_window, tmp_path):
    out = tmp_path / "results"
    backend = StubBackend(fixture="mixed", out_dir=out)
    window = make_window(backend)
    QGuiApplication.clipboard().setText("前の中身")
    run_to_result(window)
    assert names(out) == ["2026-10-01_140500.json", "2026-10-01_140500.md",
                          "2026-10-01_140500.share.md"]
    assert window.statusBar().currentMessage() == "結果を保存しました: 2026-10-01_140500"
    assert label_text(window.result_screen, "RES-13") == (
        f"保存先: {out / '2026-10-01_140500.json'} ほか 2 件")
    assert QGuiApplication.clipboard().text() == "前の中身"
    button(window.result_screen, "RES-09").click()
    copied = QGuiApplication.clipboard().text()
    data = (out / "2026-10-01_140500.share.md").read_bytes()
    assert b"\r" not in data and not data.startswith(b"\xef\xbb\xbf")
    assert copied == data.decode("utf-8")
    assert synth.DEVICE_IN not in copied and synth.DEVICE_OUT not in copied
    assert synth.USER not in copied
    assert "入力デバイスA" in copied and "出力デバイスA" in copied
    assert window.statusBar().currentMessage() == texts.MSG_24
    # 画面の中では、デバイス名をそのまま出す。
    assert synth.DEVICE_IN in label_text(window.result_screen, "RES-02")
    button(window.result_screen, "RES-10").click()
    assert backend.opened == [out]
    assert [button(window.result_screen, f"RES-{n:02d}").text() for n in (9, 10, 11, 12)] == [
        "共有用をコピー", "結果のフォルダを開く", "もう一度測る", "閉じる (Esc)"]


def test_save_failures_still_show_the_result(make_window, tmp_path):
    out = tmp_path / "results"
    out.mkdir()
    (out / "2026-10-01_140500.md").write_text("先にあった\n", encoding="utf-8")
    backend = StubBackend(out_dir=out)
    window = make_window(backend)
    run_to_result(window)
    assert names(out) == ["2026-10-01_140500.md"]
    assert label_text(window.result_screen, "RES-13") == texts.MSG_23
    assert window.statusBar().currentMessage() == (
        "同じ名前の結果があるため、保存しませんでした。結果は画面にだけ表示しています")
    assert len(table_rows(child(window.result_screen, "RES-04"))) == 6
    # 保存先を作れないとき(フォルダの名前の所にファイルがある)。
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    backend.out_dir = blocker / "results"
    button(window.result_screen, "RES-11").click()
    run_to_result(window)
    expected = "結果を保存できませんでした(保存先を作れない: "
    assert label_text(window.result_screen, "RES-13").startswith(expected)
    assert label_text(window.result_screen, "RES-13").endswith(
        ")。結果は画面にだけ表示しています")
    assert window.statusBar().currentMessage() == label_text(window.result_screen, "RES-13")
    assert names(tmp_path) == ["blocker", "results"]


# ---- AC-G12: キーボード ----


def tab_order(window: MainWindow, count: int) -> list[str]:
    seen = []
    for _ in range(count):
        focused = QApplication.focusWidget()
        assert focused is not None
        seen.append(focused.objectName())
        QTest.keyClick(focused, Qt.Key.Key_Tab)
    return seen


def test_ac_g12_tab_order_matches_the_design(make_window, tmp_path):
    backend = StubBackend(out_dir=tmp_path / "results")
    window = make_window(backend)
    QTest.qWaitForWindowActive(window)
    assert tab_order(window, 11) == [
        *(f"SYM-{n:02d}" for n in range(1, 9)), "SYM-13", "SYM-11", "SYM-01"]
    # Space でチェックの切り替え、Enter で次へ。
    first = button(window.symptom_screen, "SYM-01")
    QTest.keyClick(first, Qt.Key.Key_Space)
    assert first.isChecked()
    press(Qt.Key.Key_Return, window)
    assert window.current_screen() is window.device_screen
    assert tab_order(window, 8) == ["DEV-01", "DEV-02", "DEV-04", "DEV-05", "DEV-06",
                                    "DEV-10", "DEV-11", "DEV-01"]
    # Esc で戻り、Enter で測定を始める。
    press(Qt.Key.Key_Escape, window)
    assert window.current_screen() is window.symptom_screen
    press(Qt.Key.Key_Return, window)
    press(Qt.Key.Key_Return, window)
    wait_for(lambda: window.current_screen() is window.result_screen)
    assert backend.calls[0].symptoms == ("crackle",)
    assert tab_order(window, 12) == [
        *(f"RES-07-{key}" for key, _ in WORDS), "RES-09", "RES-10", "RES-11",
        "RES-12", "RES-07-bad"]
    # 節の見出しは Enter と Space で開閉する。
    header = window.result_screen.section_headers()[5]
    body = child(window.result_screen, "RES-07-ok-body")
    assert not header.is_open() and body.isHidden()
    QTest.keyClick(header, Qt.Key.Key_Return)
    assert header.is_open() and not body.isHidden()
    QTest.keyClick(header, Qt.Key.Key_Space)
    assert not header.is_open() and body.isHidden()
    header.click()
    assert header.is_open()
    # Esc で閉じる(終了コード 0)。
    press(Qt.Key.Key_Escape, window)
    assert not window.isVisible() and window.exit_code == 0


def test_ac_g12_tab_order_skips_checks_the_backend_cannot_run(make_window):
    window = make_window(StubBackend(capabilities=NO_CAPABILITIES))
    QTest.qWaitForWindowActive(window)
    go_to_devices(window)
    assert tab_order(window, 5) == ["DEV-01", "DEV-02", "DEV-10", "DEV-11", "DEV-01"]
    assert button(window.device_screen, "DEV-10").text() == "戻る (Esc)"
    assert button(window.device_screen, "DEV-11").text() == "測定を始める (Enter)"
