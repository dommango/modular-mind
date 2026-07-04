"""Structural validator for generated .vcv patch files.

Checks for common mistakes that cause silence or errors:
  - Every ADSR/envelope has a gate source
  - Audio chain reaches AudioInterface (via signal-flow tracing)
  - Sound sources (VCO/Noise) are actually connected to the output
  - Envelopes modulate audio amplitude (not just sit idle)
  - No cables reference nonexistent modules or ports
  - Port IDs are within known valid ranges
  - Every VCA has a CV source or is bypassed (drone)
"""

import json
import sys
from pathlib import Path

# Ground truth port counts from C++ source enums
PORT_COUNTS = {
    ("Fundamental", "VCO"): {"inputs": 4, "outputs": 4},
    ("Fundamental", "VCF"): {"inputs": 4, "outputs": 2},
    ("Fundamental", "VCA-1"): {"inputs": 2, "outputs": 1},
    ("Fundamental", "ADSR"): {"inputs": 6, "outputs": 1},
    ("Fundamental", "LFO"): {"inputs": 5, "outputs": 4},
    ("Fundamental", "SEQ3"): {"inputs": 5, "outputs": 16},
    ("Fundamental", "Quantizer"): {"inputs": 1, "outputs": 1},
    ("Fundamental", "VCMixer"): {"inputs": 9, "outputs": 5},
    ("Fundamental", "Delay"): {"inputs": 6, "outputs": 2},
    ("Fundamental", "Scope"): {"inputs": 3, "outputs": 2},
    ("Fundamental", "Random"): {"inputs": 6, "outputs": 5},
    ("Fundamental", "Noise"): {"inputs": 0, "outputs": 7},
    ("Fundamental", "Octave"): {"inputs": 2, "outputs": 1},
    ("Core", "AudioInterface"): {"inputs": 8, "outputs": 8},
    ("Core", "AudioInterface2"): {"inputs": 2, "outputs": 2},
}

# Modules and output ports that produce audio signals
AUDIO_SOURCES = {
    "VCO": {0, 1, 2, 3},       # sine, tri, saw, square
    "VCO2": {0},               # wavetable
    "Noise": {0, 1, 2, 3, 4, 5, 6},  # white, pink, red, violet, blue, gray, black
}

# For each module: input_port -> {output_ports} that audio can reach
AUDIO_ROUTING = {
    "VCA-1": {1: {0}},         # audio in(1) -> out(0)
    "VCF": {3: {0, 1}},        # audio in(3) -> lpf(0), hpf(1)
    "VCMixer": {
        1: {0, 1},             # ch1 audio in -> mix(0), ch1 out(1)
        2: {0, 2},             # ch2 audio in -> mix(0), ch2 out(2)
        3: {0, 3},             # ch3 audio in -> mix(0), ch3 out(3)
        4: {0, 4},             # ch4 audio in -> mix(0), ch4 out(4)
    },
    "Delay": {4: {0, 1}},      # audio in(4) -> mix out(0), wet out(1)
    "Scope": {0: {0}, 1: {1}}, # ch1 in->ch1 out, ch2 in->ch2 out
    "AudioInterface": {},      # sink
    "AudioInterface2": {},     # sink
}

# Modules whose outputs are CV/modulation (never audio)
CV_ONLY_MODULES = {"ADSR", "LFO", "SEQ3", "Quantizer", "Random", "Octave"}

ENVELOPE_MODULES = {"ADSR"}
ENVELOPE_GATE_INPUT = {
    "ADSR": 4,
}

AUDIO_OUTPUT_MODULES = {"AudioInterface", "AudioInterface2"}

VCA_MODULES = {"VCA-1"}
VCA_CV_INPUT = {"VCA-1": 0}
VCA_AUDIO_INPUT = {"VCA-1": 1}


