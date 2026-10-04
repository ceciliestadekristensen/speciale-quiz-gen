#!/bin/bash
# QuizGen - installation på Mac. Dobbeltklik på filen for at køre den.
cd "$(dirname "$0")" || exit 1
DIR="$(pwd)"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

fejl() {
    echo
    echo "Installationen stoppede på grund af en fejl. Se beskeden ovenfor."
    read -r -p "Tryk Enter for at lukke..."
    exit 1
}

echo "=== Installerer QuizGen ==="
echo

if ! command -v python3 >/dev/null 2>&1 || ! python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>/dev/null; then
    echo "FEJL: Python 3.10 eller nyere blev ikke fundet. Installer det fra python.org og kør installer.command igen."
    fejl
fi

if ! command -v ollama >/dev/null 2>&1; then
    echo "FEJL: Ollama blev ikke fundet. Installer Ollama fra ollama.com, åbn programmet én gang,"
    echo "og kør installer.command igen."
    fejl
fi

if ! command -v tesseract >/dev/null 2>&1; then
    echo "ADVARSEL: Tesseract blev ikke fundet. QuizGen virker, men kan ikke læse tekst fra billeder i PDF'er."
    echo
elif ! tesseract --list-langs 2>/dev/null | grep -qx dan; then
    echo "ADVARSEL: Tesseract mangler den danske sprogpakke. Installer den med: brew install tesseract-lang"
    echo
fi

echo "[1/4] Opretter Python-miljø..."
if [ ! -x ".venv/bin/python" ]; then
    python3 -m venv .venv || fejl
fi

echo "[2/4] Installerer Python-pakker..."
.venv/bin/python -m pip install --upgrade pip >/dev/null
.venv/bin/python -m pip install -r requirements.txt || fejl

echo "[3/4] Henter AI-modellen (ca. 5 GB, kan tage et stykke tid)..."
if ! curl -s http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    open -a Ollama 2>/dev/null || (ollama serve >/dev/null 2>&1 &)
    sleep 5
fi
ollama pull qwen2.5:7b-instruct || fejl

echo "[4/4] Opretter genvej på skrivebordet..."
chmod +x "$DIR/start.command"
cat > "$HOME/Desktop/QuizGen.command" <<EOF
#!/bin/bash
exec "$DIR/start.command"
EOF
chmod +x "$HOME/Desktop/QuizGen.command"

echo
echo "=== QuizGen er installeret ==="
echo "Start programmet ved at dobbeltklikke på \"QuizGen\" på skrivebordet."
read -r -p "Tryk Enter for at lukke..."
