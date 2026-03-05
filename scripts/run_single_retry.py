from pathlib import Path
import logging
import sys
from pathlib import Path as _P
# Ensure project root is on sys.path so `import translator` works when running
# this script from the scripts/ directory.
proj_root = _P(__file__).resolve().parent.parent
sys.path.insert(0, str(proj_root))
import translator

translator.setup_logging()
translator.ensure_folders()
translator.load_api_key()
# Force small context to trigger short-prompt branch for testing
translator.MODEL_CONTEXT = 1024
translator.MAX_COMPLETION = 256
logging.info(f'Forcing MODEL_CONTEXT={translator.MODEL_CONTEXT}, MAX_COMPLETION={translator.MAX_COMPLETION}')
qpath = Path('raw_chapters/failed/20260223_234443__91c5a0c9__510.txt')
try:
    translator.process_queue_file(qpath)
    print('Done processing.')
except Exception as e:
    print('Processing raised exception:', repr(e))
