"""Analyze rendered patch audio with local DSP metrics.

Per WAV: level stats (silence / clipping / DC offset), spectral shape
(centroid, flatness, bandwidth), onset density via spectral flux, and
pitch coverage via frame-wise autocorrelation. Pure numpy — librosa is
unusable on this machine (its numba/guvectorize kernels crash LLVM on
this ARM CPU, even at import time). Each file gets a verdict block:

  makes_sound: bool
  character:   drone | rhythmic | noise | silent
  flags:       [clipping, near_silent, dc_offset]

Results are merged into data/output/audio_analysis.json keyed by patch
name, in the same artifact style as the pipeline stages.

Usage:
  python3 analyze_audio.py data/audio/                # directory
  python3 analyze_audio.py data/audio/02-drone.wav    # single file
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

from config import OUTPUT_DIR

ANALYSIS_PATH = OUTPUT_DIR / "audio_analysis.json"

FRAME_LENGTH = 2048
HOP_LENGTH = 512
SILENCE_TOP_DB = 60
CLIP_PEAK = 0.99
NEAR_SILENT_RMS = 0.01
DC_OFFSET_LIMIT = 0.05
NOISE_FLATNESS = 0.3
RHYTHMIC_ONSETS_PER_SEC = 1.0
PITCH_FMIN = 32.7  # C1
PITCH_FMAX = 2093.0  # C7
PERIODICITY_THRESHOLD = 0.6


def frame_signal(y, frame_length=FRAME_LENGTH, hop_length=HOP_LENGTH):
    """(n_frames, frame_length) view of y; empty if y is too short."""
    if len(y) < frame_length:
        return np.empty((0, frame_length))
    return np.lib.stride_tricks.sliding_window_view(y, frame_length)[::hop_length]


def spectrogram(y, frame_length=FRAME_LENGTH, hop_length=HOP_LENGTH):
    """Magnitude spectrogram (n_frames, n_bins), Hann-windowed."""
    frames = frame_signal(y, frame_length, hop_length)
    if not len(frames):
        return np.empty((0, frame_length // 2 + 1))
    return np.abs(np.fft.rfft(frames * np.hanning(frame_length), axis=1))


def spectral_shape(mag, sr):
    """Mean spectral centroid (Hz), flatness (0..1), bandwidth (Hz)."""
    if not len(mag):
        return 0.0, 0.0, 0.0
    freqs = np.fft.rfftfreq(2 * (mag.shape[1] - 1), 1.0 / sr)
    total = mag.sum(axis=1)
    live = total > 1e-8
    if not live.any():
        return 0.0, 0.0, 0.0
    mag = mag[live]
    total = total[live]

    centroid = (mag * freqs).sum(axis=1) / total
    spread = ((freqs - centroid[:, None]) ** 2 * mag).sum(axis=1) / total
    bandwidth = np.sqrt(spread)

    power = mag**2 + 1e-10
    flatness = np.exp(np.mean(np.log(power), axis=1)) / np.mean(power, axis=1)

    return float(centroid.mean()), float(flatness.mean()), float(bandwidth.mean())


def silent_ratio_of(y, peak, top_db=SILENCE_TOP_DB):
    """Fraction of frames more than top_db below the file's peak."""
    frames = frame_signal(y)
    if not len(frames):
        return 1.0
    frame_rms = np.sqrt(np.mean(frames**2, axis=1))
    threshold = peak * 10 ** (-top_db / 20)
    return float(np.mean(frame_rms < threshold))


def detect_onsets(mag, sr, hop_length=HOP_LENGTH):
    """Onset count from half-wave-rectified spectral flux with a local
    mean + local max peak pick."""
    if len(mag) < 3:
        return 0
    flux = np.maximum(0.0, np.diff(mag, axis=0)).sum(axis=1)
    scale = mag.sum(axis=1).mean()
    if scale <= 0:
        return 0
    flux = flux / scale  # fraction of average spectral energy newly appearing

    w, mean_w, delta, floor = 3, 10, 0.05, 0.1
    count = 0
    last_onset = -np.inf
    min_gap = int(0.05 * sr / hop_length) + 1  # 50 ms
    for i in range(len(flux)):
        if flux[i] < floor:
            continue
        lo, hi = max(0, i - w), min(len(flux), i + w + 1)
        if flux[i] < flux[lo:hi].max():
            continue
        mlo, mhi = max(0, i - mean_w), min(len(flux), i + mean_w + 1)
        if flux[i] >= flux[mlo:mhi].mean() + delta and i - last_onset >= min_gap:
            count += 1
            last_onset = i
    return count


