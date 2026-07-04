"""Generate .vcv patch files from corpus knowledge.

Uses verified port maps from C++ source (see module-quick-ref.md).
All patches should produce sound immediately (except AudioInterface needs driver selection).

Port ID cheat sheet (from source enums):
  VCO:  in 0=V/Oct, 1=FM, 2=Sync, 3=PW_CV | out 0=Sin, 1=Tri, 2=Saw, 3=Sqr
  VCF:  in 0=Freq_CV, 1=Res_CV, 2=Drive_CV, 3=Audio | out 0=LP, 1=HP
  VCA-1: in 0=CV, 1=Audio | out 0=Audio
  ADSR: in 4=Gate, 5=Retrig | out 0=Env
  LFO:  out 0=Sin, 1=Tri, 2=Saw, 3=Sqr
  Random: out 0=Stepped, 4=Trigger | param 0=Rate
  VCMixer: in 1=Ch1, 2=Ch2, 3=Ch3, 4=Ch4 | out 0=Mix
           CV inputs: 5=Ch1 CV, 6=Ch2 CV, 7=Ch3 CV, 8=Ch4 CV
  Quantizer: in 0=Pitch | out 0=Pitch
  Delay: in 4=Audio | out 0=Mix
  SEQ3: out 0=Trigger, 1=Row1, 2=Row2, 3=Row3 | Step gates: 4-11 | param 0=Tempo
  Audio: in 0=L, 1=R
"""

import json
import random
from pathlib import Path

from config import OUTPUT_DIR

GENERATED_DIR = OUTPUT_DIR.parent / "generated"

_distributions = None


def _load_dist():
    global _distributions
    if _distributions is None:
        _distributions = json.loads((OUTPUT_DIR / "param_distributions.json").read_text())


def make_module(mid, plugin, model, x, y, params=None):
    mod = {
        "id": mid,
        "plugin": plugin,
        "model": model,
        "version": "2.0.0",
        "params": params or [],
        "pos": [x, y],
    }
    if model in ("AudioInterface", "AudioInterface2"):
        mod["data"] = {
            "audio": {
                "driver": 0,
                "deviceName": "",
                "sampleRate": 44100.0,
                "blockSize": 256,
                "inputOffset": 0,
                "outputOffset": 0,
            }
        }
    return mod


def p(pid, value):
    return {"id": pid, "value": value}


def cable(cid, out_mod, out_port, in_mod, in_port):
    return {
        "id": cid,
        "outputModuleId": out_mod,
        "outputId": out_port,
        "inputModuleId": in_mod,
        "inputId": in_port,
        "color": random.choice(["#c91847", "#0c8e15", "#0986ad", "#c9b70e", "#ffb437"]),
    }


def save_patch(name, modules, cables):
    patch = {"version": "1.1.6", "modules": modules, "cables": cables}
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    path = GENERATED_DIR / f"{name}.vcv"
    path.write_text(json.dumps(patch, indent=2))
    print(f"  {path.name} — {len(modules)} modules, {len(cables)} cables")


def patch_01_subtractive():
    """Rhythmic subtractive voice: LFO clock → ADSR → VCO → VCF → VCA → Audio."""
    AUDIO, VCO, VCF, VCA, ADSR, MOD_LFO, CLK_LFO, SCOPE = 1, 2, 3, 4, 5, 6, 7, 8

    modules = [
        make_module(CLK_LFO, "Fundamental", "LFO", 0, 0,
                    [p(0, 1.0)]),  # ~2 Hz gate clock
        make_module(VCO, "Fundamental", "VCO", 15, 0,
                    [p(2, 0.0)]),  # C4
        make_module(VCF, "Fundamental", "VCF", 30, 0,
                    [p(0, 0.7), p(2, 0.3), p(3, 0.4)]),  # cutoff, res, CV depth
        make_module(VCA, "Fundamental", "VCA-1", 42, 0,
                    [p(0, 1.0)]),  # full level, linear response (default)
        make_module(ADSR, "Fundamental", "ADSR", 50, 0,
                    [p(0, 0.05), p(1, 0.4), p(2, 0.6), p(3, 0.3)]),
        make_module(MOD_LFO, "Fundamental", "LFO", 0, 1,
                    [p(0, -3.0)]),  # slow filter modulation
        make_module(AUDIO, "Core", "AudioInterface", 62, 0),
        make_module(SCOPE, "Fundamental", "Scope", 78, 0),
    ]

    cables = [
        cable(1, CLK_LFO, 3, ADSR, 4),   # LFO Square → ADSR Gate
        cable(2, VCO, 2, VCF, 3),          # VCO Saw → VCF Audio
        cable(3, VCF, 0, VCA, 1),          # VCF LP → VCA Audio
        cable(4, VCA, 0, AUDIO, 0),        # VCA → Audio L
        cable(5, VCA, 0, AUDIO, 1),        # VCA → Audio R
        cable(6, ADSR, 0, VCA, 0),         # ADSR Env → VCA CV
        cable(7, ADSR, 0, VCF, 0),         # ADSR Env → VCF Freq CV (brightness tracking)
        cable(8, MOD_LFO, 0, VCF, 0),     # Slow LFO Sine → VCF Freq CV (sweep)
        cable(9, VCA, 0, SCOPE, 0),        # VCA → Scope
    ]

    save_patch("01-subtractive-voice", modules, cables)


