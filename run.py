#!/usr/bin/env python3
"""Tayanch - one cross-platform supervisor for the whole demo (frontend: vite, backend: uvicorn).

Pure standard library; runs on Windows, macOS and Linux with Python 3.11-3.13.
Replaces the bash logic of dev.sh / share.sh (those are now thin wrappers around this file).

    python run.py              start frontend + backend on this computer only
    python run.py --share      also reachable from other devices on the same Wi-Fi
    python run.py --reseed     rebuild the demo database first
    python run.py --no-frontend
    python run.py --check      only verify the prerequisites (Uzbek checklist)
    python run.py --open       open the browser when both servers answer

Control while it is running (no extra terminal needed), exactly like the old dev.sh:
    create an empty file ".restart"  -> the backend is restarted
    create an empty file ".reseed"   -> the demo database is rebuilt, then the backend is restarted
"""
from __future__ import annotations

import sys

if sys.version_info < (3, 11):  # keep this check before anything that needs a modern interpreter
    sys.stderr.write(
        "Tayanch uchun Python 3.11 yoki undan yangi versiya kerak (sizda %d.%d).\n"
        "Yuklab olish: https://www.python.org/downloads/\n" % sys.version_info[:2]
    )
    raise SystemExit(1)

import argparse
import os
import platform
import shutil
import signal
import socket
import subprocess
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
LOGS = ROOT / "logs"
LIVE_DB = BACKEND / "perinatal_live.db"
SEED_SCRIPT = BACKEND / "scripts" / "seed_national.py"
NODE_MARKER = FRONTEND / "node_modules" / ".tayanch-platform"
RESTART_FILE = ROOT / ".restart"
RESEED_FILE = ROOT / ".reseed"

IS_WINDOWS = os.name == "nt"
API_HOST, API_PORT, WEB_PORT = "127.0.0.1", 8000, 5173
MIN_PYTHON = (3, 11)
MIN_NODE = (18, 0)
RECOMMENDED_NODE = (20, 19)  # what the bundled Vite version asks for

# Modules the backend needs at start-up (used by --check and by install.py).
REQUIRED_MODULES = (
    "fastapi", "uvicorn", "sqlalchemy", "pydantic", "pydantic_settings", "bcrypt", "jwt", "cryptography",
    "pyotp", "httpx", "multipart", "anthropic", "sklearn", "scipy", "joblib", "numpy", "pandas",
)


# ----------------------------------------------------------------------------- helpers

def setup_console() -> None:
    """Uzbek text must never crash a cp1251 / cp866 console: write UTF-8, replace what cannot be shown."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001 - not a text stream (redirected / replaced): nothing to do
            pass


def say(text: str = "") -> None:
    print(text, flush=True)


def platform_tag() -> str:
    """Identifies the OS + CPU that node_modules was installed for (native binaries are per platform)."""
    return f"{sys.platform}-{platform.machine().lower() or 'unknown'}"


def venv_python() -> Path:
    if IS_WINDOWS:
        return ROOT / ".venv" / "Scripts" / "python.exe"
    return ROOT / ".venv" / "bin" / "python"


def child_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env.pop("PYTHONHOME", None)  # a foreign PYTHONHOME breaks the virtualenv interpreter
    return env


def which(*names: str) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def node_exe() -> str | None:
    return which("node.exe", "node") if IS_WINDOWS else which("node")


def npm_exe() -> str | None:
    return which("npm.cmd", "npm") if IS_WINDOWS else which("npm")


def npx_exe() -> str | None:
    return which("npx.cmd", "npx") if IS_WINDOWS else which("npx")


def tool_version(exe: str | None) -> tuple[int, ...] | None:
    """`node --version` -> (22, 12, 0); None when the tool is missing or does not answer."""
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    digits = out.lstrip("vV").split("-")[0].split(".")
    try:
        return tuple(int(d) for d in digits[:3])
    except ValueError:
        return None


def venv_runs(code: str, timeout: int = 120) -> tuple[bool, str]:
    """Run a snippet with the virtualenv interpreter (cwd = backend). Returns (ok, combined output)."""
    py = venv_python()
    if not py.exists():
        return False, "virtual muhit topilmadi"
    try:
        r = subprocess.run([str(py), "-c", code], cwd=str(BACKEND), env=child_env(), capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"{type(e).__name__}: {e}"
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def missing_modules() -> tuple[bool, str]:
    code = (
        "import importlib, sys\n"
        f"names = {list(REQUIRED_MODULES)!r}\n"
        "bad = []\n"
        "for n in names:\n"
        "    try:\n"
        "        importlib.import_module(n)\n"
        "    except Exception as e:\n"
        "        bad.append(n + ' (' + type(e).__name__ + ')')\n"
        "print(', '.join(bad))\n"
        "sys.exit(1 if bad else 0)\n"
    )
    return venv_runs(code)


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """Connect-only probe (never binds), so it cannot disturb a server that is already running."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            return s.connect_ex((host, port)) == 0
    except OSError:
        return False


