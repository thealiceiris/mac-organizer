# Setup and smart-sort implementation

## Apple Intelligence

1. `python3 setup.py --apple-intelligence` compiles `apple_classify.swift`,
   probes it with `--check`, and only then selects `backend = "apple"` in
   git-ignored `rules.local.toml`. Failed setup preserves existing selection.
2. Plain `python3 setup.py` creates folders/rules without starting agents.
3. `route.load_config` validates the backend; `SmartRouter` selects the matching
   classifier unless a test supplies a factory. Apple needs no `model_path`.
4. `classify.AppleIntelligence` probes availability on entry and passes the
   existing prompt and correction examples to the helper over stdin. Document
   samples are capped at 4,000 characters for Apple's smaller context window.
5. Swift uses `@Generable` output and the default on-device model. Python's
   existing `parse_verdict` checks candidate membership and confidence.
6. Helper exit 2/abnormal exits, missing executables, and timeouts are outages;
   exit 3 (generation refusal/error, including context overflow) rejects only
   the current item. Existing fallback/backoff logic remains in charge.

`setup.py --model <file.gguf>` switches back to llama.cpp. Rebuild the Swift
helper after changing its source. Generated `.build/` and personal TOML files
are git-ignored.

## Tests

```bash
.venv/bin/python -m pytest -q
MAC_ORGANIZER_APPLE_LIVE=1 .venv/bin/python -m pytest tests/test_apple_classify.py -q
```

The opt-in live test uses synthetic text, not personal documents. Default
tests cover protocol, output validation, error handling, configuration, and
failed setup without requiring Apple hardware.
