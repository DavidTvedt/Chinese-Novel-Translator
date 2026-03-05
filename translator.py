import argparse
import os
import sys
import time
import hashlib
import shutil
import logging
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
import openai
import tiktoken
from contextlib import contextmanager

# Suppress noisy GLib/GIO warnings printed to stderr by native libs (e.g. UWP app warnings)
# We'll filter out any stderr writes containing these substrings so they don't spam the terminal.
_orig_stderr = sys.stderr
class _StderrFilter:
    def __init__(self, orig, suppressed=None):
        self._orig = orig
        self._suppressed = suppressed or [
            'GLib-GIO-WARNING',
            'Glib-GIO-WARNING',
            'Unexpectedly, UWP app',
            "supports ",
        ]
    def write(self, data):
        try:
            # Only filter string data
            if isinstance(data, str) and any(s in data for s in self._suppressed):
                return
        except Exception:
            pass
        self._orig.write(data)
    def flush(self):
        try:
            self._orig.flush()
        except Exception:
            pass

sys.stderr = _StderrFilter(_orig_stderr)


# Context manager to suppress native (C-level) stderr writes by redirecting fd 2 to null.
@contextmanager
def suppress_native_stderr():
    """Temporarily redirect the OS-level stderr (fd 2) to os.devnull.
    This suppresses warnings printed by native libraries (GLib, GIO, Pango, etc.).
    """
    try:
        saved_stderr_fd = os.dup(2)
        null_fd = os.open(os.devnull, os.O_RDWR)
        os.dup2(null_fd, 2)
        os.close(null_fd)
        yield
    finally:
        try:
            os.dup2(saved_stderr_fd, 2)
            os.close(saved_stderr_fd)
        except Exception:
            pass

# Allow WUXIA_ROOT env var to override (used by exe mode)
ROOT = Path(os.environ.get('WUXIA_ROOT', Path(__file__).parent.resolve()))
RAW_CHAPTERS = ROOT / 'raw_chapters'
MASTER_INSTRUCTIONS = ROOT / 'master reference files' / 'translation_chatgpt_instructions.txt'
LOG_FILE = ROOT / 'translator.log'

# Story-specific paths (WUXIA_STORY env var set by cat.py)
STORY_NAME = os.environ.get('WUXIA_STORY', 'default')
STORY_TITLE = STORY_NAME.replace('_', ' ').replace('-', ' ').title()
INPUT_BOX = ROOT / 'input_box' / STORY_NAME
QUEUE = RAW_CHAPTERS / 'queue' / STORY_NAME
PROCESSED = RAW_CHAPTERS / 'processed' / STORY_NAME
FAILED = RAW_CHAPTERS / 'failed' / STORY_NAME
DEBUG = ROOT / 'debug_chapters' / STORY_NAME
MASTER_GLOSSARY = ROOT / 'master reference files' / STORY_NAME / 'master_glossary.txt'
TRANSLATED = ROOT / 'translated_chapter' / STORY_NAME
COMPILED_BOOKS = ROOT / 'compiled_books' / STORY_NAME
PDF_FILE = COMPILED_BOOKS / f'{STORY_NAME}.pdf'
EPUB_FILE = COMPILED_BOOKS / f'{STORY_NAME}.epub'

# Model/token config (adjustable)
MODEL_NAME = 'gpt-5.2'
MODEL_CONTEXT = 128000
MAX_COMPLETION = 16384
TOKEN_SAFETY_MARGIN = 256
SHORT_INSTRUCTIONS_MAX_CHARS = 1600
OVERLAP_TOKENS = 200

# Exe mode: clean terminal output (no verbose logging)
EXE_MODE = os.environ.get('WUXIA_EXE') == '1'

# Logging setup
def setup_logging():
    # File handler: record all INFO+ to log file
    file_handler = logging.FileHandler(LOG_FILE, mode='a', encoding='utf-8')
    file_handler.setLevel(logging.INFO)
    file_formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    file_handler.setFormatter(file_formatter)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # Avoid adding duplicate handlers on repeated setup calls
    has_file = any(isinstance(h, logging.FileHandler) for h in root.handlers)
    if not has_file:
        root.addHandler(file_handler)
    # Console handler: only in dev mode (not exe)
    if not EXE_MODE:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
        console_handler.setFormatter(console_formatter)
        has_console = any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler) for h in root.handlers)
        if not has_console:
            root.addHandler(console_handler)
    # Suppress noisy HTTP request logs from openai/httpx in exe mode
    if EXE_MODE:
        logging.getLogger('httpx').setLevel(logging.WARNING)
        logging.getLogger('openai').setLevel(logging.WARNING)
        logging.getLogger('httpcore').setLevel(logging.WARNING)

# Ensure all required folders exist
def ensure_folders():
    for folder in [INPUT_BOX, QUEUE, PROCESSED, FAILED, TRANSLATED, DEBUG, COMPILED_BOOKS, MASTER_GLOSSARY.parent]:
        folder.mkdir(parents=True, exist_ok=True)

# Load .env and OpenAI key
def load_api_key():
    load_dotenv(ROOT / '.env')
    key = os.getenv('OPENAI_API_KEY')
    if not key:
        logging.error('Missing OPENAI_API_KEY in .env')
        raise RuntimeError('Missing OPENAI_API_KEY in .env')
    logging.info('Loaded OpenAI API key')
    # For openai>=1.0.0, use OpenAI client
    global openai_client
    from openai import OpenAI
    openai_client = OpenAI(api_key=key)
    # Test API key by making a trivial request
    try:
        openai_client.models.list()
        logging.info('OpenAI API key test: SUCCESS')
    except Exception as e:
        logging.error(f'OpenAI API key test: FAILED - {e}')
        raise RuntimeError(f'OpenAI API key test failed: {e}')
    # Attempt to auto-detect the model context window for the configured model
    try:
        detect_model_context(MODEL_NAME)
    except Exception as e:
        logging.warning(f'Auto-detect model context failed: {e}')

# Utility: hash content
def hash_content(content):
    return hashlib.sha256(content.encode('utf-8')).hexdigest()

