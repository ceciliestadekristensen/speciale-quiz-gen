@echo off
chcp 65001 >nul
cd /d "%~dp0"
title QuizGen - installation

echo === Installerer QuizGen ===
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo FEJL: Python blev ikke fundet. Installer Python 3.10 eller nyere fra python.org
    echo og sæt flueben i "Add Python to PATH" under installationen.
    goto fejl
)
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
    echo FEJL: Python 3.10 eller nyere blev ikke fundet. Installer det fra python.org med flueben i "Add Python to PATH".
    goto fejl
)

where ollama >nul 2>&1
if errorlevel 1 (
    echo FEJL: Ollama blev ikke fundet. Installer Ollama fra ollama.com og kør installer.bat igen.
    goto fejl
)

set "TESS_OK="
where tesseract >nul 2>&1 && set "TESS_OK=1"
if exist "C:\Program Files\Tesseract-OCR\tesseract.exe" set "TESS_OK=1"
if exist "C:\Program Files (x86)\Tesseract-OCR\tesseract.exe" set "TESS_OK=1"
if not defined TESS_OK (
    echo ADVARSEL: Tesseract blev ikke fundet. QuizGen virker, men kan ikke læse tekst fra billeder i PDF'er.
    echo.
)

echo [1/4] Opretter Python-miljø...
if not exist ".venv\Scripts\python.exe" python -m venv .venv
if errorlevel 1 goto fejl

echo [2/4] Installerer Python-pakker...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto fejl

echo [3/4] Henter AI-modellen (ca. 5 GB, kan tage et stykke tid)...
curl -s http://127.0.0.1:11434/api/tags >nul 2>&1
if errorlevel 1 (
    start "" /min ollama serve
    timeout /t 5 /nobreak >nul
)
ollama pull qwen2.5:7b-instruct
if errorlevel 1 goto fejl

echo [4/4] Opretter genvej på skrivebordet...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\QuizGen.lnk'); $s.TargetPath='%~dp0start.bat'; $s.WorkingDirectory='%~dp0'; $s.Save()"

echo.
echo === QuizGen er installeret ===
echo Start programmet ved at dobbeltklikke på "QuizGen" på skrivebordet.
pause
exit /b 0

:fejl
echo.
echo Installationen stoppede på grund af en fejl. Se beskeden ovenfor.
pause
exit /b 1
