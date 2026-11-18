#!/usr/bin/env python3
"""Tayanch - one-time installer (standard library only; Windows, macOS, Linux; Python 3.11-3.13).

    python install.py             do everything that is still missing (safe to run again)
    python install.py --dry-run   only print what would be done

Steps: Python + Node.js check -> .venv -> backend/requirements-run.txt -> frontend `npm install`
(re-done when node_modules came from another operating system) -> backend/.env for the demo ->
shadow-model pickles (retrained only if they cannot be loaded here; never fatal) -> demo database.
"""
from __future__ import annotations

import sys

if sys.version_info < (3, 11):
    sys.stderr.write(
        "Tayanch uchun Python 3.11 yoki undan yangi versiya kerak (sizda %d.%d).\n"
        "Yuklab olish: https://www.python.org/downloads/  (Windows: \"Add python.exe to PATH\" ni belgilang)\n"
        % sys.version_info[:2]
    )
    raise SystemExit(1)

import argparse
import base64
import hashlib
import os
import secrets
import shutil
import stat
import subprocess
import time
from pathlib import Path

import run as tayanch  # run.py next to this file: shared paths and helpers

ROOT = tayanch.ROOT
BACKEND = tayanch.BACKEND
FRONTEND = tayanch.FRONTEND
REQ_FILE = BACKEND / "requirements-run.txt"
REQ_STAMP = ROOT / ".venv" / ".tayanch-requirements"
MODELS = BACKEND / "models_store"
say = tayanch.say

DRY = False
_step = 0


def step(title: str) -> None:
    global _step
    _step += 1
    say()
    say(f"[{_step}] {title}")


def note(text: str) -> None:
    say(f"    {text}")


def run_cmd(cmd: list[str], cwd: Path, *, fatal: bool = True, timeout: int | None = None) -> bool:
    """Run a command with inherited output. In --dry-run only print it."""
    shown = " ".join(f'"{c}"' if " " in str(c) else str(c) for c in cmd)
    if DRY:
        note(f"(dry-run) bajarilardi: {shown}   [papka: {cwd}]")
        return True
    note(f"> {shown}")
    try:
        code = subprocess.run([str(c) for c in cmd], cwd=str(cwd), env=tayanch.child_env(), timeout=timeout).returncode
    except subprocess.TimeoutExpired:
        code = -1
        note("vaqt tugadi (timeout)")
    except OSError as e:
        code = -1
        note(f"ishga tushirib boʻlmadi: {e}")
    if code != 0 and fatal:
        raise SystemExit(f"\nXATO: buyruq muvaffaqiyatsiz tugadi (kod {code}): {shown}")
    return code == 0