def lan_ip() -> str | None:
    """LAN address of this computer. The UDP 'connect' only selects a route - no packet leaves the machine."""
    for target in ("10.255.255.255", "192.168.255.255", "192.0.2.1"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect((target, 1))
                ip = s.getsockname()[0]
            if ip and not ip.startswith("127.") and ip != "0.0.0.0":
                return ip
        except OSError:
            continue
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if ip and not ip.startswith("127."):
            return ip
    except OSError:
        pass
    return None


def node_modules_state() -> tuple[str, str]:
    """('ok' | 'missing' | 'foreign' | 'unknown', detail). Shared with install.py."""
    nm = FRONTEND / "node_modules"
    if not (nm / "vite").is_dir():
        return "missing", "frontend/node_modules yoʻq"
    tag = platform_tag()
    if NODE_MARKER.is_file():
        try:
            found = NODE_MARKER.read_text(encoding="utf-8").strip()
        except OSError:
            found = ""
        return ("ok", found) if found == tag else ("foreign", f"{found or '?'} uchun oʻrnatilgan, bu kompyuter: {tag}")
    # No marker (installed by hand): infer from the native bindings that are present.
    os_name = {"win32": "win32", "darwin": "darwin"}.get(sys.platform, "linux")
    arch = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(platform.machine().lower(), "")
    names: list[str] = []
    for scope, prefix in (("@rolldown", "binding-"), ("@esbuild", ""), ("@rollup", "rollup-")):
        d = nm / scope
        if d.is_dir():
            names += [p.name[len(prefix):] for p in d.iterdir() if p.name.startswith(prefix) and "-" in p.name]
    native = [n for n in names if n.split("-")[0] in ("win32", "darwin", "linux", "freebsd", "android")]
    if not native:
        return "unknown", "platforma belgisi yoʻq"
    if any(n.startswith(f"{os_name}-{arch}") for n in native):
        return "ok", f"{tag} (belgisiz, lekin mos)"
    return "foreign", f"{', '.join(sorted(native))} uchun oʻrnatilgan, bu kompyuter: {tag}"


# ----------------------------------------------------------------------------- --check

def run_check(want_frontend: bool = True) -> bool:
    """Print the Uzbek prerequisite checklist. Returns True when everything required is in place."""
    ok_all = True
    install_hint = ("windows\\Tayanch-ornatish.bat" if IS_WINDOWS else "mac/Tayanch-ornatish.command"
                    if sys.platform == "darwin" else "python3 install.py")

    def line(state: str, title: str, detail: str = "") -> None:
        nonlocal ok_all
        mark = {"ok": "[ OK ]", "warn": "[ !! ]", "fail": "[XATO]"}[state]
        if state == "fail":
            ok_all = False
        say(f"  {mark} {title}" + (f" — {detail}" if detail else ""))

    say("Tayanch — tekshiruv roʻyxati")
    say(f"  Papka: {ROOT}")
    say(f"  Tizim: {platform.system()} {platform.release()} ({platform_tag()})")
    say()

    v = sys.version_info
    line("ok" if v >= MIN_PYTHON else "fail", "Python", f"{v.major}.{v.minor}.{v.micro}"
         + ("" if v[:2] <= (3, 13) else " (3.11–3.13 da sinalgan; yangiroq versiyada kutubxonalar oʻrnatilmasligi mumkin)"))

    py = venv_python()
    if not py.exists():
        line("fail", "Virtual muhit (.venv)", f"topilmadi — avval «{install_hint}» ni ishga tushiring")
    else:
        ok, out = venv_runs("import sys; print('%d.%d.%d' % sys.version_info[:3]); sys.exit(0 if sys.version_info >= (3, 11) else 1)")
        if ok:
            line("ok", "Virtual muhit (.venv)", f"Python {out}")
            ok, out = missing_modules()
            line("ok" if ok else "fail", "Python kutubxonalari",
                 "hammasi joyida" if ok else f"yetishmaydi: {out} — «{install_hint}» ni qayta ishga tushiring")
        else:
            line("fail", "Virtual muhit (.venv)", "ishlamayapti (boshqa kompyuterdan koʻchirilgan yoki papka nomi oʻzgargan boʻlishi mumkin) — "
                 f"«{install_hint}» uni qayta yaratadi")

    if want_frontend:
        nv = tool_version(node_exe())
        if nv is None:
            line("fail", "Node.js", "topilmadi — https://nodejs.org (LTS) dan oʻrnating")
        elif nv < MIN_NODE:
            line("fail", "Node.js", f"{'.'.join(map(str, nv))} juda eski — 20 yoki undan yangi LTS kerak: https://nodejs.org")
        elif nv < RECOMMENDED_NODE:
            line("warn", "Node.js", f"{'.'.join(map(str, nv))} — Vite uchun 20.19+ yoki 22.12+ tavsiya etiladi")
        else:
            line("ok", "Node.js", ".".join(map(str, nv)))
        state, detail = node_modules_state()
        if state == "ok":
            line("ok", "Frontend kutubxonalari (node_modules)", detail)
        elif state == "unknown":
            line("warn", "Frontend kutubxonalari (node_modules)", f"{detail} — shubha boʻlsa «{install_hint}» ni ishga tushiring")
        else:
            line("fail", "Frontend kutubxonalari (node_modules)", f"{detail} — «{install_hint}» ni ishga tushiring")

    if LIVE_DB.exists():
        line("ok", "Namoyish bazasi", f"{LIVE_DB.name} ({LIVE_DB.stat().st_size // (1024 * 1024)} MB)")
    elif SEED_SCRIPT.exists():
        line("warn", "Namoyish bazasi", "hali yoʻq — birinchi ishga tushirishda avtomatik yaratiladi (1–3 daqiqa)")
    else:
        line("fail", "Namoyish bazasi", "baza ham, uni yaratuvchi skript ham topilmadi")

    data_dir = ROOT / "data" / "processed"
    line("ok" if data_dir.is_dir() else "fail", "Ochiq maʼlumotlar (data/processed)", "" if data_dir.is_dir() else "papka topilmadi")

    models = sorted(p.name for p in (BACKEND / "models_store").glob("*.joblib")) if (BACKEND / "models_store").is_dir() else []
    line("ok" if models else "warn", "ML modellari (sinov rejimi)",
         f"{len(models)} ta fayl" if models else "topilmadi — ilova ishlaydi, faqat sinov modellari koʻrinmaydi")

    line("ok" if (BACKEND / ".env").exists() else "warn", "Sozlamalar (backend/.env)",
         "" if (BACKEND / ".env").exists() else f"yoʻq — «{install_hint}» namoyish sozlamalari bilan yaratadi")

    for port, name in ((API_PORT, "backend"), (WEB_PORT, "frontend")):
        if port == WEB_PORT and not want_frontend:
            continue
        busy = port_in_use(port)
        line("warn" if busy else "ok", f"Port {port} ({name})",
             "band — Tayanch allaqachon ishlayotgan boʻlishi mumkin (unda brauzerda ochavering) yoki boshqa dastur egallagan"
             if busy else "boʻsh")

    say()
    say("Natija: hammasi tayyor." if ok_all else f"Natija: kamchilik bor. Avval «{install_hint}» ni ishga tushiring.")
    return ok_all


# ----------------------------------------------------------------------------- process handling

def spawn(cmd: list[str], cwd: Path, log_path: Path, append: bool = False) -> subprocess.Popen:
    """Start a child in its own process group / session with stdout+stderr going to a log file."""
    LOGS.mkdir(parents=True, exist_ok=True)
    log = open(log_path, "ab" if append else "wb")  # binary: the child writes UTF-8 itself
    if append:
        log.write(f"\n----- {datetime.now():%Y-%m-%d %H:%M:%S} start -----\n".encode("utf-8"))
        log.flush()
    kwargs: dict = {}
    if IS_WINDOWS:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # Ctrl-C reaches only the supervisor
    else:
        kwargs["start_new_session"] = True  # own process group -> the whole tree can be signalled
    try:
        return subprocess.Popen([str(c) for c in cmd], cwd=str(cwd), env=child_env(), stdin=subprocess.DEVNULL,
                                stdout=log, stderr=subprocess.STDOUT, **kwargs)
    finally:
        log.close()  # the child keeps its own handle


def stop(proc: subprocess.Popen | None, grace: float = 6.0) -> None:
    """Terminate a child and everything it started (venv launcher -> python, node -> esbuild/rolldown ...)."""
    if proc is None or proc.poll() is not None:
        return
    try:
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=30)
        else:
            os.killpg(proc.pid, signal.SIGTERM)
    except (OSError, subprocess.SubprocessError):
        try:
            proc.terminate()
        except OSError:
            pass
    try:
        proc.wait(timeout=grace)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        if IS_WINDOWS:
            proc.kill()
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def frontend_cmd(host: str) -> list[str] | None:
    args = ["--host", host, "--port", str(WEB_PORT), "--strictPort"]
    vite_js = FRONTEND / "node_modules" / "vite" / "bin" / "vite.js"
    node = node_exe()
    if node and vite_js.is_file():
        return [node, str(vite_js), *args]  # no cmd.exe / npx wrapper in between: clean process tree on Windows
    npx = npx_exe()  # npx.cmd on Windows
    if npx:
        return [npx, "vite", *args]
    return None


