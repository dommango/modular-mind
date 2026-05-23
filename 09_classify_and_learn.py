"""Step 9: Classify patches into archetypes and extract learning artifacts.

Reads decoded patches and existing analysis to produce:
  - data/reference/archetypes.md      — patch archetype catalog
  - data/reference/voice-patterns.md  — recurring voice architectures
  - data/reference/connection-grammar.md — formalized connection rules
  - Updates data/reference/patches/*.md  — adds archetype to frontmatter
"""

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from config import OUTPUT_DIR

REF_DIR = OUTPUT_DIR.parent / "reference"
PATCHES_DIR = REF_DIR / "patches"

MIDI_MODULES = {"MIDIToCVInterface", "MIDI-Map", "MIDICCToCVInterface", "MIDITriggerToCVInterface"}
CLOCK_MODULES = {"Clocked", "Clocked-Clkd", "PwmClock", "BPMClock", "ClockM8"}
SEQ_MODULES = {"SEQ3", "S16", "AG", "CV", "Chords", "AddrSeq", "Bogaudio-AddrSeq"}
RANDOM_MODULES = {"Marbles", "Random", "Bogaudio-Walk", "Bogaudio-Walk2", "RndC", "RndH", "Noise"}
REVERB_MODULES = {"Plateau", "Galaxy", "Freeverb", "ReverbFx", "ReverbStereoFx", "SpringReverb"}
FILTER_MODULES = {"VCF", "L4P", "SPF", "Bogaudio-VCF", "LVCF", "SurgeXTVCF", "LWF"}
VCO_MODULES = {"VCO", "VCO2", "Bogaudio-VCO", "Bogaudio-LVCO", "Bogaudio-Sine", "Osc4", "FS6",
               "Bogaudio-XCO", "Bogaudio-Additator", "Plaits", "Braids", "Tides", "Tides2"}
FM_MODULES = {"Bogaudio-FMOp", "FMOp"}
DRUM_MODULES = {"Drums", "Bogaudio-AD"}
ENV_MODULES = {"ADSR", "EVA", "Bogaudio-Shaper", "Bogaudio-ShaperPlus", "Stages", "ComplexEG"}
VCA_MODULES = {"VCA", "VCA-1", "Bogaudio-VCA"}
QUANT_MODULES = {"Quantizer", "Quant", "Quantum"}
MIXER_MODULES = {"VCMixer", "Mixer", "Mix4", "Mix8", "MixMasterJr", "AUX", "M16S", "M16", "StMix"}
OUTPUT_MODULES = {"AudioInterface", "AudioInterface2", "AudioInterface16"}


def get_module_set(patch):
    return {m["model"] for m in patch["modules"]}


def get_plugin_model_set(patch):
    return {(m["plugin"], m["model"]) for m in patch["modules"]}


def has_any(module_set, reference):
    return bool(module_set & reference)


def count_of(module_set, reference):
    return len(module_set & reference)


