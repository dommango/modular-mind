"""Generate batch 3: Copy proven wiring from corpus patches.

Instead of theorizing about port IDs, directly replicate the exact
cable connections from known-working Omri Cohen & community patches.
Then vary only the parameters.
"""

import json
import random
from pathlib import Path

from config import OUTPUT_DIR
from validate_patch import PatchValidator

GENERATED_DIR = OUTPUT_DIR.parent / "generated" / "batch3"


def p(pid, value):
    return {"id": pid, "value": value}


def mod(mid, plugin, model, x, y, params=None, data=None):
    m = {
        "id": mid,
        "plugin": plugin,
        "model": model,
        "version": "2.0.0",
        "params": params or [],
        "pos": [x, y],
    }
    if data:
        m["data"] = data
    if model in ("AudioInterface", "AudioInterface2"):
        m["data"] = {
            "audio": {"driver": 0, "deviceName": "", "sampleRate": 44100.0,
                      "blockSize": 256, "inputOffset": 0, "outputOffset": 0}
        }
    return m


def cab(cid, out_mod, out_port, in_mod, in_port):
    return {
        "id": cid,
        "outputModuleId": out_mod,
        "outputId": out_port,
        "inputModuleId": in_mod,
        "inputId": in_port,
        "color": random.choice(["#c91847", "#0c8e15", "#0986ad", "#c9b70e", "#ffb437"]),
    }


def save(name, modules, cables):
    patch = {"version": "1.1.6", "modules": modules, "cables": cables}
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    path = GENERATED_DIR / f"{name}.vcv"
    path.write_text(json.dumps(patch, indent=2))

    v = PatchValidator(patch)
    valid = v.validate()
    status = "PASS" if valid else "FAIL"
    errs = f" — {'; '.join(v.errors)}" if v.errors else ""
    print(f"  [{status}] {name} ({len(modules)} mod, {len(cables)} cab){errs}")
    return valid


# ============================================================
# TEMPLATE A: Omri Cohen "Basic Sequence" pattern
# Proven wiring: SEQ3→Quantizer→VCO, SEQ3 trigger→ADSR, ADSR→VCMixer CV
# NO VCA-1 — VCMixer handles amplitude via ADSR→channel CV
# ============================================================

def omri_sequenced(variant=0):
    AUDIO, SEQ, VCO, SCOPE, MIX, QUANT, ADSR = 25, 112, 113, 114, 115, 116, 117

    # Random step values for variety
    step_vals = [random.uniform(0.0, 2.0) for _ in range(8)]
    seq_params = [
        p(0, random.uniform(1.5, 4.0)),  # tempo
        p(1, 0.0), p(2, 0.0),
        p(3, random.choice([4.0, 6.0, 7.0, 8.0])),  # steps
        p(4, 0.0), p(5, 0.0),
    ]
    # Params 6-13 = Row 1 knobs, 14-21 = Row 2 knobs
    for i, val in enumerate(step_vals):
        seq_params.append(p(14 + i, val))  # Row 2 (used for pitch)
    # Set some gates
    gate_pattern = [random.choice([0, 1]) for _ in range(8)]
    gate_pattern[0] = 1  # ensure at least first step has gate

    vco_wave = random.choice([0, 1, 2, 3])  # sin, tri, saw, sqr
    adsr_a = random.uniform(0.0, 0.3)
    adsr_d = random.uniform(0.2, 0.6)
    adsr_s = random.uniform(0.3, 0.7)
    adsr_r = random.uniform(0.2, 0.5)

    modules = [
        mod(AUDIO, "Core", "AudioInterface", 67, 0),
        mod(SEQ, "Fundamental", "SEQ3", 0, 0, seq_params,
            data={"running": True, "gates": gate_pattern}),
        mod(VCO, "Fundamental", "VCO", 25, 0,
            [p(2, random.choice([0.0, -12.0]))]),
        mod(SCOPE, "Fundamental", "Scope", 43, 0),
        mod(MIX, "Fundamental", "VCMixer", 57, 0,
            [p(0, 1.0), p(1, 1.0), p(2, 1.0), p(3, 1.0), p(4, 1.0)]),
        mod(QUANT, "Fundamental", "Quantizer", 22, 0),
        mod(ADSR, "Fundamental", "ADSR", 35, 0,
            [p(0, adsr_a), p(1, adsr_d), p(2, adsr_s), p(3, adsr_r)]),
    ]

    # Exact wiring from Omri Cohen's working patch
    cables = [
        cab(1, VCO, vco_wave, MIX, 1),    # VCO wave → VCMixer Ch1
        cab(2, MIX, 0, AUDIO, 0),          # VCMixer → Audio L
        cab(3, MIX, 0, AUDIO, 1),          # VCMixer → Audio R
        cab(4, SEQ, 1, QUANT, 0),          # SEQ3 Row2 → Quantizer
        cab(5, QUANT, 0, VCO, 0),          # Quantizer → VCO V/Oct
        cab(6, SEQ, 0, ADSR, 4),           # SEQ3 Trigger → ADSR Gate
        cab(7, SEQ, 0, SCOPE, 1),          # SEQ3 Trigger → Scope Ch2
        cab(8, ADSR, 0, SCOPE, 0),         # ADSR Env → Scope Ch1
        cab(9, ADSR, 0, MIX, 5),           # ADSR Env → VCMixer Ch1 CV
    ]

    return modules, cables, "omri-seq"


