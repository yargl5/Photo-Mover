$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    $pythonRoot = python -c "import sys; print(sys.base_prefix)"
    & "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe" /nologo /target:exe /out:vendor\MediaDevices\MtpBridge.exe /reference:vendor\MediaDevices\MediaDevices.dll /reference:System.Web.Extensions.dll MtpBridge.cs WpdUpload.cs
    if ($LASTEXITCODE -ne 0) { throw 'MTP bridge compilation failed' }
    python -m PyInstaller --noconfirm --clean --onefile --windowed --name PhotoMoverMTP --additional-hooks-dir build_hooks --hidden-import=tkinter.ttk --hidden-import=tkinter.filedialog --hidden-import=tkinter.messagebox --hidden-import=_tkinter --add-binary "$pythonRoot\DLLs\_tkinter.pyd;." --add-binary "$pythonRoot\DLLs\tk86t.dll;." --add-binary "$pythonRoot\DLLs\tcl86t.dll;." --add-data "$pythonRoot\tcl\tk8.6;_tk_data" --add-data "$pythonRoot\tcl\tcl8.6;_tcl_data" --add-data 'vendor\MediaDevices;vendor\MediaDevices' PhotoMover.py
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed' }
    Copy-Item dist\PhotoMoverMTP.exe PhotoMover-MTP.exe -Force
} finally {
    Pop-Location
}
