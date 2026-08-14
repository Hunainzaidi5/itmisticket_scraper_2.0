# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['ticket_scraper.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('logo.ico', '.'),
        ('background.png', '.'),
        ('full_xpath.txt', '.'),
    ],
    hiddenimports=[
        'PyQt6.QtCore',
        'PyQt6.QtGui',
        'PyQt6.QtWidgets',
        'selenium',
        'webdriver_manager',
        'pandas',
        'psutil',
        'PIL',
        'PIL.Image',
        'PIL.ImageFilter',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'torch',
        'torchvision',
        'torchaudio',
        'pytorch_lightning',
        'torchmetrics',
        'lightning_fabric',
        'numba',
        'scipy',
        'networkx',
        'sqlalchemy',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='ITMIS_Ticket_Scraper',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='logo.ico'
)
