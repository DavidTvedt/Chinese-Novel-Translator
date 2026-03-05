# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_submodules


# tiktoken loads encodings via the namespace package `tiktoken_ext` (e.g. cl100k_base lives in
# tiktoken_ext.openai_public). In onefile builds, these dynamic imports can be missed unless
# explicitly included.
_hidden = []
try:
    _hidden += collect_submodules('tiktoken_ext')
except Exception:
    _hidden += ['tiktoken_ext.openai_public']


a = Analysis(
    ['scripts\\cat.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='WuxiaTranslator',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