def patch_02_generative():
    """Self-playing: Random → Quantizer → VCO → VCF → VCA → Delay → Audio."""
    AUDIO, RND, QUANT, VCO, VCF, VCA, ADSR, LFO, DELAY, SCOPE = 1, 2, 3, 4, 5, 6, 7, 8, 9, 10

    modules = [
        make_module(RND, "Fundamental", "Random", 0, 0,
                    [p(0, 1.5), p(4, 1.0), p(5, 1.0)]),  # rate=1.5Hz, prob=100%, spread=full
        make_module(QUANT, "Fundamental", "Quantizer", 10, 0),
        make_module(VCO, "Fundamental", "VCO", 18, 0,
                    [p(2, 0.0)]),  # C4
        make_module(VCF, "Fundamental", "VCF", 33, 0,
                    [p(0, 0.6), p(2, 0.25), p(3, 0.3)]),  # warmish cutoff
        make_module(VCA, "Fundamental", "VCA-1", 45, 0,
                    [p(0, 1.0)]),
        make_module(ADSR, "Fundamental", "ADSR", 18, 1,
                    [p(0, 0.3), p(1, 0.5), p(2, 0.4), p(3, 0.8)]),  # slow attack, long release
        make_module(LFO, "Fundamental", "LFO", 0, 1,
                    [p(0, -4.0)]),  # very slow filter mod
        make_module(DELAY, "Fundamental", "Delay", 55, 0,
                    [p(0, 0.4), p(1, 0.55), p(2, 0.6), p(3, 0.35)]),  # time, fb, tone, mix
        make_module(AUDIO, "Core", "AudioInterface", 68, 0),
        make_module(SCOPE, "Fundamental", "Scope", 84, 0),
    ]

    cables = [
        cable(1, RND, 0, QUANT, 0),       # Random Stepped → Quantizer Pitch in
        cable(2, QUANT, 0, VCO, 0),        # Quantizer Pitch → VCO V/Oct
        cable(3, RND, 4, ADSR, 4),         # Random Trigger → ADSR Gate
        cable(4, VCO, 1, VCF, 3),          # VCO Triangle → VCF Audio
        cable(5, VCF, 0, VCA, 1),          # VCF LP → VCA Audio
        cable(6, ADSR, 0, VCA, 0),         # ADSR Env → VCA CV
        cable(7, ADSR, 0, VCF, 0),         # ADSR Env → VCF Freq CV
        cable(8, LFO, 0, VCF, 0),          # LFO Sine → VCF Freq CV
        cable(9, VCA, 0, DELAY, 4),        # VCA → Delay Audio in
        cable(10, DELAY, 0, AUDIO, 0),     # Delay Mix → Audio L
        cable(11, DELAY, 0, AUDIO, 1),     # Delay Mix → Audio R
        cable(12, DELAY, 0, SCOPE, 0),     # Delay → Scope
    ]

    save_patch("02-generative-ambient", modules, cables)