def _on_rm_error(func, path, _exc):
    """Windows: read-only files inside node_modules/.venv cannot be deleted until made writable."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


def remove_tree(path: Path) -> None:
    if DRY:
        note(f"(dry-run) oʻchirilardi: {path}")
        return
    note(f"oʻchirilmoqda: {path}")
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_on_rm_error)
    else:
        shutil.rmtree(path, onerror=_on_rm_error)
    if path.exists() and tayanch.IS_WINDOWS:  # very long paths: let cmd.exe finish the job
        subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if path.exists():
        raise SystemExit(f"\nXATO: {path} papkasini oʻchirib boʻlmadi. Tayanch oynalarini va antivirus tekshiruvini "
                         "yoping, papkani qoʻlda oʻchiring va qaytadan urinib koʻring.")


# ----------------------------------------------------------------------------- steps

def check_python() -> None:
    step("Python")
    v = sys.version_info
    note(f"Python {v.major}.{v.minor}.{v.micro} — {sys.executable}")
    if v[:2] > (3, 13):
        note("Diqqat: loyiha 3.11–3.13 da sinalgan. Kutubxonalar oʻrnatilmasa, Python 3.13 ni oʻrnating.")
    if tayanch.IS_WINDOWS and "WindowsApps" in sys.executable:
        note("Diqqat: bu Microsoft Store Python'i. Muammo chiqsa, python.org dagi oʻrnatuvchidan foydalaning.")
    try:
        import venv  # noqa: F401
        import ensurepip  # noqa: F401
    except ImportError:
        raise SystemExit("XATO: bu Python'da venv/ensurepip yoʻq. Linux: `sudo apt install python3-venv`. "
                         "Windows/macOS: https://www.python.org/downloads/ dan toʻliq Python oʻrnating.")


def check_node() -> None:
    step("Node.js")
    node = tayanch.node_exe()
    nv = tayanch.tool_version(node)
    if nv is None:
        raise SystemExit(
            "XATO: Node.js topilmadi.\n"
            "  1) https://nodejs.org saytidan LTS versiyani (22 yoki yangiroq) yuklab oling va oʻrnating;\n"
            "  2) shu oynani yoping va oʻrnatish faylini qaytadan ishga tushiring (PATH yangilanishi uchun).")
    if nv < tayanch.MIN_NODE:
        raise SystemExit(f"XATO: Node.js {'.'.join(map(str, nv))} juda eski. https://nodejs.org dan LTS (22+) ni oʻrnating.")
    note(f"Node.js {'.'.join(map(str, nv))} — {node}")
    if nv < tayanch.RECOMMENDED_NODE:
        note("Diqqat: Vite uchun Node.js 20.19+ yoki 22.12+ kerak boʻlishi mumkin. Frontend ochilmasa, Node.js ni yangilang.")
    if tayanch.npm_exe() is None:
        raise SystemExit("XATO: npm topilmadi (Node.js bilan birga keladi). Node.js ni https://nodejs.org dan qayta oʻrnating.")


def ensure_venv() -> None:
    step("Virtual muhit (.venv)")
    py = tayanch.venv_python()
    venv_dir = ROOT / ".venv"
    if py.exists():
        ok, out = tayanch.venv_runs("import sys; print('%d.%d.%d' % sys.version_info[:3]); sys.exit(0 if sys.version_info >= (3, 11) else 1)")
        if ok:
            note(f"bor va ishlayapti (Python {out})")
            return
        note("bor, lekin ishlamayapti (boshqa kompyuterdan koʻchirilgan yoki eski Python) — qayta yaratiladi")
        remove_tree(venv_dir)
    elif venv_dir.exists():
        note("boshqa operatsion tizimda yaratilgan .venv topildi — qayta yaratiladi")
        remove_tree(venv_dir)
    run_cmd([sys.executable, "-m", "venv", str(venv_dir)], ROOT)
    if not DRY and not py.exists():
        raise SystemExit(f"XATO: virtual muhit yaratilmadi ({py} yoʻq).")
    note("yaratildi")


def ensure_python_deps() -> None:
    step("Python kutubxonalari (backend/requirements-run.txt)")
    digest = hashlib.sha256(REQ_FILE.read_bytes()).hexdigest()
    py = tayanch.venv_python()
    if DRY and not py.exists():
        note("(dry-run) .venv yaratilgach kutubxonalar oʻrnatilardi")
        return
    ok, missing = tayanch.missing_modules()
    stamp = REQ_STAMP.read_text(encoding="utf-8").strip() if REQ_STAMP.is_file() else ""
    if ok and stamp in ("", digest):
        note("hammasi oʻrnatilgan")
        if not stamp and not DRY:
            REQ_STAMP.write_text(digest + "\n", encoding="utf-8")
        return
    note(f"oʻrnatish kerak: {missing or 'roʻyxat oʻzgargan'} (internet kerak, bir necha daqiqa)")
    run_cmd([py, "-m", "pip", "install", "--disable-pip-version-check", "--upgrade", "pip"], ROOT, fatal=False)
    run_cmd([py, "-m", "pip", "install", "--disable-pip-version-check", "--prefer-binary", "-r", REQ_FILE], ROOT)
    # Optional speed-ups: binary wheels only, so a missing compiler can never break the install. No uvloop.
    run_cmd([py, "-m", "pip", "install", "--disable-pip-version-check", "--only-binary", ":all:", "httptools", "watchfiles"],
            ROOT, fatal=False)
    if DRY:
        return
    ok, missing = tayanch.missing_modules()
    if not ok:
        raise SystemExit(f"XATO: oʻrnatishdan keyin ham yetishmaydi: {missing}")
    REQ_STAMP.write_text(digest + "\n", encoding="utf-8")
    note("oʻrnatildi")


def ensure_node_modules() -> None:
    step("Frontend kutubxonalari (frontend/node_modules)")
    nm = FRONTEND / "node_modules"
    state, detail = tayanch.node_modules_state()
    if state == "ok":
        note(f"bor va shu kompyuterga mos ({detail})")
        if not tayanch.NODE_MARKER.is_file() and not DRY:
            tayanch.NODE_MARKER.write_text(tayanch.platform_tag() + "\n", encoding="utf-8")
        return
    if state in ("foreign", "unknown") and nm.exists():
        note(f"boshqa kompyuter uchun oʻrnatilgan ({detail}) — oʻchirib, qayta oʻrnatiladi")
        remove_tree(nm)
    elif nm.exists():
        note("toʻliq emas — qayta oʻrnatiladi")
        remove_tree(nm)
    else:
        note("yoʻq — oʻrnatiladi (internet kerak, bir necha daqiqa)")
    run_cmd([tayanch.npm_exe() or "npm", "install", "--no-audit", "--no-fund"], FRONTEND)
    if not DRY:
        if not (nm / "vite").is_dir():
            raise SystemExit("XATO: npm install tugadi, lekin frontend/node_modules/vite yoʻq.")
        tayanch.NODE_MARKER.write_text(tayanch.platform_tag() + "\n", encoding="utf-8")
        note("oʻrnatildi")


def ensure_env_file() -> None:
    step("Sozlamalar (backend/.env)")
    env_file = BACKEND / ".env"
    if env_file.exists():
        note("bor — oʻzgartirilmaydi")
        return
    if DRY:
        note("(dry-run) namoyish sozlamalari va shu kompyuterda yaratilgan kalitlar bilan backend/.env yozilardi")
        return
    text = (
        "# Tayanch - local demo settings generated by install.py on this computer (git-ignored; never copy to production).\n"
        "APP_ENV=development\n"
        f"JWT_SECRET={secrets.token_urlsafe(48)}\n"
        f"ENCRYPTION_KEY={base64.urlsafe_b64encode(secrets.token_bytes(32)).decode('ascii')}\n"
        "LIVE_SIMULATOR=1\n"
        "LIVE_DEMO_LOGIN=1\n"
        "# CLAUDE_API_KEY=\n"
    )
    env_file.write_text(text, encoding="utf-8", newline="\n")
    note("yaratildi (namoyish rejimi, kalitlar shu kompyuterda generatsiya qilindi)")
    if tayanch.LIVE_DB.exists():
        note("Diqqat: baza eski kalitlar bilan yaratilgan boʻlishi mumkin — kirishda xato chiqsa, bazani yangilang (.reseed).")


_MODEL_CHECK = r"""
import sys, warnings, pathlib
import joblib
try:
    from sklearn.exceptions import InconsistentVersionWarning
    warnings.filterwarnings("error", category=InconsistentVersionWarning)
