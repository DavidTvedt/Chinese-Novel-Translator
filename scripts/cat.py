"""CAT (Capture And Translate) — main menu for the Wuxia translation toolkit.

Usage:
  python scripts/cat.py          # dev mode (runs from project folder)
  WuxiaTranslator.exe            # standalone (creates workspace next to exe)

Main menu:
    0. Set API Key — configure OPENAI_API_KEY in the workspace .env
    1. Translate  — pick a story, capture chapters, translate, then compile
    2. Compile Story — rebuild PDF/EPUB from existing translated chapters
    3. Help       — show instructions and folder layout
    4. Delete Story
    5. Quit
"""

import os
import shutil
import subprocess
import sys
import traceback
import time
from pathlib import Path

# --- Detect missing stdin (e.g., double-clicked exe / windowed mode) ---
# NOTE: do not require a TTY; piping/redirecting input should still work.
if sys.stdin is None or getattr(sys.stdin, "closed", False):
    print(
        "\n[ERROR] No console input available.\n\n"
        "Please run this program from a terminal (Command Prompt or PowerShell).\n"
        "If you double-clicked the exe, close this window and launch it from a terminal instead.\n"
    )
    sys.exit(1)


def _crash_log_path() -> Path:
    # Prefer writing next to the exe workspace if frozen.
    frozen = getattr(sys, "frozen", False)
    if frozen:
        try:
            return Path(sys.executable).resolve().parent / "WuxiaTranslatorFiles" / "crash.log"
        except Exception:
            pass
    return Path("crash.log")


def _install_excepthook() -> None:
    def _hook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        try:
            path = _crash_log_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        except Exception:
            pass
        # Also print to stderr when possible.
        try:
            sys.stderr.write(text)
            sys.stderr.flush()
        except Exception:
            pass

    sys.excepthook = _hook


_install_excepthook()

# Force unbuffered stdout so prompts appear immediately in frozen exe
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(line_buffering=True)
os.environ['PYTHONUNBUFFERED'] = '1'


def _clear():
    """Clear the terminal screen."""
    if os.environ.get("WUXIA_NO_CLEAR", "0") == "1":
        return
    os.system('cls' if os.name == 'nt' else 'clear')


_LAST_TRANSLATION_SECONDS: float | None = None


def _format_duration(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    total = int(round(seconds))
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    if h > 0:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:d}:{s:02d}"

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------

FROZEN = getattr(sys, 'frozen', False)

if FROZEN:
    # Running as bundled exe — scripts live inside the temp extraction dir
    BUNDLE_DIR = Path(sys._MEIPASS)
    # Workspace is a "CAT" folder next to the exe
    WORKSPACE = Path(sys.executable).resolve().parent / "WuxiaTranslatorFiles"
else:
    BUNDLE_DIR = Path(__file__).resolve().parents[1]
    WORKSPACE = BUNDLE_DIR


# ---------------------------------------------------------------------------
# Bootstrap workspace (exe mode only)
# ---------------------------------------------------------------------------

_WORKSPACE_FOLDERS = {
    "input_box":              "Staging area for raw chapter text before queuing",
    "raw_chapters/queue":     "Chapters waiting to be translated",
    "raw_chapters/processed": "Original chapters after successful translation",
    "raw_chapters/failed":    "Chapters that failed translation (for retry)",
    "translated_chapter":     "Translated XHTML chapters, organised by story",
    "debug_chapters":         "Debug output for troubleshooting translations",
    "compiled_books":         "Generated PDF and EPUB files, organised by story",
    "master reference files": "Translation instructions and per-story glossaries",
}