def patch_03_drone():
    """Two detuned VCOs + noise → filter → slow modulation. No gate needed."""
    AUDIO, VCO1, VCO2, MIXER, VCF, LFO1, LFO2, NOISE, SCOPE = 1, 2, 3, 4, 5, 6, 7, 8, 9

    modules = [
        make_module(VCO1, "Fundamental", "VCO", 0, 0,
                    [p(2, -12.0)]),  # C3
        make_module(VCO2, "Fundamental", "VCO", 15, 0,
                    [p(2, -11.92)]),  # slightly detuned
        make_module(NOISE, "Fundamental", "Noise", 30, 0),
        make_module(MIXER, "Fundamental", "VCMixer", 38, 0,
                    [p(0, 1.0), p(1, 1.0), p(2, 1.0), p(3, 1.0)]),  # full mix and channel levels
        make_module(VCF, "Fundamental", "VCF", 55, 0,
                    [p(0, 0.5), p(2, 0.2), p(3, 0.3)]),  # moderate cutoff
        make_module(LFO1, "Fundamental", "LFO", 0, 1,
                    [p(0, -5.0)]),  # very slow filter
        make_module(LFO2, "Fundamental", "LFO", 15, 1,
                    [p(0, -4.5)]),  # slightly faster amp
        make_module(AUDIO, "Core", "AudioInterface", 70, 0),
        make_module(SCOPE, "Fundamental", "Scope", 86, 0),
    ]

    # Drone: no VCA gating — LFO modulates filter, audio passes through directly
    cables = [
        cable(1, VCO1, 2, MIXER, 1),      # VCO1 Saw → Mixer Ch1
        cable(2, VCO2, 1, MIXER, 2),      # VCO2 Triangle → Mixer Ch2
        cable(3, NOISE, 1, MIXER, 3),     # Pink noise → Mixer Ch3
        cable(4, MIXER, 0, VCF, 3),       # Mixer → VCF Audio
        cable(5, VCF, 0, AUDIO, 0),       # VCF LP → Audio L
        cable(6, VCF, 0, AUDIO, 1),       # VCF LP → Audio R
        cable(7, LFO1, 0, VCF, 0),        # LFO1 Sine → VCF Freq CV
        cable(8, LFO2, 1, VCO2, 1),       # LFO2 Triangle → VCO2 FM (gentle detune wobble)
        cable(9, VCF, 0, SCOPE, 0),       # VCF → Scope
    ]

    save_patch("03-drone-texture", modules, cables)


def patch_04_sequenced():
    """SEQ3-driven subtractive voice with quantizer."""
    AUDIO, SEQ, QUANT, VCO, VCF, VCA, ADSR, LFO, SCOPE = 1, 2, 3, 4, 5, 6, 7, 8, 9

    modules = [
        make_module(SEQ, "Fundamental", "SEQ3", 0, 0,
                    [p(0, 2.0), p(3, 8.0)]),  # tempo, 8 steps
        make_module(QUANT, "Fundamental", "Quantizer", 30, 0),
        make_module(VCO, "Fundamental", "VCO", 38, 0,
                    [p(2, 0.0)]),
        make_module(VCF, "Fundamental", "VCF", 53, 0,
                    [p(0, 0.65), p(2, 0.35), p(3, 0.4)]),
        make_module(VCA, "Fundamental", "VCA-1", 65, 0,
                    [p(0, 1.0)]),
        make_module(ADSR, "Fundamental", "ADSR", 38, 1,
                    [p(0, 0.02), p(1, 0.3), p(2, 0.5), p(3, 0.2)]),  # snappy
        make_module(LFO, "Fundamental", "LFO", 0, 1,
                    [p(0, -3.5)]),  # slow filter sweep
        make_module(AUDIO, "Core", "AudioInterface", 75, 0),
        make_module(SCOPE, "Fundamental", "Scope", 91, 0),
    ]

    cables = [
        cable(1, SEQ, 1, QUANT, 0),       # SEQ3 Row1 → Quantizer Pitch
        cable(2, QUANT, 0, VCO, 0),        # Quantizer → VCO V/Oct
        cable(3, SEQ, 0, ADSR, 4),         # SEQ3 Trigger → ADSR Gate
        cable(4, VCO, 2, VCF, 3),          # VCO Saw → VCF Audio
        cable(5, VCF, 0, VCA, 1),          # VCF LP → VCA Audio
        cable(6, VCA, 0, AUDIO, 0),        # VCA → Audio L
        cable(7, VCA, 0, AUDIO, 1),        # VCA → Audio R
        cable(8, ADSR, 0, VCA, 0),         # ADSR → VCA CV
        cable(9, ADSR, 0, VCF, 0),         # ADSR → VCF Freq CV
        cable(10, LFO, 0, VCF, 0),         # LFO Sine → VCF Freq CV
        cable(11, VCA, 0, SCOPE, 0),       # VCA → Scope
    ]

    save_patch("04-sequenced-melody", modules, cables)


def main():
    print("Generating patches with verified port IDs...\n")
    patch_01_subtractive()
    patch_02_generative()
    patch_03_drone()
    patch_04_sequenced()
    print(f"\nOpen in VCV Rack: File → Open")
    print(f"  \\\\wsl$\\Ubuntu{GENERATED_DIR}")
    print(f"\nNOTE: Right-click AudioInterface → select your audio device")


if __name__ == "__main__":
    main()