class SignalTracer:
    """Trace audio signal flow from sources to sinks through the cable graph."""

    def __init__(self, modules, cables):
        self.modules = modules
        # Build adjacency: (module_id, output_port) -> [(target_module_id, target_port), ...]
        self.outgoing = {}
        for c in cables:
            key = (c.get("outputModuleId"), c.get("outputId"))
            self.outgoing.setdefault(key, []).append(
                (c.get("inputModuleId"), c.get("inputId"))
            )

    def _get_param(self, mid, param_id, default=0.0):
        m = self.modules.get(mid)
        if not m:
            return default
        for p in m.get("params", []):
            if isinstance(p, dict) and p.get("id") == param_id:
                return p.get("value", default)
        return default

    def trace_audio(self):
        """
        Returns:
          reachable_outputs: set of (module_id, output_port) carrying audio
          reaches_interface: bool — does any audio reach AudioInterface?
          source_to_interface: list of (source_module_id, source_port, path)
          idle_sources: list of (source_module_id, source_port) that never reach output
        """
        # Build list of all audio source outputs
        source_outputs = []
        for mid, m in self.modules.items():
            model = m.get("model", "")
            ports = AUDIO_SOURCES.get(model, set())
            for port in ports:
                source_outputs.append((mid, port))

        reached_sources = set()
        source_to_interface = []

        # Trace independently from each source so shared modules don't hide paths
        for src_mid, src_port in source_outputs:
            visited = set()
            stack = [(src_mid, src_port)]
            visited.add((src_mid, src_port))
            found = False

            while stack and not found:
                mid, out_port = stack.pop()

                for next_mid, next_in_port in self.outgoing.get((mid, out_port), []):
                    next_m = self.modules.get(next_mid)
                    if not next_m:
                        continue
                    next_model = next_m.get("model", "")

                    # Reached audio interface?
                    if next_model in AUDIO_OUTPUT_MODULES:
                        reached_sources.add((src_mid, src_port))
                        source_to_interface.append((
                            src_mid, src_port,
                            [(src_mid, src_port), (next_mid, next_in_port)]
                        ))
                        found = True
                        break

                    # Check blockers
                    if next_model == "VCA-1":
                        has_cv = False
                        for ckey, targets in self.outgoing.items():
                            for tmid, tport in targets:
                                if tmid == next_mid and tport == 0:
                                    has_cv = True
                                    break
                            if has_cv:
                                break
                        if not has_cv:
                            level = self._get_param(next_mid, 0, 1.0)
                            if level <= 0.0:
                                continue

                    # Route through module
                    routing = AUDIO_ROUTING.get(next_model, {})
                    if next_in_port in routing:
                        for next_out_port in routing[next_in_port]:
                            next_key = (next_mid, next_out_port)
                            if next_key not in visited:
                                visited.add(next_key)
                                stack.append(next_key)
                    # else: signal absorbed

        idle_sources = [s for s in source_outputs if s not in reached_sources]
        return set(), bool(reached_sources), source_to_interface, idle_sources

    def trace_cv(self, source_model, source_port):
        """Trace a CV signal from a specific module output to see where it lands."""
        stack = []
        visited = set()
        destinations = []

        for mid, m in self.modules.items():
            if m.get("model") == source_model:
                key = (mid, source_port)
                if key not in visited:
                    visited.add(key)
                    stack.append(key)

        while stack:
            key = stack.pop()
            for next_mid, next_in_port in self.outgoing.get(key, []):
                next_m = self.modules.get(next_mid)
                if not next_m:
                    continue
                next_model = next_m.get("model", "")
                destinations.append((next_mid, next_model, next_in_port))

                # CV signals don't pass through audio modules in a useful way
                # But they can pass through modules like Quantizer, Octave
                if next_model in ("Quantizer", "Octave"):
                    routing = AUDIO_ROUTING.get(next_model, {})
                    if next_in_port in routing:
                        for next_out_port in routing[next_in_port]:
                            next_key = (next_mid, next_out_port)
                            if next_key not in visited:
                                visited.add(next_key)
                                stack.append(next_key)

        return destinations


