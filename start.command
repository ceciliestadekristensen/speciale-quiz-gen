#!/bin/bash
# QuizGen - start på Mac. Dobbeltklik på filen (eller genvejen på skrivebordet).
cd "$(dirname "$0")" || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

if [ ! -x ".venv/bin/python" ]; then
    echo "QuizGen er ikke installeret endnu. Kør installer.command først."
    read -r -p "Tryk Enter for at lukke..."
    exit 1
fi

echo "Starter QuizGen..."

# Start Ollama, hvis den ikke allerede kører i baggrunden
if ! curl -s http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    open -a Ollama 2>/dev/null || (ollama serve >/dev/null 2>&1 &)
    sleep 5
fi

# Åbn browseren, så snart serveren svarer (venter op til 60 sekunder)
(
    for _ in $(seq 1 60); do
        if curl -s http://127.0.0.1:8000/ >/dev/null 2>&1; then
            open "http://127.0.0.1:8000/?v=$(date +%s)"
            exit 0
        fi
        sleep 1
    done
) &

echo
echo "QuizGen kører på http://127.0.0.1:8000"
echo "Luk dette vindue for at stoppe QuizGen."
echo
.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000

echo
echo "QuizGen er stoppet. Hvis der står en fejl ovenfor, så send et billede af den til Cecilie."
read -r -p "Tryk Enter for at lukke..."
