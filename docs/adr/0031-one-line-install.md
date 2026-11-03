# 0031. A one-line install that keeps the questions

- **Status:** Accepted
- **Date:** 2026-11-03
- **Related:** 0006, 0019, 0023
- **Number:** 0026-0030 are taken on the unmerged `adr2` branch.

## Context

Installing took three separate steps in the Terminal (clone, `cd`,
`./install.sh`), and a Python older than 3.11 only showed up as an error at
the end. The owner wanted one line to paste, for the README and a website.

The common pattern, `curl ... | bash`, is unsafe here. It gives `install.sh` a
pipe instead of the terminal, so its "Load the background sorter now?"
question is skipped and the sorter starts moving files on a busy Mac without
asking, which breaks 0019 (nothing starts without a yes).

## Decision

- Add `get.sh` and document the Homebrew-style line, which keeps the terminal:
  `/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/thealiceiris/mac-organizer/main/get.sh)"`
- `get.sh` refuses to run without a terminal (so a piped run fails loudly
  rather than skipping questions), unless `MAC_ORGANIZER_ASSUME_YES=1`.
- It finds Python 3.11+, also in the Homebrew folders when they are not on
  `PATH` yet, and only runs `brew install python` after a yes.
- It clones into `~/.mac-organizer` or fast-forwards an existing checkout, so
  the same line installs and updates. A folder at that path that is not a
  checkout is never touched.
- It then hands over to `install.sh`, unchanged, with that Python first on
  `PATH`.
- The README has people download the AI model before the one-liner, because
  once the sorter runs a finished `.gguf` in Downloads gets sorted into Misc.

## Consequences

- One line installs and updates; the existing questions still appear.
- The line downloads whatever is on `main` on GitHub, so `main` must be pushed
  before the README line installs the current version.
- The optional tools (llama.cpp, poppler, terminal-notifier) are still a
  separate `brew install`; `install.sh` keeps warning about them.

## Alternatives considered

- `curl ... | bash`: skips the safety question (see Context).
- Reading answers from `/dev/tty` inside `install.sh`: makes `curl | bash`
  work but changes `install.sh` and its tests; the Homebrew form needs neither.
- A Homebrew tap or a `.pkg`: friendlier, but needs signing or a second repo to
  maintain. Revisit if there are many users.
