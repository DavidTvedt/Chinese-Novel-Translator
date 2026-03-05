import sys
from pathlib import Path
proj_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(proj_root))
import translator

print('MODEL_NAME =', getattr(translator, 'MODEL_NAME', None))
print('MODEL_CONTEXT =', getattr(translator, 'MODEL_CONTEXT', None))
print('MAX_COMPLETION =', getattr(translator, 'MAX_COMPLETION', None))
