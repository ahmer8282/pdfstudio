@echo off
title Uninstall PDF Studio
taskkill /im "PDF Studio.exe" /f >nul 2>&1
rmdir /s /q "%LocalAppData%\Programs\PDF Studio"
powershell -NoProfile -Command "Remove-Item -ErrorAction SilentlyContinue (Join-Path ([Environment]::GetFolderPath('Desktop')) 'PDF Studio.lnk'), (Join-Path ([Environment]::GetFolderPath('Programs')) 'PDF Studio.lnk')"
reg delete "HKCU\Software\Classes\PDFStudio.Document" /f >nul 2>&1
reg delete "HKCU\Software\Classes\Applications\PDF Studio.exe" /f >nul 2>&1
for %%E in (.pdf .xps .epub .png .jpg .jpeg .tif .tiff) do reg delete "HKCU\Software\Classes\%%E\OpenWithProgids" /v PDFStudio.Document /f >nul 2>&1
echo PDF Studio removed.
pause