def classify_patch(patch, profiles):
    mods = get_module_set(patch)
    connections = patch["connections"]

    has_midi = has_any(mods, MIDI_MODULES)
    has_clock = has_any(mods, CLOCK_MODULES)
    has_seq = has_any(mods, SEQ_MODULES)
    has_random = has_any(mods, RANDOM_MODULES)
    has_reverb = has_any(mods, REVERB_MODULES)
    has_filter = has_any(mods, FILTER_MODULES)
    has_vco = has_any(mods, VCO_MODULES)
    has_fm = has_any(mods, FM_MODULES)
    has_drums = has_any(mods, DRUM_MODULES)
    has_env = has_any(mods, ENV_MODULES)
    has_vca = has_any(mods, VCA_MODULES)
    has_quant = has_any(mods, QUANT_MODULES)

    n_vco = count_of(mods, VCO_MODULES)
    n_fm = count_of(mods, FM_MODULES)
    n_output = count_of(mods, OUTPUT_MODULES)
    n_mixer = count_of(mods, MIXER_MODULES)

    fm_instance_count = sum(1 for m in patch["modules"] if m["model"] in FM_MODULES)
    drum_instance_count = sum(1 for m in patch["modules"] if m["model"] in DRUM_MODULES)
    mc = patch["stats"]["module_count"]

    scores = {
        "generative-ambient": 0,
        "drum-machine": 0,
        "subtractive-voice": 0,
        "fm-synthesis": 0,
        "drone-texture": 0,
        "sequenced-composition": 0,
    }

    if has_random:
        scores["generative-ambient"] += 3
    if not has_midi:
        scores["generative-ambient"] += 1
        scores["drone-texture"] += 1
    if has_reverb:
        scores["generative-ambient"] += 1
        scores["drone-texture"] += 1
    if has_clock and not has_seq:
        scores["generative-ambient"] += 1

    if has_drums or drum_instance_count >= 3:
        scores["drum-machine"] += 4
    if n_mixer >= 2:
        scores["drum-machine"] += 1

    if has_vco and has_filter and has_env and has_vca:
        scores["subtractive-voice"] += 4
    if has_quant:
        scores["subtractive-voice"] += 1
        scores["sequenced-composition"] += 1

    if has_fm or fm_instance_count >= 2:
        scores["fm-synthesis"] += 4
    if fm_instance_count >= 5:
        scores["fm-synthesis"] += 3

    if not has_clock and not has_seq and has_reverb:
        scores["drone-texture"] += 3
    if not has_clock and not has_seq and not has_random:
        scores["drone-texture"] += 1

    if has_seq and has_quant:
        scores["sequenced-composition"] += 3
    if has_seq and has_clock:
        scores["sequenced-composition"] += 2
    if has_midi:
        scores["sequenced-composition"] += 2

    best = max(scores, key=scores.get)
    confidence = scores[best] / max(sum(scores.values()), 1)
    return best, round(confidence, 2)


def extract_voice_chains(decoded_patches):
    chain_counter = Counter()

    for patch in decoded_patches:
        adj = defaultdict(list)
        for c in patch["connections"]:
            adj[c["from_module"]].append(c)

        module_map = {m["instance_id"]: m for m in patch["modules"]}

        in_mods = {c["to_module"] for c in patch["connections"]}
        out_mods = {c["from_module"] for c in patch["connections"]}
        sources = out_mods - in_mods
        if not sources:
            sources = out_mods

        for start in sources:
            _trace_voice(start, adj, module_map, [], set(), chain_counter)

    patterns = []
    for chain, count in chain_counter.most_common(30):
        if count >= 2 and len(chain) >= 2:
            patterns.append({"chain": list(chain), "count": count})

    return patterns


def _trace_voice(mid, adj, module_map, current, visited, counter, max_len=5):
    if mid in visited or len(current) >= max_len:
        if len(current) >= 2:
            counter[tuple(current)] += 1
        return

    visited.add(mid)
    mod = module_map.get(mid, {})
    role_label = mod.get("model", "?")

    nexts = adj.get(mid, [])
    if not nexts:
        chain = current + [role_label]
        if len(chain) >= 2:
            counter[tuple(chain)] += 1
        visited.discard(mid)
        return

    for c in nexts:
        _trace_voice(
            c["to_module"], adj, module_map,
            current + [role_label], visited, counter, max_len,
        )
    visited.discard(mid)


def build_connection_grammar(patterns, profiles):
    rules = defaultdict(lambda: Counter())
    port_types = {}

    for pair in patterns["port_pairs"]:
        from_parts = pair["from"].split(":")
        to_parts = pair["to"].split(":")
        if len(from_parts) >= 3 and len(to_parts) >= 3:
            from_model = from_parts[1]
            from_port = from_parts[2]
            to_model = to_parts[1]
            to_port = to_parts[2]

            from_role = profiles.get(f"{from_parts[0]}:{from_model}", {}).get("role", "?")
            to_role = profiles.get(f"{to_parts[0]}:{to_model}", {}).get("role", "?")

            rules[f"{from_role}:{from_port}"][f"{to_role}:{to_port}"] += pair["count"]

    output_rules = {}
    for source, targets in rules.items():
        top = targets.most_common(5)
        output_rules[source] = [{"target": t, "count": c} for t, c in top]

    never_seen = set()
    role_pairs_seen = set()
    for pair in patterns["port_pairs"]:
        from_parts = pair["from"].split(":")
        to_parts = pair["to"].split(":")
        if len(from_parts) >= 3 and len(to_parts) >= 3:
            from_role = profiles.get(f"{from_parts[0]}:{from_parts[1]}", {}).get("role", "?")
            to_role = profiles.get(f"{to_parts[0]}:{to_parts[1]}", {}).get("role", "?")
            role_pairs_seen.add((from_role, to_role))

    all_roles = ["source", "processor", "output", "utility"]
    for r1 in all_roles:
        for r2 in all_roles:
            if (r1, r2) not in role_pairs_seen:
                never_seen.add(f"{r1} → {r2}")

    return {
        "production_rules": output_rules,
        "anti_patterns": sorted(never_seen),
    }


