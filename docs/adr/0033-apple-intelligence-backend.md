# 0033. Optional Apple Intelligence on-device backend

- **Status:** Accepted
- **Date:** 2026-11-16
- **Related:** 0008, 0009, 0010, `classify.py`, `apple_classify.swift`

## Context

Compatible Macs already have Apple's on-device language model. Downloading a
separate multi-gigabyte GGUF model should not be necessary for document filing.
The Python sorter cannot call Swift's Foundation Models framework directly.

## Decision

Add an explicitly selected `apple` backend alongside the unchanged default
`llama.cpp` backend (amending 0009's engine choice). Build a small Swift helper
with Command Line Tools, verify model availability before changing config,
and exchange JSON through stdin/stdout. Use only `SystemLanguageModel.default`
and guided generation, with existing Python candidate/confidence validation.
Treat model unavailability as an outage and generation refusals/context errors
as item-specific rejections. Keep all existing move safety checks.

## Consequences

No separate model download or server is needed on supported Macs. Apple
Intelligence must be enabled and available. Samples use a smaller character
budget, and the helper must be rebuilt after source changes. Non-Apple setups
retain llama.cpp. The Python runtime remains standard-library only.

## Alternatives considered

- Keep only llama.cpp: portable but requires another model download.
- Cloud inference: violates the existing local-only privacy boundary.
- UI automation of Writing Tools: fragile and unsuitable for background use.
