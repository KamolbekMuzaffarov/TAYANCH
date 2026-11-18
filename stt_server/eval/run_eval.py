"""Benchmark Uzbek STT configurations.

    python stt_server/eval/run_eval.py [config_name ...]

Sets (under ~/perinatal-devtools/eval_data):
  fleurs_uz/   real read Uzbek speech (Google FLEURS test subset, CC-BY 4.0)  -> WER / CER
  medical_uz/  synthetic clinical dictations with ground truth               -> field accuracy via the backend parser

Clinical safety metric: `wrong` = a field value produced that differs from the truth (the dangerous error);
`missing` = not extracted (safe: the clinician enters it). Results -> stt_server/eval/results.json / results.md
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "backend"))
sys.path.insert(0, str(HERE.parents[1]))

import stt_server.server as srv  # noqa: E402  (adds CUDA DLL dirs on Windows)
from faster_whisper import WhisperModel, decode_audio  # noqa: E402

from app.services.voice.parser import parse  # noqa: E402
from app.services.voice.providers import STT_PROMPTS  # noqa: E402

DATA = Path.home() / "perinatal-devtools" / "eval_data"
MODELS = Path.home() / "perinatal-devtools" / "stt_models"
PROMPT = STT_PROMPTS["uz"]

CONFIGS = {
    "turbo_base": ("large-v3-turbo", None),
    "turbo_base+prompt": ("large-v3-turbo", PROMPT),
    "turbo_uz(hostmepanda)": (str(MODELS / "hostmepanda__whisper-large-v3-turbo-uzbek-ct2"), None),
    "turbo_uz(hostmepanda)+prompt": (str(MODELS / "hostmepanda__whisper-large-v3-turbo-uzbek-ct2"), PROMPT),
    "rubaistt_v2_medium": (str(MODELS / "AlexAnoshka__fast-whisper-uz-rubaistt-v2-medium-ct2"), None),
    "rubaistt_v2_medium+prompt": (str(MODELS / "AlexAnoshka__fast-whisper-uz-rubaistt-v2-medium-ct2"), PROMPT),
    "kotib_uzbek_stt_v1": (str(MODELS / "yuldashev222__uzbek_stt_v1-ct2"), None),
    "kotib_uzbek_stt_v1+prompt": (str(MODELS / "yuldashev222__uzbek_stt_v1-ct2"), PROMPT),
}

_APOS = re.compile(r"[‘’ʻʼ`´']")


def norm(t: str) -> str:
    t = _APOS.sub("'", t.lower())
    t = re.sub(r"[^\w\s']", " ", t)
    return " ".join(t.split())


def edit(a: list, b: list) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, y in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y))
        prev = cur
    return prev[-1]


def transcribe(model: WhisperModel, path: Path, prompt: str | None) -> tuple[str, float]:
    audio = decode_audio(str(path), sampling_rate=16000)
    segs, _ = model.transcribe(audio, language="uz", beam_size=5, vad_filter=True,
                               condition_on_previous_text=False, initial_prompt=prompt)
    segs = list(segs)
    text = " ".join(s.text.strip() for s in segs).strip()
    import math
    if segs:
        tot = sum(s.end - s.start for s in segs) or 1.0
        lp = sum(s.avg_logprob * (s.end - s.start) for s in segs) / tot
        conf = math.exp(lp) * (1 - max(s.no_speech_prob for s in segs))
    else:
        conf = 0.0
    return text, conf


def eval_fleurs(model, prompt) -> dict:
    rows = {}
    for line in (DATA / "fleurs_uz" / "test.tsv").read_text(encoding="utf-8").splitlines():
        cols = line.split("\t")
        rows[cols[1]] = cols[3]
    we = wn = ce = cn = 0
    files = sorted((DATA / "fleurs_uz" / "audio").glob("*.wav"))
    for f in files:
        if f.name not in rows:
            continue
        hyp, _ = transcribe(model, f, prompt)
        r, h = norm(rows[f.name]), norm(hyp)
        we += edit(r.split(), h.split()); wn += len(r.split())
        ce += edit(list(r), list(h)); cn += len(r)
    return {"n": len(files), "wer": round(100 * we / wn, 1), "cer": round(100 * ce / cn, 1)}


def eval_medical(model, prompt) -> dict:
    cases = [json.loads(l) for l in (DATA / "medical_uz" / "truth.jsonl").read_text(encoding="utf-8").splitlines()]
    tot = correct = wrong = missing = spurious = exact = low_conf = 0
    wrong_examples = []
    for c in cases:
        hyp, conf = transcribe(model, DATA / "medical_uz" / f"{c['id']}.mp3", prompt)
        low_conf += conf < 0.8
        got = {x["field"]: x["value"] for x in parse(hyp, "pregnancy")}
        ok_case = True
        for k, v in c["truth"].items():
            tot += 1
            if k not in got:
                missing += 1; ok_case = False
            elif (abs(float(got[k]) - float(v)) < 0.01 if not isinstance(v, str) else got[k] == v):
                correct += 1
            else:
                wrong += 1; ok_case = False
                if len(wrong_examples) < 6:
                    wrong_examples.append(f"{c['id']} {k}: truth={v} got={got[k]} | {hyp[:90]}")
        spurious += len(set(got) - set(c["truth"]))
        exact += ok_case
    return {"cases": len(cases), "fields": tot, "field_acc": round(100 * correct / tot, 1),
            "wrong_pct": round(100 * wrong / tot, 1), "missing_pct": round(100 * missing / tot, 1),
            "spurious": spurious, "exact_cases_pct": round(100 * exact / len(cases), 1),
            "low_conf_cases": low_conf, "wrong_examples": wrong_examples}


def main() -> None:
    names = sys.argv[1:] or list(CONFIGS)
    out = HERE / "results.json"
    results = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    root = str(MODELS)
    for name in names:
        model_id, prompt = CONFIGS[name]
        t0 = time.time()
        model = WhisperModel(model_id, device="cuda", compute_type="int8_float16", download_root=root)
        med = eval_medical(model, prompt)
        fl = eval_fleurs(model, prompt)
        results[name] = {"medical": med, "fleurs": fl, "seconds": round(time.time() - t0)}
        print(name, json.dumps({**med, "wrong_examples": len(med["wrong_examples"])}), json.dumps(fl), flush=True)
        del model
        out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = ["| config | field acc % | wrong % | missing % | exact cases % | low-conf cases | FLEURS WER % | CER % |",
             "|---|---|---|---|---|---|---|---|"]
    for k, r in results.items():
        m, f = r["medical"], r["fleurs"]
        lines.append(f"| {k} | {m['field_acc']} | {m['wrong_pct']} | {m['missing_pct']} | {m['exact_cases_pct']} | "
                     f"{m['low_conf_cases']}/{m['cases']} | {f['wer']} | {f['cer']} |")
    (HERE / "results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    _ = srv


if __name__ == "__main__":
    main()
