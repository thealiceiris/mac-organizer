# 0003. Python standard library only

- **Status:** Accepted
- **Date:** 2026-09-07
- **Related:** 0002, 0009

## Context

The router is started by launchd with no shell profile, no virtualenv and no
PATH set up by the user. Anything it imports must simply be there. The tool
should also be easy for friends to install with one `git clone`.

## Decision

Write everything in Python 3 using only the standard library at runtime
(`tomllib` for config needs 3.11+). The target is Homebrew `python3`
(3.14 at the time of writing). `install.sh` refuses to continue on an
interpreter older than 3.11, because `/usr/bin/python3` on a stock Mac is 3.9
and would fail silently on every launchd fire.

External capabilities are called as subprocesses of tools the user installs
with Homebrew, never as Python packages: `llama-server` (llama.cpp),
`pdftotext` (poppler), `terminal-notifier`, plus macOS's own `textutil`,
`hdiutil`, `mdfind`, `osascript` and `osacompile`. `install.sh` checks for
the Homebrew tools and prints the `brew install` line; it never installs
them itself. pytest is a development-only dependency.

## Consequences

- No `pip install`, no dependency drift, nothing to break on a Python upgrade
  beyond the interpreter path.
- Some things are hand-rolled: the HTTP client for llama-server (`urllib`
  with proxies bypassed), office-file text extraction (`zipfile` + XML), and
  the macOS rename-without-replace call (`renamex_np` through `ctypes`).
- Features that need a missing tool degrade instead of failing: no
  `pdftotext` means PDFs are judged by name; no `terminal-notifier` means
  plain `osascript` notifications.

## Alternatives considered

- **PyPI packages** (watchdog, pdfminer, an LLM client): nicer APIs, but a
  virtualenv that launchd must find and that breaks on Python upgrades.
- **A compiled binary (Swift/Go):** better native integration, but a build
  step for every change and harder for friends to read or tweak.
