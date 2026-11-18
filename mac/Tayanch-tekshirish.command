#!/bin/bash
# Tayanch - macOS launcher (double-click in Finder). All logic lives in run.py / install.py.
cd "$(dirname "$0")/.." || { echo "Loyiha papkasi topilmadi"; read -n 1 -s -r; exit 1; }
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
PY=""
for c in python3.13 python3.12 python3.11 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 11) else 1)' >/dev/null 2>&1; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  echo
  echo "XATO: Python 3.11 yoki undan yangi versiya topilmadi."
  echo "  https://www.python.org/downloads/ saytidan Python 3.13 ni o'rnating (yoki: brew install python@3.13),"
  echo "  so'ng shu faylni qaytadan ishga tushiring."
  echo
  read -n 1 -s -r -p "Yopish uchun istalgan tugmani bosing..."
  exit 1
fi
"$PY" run.py --check "$@"
RC=$?
echo
read -n 1 -s -r -p "Yopish uchun istalgan tugmani bosing..."
echo
exit $RC
