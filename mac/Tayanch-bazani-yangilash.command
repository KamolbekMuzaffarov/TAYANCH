#!/bin/bash
# Tayanch - ask the running supervisor (run.py) to rebuild the demo database.
cd "$(dirname "$0")/.." || exit 1
touch .reseed
echo
echo "Buyruq berildi: namoyish bazasi 1-3 daqiqada yangilanadi."
echo "Tayanch ishlab turgan bo'lsa - o'zi yangilaydi; aks holda keyingi ishga tushirishda."
echo
read -n 1 -s -r -p "Yopish uchun istalgan tugmani bosing..."
echo
