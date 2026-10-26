# mac-organizer

**Keeps your Mac tidy automatically.** Things you download or drop on your
Desktop are moved into the right folder for you: pictures to Pictures, music
to Music, documents into the matching folder in Documents.

For documents, a small AI model reads each file **on your own Mac** and picks
the best folder. Nothing is ever uploaded anywhere.

Your files are never deleted or overwritten. Anything it isn't sure about
goes to a folder called **Misc**, so you can sort it yourself.

---

## What you need

- A Mac
- [Homebrew](https://brew.sh) (a free tool for installing apps from the Terminal)
- About 5 GB of free space for the AI model (optional, but it's what makes
  document sorting smart)

---

## Install

Open the **Terminal** app (press ⌘ Space, type *Terminal*, press Enter), then
copy and paste each step.

**1. Install the helper tools**

```bash
brew install python llama.cpp poppler terminal-notifier
```

**2. Download an AI model** *(optional, recommended)*

Any "instruct" model in **GGUF** format from [Hugging Face](https://huggingface.co)
works. A file of about 4–5 GB (look for **Q4_K_M** in the name) suits most
Macs. Save it in a folder called `models` in your home folder.

**3. Download mac-organizer**

```bash
git clone https://github.com/thealiceiris/mac-organizer.git ~/.mac-organizer
cd ~/.mac-organizer
```

**4. Switch it on**

```bash
./install.sh
```

It may ask you two questions. Both are optional, and the answer is **No**
unless you type `y`:

- **"Your Documents folder is empty. Create starter folders…?"** Say yes if
  you'd like a few ready-made folders (Work, School, Finance, Health,
  Personal, Travel).
- **"Load the background sorter now?"** This only appears if you already have
  lots of files lying around. Say **No** if you'd like to see what it would do
  first (see *Already have a messy Mac?* below).

**5. Tell it where your AI model is**

Open the settings file:

```bash
open -e ~/.mac-organizer/rules.local.toml
```

Find the `model_path` line, remove the `#` at its start, and put in your
model's file name, e.g. `model_path = "~/models/my-model.gguf"`. Save the file.
That's it.

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
| The same file downloaded twice | the extra copy goes to the Trash |

You'll see a short notification when it sorts documents, and a summary of where
things went.

**It filed something in the wrong place?** Just drag it to the right folder.
It learns from that.

**Opened an installer disk and forgot to close it?** It's ejected for you after
15 minutes.

---

## Folder suggestions

mac-organizer never creates folders on its own. Instead, when several files in
**Misc** clearly belong together, it **asks you**, at most once a day:

> 3 files in Misc look like Taxes. Open Folder Suggestions to review.

Open the **Folder Suggestions** app (it's in your Applications folder, or press
⌘ Space and type *Folder Suggestions*). It shows the files and asks:

> **Create a "Taxes" folder in Documents?**
> [ Not now ]  [ Never suggest this ]  [ **Create & move** ]

You can change the name before clicking. Nothing happens unless you click
**Create & move**. If you ignore it, nothing changes.

---

## Already have a messy Mac?

If your Downloads or Documents are already full, you can **preview first**.
Nothing moves until you say so.

```bash
cd ~/.mac-organizer
python3 setup.py
python3 route.py --backlog ~/Downloads ~/Documents
```

These two commands never start the background sorter.

This writes a list of everything it *would* do and shows you where the list
is. Open it in TextEdit:

- To **skip** a line, put `#` at the start of it.
- Lines starting with `#NEWFOLDER` are **suggested new folders**. To accept
  one, remove its `#`. You can change the folder name (the third column).

When you're happy, run the command printed at the top of the list (it starts
with `python3 route.py --backlog --apply`). Then switch on the everyday sorter
with `./install.sh`.

---

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