except Exception:
    pass
bad = []
for name in ("maternal_shadow_v2", "fetal_ctg_shadow_v1", "maternal_baseline_uci"):
    p = pathlib.Path("models_store") / (name + ".joblib")
    if not p.exists():
        bad.append(name + ":missing"); continue
    try:
        joblib.load(p)
    except BaseException as e:
        bad.append(name + ":" + type(e).__name__)
print(" ".join(bad))
sys.exit(3 if bad else 0)
"""


def ensure_models() -> None:
    step("ML modellari (sinov rejimi; ilova ularsiz ham ishlaydi)")
    try:
        if DRY and not tayanch.venv_python().exists():
            note("(dry-run) .venv yaratilgach modellar tekshirilardi")
            return
        ok, out = tayanch.venv_runs(_MODEL_CHECK, timeout=300)
        if ok:
            note("uchala model shu Python/scikit-learn bilan ochildi")
            return
        bad = out.split()[-3:] if out else []
        note(f"ochilmadi yoki yoʻq: {out or 'nomaʼlum sabab'} — modellar Python/scikit-learn versiyasiga bogʻliq, qayta oʻqitiladi")
        py = tayanch.venv_python()
        scripts = []
        if any(b.startswith(("maternal_shadow_v2", "fetal_ctg_shadow_v1")) for b in bad) or not bad:
            scripts.append(BACKEND / "scripts" / "train_shadow_models.py")
        if any(b.startswith("maternal_baseline_uci") for b in bad) or not bad:
            scripts.append(BACKEND / "scripts" / "train_baseline_model.py")
        if not (ROOT / "data" / "raw").is_dir():
            note("data/raw papkasi yoʻq — qayta oʻqitib boʻlmaydi; ilova modellarsiz ishlayveradi")
            return
        if not DRY:
            tag = f"py{sys.version_info.major}{sys.version_info.minor}-{time.strftime('%Y%m%d')}"
            for f in MODELS.glob("*.joblib"):  # keep the old pickles next to the new ones
                try:
                    shutil.copy2(f, f.with_name(f.name + f".before-{tag}.bak"))
                except OSError:
                    pass
        for s in scripts:
            if s.is_file():
                if not run_cmd([py, s], BACKEND, fatal=False, timeout=1800):
                    note(f"{s.name} muvaffaqiyatsiz — muhim emas, ilova bu modelsiz ishlaydi")
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001 - this step must never stop the installation
        note(f"modellarni tekshirishda xato ({type(e).__name__}: {e}) — oʻtkazib yuborildi")


def ensure_database() -> None:
    step("Namoyish bazasi (backend/perinatal_live.db)")
    if tayanch.LIVE_DB.exists():
        note("bor — oʻzgartirilmaydi (yangilash: «Tayanch-bazani-yangilash» fayli yoki `.reseed`)")
        return
    if DRY:
        note("(dry-run) baza yaratilardi: backend/scripts/seed_national.py (1–3 daqiqa)")
        return
    if tayanch.seed() != 0:
        raise SystemExit("XATO: bazani yaratib boʻlmadi — logs/seed.log faylini qarang.")


def main(argv: list[str] | None = None) -> int:
    global DRY
    tayanch.setup_console()
    ap = argparse.ArgumentParser(description="Tayanch oʻrnatuvchisi (qayta ishga tushirish xavfsiz)")
    ap.add_argument("--dry-run", action="store_true", help="hech narsani oʻzgartirmasdan, nima qilinishini koʻrsatish")
    args = ap.parse_args(argv)
    DRY = args.dry_run
    os.chdir(ROOT)

    say("Tayanch — oʻrnatish" + (" (dry-run: hech narsa oʻzgartirilmaydi)" if DRY else ""))
    say(f"Papka: {ROOT}")
    if tayanch.IS_WINDOWS and len(str(ROOT)) > 120:
        say("Diqqat: papka yoʻli juda uzun. Muammo chiqsa, loyihani qisqa joyga koʻchiring (masalan C:\\Tayanch).")

    check_python()
    check_node()
    ensure_venv()
    ensure_python_deps()
    ensure_node_modules()
    ensure_env_file()
    ensure_models()
    ensure_database()

    say()
    say("=" * 62)
    if DRY:
        say("  Dry-run tugadi: hech narsa oʻzgartirilmadi.")
    else:
        say("  Tayyor! Endi ishga tushirish faylini ikki marta bosing:")
        if tayanch.IS_WINDOWS:
            say("    windows\\Tayanch-ishga-tushirish.bat")
        elif sys.platform == "darwin":
            say("    mac/Tayanch-ishga-tushirish.command")
        else:
            say("    python3 run.py")
        say(f"  Soʻng brauzerda: http://127.0.0.1:{tayanch.WEB_PORT}")
    say("=" * 62)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
