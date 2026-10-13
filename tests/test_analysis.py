# analysis.py の純関数のテスト(AC-3〜AC-5)。合成の配列だけを使う。
from __future__ import annotations

import pytest

from tests.fixtures.synth import MEASURE, RATE, mic_raws, recording, sine
from vccheck import analysis
from vccheck.model import MIC_ITEM_IDS


def by_id(raws):
    return {r.id: r for r in raws}


def test_ac3_sine_rms_peak_goertzel():
    wave = sine(0.1, 440.0, 48000, 48000)
    assert analysis.to_dbfs(analysis.rms(wave))[0] == pytest.approx(-23.0, abs=0.05)
    assert analysis.to_dbfs(max(abs(x) for x in wave))[0] == pytest.approx(-20.0, abs=0.05)
    tone = analysis.goertzel_amplitude(wave, 440.0, 48000)
    assert analysis.to_dbfs(tone)[0] == pytest.approx(-20.0, abs=0.1)


def test_goertzel_ignores_other_frequency():
    wave = sine(0.1, 1000.0, 48000, 48000)
    assert analysis.goertzel_amplitude(wave, 440.0, 48000) < 0.001


def test_ac4_clip_runs():
    five = [0.0] * 20 + [1.0] * 5 + [0.0] * 20
    assert analysis.clip_runs(five, 0.999, 3) == (1, 5)
    two = [0.0] * 20 + [1.0] * 2 + [0.0] * 20
    assert analysis.clip_runs(two, 0.999, 3) == (0, 0)
    edges = [1.0] * 4 + [0.0] * 10 + [-1.0] * 3
    assert analysis.clip_runs(edges, 0.999, 3) == (2, 7)
    assert analysis.clip_runs([1.0] * 6, 0.999, 3) == (1, 6)
    assert analysis.clip_runs([1.0, -1.0, 1.0], 0.999, 3) == (1, 3)


def test_ac4_clip_ms_is_five_samples():
    rate = 48000
    head = round(rate * MEASURE.discard_head_ms / 1000.0)
    body = round(rate * (MEASURE.silent_s + MEASURE.speech_s))
    samples = [0.001] * (head + body)
    for offset in range(5):
        samples[head + 1000 + offset] = 1.0
    raws = by_id(analysis.measure_mic(samples, rate, MEASURE, None))
    assert raws["mic.clip_events"].value == 1
    assert raws["mic.clip_ms"].value == pytest.approx(5 * 1000 / rate, abs=0.001)


def test_ac5_dc_offset_percentile_all_zero():
    assert analysis.dc_offset_pct([0.01] * 480) == pytest.approx(1.0, abs=0.01)
    assert analysis.nearest_rank([float(n) for n in range(1, 11)], 90) == 9.0
    assert analysis.nearest_rank([3.0, 1.0, 2.0], 100) == 3.0
    assert analysis.nearest_rank([3.0, 1.0, 2.0], 1) == 1.0
    assert analysis.is_all_zero([0.0] * 10)
    assert not analysis.is_all_zero([0.0, 1e-9])


def test_ac5_all_zero_recording():
    raws = by_id(mic_raws(0.0, 0.0, None))
    assert raws["mic.all_zero"].value is True
    for item_id in MIC_ITEM_IDS:
        if item_id != "mic.all_zero":
            assert raws[item_id].status == "failed"
            assert raws[item_id].value is None
            assert raws[item_id].reason == "全サンプルが 0"


def test_ac5_speech_gate():
    gated = by_id(mic_raws(0.05, 0.05, 10))
    for item_id in ("mic.speech_level_dbfs", "mic.snr_db"):
        assert gated[item_id].status == "failed"
        assert gated[item_id].value is None
        assert gated[item_id].reason == "声が検出できなかった(または無言の区間に声が入った)"
    assert gated["mic.noise_floor_dbfs"].status == "measured"
    passed = by_id(mic_raws(0.001, 0.1, 10))
    assert passed["mic.speech_level_dbfs"].status == "measured"
    assert passed["mic.speech_level_dbfs"].note is None