def _bootstrap_workspace():
    """Create the workspace folder and seed it with master files."""
    first_run = not WORKSPACE.exists()
    created = []
    for folder in _WORKSPACE_FOLDERS:
        p = WORKSPACE / folder
        if not p.exists():
            p.mkdir(parents=True, exist_ok=True)
            created.append(folder)

    # Copy bundled master reference files if they don't exist yet
    # Only copy shared files (e.g. instructions), NOT master_glossary.txt (per-story)
    bundled_master = BUNDLE_DIR / "master reference files"
    ws_master = WORKSPACE / "master reference files"
    if bundled_master.exists():
        for src_file in bundled_master.iterdir():
            if src_file.is_dir() or src_file.name == "master_glossary.txt":
                continue
            dest_file = ws_master / src_file.name
            if not dest_file.exists():
                shutil.copy2(str(src_file), str(dest_file))

    # Ensure workspace .env exists.
    # In exe mode, we intentionally create a blank key so no secret/template value
    # is ever shipped inside the binary.
    ws_env = WORKSPACE / ".env"
    if not ws_env.exists():
        ws_env.write_text(
            "# OpenAI API key (required for translation)\n"
            "OPENAI_API_KEY=\n",
            encoding="utf-8",
        )

    if first_run or created:
        print(f"\n  Workspace: {WORKSPACE}")
        if created:
            print("  Created folders:")
            for folder in created:
                desc = _WORKSPACE_FOLDERS.get(folder, "")
                # Show short name (last part) for nested paths
                print(f"    + {folder + '/':<30s} {desc}")
        if first_run:
            print(f"\n  Set your OpenAI API key in: {ws_env}")
        print()


# ---------------------------------------------------------------------------
# Dependency check (exe mode)
# ---------------------------------------------------------------------------

_REQUIRED_PACKAGES = {
    "openai": "openai",
    "dotenv": "python-dotenv",
    "tiktoken": "tiktoken",
    "weasyprint": "weasyprint",
    "ebooklib": "ebooklib",
}


def _check_dependencies() -> list[str]:
    """Return list of user-friendly error messages for missing deps."""
    errors = []

    # Check Python packages
    missing_pkgs = []
    for import_name, pip_name in _REQUIRED_PACKAGES.items():
        try:
            __import__(import_name)
        except ImportError:
            missing_pkgs.append(pip_name)
    if missing_pkgs:
        errors.append(
            f"Missing Python packages: {', '.join(missing_pkgs)}\n"
            f"  Install with:  pip install {' '.join(missing_pkgs)}"
        )

    # Check GTK / WeasyPrint native libs (needed for PDF generation)
    try:
        import weasyprint  # noqa: F811
        # Suppress GLib-GIO stderr noise during test render
        _saved_stderr = sys.stderr
        sys.stderr = open(os.devnull, 'w')
        try:
            weasyprint.HTML(string="<p>test</p>").write_pdf()
        finally:
            sys.stderr.close()
            sys.stderr = _saved_stderr
    except ImportError:
        pass  # already caught above
    except OSError as e:
        err_str = str(e)
        if "cannot" in err_str.lower() or "gobject" in err_str.lower() or "pango" in err_str.lower():
            errors.append(
                "WeasyPrint native libraries (GTK/Pango/Cairo) not found.\n"
                "  PDF generation will fail without them.\n"
                "  Install GTK for Windows:\n"
                "    1. Install MSYS2 from https://www.msys2.org\n"
                "    2. In MSYS2 terminal run:  pacman -S mingw-w64-x86_64-pango\n"
                "    3. Add C:\\msys64\\mingw64\\bin to your system PATH\n"
                "    4. Restart this program"
            )
    except Exception:
        pass  # other errors will surface later

    return errors


def _read_openai_api_key_from_env_file(env_path: Path) -> str | None:
    """Return OPENAI_API_KEY value from a .env file, or None if missing/invalid."""
    try:
        if not env_path.exists():
            return None
        for line in env_path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if not s.startswith("OPENAI_API_KEY="):
                continue
            val = s.split("=", 1)[1].strip().strip('"').strip("'")
            if not val or val == "sk-your-key-here" or len(val) <= 10:
                return None
            return val
    except Exception:
        return None
    return None


def _get_configured_openai_api_key() -> str | None:
    """Return the configured API key (env var first, then workspace .env)."""
    env_val = os.environ.get("OPENAI_API_KEY", "").strip().strip('"').strip("'")
    if env_val and env_val != "sk-your-key-here" and len(env_val) > 10:
        return env_val
    return _read_openai_api_key_from_env_file(WORKSPACE / ".env")


def _mask_api_key(key: str | None) -> str:
    if not key:
        return "(not set)"
    k = key.strip()
    if len(k) <= 12:
        return "***hidden***"
    return f"{k[:6]}...{k[-4:]}"