def write_archetypes_md(classifications, decoded_patches, profiles):
    by_archetype = defaultdict(list)
    for patch, (archetype, confidence) in zip(decoded_patches, classifications):
        by_archetype[archetype].append((patch, confidence))

    lines = [
        "---",
        "name: VCV Rack Patch Archetypes",
        "type: reference",
        "triggers: [archetype, classification, patch type, generative, ambient, drum, subtractive, fm, drone, sequencer]",
        "---",
        "",
        "# Patch Archetypes",
        "",
        f"137 patches classified into {len(by_archetype)} archetypes.",
        "",
    ]

    archetype_descriptions = {
        "generative-ambient": "Self-playing patches using randomness, LFOs, and probability to create evolving soundscapes. No MIDI input — the patch plays itself. Typically features reverb (Plateau) and delay for spatial depth.",
        "drum-machine": "Rhythm-focused patches with drum synthesis modules, clock-driven patterns, and multi-channel mixing. Often uses sequencers to trigger percussive voices.",
        "subtractive-voice": "Classic synthesizer voice: oscillator → filter → amplifier. Uses envelopes (ADSR) to shape dynamics and quantizers for pitched sequences.",
        "fm-synthesis": "Frequency modulation synthesis using FM operator modules. Multiple operators modulate each other's frequencies for complex timbres.",
        "drone-texture": "Sustained, evolving sounds without rhythmic structure. No clock or sequencer — relies on slow modulation, feedback, and heavy processing.",
        "sequenced-composition": "Structured musical pieces with step sequencers, quantizers, and deliberate note patterns. May include MIDI input for external control.",
    }

    for archetype in sorted(by_archetype.keys()):
        patches = by_archetype[archetype]
        patches.sort(key=lambda x: -x[0]["like_count"])

        lines.append(f"## {archetype.replace('-', ' ').title()} ({len(patches)} patches)")
        lines.append("")
        lines.append(archetype_descriptions.get(archetype, ""))
        lines.append("")

        plugin_freq = Counter()
        for patch, _ in patches:
            for m in patch["modules"]:
                plugin_freq[f"{m['plugin']}:{m['model']}"] += 1
        top_modules = plugin_freq.most_common(8)
        lines.append("**Defining modules:** " + ", ".join(f"{m} ({c}x)" for m, c in top_modules))
        lines.append("")

        avg_mods = sum(p["stats"]["module_count"] for p, _ in patches) / len(patches)
        avg_cables = sum(p["stats"]["cable_count"] for p, _ in patches) / len(patches)
        avg_likes = sum(p["like_count"] for p, _ in patches) / len(patches)
        lines.append(f"**Avg:** {avg_mods:.0f} modules, {avg_cables:.0f} cables, {avg_likes:.1f} likes")
        lines.append("")

        lines.append("**Example patches:**")
        lines.append("")
        for patch, conf in patches[:5]:
            lines.append(f"- [{patch['title']}](patches/{patch['id']}.md) by {patch['author']} ({patch['like_count']} likes) — confidence: {conf}")
        lines.append("")

    (REF_DIR / "archetypes.md").write_text("\n".join(lines))
    return by_archetype


def write_voice_patterns_md(voice_patterns):
    lines = [
        "---",
        "name: Voice Architecture Patterns",
        "type: reference",
        "triggers: [voice, signal flow, chain, routing, architecture, wiring, patching]",
        "---",
        "",
        "# Voice Architecture Patterns",
        "",
        "Recurring module chains extracted from 137 decoded patches.",
        "These represent the most common ways modules are wired together.",
        "",
    ]

    for i, vp in enumerate(voice_patterns[:20], 1):
        chain_str = " → ".join(vp["chain"])
        lines.append(f"## Pattern {i}: {chain_str} ({vp['count']}x)")
        lines.append("")

        if len(vp["chain"]) >= 3:
            first = vp["chain"][0]
            last = vp["chain"][-1]
            middle = vp["chain"][1:-1]
            lines.append(f"- **Source:** {first}")
            if middle:
                lines.append(f"- **Processing:** {' → '.join(middle)}")
            lines.append(f"- **Destination:** {last}")
        lines.append("")

    (REF_DIR / "voice-patterns.md").write_text("\n".join(lines))