# Utility: get next chapter number
def get_next_chapter_number():
    # Prefer extracting the chapter number from each XHTML's internal title
    import re
    files = list(TRANSLATED.glob('*.xhtml'))
    max_num = 0
    for f in files:
        try:
            text = f.read_text(encoding='utf-8')
            m = re.search(r'Chapter\s*(\d+)', text)
            if m:
                num = int(m.group(1))
            else:
                # fallback: try story_chapter_N pattern, then bare digits
                m2 = re.search(r'_chapter_(\d+)', f.stem)
                if not m2:
                    m2 = re.search(r'(\d{1,6})', f.stem)
                num = int(m2.group(1)) if m2 else 0
            if num > max_num:
                max_num = num
        except Exception:
            continue
    return max_num + 1


# Utility: move all files from input_box to queue, then process
def enqueue_input_box():
    files = sorted(INPUT_BOX.glob('*.txt'))
    if not files:
        logging.info('No files in input_box to enqueue')
        return False
    for f in files:
        content = f.read_text(encoding='utf-8').strip()
        if not content:
            logging.info(f'{f.name} is empty, skipping')
            continue
        now = datetime.now().strftime('%Y%m%d_%H%M%S')
        h = hash_content(content)[:8]
        # Keep the original input filename inside the queue filename so we can
        # restore it later when moving to processed. Format: <timestamp>__<hash>__<origname>
        fname = f'{now}__{h}__{f.name}'
        out_path = QUEUE / fname
        out_path.write_text(content, encoding='utf-8')
        logging.info(f'Enqueued {fname} from {f.name}')
        logging.info(f'Queued: {fname}')
        f.unlink()  # Delete original
    return True

# Utility: move queue file
def move_queue_file(src, dest_folder):
    dest = dest_folder / src.name
    shutil.move(str(src), str(dest))


def _format_duration(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    total = int(round(seconds))
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    if h > 0:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:d}:{s:02d}"

# Load master files
def load_master_files():
    # Auto-create empty glossary if missing
    if not MASTER_GLOSSARY.exists():
        MASTER_GLOSSARY.parent.mkdir(parents=True, exist_ok=True)
        MASTER_GLOSSARY.write_text("", encoding="utf-8")
        logging.info("Created empty master_glossary.txt for story: %s", STORY_NAME)
    if not MASTER_INSTRUCTIONS.exists():
        raise FileNotFoundError('Missing translation_chatgpt_instructions.txt')
    glossary = MASTER_GLOSSARY.read_text(encoding='utf-8')
    instructions = MASTER_INSTRUCTIONS.read_text(encoding='utf-8')
    return glossary, instructions


def load_glossary_entries():
    """Return list of (zh, en) tuples from master glossary file."""
    if not MASTER_GLOSSARY.exists():
        return []
    lines = MASTER_GLOSSARY.read_text(encoding='utf-8').splitlines()
    entries = []
    for i, line in enumerate(lines):
        if i == 0:
            continue
        if not line.strip():
            continue
        if ',' in line:
            zh, en = line.split(',', 1)
            entries.append((zh.strip(), en.strip()))
    return entries


def build_relevant_glossary(text, max_chars=2000, max_terms=200):
    """Return a CSV-like string with glossary entries relevant to `text`.
    Picks entries whose Chinese term appears in `text`, ordered by term length (longer first).
    Stops when `max_chars` or `max_terms` reached.
    """
    text_sample = text
    entries = load_glossary_entries()
    # sort by Chinese term length descending to match longer phrases first
    entries.sort(key=lambda x: len(x[0]), reverse=True)
    kept = []
    chars = 0
    terms = 0
    for zh, en in entries:
        if terms >= max_terms:
            break
        if zh and zh in text_sample:
            line = f'{zh},{en}'
            if chars + len(line) + 1 > max_chars:
                break
            kept.append(line)
            chars += len(line) + 1
            terms += 1
    if not kept:
        # fallback: include a small subset of the most common entries (first N)
        for zh, en in entries[:20]:
            line = f'{zh},{en}'
            if chars + len(line) + 1 > max_chars:
                break
            kept.append(line)
            chars += len(line) + 1
            terms += 1
    # Prepend header
    out = 'Chinese,English\n' + '\n'.join(kept)
    return out

# Clean input (remove junk)
def clean_input(text):
    import re
    # Remove only obvious junk: URLs and lines that are clearly navigation/UI, but keep as much as possible
    lines = text.splitlines()
    keep = []
    for line in lines:
        l = line.strip()
        # Remove URLs
        if re.match(r'https?://', l) or l.startswith('www.'):
            continue
        # Remove lines that are only numbers or single symbols (pagination, votes, etc.)
        if re.match(r'^[0-9一二三四五六七八九十百千万]+$', l):
            continue
        # Remove lines that are only feedback/navigation/ads
        if l in ['上一章', '下一章', '目录', '旧版', '反 馈', '举报', '书详情', '在书架', '投票', '夜间', '设置', '客户端', '首页']:
            continue
        # Remove lines that are only UI icons
        if re.match(r'^[]+$', l):
            continue
        keep.append(l)
    return '\n'.join(keep)

