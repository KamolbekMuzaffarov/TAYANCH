"""Benchmark VoiceLab STT on the same sets as run_eval.py (synthetic dictations + public FLEURS audio only).

    python stt_server/eval/eval_voicelab.py [n_fleurs]      # default 30 FLEURS clips (uses VoiceLab credits)

Reads VOICELAB_API_KEY etc. from backend/.env through the backend settings; the same privacy gates apply.
Every remote transcription is deleted right after it is read. Results are merged into results.json / results.md.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "backend"))
sys.path.insert(0, str(HERE))

from run_eval import DATA, edit, norm  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.services.voice.parser import parse  # noqa: E402
from app.services.voice.providers import VoiceLabSTTProvider  # noqa: E402

MIME = {".mp3": "audio/mpeg", ".wav": "audio/wav"}


def run(prov: VoiceLabSTTProvider, path: Path, key: str) -> str:
    res = prov.transcribe(path.read_bytes(), MIME[path.suffix], "uz", key, consent_external=True)
    prov.delete_remote(res.external_id)
    return res.text


def main() -> None:
    n_fleurs = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    prov = VoiceLabSTTProvider(get_settings())
    blocked = prov.gate(True)
    if blocked:
        sys.exit(f"VoiceLab blocked: {blocked} (set VOICELAB_API_KEY in backend/.env)")
    t0 = time.time()
    cases = [json.loads(l) for l in (DATA / "medical_uz" / "truth.jsonl").read_text(encoding="utf-8").splitlines()]
    tot = correct = wrong = missing = spurious = exact = 0
    examples = []
    for c in cases:
        hyp = run(prov, DATA / "medical_uz" / f"{c['id']}.mp3", f"eval-{c['id']}")
        got = {x["field"]: x["value"] for x in parse(hyp, "pregnancy")}
        ok = True
        for k, v in c["truth"].items():
            tot += 1
            if k not in got:
                missing += 1; ok = False
            elif (abs(float(got[k]) - float(v)) < 0.01 if not isinstance(v, str) else got[k] == v):
                correct += 1
            else:
                wrong += 1; ok = False
                if len(examples) < 6:
                    examples.append(f"{c['id']} {k}: truth={v} got={got[k]} | {hyp[:90]}")
        spurious += len(set(got) - set(c["truth"]))
        exact += ok
    med = {"cases": len(cases), "fields": tot, "field_acc": round(100 * correct / tot, 1),
           "wrong_pct": round(100 * wrong / tot, 1), "missing_pct": round(100 * missing / tot, 1), "spurious": spurious,
           "exact_cases_pct": round(100 * exact / len(cases), 1), "low_conf_cases": "n/a", "wrong_examples": examples}
    rows = {}
    for line in (DATA / "fleurs_uz" / "test.tsv").read_text(encoding="utf-8").splitlines():
        cols = line.split("\t")
        rows[cols[1]] = cols[3]
    we = wn = ce = cn = 0
    files = [f for f in sorted((DATA / "fleurs_uz" / "audio").glob("*.wav")) if f.name in rows][:n_fleurs]
    for f in files:
        r, h = norm(rows[f.name]), norm(run(prov, f, f"eval-{f.stem}"))
        we += edit(r.split(), h.split()); wn += len(r.split())
        ce += edit(list(r), list(h)); cn += len(r)
    fl = {"n": len(files), "wer": round(100 * we / max(wn, 1), 1), "cer": round(100 * ce / max(cn, 1), 1)}
    out = HERE / "results.json"
    results = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    results["voicelab"] = {"medical": med, "fleurs": fl, "seconds": round(time.time() - t0)}
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(results["voicelab"], ensure_ascii=False, indent=1))
    lines = ["| config | field acc % | wrong % | missing % | exact cases % | low-conf cases | FLEURS WER % | CER % |",
             "|---|---|---|---|---|---|---|---|"]
    for k, r in results.items():
        m, f = r["medical"], r["fleurs"]
        lines.append(f"| {k} | {m['field_acc']} | {m['wrong_pct']} | {m['missing_pct']} | {m['exact_cases_pct']} | "
                     f"{m['low_conf_cases']} | {f['wer']} (n={f['n']}) | {f['cer']} |")
    (HERE / "results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
