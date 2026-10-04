@echo off
chcp 65001 >nul
cd /d "%~dp0"
title QuizGen

if not exist ".venv\Scripts\python.exe" (
    echo QuizGen er ikke installeret endnu. Kør installer.bat først.
    pause
    exit /b 1
)

echo Starter QuizGen...

rem Start Ollama, hvis den ikke allerede kører i baggrunden
curl -s http://127.0.0.1:11434/api/tags >nul 2>&1
if errorlevel 1 (
    start "" /min ollama serve
    timeout /t 5 /nobreak >nul
)

rem Åbn browseren, så snart serveren svarer (venter op til 60 sekunder)
start "" /b cmd /c "for /l %%i in (1,1,60) do (curl -s http://127.0.0.1:8000/ >nul 2>&1 && (start "" "http://127.0.0.1:8000/?v=%%random%%" & exit) || timeout /t 1 /nobreak >nul)"

echo.
echo QuizGen kører på http://127.0.0.1:8000
echo Luk dette vindue for at stoppe QuizGen.
echo.
".venv\Scripts\python.exe" -m uvicorn backend.main:app --host 127.0.0.1 --port 8000

echo.
echo QuizGen er stoppet. Hvis der står en fejl ovenfor, så send et billede af den til Cecilie.
pause