# OpenAI translation (two-pass)
def openai_translate(prompt, text):
    # Modern OpenAI API call (gpt-5-mini, openai>=1.0.0)
    # Token counting and truncation
    try:
        enc = tiktoken.encoding_for_model(MODEL_NAME)
    except Exception:
        enc = tiktoken.get_encoding('cl100k_base')
    prompt_tokens = len(enc.encode(prompt))
    text_tokens = len(enc.encode(text))
    total_tokens = prompt_tokens + text_tokens
    # Use configured model name
    model = MODEL_NAME
    model_context = MODEL_CONTEXT
    max_completion = MAX_COMPLETION
    logging.info(f'Prompt tokens: {prompt_tokens}, Input tokens: {text_tokens}, Total: {total_tokens}')
    # Truncate input if needed: allowed_input = model_context - prompt_tokens - max_completion
    allowed_input = model_context - prompt_tokens - max_completion
    # If allowed_input is too small (or non-positive), prefer preserving the
    # source text rather than truncating it to zero. Reduce the completion
    # budget so the model still receives the chapter text instead of an
    # empty input which yields empty assistant responses.
    if allowed_input <= 32:
        logging.warning('Allowed input tokens too small for prompt; preserving full input and reducing completion size.')
        # Reduce completion budget to a small value to make room for input
        max_completion = min(max_completion, 128)
        # Do not truncate the input in this case; allow sending the full text
        allowed_input = text_tokens
    if text_tokens > allowed_input:
        truncated = enc.decode(enc.encode(text)[:allowed_input])
        logging.warning(f'Input truncated from {text_tokens} to {allowed_input} tokens to fit context window.')
        text = truncated

    def _extract_text_from_responses_api(resp):
        # openai>=2.x provides `output_text` convenience
        out = getattr(resp, 'output_text', None)
        if isinstance(out, str) and out.strip():
            return out
        # Fallback: walk output items
        try:
            parts = []
            for item in getattr(resp, 'output', []) or []:
                for c in getattr(item, 'content', []) or []:
                    t = getattr(c, 'text', None)
                    if isinstance(t, str) and t:
                        parts.append(t)
            joined = ''.join(parts)
            return joined
        except Exception:
            return ''

    def _extract_text_from_chat_completions(resp):
        content = ''
        try:
            if getattr(resp, 'choices', None):
                choice0 = resp.choices[0]
                content = getattr(getattr(choice0, 'message', {}), 'content', '') or ''
            else:
                content = (resp.get('choices', [{}])[0].get('message', {}).get('content', '') if isinstance(resp, dict) else '')
        except Exception:
            content = ''
        return content

    # Retry logic for empty responses
    attempts = 3
    backoff = 1
    for attempt in range(1, attempts + 1):
        try:
            # Prefer the Responses API for GPT-5 models; we've observed Chat Completions
            # sometimes returns empty `message.content` while spending tokens on reasoning.
            use_responses = hasattr(openai_client, 'responses') and str(model).startswith('gpt-5')
            if use_responses:
                response = openai_client.responses.create(
                    model=model,
                    instructions=prompt,
                    input=text,
                    max_output_tokens=max_completion,
                )
                content = _extract_text_from_responses_api(response)
            else:
                response = openai_client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": text}
                    ],
                    max_completion_tokens=max_completion
                )
                content = _extract_text_from_chat_completions(response)
            if content and content.strip():
                return content
            # Empty content: log response and retry unless last attempt
            logging.error(f'OpenAI returned empty content (attempt {attempt}). Full response repr logged for debugging.')
            try:
                logging.error(repr(response))
            except Exception:
                logging.error('Failed to repr OpenAI response')
            if attempt < attempts:
                time.sleep(backoff)
                backoff *= 2
                continue
            # final failure
            return ''
        except Exception as e:
            logging.error(f'OpenAI API error on attempt {attempt}: {e}')
            if attempt < attempts:
                time.sleep(backoff)
                backoff *= 2
                continue
            raise


def detect_model_context(model_name='gpt-5-mini'):
    """Try to detect the model's context window via the OpenAI Models API.
    This will set the global MODEL_CONTEXT and adjust MAX_COMPLETION to a
    reasonable fraction of the detected context if successful.
    """
    global MODEL_CONTEXT, MAX_COMPLETION
    try:
        info = None
        # Try retrieve first (may fail if model id isn't directly retrievable)
        try:
            info = openai_client.models.retrieve(model_name)
        except Exception:
            # fallback to listing models and matching by id substring
            res = openai_client.models.list()
            for m in getattr(res, 'data', []) or []:
                mid = getattr(m, 'id', '') or m.get('id') if isinstance(m, dict) else ''
                if model_name in mid:
                    info = m
                    break
        if not info:
            logging.warning(f'No model info found for {model_name}')
            return
        # Gather possible numeric context values from common fields
        candidates = []
        # dict-like access
        if isinstance(info, dict):
            for k, v in info.items():
                if isinstance(v, int) and 'context' in k.lower():
                    candidates.append(v)
        # attribute access
        for attr in ['context_window', 'context_length', 'context_size', 'context_tokens', 'max_input_tokens', 'input_tokens']:
            val = getattr(info, attr, None)
            if isinstance(val, int):
                candidates.append(val)
        # metadata or nested fields
        meta = getattr(info, 'metadata', None) if hasattr(info, 'metadata') else None
        if isinstance(meta, dict):
            for k, v in meta.items():
                if isinstance(v, int) and 'context' in k.lower():
                    candidates.append(v)
        if candidates:
            detected = max(candidates)
            MODEL_CONTEXT = int(detected)
            # pick a safe completion size (25% of context) but cap reasonably
            MAX_COMPLETION = min(int(MODEL_CONTEXT * 0.25), 8192)
            logging.info(f'Detected model context {MODEL_CONTEXT} for {model_name} (candidates: {candidates})')
            logging.info(f'Set MODEL_CONTEXT={MODEL_CONTEXT}, MAX_COMPLETION={MAX_COMPLETION}')
        else:
            logging.warning(f'Could not detect model context for {model_name}; keeping defaults')
    except Exception as e:
        logging.warning(f'Error while detecting model context: {e}')

# Append new glossary entries
def update_glossary(new_entries):
    # new_entries: list of (Chinese, English)
    if not MASTER_GLOSSARY.exists():
        MASTER_GLOSSARY.parent.mkdir(parents=True, exist_ok=True)
        MASTER_GLOSSARY.write_text("", encoding="utf-8")
    lines = MASTER_GLOSSARY.read_text(encoding='utf-8').splitlines()
    if not lines:
        lines = ["Chinese,English"]
        MASTER_GLOSSARY.write_text("Chinese,English\n", encoding="utf-8")
    header = lines[0]
    existing = set(l.split(',')[0] for l in lines[1:] if ',' in l)
    to_append = []
    for zh, en in new_entries:
        if zh not in existing:
            to_append.append(f'{zh},{en}')
    if to_append:
        with MASTER_GLOSSARY.open('a', encoding='utf-8') as f:
            for line in to_append:
                f.write('\n' + line)
        logging.info(f'Appended {len(to_append)} new glossary entries')

