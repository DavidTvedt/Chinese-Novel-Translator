import shutil
from pathlib import Path

ROOT = Path(__file__).parent.parent
LOG = ROOT / 'translator.log'
BACKUP = ROOT / f'translator.log.bak'

KEEP = ['ERROR', 'Failed', 'failed', 'Traceback', 'OpenAI API error']

def prune():
    if not LOG.exists():
        print('translator.log not found')
        return
    # backup first
    shutil.copy2(LOG, BACKUP)
    kept = []
    # Read with replacement for invalid bytes to avoid UnicodeDecodeError
    with LOG.open('r', encoding='utf-8', errors='replace') as f:
        for line in f:
            if any(k in line for k in KEEP):
                kept.append(line)
    # overwrite log with kept lines only
    with LOG.open('w', encoding='utf-8') as f:
        f.writelines(kept)
    print(f'Pruned translator.log: kept {len(kept)} lines. Backup at {BACKUP}')

if __name__ == '__main__':
    prune()
