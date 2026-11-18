from __future__ import annotations

import re

from app.services.trend import SYMPTOM_UZ, num
from app.services.voice import parser

_NUM_WORDS = sorted((w for w in parser._NUM if re.fullmatch(r"[a-z']+", w)), key=len, reverse=True)
_SUFFIX = re.compile(r"\b(" + "|".join(re.escape(w) for w in _NUM_WORDS) + r")(ga|ka|qa)\b")
_PROT_VALUES = {"neg", "trace", "1+", "2+", "3+", "4+"}
_NEG = r"(?:\s+[\w']+){0,2}?\s+(?:yo'q|emas|kuzatilmadi|kuzatilmayapti|bo'lmadi)"


def _symptom(text: str, pattern: str, negative_stem: str | None = None) -> bool:
    for m in re.finditer(pattern, text):
        if negative_stem and re.search(negative_stem, m.group(0)):
            continue
        if re.match(_NEG, text[m.end():]):
            continue
        return True
    return False


def parse_visit(raw: str) -> dict:
    t = parser._normalise(raw or "")
    t = re.sub(r"(\d)(ga|ka|qa)\b", r"\1 \2", t)          # "150ga 95" -> "150 ga 95"
    t = _SUFFIX.sub(r"\1 ga", t)                          # "ellikka" -> "ellik ga"
    t = re.sub(r"\b(?:a/?d|qon\s+bosimi?|arterial\s+bosim[\w']*)\b", "bosim", t)
    cands = {c["field"]: c for c in parser.parse(t, "pregnancy")}
    digits, _ = parser.words_to_numbers(t)
    low = False
    f: dict = {"systolic_bp": None, "diastolic_bp": None, "heart_rate": None, "temperature_c": None, "weight_kg": None,
               "hemoglobin_g_l": None, "glucose_mmol_l": None, "proteinuria": None}

    def take(src: str, dst: str, cast=float):
        nonlocal low
        c = cands.get(src)
        if not c:
            return
        if c.get("note") and c["confidence"] <= 0.4:   # implausible / wrong unit: do not pre-fill
            low = True
            return
        f[dst] = cast(c["value"])
        if c["confidence"] < 0.6:
            low = True

    take("systolic_bp", "systolic_bp", int)
    take("diastolic_bp", "diastolic_bp", int)
    take("heart_rate", "heart_rate", int)
    take("temperature_c", "temperature_c", float)
    take("weight_kg", "weight_kg", float)
    take("lab_hemoglobin", "hemoglobin_g_l", float)
    take("lab_glucose_fasting", "glucose_mmol_l", float)
    if f["systolic_bp"] is None and f["diastolic_bp"] is None:
        m = re.search(r"\bbosim[\w']*\D{0,12}?(\d{2,3})\b(?!\s*(?:ga|/|\d))", digits)
        pair = None
        for pm in re.finditer(r"\b(\d{2,3})\s*(?:ga|/|-|x)?\s*(\d{2,3})\b", digits):
            a, b = int(pm.group(1)), int(pm.group(2))
            if 70 <= a <= 260 and 40 <= b <= 150 and a > b:
                pair = (a, b)
                break
        if pair:
            f["systolic_bp"], f["diastolic_bp"], low = pair[0], pair[1], True
        elif m and 70 <= int(m.group(1)) <= 260:
            f["systolic_bp"], low = int(m.group(1)), True
    # proteinuria
    c = cands.get("proteinuria")
    if c and str(c["value"]) in _PROT_VALUES:
        f["proteinuria"] = str(c["value"])
    else:
        pm = re.search(r"oqsil[\w']*[^.;\n]{0,25}?(yo'q|manfiy|topilmadi|aniqlanmadi|izlar[\w']*|(\d)\s*(?:\+|plyus|plus|krest)|bor\b)", digits)
        if pm:
            g = pm.group(1)
            if pm.group(2):
                f["proteinuria"] = f"{min(4, max(1, int(pm.group(2))))}+"
            elif g.startswith("izlar"):
                f["proteinuria"] = "trace"
            elif g.startswith("bor"):
                f["proteinuria"], low = "1+", True
            else:
                f["proteinuria"] = "neg"
    # symptoms (Uzbek phrases, normalised apostrophes)
    f["severe_headache"] = _symptom(t, r"\bbosh[\w']*\s+(?:[\w']+\s+){0,2}?og'ri[\w']*", r"og'rima") or bool(re.search(r"головн[\w']+\s+бол|голова\s+болит|headache", t))
    f["visual_disturbance"] = _symptom(t, r"\bko'z[\w']*\s+(?:[\w']+\s+){0,2}?(?:xira[\w']*|qorong'i[\w']*|jimirla[\w']*|tin[\w']*|pashsha[\w']*)") \
        or _symptom(t, r"\bko'r(?:ish|ishi)\s+(?:[\w']+\s+)?(?:xira[\w']*|yomon[\w']*|buzil[\w']*|pasay[\w']*)") or bool(re.search(r"в\s+глазах|мушки|blurred", t))
    f["vaginal_bleeding"] = _symptom(t, r"\bqon\s+(?:ket[\w']*|kel[\w']*|oq[\w']*)", r"qon\s+(?:ketma|kelma|oqma)") or bool(re.search(r"кровотечени|bleeding", t))
    rfm = False
    for m in re.finditer(r"\b(?:bola|bolasi|homila|homilasi|chaqaloq)[\w']*\s+((?:[\w']+\s+){0,2}?)qimirla([\w']*)", t):
        mid, tail = m.group(1), m.group(2)
        if re.search(r"\b(?:kam|sust|kamroq|zo'rg'a)\b", mid) or tail.startswith("ma") or re.match(r"[\w']*\s*(?:kamay|sustlash)", t[m.end():].strip()):
            rfm = True
    f["reduced_fetal_movements"] = rfm or bool(re.search(r"\bqimirla[\w']*\s+(?:kamay[\w']*|sezilma[\w']*)|\bkam\s+qimirla|шевелени[\w']+\s+(?:нет|мало)|не\s+шевел", t))
    f["convulsions"] = _symptom(t, r"\b(?:tutqanoq[\w']*|talvasa[\w']*|tirishish[\w']*)") or bool(re.search(r"судорог|convuls|seizure", t))

    understood: list[str] = []
    if f["systolic_bp"] is not None and f["diastolic_bp"] is not None:
        understood.append(f"Bosim {f['systolic_bp']}/{f['diastolic_bp']}")
    elif f["systolic_bp"] is not None:
        understood.append(f"Yuqori bosim {f['systolic_bp']}")
    if f["heart_rate"] is not None:
        understood.append(f"Puls {f['heart_rate']}")
    if f["temperature_c"] is not None:
        understood.append(f"Harorat {num(f['temperature_c'])}")
    if f["weight_kg"] is not None:
        understood.append(f"Vazn {num(f['weight_kg'])} kg")
    if f["hemoglobin_g_l"] is not None:
        understood.append(f"Gemoglobin {num(f['hemoglobin_g_l'])}")
    if f["glucose_mmol_l"] is not None:
        understood.append(f"Qand {num(f['glucose_mmol_l'])}")
    if f["proteinuria"] is not None:
        understood.append("Siydikda oqsil " + {"neg": "yoʻq", "trace": "izlari"}.get(f["proteinuria"], f["proteinuria"]))
    for key, label in SYMPTOM_UZ.items():
        if f[key]:
            understood.append(label)
    if f["systolic_bp"] is not None and f["diastolic_bp"] is not None and f["systolic_bp"] <= f["diastolic_bp"]:
        low = True
    return {"fields": f, "understood": understood, "confidence": "low" if (low or not understood) else "high"}