class PatchValidator:
    def __init__(self, patch_data):
        self.patch = patch_data
        self.modules = {m["id"]: m for m in patch_data.get("modules", [])}
        self.cables = patch_data.get("cables", patch_data.get("wires", []))
        self.errors = []
        self.warnings = []

    def error(self, msg):
        self.errors.append(f"ERROR: {msg}")

    def warn(self, msg):
        self.warnings.append(f"WARN: {msg}")

    def validate(self):
        self._check_has_audio_output()
        self._check_signal_flow()
        self._check_envelopes_have_gates()
        self._check_vcas_have_cv()
        self._check_vcmixer_port_usage()
        self._check_cable_references()
        self._check_port_ranges()
        self._check_audible_frequencies()
        return len(self.errors) == 0

    def _check_has_audio_output(self):
        has_audio = any(
            m.get("model") in AUDIO_OUTPUT_MODULES
            for m in self.modules.values()
        )
        if not has_audio:
            self.error("No AudioInterface module — patch has no audio output")

    def _check_signal_flow(self):
        tracer = SignalTracer(self.modules, self.cables)
        reachable, reaches_interface, paths, idle_sources = tracer.trace_audio()

        # 1. Is there any sound source at all?
        has_source = bool(idle_sources or paths)
        if not has_source:
            self.error("No sound source (VCO/Noise) in patch — will be silent")

        # 2. Does any sound source actually reach the audio interface?
        if has_source and not reaches_interface:
            self.error("Sound source exists but no audio path reaches AudioInterface — will be silent")

        # 3. Report idle sources — only warn if a source module has ZERO outputs
        # reaching the interface (not just individual unused outputs)
        source_models = {}
        for mid, port in idle_sources:
            source_models.setdefault(mid, []).append(port)

        for mid, ports in source_models.items():
            m = self.modules.get(mid)
            model = m.get("model", "?") if m else "?"
            all_ports = AUDIO_SOURCES.get(model, set())
            if len(ports) == len(all_ports):
                self.warn(
                    f"{model} (id={mid}) has {len(ports)} output(s) and NONE reach "
                    f"AudioInterface — module is silent"
                )

        # 4. Check if ADSR envelopes actually modulate audio amplitude
        self._check_envelope_modulates_audio(tracer)

    def _check_envelope_modulates_audio(self, tracer):
        """Verify ADSR outputs reach a VCA CV or VCMixer channel CV input."""
        for mid, m in self.modules.items():
            if m.get("model") != "ADSR":
                continue

            # Check if ADSR has a gate
            has_gate = any(
                c.get("inputModuleId") == mid and c.get("inputId") == 4
                for c in self.cables
            )
            if not has_gate:
                continue  # Already caught by _check_envelopes_have_gates

            # Trace ADSR output (port 0) to see where it goes
            adsr_destinations = tracer.trace_cv("ADSR", 0)

            modulates_audio = False
            for dest_mid, dest_model, dest_port in adsr_destinations:
                if dest_model == "VCA-1" and dest_port == 0:
                    modulates_audio = True
                    # Exponential response on a VCA makes envelopes very quiet
                    vca = self.modules.get(dest_mid, {})
                    vca_params = {p.get("id"): p.get("value", 0) for p in vca.get("params", []) if isinstance(p, dict)}
                    if vca_params.get(1, 1.0) < 0.5:
                        self.warn(
                            f"ADSR (id={mid}) controls VCA-1 (id={dest_mid}) with exponential "
                            f"response — sustain levels will be much quieter than expected"
                        )
                elif dest_model == "VCMixer" and dest_port in {5, 6, 7, 8}:
                    # VCMixer CV inputs: 5=ch1 CV, 6=ch2 CV, 7=ch3 CV, 8=ch4 CV
                    modulates_audio = True

            if not modulates_audio:
                self.warn(
                    f"ADSR (id={mid}) is triggered but its envelope output never "
                    f"modulates a VCA or mixer channel CV — amplitude is static"
                )

    def _check_vcmixer_port_usage(self):
        """Warn if audio signals are patched into VCMixer CV inputs (common port-map mistake)."""
        vcmixer_ids = {mid for mid, m in self.modules.items() if m.get("model") == "VCMixer"}
        if not vcmixer_ids:
            return

        for c in self.cables:
            in_mod = c.get("inputModuleId")
            in_port = c.get("inputId")
            if in_mod in vcmixer_ids and in_port in {5, 6, 7, 8}:
                out_mod = c.get("outputModuleId")
                out_m = self.modules.get(out_mod, {})
                out_model = out_m.get("model", "?")
                if out_model in AUDIO_SOURCES or out_model in ("Noise",):
                    self.warn(
                        f"Audio source {out_model} (id={out_mod}) patched to VCMixer "
                        f"CV input {in_port - 4} — should be a channel audio input (1-4)"
                    )

    def _check_envelopes_have_gates(self):
        for mid, m in self.modules.items():
            model = m.get("model", "")
            if model not in ENVELOPE_MODULES:
                continue

            gate_port = ENVELOPE_GATE_INPUT.get(model)
            if gate_port is None:
                continue

            has_gate = any(
                c.get("inputModuleId") == mid and c.get("inputId") == gate_port
                for c in self.cables
            )
            if not has_gate:
                self.error(f"{model} (id={mid}) has no gate input — envelope will never trigger (need cable to input {gate_port})")

    def _check_vcas_have_cv(self):
        for mid, m in self.modules.items():
            model = m.get("model", "")
            if model not in VCA_MODULES:
                continue

            cv_port = VCA_CV_INPUT.get(model)
            audio_port = VCA_AUDIO_INPUT.get(model)

            has_cv = any(
                c.get("inputModuleId") == mid and c.get("inputId") == cv_port
                for c in self.cables
            )
            has_audio = any(
                c.get("inputModuleId") == mid and c.get("inputId") == audio_port
                for c in self.cables
            )

            if has_audio and not has_cv:
                self.warn(f"VCA-1 (id={mid}) has audio input but no CV — will be silent unless Level param > 0")

    def _check_cable_references(self):
        for i, c in enumerate(self.cables):
            out_mod = c.get("outputModuleId")
            in_mod = c.get("inputModuleId")

            if out_mod not in self.modules:
                self.error(f"Cable {i}: outputModuleId={out_mod} doesn't exist")
            if in_mod not in self.modules:
                self.error(f"Cable {i}: inputModuleId={in_mod} doesn't exist")

    def _check_port_ranges(self):
        for i, c in enumerate(self.cables):
            out_mod = c.get("outputModuleId")
            in_mod = c.get("inputModuleId")
            out_port = c.get("outputId")
            in_port = c.get("inputId")

            if out_mod in self.modules:
                m = self.modules[out_mod]
                key = (m.get("plugin", ""), m.get("model", ""))
                if key in PORT_COUNTS:
                    max_out = PORT_COUNTS[key]["outputs"]
                    if out_port >= max_out:
                        self.error(f"Cable {i}: {key[1]} output port {out_port} >= max {max_out}")

            if in_mod in self.modules:
                m = self.modules[in_mod]
                key = (m.get("plugin", ""), m.get("model", ""))
                if key in PORT_COUNTS:
                    max_in = PORT_COUNTS[key]["inputs"]
                    if in_port >= max_in:
                        self.error(f"Cable {i}: {key[1]} input port {in_port} >= max {max_in}")

    def _check_audible_frequencies(self):
        for mid, m in self.modules.items():
            model = m.get("model", "")
            params = {p.get("id"): p.get("value", 0) for p in m.get("params", []) if isinstance(p, dict)}

            if model in ("VCO", "VCO2"):
                freq_param = params.get(2, 0.0)
                freq_hz = 261.63 * (2 ** (freq_param / 12.0))
                if freq_hz < 20:
                    self.error(f"VCO (id={mid}) frequency {freq_hz:.1f} Hz is below human hearing (param={freq_param})")
                elif freq_hz > 20000:
                    self.error(f"VCO (id={mid}) frequency {freq_hz:.0f} Hz is above human hearing (param={freq_param})")

            if model == "VCF":
                cutoff_param = params.get(0, 0.5)
                cutoff_hz = 261.63 * (2 ** (10 * cutoff_param - 5))
                if cutoff_hz < 80:
                    self.warn(f"VCF (id={mid}) cutoff {cutoff_hz:.0f} Hz may filter out all audible content (param={cutoff_param:.2f})")

    def report(self):
        lines = []
        for e in self.errors:
            lines.append(f"  {e}")
        for w in self.warnings:
            lines.append(f"  {w}")
        return "\n".join(lines)


