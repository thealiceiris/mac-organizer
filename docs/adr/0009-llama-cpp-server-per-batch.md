# 0009. llama.cpp server started per batch, with constrained output

- **Status:** Accepted
- **Date:** 2026-10-25
- **Related:** 0008, `classify.py`

## Context

The default model is about 5 GB and the owner's Mac has 16 GB of RAM. An
anti-goal of v2 was "the model sitting in memory all day". The model's
answer also has to be machine-readable every time, and a confident wrong
guess ("misfiling into the wrong course") is worse than no guess.

## Decision

- **Engine:** Homebrew `llama-server`, bound to `127.0.0.1` only, started
  with `--offline --no-webui --reasoning off` and an 8,192-token context.
- **Lifecycle:** the server starts only when a batch contains at least one
  item that needs the model, serves the whole batch, and is stopped when the
  batch ends. A pidfile lets the next run clean up a server left behind by a
  crash.
- **Output:** constrained with a JSON schema to
  `{"folder": <one of the candidates> | "unsure", "confidence": 0–1,
  "reason": "...", "topic": "..."}`. Below the confidence threshold
  (default 0.6) the answer counts as unsure.
- **Failures:** a missing model or a server that won't start sends items to
  the fallback (0017), logs the error, and the notification says
  "AI sorting unavailable". A failure counter stops an item that keeps
  crashing the server, or a model that never starts, from being retried on
  every sweep.

## Consequences

- No memory cost outside sorting batches; a batch costs a model load.
- The model can only name a folder from the list; it cannot invent paths.
- "Unsure" is a first-class answer, which makes the safe fallback possible.

## Alternatives considered

- **Keep the server running:** faster per item, but 5 GB resident all day.
- **`llama-cli` per item:** a full model load per file.
- **Free-text answers parsed with regexes:** brittle; constrained JSON is
  supported natively by llama.cpp.
