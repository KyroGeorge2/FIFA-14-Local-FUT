# v2.41.1 BETA 2.25.1

- Hotfixes the Windows PowerShell launch failure at `run_fifa14_local_beta.ps1` market verification.
- Wraps install, pack-performance and transfer-market Python verifiers with a temporary `ErrorActionPreference = Continue`.
- Gates verifier success/failure on the actual Python exit code rather than PowerShell's `NativeCommandError` wrapper for redirected stderr.
- Prints the complete verifier output if Python genuinely exits non-zero.
- Leaves the BETA 2.25.0 gameplay payload unchanged: GK/outfield card fix, 30-slot Transfer List, full player market, old-era pricing curve, and lenient max-two-special pack weights are preserved.
