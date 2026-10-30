# The Bin-Ghoul: Your Recycle Bin Is Haunted (and Everything Stays on Your Laptop)

*Submission for the "Ghost in the Machine" challenge.*

## What I built

Every operating system has a graveyard: the Recycle Bin. Mine is full of half-finished drafts, broken configs and files named `final_v2_REALLY_final`. I wanted to know what they would say if they could talk.

**Bin-Ghoul** reads your Recycle Bin (read-only) and shows each deleted file as a tombstone in a small web graveyard. Click one and the ghost of that file speaks, with a two-sentence epitaph about how it was discarded. If the file is a small text or code file, the ghost gets a short fragment of its contents, so it can complain about something specific.

[Add a screenshot or GIF of the graveyard here]

## Demo

[Link to repo]

Run it with `python bin_ghoul.py`. Use `--demo` for a fictional graveyard.

## How it works

- **Python, standard library only:** a tiny local server, no pip installs.
- **Reading the bin:** PowerShell's Shell interface on Windows lists names, original folders, deletion dates and sizes. Linux and macOS read the Trash folder.
- **The model:** Llama 3.2 1B, running locally through Ollama. A system prompt turns each file into a dramatic, slightly passive-aggressive spirit.
- **The UI:** one self-contained page with no external assets. Stone is textured with SVG lighting filters, the fog is a WebGL fragment shader, and the scene has a haunted manor with flickering windows, bats crossing the harvest moon, swaying grass, drifting embers and the occasional flash of lightning. Tombstones rise from the ground on load, and waking a ghost releases a wisp and lights a candle. It respects reduced-motion settings.

## Why open innovation matters here

A Recycle Bin holds the things you were least proud of: unfinished thoughts, private notes, mistakes. That is exactly the data you should not paste into a cloud API.

- **Privacy by design:** an open-weight model means your file names and contents are processed on your own machine and never leave it.
- **No cost, no account, works offline:** once the model is pulled, there are no API keys or bills.
- **Hackable:** the whole personality is one system prompt. Swap it to make the ghosts gothic poets, grumpy sysadmins or cheerful motivational speakers. Swap the model with one flag.

This only works because small open models are now good enough to be funny on a laptop. A 1B-parameter model is not perfect (some epitaphs are repetitive), but it is good enough for a haunting.

## What I learned

- Windows hides Recycle Bin details behind renamed files, so asking the Shell interface is far easier than parsing the raw folder.
- Small models need tight prompts: "exactly two sentences, no emojis" makes a big difference.
- Generating each epitaph only when you click keeps the page instant and the laptop cool.

## What's next

- Choose the ghost's personality in the UI
- Group related files into a single "haunted project"
- Optional voice for the spirits using a local text-to-speech model

*Rest in peace, `todo_list_2024.txt`.*
