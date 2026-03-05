import sys
from pathlib import Path
proj_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(proj_root))
import translator

translator.setup_logging()
translator.ensure_folders()
translator.load_api_key()

try:
    res = translator.openai_client.models.list()
    models = []
    for m in getattr(res, 'data', []) or []:
        mid = getattr(m, 'id', None) or (m.get('id') if isinstance(m, dict) else None)
        if mid:
            models.append(mid)
    print('\n'.join(models))
except Exception as e:
    print('Failed to list models:', e)
