from __future__ import annotations

import re
from dataclasses import asdict, dataclass

# ------------------------------------------------------------------ spoken numbers
_NUM = {
    # uz latin
    "nol": 0, "bir": 1, "ikki": 2, "uch": 3, "to'rt": 4, "besh": 5, "olti": 6, "yetti": 7, "sakkiz": 8, "to'qqiz": 9,
    "o'n": 10, "yigirma": 20, "o'ttiz": 30, "qirq": 40, "ellik": 50, "oltmish": 60, "yetmish": 70, "sakson": 80,
    "to'qson": 90, "yuz": 100,
    # uz cyrillic
    "бир": 1, "икки": 2, "уч": 3, "тўрт": 4, "беш": 5, "олти": 6, "етти": 7, "саккиз": 8, "тўққиз": 9, "ўн": 10,
    "йигирма": 20, "ўттиз": 30, "қирқ": 40, "эллик": 50, "олтмиш": 60, "етмиш": 70, "саксон": 80, "тўқсон": 90, "юз": 100,
    # russian
    "ноль": 0, "один": 1, "одна": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5, "шесть": 6, "семь": 7,
    "восемь": 8, "девять": 9, "десять": 10, "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14,
    "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18, "девятнадцать": 19, "двадцать": 20,
    "тридцать": 30, "сорок": 40, "пятьдесят": 50, "шестьдесят": 60, "семьдесят": 70, "восемьдесят": 80,
    "девяносто": 90, "сто": 100, "двести": 200,
    # english
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100,
}
_DECIMAL_WORDS = {"butun", "бутун", "целых", "целая", "point"}
_APOS = re.compile(r"[‘’ʻʼ`´]")


def _normalise(text: str) -> str:
    t = _APOS.sub("'", text.lower())
    t = re.sub(r"(\d),(\d)", r"\1.\2", t)  # 36,7 -> 36.7
    return t


def words_to_numbers(text: str) -> tuple[str, bool]:
    """Replace runs of number words with digits. Returns (text, used_words)."""
    tokens = re.findall(r"\d+(?:\.\d+)?|[\w'+/-]+|[^\w\s]", text, flags=re.UNICODE)
    out: list[str] = []
    i, used = 0, False

    def read_int(j: int) -> tuple[int | None, int]:
        total, cur, k, seen = 0, 0, j, False
        while k < len(tokens) and tokens[k] in _NUM:
            v = _NUM[tokens[k]]
            seen = True
            if v == 100:
                cur = (cur or 1) * 100
            elif v == 200:
                cur += 200
            else:
                cur += v
            k += 1
        total += cur
        return (total if seen else None), k

    while i < len(tokens):
        val, j = read_int(i)
        if val is None:
            out.append(tokens[i])
            i += 1
            continue
        used = True
        if j < len(tokens) and tokens[j] in _DECIMAL_WORDS:
            frac, k = read_int(j + 1)
            if frac is not None:
                out.append(f"{val}.{frac}")
                i = k
                continue
        out.append(str(val))
        i = j
    return " ".join(out), used


# ------------------------------------------------------------------ field specs
@dataclass
class Candidate:
    field: str
    value: float | int | str
    unit: str
    confidence: float
    needs_review: bool
    source_text: str
    note: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


NUM = r"(\d{1,4}(?:\.\d{1,2})?)"
GAP = r"[^\d]{0,30}?"