# ============================================================
# TEMPLATE B: Drone (no VCA, no ADSR, no gate needed)
# VCOs → VCMixer → Audio directly, LFO on filter
# ============================================================

def drone(variant=0):
    AUDIO, V1, V2, MIX, SCOPE = 1, 2, 3, 4, 5
    LFO1, NOISE = 6, 7

    base = random.choice([0.0, -12.0])
    detune = random.uniform(0.03, 0.12)
    w1 = random.choice([1, 2])  # tri or saw
    w2 = random.choice([0, 1])  # sin or tri

    modules = [
        mod(V1, "Fundamental", "VCO", 0, 0, [p(2, base)]),
        mod(V2, "Fundamental", "VCO", 15, 0, [p(2, base + detune)]),
        mod(NOISE, "Fundamental", "Noise", 30, 0),
        mod(MIX, "Fundamental", "VCMixer", 38, 0,
            [p(0, 1.0), p(1, 1.0), p(2, 1.0), p(3, 0.5), p(4, 0.3)]),
        mod(LFO1, "Fundamental", "LFO", 0, 1,
            [p(0, random.uniform(-5.0, -3.0))]),
        mod(AUDIO, "Core", "AudioInterface", 55, 0),
        mod(SCOPE, "Fundamental", "Scope", 71, 0),
    ]

    # Direct: VCOs + noise → mixer → audio. No filter, no VCA.
    cables = [
        cab(1, V1, w1, MIX, 1),           # VCO1 → mixer ch1
        cab(2, V2, w2, MIX, 2),           # VCO2 → mixer ch2
        cab(3, NOISE, 1, MIX, 3),         # pink noise → mixer ch3
        cab(4, MIX, 0, AUDIO, 0),         # mixer → audio L
        cab(5, MIX, 0, AUDIO, 1),         # mixer → audio R
        cab(6, MIX, 0, SCOPE, 0),         # mixer → scope ch1
        cab(7, LFO1, 0, V2, 1),           # LFO → VCO2 FM (subtle wobble)
        cab(8, LFO1, 0, SCOPE, 1),        # LFO → scope ch2 (visual reference)
    ]

    return modules, cables, "drone"


# ============================================================
# TEMPLATE C: Generative — Random→Quantizer→VCO→VCMixer, ADSR via mixer CV
# Same "no VCA" pattern as Omri Cohen
# ============================================================