def pitch_track(y, sr, fmin=PITCH_FMIN, fmax=PITCH_FMAX):
    """Frame-wise autocorrelation pitch tracking.

    Returns (voiced_ratio, median_f0). A frame is voiced when its
    normalized autocorrelation peak in the [fmin, fmax] lag range exceeds
    PERIODICITY_THRESHOLD and the frame is not silent.
    """
    frames = frame_signal(y)
    if not len(frames):
        return 0.0, None
    frames = frames - frames.mean(axis=1, keepdims=True)

    spec = np.fft.rfft(frames, n=2 * FRAME_LENGTH, axis=1)
    ac = np.fft.irfft(spec * np.conj(spec), axis=1)[:, :FRAME_LENGTH]
    ac0 = ac[:, 0]

    lag_min = max(int(sr / fmax), 1)
    lag_max = min(int(sr / fmin), FRAME_LENGTH - 1)
    window = ac[:, lag_min : lag_max + 1]
    best_lag = window.argmax(axis=1) + lag_min
    with np.errstate(divide="ignore", invalid="ignore"):
        periodicity = np.where(ac0 > 0, window.max(axis=1) / ac0, 0.0)
    frame_rms = np.sqrt(np.maximum(ac0, 0.0) / FRAME_LENGTH)

    voiced = (periodicity > PERIODICITY_THRESHOLD) & (frame_rms > 1e-3)
    voiced_ratio = float(voiced.mean())
    median_f0 = float(sr / np.median(best_lag[voiced])) if voiced.any() else None
    return voiced_ratio, median_f0


def analyze_file(wav_path):
    """Compute metrics + verdict for one WAV. Pure function of the file."""
    data, sr = sf.read(str(wav_path), always_2d=True)
    peak = float(np.abs(data).max())
    y = data.mean(axis=1)
    duration = len(y) / sr

    rms = float(np.sqrt(np.mean(y**2)))
    dc_offset = float(np.mean(y))

    silent_ratio = silent_ratio_of(y, peak) if peak > 0 else 1.0
    makes_sound = peak > 1e-3 and silent_ratio < 0.99

    if makes_sound:
        mag = spectrogram(y - dc_offset)
        centroid, flatness, bandwidth = spectral_shape(mag, sr)
        onset_count = detect_onsets(mag, sr)
        onset_rate = onset_count / duration if duration else 0.0
        voiced_ratio, median_f0 = pitch_track(y, sr)
    else:
        centroid = flatness = bandwidth = onset_rate = voiced_ratio = 0.0
        onset_count = 0
        median_f0 = None

    flags = []
    if peak >= CLIP_PEAK:
        flags.append("clipping")
    if makes_sound and rms < NEAR_SILENT_RMS:
        flags.append("near_silent")
    if abs(dc_offset) > DC_OFFSET_LIMIT:
        flags.append("dc_offset")

    if not makes_sound:
        character = "silent"
    elif flatness > NOISE_FLATNESS:
        character = "noise"
    elif onset_rate >= RHYTHMIC_ONSETS_PER_SEC:
        character = "rhythmic"
    else:
        character = "drone"

    return {
        "duration": round(duration, 3),
        "sample_rate": sr,
        "peak": round(peak, 4),
        "rms": round(rms, 4),
        "dc_offset": round(dc_offset, 4),
        "silent_ratio": round(silent_ratio, 3),
        "spectral_centroid_hz": round(centroid, 1),
        "spectral_flatness": round(flatness, 4),
        "spectral_bandwidth_hz": round(bandwidth, 1),
        "onset_count": onset_count,
        "onset_rate": round(onset_rate, 2),
        "voiced_ratio": round(voiced_ratio, 3),
        "median_f0_hz": round(median_f0, 1) if median_f0 else None,
        "verdict": {
            "makes_sound": makes_sound,
            "character": character,
            "flags": flags,
        },
    }


def collect_wavs(paths):
    files = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            files.extend(sorted(p.rglob("*.wav")))
        else:
            files.append(p)
    return files


def load_existing(path=ANALYSIS_PATH):
    if Path(path).exists():
        return json.loads(Path(path).read_text())
    return {}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="+", help=".wav files or directories")
    parser.add_argument("--output", default=str(ANALYSIS_PATH))
    args = parser.parse_args()

    files = collect_wavs(args.paths)
    if not files:
        print("No .wav files found")
        return 1

    results = load_existing(args.output)
    failures = 0
    for f in files:
        try:
            metrics = analyze_file(f)
        except (RuntimeError, ValueError, OSError) as e:
            failures += 1
            print(f"FAIL  {f}: {e}")
            continue
        results = {**results, f.stem: metrics}
        v = metrics["verdict"]
        flag_str = ",".join(v["flags"]) or "-"
        print(
            f"{f.stem:30s} {v['character']:9s} rms={metrics['rms']:.3f} "
            f"peak={metrics['peak']:.2f} flags={flag_str}"
        )

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2))
    print(f"\n{len(files) - failures}/{len(files)} analyzed -> {out}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