# Utility: derive a filename-safe slug from the translated chapter title
def _slugify_title(title_line: str, chapter_num: int) -> str:
    """Return a lowercased, underscore-separated slug from the English chapter title.
    e.g. 'Chapter 535 - Celebrating for Island Master Li' -> 'celebrating_for_island_master_li'
    Returns '' if no meaningful title beyond the chapter number."""
    import re
    # Strip leading "Chapter N" prefix and separator
    slug = re.sub(r'^Chapter\s*\d+\s*[-–—:：]?\s*', '', title_line, flags=re.IGNORECASE).strip()
    if not slug:
        return ''
    # Keep only ASCII alphanumeric, spaces, underscores, hyphens
    slug = re.sub(r'[^a-zA-Z0-9\s_-]', '', slug)
    # Collapse whitespace / hyphens to underscores
    slug = re.sub(r'[\s-]+', '_', slug).strip('_').lower()
    # Limit length
    if len(slug) > 50:
        slug = slug[:50].rsplit('_', 1)[0]
    return slug


# Save debug and xhtml files
def save_debug_and_xhtml(chapter_num, pass1, pass2):
    import re
    lines = [l.strip() for l in pass2.splitlines() if l.strip()]
    title_line = lines[0] if lines else f'Chapter {chapter_num}'
    # Build base name with optional title slug
    slug = _slugify_title(title_line, chapter_num)
    base = f'chapter_{chapter_num}_{slug}' if slug else f'chapter_{chapter_num}'
    pass1_path = DEBUG / f'{base}_pass1.txt'
    pass2_path = DEBUG / f'{base}_pass2.txt'
    xhtml_path = TRANSLATED / f'{base}.xhtml'
    pass1_path.write_text(pass1, encoding='utf-8')
    pass2_path.write_text(pass2, encoding='utf-8')

    # --- Formatting: Markdown bold to <b>, paragraphs to <p>, and title to <h1> ---
    def md_bold_to_html(text):
        return re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    paras = [p.strip() for p in pass2.split('\n') if p.strip()]
    # If the first paragraph equals the title_line, skip it for body (we'll render as <h1>)
    body_paras = paras[1:] if paras and paras[0].strip() == title_line.strip() else paras
    formatted_paras = '\n'.join(f'<p>{md_bold_to_html(p)}</p>' for p in body_paras)
    # Title rendered as H1 for PDF styling
    title_html = f'<h1 class="chapter-title">{md_bold_to_html(title_line)}</h1>'

    xhtml = f'<?xml version="1.0" encoding="utf-8"?>\n<html xmlns="http://www.w3.org/1999/xhtml"><head><title>{title_line}</title></head><body>\n{title_html}\n{formatted_paras}\n</body></html>'
    xhtml_path.write_text(xhtml, encoding='utf-8')
    return base


# Build PDF from xhtml files
def build_pdf():
    try:
        files = list(TRANSLATED.glob('*.xhtml'))
        # Extract numeric chapter from file content (title) or filename, then sort
        import re
        numbered = []
        for f in files:
            try:
                txt = f.read_text(encoding='utf-8')
                m = re.search(r'Chapter\s*(\d+)', txt)
                if m:
                    num = int(m.group(1))
                else:
                    # fallback to filename: find first group of digits in the stem
                    m2 = re.search(r'(\d{1,6})', f.stem)
                    num = int(m2.group(1)) if m2 else 10**9
            except Exception:
                num = 10**9
            numbered.append((num, f))
        numbered.sort(key=lambda x: x[0])
        files_sorted = [f for _, f in numbered]
        logging.info(f'Found {len(files_sorted)} chapter files for PDF. Order: {[p.name for p in files_sorted]}')
        html_parts = []
        toc_items = []
        for f in files_sorted:
            content = f.read_text(encoding='utf-8')
            # extract inner body if present
            mbody = re.search(r'<body[^>]*>(.*?)</body>', content, re.DOTALL | re.IGNORECASE)
            inner = mbody.group(1) if mbody else content
            # find chapter title (h1.chapter-title or <title>)
            mtitle = re.search(r'<h1[^>]*class="chapter-title"[^>]*>(.*?)</h1>', inner, re.DOTALL | re.IGNORECASE)
            if mtitle:
                title = re.sub(r'<[^>]+>', '', mtitle.group(1)).strip()
            else:
                mtitle2 = re.search(r'<title>(.*?)</title>', content, re.IGNORECASE | re.DOTALL)
                if mtitle2:
                    title = mtitle2.group(1).strip()
                else:
                    # Derive clean title from filename: {story}_chapter_{N}_{slug}
                    m_fn = re.search(r'_chapter_(\d+)(?:_(.+))?$', f.stem)
                    if m_fn:
                        title = f'Chapter {m_fn.group(1)}'
                        if m_fn.group(2):
                            title += ' - ' + m_fn.group(2).replace('_', ' ').title()
                    else:
                        m_num = re.search(r'(\d{1,6})', f.stem)
                        title = f'Chapter {m_num.group(1)}' if m_num else f.stem.replace('_', ' ').title()
            # ensure an anchor id exists for the chapter (use numeric if available)
            mnum = re.search(r'(\d{1,6})', f.stem)
            chap_id = f'chapter-{mnum.group(1) if mnum else f.stem}'
            # wrap inner content in a div with the anchor id
            chapter_div = f'<div id="{chap_id}">\n' + inner + '\n</div>'
            html_parts.append(chapter_div)
            toc_items.append((chap_id, title))
        if not html_parts:
            logging.warning('No chapters found to add to PDF. PDF will be empty!')
        # Combine all chapters into one HTML document with CSS for font and spacing
        css = '''
        <style>
        @page { size: A4; margin: 1in; }
        body { font-family: "Calibri", "Segoe UI", Tahoma, Arial, sans-serif; font-size: 12pt; line-height: 1.15; }
        .chapter-title { font-size: 18pt; text-decoration: underline; font-weight: bold; margin: 0 0 0.8em 0; text-align: left; }
        p { margin: 0 0 0.9em 0; text-align: justify; }
        b, strong { font-weight: bold; }
        hr.pagebreak { page-break-after: always; border: none; }
        /* Table of Contents styling */
        .toc { margin: 0 0 1.2em 0; font-size: 12pt; line-height: 1.35; }
        .toc h1 { font-size: 20pt; margin: 0 0 0.8em 0; }
        .toc ul { list-style: none; padding: 0; margin: 0 0 0 0.25in; }
        .toc li { margin: 0; padding: 0.18em 0; }
        /* Make TOC entries look like links */
        .toc a,
        .toc a:link,
        .toc a:visited { display: block; color: blue; text-decoration: underline; padding: 0.05em 0; }
        /* Dot-leaders + page numbers (WeasyPrint supports leader() + target-counter()) */
        .toc a::after { content: leader(dotted) ' ' target-counter(attr(href), page); }
        </style>
        '''
        # Build a Table of Contents HTML block with links to chapter anchors
        toc_entries = []
        for cid, title in toc_items:
            toc_entries.append(f'<li><a href="#{cid}"><span class="toc-title">{title}</span></a></li>')
        toc_html = '<div class="toc"><h1>Table of Contents</h1><ul>' + '\n'.join(toc_entries) + '</ul></div>\n<hr class="pagebreak">\n'
        full_html = '<html><head><meta charset="utf-8">' + css + '</head><body>' + toc_html + '\n<hr class="pagebreak">\n'.join(html_parts) + '</body></html>'
        # Import and call WeasyPrint while native stderr is suppressed to hide GLib/GIO warnings
        with suppress_native_stderr():
            try:
                from weasyprint import HTML
                HTML(string=full_html).write_pdf(str(PDF_FILE))
            except Exception as e:
                # If PDF generation fails, re-raise after restoring stderr so we see the error
                raise
        logging.info(f'PDF rebuilt and written to {PDF_FILE}')
    except Exception as e:
        logging.error(f'PDF build failed: {e}')


