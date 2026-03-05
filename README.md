# Chinese-Novel-Translator

A standalone translation pipeline that converts Chinese web-novel chapters into polished English XHTML, then compiles them into PDF and EPUB books. Uses the OpenAI API with a strict two-pass translation protocol and per-story glossaries for consistent character/term naming.

A standalone translation pipeline that converts Chinese web-novel chapters into polished English XHTML, then compiles them into PDF and EPUB books. Uses the OpenAI API with a strict two-pass translation protocol and per-story glossaries for consistent character/term naming.

## Features

- **Clipboard capture** — copies Chinese text straight from the clipboard into the processing queue
- **Two-pass AI translation** — rough pass → polished literary English, guided by a glossary and style instructions
- **Multi-story support** — each story gets its own translated chapters, compiled books, and glossary
- **PDF & EPUB compilation** — one-click build from translated XHTML chapters
- **Standalone exe** — runs on any Windows machine without Python installed

## Quick Start (exe)

1. Download or build `WuxiaTranslator.exe`
2. Double-click — it creates a `WuxiaTranslatorFiles` workspace on your Desktop
3. Set your OpenAI API key in the `.env` file inside that workspace
4. Run the exe and follow the on-screen menus

## Quick Start (source)

```bash
pip install -r requirements.txt
# Add your OpenAI API key to .env
python scripts/cat.py
```

## Building the exe

Use the provided build script (recommended):

- `python build_exe.py --noconfirm`

This builds via `WuxiaTranslator.spec`, which includes required hidden imports for the frozen EXE.

Avoid running raw `pyinstaller --onefile ...` commands directly, because they can miss spec-only
packaging fixes.

## Workspace Structure

| Folder | Purpose |
|--------|---------|
| `input_box/<story>/` | Staging area for raw chapter text before queuing |
| `raw_chapters/queue/<story>/` | Chapters waiting to be translated |
| `raw_chapters/processed/<story>/` | Originals after successful translation |
| `raw_chapters/failed/<story>/` | Chapters that failed (for retry) |
| `translated_chapter/<story>/` | Translated XHTML chapters per story |
| `compiled_books/<story>/` | Generated PDF and EPUB files per story |
| `master reference files/` | Shared translation instructions |
| `master reference files/<story>/` | Per-story glossary |
| `debug_chapters/<story>/` | Debug output for troubleshooting |

## Requirements

- Python 3.10+
- OpenAI API key
- GTK3 runtime (for WeasyPrint PDF generation)
- See `requirements.txt` for Python packages

## Disclaimer

This tool is provided for **personal and educational use only**. Chinese web novels and other literary works are protected by copyright. Translating copyrighted content without permission from the rights holder may constitute infringement under applicable law. Users are solely responsible for ensuring they have the necessary rights or permissions for any content they process with this tool. The author of this software does not condone or encourage copyright infringement.
