@echo off
rem Re-open in a window that never closes by itself, so any message stays visible
if /i not "%~1"=="run" (
  start "PDF Studio install" cmd /k call "%~f0" run
  exit /b
)
setlocal EnableExtensions
cd /d "%~dp0"
title PDF Studio - one-click install
set "LOG=%~dp0install_log.txt"
echo PDF Studio install log  %date% %time% > "%LOG%"
echo Folder: %CD% >> "%LOG%"
echo.
echo  ==================================================
echo    PDF Studio - building and installing the program
echo    (first time takes 5-10 minutes, needs internet)
echo    Progress is also written to install_log.txt
echo  ==================================================
echo.

rem ---------- 1. find or install Python ----------
set "PYCMD="
for %%V in (3.12 3.13 3.11 3.14 3.10) do call :trypy %%V
if not defined PYCMD call :trypath python
if not defined PYCMD call :trypath python3
if not defined PYCMD (
  echo [1/5] Python not found - installing Python 3.12, please wait ...
  echo Installing Python with winget >> "%LOG%"
  winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements >> "%LOG%" 2>&1
  if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set PYCMD="%LocalAppData%\Programs\Python\Python312\python.exe"
)
if not defined PYCMD (
  echo.
  echo  Could not find or install Python automatically.
  echo  Install Python 3.12 from https://www.python.org/downloads/
  echo  - tick "Add python.exe to PATH" - then run this file again.
  echo NO PYTHON >> "%LOG%"
  goto fail
)
echo [1/5] Using Python: %PYCMD%
echo Python: %PYCMD% >> "%LOG%"
%PYCMD% --version >> "%LOG%" 2>&1

rem ---------- 2. private environment + libraries ----------
if not exist ".venv\Scripts\python.exe" (
  echo [2/5] Creating private Python environment ...
  %PYCMD% -m venv .venv >> "%LOG%" 2>&1
)
if not exist ".venv\Scripts\python.exe" (
  echo  Could not create the Python environment.
  goto fail
)
set "VPY=%~dp0.venv\Scripts\python.exe"
echo [2/5] Downloading PDF libraries (a few minutes) ...
"%VPY%" -m pip install --upgrade pip >> "%LOG%" 2>&1
"%VPY%" -m pip install -r requirements.txt pyinstaller >> "%LOG%" 2>&1
if errorlevel 1 (
  echo  Library download failed - check your internet connection.
  goto fail
)

rem ---------- 3. build the .exe ----------
echo [3/5] Building PDF Studio.exe (2-5 minutes) ...
"%VPY%" -m PyInstaller --noconfirm --log-level WARN PDFStudio.spec >> "%LOG%" 2>&1
if not exist "dist\PDF Studio\PDF Studio.exe" (
  echo  Build failed.
  goto fail
)

rem ---------- 4. self-test every export format ----------
echo [4/5] Testing all export formats ...
set "ST=%TEMP%\pdfstudio_selftest"
if exist "%ST%" rmdir /s /q "%ST%"
"dist\PDF Studio\PDF Studio.exe" --selftest "%~dp0selftest.pdf" "%ST%"
if exist "%ST%\selftest_report.txt" (
  type "%ST%\selftest_report.txt"
  type "%ST%\selftest_report.txt" >> "%LOG%"
) else (
  echo  Self-test produced no report. >> "%LOG%"
)

rem ---------- 5. install to AppData + shortcuts + "Open with" ----------
set "DEST=%LocalAppData%\Programs\PDF Studio"
echo [5/5] Installing to %DEST% ...
taskkill /im "PDF Studio.exe" /f >nul 2>&1
robocopy "dist\PDF Studio" "%DEST%" /MIR /NFL /NDL /NJH /NJS /NP >> "%LOG%" 2>&1
if not exist "%DEST%\PDF Studio.exe" (
  echo  Could not copy the program to %DEST%.
  goto fail
)
set "EXE=%DEST%\PDF Studio.exe"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$w=New-Object -ComObject WScript.Shell; foreach($d in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))){ $s=$w.CreateShortcut((Join-Path $d 'PDF Studio.lnk')); $s.TargetPath=$env:EXE; $s.WorkingDirectory=$env:DEST; $s.IconLocation=$env:EXE+',0'; $s.Description='View, edit and convert PDF files'; $s.Save() }" >> "%LOG%" 2>&1

reg add "HKCU\Software\Classes\PDFStudio.Document" /ve /d "PDF Document" /f >nul 2>&1
reg add "HKCU\Software\Classes\PDFStudio.Document\DefaultIcon" /ve /d "\"%EXE%\",0" /f >nul 2>&1
reg add "HKCU\Software\Classes\PDFStudio.Document\shell\open\command" /ve /d "\"%EXE%\" \"%%1\"" /f >nul 2>&1
reg add "HKCU\Software\Classes\Applications\PDF Studio.exe\shell\open\command" /ve /d "\"%EXE%\" \"%%1\"" /f >nul 2>&1
reg add "HKCU\Software\Classes\Applications\PDF Studio.exe" /v FriendlyAppName /d "PDF Studio" /f >nul 2>&1
for %%E in (.pdf .xps .epub .png .jpg .jpeg .tif .tiff) do (
  reg add "HKCU\Software\Classes\%%E\OpenWithProgids" /v PDFStudio.Document /t REG_NONE /f >nul 2>&1
  reg add "HKCU\Software\Classes\Applications\PDF Studio.exe\SupportedTypes" /v %%E /d "" /f >nul 2>&1
)

echo INSTALLED OK >> "%LOG%"
echo.
echo  ==================================================
echo    DONE!  "PDF Studio" is on your Desktop and Start menu.
echo  ==================================================
echo.
start "" "%EXE%"
pause
exit /b 0

:trypy
if defined PYCMD exit /b 0
py -%1 -c "import sys" >nul 2>&1
if not errorlevel 1 set "PYCMD=py -%1"
exit /b 0

:trypath
if defined PYCMD exit /b 0
%1 -c "import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,14) else 1)" >nul 2>&1
if not errorlevel 1 set "PYCMD=%1"
exit /b 0

:fail
echo FAILED >> "%LOG%"
echo.
echo  *** Something went wrong. Details are in install_log.txt ***
echo  *** Tell Claude "check the log" and it will fix it.       ***
pause
exit /b 1
