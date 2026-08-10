# Friend-PC prerequisites fix

This friend build now contains `INSTALL_PREREQUISITES.cmd` and also performs the same lightweight prerequisite check automatically when `RUN_FIFA14_LOCAL_BETA.cmd` starts.

It checks/installs:

- Python 3.10+ (the automatic installer targets Python 3.13)
- Git for Windows, which supplies the `openssl.exe` required by the FIFA 14 old-ProtoSSL certificate generator
- the build-local `.venv` plus `requirements.txt` (`frida` and `cryptography`)

The immediate friend-PC crash fixed here was:

`RuntimeError: SHA-1 certificate generation needs OpenSSL.`

Normal use: extract the ZIP, run `RUN_FIFA14_LOCAL_BETA.cmd` as Administrator, and let the prerequisite preflight install anything missing. For an explicit setup pass first, run `INSTALL_PREREQUISITES.cmd` as Administrator.

The prerequisite downloader uses WinGet when available. If WinGet cannot be used, it falls back to official Python.org and Git-for-Windows GitHub downloads. The pinned Python fallback is SHA-256 verified before execution; GitHub's release digest is verified when supplied by the API.