def test_gate_null_adds_note_and_does_not_gate():
    raws = by_id(mic_raws(0.05, 0.05, None))
    for item_id in ("mic.speech_level_dbfs", "mic.snr_db"):
        assert raws[item_id].status == "measured"
        assert raws[item_id].note == "発話の検出は未較正のため確認していない"


def test_mic_values_of_healthy_recording():
    raws = by_id(mic_raws(0.001, 0.1, 10))
    assert raws["mic.noise_floor_dbfs"].value == pytest.approx(-63.0, abs=0.1)
    assert raws["mic.speech_level_dbfs"].value == pytest.approx(-23.0, abs=0.1)
    assert raws["mic.peak_dbfs"].value == pytest.approx(-20.0, abs=0.1)
    assert raws["mic.snr_db"].value == pytest.approx(
        raws["mic.speech_level_dbfs"].value - raws["mic.noise_floor_dbfs"].value, abs=0.05)
    assert raws["mic.clip_events"].value == 0
    assert raws["mic.dc_offset_pct"].value == pytest.approx(0.0, abs=0.01)
    assert raws["mic.all_zero"].value is False
    assert [r.id for r in mic_raws(0.001, 0.1, 10)] == list(MIC_ITEM_IDS)


def test_head_is_discarded():
    # 合成の録音の先頭(捨てる分)には 0.3 を入れてある。捨てていれば最大は −20 dBFS。
    assert recording(0.001, 0.1)[0] == 0.3
    assert by_id(mic_raws(0.001, 0.1, 10))["mic.peak_dbfs"].value < -19.0


def test_silent_zero_is_floored_with_notes():
    raws = by_id(mic_raws(0.0, 0.1, 10))
    noise = raws["mic.noise_floor_dbfs"]
    assert noise.status == "measured"
    assert noise.value == -120.0
    assert "下限丸め" in (noise.note or "")
    assert "ドライバーのノイズゲートの可能性(未確認)" in (noise.note or "")
    assert raws["mic.all_zero"].value is False
    assert "下限丸め" in (raws["mic.snr_db"].note or "")


def test_to_dbfs_floor_and_rounding():
    assert analysis.to_dbfs(0.0) == (-120.0, True)
    assert analysis.to_dbfs(1e-9) == (-120.0, True)
    assert analysis.to_dbfs(1.0) == (0.0, False)
    assert analysis.to_dbfs(0.5) == (-6.0, False)


def test_normalize_formats():
    assert analysis.normalize([-32768, 0, 16384], "Int16") == [-1.0, 0.0, 0.5]
    assert analysis.normalize([-2147483648, 1073741824], "Int32") == [-1.0, 0.5]
    assert analysis.normalize([0.25, -0.5], "Float") == [0.25, -0.5]
    assert analysis.normalize([0, 128, 192], "UInt8") == [-1.0, 0.0, 0.5]
    assert analysis.is_supported_format("Int16")
    assert not analysis.is_supported_format("Unknown")
    with pytest.raises(ValueError, match="未対応のサンプル形式: Unknown"):
        analysis.normalize([0], "Unknown")


def test_first_channel():
    assert analysis.first_channel([1, 9, 2, 9, 3, 9], 2) == [1, 2, 3]
    assert analysis.first_channel([1, 2, 3], 1) == [1, 2, 3]


def test_window_rms_drops_partial_window():
    assert analysis.window_rms([1.0] * 10, 4) == [1.0, 1.0]


def test_summarize():
    summary = analysis.summarize([3.0, 1.0, 2.0, 10.0])
    assert (summary.count, summary.minimum, summary.median, summary.maximum) == (4, 1.0, 2.5, 10.0)


def test_too_short_recording_is_rejected():
    with pytest.raises(ValueError):
        analysis.measure_mic([0.0] * 100, RATE, MEASURE, None)
