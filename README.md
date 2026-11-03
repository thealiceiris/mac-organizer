# mac-organizer

**Keeps your Mac tidy automatically.** Things you download or drop on your
Desktop are moved where they belong: pictures, music and videos into a
**Media** folder in your home folder, and documents into the matching folder in
**Documents**.

For documents, a small AI model reads each file **on your own Mac** and picks
the best folder. Nothing is ever uploaded anywhere.

Your files are never deleted or overwritten. Anything it isn't sure about
goes to a folder called **Documents/Misc**, so you can sort it yourself.

---

## What you need

- A Mac
- [Homebrew](https://brew.sh), a free tool for installing apps from the
  Terminal. When Homebrew finishes installing, it shows **"Next steps"** with
  two or three commands: copy and run those too, or the steps below won't work.
- About 5 GB of free space for the AI model (optional, but it's what makes
  document sorting smart)

---

## Install

Open the **Terminal** app (press ⌘ Space, type *Terminal*, press Enter). Copy
and paste each step, then press Enter.

**1. Install the helper tools**

```bash
brew install python llama.cpp poppler terminal-notifier
```

**2. Get an AI model** *(optional, recommended)*

The model is a single large file ending in `.gguf`. One that works well on most
Macs: go to [huggingface.co](https://huggingface.co), search for
**Qwen2.5-7B-Instruct-GGUF**, open the **Files** tab and download the file
whose name ends in **Q4_K_M.gguf** (about 4.7 GB). Any other "Instruct" GGUF
model of a similar size works too.

When the download has finished, move it out of Downloads (do this before step
3, or mac-organizer will tidy it away into Documents/Misc):

```bash
mkdir -p ~/models && mv ~/Downloads/*.gguf ~/models/
```

(Without a model, everything still works; documents just go to Documents/Misc.)

**3. Download mac-organizer and switch it on**

Paste this one line:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/thealiceiris/mac-organizer/main/get.sh)"
```

It may ask up to three questions. The answer is **No** unless you type `y`:

- **"Install Python with Homebrew now?"** Only if step 1 didn't install
  Python. Say yes.
- **"Load the background sorter now?"** This only appears if you already have
  lots of files lying around. Say **No** if you'd like to see what it would do
  first (see *Already have a messy Mac?* below).
- **"Your Documents folder is empty. Create starter folders…?"** Say yes if
  you'd like a few ready-made folders (Work, School, Finance, Health,
  Personal, Travel).

**4. Tell it where the AI model is** *(if you got one in step 2)*

Change the file name to match yours:

```bash
cd ~/.mac-organizer
python3 setup.py --model ~/models/Qwen2.5-7B-Instruct-Q4_K_M.gguf
```

It replies "AI model set".

**5. Say "Allow" when your Mac asks**

The first time things run, macOS may ask whether mac-organizer may **send
notifications**, and whether **Folder Suggestions** may use your **Documents**
folder. Click **Allow** for both.

---

## Is it working?

Download any PDF (or drag one into your Downloads folder). Within about a
minute it disappears from Downloads and shows up in a folder in Documents, or
in Documents/Misc. You'll see a notification when it sorts documents.

---

## Every day

You don't need to do anything. Within about a minute of a file landing in
Downloads or on your Desktop:

| What arrives | Where it goes |
| --- | --- |
| Photos and pictures | `Media/Images` |
| Music, voice notes | `Media/Audio` |
| Videos | `Media/Video` |
| Screenshots | `Media/Screenshots`, by month |
| App installers (`.dmg`) | `Downloads/Installers` |
| Documents, slides, spreadsheets, zips | the matching folder in **Documents**, or **Documents/Misc** if unsure |
| Notes (`.txt`, `.md`) | `Notes/00-Inbox` |
| The same file downloaded twice | the extra copy goes to the Trash |

**It filed something in the wrong place?** Just drag it to the right folder.
It learns from that.

**Opened an installer disk and forgot to close it?** It's ejected for you after
15 minutes.

### Where things are

mac-organizer creates a few folders in your home folder (in Finder: **Go →
Home**):

- **Media**: Images, Audio, Video, Screenshots
- **Documents/Misc**: anything it wasn't sure about
- **Documents/Keys**: security keys and certificates (private)
- **Inbox**: things from your Desktop pass through here for a moment
- **Notes**: a notes folder (you can open it with the free
  [Obsidian](https://obsidian.md) app, or ignore it)

---

## Folder suggestions

mac-organizer never creates folders on its own. Instead, when several files in
**Misc** clearly belong together, it **asks you**, at most once a day:

> 3 files in Misc look like Taxes. Open Folder Suggestions to review.

Press ⌘ Space, type **Folder Suggestions** and press Enter. It shows the files
and asks:

> **Create a "Taxes" folder in Documents?**
> [ Not now ]  [ Never suggest this ]  [ **Create & move** ]

You can change the name before clicking. Nothing happens unless you click
**Create & move**. If you ignore it, nothing changes. You can open Folder
Suggestions any time; if nothing is waiting it says so.

---

## Already have a messy Mac?

If your Downloads or Documents are already full, preview first. Nothing moves
until you say so.

1. Do the install above, and answer **No** to "Load the background sorter
   now?" (it only asks when there are lots of files; if it doesn't ask,
   sorting simply starts). Set up the AI model first, or every document will
   be planned into Misc.

2. Make the preview:

   ```bash
   cd ~/.mac-organizer
   python3 route.py --backlog ~/Downloads ~/Documents
   ```

   These commands never start the background sorter. When it finishes, it
   prints where it saved the list (a path ending in `.tsv`).

3. Open the list, putting the path it printed between the quotes:

   ```bash
   open -e "PASTE THE PATH HERE"
   ```

   - To **skip** a line, put `#` at the very start of it.
   - Lines starting with `#NEWFOLDER` are **suggested new folders**. To accept
     one, delete just that `#`. You may change the folder name (the third
     column).
   - Don't change anything else on a line. The columns are separated by tabs,
     so don't delete those.

   Save the file.

4. Near the top of the list is a line containing
   `--backlog --apply`. Copy it **starting from `python3`** to the end of that
   line, paste it into Terminal and press Enter. It does exactly what's left in
   the list.

5. Switch on the everyday sorter: run `./install.sh` again and answer `y`.

---

## Update to the latest version

Paste the same line you installed with. It fetches the latest version and runs
the install again:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/thealiceiris/mac-organizer/main/get.sh)"
```

## Turn it off

```bash
cd ~/.mac-organizer
./uninstall.sh
```

Your files stay exactly where they are.

---

## Privacy

Everything happens on your Mac. The AI model runs locally and only talks to
your own computer. No file, name or content is ever sent over the internet.

---

## Want more control?

Rules, settings, logs and every command are explained in the
**[advanced guide](ADVANCED.md)**.

Found a problem? Please open an issue on GitHub and describe what happened.
The file `~/.local/state/mac-organizer/actions.log` lists every move it made,
which helps a lot.