def backend_cmd() -> list[str]:
    return [str(venv_python()), "-m", "uvicorn", "app.main:app", "--host", API_HOST, "--port", str(API_PORT)]


def seed() -> int:
    if not SEED_SCRIPT.is_file():
        return 0
    say("Namoyish bazasi yaratilmoqda (1–3 daqiqa)…")
    LOGS.mkdir(parents=True, exist_ok=True)
    try:
        with open(LOGS / "seed.log", "wb") as log:
            code = subprocess.run([str(venv_python()), str(SEED_SCRIPT)], cwd=str(BACKEND), env=child_env(),
                                  stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT).returncode
    except OSError as e:
        say(f"  baza skriptini ishga tushirib boʻlmadi: {e}")
        return 1
    say("  baza tayyor." if code == 0 else f"  baza yaratishda xato (kod {code}) — logs/seed.log faylini qarang")
    return code


def remove(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


class _Stop(Exception):
    pass


def _raise_stop(signum, frame):  # noqa: ARG001
    raise _Stop()


def supervise(args: argparse.Namespace) -> int:
    want_frontend = not args.no_frontend
    host = "0.0.0.0" if args.share else os.environ.get("SHARE_HOST", "127.0.0.1")
    install_hint = ("windows\\Tayanch-ornatish.bat" if IS_WINDOWS else "mac/Tayanch-ornatish.command"
                    if sys.platform == "darwin" else "python3 install.py")

    # --- prerequisites (short, fatal ones only; the full list is `--check`)
    if not venv_python().exists():
        say(f"Virtual muhit (.venv) topilmadi. Avval «{install_hint}» ni ishga tushiring.")
        return 1
    ok, out = missing_modules()
    if not ok:
        say(f"Python kutubxonalari tayyor emas ({out}). «{install_hint}» ni ishga tushiring.")
        return 1
    fe_cmd = frontend_cmd(host) if want_frontend else None
    if want_frontend:
        state, detail = node_modules_state()
        if fe_cmd is None or state in ("missing", "foreign"):
            say(f"Frontend tayyor emas ({detail if fe_cmd else 'Node.js topilmadi'}). «{install_hint}» ni ishga tushiring.")
            return 1
    busy = [p for p in ([API_PORT, WEB_PORT] if want_frontend else [API_PORT]) if port_in_use(p)]
    if busy:
        say(f"Port {', '.join(map(str, busy))} band. Tayanch allaqachon ishlayotgan boʻlishi mumkin:")
        say(f"  brauzerda http://127.0.0.1:{WEB_PORT} ni oching; yoki oldingi oynani yoping va qaytadan urinib koʻring.")
        return 2

    LOGS.mkdir(parents=True, exist_ok=True)
    signal.signal(signal.SIGTERM, _raise_stop)
    for name in ("SIGHUP", "SIGBREAK"):  # terminal window closed (POSIX) / Ctrl-Break or console close (Windows)
        if hasattr(signal, name):
            try:
                signal.signal(getattr(signal, name), _raise_stop)
            except (OSError, ValueError):
                pass

    web: subprocess.Popen | None = None
    api: subprocess.Popen | None = None

    def start_api(first: bool = False) -> None:
        nonlocal api
        api = spawn(backend_cmd(), BACKEND, LOGS / "backend.log", append=not first)
        say(f"backend ishga tushdi (pid {api.pid}) {datetime.now():%H:%M:%S}")

    def start_web() -> None:
        nonlocal web
        assert fe_cmd is not None
        web = spawn(fe_cmd, FRONTEND, LOGS / "frontend.log")
        say(f"frontend ishga tushdi (pid {web.pid})")

    try:
        if want_frontend:
            start_web()
        if args.reseed or RESEED_FILE.exists():
            remove(RESEED_FILE)
            remove(RESTART_FILE)
            seed()
        elif not LIVE_DB.exists():
            seed()
        remove(RESTART_FILE)
        start_api(first=True)

        say()
        say("=" * 62)
        if want_frontend:
            say(f"  Shu kompyuterda:        http://127.0.0.1:{WEB_PORT}")
            if host == "0.0.0.0":
                ip = lan_ip()
                say(f"  Boshqa qurilmalar uchun: http://{ip}:{WEB_PORT}" if ip
                    else "  Boshqa qurilmalar uchun: IP manzil aniqlanmadi (Wi-Fi ulanganini tekshiring)")
                say("  (hamma qurilma bitta Wi-Fi tarmogʻida boʻlishi va xavfsizlik devori ruxsat berishi kerak)")
        say(f"  API hujjati:            http://{API_HOST}:{API_PORT}/api/docs")
        say("  Loglar: logs/backend.log, logs/frontend.log")
        say("  Toʻxtatish: Ctrl+C (yoki shu oynani yoping)")
        say("=" * 62)
        say()

        open_pending = bool(args.open) and want_frontend
        open_deadline = time.monotonic() + 90
        web_warned = False
        while True:
            time.sleep(2)
            if RESEED_FILE.exists():
                remove(RESEED_FILE)
                remove(RESTART_FILE)
                say("Baza yangilanmoqda (.reseed)…")
                stop(api)  # the database file must be closed before it can be replaced (mandatory on Windows)
                time.sleep(1)
                seed()
                start_api()
            if RESTART_FILE.exists():
                remove(RESTART_FILE)
                say("Backend qayta ishga tushirilmoqda (.restart)…")
                stop(api)
                time.sleep(1)
                start_api()
            if api is not None and api.poll() is not None:
                say(f"backend toʻxtab qoldi (kod {api.returncode}); logs/backend.log ni qarang; 10 soniyadan soʻng qayta urinish")
                time.sleep(10)
                start_api()
            if web is not None and web.poll() is not None and not web_warned:
                web_warned = True
                say(f"frontend toʻxtab qoldi (kod {web.returncode}); logs/frontend.log ni qarang")
            if open_pending:
                if port_in_use(WEB_PORT) and port_in_use(API_PORT):
                    open_pending = False
                    try:
                        import webbrowser

                        webbrowser.open(f"http://127.0.0.1:{WEB_PORT}")
                    except Exception:  # noqa: BLE001 - opening a browser is a convenience only
                        pass
                elif time.monotonic() > open_deadline:
                    open_pending = False
    except (KeyboardInterrupt, _Stop):
        say()
        say("Toʻxtatilmoqda…")
        return 0
    finally:
        try:
            signal.signal(signal.SIGINT, signal.SIG_IGN)  # a second Ctrl-C must not leave orphans behind
        except (OSError, ValueError):
            pass
        stop(api)
        stop(web)


def main(argv: list[str] | None = None) -> int:
    setup_console()
    ap = argparse.ArgumentParser(description="Tayanch: frontend + backend supervisor (Windows / macOS / Linux)")
    ap.add_argument("--share", action="store_true", help="bir xil Wi-Fi dagi boshqa qurilmalarga ham ochish")
    ap.add_argument("--reseed", action="store_true", help="ishga tushirishdan oldin namoyish bazasini qayta yaratish")
    ap.add_argument("--no-frontend", action="store_true", help="faqat backend")
    ap.add_argument("--check", action="store_true", help="faqat talablarni tekshirish")
    ap.add_argument("--open", action="store_true", help="tayyor boʻlganda brauzerni ochish")
    args = ap.parse_args(argv)
    os.chdir(ROOT)
    if args.check:
        return 0 if run_check(want_frontend=not args.no_frontend) else 1
    return supervise(args)


if __name__ == "__main__":
    raise SystemExit(main())
