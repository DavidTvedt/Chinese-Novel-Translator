from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from translator import ensure_folders, load_api_key, process_queue_file, FAILED

if __name__ == '__main__':
    ensure_folders()
    load_api_key()
    # Retry the most recent chapter 535 failed file if present
    candidates = sorted(FAILED.glob('*__535.txt'))
    if not candidates:
        raise SystemExit('No failed 535 files found')
    path = candidates[-1]
    print(f'Retrying: {path.name}')
    process_queue_file(path)
    print('Done')
