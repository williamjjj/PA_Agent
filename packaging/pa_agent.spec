# Build from the repository root with: pyinstaller packaging/pa_agent.spec
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

root = Path(SPECPATH).parent
datas = [(str(root / 'prompt_engineering'), 'prompt_engineering'),
         (str(root / 'pa_agent/gui/theme/dark.qss'), 'pa_agent/gui/theme'),
         (str(root / 'LICENSE'), '.'), (str(root / 'third_party'), 'third_party'),
         (str(root / 'integrations'), 'integrations')]
datas += collect_data_files('akshare') + collect_data_files('curl_cffi')
datas += collect_data_files('pa_agent.quant', include_py_files=True)
for package in ('pa-agent', 'tiktoken', 'openai', 'jsonschema', 'jsonschema-specifications',
                'PyQt6', 'PyQt6-Qt6', 'numpy', 'pandas', 'cryptography', 'akshare',
                'curl_cffi', 'yfinance', 'mcp', 'httpx-sse'):
    datas += copy_metadata(package)
hiddenimports = collect_submodules('pa_agent') + collect_submodules('tiktoken_ext')
hiddenimports += ['tvDatafeed','MetaTrader5','win32api','win32job','win32crypt',
                  'numpy','pandas','pyqtgraph','akshare','baostock','tushare','yfinance']
exclude = ['PySide6','PyQt5','PyQt6.QtWebEngineWidgets','PyQt6.QtWebEngineCore',
           'IPython','notebook','jupyter','matplotlib','scipy','pytest','hypothesis','tkinter']
a = Analysis([str(root/'packaging/gui_entry.py'), str(root/'packaging/cli_entry.py')],
             pathex=[str(root)], binaries=[], datas=datas, hiddenimports=hiddenimports,
             hookspath=[], excludes=exclude, noarchive=False)
# Qt uses Windows' unversioned ICU API. A foreign Poppler/Conda ICU found on
# the build machine's PATH exports versioned symbols and breaks QtCore import.
# Windows 10/11 supplies this DLL; do not redistribute an unrelated ICU build.
a.binaries = [item for item in a.binaries
              if Path(item[0]).name.lower() not in {'icuuc.dll', 'icudt78.dll'}]
pyz = PYZ(a.pure)
runtime_hooks = [s for s in a.scripts if s[0] not in {'gui_entry', 'cli_entry'}]
gui = EXE(pyz, runtime_hooks + [s for s in a.scripts if s[0] == 'gui_entry'], [], exclude_binaries=True, name='PA-Agent',
          debug=False, strip=False, upx=False, console=False)
cli = EXE(pyz, runtime_hooks + [s for s in a.scripts if s[0] == 'cli_entry'], [], exclude_binaries=True, name='pa-agent-cli',
          debug=False, strip=False, upx=False, console=True)
coll = COLLECT(gui, cli, a.binaries, a.datas, strip=False, upx=False, name='PA-Agent')
