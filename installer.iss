; Inno Setup script - builds PDF-Studio-Setup.exe (run by GitHub Actions)
#ifndef AppVersion
  #define AppVersion "1.0"
#endif

[Setup]
AppId={{6E3B2C1A-8F4D-4C2B-9A1E-PDFSTUDIO0001}
AppName=PDF Studio
AppVersion={#AppVersion}
AppPublisher=PDF Studio
DefaultDirName={localappdata}\Programs\PDF Studio
DefaultGroupName=PDF Studio
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=Output
OutputBaseFilename=PDF-Studio-Setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\PDF Studio.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ChangesAssociations=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "dist\PDF Studio\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\PDF Studio"; Filename: "{app}\PDF Studio.exe"
Name: "{autodesktop}\PDF Studio"; Filename: "{app}\PDF Studio.exe"; Tasks: desktopicon

[Registry]
; adds PDF Studio to "Open with" for PDFs and images (current user only)
Root: HKCU; Subkey: "Software\Classes\PDFStudio.Document"; ValueType: string; ValueData: "PDF Document"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\PDFStudio.Document\DefaultIcon"; ValueType: string; ValueData: """{app}\PDF Studio.exe"",0"
Root: HKCU; Subkey: "Software\Classes\PDFStudio.Document\shell\open\command"; ValueType: string; ValueData: """{app}\PDF Studio.exe"" ""%1"""
Root: HKCU; Subkey: "Software\Classes\Applications\PDF Studio.exe"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "PDF Studio"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Applications\PDF Studio.exe\shell\open\command"; ValueType: string; ValueData: """{app}\PDF Studio.exe"" ""%1"""
Root: HKCU; Subkey: "Software\Classes\.pdf\OpenWithProgids"; ValueType: none; ValueName: "PDFStudio.Document"; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\.png\OpenWithProgids"; ValueType: none; ValueName: "PDFStudio.Document"; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\.jpg\OpenWithProgids"; ValueType: none; ValueName: "PDFStudio.Document"; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\.xps\OpenWithProgids"; ValueType: none; ValueName: "PDFStudio.Document"; Flags: uninsdeletevalue

[Run]
Filename: "{app}\PDF Studio.exe"; Description: "Open PDF Studio now"; Flags: nowait postinstall skipifsilent