def validate_file(path):
    data = json.loads(Path(path).read_text())
    v = PatchValidator(data)
    passed = v.validate()
    return v, passed


def validate_directory(directory):
    directory = Path(directory)
    files = sorted(directory.glob("*.vcv"))
    if not files:
        print(f"No .vcv files found in {directory}")
        return

    total = passed = failed = 0
    for f in files:
        try:
            data = json.loads(f.read_text())
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue

        v = PatchValidator(data)
        ok = v.validate()
        total += 1

        status = "PASS" if ok else "FAIL"
        if not ok:
            failed += 1
        else:
            passed += 1

        print(f"[{status}] {f.name}")
        if v.errors or v.warnings:
            print(v.report())

    print(f"\nResults: {passed}/{total} passed, {failed} failed")


def main():
    if len(sys.argv) < 2:
        print("Usage: python validate_patch.py <file.vcv | directory>")
        sys.exit(1)

    target = Path(sys.argv[1])
    if target.is_dir():
        validate_directory(target)
    elif target.is_file():
        v, passed = validate_file(target)
        print(f"[{'PASS' if passed else 'FAIL'}] {target.name}")
        if v.errors or v.warnings:
            print(v.report())
    else:
        print(f"Not found: {target}")
        sys.exit(1)


if __name__ == "__main__":
    main()