# field: (keywords, unit, int?, plausible input bounds, subjects)
SPECS: dict[str, tuple[list[str], str, bool, tuple[float, float], set[str]]] = {
    "fetal_heart_rate": ([r"h[ao]m[iy]l[ae]\s+yurak(?:\s+uri[sš]h?i)?", r"ҳомила\s+юрак(?:\s+уриши)?", r"чсс\s+плода", r"сердцебиение\s+плода", r"fetal\s+heart(?:\s+rate)?", r"fhr"],
                         "bpm", True, (50, 240), {"pregnancy"}),
    "systolic_bp": ([r"(?:s[iy]?st[oa]lik|st[oa]lik)(?:\s+b[oa]sim\w*)?", r"систолик", r"систолическое", r"yuqori\s+bosim", r"верхнее", r"systolic"],
                    "mmHg", True, (50, 260), {"pregnancy"}),
    "diastolic_bp": ([r"d[iy]ast[oa]lik(?:\s+b[oa]sim\w*)?", r"диастолик", r"диастолическое", r"pastki\s+bosim", r"нижнее", r"diastolic"],
                     "mmHg", True, (30, 170), {"pregnancy"}),
    "heart_rate": ([r"puls", r"пульс", r"yurak\s+urishi", r"юрак\s+уриши", r"чсс", r"heart\s+rate", r"pulse"],
                   "bpm", True, (30, 260), {"pregnancy", "newborn"}),
    "respiratory_rate": ([r"nafas(?:\s+soni)?", r"нафас", r"чдд", r"дыхани[ея]", r"respiratory\s+rate"],
                         "/min", True, (5, 120), {"pregnancy", "newborn"}),
    "temperature_c": ([r"h[ao]r[ao]rat", r"ҳарорат", r"температура", r"temperatura", r"temperature"],
                      "C", False, (30, 43), {"pregnancy", "newborn"}),
    "spo2": ([r"saturats\w*", r"сатурация", r"spo2", r"kislorod", r"кислород", r"saturation"],
             "%", True, (50, 100), {"pregnancy", "newborn"}),
    "gestational_week": ([r"h[ao]m[iy]lad[ao]rlik\s+muddati", r"ҳомиладорлик\s+муддати", r"срок(?:\s+беременности)?", r"gestational\s+age"],
                         "weeks", False, (4, 44), {"pregnancy"}),
    "weight_kg": ([r"vazn(?:i)?", r"вазн", r"вес", r"og'irlik", r"weight"], "kg", False, (30, 200), {"pregnancy"}),
    "weight_g": ([r"vazn(?:i)?", r"вазн", r"вес", r"weight"], "g", True, (300, 6500), {"newborn"}),
    "lab_hemoglobin": ([r"gemoglobin", r"гемоглобин", r"hemoglobin", r"haemoglobin", r"hb"], "g/L", False, (30, 200), {"pregnancy"}),
    "lab_glucose_fasting": ([r"qand", r"қанд", r"glyukoza", r"глюкоза", r"сахар", r"glucose"], "mmol/L", False, (1, 35), {"pregnancy"}),
}
_BP_PAIR = re.compile(r"(?:q[oa]n\s+)?(?:b[oa]sim\w*|давлени[ея]|артериал\w*|blood\s+pressure|қон\s+босими|босим)" + GAP +
                      r"(\d{2,3})\s*(?:ga|на|/|over|\s)\s*(\d{2,3})")
_PROTEIN = re.compile(r"([oa]qsil|оқсил|белок|протеинури\w*|protein\w*)" + r"[^\n.;]{0,25}?(manfiy|отрицательн\w*|negative|izlar|следы|trace|\d\s*(?:\+|plus|плюс)|\+{1,4})")


def parse(transcript: str, subject_type: str) -> list[dict]:
    text, used_words = words_to_numbers(_normalise(transcript))
    cands: list[Candidate] = []
    consumed: list[tuple[int, int]] = []

    def free(a: int, b: int) -> bool:
        return all(b <= x or a >= y for x, y in consumed)

    if subject_type == "pregnancy":
        m = _BP_PAIR.search(text)
        if m:
            sbp, dbp = int(m.group(1)), int(m.group(2))
            ok = 50 <= sbp <= 260 and 30 <= dbp <= 170 and sbp > dbp
            for f, v in (("systolic_bp", sbp), ("diastolic_bp", dbp)):
                cands.append(Candidate(f, v, "mmHg", 0.85 if ok else 0.4, not ok or used_words, m.group(0),
                                       None if ok else "implausible or inverted pair"))
            consumed.append(m.span())
        pm = _PROTEIN.search(text)
        if pm:
            raw = pm.group(2)
            val = "neg" if re.match(r"manfiy|отриц|negative", raw) else "trace" if re.match(r"izlar|след|trace", raw) else \
                (f"{raw.count('+')}+" if raw.startswith("+") else re.sub(r"\s*(plus|плюс)", "+", raw).replace(" ", ""))
            cands.append(Candidate("proteinuria", val, "dipstick", 0.7, True, pm.group(0)))
            consumed.append(pm.span())

    for fname, (kws, unit, is_int, (lo, hi), subjects) in SPECS.items():
        if subject_type not in subjects or any(c.field == fname for c in cands):
            continue
        for kw in kws:
            m = re.search(r"\b" + kw + r"\b" + GAP + NUM, text)
            if not m or not free(*m.span()):
                continue
            raw = float(m.group(1))
            value = int(round(raw)) if is_int else raw
            in_range = lo <= raw <= hi
            note, conf = None, (0.8 if not used_words else 0.65)
            if is_int and raw != int(raw):
                note, conf = "decimal in an integer field - possible misrecognition (e.g. two numbers merged)", 0.4
            if fname == "lab_hemoglobin" and raw < 25:
                note, in_range = "value looks like g/dL - confirm unit (not auto-converted)", False
            if fname == "gestational_week" and not re.search(r"hafta|ҳафта|недел|week", text[m.end():m.end() + 15] + m.group(0)):
                conf -= 0.1
            if not in_range:
                conf = 0.3
                note = note or f"outside plausible input range {lo}-{hi}"
            cands.append(Candidate(fname, value, unit, round(conf, 2), (not in_range) or conf < 0.8, m.group(0), note))
            consumed.append(m.span())
            break
    return [c.as_dict() for c in cands]