def build_epub():
    """Build an EPUB from the translated XHTML chapter files using ebooklib."""
    try:
        import re
        from ebooklib import epub

        files = list(TRANSLATED.glob('*.xhtml'))
        numbered = []
        for f in files:
            try:
                txt = f.read_text(encoding='utf-8')
                m = re.search(r'Chapter\s*(\d+)', txt)
                if m:
                    num = int(m.group(1))
                else:
                    m2 = re.search(r'(\d{1,6})', f.stem)
                    num = int(m2.group(1)) if m2 else 10**9
            except Exception:
                num = 10**9
            numbered.append((num, f))
        numbered.sort(key=lambda x: x[0])
        files_sorted = [f for _, f in numbered]
        logging.info(f'Building EPUB from {len(files_sorted)} chapters')

        if not files_sorted:
            logging.warning('No chapters found for EPUB build')
            return

        book = epub.EpubBook()
        book.set_identifier(STORY_NAME)
        book.set_title(STORY_TITLE)
        book.set_language('en')
        book.add_author('Angry Squid')

        # CSS for epub chapters and TOC
        epub_css = epub.EpubItem(
            uid='style',
            file_name='style/default.css',
            media_type='text/css',
            content=b'''
            body { font-family: serif; font-size: 1em; line-height: 1.4; }
            .chapter-title { font-size: 1.4em; text-decoration: underline; font-weight: bold; margin: 0 0 0.8em 0; }
            p { margin: 0 0 0.9em 0; text-align: justify; }
            b, strong { font-weight: bold; }
            /* Table of Contents styling */
            .toc h1 { font-size: 1.6em; font-weight: bold; margin: 0 0 0.6em 0; }
            .toc ul { list-style: none; padding: 0; margin: 0; }
            .toc li { margin: 0; padding: 0.25em 0; }
            .toc a { color: blue; text-decoration: underline; }
            '''
        )
        book.add_item(epub_css)

        spine = ['nav']
        toc_links = []
        chapters = []

        for f in files_sorted:
            content = f.read_text(encoding='utf-8')
            # Extract title
            mtitle = re.search(r'<h1[^>]*class="chapter-title"[^>]*>(.*?)</h1>', content, re.DOTALL | re.IGNORECASE)
            if mtitle:
                title = re.sub(r'<[^>]+>', '', mtitle.group(1)).strip()
            else:
                mtitle2 = re.search(r'<title>(.*?)</title>', content, re.IGNORECASE | re.DOTALL)
                if mtitle2:
                    title = mtitle2.group(1).strip()
                else:
                    # Derive clean title from filename: {story}_chapter_{N}_{slug}
                    m_fn = re.search(r'_chapter_(\d+)(?:_(.+))?$', f.stem)
                    if m_fn:
                        title = f'Chapter {m_fn.group(1)}'
                        if m_fn.group(2):
                            title += ' - ' + m_fn.group(2).replace('_', ' ').title()
                    else:
                        m_num = re.search(r'(\d{1,6})', f.stem)
                        title = f'Chapter {m_num.group(1)}' if m_num else f.stem.replace('_', ' ').title()

            # Extract body inner
            mbody = re.search(r'<body[^>]*>(.*?)</body>', content, re.DOTALL | re.IGNORECASE)
            inner = mbody.group(1) if mbody else content

            chapter_file = f'chapter_{f.stem}.xhtml'
            ch = epub.EpubHtml(title=title, file_name=chapter_file, lang='en')
            ch.content = f'<html><head><link rel="stylesheet" href="style/default.css"/></head><body>{inner}</body></html>'
            ch.add_item(epub_css)
            book.add_item(ch)
            chapters.append(ch)
            toc_links.append((chapter_file, title))

        # Spine: nav then chapters
        for ch in chapters:
            spine.append(ch)

        # Navigation TOC (NCX + Nav)
        book.toc = [epub.Link(cf, t, f'toc_{i}') for i, (cf, t) in enumerate(toc_links)]
        book.spine = spine
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())

        COMPILED_BOOKS.mkdir(parents=True, exist_ok=True)
        epub.write_epub(str(EPUB_FILE), book, {})
        logging.info(f'EPUB rebuilt and written to {EPUB_FILE}')
    except Exception as e:
        logging.error(f'EPUB build failed: {e}')