def write_connection_grammar_md(grammar, profiles):
    lines = [
        "---",
        "name: Connection Grammar",
        "type: reference",
        "triggers: [connection, grammar, rules, wiring, cable, port, compatible, signal routing]",
        "---",
        "",
        "# Connection Grammar",
        "",
        "Formalized rules for how modules connect, derived from 137 decoded patches.",
        "Use these rules to validate and generate patch connections.",
        "",
        "## Production Rules",
        "",
        "Format: `source_role:port → target_role:port (count)`",
        "",
    ]

    for source, targets in sorted(
        grammar["production_rules"].items(),
        key=lambda x: -sum(t["count"] for t in x[1]),
    )[:40]:
        lines.append(f"### {source}")
        lines.append("")
        for t in targets:
            lines.append(f"- → {t['target']} ({t['count']}x)")
        lines.append("")

    if grammar["anti_patterns"]:
        lines.append("## Anti-Patterns (never observed)")
        lines.append("")
        lines.append("These role-level connections were never seen in the corpus:")
        lines.append("")
        for ap in grammar["anti_patterns"]:
            lines.append(f"- {ap}")
        lines.append("")

    lines.append("## Signal Flow Conventions")
    lines.append("")
    lines.append("1. **Pitch path:** Clock/Sequencer → Quantizer → Oscillator V/Oct input")
    lines.append("2. **Audio path:** Oscillator output → Filter input → VCA input → Mixer → Audio Interface")
    lines.append("3. **Envelope path:** Gate source → Envelope generator → VCA CV or Filter CV")
    lines.append("4. **Modulation path:** LFO → any CV input (filter cutoff, VCA level, oscillator FM)")
    lines.append("5. **Stereo output:** Mixer L/R → Audio Interface inputs 0/1")
    lines.append("")

    (REF_DIR / "connection-grammar.md").write_text("\n".join(lines))


def update_patch_files(decoded_patches, classifications):
    updated = 0
    for patch, (archetype, confidence) in zip(decoded_patches, classifications):
        path = PATCHES_DIR / f"{patch['id']}.md"
        if not path.exists():
            continue

        text = path.read_text()
        if "archetype:" in text:
            text = re.sub(r'archetype:.*\n', f'archetype: {archetype}\n', text)
        elif "---" in text:
            first_fence = text.index("---")
            second_fence = text.index("---", first_fence + 3)
            insert_point = second_fence
            text = text[:insert_point] + f"archetype: {archetype}\n" + text[insert_point:]

        path.write_text(text)
        updated += 1

    return updated


def main():
    decoded = json.loads((OUTPUT_DIR / "decoded_patches.json").read_text())
    patterns = json.loads((OUTPUT_DIR / "connection_patterns.json").read_text())
    profiles = json.loads((OUTPUT_DIR / "module_profiles.json").read_text())

    print(f"Loaded {len(decoded)} decoded patches")

    print("\nClassifying patches...")
    classifications = []
    for patch in decoded:
        archetype, confidence = classify_patch(patch, profiles)
        classifications.append((archetype, confidence))

    counts = Counter(a for a, _ in classifications)
    for archetype, count in counts.most_common():
        avg_conf = sum(c for a, c in classifications if a == archetype) / count
        print(f"  {archetype}: {count} patches (avg confidence: {avg_conf:.2f})")

    print("\nWriting archetypes.md...")
    write_archetypes_md(classifications, decoded, profiles)

    print("Extracting voice patterns...")
    voice_patterns = extract_voice_chains(decoded)
    print(f"  Found {len(voice_patterns)} patterns")
    write_voice_patterns_md(voice_patterns)

    print("Building connection grammar...")
    grammar = build_connection_grammar(patterns, profiles)
    print(f"  {len(grammar['production_rules'])} production rules")
    print(f"  {len(grammar['anti_patterns'])} anti-patterns")
    write_connection_grammar_md(grammar, profiles)

    print("Updating patch files with archetype tags...")
    updated = update_patch_files(decoded, classifications)
    print(f"  Updated {updated} patch files")

    print(f"\nDone. Files written to {REF_DIR}/")


if __name__ == "__main__":
    main()
