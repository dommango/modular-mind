"""Step 10: Build modular synthesis knowledge base from corpus + research.

Generates persistent reference files for patch generation:
  - data/reference/synthesis-fundamentals.md
  - data/reference/patch-building-guide.md
  - data/reference/module-quick-ref.md
"""

import json
from config import OUTPUT_DIR

REF_DIR = OUTPUT_DIR.parent / "reference"

# Ground-truth port maps from C++ source (verified against enum definitions)
# These override the registry where the registry is wrong
PORT_MAPS = {
    "Fundamental:VCO": {
        "inputs": {0: "V/Oct", 1: "FM", 2: "Sync", 3: "PW CV"},
        "outputs": {0: "Sine", 1: "Triangle", 2: "Sawtooth", 3: "Square"},
        "params": {1: "Sync mode", 2: "Frequency", 4: "FM depth", 5: "Pulse width", 6: "PW CV atten", 7: "FM mode"},
    },
    "Fundamental:VCF": {
        "inputs": {0: "Freq CV", 1: "Res CV", 2: "Drive CV", 3: "Audio in"},
        "outputs": {0: "Lowpass", 1: "Highpass"},
        "params": {0: "Cutoff freq", 2: "Resonance", 3: "Freq CV atten", 4: "Drive", 5: "Res CV atten", 6: "Drive CV atten"},
    },
    "Fundamental:VCA-1": {
        "inputs": {0: "CV", 1: "Audio in"},
        "outputs": {0: "Audio out"},
        "params": {0: "Level", 1: "Response (0=exp, 1=lin)"},
    },
    "Fundamental:ADSR": {
        "inputs": {0: "Attack CV", 1: "Decay CV", 2: "Sustain CV", 3: "Release CV", 4: "Gate", 5: "Retrigger"},
        "outputs": {0: "Envelope"},
        "params": {0: "Attack", 1: "Decay", 2: "Sustain", 3: "Release"},
    },
    "Fundamental:LFO": {
        "inputs": {0: "FM", 2: "Reset", 3: "PW CV", 4: "Clock"},
        "outputs": {0: "Sine", 1: "Triangle", 2: "Sawtooth", 3: "Square"},
        "params": {0: "Frequency", 1: "Offset/Invert"},
    },
    "Fundamental:SEQ3": {
        "inputs": {0: "Tempo CV", 1: "Clock", 2: "Reset", 3: "Steps CV", 4: "Run"},
        "outputs": {0: "Row 1", 1: "Row 2", 2: "Row 3", 3: "Gate row 1", 4: "Gate row 2", 5: "Gate row 3"},
        "params": {0: "Tempo", 3: "Steps"},
    },
    "Fundamental:Quantizer": {
        "inputs": {0: "Pitch in"},
        "outputs": {0: "Pitch out"},
        "params": {1: "Pre-offset"},
    },
    "Fundamental:VCMixer": {
        "inputs": {0: "Mix CV", 1: "Ch1 in", 2: "Ch1 CV", 3: "Ch2 in", 4: "Ch2 CV", 5: "Ch3 in", 6: "Ch3 CV", 7: "Ch4 in", 8: "Ch4 CV"},
        "outputs": {0: "Mix out", 1: "Ch1 out", 2: "Ch2 out", 3: "Ch3 out", 4: "Ch4 out"},
        "params": {0: "Mix level", 1: "Ch1 level", 2: "Ch2 level", 3: "Ch3 level", 4: "Ch4 level"},
    },
    "Fundamental:Delay": {
        "inputs": {0: "Time CV", 1: "Feedback CV", 2: "Tone CV", 3: "Mix CV", 4: "Audio in", 5: "Clock"},
        "outputs": {0: "Mix out", 1: "Wet out"},
        "params": {0: "Time", 1: "Feedback", 2: "Tone", 3: "Mix"},
    },
    "Fundamental:Random": {
        "inputs": {0: "Rate CV", 1: "Shape CV", 2: "Trigger in", 3: "External", 4: "Prob CV", 5: "Spread CV"},
        "outputs": {0: "Stepped", 1: "Linear", 2: "Smooth", 3: "Exponential", 4: "Trigger"},
        "params": {0: "Rate", 1: "Shape", 4: "Probability", 5: "Spread"},
    },
    "Fundamental:Noise": {
        "inputs": {},
        "outputs": {0: "White", 1: "Pink", 2: "Red", 3: "Violet", 4: "Blue", 5: "Gray", 6: "Black"},
        "params": {},
    },
    "Fundamental:Scope": {
        "inputs": {0: "Ch1", 1: "Ch2", 2: "Ext trig"},
        "outputs": {0: "Ch1", 1: "Ch2"},
        "params": {0: "Gain 1", 1: "Offset 1", 4: "Time"},
    },
    "Core:AudioInterface": {
        "inputs": {0: "To device 1", 1: "To device 2", 2: "To device 3", 3: "To device 4",
                   4: "To device 5", 5: "To device 6", 6: "To device 7", 7: "To device 8"},
        "outputs": {0: "From device 1", 1: "From device 2"},
        "params": {},
    },
    "Core:AudioInterface2": {
        "inputs": {0: "To device 1", 1: "To device 2"},
        "outputs": {0: "From device 1", 1: "From device 2"},
        "params": {0: "Level"},
    },
}