def sync_translated_with_processed(dry_run=False):
    """Rename existing translated XHTML files to match processed filenames when possible.
    For each xhtml in TRANSLATED, extract chapter number from the file content and try
    to find a processed file in PROCESSED whose stem contains that number. If found,
    rename the xhtml to match the processed stem (with .xhtml extension).
    """
    import re
    renames = []
    xfiles = list(TRANSLATED.glob('*.xhtml'))
    pfiles = list(PROCESSED.glob('*.txt'))
    pmap = {p.stem: p for p in pfiles}
    for xf in xfiles:
        try:
            txt = xf.read_text(encoding='utf-8')
        except Exception:
            continue
        # prefer an explicit numeric chapter in the xhtml content/title
        m = re.search(r'Chapter\s*(\d+)', txt)
        num = None
        if m:
            num = m.group(1)
        else:
            # attempt to extract a short title line and parse English number words
            tline = None
            mtitle = re.search(r'<title>(.*?)</title>', txt, re.IGNORECASE|re.DOTALL)
            if mtitle:
                tline = mtitle.group(1)
            else:
                lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]
                tline = lines[0] if lines else ''
            if tline:
                # simple english number word parser
                def words_to_int(s):
                    s = s.lower().replace('-', ' ')
                    tokens = [w for w in re.split(r"[^a-z]+", s) if w]
                    units = {'zero':0,'one':1,'two':2,'three':3,'four':4,'five':5,'six':6,'seven':7,'eight':8,'nine':9,'ten':10,'eleven':11,'twelve':12,'thirteen':13,'fourteen':14,'fifteen':15,'sixteen':16,'seventeen':17,'eighteen':18,'nineteen':19}
                    tens = {'twenty':20,'thirty':30,'forty':40,'fifty':50,'sixty':60,'seventy':70,'eighty':80,'ninety':90}
                    scales = {'hundred':100,'thousand':1000,'million':1000000}
                    total = 0
                    current = 0
                    for tok in tokens:
                        if tok in units:
                            current += units[tok]
                        elif tok in tens:
                            current += tens[tok]
                        elif tok in scales:
                            if current == 0:
                                current = 1
                            current *= scales[tok]
                            total += current
                            current = 0
                        else:
                            # ignore unknown words
                            pass
                    return total + current if (total + current) > 0 else None
                parsed = words_to_int(tline)
                num = str(parsed) if parsed else None
            # last resort: find any digits in filename stem
            if not num:
                m2 = re.search(r'(\d{1,6})', xf.stem)
                num = m2.group(1) if m2 else None
        if not num:
            continue
        # find a processed file whose stem contains this number
        candidate = None
        for stem in pmap:
            if str(num) in stem:
                candidate = stem
                break
        if candidate:
            target = TRANSLATED / (candidate + '.xhtml')
            if target.exists():
                logging.info(f'Target {target.name} already exists; skipping rename for {xf.name}')
                continue
            if dry_run:
                renames.append((xf.name, target.name))
            else:
                try:
                    xf.rename(target)
                    renames.append((xf.name, target.name))
                    logging.info(f'Renamed {xf.name} -> {target.name}')
                except Exception as e:
                    logging.error(f'Failed to rename {xf.name} -> {target.name}: {e}')
    return renames

