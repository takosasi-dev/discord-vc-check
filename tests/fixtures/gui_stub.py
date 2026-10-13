# GUI のテストと画像の作成で使う、合成の Backend(測定部のスタブ)。
# 実機のマイク・スピーカー・レジストリには触れず、合成の結果(synth.py)を返す。
# デバイス名は合成の名前(Headset (TestUser-PC) / Speakers (TestUser-PC))だけを使う。
from __future__ import annotations

import threading
from pathlib import Path

from tests.fixtures import synth
from vccheck.gui.backend import (
    DeviceList,
    MultimediaUnavailableError,
    SaveReport,
    Settings,
    stem_of,
)
from vccheck.io_safe import OutputError, OutputExistsError, dump_json
from vccheck.model import Conditions, result_to_obj
from vccheck.report_md import render_markdown
from vccheck.runner import (
    STAGE_MIC_SILENT,
    STAGE_MIC_SPEECH,
    STAGE_NET,
    STAGE_PREPARE,
    STAGE_SUMMARY,
    STAGE_TONE,
    Capabilities,
    ConfigFileError,
    RunHooks,
    RunOutput,
    RunRequest,
    _skipped_raws,
    build_result,
    save_run,
    tone_heard_raw,
)

ALL_CAPABILITIES = Capabilities(mic=True, net=True, tone=True)
NO_CAPABILITIES = Capabilities(mic=False, net=False, tone=False)
DEVICES = DeviceList((synth.DEVICE_IN, "Webcam Mic (TestUser-PC)"),
                     (synth.DEVICE_OUT, "Monitor (TestUser-PC)"), 0, 0)
FIXTURES = {fixture.name: fixture for fixture in synth.fixtures()}


def _this_version() -> synth.Fixture:
    """この版の run が返す形の検体(設定と PC の負荷だけ合成の値、ほかは実行層と同じ未チェック)。"""
    measured_now = [raw for raw in synth.other_raws().values()
                    if raw.id.split(".")[0] in ("env", "priv", "duck", "sys")]
    return synth.Fixture("this_version", [*measured_now, *_skipped_raws()],
                         synth.NULL_THRESHOLDS, ("noise",))


# AC-7 の10種とは別。README の画像を、この版の実際の画面と同じ形にするための検体。
EXTRA_FIXTURES = {"this_version": _this_version()}


def fixture_of(name: str) -> synth.Fixture:
    return FIXTURES[name] if name in FIXTURES else EXTRA_FIXTURES[name]


def make_output(name: str, request: RunRequest, tone_answer: str | None = None,
                *, tone_asked: bool = False) -> RunOutput:
    """合成の結果を、画面からの指定(症状・申告したデバイス)で組み立てる。"""
    fixture = fixture_of(name)
    raws = list(fixture.raws)
    if tone_asked:
        raws = [tone_heard_raw(tone_answer) if raw.id == "tone.heard" else raw for raw in raws]
    measure = synth.MEASURE
    result = build_result(
        raws, symptoms=request.symptoms,
        conditions=Conditions(request.input_declared, request.output_declared,
                              request.input_default_used, 1, measure.silent_s,
                              measure.speech_s, measure.net_attempts),
        measured_at=synth.STAMP, created_at=synth.STAMP, thresholds=fixture.thresholds,
        rules=synth.CONFIG.rules, advice=synth.CONFIG.advice,
    )
    obj = result_to_obj(result)
    return RunOutput(obj, dump_json(obj), render_markdown(result),
                     render_markdown(result, share=True, identity=synth.IDENTITY))


def fixture_request(name: str) -> RunRequest:
    """合成の結果そのまま(症状も合成の物)を描くときの指定。"""
    return RunRequest(symptoms=FIXTURES[name].symptoms, skip_mic=False, skip_net=False,
                      input_declared=synth.DEVICE_IN, output_declared=synth.DEVICE_OUT,
                      input_default_used=False)


class StubBackend:
    """測定部のスタブ。calls に、測定が始まった回数分の指定が残る。"""

    def __init__(self, *, capabilities: Capabilities = ALL_CAPABILITIES,
                 devices: DeviceList | None = DEVICES, fixture: str = "clipping",
                 out_dir: Path | None = None, stage_seconds: float = 0.0,
                 broken_config: str | None = None, shown_folder: Path | None = None) -> None:
        self.capabilities = capabilities
        self.device_list = devices
        self.fixture = fixture
        self.out_dir = out_dir
        self.stage_seconds = stage_seconds
        self.broken_config = broken_config
        self.shown_folder = shown_folder
        self.calls: list[RunRequest] = []
        self.opened: list[Path] = []
        self.stages: list[tuple[str, float | None]] = []
        # hold_at の段階に入ったら reached を立て、release が立つまで進まない。
        self.hold_at: str | None = None
        self.reached = threading.Event()
        self.release = threading.Event()
        # 測定が結果を返す直前に立てる。
        self.returned = threading.Event()

    def load(self) -> Settings:
        if self.broken_config is not None:
            raise ConfigFileError(self.broken_config, f"{self.broken_config}: object でない")
        done, total = fixture_of(self.fixture).thresholds.calibrated_counts()
        return Settings(synth.MEASURE, done, total, self.capabilities)

    def devices(self) -> DeviceList:
        if self.device_list is None:
            raise MultimediaUnavailableError("ModuleNotFoundError")
        return self.device_list

    def _stage(self, hooks: RunHooks, stage: str, seconds: float | None) -> None:
        self.stages.append((stage, seconds))
        hooks.on_stage(stage, seconds)
        if self.hold_at == stage:
            self.reached.set()
            while not self.release.wait(0.01):
                hooks.check()
        if seconds is not None:
            hooks.sleep(self.stage_seconds)

    def run(self, request: RunRequest, hooks: RunHooks) -> RunOutput:
        self.calls.append(request)
        measure = synth.MEASURE
        self._stage(hooks, STAGE_PREPARE, None)
        if not request.skip_mic:
            self._stage(hooks, STAGE_MIC_SILENT,
                        measure.discard_head_ms / 1000.0 + measure.silent_s)
            self._stage(hooks, STAGE_MIC_SPEECH, measure.speech_s)
        if not request.skip_net:
            self._stage(hooks, STAGE_NET,
                        measure.net_attempts * measure.net_interval_ms / 1000.0)
        answer: str | None = None
        if request.confirm_tone:
            self._stage(hooks, STAGE_TONE, measure.tone_s)
            answer = hooks.ask_tone_heard()
        self._stage(hooks, STAGE_SUMMARY, None)
        output = make_output(self.fixture, request, answer, tone_asked=request.confirm_tone)
        self.returned.set()
        return output

    def save(self, output: RunOutput) -> SaveReport:
        stem = stem_of(output)
        if self.out_dir is None:
            # 画像の作成用: 何も書かずに、保存できたときの表示だけを作る。
            return SaveReport(stem, self.shown_folder)
        try:
            save_run(output, self.out_dir, stem)
        except OutputExistsError as error:
            return SaveReport(stem, self.out_dir, str(error), exists=True)
        except OutputError as error:
            return SaveReport(stem, self.out_dir, str(error))
        return SaveReport(stem, self.out_dir)

    def open_folder(self, folder: Path) -> None:
        self.opened.append(folder)