def _write_openai_api_key_to_env_file(env_path: Path, new_key: str) -> None:
    """Set OPENAI_API_KEY in the env file, preserving other lines when possible."""
    new_key = (new_key or "").strip().strip('"').strip("'")
    if not new_key:
        raise ValueError("Empty API key")
    lines: list[str] = []
    if env_path.exists():
        try:
            lines = env_path.read_text(encoding="utf-8").splitlines()
        except Exception:
            lines = []
    out: list[str] = []
    replaced = False
    for line in lines:
        if line.strip().startswith("OPENAI_API_KEY="):
            out.append(f"OPENAI_API_KEY={new_key}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        if out and out[-1].strip() != "":
            out.append("")
        out.append(f"OPENAI_API_KEY={new_key}")
    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")


def _api_key_menu():
    """UI for viewing/setting the OpenAI API key."""
    while True:
        _clear()
        env_path = WORKSPACE / ".env"
        current = _get_configured_openai_api_key()
        print()
        print("=" * 60)
        print("  OpenAI API Key Settings")
        print("=" * 60)
        print()
        print(f"  Workspace .env: {env_path}")
        print(f"  OPENAI_API_KEY: {_mask_api_key(current)}")
        print()
        print("    1. Set OpenAI key (overwrites existing)")
        print("    2. Back to main menu")
        print()
        _flush_stdin()
        choice = _choose("  Enter number: ", 2)
        if choice == 2:
            return

        # choice == 1
        print()
        try:
            # Use visible input so paste is shown in the terminal (user preference).
            print("  Paste OpenAI API key (OPENAI_API_KEY): ", end="", flush=True)
            raw = input()
            key = (raw or "").strip().strip('"').strip("'")
            if not key or key == "sk-your-key-here" or len(key) <= 10:
                print("\n  That doesn't look like a valid key.")
                print("  Press Enter to continue...", end="", flush=True)
                input()
                continue
            _write_openai_api_key_to_env_file(env_path, key)
            os.environ["OPENAI_API_KEY"] = key
            print("\n  API key saved.")
            print("  Press Enter to continue...", end="", flush=True)
            input()
        except Exception as e:
            print(f"\n  Failed to save key: {e}")
            print("  Press Enter to continue...", end="", flush=True)
            input()


def _select_story(*, allow_new: bool = True, heading: str = "Which story would you like to continue translating?") -> str | None:
    """Paginated story selector. Returns story name or None to go back.

    Args:
        allow_new: If True, option 1 is "New story". If False, only existing stories.
        heading: Prompt text shown above the list.
    """
    books_dir = WORKSPACE / "compiled_books"
    translated_dir = WORKSPACE / "translated_chapter"
    PAGE_SIZE = 10

    # Gather story names from all story-specific subfolders
    master_dir = WORKSPACE / "master reference files"
    scan_parents = [
        books_dir, translated_dir, master_dir,
        WORKSPACE / "input_box",
        WORKSPACE / "raw_chapters" / "queue",
        WORKSPACE / "raw_chapters" / "processed",
        WORKSPACE / "debug_chapters",
    ]
    existing = set()
    for folder in scan_parents:
        if folder.exists():
            for sub in folder.iterdir():
                if sub.is_dir() and not sub.name.startswith("."):
                    existing.add(sub.name)

    stories = sorted(existing)

    # ---- No stories yet ----
    if not stories:
        if allow_new:
            print("\n  No existing stories found. Let's create one.\n")
            return _prompt_new_story(books_dir, translated_dir)
        else:
            print("\n  No stories with translated chapters found.")
            return None

    # ---- Paginated selection ----
    total_pages = max(1, (len(stories) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = 0

    while True:
        _clear()
        start = page * PAGE_SIZE
        end = min(start + PAGE_SIZE, len(stories))
        page_stories = stories[start:end]

        print(f"\n  {heading}\n")

        # Build numbered list — offset tracks the next available number
        offset = 1
        if allow_new:
            print("    1. [New story]")
            offset = 2

        max_num = offset - 1
        for i, name in enumerate(page_stories, offset):
            ch_dir = translated_dir / name
            count = len(list(ch_dir.glob("*.xhtml"))) if ch_dir.exists() else 0
            label = name.replace("_", " ").replace("-", " ").title()
            print(f"    {i}. {label}  ({count} ch)")
            max_num = i

        # Pagination section (letter keys)
        if total_pages > 1:
            print()
            nav_keys = {}
            if page > 0:
                nav_keys["a"] = "first"
                nav_keys["s"] = "prev"
                print(f"    A. [First page]    S. [Previous page]")
            if page < total_pages - 1:
                nav_keys["d"] = "next"
                nav_keys["f"] = "last"
                print(f"    D. [Next page]     F. [Last page]")
            print(f"         (page {page + 1}/{total_pages})")
        else:
            nav_keys = {}

        # Back / menu section
        print()
        print("    B. [Back to main menu]")
        print()

        print("  Enter choice: ", end="", flush=True)
        raw = input().strip().lower()

        # Check letter commands first
        if raw == "b":
            return None
        if raw in nav_keys:
            action = nav_keys[raw]
            if action == "first":
                page = 0
            elif action == "prev":
                page -= 1
            elif action == "next":
                page += 1
            elif action == "last":
                page = total_pages - 1
            continue

        # Check numeric selection
        try:
            idx = int(raw)
        except ValueError:
            print("  Invalid input. Enter a number or letter option.")
            continue

        if allow_new and idx == 1:
            return _prompt_new_story(books_dir, translated_dir)
        elif offset <= idx <= max_num:
            selected = page_stories[idx - offset]
            label = selected.replace("_", " ").replace("-", " ").title()
            print(f"  → {label}")
            return selected
        else:
            print("  Invalid number, try again.")


def _prompt_new_story(books_dir: Path, translated_dir: Path) -> str:
    """Prompt user for a new story name and create its folders."""
    while True:
        print("  Enter new story name: ", end="", flush=True)
        name = input().strip()
        if not name:
            print("  Name cannot be empty.")
            continue
        # Normalise to folder-safe slug: first word capitalised, rest lowercase, underscores
        words = name.split()
        slug = "_".join([words[0].capitalize()] + [w.lower() for w in words[1:]])
        if any(c in slug for c in '<>:"/\\|?*'):
            print("  Invalid characters in name. Try again.")
            continue
        (books_dir / slug).mkdir(parents=True, exist_ok=True)
        (translated_dir / slug).mkdir(parents=True, exist_ok=True)
        # Create all story-specific folders
        for sub in ["input_box", "raw_chapters/queue", "raw_chapters/processed",
                     "raw_chapters/failed", "debug_chapters"]:
            (WORKSPACE / sub / slug).mkdir(parents=True, exist_ok=True)
        # Create story-specific glossary folder with empty glossary
        glossary_dir = WORKSPACE / "master reference files" / slug
        glossary_dir.mkdir(parents=True, exist_ok=True)
        glossary_file = glossary_dir / "master_glossary.txt"
        if not glossary_file.exists():
            glossary_file.write_text("", encoding="utf-8")
        label = slug.replace("_", " ").replace("-", " ").title()
        print(f"  → Created: {label}")
        return slug


# ---------------------------------------------------------------------------
# Run helpers (exe vs dev mode)
# ---------------------------------------------------------------------------

def _run_capture() -> int:
    """Run clipboard capture. Returns exit code."""
    _clear()
    if FROZEN:
        try:
            sys.argv = ["clipboard_to_input_box.py", "--watch"]
            import importlib
            import clipboard_to_input_box
            importlib.reload(clipboard_to_input_box)
            return clipboard_to_input_box.main() or 0
        except SystemExit as e:
            return e.code if e.code is not None else 0
        except Exception as e:
            print(f"\n  Capture failed: {e}")
            return 1
    else:
        capture_script = BUNDLE_DIR / "scripts" / "clipboard_to_input_box.py"
        result = subprocess.run(
            [sys.executable, str(capture_script), "--watch"],
            cwd=str(WORKSPACE),
            env=os.environ.copy(),
        )
        return result.returncode


def _reload_translator():
    """Reload the translator module so it picks up the current WUXIA_STORY env var."""
    import importlib
    import translator
    importlib.reload(translator)
    translator.setup_logging()
    translator.ensure_folders()
    translator.load_api_key()
    return translator


def _run_translate() -> int:
    """Run translation (no build). Returns exit code."""
    _clear()
    print()
    print("=" * 60)
    print("  Translating captured chapters...")
    print("  This may take a minute or two per chapter. Please wait patiently.")
    print("=" * 60)
    print()
    global _LAST_TRANSLATION_SECONDS
    start = time.perf_counter()
    if FROZEN:
        try:
            t = _reload_translator()
            t.translate_all()
            _LAST_TRANSLATION_SECONDS = time.perf_counter() - start
            return 0
        except Exception as e:
            print(f"\n  Translation failed: {e}")
            _LAST_TRANSLATION_SECONDS = None
            return 1
    else:
        translator_script = BUNDLE_DIR / "translator.py"
        result = subprocess.run(
            [sys.executable, str(translator_script), "translate"],
            cwd=str(WORKSPACE),
            env=os.environ.copy(),
        )
        if result.returncode == 0:
            _LAST_TRANSLATION_SECONDS = time.perf_counter() - start
        else:
            _LAST_TRANSLATION_SECONDS = None
        return result.returncode


def _run_build() -> int:
    """Run PDF/EPUB compilation. Returns exit code."""
    _clear()
    print()
    print("=" * 60)
    print("  Compiling PDF and EPUB...")
    print("=" * 60)
    print()
    if FROZEN:
        try:
            t = _reload_translator()
            t.build_pdf()
            t.build_epub()
            return 0
        except Exception as e:
            print(f"\n  Build failed: {e}")
            return 1
    else:
        translator_script = BUNDLE_DIR / "translator.py"
        result = subprocess.run(
            [sys.executable, str(translator_script), "build"],
            cwd=str(WORKSPACE),
            env=os.environ.copy(),
        )
        return result.returncode


def _show_help():
    """Display usage instructions."""
    _clear()
    print("""
  ============================================================
  Help — How to use CAT
  ============================================================

  CAT (Capture And Translate) automates the process of turning
  raw Chinese web-novel chapters into translated PDF and EPUB
  files.

  Workflow:
    1. Select "Translate" from the main menu
    2. Pick an existing story or create a new one
    3. Copy chapters — CAT watches your clipboard and auto-saves
       each chapter (min 1000 characters to avoid accidental
         captures). Press 'T' at any time to stop.
    4. Chapters are translated via the OpenAI API
    5. After translation you can copy more chapters, compile
       the book, or quit

  Folder structure:
""")
    for folder, desc in _WORKSPACE_FOLDERS.items():
        print(f"    {folder + '/':<30s} {desc}")
    print(f"""
  Tips:
    - Duplicate chapters are detected and skipped automatically
    - You can re-compile books any time — translated chapters
      are preserved between sessions
    - Each story has its own translated_chapter/,
      compiled_books/, and glossary subfolder

  Press Enter to return to the main menu...""")
    input()


def _flush_stdin():
    """Drain stale characters sitting in the Windows console input buffer.

    Default behavior: discard only pending newlines (CR/LF). This prevents the common
    "empty input" situation without eating real typed digits.

    Opt-in aggressive behavior: if WUXIA_FLUSH_STDIN=1, drain *all* buffered keys.
    """

    if sys.stdin is None or not getattr(sys.stdin, "isatty", lambda: False)():
        return

    aggressive = os.environ.get("WUXIA_FLUSH_STDIN", "0") == "1"
    try:
        import msvcrt

        if aggressive:
            while msvcrt.kbhit():
                msvcrt.getch()
            return

        # Safe flush: only consume CR/LF. If we see any other character, push it back.
        while msvcrt.kbhit():
            ch = msvcrt.getwch()
            if ch in ("\r", "\n"):
                continue
            try:
                msvcrt.ungetch(ch)
            except Exception:
                # If ungetch fails for any reason, fall back to leaving the buffer as-is.
                pass
            break
    except Exception:
        pass


def _choose(prompt: str, count: int, *, min_value: int = 1) -> int:
    """Prompt for a number min_value..count. Loops until valid. Skips empty/whitespace lines. Logs all input to input_debug.log."""
    import datetime
    log_path = Path("input_debug.log")
    while True:
        _flush_stdin()  # Clear stale newlines so input() doesn't immediately read ""
        print(prompt, end="", flush=True)
        raw = input()
        # Log every input attempt
        with log_path.open("a", encoding="utf-8") as f:
            ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
            f.write(f"[{ts}] _choose() raw input: {repr(raw)}\n")
        raw = raw.strip()
        if raw == "":
            print(f"  Please enter a number between {min_value} and {count}.")
            continue
        try:
            n = int(raw)
            if min_value <= n <= count:
                return n
        except ValueError:
            pass
        print(f"  Please enter a number between {min_value} and {count}.")


def _delete_story():
    """Select and delete a story and all its associated files."""
    while True:
        _clear()
        story = _select_story(
            allow_new=False,
            heading="Which story would you like to delete?",
        )
        if story is None:
            return  # back to main menu

        label = story.replace("_", " ").replace("-", " ").title()
        translated_dir = WORKSPACE / "translated_chapter" / story
        books_dir = WORKSPACE / "compiled_books" / story
        glossary_dir = WORKSPACE / "master reference files" / story
        input_dir = WORKSPACE / "input_box" / story
        queue_dir = WORKSPACE / "raw_chapters" / "queue" / story
        processed_dir = WORKSPACE / "raw_chapters" / "processed" / story
        failed_dir = WORKSPACE / "raw_chapters" / "failed" / story
        debug_dir = WORKSPACE / "debug_chapters" / story

        # Count what will be deleted
        ch_count = len(list(translated_dir.glob("*.xhtml"))) if translated_dir.exists() else 0
        book_count = len(list(books_dir.iterdir())) if books_dir.exists() else 0
        input_count = len(list(input_dir.iterdir())) if input_dir.exists() else 0
        queue_count = len(list(queue_dir.iterdir())) if queue_dir.exists() else 0
        processed_count = len(list(processed_dir.iterdir())) if processed_dir.exists() else 0
        debug_count = len(list(debug_dir.iterdir())) if debug_dir.exists() else 0

        print(f"\n  About to delete: {label}")
        print(f"    - {ch_count} translated chapter(s)")
        print(f"    - {book_count} compiled book file(s)")
        print(f"    - Glossary folder")
        print(f"    - {input_count} input / {queue_count} queued / {processed_count} processed raw file(s)")
        print(f"    - {debug_count} debug file(s)")
        print()
        print("    1. Yes - delete and cleanup")
        print("    2. No - go back")
        print()
        _flush_stdin()
        confirm = _choose("  Enter number: ", 2)

        if confirm == 2:
            continue  # back to delete story selector

        # Delete all story-specific folders
        failed_ops = []
        for folder in [translated_dir, books_dir, glossary_dir,
                        input_dir, queue_dir, processed_dir, failed_dir, debug_dir]:
            if folder.exists():
                try:
                    shutil.rmtree(folder)
                except Exception:
                    failed_ops.append(folder.name)

        if failed_ops:
            print(f"\n  Partially deleted: {label}")
            print(f"  Could not remove: {', '.join(failed_ops)} (may be in use)")
        else:
            print(f"\n  Deletion complete: {label}")
        import time; time.sleep(1.5)


def _parse_chapter_num_from_name(name: str) -> int | None:
    """Best-effort extraction of a chapter number from various filenames."""
    import re
    # Prefer explicit chapter pattern
    m = re.search(r"chapter[_-]?(\d{1,6})", name, re.IGNORECASE)
    if not m:
        # fallback: any 1-6 digit number
        m = re.search(r"(\d{1,6})", name)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def _format_int_ranges(nums: list[int]) -> str:
    """Format a sorted list of ints as compact ranges (e.g. 1-3, 7, 9-10)."""
    if not nums:
        return ""
    nums = sorted(set(nums))
    ranges: list[tuple[int, int]] = []
    start = prev = nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
            continue
        ranges.append((start, prev))
        start = prev = n
    ranges.append((start, prev))
    parts = []
    for a, b in ranges:
        parts.append(str(a) if a == b else f"{a}-{b}")
    return ", ".join(parts)


def _missing_chapters_between_first_and_highest_copied(story: str) -> tuple[int | None, int | None, list[int]]:
    """Return (first, highest, missing_list) based on copied + translated chapters.

    Range is based on chapters you currently have available (copied in input_box/queue
    OR already translated). Presence is counted across copied + processed + translated so
    already-done chapters won't be flagged as missing.
    """

    input_dir = WORKSPACE / "input_box" / story
    queue_dir = WORKSPACE / "raw_chapters" / "queue" / story
    processed_dir = WORKSPACE / "raw_chapters" / "processed" / story
    translated_dir = WORKSPACE / "translated_chapter" / story

    copied_nums: set[int] = set()
    present_nums: set[int] = set()
    translated_nums: set[int] = set()

    def _add_nums_from_paths(paths, *, is_queue: bool = False):
        for p in paths:
            name = p.name
            if is_queue:
                parts = name.split("__", 2)
                if len(parts) == 3:
                    name = parts[2]
            n = _parse_chapter_num_from_name(name)
            if n is not None:
                present_nums.add(n)
                if is_queue or p.parent == input_dir:
                    copied_nums.add(n)

    if input_dir.exists():
        _add_nums_from_paths(input_dir.glob("*.txt"), is_queue=False)
    if queue_dir.exists():
        _add_nums_from_paths(queue_dir.glob("*.txt"), is_queue=True)
    if processed_dir.exists():
        _add_nums_from_paths(processed_dir.glob("*.txt"), is_queue=False)
    if translated_dir.exists():
        for p in translated_dir.glob("*.xhtml"):
            n = _parse_chapter_num_from_name(p.name)
            if n is not None:
                translated_nums.add(n)
                present_nums.add(n)

    range_nums = set(copied_nums) | set(translated_nums)
    if not range_nums:
        return None, None, []

    first = min(range_nums)
    highest = max(range_nums)
    missing = [n for n in range(first, highest + 1) if n not in present_nums]
    return first, highest, missing


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    # In exe mode, suppress native GLib-GIO stderr warnings at the OS level
    # (these come from C libraries and bypass Python's sys.stderr)
    if FROZEN:
        try:
            _devnull_fd = os.open(os.devnull, os.O_WRONLY)
            _saved_stderr_fd = os.dup(2)
            os.dup2(_devnull_fd, 2)
            os.close(_devnull_fd)
        except Exception:
            _saved_stderr_fd = None

    if FROZEN:
        _bootstrap_workspace()

    # Pre-flight checks (missing API key is handled in-app via menu option 0)
    issues = _check_dependencies()
    if issues:
        print("\n  Setup required before running:\n")
        for i, issue in enumerate(issues, 1):
            print(f"  {i}. {issue}\n")
        print("  Press Enter to exit...", end="", flush=True)
        input()
        return 1

    # Set env vars
    os.environ["WUXIA_ROOT"] = str(WORKSPACE)
    os.environ["WUXIA_EXE"] = "1" if FROZEN else "0"

    if FROZEN:
        sys.path.insert(0, str(BUNDLE_DIR))
        sys.path.insert(0, str(BUNDLE_DIR / "scripts"))

    # === Main menu loop ===
    while True:
        _clear()
        has_key = _get_configured_openai_api_key() is not None
        print()
        print("=" * 60)
        print("  CAT — Capture And Translate")
        print("  Chinese Web-Novel Translation Toolkit")
        print("=" * 60)
        print()
        print("    0. Set OpenAI API Key")
        if has_key:
            print("    1. Translate")
            print("    2. Compile Story")
            print("    3. Help")
            print("    4. Delete Story")
        print("    5. Quit")
        print()
        _flush_stdin()
        choice = _choose("  Enter number: ", 5, min_value=0)

        if choice == 0:
            _api_key_menu()
            continue

        if choice == 5:
            print("\n  Goodbye!")
            return 0

        if not has_key:
            # Only API key setup (0) and quit (5) are available.
            print("\n  Please set your OpenAI API key (OPENAI_API_KEY) first (option 0).")
            print("  Press Enter to continue...", end="", flush=True)
            input()
            continue

        if choice == 4:
            _delete_story()
            continue

        if choice == 3:
            _show_help()
            continue

        if choice == 2:
            # --- Compile an existing story ---
            _clear()
            compile_story = _select_story(
                allow_new=False,
                heading="Which story would you like to compile?",
            )
            if compile_story is None:
                continue  # back to main menu
            os.environ["WUXIA_STORY"] = compile_story
            rc = _run_build()
            if rc == 0:
                out_dir = WORKSPACE / "compiled_books" / compile_story
                print(f"\n  Done — check {out_dir}")
            else:
                print(f"\n  Build exited with code {rc}.")
            input("\n  Press Enter to return to main menu...")
            continue

        if choice != 1:
            print("\n  Invalid choice.")
            input("  Press Enter to continue...")
            continue

        # --- Translate workflow ---
        _clear()
        story_name = _select_story(
            allow_new=True,
            heading="Which story would you like to translate?",
        )
        if story_name is None:
            continue  # back to main menu

        os.environ["WUXIA_STORY"] = story_name

        while True:
            # Capture
            rc = _run_capture()
            _clear()
            print()
            print("=" * 60)
            story_label = story_name.replace("_", " ").replace("-", " ").title()
            if rc == 0:
                print(f"  Capture complete — {story_label}")
            else:
                print(f"  Capture stopped (code {rc}) — {story_label}")
            print("=" * 60)
            print()
            print("    1. Continue copying chapters")
            print("    2. Start translating")
            print("    3. Back to main menu")
            print()
            for _ in range(5):
                _flush_stdin()
            after_capture = _choose("  Enter number: ", 3)
            if after_capture == 1:
                continue  # loop back to capture
            if after_capture == 3:
                print("\n  Returning to main menu...")
                for _ in range(5):
                    _flush_stdin()
                break  # back to main menu

            # Missing-chapter checker before translating
            first, highest, missing = _missing_chapters_between_first_and_highest_copied(story_name)
            if missing and first is not None and highest is not None:
                _clear()
                print()
                print("=" * 60)
                print(f"  Missing chapters detected — {story_label}")
                print("=" * 60)
                print()
                print(f"  Available chapter range (copied or translated): {first} to {highest}")
                print(f"  Missing ({len(missing)}):")
                print(f"    {_format_int_ranges(missing)}")
                print()
                print("  What would you like to do?")
                print()
                print("    1. Continue copying chapters to fill the gaps")
                print("    2. Translate anyway (skip missing chapters)")
                print()
                for _ in range(5):
                    _flush_stdin()
                gap_choice = _choose("  Enter number: ", 2)
                if gap_choice == 1:
                    continue  # loop back to capture

            # Translate
            rc = _run_translate()
            if rc != 0:
                print(f"\n  Translation exited with code {rc}.")
                break  # back to main menu

            # Post-translation menu: compile, copy more, or quit
            _clear()
            print()
            print("=" * 60)
            story_label = story_name.replace("_", " ").replace("-", " ").title()
            print(f"  Translation complete — {story_label}")
            print("=" * 60)
            if _LAST_TRANSLATION_SECONDS is not None:
                print(f"  Total translation time: {_format_duration(_LAST_TRANSLATION_SECONDS)}")
            print()
            print("    1. Compile PDF & EPUB")
            print("    2. Continue copying chapters")
            print("    3. Back to main menu")
            print()
            for _ in range(5):
                _flush_stdin()
            post = _choose("  Enter number: ", 3)
            if post == 1:
                rc = _run_build()
                if rc == 0:
                    out_dir = WORKSPACE / "compiled_books" / story_name
                    _clear()
                    print()
                    print("=" * 60)
                    print(f"  Compilation complete — {story_label}")
                    print("=" * 60)
                    print(f"\n  Books saved to: {out_dir}")
                    print()
                    print("    1. Continue copying chapters")
                    print("    2. Back to main menu")
                    print()
                    for _ in range(5):
                        _flush_stdin()
                    post2 = _choose("  Enter number: ", 2)
                    if post2 == 1:
                        continue  # loop back to capture
                    break  # back to main menu
                else:
                    print(f"\n  Build exited with code {rc}.")
                    break  # back to main menu
            elif post == 2:
                continue  # loop back to capture
            else:
                print("\n  Returning to main menu...")
                for _ in range(5):
                    _flush_stdin()
                break  # back to main menu

        continue

    # implicit: back to top of main menu loop
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
