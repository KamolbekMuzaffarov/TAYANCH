"""Generate a SYNTHETIC Uzbek clinical-dictation test set with known ground truth (edge-tts voices).

    python stt_server/eval/make_medical_set.py [out_dir] [n]

Only invented values/sentences are sent to the TTS service - no patient data.
Limitations: TTS speech is cleaner than real clinicians (no noise, accents, hesitations).
"""
from __future__ import annotations

import asyncio
import json
import random
import sys
from pathlib import Path

import edge_tts

ONES = ["", "bir", "ikki", "uch", "to'rt", "besh", "olti", "yetti", "sakkiz", "to'qqiz"]
TENS = ["", "o'n", "yigirma", "o'ttiz", "qirq", "ellik", "oltmish", "yetmish", "sakson", "to'qson"]


def words(n: int) -> str:
    parts = []
    if n >= 100:
        parts.append(("bir yuz" if n // 100 == 1 else ONES[n // 100] + " yuz"))
        n %= 100
    if n >= 10:
        parts.append(TENS[n // 10])
        n %= 10
    if n:
        parts.append(ONES[n])
    return " ".join(parts) or "nol"


def dec_words(x: float) -> str:
    i, f = str(x).split(".")
    return f"{words(int(i))} butun {words(int(f))}"


def num(v, spoken: bool) -> str:
    if isinstance(v, float):
        return dec_words(v) if spoken else str(v).replace(".", ",")
    return words(v) if spoken else str(v)


def make_case(rnd: random.Random, i: int) -> dict:
    spoken = rnd.random() < 0.5
    truth, parts = {}, []
    sbp, dbp = rnd.randint(95, 175), rnd.randint(55, 115)
    dbp = min(dbp, sbp - 25)
    truth.update(systolic_bp=sbp, diastolic_bp=dbp)
    if rnd.random() < 0.5:
        parts.append(f"Qon bosimi {num(sbp, spoken)} ga {num(dbp, spoken)}")
    else:
        parts.append(f"Sistolik bosim {num(sbp, spoken)}, diastolik {num(dbp, spoken)}")
    if rnd.random() < 0.9:
        hr = rnd.randint(58, 125); truth["heart_rate"] = hr; parts.append(f"Puls {num(hr, spoken)}")
    if rnd.random() < 0.8:
        t = round(rnd.uniform(35.6, 39.2), 1)
        if t == int(t):
            t += 0.1
        t = round(t, 1); truth["temperature_c"] = t; parts.append(f"Harorat {num(t, spoken)}")
    if rnd.random() < 0.7:
        s = rnd.randint(88, 99); truth["spo2"] = s; parts.append(f"Saturatsiya {num(s, spoken)}")
    if rnd.random() < 0.6:
        f = rnd.randint(105, 170); truth["fetal_heart_rate"] = f; parts.append(f"Homila yurak urishi {num(f, spoken)}")
    if rnd.random() < 0.5:
        w = rnd.randint(12, 40); truth["gestational_week"] = float(w); parts.append(f"Homiladorlik muddati {num(w, spoken)} hafta")
    if rnd.random() < 0.4:
        hb = rnd.randint(68, 135); truth["lab_hemoglobin"] = float(hb); parts.append(f"Gemoglobin {num(hb, spoken)}")
    if rnd.random() < 0.4:
        p = rnd.choice(["manfiy", "bir plus", "ikki plus", "uch plus"])
        truth["proteinuria"] = {"manfiy": "neg", "bir plus": "1+", "ikki plus": "2+", "uch plus": "3+"}[p]
        parts.append(f"Oqsil {p}")
    rate = rnd.choice(["+0%", "+0%", "+15%", "+25%"])
    voice = rnd.choice(["uz-UZ-MadinaNeural", "uz-UZ-SardorNeural"])
    return {"id": f"med{i:03d}", "text": ". ".join(parts) + ".", "truth": truth, "voice": voice, "rate": rate,
            "spoken_numbers": spoken}


async def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / "perinatal-devtools" / "eval_data" / "medical_uz"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    out.mkdir(parents=True, exist_ok=True)
    rnd = random.Random(2026)
    cases = [make_case(rnd, i) for i in range(n)]
    for c in cases:
        await edge_tts.Communicate(c["text"], c["voice"], rate=c["rate"]).save(str(out / f"{c['id']}.mp3"))
    (out / "truth.jsonl").write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in cases), encoding="utf-8")
    print(f"{n} cases -> {out}")


if __name__ == "__main__":
    asyncio.run(main())
