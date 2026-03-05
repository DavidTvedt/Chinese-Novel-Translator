"""Clipboard watcher — auto-saves copied chapter text into input_box/.

Usage:
  python scripts/clipboard_to_input_box.py --watch     # recommended: runs in background, auto-saves on clipboard change
  python scripts/clipboard_to_input_box.py              # one-shot: save current clipboard and exit
  python scripts/clipboard_to_input_box.py --session 10 # interactive: prompts you N times to copy+Enter

Workflow (watch mode):
  1) Start this script
  2) On the webpage: Ctrl+P → Ctrl+A → Ctrl+C → close print → click next chapter
  3) Each copy auto-creates a file like input_box/535.txt
    4) When done, press T in the terminal to stop (Ctrl+C aborts)
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


# Allow WUXIA_ROOT env var to override (used by exe mode)
import os as _os
ROOT = Path(_os.environ.get('WUXIA_ROOT', Path(__file__).resolve().parents[1]))
_STORY = _os.environ.get('WUXIA_STORY', 'default')
INPUT_BOX = ROOT / "input_box" / _STORY
TRANSLATED = ROOT / "translated_chapter" / _STORY
QUEUE = ROOT / "raw_chapters" / "queue" / _STORY
PROCESSED = ROOT / "raw_chapters" / "processed" / _STORY


def _latest_translated_chapter() -> int | None:
    """Return the highest translated chapter number found for this story."""
    try:
        if not TRANSLATED.exists():
            return None
        nums: list[int] = []
        for f in TRANSLATED.glob("*.xhtml"):
            stem = f.stem
            # Accept both newer and older naming conventions as long as a chapter
            # number appears somewhere in the filename (e.g. "570", "chapter_572_slug").
            m = re.search(r"chapter[_-]?(\d{1,6})", stem, re.IGNORECASE)
            if not m:
                m = re.search(r"(\d{1,6})", stem)
            if m:
                try:
                    nums.append(int(m.group(1)))
                except Exception:
                    pass
        return max(nums) if nums else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Clipboard reading
# ---------------------------------------------------------------------------

def _get_clipboard_text() -> str:
    """Return current clipboard text (tkinter preferred; pyperclip fallback)."""
    try:
        import tkinter as tk
        r = tk.Tk()
        r.withdraw()
        try:
            data = r.clipboard_get()
        except tk.TclError:
            data = ""
        finally:
            try:
                r.destroy()
            except Exception:
                pass
        return data or ""
    except Exception:
        pass

    try:
        import pyperclip  # type: ignore
        return pyperclip.paste() or ""
    except Exception as e:
        raise RuntimeError(
            "Could not read clipboard. Ensure tkinter is available (ships with Python on Windows) "
            f"or install pyperclip. Error: {e}"
        )


# ---------------------------------------------------------------------------
# Chapter number extraction
# ---------------------------------------------------------------------------

_CHAPTER_NUM_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"第\s*([一二三四五六七八九零十百千万0-9]{1,15})\s*章"),
    re.compile(r"\bChapter\s*(\d{1,6})\b", re.IGNORECASE),
    re.compile(r"\b(\d{1,6})\s*章\b"),
]


def _chinese_to_arabic(cn: str) -> int | None:
    """Convert Chinese numerals like 五百三十五 → 535."""
    if not cn:
        return None
    cn = cn.strip()
    if cn.isdigit():
        try:
            return int(cn)
        except Exception:
            return None

    digit_map = {
        "零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
        "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
    }
    unit_map = {"十": 10, "百": 100, "千": 1000}
    section_unit_map = {"万": 10000}

    total = 0
    section = 0
    number = 0

    for ch in cn:
        if ch in digit_map:
            number = digit_map[ch]
        elif ch in unit_map:
            unit = unit_map[ch]
            if number == 0:
                number = 1
            section += number * unit
            number = 0
        elif ch in section_unit_map:
            unit = section_unit_map[ch]
            section += number
            number = 0
            total += section * unit
            section = 0

    return (total + section + number) or None


def _extract_chapter_number(text: str) -> int | None:
    for pat in _CHAPTER_NUM_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        raw = m.group(1)
        if raw.isdigit():
            try:
                return int(raw)
            except Exception:
                continue
        n = _chinese_to_arabic(raw)
        if n:
            return n
    return None


# ---------------------------------------------------------------------------
# Text normalization and save
# ---------------------------------------------------------------------------

def _normalize_text(text: str) -> str:
    text = text.replace("\x00", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.strip()


def _unique_path(base: Path) -> Path:
    if not base.exists():
        return base
    stem = base.stem
    suffix = base.suffix
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate = base.with_name(f"{stem}__{ts}{suffix}")
    if not candidate.exists():
        return candidate
    for i in range(2, 1000):
        c = base.with_name(f"{stem}__{ts}_{i}{suffix}")
        if not c.exists():
            return c
    return base.with_name(f"{stem}__{ts}_{int(time.time())}{suffix}")


@dataclass(frozen=True)
class SaveResult:
    path: Path
    chapter_number: int | None
    chars: int


def _chapter_already_exists(chap_num: int) -> str | None:
    """Check if a chapter already exists in input_box, queue, processed, or translated.
    Returns a description of where it was found, or None."""
    # Match chapter_N pattern (with optional title slug)
    pattern = f"chapter_{chap_num}"
    for folder, label in [
        (INPUT_BOX, "input_box"),
        (QUEUE, "queue"),
        (PROCESSED, "processed"),
    ]:
        if folder.exists():
            for f in folder.glob("*.txt"):
                if f.stem == pattern or f.stem.startswith(pattern + "_"):
                    return label
    if TRANSLATED.exists():
        for f in TRANSLATED.glob("*.xhtml"):
            if f.stem == pattern or f.stem.startswith(pattern + "_"):
                return "translated_chapter"
    return None


def save_clipboard_to_input_box(text: str) -> SaveResult | None:
    INPUT_BOX.mkdir(parents=True, exist_ok=True)
    normalized = _normalize_text(text)
    if not normalized:
        return None

    chap = _extract_chapter_number(normalized)

    # Duplicate detection
    if chap is not None:
        location = _chapter_already_exists(chap)
        if location:
            print(f"  Skipped chapter {chap}: already exists in {location}/")
            return None
        filename = f"chapter_{chap}.txt"
    else:
        filename = datetime.now().strftime("%Y%m%d_%H%M%S") + ".txt"

    out_path = _unique_path(INPUT_BOX / filename)
    out_path.write_text(normalized, encoding="utf-8")
    return SaveResult(path=out_path, chapter_number=chap, chars=len(normalized))


# ---------------------------------------------------------------------------
# CLI modes
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Save clipboard chapter text into input_box/ as .txt files"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--watch", action="store_true",
        help="Watch clipboard continuously; auto-save on each new large paste (recommended)",
    )
    mode.add_argument(
        "--session", type=int, metavar="N",
        help="Interactive mode: prompt N times for copy+Enter",
    )
    parser.add_argument(
        "--min-chars", type=int, default=1000,
        help="Minimum clipboard size to trigger auto-save in watch mode (default: 1000)",
    )
    parser.add_argument(
        "--poll", type=float, default=0.5,
        help="Polling interval in seconds for watch mode (default: 0.5)",
    )
    args = parser.parse_args()

    # Optional env overrides (useful in frozen exe without changing CLI flags)
    try:
        env_min = _os.environ.get("WUXIA_CLIP_MIN_CHARS")
        if env_min is not None and str(env_min).strip() != "":
            args.min_chars = int(env_min)
    except Exception:
        pass

    try:
        env_poll = _os.environ.get("WUXIA_CLIP_POLL")
        if env_poll is not None and str(env_poll).strip() != "":
            args.poll = float(env_poll)
    except Exception:
        pass

    debug = _os.environ.get("WUXIA_CLIP_DEBUG", "0") == "1"

    # --- Session mode ---
    if args.session:
        n = max(1, args.session)
        print(f"Manual capture session: {n} page(s).")
        print("For each page: copy the chapter text, then press Enter here.")
        last_saved = ""
        saved_count = 0
        while saved_count < n:
            input(f"[{saved_count}/{n}] Copy chapter text, then press Enter to save... ")
            text = _normalize_text(_get_clipboard_text())
            if not text:
                print("  Clipboard empty; skipped (does not count). Try again.")
                continue
            if text == last_saved:
                print("  Clipboard unchanged; skipped (copy the new page first).")
                continue
            result = save_clipboard_to_input_box(text)
            if result:
                saved_count += 1
                chap = f"chapter {result.chapter_number}" if result.chapter_number else "unknown chapter"
                print(f"  Saved {chap}: {result.path.name} ({result.chars} chars)")
                last_saved = text
            else:
                print("  Skipped (duplicate or empty). Does not count.")
            if saved_count < n:
                print("  Now go to the next page and copy again.")
        print(f"Session complete. Saved {saved_count} file(s).")
        return 0

    # --- Watch mode ---
    if args.watch:
        # Screen 1: show status
        print("=" * 50)
        print("  Clipboard Chapter Capture")
        print("=" * 50)
        print()

        latest = _latest_translated_chapter()
        if latest is None:
            print("  Latest translated chapter: (none yet)")
        else:
            print(f"  Latest translated chapter: {latest}")
        print()

        print("  Watching continues until you press T.")

        # Screen 2: clear and show watching status
        _os.system('cls' if _os.name == 'nt' else 'clear')
        print("=" * 50)
        print("  Clipboard Chapter Capture")
        print("=" * 50)
        print()
        print("  Watching clipboard (no chapter limit).")
        print(f"  Each copy is auto-detected and saved.")
        print(f"  Min chars: {args.min_chars} | Poll interval: {args.poll}s")
        print()
        print("  Press T → stop and continue.")
        print("  Ctrl+C → abort.")
        if debug:
            print("  Debug: WUXIA_CLIP_DEBUG=1 (will print clipboard-change info)")
        print()

        # Ignore whatever is already in the clipboard at startup.
        # Otherwise, the first chapter often gets auto-saved immediately from stale content.
        try:
            last = _normalize_text(_get_clipboard_text())
        except Exception:
            last = ""
        if debug and last:
            print(f"  [debug] initial clipboard ignored: {len(last)} chars")
        saved_count = 0
        stop_early = False
        aborted = False

        # IMPORTANT: Do NOT spawn a background thread that calls input().
        # In exe mode, that thread can keep running after capture completes and
        # accidentally consume keystrokes meant for the main menu.
        def _poll_for_stop_key() -> None:
            nonlocal stop_early
            if stop_early:
                return
            if _os.name != "nt":
                return
            try:
                import msvcrt
                while msvcrt.kbhit():
                    ch = msvcrt.getwch()
                    if ch and ch.upper() == "T":
                        stop_early = True
                        print("\n  → 'T' received — stopping capture...")
                        return
            except Exception:
                return

        try:
            while not stop_early:
                _poll_for_stop_key()
                if stop_early:
                    break
                try:
                    cur = _normalize_text(_get_clipboard_text())
                except Exception as e:
                    if debug:
                        print(f"  [debug] clipboard read error: {e}")
                    time.sleep(args.poll)
                    continue
                if cur and cur != last:
                    if debug:
                        print(f"  [debug] clipboard changed: {len(cur)} chars")
                    if len(cur) < args.min_chars:
                        if debug:
                            print(f"  [debug] below min-chars ({args.min_chars}); not saving")
                        last = cur
                        time.sleep(max(0.1, args.poll))
                        continue

                    result = save_clipboard_to_input_box(cur)
                    if result:
                        saved_count += 1
                        chap = f"chapter {result.chapter_number}" if result.chapter_number else "?"
                        print(f"[{saved_count}] Saved {chap}: {result.path.name} ({result.chars} chars)")
                    else:
                        # Duplicate detected — don't count, but update last to avoid re-checking
                        pass
                    last = cur
                time.sleep(max(0.1, args.poll))
        except KeyboardInterrupt:
            aborted = True

        if aborted:
            print(f"\nAborted. Saved {saved_count} file(s).")
            return 2

        print(f"\nDone. Saved {saved_count} file(s).")
        return 0

    # --- One-shot mode (default) ---
    text = _get_clipboard_text()
    result = save_clipboard_to_input_box(text)
    if not result:
        print("Clipboard empty; nothing saved.")
        return 1
    chap = f" (chapter {result.chapter_number})" if result.chapter_number else ""
    print(f"Saved{chap}: {result.path.name} ({result.chars} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
