# 0008. Content-aware sorting with an on-device model only

- **Status:** Accepted
- **Date:** 2026-10-25
- **Related:** `docs/specs/2026-10-25-mac-organizer-v2-design.md`, 0009, 0010

## Context

v1 sorted by extension, so every document landed in one Inbox regardless of
what it was. The owner wanted documents read and placed in the matching
folder ("it should read the document and place it in a corresponding
folder"). Reading documents means handling coursework, CVs, finance and
personal papers.

Asked about a cloud API, the owner said: **"no i am not, that is too much
data"**. They already had local open-weight models on disk, downloaded by
a desktop chat app that offers no API.

## Decision

Content classification runs **entirely on the Mac**, using a local GGUF
model through llama.cpp. No file content, filename or sample is ever sent
over the network. The model is configurable; the shipped default is any
instruction-tuned GGUF chat model the user points at with
`python3 setup.py --model PATH`. The model file is used in place, not
copied.

A short text sample is extracted per item: the first ~1,500 words of a PDF
(`pdftotext`), text from inside docx/pptx/xlsx, `textutil` for doc/rtf, the
first ~150 lines of code or notebooks, up to 200 member names of an archive
(never extracted), and for folders up to 200 paths plus up to 3 documents
inside. Samples are capped at 12,000 characters. Scanned PDFs are not OCR'd;
they are judged by name.

## Consequences

- The privacy promise is simple and checkable: the only socket is
  `127.0.0.1`, and requests bypass any system proxy.
- Quality is limited by a small local model; this is why the sorter only
  chooses between existing folders (0010) and has a safe fallback (0017).
- Each user must download a model (a few GB) once; the README walks them
  through it.
- Sorting a batch takes seconds to load the model, not milliseconds.

## Alternatives considered

- **A cloud LLM API:** rejected by the owner on privacy grounds.
- **The desktop chat app's model runner:** no API to call.
- **Keyword rules only:** cheap, but cannot tell one course's PDFs from
  another's; kept as the instant first step (name rules) before the model.
