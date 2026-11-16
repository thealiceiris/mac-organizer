# Architecture map

This is a focused map of setup and smart sorting; detailed historical design
decisions live in [adr/README.md](adr/README.md).

- `setup.py`: creates folders and local rules; writes agents only with `--agents`.
- `install.sh`: checks optional tools, runs setup, and loads launchd agents.
- `route.py`: parses layered TOML, applies routing rules, and owns `SmartRouter`.
- `classify.py`: enumerates existing candidate folders, builds prompts, validates
  verdicts, and implements local inference backends.
- `apple_classify.swift`: Foundation Models bridge compiled to
  `.build/apple-classify`. Reads JSON over stdin, returns a guided JSON decision.
- `memory.py`: verdict caching, failure counters, and correction examples.
- `extract.py`: local document text sampling.
- `backlog.py`: reviewable planning and explicit apply; planning moves nothing.

## Inference backends

`[smart_sort] backend` selects `llama.cpp` (default) or `apple`.
The former runs a loopback-only server per batch and requires a GGUF model;
the latter uses Apple's on-device `SystemLanguageModel.default`, with one
short-lived helper process per request. Neither sends documents off-device.
Python validates the returned candidate name and confidence for both backends.

Apple setup requires Apple Silicon, macOS 26+, Command Line Tools, and an
available Apple Intelligence model. No additional Python runtime dependency
is introduced.