def build_fundamentals():
    lines = [
        "---",
        "name: Modular Synthesis Fundamentals",
        "type: reference",
        "triggers: [synthesis, fundamentals, signal, cv, gate, trigger, voltage, modular, basics]",
        "---",
        "",
        "# Modular Synthesis Fundamentals",
        "",
        "Core concepts for understanding and generating VCV Rack patches.",
        "",
        "## Signal Types",
        "",
        "Everything in modular synthesis is **voltage**. The difference is frequency and intent:",
        "",
        "| Signal | Frequency | Purpose | Example |",
        "|--------|-----------|---------|---------|",
        "| **Audio** | 20 Hz – 20 kHz | Sound you hear | VCO output |",
        "| **CV (Control Voltage)** | < 20 Hz | Modulate parameters | LFO output, envelope output |",
        "| **Gate** | On/Off | Sustaining trigger | Key held down = +5V, released = 0V |",
        "| **Trigger** | Brief pulse (~5ms) | Momentary event | Clock tick, drum hit |",
        "",
        "**Key principle:** Audio and CV are the same signal at different frequencies. An LFO at 0.5 Hz is CV; speed it up to 440 Hz and it's an audio oscillator.",
        "",
        "## Pitch Standard: 1 Volt Per Octave",
        "",
        "- 0V = C4 (middle C, 261.63 Hz)",
        "- +1V = C5 (one octave up)",
        "- -1V = C3 (one octave down)",
        "- 1/12V (0.0833V) = one semitone",
        "",
        "All VCO pitch inputs expect this standard. Quantizers snap continuous CV to these intervals.",
        "",
        "## The Subtractive Signal Chain",
        "",
        "```",
        "VCO ──→ VCF ──→ VCA ──→ Audio Output",
        " │              ↑       ↑",
        " │         Envelope  Envelope",
        " │              ↑       ↑",
        " │            Gate    Gate",
        " ↑",
        "Pitch CV (from sequencer, quantizer, or keyboard)",
        "```",
        "",
        "**Why this order:**",
        "1. **VCO** generates harmonically rich waveform (sawtooth, square)",
        "2. **VCF** removes frequencies — sculpts timbre. Filter before amp preserves harmonic detail",
        "3. **VCA** controls loudness — shapes dynamics. Last in chain so filtered tone is preserved",
        "",
        "## Envelope Lifecycle (ADSR)",
        "",
        "```",
        "Gate HIGH ──→ Attack (rise) ──→ Decay (fall to sustain) ──→ Sustain (hold)",
        "Gate LOW  ──→ Release (fade to 0)",
        "```",
        "",
        "**CRITICAL: No gate = no envelope = no sound** from an ADSR-controlled VCA.",
        "Every ADSR needs a gate source: LFO square output, sequencer gate, clock, or keyboard.",
        "",
        "- ADSR **Gate input is port 4** (not 0!)",
        "- ADSR **Envelope output is port 0**",
        "",
        "## Modulation Conventions",
        "",
        "| Modulation | Source → Target | Musical Effect |",
        "|-----------|----------------|----------------|",
        "| Filter sweep | LFO → VCF cutoff CV | Rhythmic brightness change (wah) |",
        "| Tremolo | LFO → VCA CV | Volume wobble |",
        "| Vibrato | LFO → VCO FM | Pitch wobble |",
        "| Dynamics | Envelope → VCA CV | Note shape (pluck, pad, swell) |",
        "| Brightness tracking | Envelope → VCF cutoff CV | Bright attack, dark decay |",
        "",
        "## Corpus Parameter Insights",
        "",
        "From 137 analyzed patches:",
        "",
        "| Parameter | Typical Value | Meaning |",
        "|-----------|--------------|---------|",
        "| VCO Frequency | median 0.0 | C4 (middle C) |",
        "| VCF cutoff CV depth | mean 0.32 | Subtle modulation, not extreme |",
        "| VCF Resonance | mean 0.38 | Moderate — not self-oscillating |",
        "| Reverb wet | mean 0.21 | Spatial depth, not drenched |",
        "| Reverb dry | mean 0.86 | Mostly dry signal |",
        "| ADSR Attack | typically 0.05-0.5 | Fast to medium |",
        "| ADSR Release | typically 0.3-0.8 | Medium to long tail |",
        "| Tempo | mean 111 BPM | House/techno range |",
        "",
        "## East Coast vs West Coast",
        "",
        "| | East Coast (Subtractive) | West Coast (Waveshaping) |",
        "|---|---|---|",
        "| **Approach** | Start complex, filter down | Start simple, add harmonics |",
        "| **Core modules** | VCO, VCF, VCA, ADSR | Oscillator, wavefolder, function gen, LPG |",
        "| **Timbre** | Warm, filtered | Complex, evolving |",
        "| **In our corpus** | 16 subtractive + 45 sequenced | Rare — mostly Mutable Instruments patches |",
        "",
    ]
    (REF_DIR / "synthesis-fundamentals.md").write_text("\n".join(lines))


