from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRACE = ROOT / "tools" / "frida_pc_fut_nav_route_patch_trace.py"
PROBE = ROOT / "server" / "probe.py"
WEIGHTS = ROOT / "server" / "pack-weights.v237.json"
EXPECTED_WEIGHTS_SHA256 = "907943853b35210d7fa3669cfda5d175aeb964e5a35c7ce0728fbcda83a29964"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def main() -> int:
    source = TRACE.read_text(encoding="utf-8")
    probe_source = PROBE.read_text(encoding="utf-8")
    checks = {
        "runtimePerformanceMode": "const RUNTIME_PERFORMANCE_MODE = true;" in source,
        "matchBridgeTelemetryDisabled": "if (!RUNTIME_PERFORMANCE_MODE) installMatchBridgeTrace(module, reason);" in source,
        "passiveStoreTelemetryDisabled": "if (!RUNTIME_PERFORMANCE_MODE) installPassiveStoreWrapperTrace(module);" in source,
        "storeNumberAndCreditTelemetryDisabled": (
            "if (!RUNTIME_PERFORMANCE_MODE) {\n    installStoreNumberArgTrace(module);\n    installUserCreditsParserTrace(module);\n  }" in source
        ),
        "stableStadiumSnapshot": "stadiumProviderLastSnapshotObject !== objectKey || !getter.isNull()" not in source,
        "bufferedTraceWriter": 'buffering=262144' in source and 'with output.open("a", encoding="utf-8") as handle:' not in source,
        "hotStoreBacktraceGuarded": "if (!RUNTIME_PERFORMANCE_MODE) {\n            emit('cards-store-local-wrapper-enter'" in source,
        "runtimeMarker": "cards-runtime-performance-mode-beta2244" in source,
        "genericResponseTraceDisabled": "cards-generic-response-tracing-disabled-beta2244" in source,
        "staticAssetTraceDisabled": "cards-static-asset-diagnostics-disabled-beta2244" in source and "fifa-static-asset-bridge-disabled-beta2244" in source,
        "moduleAndPluginTraceDisabled": "fifa-module-wrapper-diagnostics-disabled-beta2244" in source and "fifa-plugin-loader-diagnostics-disabled-beta2244" in source,
        "uiWriterWindowed": "recordUiWriterDepthByThread" in source and "badgeUiWriterDepthByThread" in source and "The setters discovered below are generic UI writer methods shared" in source,
        "navBacktraceFastPath": "const common = RUNTIME_PERFORMANCE_MODE ? {name:item.name, thread_id:tid()}" in source,
        "packPayloadLoggingSummarized": "never serialize the entire pack payload" in probe_source,
    }
    for name, ok in checks.items():
        require(ok, f"BETA 2.25.0 performance verifier failed: {name}")

    digest = hashlib.sha256(WEIGHTS.read_bytes()).hexdigest()
    require(digest == EXPECTED_WEIGHTS_SHA256, "Pack weights changed inside the performance-only hotfix")

    print(json.dumps({
        "status": "ok",
        "checks": checks,
        "packWeightsSha256": digest,
        "note": "BETA 2.25.0 removes the remaining pack-screen hot-path instrumentation and full pack-payload disk logging; pack weights remain unchanged for isolated FPS testing.",
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
