import numpy as np
import pytest
import soundfile as sf

from analyze_audio import analyze_file

SR = 44100


def write_wav(path, y, sr=SR):
    sf.write(str(path), y, sr, subtype="PCM_16")
    return path


def test_sine_is_tonal_and_clean(tmp_path):
    t = np.arange(2 * SR) / SR
    y = 0.5 * np.sin(2 * np.pi * 440 * t)
    m = analyze_file(write_wav(tmp_path / "sine.wav", y))
    assert m["verdict"]["makes_sound"] is True
    assert m["verdict"]["character"] == "drone"
    assert m["verdict"]["flags"] == []
    assert m["median_f0_hz"] == pytest.approx(440, rel=0.02)
    assert m["voiced_ratio"] > 0.8


def test_zeros_is_silent(tmp_path):
    y = np.zeros(2 * SR)
    m = analyze_file(write_wav(tmp_path / "zeros.wav", y))
    assert m["verdict"]["makes_sound"] is False
    assert m["verdict"]["character"] == "silent"


def test_full_scale_square_flags_clipping(tmp_path):
    t = np.arange(2 * SR) / SR
    y = np.sign(np.sin(2 * np.pi * 220 * t)) * 0.9999
    m = analyze_file(write_wav(tmp_path / "square.wav", y))
    assert "clipping" in m["verdict"]["flags"]
    assert m["verdict"]["makes_sound"] is True


def test_dc_offset_flagged(tmp_path):
    t = np.arange(2 * SR) / SR
    y = 0.3 * np.sin(2 * np.pi * 440 * t) + 0.3
    m = analyze_file(write_wav(tmp_path / "dc.wav", y))
    assert "dc_offset" in m["verdict"]["flags"]


def test_white_noise_is_noise(tmp_path):
    rng = np.random.default_rng(42)
    y = 0.4 * rng.standard_normal(2 * SR).clip(-1, 1)
    m = analyze_file(write_wav(tmp_path / "noise.wav", y))
    assert m["verdict"]["character"] == "noise"


def test_quiet_tone_is_near_silent(tmp_path):
    t = np.arange(2 * SR) / SR
    y = 0.005 * np.sin(2 * np.pi * 440 * t)
    m = analyze_file(write_wav(tmp_path / "quiet.wav", y))
    assert "near_silent" in m["verdict"]["flags"]


def test_bursts_are_rhythmic(tmp_path):
    # 4 tone bursts per second over 2s
    t = np.arange(2 * SR) / SR
    envelope = (np.sin(2 * np.pi * 4 * t - np.pi / 2) > 0.2).astype(float)
    y = 0.5 * np.sin(2 * np.pi * 440 * t) * envelope
    m = analyze_file(write_wav(tmp_path / "bursts.wav", y))
    assert m["verdict"]["character"] == "rhythmic"
    assert m["onset_rate"] >= 2