def build_patch_guide():
    lines = [
        "---",
        "name: Patch Building Guide",
        "type: reference",
        "triggers: [build, patch, create, generate, wire, connect, how to, guide, tutorial]",
        "---",
        "",
        "# Patch Building Guide",
        "",
        "Step-by-step process for building VCV Rack patches that produce sound.",
        "",
        "## Pre-Flight Checklist",
        "",
        "Before generating ANY patch, verify:",
        "- [ ] Every ADSR/envelope has a gate source connected to its Gate input",
        "- [ ] Audio signal chain reaches AudioInterface (input 0=L, 1=R)",
        "- [ ] AudioInterface has `data.audio` block (driver config)",
        "- [ ] VCO has pitch source OR is set to audible frequency",
        "- [ ] No dead-end connections (output going nowhere useful)",
        "",
        "## Step 1: Choose Archetype",
        "",
        "| Archetype | Key Modules | Signal Flow |",
        "|-----------|------------|-------------|",
        "| Subtractive voice | VCO, VCF, VCA, ADSR, LFO | Clock→ADSR, VCO→VCF→VCA→Audio |",
        "| Generative ambient | Random, Quantizer, VCO, VCF, VCA, Delay | Random→Quant→VCO→VCF→VCA→Delay→Audio |",
        "| Drone/texture | 2×VCO, VCF, VCA, 2×LFO, Noise | VCOs+Noise→Mixer→VCF→VCA→Audio (no gate needed if VCA bias) |",
        "| Drum machine | Clock, Drum modules, Mixer | Clock→Triggers→Drums→Mixer→Audio |",
        "| Sequenced composition | SEQ3, Quantizer, VCO, VCF, VCA, ADSR | SEQ3→Quant→VCO→VCF→VCA→Audio |",
        "",
        "## Step 2: Place Audio Output",
        "",
        "Always start with `Core:AudioInterface` (8-channel) or `Core:AudioInterface2` (2-channel).",
        "Include the `data` block so Rack initializes the audio driver:",
        "",
        '```json',
        '"data": {"audio": {"driver": 0, "deviceName": "", "sampleRate": 44100.0, "blockSize": 256}}',
        '```',
        "",
        "User must right-click and select their device once. After that it persists.",
        "",
        "## Step 3: Build Voice Chain",
        "",
        "### Subtractive voice (most common)",
        "```",
        "VCO:Sawtooth(out 2) ──→ VCF:Audio(in 3) ──→ VCA-1:Audio(in 1) ──→ Audio:L(in 0)",
        "                                                                  ──→ Audio:R(in 1)",
        "```",
        "",
        "### Generative melodic voice",
        "```",
        "Random:Stepped(out 0) ──→ Quantizer:Pitch(in 0)",
        "Quantizer:Pitch(out 0) ──→ VCO:V/Oct(in 0)",
        "Random:Trigger(out 4)  ──→ ADSR:Gate(in 4)    ← THIS IS THE KEY CONNECTION",
        "```",
        "",
        "## Step 4: Add Gate/Clock Source",
        "",
        "**EVERY envelope needs a gate.** Common sources:",
        "",
        "| Source | Output | Use Case |",
        "|--------|--------|----------|",
        "| LFO Square output (port 3) | Continuous gates at LFO rate | Simple rhythmic triggering |",
        "| SEQ3 Gate outputs (ports 3-5) | Step-synced gates | Sequenced patterns |",
        "| Random Trigger (port 4) | Probabilistic triggers | Generative/random |",
        "| External MIDI | Gate from keyboard | Performance |",
        "",
        "## Step 5: Add Modulation",
        "",
        "Typical modulation depth from corpus: **subtle, not extreme**.",
        "",
        "```",
        "LFO:Sine(out 0) ──→ VCF:Freq CV(in 0)     # filter sweep",
        "ADSR:Env(out 0)  ──→ VCA-1:CV(in 0)         # amplitude envelope",
        "ADSR:Env(out 0)  ──→ VCF:Freq CV(in 0)      # brightness tracking",
        "```",
        "",
        "## Step 6: Set Parameters",
        "",
        "Use corpus medians as starting points:",
        "- VCO Frequency: 0.0 (C4) — adjust by ±12 for octave shifts",
        "- ADSR: Attack=0.05, Decay=0.4, Sustain=0.6, Release=0.3",
        "- VCF Cutoff CV atten: 0.3 (subtle sweep)",
        "- LFO Frequency: -3.0 (slow modulation), 1.0 (rhythmic clock)",
        "",
        "## Step 7: Verify",
        "",
        "Trace the signal from every source to AudioInterface. Ask:",
        "1. Does audio reach the output? (VCO→...→AudioInterface)",
        "2. Does every VCA have a CV source or bias? (no CV = silence)",
        "3. Does every ADSR have a gate? (no gate = envelope stays at 0)",
        "4. Are port IDs correct? (consult module-quick-ref.md)",
        "",
    ]
    (REF_DIR / "patch-building-guide.md").write_text("\n".join(lines))


