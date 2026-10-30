<img width="1906" height="907" alt="demo2" src="https://github.com/user-attachments/assets/4f583726-59ac-4896-9515-82805e249439" />

# The Bin-Ghoul: Echoes from the Recycle Bin

Your Recycle Bin is a graveyard. Bin-Ghoul reads it (read-only), and a small open-weight model running **on your own computer** writes a ghostly epitaph for every file you threw away. Click a tombstone to wake the spirit.

Built for the "Ghost in the Machine" challenge.

## Setup (Windows 11)

1. **Install Python 3.9 or newer** from python.org. Tick "Add Python to PATH". Nothing else to install: the project uses only the standard library.
2. **Set up Ollama** (open a terminal, such as PowerShell):
   ```
   ollama --version
   ollama pull llama3.2:1b
   ollama run llama3.2:1b "say hello like a ghost"
   ```
   The `pull` downloads the model once (about 1.3 GB). The `run` test confirms it works; type `/bye` to exit. Ollama keeps a background server running at `localhost:11434` while its tray icon is active. If it isn't, run `ollama serve` in a separate terminal.
3. **Run Bin-Ghoul** from this folder:
   ```
   python bin_ghoul.py
   ```
   Your browser opens at http://127.0.0.1:8765.

## Options

| Flag | What it does |
| --- | --- |
| `--demo` | Use a fictional graveyard instead of your real bin. Use this for screen recordings. |
| `--model qwen2.5:1.5b` | Use a different Ollama model (pull it first). |
| `--port 9000` | Use a different port. |
| `--no-browser` | Don't open the browser automatically. |

If the bin is empty, the demo graveyard appears automatically.

## How it works

- **Reading the bin:** on Windows it asks PowerShell's Shell interface for the bin's contents (original name, original folder, deletion date, size). On Linux and macOS it reads the Trash folder.
- **Snippets:** for small text and code files, the first 400 characters are shown to the model so the epitaph can mention what you actually wrote. Other files only share name, size, date and location.
- **The model:** each epitaph is one request to your local Ollama server, using a system prompt that makes the file a dramatic, slightly passive-aggressive ghost.
- **The page:** a tiny local web server on 127.0.0.1 serves one self-contained page. Nothing is reachable from outside your computer, and the page loads no images, fonts or scripts from the internet.

## Files

| File | Purpose |
| --- | --- |
| `bin_ghoul.py` | The whole app: bin reader, Ollama client, local server and the graveyard page |
| `run.bat` | Windows: double-click to start with your real Recycle Bin |
| `run_demo.bat` | Windows: double-click to start with the fictional graveyard (for recordings) |
| `README.md` | This file |
| `DEV_POST.md` | Draft of the challenge submission post |
| `LICENSE` | MIT license |
| `.gitignore` | Ignores Python cache files |

## The look

- Procedurally textured stone (SVG lighting filters), moss, cracks and cast shadows, in four tombstone shapes
- A WebGL fragment shader for drifting fog, with a CSS fallback if WebGL is unavailable
- A harvest moon, bats, a haunted manor with flickering windows, swaying trees and grass, drifting embers, film grain and rare lightning
- Tombstones rise from the ground on load. Waking a ghost releases a wisp and lights a candle
- Respects the reduced-motion setting and is fully keyboard accessible

## Privacy and safety

- Read-only: it never deletes, restores, moves or edits anything.
- Fully local: no cloud API, no telemetry, works offline once the model is pulled.
- Your files' contents are only ever sent to Ollama on your own machine. The page itself shows names, dates and the generated epitaphs, never file contents.
- Skips folders' contents, binary files, and text files over 200 KB.

## Troubleshooting

- **"Ollama is not running":** start the Ollama app (or `ollama serve`) and refresh.
- **"The model is missing":** run `ollama pull llama3.2:1b`.
- **The first epitaph is slow:** the model loads into memory on first use. Later ones are fast.
- **Odd or repetitive epitaphs:** 1B models are small. Try `--model qwen2.5:1.5b` or `llama3.2:3b` if your laptop can handle it.