def generative(variant=0):
    AUDIO, RND, QUANT, VCO, MIX, ADSR, LFO, DLY, SCOPE = 1, 2, 3, 4, 5, 6, 7, 8, 9

    rate = random.uniform(0.8, 2.5)
    wave = random.choice([0, 1, 2])
    adsr_a = random.uniform(0.1, 0.5)
    adsr_r = random.uniform(0.3, 0.8)

    modules = [
        mod(RND, "Fundamental", "Random", 0, 0,
            [p(0, rate), p(4, 1.0), p(5, 1.0)]),
        mod(QUANT, "Fundamental", "Quantizer", 10, 0),
        mod(VCO, "Fundamental", "VCO", 18, 0,
            [p(2, random.choice([0.0, -12.0, 12.0]))]),
        mod(MIX, "Fundamental", "VCMixer", 33, 0,
            [p(0, 1.0), p(1, 1.0), p(2, 1.0), p(3, 1.0), p(4, 1.0)]),
        mod(ADSR, "Fundamental", "ADSR", 0, 1,
            [p(0, adsr_a), p(1, 0.4), p(2, 0.5), p(3, adsr_r)]),
        mod(LFO, "Fundamental", "LFO", 15, 1,
            [p(0, random.uniform(-5.0, -3.0))]),
        mod(DLY, "Fundamental", "Delay", 50, 0,
            [p(0, random.uniform(0.3, 0.6)), p(1, random.uniform(0.4, 0.7)),
             p(2, 0.5), p(3, random.uniform(0.25, 0.45))]),
        mod(AUDIO, "Core", "AudioInterface", 63, 0),
        mod(SCOPE, "Fundamental", "Scope", 79, 0),
    ]

    cables = [
        cab(1, RND, 0, QUANT, 0),         # Random Stepped → Quantizer
        cab(2, QUANT, 0, VCO, 0),          # Quantizer → VCO V/Oct
        cab(3, RND, 4, ADSR, 4),           # Random Trigger → ADSR Gate
        cab(4, VCO, wave, MIX, 1),         # VCO → mixer ch1
        cab(5, ADSR, 0, MIX, 5),           # ADSR Env → mixer ch1 CV
        cab(6, MIX, 0, DLY, 4),            # mixer → delay audio in
        cab(7, DLY, 0, AUDIO, 0),          # delay → audio L
        cab(8, DLY, 0, AUDIO, 1),          # delay → audio R
        cab(9, DLY, 0, SCOPE, 0),          # delay → scope ch1
        cab(10, ADSR, 0, SCOPE, 1),        # envelope → scope ch2
    ]

    return modules, cables, "generative"


# ============================================================
# TEMPLATE D: Subtractive with LFO clock — ADSR→VCMixer CV (not VCA)
# ============================================================

def subtractive(variant=0):
    AUDIO, CLK, VCO, MIX, ADSR, MLFO, SCOPE = 1, 2, 3, 4, 5, 6, 7

    clock_rate = random.uniform(0.5, 2.5)
    wave = random.choice([2, 3])  # saw or square
    freq = random.choice([0.0, -12.0, 12.0])

    modules = [
        mod(CLK, "Fundamental", "LFO", 0, 0, [p(0, clock_rate)]),
        mod(VCO, "Fundamental", "VCO", 15, 0, [p(2, freq)]),
        mod(MLFO, "Fundamental", "LFO", 0, 1,
            [p(0, random.uniform(-4.0, -2.0))]),
        mod(MIX, "Fundamental", "VCMixer", 30, 0,
            [p(0, 1.0), p(1, 1.0), p(2, 1.0)]),
        mod(ADSR, "Fundamental", "ADSR", 42, 0,
            [p(0, random.uniform(0.01, 0.15)),
             p(1, random.uniform(0.2, 0.5)),
             p(2, random.uniform(0.4, 0.7)),
             p(3, random.uniform(0.15, 0.4))]),
        mod(AUDIO, "Core", "AudioInterface", 55, 0),
        mod(SCOPE, "Fundamental", "Scope", 71, 0),
    ]

    cables = [
        cab(1, CLK, 3, ADSR, 4),          # LFO Square → ADSR Gate
        cab(2, VCO, wave, MIX, 1),         # VCO → mixer ch1
        cab(3, ADSR, 0, MIX, 5),           # ADSR Env → mixer ch1 CV
        cab(4, MIX, 0, AUDIO, 0),          # mixer → audio L
        cab(5, MIX, 0, AUDIO, 1),          # mixer → audio R
        cab(6, MIX, 0, SCOPE, 0),          # mixer → scope ch1
        cab(7, MLFO, 1, VCO, 1),           # slow LFO tri → VCO FM (vibrato)
        cab(8, CLK, 3, SCOPE, 1),          # gate clock → scope ch2
    ]

    return modules, cables, "subtractive"