def build_quick_ref():
    distributions = json.loads((OUTPUT_DIR / "param_distributions.json").read_text())
    patterns = json.loads((OUTPUT_DIR / "connection_patterns.json").read_text())

    lines = [
        "---",
        "name: Module Quick Reference",
        "type: reference",
        "triggers: [module, port, input, output, param, id, quick ref, lookup, wiring]",
        "---",
        "",
        "# Module Quick Reference",
        "",
        "Verified port IDs for the most-used modules. Use this when generating cables.",
        "Port IDs verified against C++ source enums — these are ground truth.",
        "",
    ]

    for key, ports in sorted(PORT_MAPS.items()):
        plugin, model = key.split(":", 1)

        lines.append(f"## {key}")
        lines.append("")

        if ports["inputs"]:
            inp_str = ", ".join(f"**{k}**={v}" for k, v in sorted(ports["inputs"].items()))
            lines.append(f"**Inputs:** {inp_str}")

        if ports["outputs"]:
            out_str = ", ".join(f"**{k}**={v}" for k, v in sorted(ports["outputs"].items()))
            lines.append(f"**Outputs:** {out_str}")

        if ports["params"]:
            param_parts = []
            for pid, name in sorted(ports["params"].items()):
                dist_key = f"{plugin}:{model}:{name}"
                d = distributions.get(dist_key)
                if d:
                    param_parts.append(f"**{pid}**={name} (median={d['median']:.2f})")
                else:
                    param_parts.append(f"**{pid}**={name}")
            lines.append(f"**Params:** {', '.join(param_parts)}")

        conn_from = [p for p in patterns["port_pairs"] if p["from"].startswith(key + ":")][:3]
        conn_to = [p for p in patterns["port_pairs"] if p["to"].startswith(key + ":")][:3]
        if conn_from or conn_to:
            lines.append("")
            lines.append("**Common wiring:**")
            for c in conn_from:
                lines.append(f"- {c['from']} → {c['to']} ({c['count']}x)")
            for c in conn_to:
                lines.append(f"- {c['from']} → {c['to']} ({c['count']}x)")

        lines.append("")

    (REF_DIR / "module-quick-ref.md").write_text("\n".join(lines))


def main():
    REF_DIR.mkdir(parents=True, exist_ok=True)

    print("Building synthesis fundamentals...")
    build_fundamentals()

    print("Building patch building guide...")
    build_patch_guide()

    print("Building module quick reference...")
    build_quick_ref()

    print(f"\nWritten to {REF_DIR}/:")
    print(f"  synthesis-fundamentals.md")
    print(f"  patch-building-guide.md")
    print(f"  module-quick-ref.md")


if __name__ == "__main__":
    main()
