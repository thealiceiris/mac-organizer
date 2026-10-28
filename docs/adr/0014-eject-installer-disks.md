# 0014. Eject installer disk images, safely

- **Status:** Accepted
- **Date:** 2026-10-25
- **Related:** `disks.py`

## Context

Opening a `.dmg` mounts it and puts an icon on the Desktop; the owner wanted
those installer disks cleared away. Ejecting the wrong thing (a USB drive, a
Time Machine disk, a disk still in use) would be harmful.

## Decision

On each sweep, list mounts with `hdiutil info` and consider **only disk
images** whose image file is a `.dmg` under `$HOME`; never USB, external, SD,
Time Machine, Recovery or simulator images. Eject one only after it has been
mounted for **15 minutes** (the builder's call), with a plain
`hdiutil detach`, **never `-force`**. If macOS says it is busy, try again next
sweep. Ejects are logged and silent. The `.dmg` file itself is untouched; it
is routed to `~/Downloads/Installers` like any installer.

## Consequences

- Installer icons disappear on their own, with no risk to real drives.
- An installer still running keeps its disk; the next sweep retries.

## Alternatives considered

- **Eject immediately:** would interrupt installs.
- **Force eject:** could corrupt an install in progress.