# Process a single queue file
def process_queue_file(qfile):
    try:
        raw = qfile.read_text(encoding='utf-8')
        cleaned = clean_input(raw)
        # Log start of processing (chapter may be extracted below)
        logging.info(f'Queue file ready for processing: {qfile.name}')
        # Skip if cleaned input is empty or matches fallback template
        fallback_templates = [
            "I didn’t receive any chapter text. Please paste the chapter title and the story body (only those — no headers, navigation, comments, author notes, or URLs).",
            "I don't see any chapter text. Please paste the Chinese chapter (only the chapter title and story body). Before you send it, please make sure:",
        ]
        if not cleaned.strip() or any(cleaned.strip().startswith(t) for t in fallback_templates):
            logging.warning(f'Skipping {qfile.name}: empty or fallback template detected.')
            move_queue_file(qfile, FAILED)
            return False
        glossary, instructions = load_master_files()
        # Build a relevant glossary subset for this chapter to keep prompts small
        relevant_glossary = build_relevant_glossary(cleaned, max_chars=2000)
        logging.info(f'Using {len(relevant_glossary.splitlines())-1} glossary entries for this chapter')
        # Prepare tokenizer
        try:
            enc = tiktoken.encoding_for_model('gpt-5-mini')
        except Exception:
            enc = tiktoken.get_encoding('cl100k_base')
        # Extract chapter number and title from cleaned input (e.g., '第四百九十八章 为李岛主贺')
        import re
        # If the queue filename preserved the original input name, prefer extracting
        # the chapter number from that filename so debug/processed names stay consistent.
        parts_q = qfile.name.split('__', 2)
        orig_name_from_queue = parts_q[2] if len(parts_q) == 3 else None
        chapter_num_from_filename = None
        if orig_name_from_queue:
            # Prefer _chapter_N pattern, fall back to bare digits
            mfn = re.search(r'_chapter_(\d+)', orig_name_from_queue)
            if not mfn:
                mfn = re.search(r'(\d{1,6})', orig_name_from_queue)
            if mfn:
                try:
                    chapter_num_from_filename = int(mfn.group(1))
                except Exception:
                    chapter_num_from_filename = None

        match = re.search(r'第([一二三四五六七八九零十百千万0-9]+)章[\s:：-]*([\S ]+)?', cleaned)
        def chinese_to_arabic(cn):
            cn_num = {'零':0,'一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10,'百':100,'千':1000,'万':10000}
            if cn.isdigit():
                return int(cn)
            total = 0
            unit = 1
            num = 0
            for c in reversed(cn):
                if c in cn_num:
                    val = cn_num[c]
                    if val >= 10:
                        if num == 0:
                            num = 1
                        unit = val
                    else:
                        num = val
                else:
                    continue
                if unit > 1:
                    total += num * unit
                    num = 0
                    unit = 1
            total += num
            return total
        if match:
            cn_chap = match.group(1)
            chapter_num = chinese_to_arabic(cn_chap)
            chapter_title = match.group(2).strip() if match.group(2) else f'Chapter {chapter_num}'
        else:
            chapter_num = get_next_chapter_number()
            chapter_title = f'Chapter {chapter_num}'

        # If the original filename contained a numeric chapter, prefer it as authoritative
        if chapter_num_from_filename:
            chapter_num = chapter_num_from_filename
            # if no explicit title was parsed, derive a fallback from filename (without extension)
            if not chapter_title or chapter_title.startswith('Chapter'):
                chapter_title = Path(orig_name_from_queue).stem
        logging.info(f'Now translating: chapter_{chapter_num} (queue: {qfile.name})')
        if EXE_MODE:
            print(f'  Translating Chapter {chapter_num}...')
        # First pass
        prompt1 = instructions + '\n\n' + relevant_glossary
        logging.info(f'Cleaned input length: {len(cleaned)} chars')
        logging.info(f'Prompt1 length: {len(prompt1)} chars')

        # Token-aware handling: compute prompt and input token sizes
        prompt_tokens = len(enc.encode(prompt1))
        input_tokens = len(enc.encode(cleaned))
        allowed_input = MODEL_CONTEXT - prompt_tokens - MAX_COMPLETION - TOKEN_SAFETY_MARGIN

        if allowed_input <= 0:
            # Prompt itself is too large to fit with desired completion.
            # Reset to a short prompt (ask for a new shorter input implicitly).
            logging.warning('Prompt+glossary too large for model context. Switching to short prompt.')
            # Per user request: always include the full instruction file in the
            # prompt. The glossary is already trimmed to relevant entries, so
            # use `instructions` + relevant_glossary as the short prompt.
            prompt_short = instructions + '\n\n' + relevant_glossary
            logging.info(f'Using short prompt length: {len(prompt_short)} chars')
            # Translate by chunking the input (preserve full chapter): pick a
            # per-call token chunk size that gives the model room for the short
            # prompt and a small completion. If the computed per-call allowance
            # is too small or negative, fall back to a conservative chunk size.
            def chunk_text_by_tokens(text, enc, max_toks):
                toks = enc.encode(text)
                for i in range(0, len(toks), max_toks):
                    yield enc.decode(toks[i:i+max_toks])

            prompt_short_tokens = len(enc.encode(prompt_short))
            per_call_allowed = MODEL_CONTEXT - prompt_short_tokens - MAX_COMPLETION - TOKEN_SAFETY_MARGIN
            if per_call_allowed <= 128:
                per_call_allowed = 1024
            parts = list(chunk_text_by_tokens(cleaned, enc, per_call_allowed))
            pass1_parts = []
            for idx, part in enumerate(parts, 1):
                logging.info(f'Translating short-prompt chunk {idx}/{len(parts)} (chars: {len(part)})')
                p = openai_translate(prompt_short, part)
                if not p.strip():
                    logging.error(f'Empty first-pass for short-prompt chunk {idx}; aborting.')
                    raise RuntimeError('OpenAI first pass returned empty content for short-prompt chunk')
                pass1_parts.append(p)
            pass1 = '\n'.join(pass1_parts)
        else:
            # If input is bigger than allowed, chunk it into pieces that fit
            if input_tokens > allowed_input:
                logging.info(f'Input tokens {input_tokens} exceed allowed {allowed_input}; chunking input.')
                # chunk by token count
                def chunk_text_by_tokens(text, enc, max_toks):
                    toks = enc.encode(text)
                    for i in range(0, len(toks), max_toks):
                        yield enc.decode(toks[i:i+max_toks])
                parts = list(chunk_text_by_tokens(cleaned, enc, allowed_input))
                pass1_parts = []
                for idx, part in enumerate(parts, 1):
                    logging.info(f'Translating chunk {idx}/{len(parts)} (chars: {len(part)})')
                    p = openai_translate(prompt1, part)
                    if not p.strip():
                        logging.error(f'Empty first-pass for chunk {idx}; aborting.')
                        raise RuntimeError('OpenAI first pass returned empty content for chunk')
                    pass1_parts.append(p)
                # join chunk translations into one combined pass1
                pass1 = '\n'.join(pass1_parts)
            else:
                pass1 = openai_translate(prompt1, cleaned)
        if not pass1.strip():
            logging.error(f'OpenAI first pass returned empty content. Cleaned input: {cleaned[:500]}...')
            logging.error(f'Prompt1 (first 500 chars): {prompt1[:500]}...')
            raise RuntimeError('OpenAI first pass returned empty content')
        # Single-pass workflow: treat the first-pass result as the final output
        pass2 = pass1
        # Remove Chinese characters from the final chapter body, but preserve
        # any NEW GLOSSARY: block long enough to extract new entries. Write a
        # raw pass2 debug file (including NEW GLOSSARY) for auditing, then
        # append new glossary entries to the master glossary and remove the
        # glossary block from the saved chapter so it won't appear in PDFs.
        try:
            if 'NEW GLOSSARY:' in pass2:
                body, gloss_block = pass2.split('NEW GLOSSARY:', 1)
                gloss_block = gloss_block.strip()
            else:
                body = pass2
                gloss_block = None
            import re
            # Remove CJK Unified Ideographs and related ranges from body
            body_clean = re.sub(r'[\u4E00-\u9FFF\u3400-\u4DBF\uF900-\uFAFF]', '', body)
            # Remove common Chinese punctuation and bracket symbols but keep ASCII punctuation
            body_clean = re.sub(r'[【】「」『』《》〈〉—–…·，。！？；：、（）￥]', '', body_clean)
            # Collapse multiple blank lines
            body_clean = re.sub(r'\n{3,}', '\n\n', body_clean).strip()
            # Write raw pass2 to debug for record (includes glossary if present)
            raw_pass2 = (body + '\n\nNEW GLOSSARY:\n' + gloss_block) if gloss_block else body
            # Derive the title slug from body so raw debug file matches
            _raw_lines = [l.strip() for l in body.splitlines() if l.strip()]
            _raw_title = _raw_lines[0] if _raw_lines else f'Chapter {chapter_num}'
            _raw_slug = _slugify_title(_raw_title, chapter_num)
            _raw_base = f'chapter_{chapter_num}_{_raw_slug}' if _raw_slug else f'chapter_{chapter_num}'
            try:
                raw_path = DEBUG / f'{_raw_base}_pass2_raw.txt'
                raw_path.write_text(raw_pass2, encoding='utf-8')
            except Exception:
                pass
            # If there's a glossary block, extract and append new entries, then
            # remove the glossary block from the final saved chapter text.
            if gloss_block:
                new_entries = []
                for line in gloss_block.splitlines():
                    if ',' in line:
                        zh, en = line.split(',', 1)
                        new_entries.append((zh.strip(), en.strip()))
                if new_entries:
                    update_glossary(new_entries)
                pass2 = body_clean
            else:
                pass2 = body_clean
        except Exception as e:
            logging.warning(f'Failed to strip Chinese characters or process NEW GLOSSARY: {e}')
        # Save standardized debug and xhtml files (uses English title slug)
        base = save_debug_and_xhtml(chapter_num, pass1, pass2)
        # Use the same base name (with title slug) for the processed file
        processed_name = f'{base}.txt'

        # Move the queue file to processed
        processed_path = PROCESSED / processed_name
        shutil.move(str(qfile), str(processed_path))
        logging.info(f'Translated {processed_name} as chapter {chapter_num}')
        logging.info(f'Translated: {base} (moved to processed)')
        return True
    except Exception as e:
        logging.error(f'Failed to process {qfile.name}: {e}')
        move_queue_file(qfile, FAILED)
        return False


# Process all queue files sequentially
def process_queue():
    # Sort queue files by the chapter number embedded in the original filename
    # Queue filename format: <timestamp>__<hash>__<original_filename>
    import re
    queue_files = list(QUEUE.glob('*.txt'))

    def _extract_chapter_num_from_queue_filename(name: str) -> int | None:
        parts = name.split('__', 2)
        candidate = parts[2] if len(parts) == 3 else name
        m = re.search(r'_chapter_(\d+)', candidate)
        if not m:
            m = re.search(r'(\d{1,6})', candidate)
        if not m:
            return None
        try:
            return int(m.group(1))
        except Exception:
            return None

    def _queue_key(q):
        parts = q.name.split('__', 2)
        candidate = parts[2] if len(parts) == 3 else q.name
        # look for _chapter_N pattern first, then fall back to bare digits
        m = re.search(r'_chapter_(\d+)', candidate)
        if not m:
            m = re.search(r'(\d{1,6})', candidate)
        if m:
            try:
                return int(m.group(1))
            except Exception:
                pass
        # fallback to timestamp prefix (YYYYMMDD_HHMMSS -> numeric)
        m2 = re.match(r'(\d{8})_(\d{6})', q.name)
        if m2:
            try:
                return int(m2.group(1) + m2.group(2))
            except Exception:
                pass
        # final fallback: keep lexicographic order
        return q.name

    queue_files.sort(key=_queue_key)
    if EXE_MODE:
        print(f'\n  {len(queue_files)} chapter(s) to translate.\n')
    failed_count = 0
    ok_durations: list[float] = []
    total_start = time.perf_counter()
    for qfile in queue_files:
        chap_num = _extract_chapter_num_from_queue_filename(qfile.name)
        one_start = time.perf_counter()
        ok = process_queue_file(qfile)
        one_elapsed = time.perf_counter() - one_start
        if not ok:
            failed_count += 1
            if EXE_MODE:
                label = f"Chapter {chap_num}" if chap_num is not None else qfile.name
                print(f'  [FAIL] {label} after {_format_duration(one_elapsed)}')
            else:
                label = f"Chapter {chap_num}" if chap_num is not None else qfile.name
                logging.info(f'{label} failed after {_format_duration(one_elapsed)}')
        else:
            ok_durations.append(one_elapsed)
            if EXE_MODE:
                label = f"Chapter {chap_num}" if chap_num is not None else qfile.name
                print(f'  [OK] Finished {label} in {_format_duration(one_elapsed)}')
            else:
                label = f"Chapter {chap_num}" if chap_num is not None else qfile.name
                logging.info(f'Finished {label} in {_format_duration(one_elapsed)}')

    total_elapsed = time.perf_counter() - total_start
    ok_count = len(ok_durations)
    avg = (sum(ok_durations) / ok_count) if ok_count else 0.0
    if EXE_MODE:
        if failed_count:
            print(f'\n  Translation finished with {failed_count} failure(s).')
            print(f'  See translator.log and raw_chapters/failed for details.\n')
        else:
            print('\n  Translation complete.\n')

        if ok_count:
            print(f'  Total time: {_format_duration(total_elapsed)}')
            print(f'  Average per chapter: {_format_duration(avg)} ({ok_count} chapter(s))\n')
        else:
            print(f'  Total time: {_format_duration(total_elapsed)}\n')
    else:
        if ok_count:
            logging.info(f'Total translation time: {_format_duration(total_elapsed)}')
            logging.info(f'Average per chapter: {_format_duration(avg)} ({ok_count} chapter(s))')
        else:
            logging.info(f'Total translation time: {_format_duration(total_elapsed)}')

    if failed_count:
        raise RuntimeError(f'Translation failed for {failed_count} chapter(s).')




# Translate all chapters in input_box
def translate_all():
    if enqueue_input_box():
        process_queue()


# Build only mode
def build_only():
    if EXE_MODE:
        print('\n  Compiling PDF and EPUB...')
    build_pdf()
    build_epub()
    if EXE_MODE:
        print('  Done!\n')


# Main CLI
def main():
    parser = argparse.ArgumentParser(description='Wuxia Translator')
    parser.add_argument('mode', choices=['translate', 'build'], help='Mode to run')
    args = parser.parse_args()
    setup_logging()
    ensure_folders()
    load_api_key()
    if args.mode == 'translate':
        translate_all()
    elif args.mode == 'build':
        build_only()

if __name__ == '__main__':
    main()