# ============================================================
# TEMPLATE E: Two voices mixed
# ============================================================

def dual_voice(variant=0):
    AUDIO, CLK, V1, V2, MIX, E1, E2, SCOPE = 1, 2, 3, 4, 5, 6, 7, 8

    clock_rate = random.uniform(0.8, 2.5)
    freq1 = random.choice([0.0, -12.0])
    freq2 = freq1 + random.choice([5, 7, 12])  # musical interval

    modules = [
        mod(CLK, "Fundamental", "LFO", 0, 0, [p(0, clock_rate)]),
        mod(V1, "Fundamental", "VCO", 10, 0, [p(2, freq1)]),
        mod(V2, "Fundamental", "VCO", 25, 0, [p(2, freq2)]),
        mod(MIX, "Fundamental", "VCMixer", 40, 0,
            [p(0, 1.0), p(1, 1.0), p(2, 1.0), p(3, 1.0), p(4, 1.0)]),
        mod(E1, "Fundamental", "ADSR", 10, 1,
            [p(0, random.uniform(0.01, 0.1)), p(1, 0.4), p(2, 0.5), p(3, 0.3)]),
        mod(E2, "Fundamental", "ADSR", 25, 1,
            [p(0, random.uniform(0.05, 0.2)), p(1, 0.5), p(2, 0.6), p(3, 0.4)]),
        mod(AUDIO, "Core", "AudioInterface", 57, 0),
        mod(SCOPE, "Fundamental", "Scope", 73, 0),
    ]

    w1 = random.choice([1, 2, 3])
    w2 = random.choice([0, 1, 2])

    cables = [
        cab(1, CLK, 3, E1, 4),            # clock → env1 gate
        cab(2, CLK, 3, E2, 4),            # clock → env2 gate
        cab(3, V1, w1, MIX, 1),           # VCO1 → mixer ch1
        cab(4, V2, w2, MIX, 2),           # VCO2 → mixer ch2
        cab(5, E1, 0, MIX, 5),            # env1 → mixer ch1 CV
        cab(6, E2, 0, MIX, 6),            # env2 → mixer ch2 CV
        cab(7, MIX, 0, AUDIO, 0),         # mixer → audio L
        cab(8, MIX, 0, AUDIO, 1),         # mixer → audio R
        cab(9, MIX, 0, SCOPE, 0),         # audio → scope ch1
        cab(10, CLK, 3, SCOPE, 1),        # gate clock → scope ch2
    ]

    return modules, cables, "dual-voice"


TEMPLATES = [omri_sequenced, drone, generative, subtractive, dual_voice]


def main():
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    random.seed(77)

    results = []
    for i in range(20):
        tpl = TEMPLATES[i % len(TEMPLATES)]
        modules, cables, archetype = tpl(variant=i)
        name = f"{i+1:02d}-{archetype}"
        save(name, modules, cables)
        results.append({"name": name, "archetype": archetype})

    manifest = GENERATED_DIR / "manifest.json"
    manifest.write_text(json.dumps(results, indent=2))
    print(f"\nOpen in Rack: \\\\wsl$\\Ubuntu{GENERATED_DIR}")


if __name__ == "__main__":
    main()
