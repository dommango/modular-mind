"""Step 3: Parse raw .vcv patches and filter to verified free modules only.

VCV file formats:
  - Plain JSON (Rack v0.x/v1): file starts with '{'
  - Zstd-compressed tar (Rack v2): contains ./patch.json

License tiers:
  - open_source: all modules are in free_plugins.json
  - freeware: at least one module is in freeware_plugins.json (none rejected)
  - rejected: at least one module is proprietary/paid or unverifiable

A patch's tier = its most restrictive module.
"""

import ctypes
import ctypes.util
import io
import json
import tarfile

from config import RAW_DIR, OUTPUT_DIR, WHITELIST_DIR


ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"


def _load_libzstd():
    path = ctypes.util.find_library("zstd")
    if not path:
        raise RuntimeError("libzstd not found — install libzstd1")
    lib = ctypes.CDLL(path)
    lib.ZSTD_decompress.argtypes = [
        ctypes.c_void_p, ctypes.c_size_t,
        ctypes.c_void_p, ctypes.c_size_t,
    ]
    lib.ZSTD_decompress.restype = ctypes.c_size_t
    lib.ZSTD_isError.argtypes = [ctypes.c_size_t]
    lib.ZSTD_isError.restype = ctypes.c_uint
    return lib


_libzstd = None


def _get_libzstd():
    global _libzstd
    if _libzstd is None:
        _libzstd = _load_libzstd()
    return _libzstd


def decompress_zstd(data):
    lib = _get_libzstd()
    src = (ctypes.c_char * len(data))(*data)
    for multiplier in (50, 200, 1000):
        dst_size = max(len(data) * multiplier, 1024 * 1024)
        dst = (ctypes.c_char * dst_size)()
        result = lib.ZSTD_decompress(dst, dst_size, src, len(data))
        if not lib.ZSTD_isError(result):
            return bytes(dst[:result])
    raise RuntimeError(f"zstd decompression failed after retries (code {result})")


def parse_vcv(file_path):
    raw = file_path.read_bytes()

    if raw[:4] == ZSTD_MAGIC:
        tar_bytes = decompress_zstd(raw)
        tf = tarfile.open(fileobj=io.BytesIO(tar_bytes))
        for member in tf.getmembers():
            if member.name.endswith("/patch.json") or member.name == "patch.json":
                content = tf.extractfile(member).read()
                return json.loads(content)
        raise ValueError(f"No patch.json in tar: {file_path}")

    return json.loads(raw)


def extract_modules(patch_data):
    modules = []
    for m in patch_data.get("modules", []):
        plugin = m.get("plugin", "")
        model = m.get("model", "")
        if plugin and model:
            modules.append({"plugin": plugin, "model": model})
    return modules


def load_whitelist():
    free_plugins = json.loads(
        (WHITELIST_DIR / "free_plugins.json").read_text()
    )
    freeware_plugins = json.loads(
        (WHITELIST_DIR / "freeware_plugins.json").read_text()
    )
    empty_manifests = json.loads(
        (WHITELIST_DIR / "empty_manifests.json").read_text()
    )
    return free_plugins, freeware_plugins, set(empty_manifests)


def classify_module(plugin, model, free_plugins, freeware_plugins, empty_slugs):
    if plugin in empty_slugs:
        return None
    if plugin in free_plugins and model in free_plugins[plugin]:
        return "open_source"
    if plugin in freeware_plugins and model in freeware_plugins[plugin]:
        return "freeware"
    return None


def check_patch(modules, free_plugins, freeware_plugins, empty_slugs):
    tier = "open_source"
    for m in modules:
        classification = classify_module(
            m["plugin"], m["model"],
            free_plugins, freeware_plugins, empty_slugs,
        )
        if classification is None:
            return None, m["plugin"], m["model"]
        if classification == "freeware":
            tier = "freeware"
    return tier, None, None


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    free_plugins, freeware_plugins, empty_slugs = load_whitelist()

    manifest = json.loads((RAW_DIR / "manifest.json").read_text())
    downloaded = {
        pid: entry for pid, entry in manifest.items()
        if entry["status"] == "downloaded"
    }
    print(f"Patches to parse: {len(downloaded)}")

    accepted = []
    accepted_open = 0
    accepted_freeware = 0
    rejected_paid = 0
    rejected_empty = 0
    rejected_unknown = 0
    parse_errors = 0
    reject_reasons = {}

    for i, (pid, entry) in enumerate(sorted(downloaded.items()), 1):
        vcv_path = RAW_DIR / f"{pid}.vcv"
        if not vcv_path.exists():
            parse_errors += 1
            continue

        try:
            patch_data = parse_vcv(vcv_path)
            modules = extract_modules(patch_data)
        except Exception as e:
            parse_errors += 1
            if parse_errors <= 5:
                print(f"  Parse error {pid}: {e}")
            continue

        if not modules:
            rejected_empty += 1
            continue

        tier, bad_plugin, bad_model = check_patch(
            modules, free_plugins, freeware_plugins, empty_slugs,
        )

        if tier is not None:
            accepted.append({
                "id": int(pid),
                "license_tier": tier,
                "modules": modules,
                "module_count": len(modules),
                "version": patch_data.get("version", ""),
            })
            if tier == "open_source":
                accepted_open += 1
            else:
                accepted_freeware += 1
        else:
            if bad_plugin in empty_slugs:
                rejected_empty += 1
            elif bad_plugin not in free_plugins and bad_plugin not in freeware_plugins:
                rejected_paid += 1
            else:
                rejected_unknown += 1
            reject_reasons[bad_plugin] = reject_reasons.get(bad_plugin, 0) + 1

        if i % 500 == 0:
            print(f"  [{i}/{len(downloaded)}] accepted={len(accepted)}")

    out_path = OUTPUT_DIR / "filtered_patches.json"
    out_path.write_text(json.dumps(accepted, indent=2))

    top_rejects = sorted(reject_reasons.items(), key=lambda x: -x[1])[:10]

    print(f"\nSummary:")
    print(f"  Parsed:              {len(downloaded) - parse_errors}")
    print(f"  Parse errors:        {parse_errors}")
    print(f"  Accepted:            {len(accepted)}")
    print(f"    open_source:       {accepted_open}")
    print(f"    freeware:          {accepted_freeware}")
    print(f"  Rejected (paid):     {rejected_paid}")
    print(f"  Rejected (empty):    {rejected_empty}")
    print(f"  Rejected (unknown):  {rejected_unknown}")
    print(f"\nTop rejected plugins:")
    for plugin, count in top_rejects:
        print(f"  {plugin}: {count}")
    print(f"\nWritten to {out_path}")


if __name__ == "__main__":
    main()
