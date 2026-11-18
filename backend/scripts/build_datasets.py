from __future__ import annotations

import csv
import json
import math
import shutil
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pyreadstat
from shapely.geometry import shape

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "processed"

# Official SOATO (MHOBT) region codes <-> ISO 3166-2 <-> MICS6 HH7 code.
REGIONS = [
    # soato, iso, mics6_hh7, name_uz, name_ru, name_en, name_uzc
    ("1735", "UZ-QR", 1, "Qoraqalpog‘iston Respublikasi", "Республика Каракалпакстан", "Republic of Karakalpakstan", "Қорақалпоғистон Республикаси"),
    ("1703", "UZ-AN", 2, "Andijon viloyati", "Андижанская область", "Andijan region", "Андижон вилояти"),
    ("1706", "UZ-BU", 3, "Buxoro viloyati", "Бухарская область", "Bukhara region", "Бухоро вилояти"),
    ("1708", "UZ-JI", 4, "Jizzax viloyati", "Джизакская область", "Jizzakh region", "Жиззах вилояти"),
    ("1710", "UZ-QA", 5, "Qashqadaryo viloyati", "Кашкадарьинская область", "Kashkadarya region", "Қашқадарё вилояти"),
    ("1712", "UZ-NW", 6, "Navoiy viloyati", "Навоийская область", "Navoi region", "Навоий вилояти"),
    ("1714", "UZ-NG", 7, "Namangan viloyati", "Наманганская область", "Namangan region", "Наманган вилояти"),
    ("1718", "UZ-SA", 8, "Samarqand viloyati", "Самаркандская область", "Samarkand region", "Самарқанд вилояти"),
    ("1722", "UZ-SU", 9, "Surxondaryo viloyati", "Сурхандарьинская область", "Surkhandarya region", "Сурхондарё вилояти"),
    ("1724", "UZ-SI", 10, "Sirdaryo viloyati", "Сырдарьинская область", "Syrdarya region", "Сирдарё вилояти"),
    ("1727", "UZ-TO", 11, "Toshkent viloyati", "Ташкентская область", "Tashkent region", "Тошкент вилояти"),
    ("1730", "UZ-FA", 12, "Farg‘ona viloyati", "Ферганская область", "Fergana region", "Фарғона вилояти"),
    ("1733", "UZ-XO", 13, "Xorazm viloyati", "Хорезмская область", "Khorezm region", "Хоразм вилояти"),
    ("1726", "UZ-TK", 14, "Toshkent shahri", "город Ташкент", "Tashkent city", "Тошкент шаҳри"),
]
HH7_TO_SOATO = {r[2]: r[0] for r in REGIONS}
ISO_TO_SOATO = {r[1]: r[0] for r in REGIONS}
NATIONAL = "1700"

# SIAT indicator id -> (internal code, unit, layer). Only indicators used by the platform.
SIAT_MAP = {
    223: ("births_total", "count", "context"),
    586: ("infant_mortality_rate", "per_1000_live_births", "newborn"),
    616: ("infant_deaths", "count", "newborn"),
    535: ("under5_mortality_rate", "per_1000_live_births", "newborn"),
    503: ("under5_deaths", "count", "newborn"),
    662: ("maternal_mortality_ratio", "per_100000_live_births", "maternal"),
    663: ("maternal_deaths", "count", "maternal"),
    576: ("preterm_births", "count", "newborn"),
    664: ("adolescent_birth_rate_15_17", "per_1000_women", "maternal"),
    665: ("total_fertility_rate", "children_per_woman", "context"),
    241: ("general_fertility_rate", "per_1000_women_15_49", "context"),
    2776: ("birth_registration_completeness", "percent", "context"),
    1360: ("obgyn_doctors", "count", "response"),
    2846: ("doctors_total", "count", "response"),
    3193: ("doctors_per_10000", "per_10000_population", "response"),
    4215: ("maternity_beds_per_10000_women", "per_10000_women_15_49", "response"),
    4228: ("maternity_beds", "count", "response"),
    4202: ("abortions_per_1000_women", "per_1000_women_15_49", "maternal"),
    4204: ("abortions_per_100_births", "per_100_births", "maternal"),
    1397: ("underweight_children_pct", "percent", "newborn"),
    3500: ("ipv_women_15_49", "count", "maternal"),
}
# National-only SIAT series (kept for trend/context; region rows not published).
SIAT_NATIONAL_MAP = {
    4236: ("preterm_pct_completed_pregnancies", "percent"),
    4238: ("anaemia_women_completed_pregnancy", "count"),
    4240: ("diabetes_in_pregnancy", "count"),
    4241: ("late_toxicosis_cases", "count"),
    4250: ("labour_bleeding_complications", "count"),
    4253: ("live_births_sick_pct", "percent"),
    4256: ("premature_live_births", "count"),
    3202: ("maternity_beds_per_10000_women_national", "per_10000_women_15_49"),
}


# ----------------------------------------------------------------- survey stats
def survey_proportion(df: pd.DataFrame, y: str, w: str = "w", psu: str = "PSU", strat: str = "stratum") -> dict:
    """Weighted proportion with Taylor-linearised, cluster-robust SE (stratified PSU design)."""
    d = df[[y, w, psu, strat]].dropna()
    d = d[d[w] > 0]
    n = len(d)
    if n == 0:
        return {"n_unweighted": 0}
    W = d[w].sum()
    p = float((d[w] * d[y]).sum() / W)
    d = d.assign(z=d[w] * (d[y] - p) / W)
    zc = d.groupby([strat, psu])["z"].sum().reset_index()
    var = 0.0
    for _, g in zc.groupby(strat):
        nh = len(g)
        if nh > 1:
            var += nh / (nh - 1) * float(((g["z"] - g["z"].mean()) ** 2).sum())
    se = math.sqrt(var)
    lo, hi = max(0.0, p - 1.96 * se), min(1.0, p + 1.96 * se)
    return {
        "n_unweighted": n,
        "weighted_n": round(float(W), 1),
        "numerator_weighted": round(float((d[w] * d[y]).sum()), 2),
        "value": round(100 * p, 2),
        "se": round(100 * se, 3),
        "ci_low": round(100 * lo, 2),
        "ci_high": round(100 * hi, 2),
    }


def suppression(n: int) -> str:
    # MICS convention: <25 unweighted cases suppressed, 25-49 shown with caution.
    if n < 25:
        return "suppressed"
    if n < 50:
        return "low_precision"
    return "none"


def mics6_indicators() -> list[dict]:
    base = RAW / "mics" / "mics6_2021"
    rows: list[dict] = []

    wm, _ = pyreadstat.read_sav(base / "wm.sav", usecols=[
        "WM17", "CM17", "MN2", "MN3A", "MN3B", "MN3C", "MN5", "MN6A", "MN6B", "MN6C", "MN19A", "MN19B", "MN19C",
        "MN20", "MN21", "MN33", "MN34", "MN36", "MN37U", "MN37N", "PN4", "HH7", "wmweight", "PSU", "stratum"])
    wm = wm[(wm.WM17 == 1) & (wm.CM17 >= 1)].copy()
    wm["w"] = wm["wmweight"]
    anc = wm[wm.MN2.notna()].copy()
    anc["anc_skilled"] = ((anc.MN3A == "A") | (anc.MN3B == "B") | (anc.MN3C == "C")).astype(float)
    anc["anc4"] = ((anc.MN5 >= 4) & (anc.MN5 < 98)).astype(float)
    anc["anc_content"] = ((anc.MN6A == 1) & (anc.MN6B == 1) & (anc.MN6C == 1)).astype(float)
    dl = wm[wm.MN20.notna()].copy()
    dl["inst"] = dl.MN20.between(21, 76).astype(float)
    # MN19 (birth attendant) was only asked in the ANC sub-sample (same filter as MN2)
    sba = wm[wm.MN2.notna()].copy()
    sba["sba"] = ((sba.MN19A == "A") | (sba.MN19B == "B") | (sba.MN19C == "C")).astype(float)
    cs = wm[wm.MN21.isin([1, 2])].copy()
    cs["cs"] = (cs.MN21 == 1).astype(float)
    wt = wm[wm.MN33.notna()].copy()
    wt["weighed"] = (wt.MN33 == 1).astype(float)
    lbw = wm[wm.MN34.notna() & (wm.MN34 < 90)].copy()
    lbw["lbw"] = (lbw.MN34 < 2.5).astype(float)
    bf = wm[wm.MN36.notna()].copy()
    bf["ebf1h"] = ((bf.MN37U == 0) | ((bf.MN37U == 1) & (bf.MN37N < 1))).astype(float)
    pn = wm[wm.PN4.isin([1, 2])].copy()
    pn["pn4"] = (pn.PN4 == 1).astype(float)

    specs = [
        ("mics_anc_skilled_any", anc, "anc_skilled", "maternal", "higher_better",
         "Women 15-49 with a live birth in last 2 years who received antenatal care from skilled personnel"),
        ("mics_anc_4plus", anc, "anc4", "response", "higher_better", "Antenatal care at least 4 times"),
        ("mics_anc_content_bp_urine_blood", anc, "anc_content", "response", "higher_better",
         "Blood pressure measured, urine and blood sample taken during ANC"),
        ("mics_institutional_delivery", dl, "inst", "response", "higher_better", "Delivered in a health facility"),
        ("mics_skilled_birth_attendance", sba, "sba", "response", "higher_better", "Delivery assisted by skilled personnel"),
        ("mics_caesarean_rate", cs, "cs", "maternal", "context", "Delivery by caesarean section"),
        ("mics_weighed_at_birth", wt, "weighed", "response", "higher_better", "Last-born child weighed at birth"),
        ("mics_low_birthweight_reported", lbw, "lbw", "newborn", "lower_better",
         "Reported birth weight below 2.5 kg among weighed births (unadjusted)"),
        ("mics_early_breastfeeding_1h", bf, "ebf1h", "newborn", "higher_better", "Put to the breast within one hour of birth"),
        ("mics_newborn_check_before_discharge", pn, "pn4", "response", "higher_better",
         "Newborn health checked before leaving facility (facility births)"),
    ]

    ch, _ = pyreadstat.read_sav(base / "ch.sav", usecols=[
        "HAZ2", "WHZ2", "WAZ2", "HAZFLAG", "WHZFLAG", "WAZFLAG", "chweight", "HH7", "PSU", "stratum"])
    ch["w"] = ch["chweight"]
    st = ch[(ch.HAZ2 < 90) & (ch.HAZFLAG == 0)].copy(); st["y"] = (st.HAZ2 < -2).astype(float)
    ws = ch[(ch.WHZ2 < 90) & (ch.WHZFLAG == 0)].copy(); ws["y"] = (ws.WHZ2 < -2).astype(float)
    uw = ch[(ch.WAZ2 < 90) & (ch.WAZFLAG == 0)].copy(); uw["y"] = (uw.WAZ2 < -2).astype(float)
    ow = ws.copy(); ow["y"] = (ow.WHZ2 > 2).astype(float)
    specs += [
        ("mics_stunting_u5", st, "y", "newborn", "lower_better", "Children under 5 height-for-age below -2 SD (WHO)"),
        ("mics_wasting_u5", ws, "y", "newborn", "lower_better", "Children under 5 weight-for-height below -2 SD"),
        ("mics_underweight_u5", uw, "y", "newborn", "lower_better", "Children under 5 weight-for-age below -2 SD"),
        ("mics_overweight_u5", ow, "y", "newborn", "lower_better", "Children under 5 weight-for-height above +2 SD"),
    ]

    bh, _ = pyreadstat.read_sav(base / "bh.sav", usecols=["BH4C", "BH5", "BH9C", "WDOI", "HH7", "wmweight", "PSU", "stratum"])
    bh["w"] = bh["wmweight"]
    bh["age_m"] = bh.WDOI - bh.BH4C
    nn = bh[(bh.age_m >= 1) & (bh.age_m < 60)].copy()          # births 1-59 months before interview
    nn["y"] = ((bh_dead := nn.BH5 == 2) & (nn.BH9C == 0)).astype(float)
    inf = bh[(bh.age_m >= 12) & (bh.age_m < 72)].copy()         # fully exposed to first year
    inf["y"] = ((inf.BH5 == 2) & (inf.BH9C < 12)).astype(float)
    specs += [
        ("mics_neonatal_death_proportion", nn, "y", "context", "lower_better",
         "Direct proportion of births 1-59 months before survey that died in month 0 (x100 = %); not a life-table rate"),
        ("mics_infant_death_proportion", inf, "y", "context", "lower_better",
         "Direct proportion of births 12-71 months before survey that died before age 12 months (%)"),
    ]

    for code, df, y, layer, direction, desc in specs:
        groups = [(NATIONAL, df)] + [(HH7_TO_SOATO[int(k)], g) for k, g in df.groupby("HH7") if not pd.isna(k)]
        for soato, g in groups:
            r = survey_proportion(g, y)
            n = r.get("n_unweighted", 0)
            rows.append({
                "indicator_code": code, "region_soato": soato, "layer": layer, "direction": direction,
                "description": desc, "period_start": "2021-01-01", "period_end": "2022-12-31", "unit": "percent",
                **{k: r.get(k) for k in ["value", "se", "ci_low", "ci_high", "n_unweighted", "weighted_n", "numerator_weighted"]},
                "suppression_status": suppression(n),
                "source": "UNICEF MICS6 Uzbekistan 2021-2022 (UZB_2021_MICS_v01_M)",
            })
    return rows


def mics_national_trend(mics6_cache: list[dict]) -> list[dict]:
    """Comparable national indicators across MICS2 (2000), MICS3 (2006), MICS6 (2021-22)."""
    out = []
    # MICS3 2006
    wm3, _ = pyreadstat.read_sav(RAW / "mics/mics3_2006/wm.sav", usecols=["CM12", "MN2A", "MN2B", "MN2C", "MN7A", "MN7B", "MN7C", "MN8", "MN11", "wmweight"])
    w3 = wm3[wm3.CM12 == "Y"].copy()
    def wmean(mask, w):
        return round(100 * float((w * mask).sum() / w.sum()), 2), int(len(w))
    for code, mask in [
        ("anc_skilled_any", (w3.MN2A == "A") | (w3.MN2B == "B") | (w3.MN2C == "C")),
        ("skilled_birth_attendance", (w3.MN7A == "A") | (w3.MN7B == "B") | (w3.MN7C == "C")),
    ]:
        v, n = wmean(mask.astype(float), w3.wmweight)
        out.append({"indicator_code": code, "survey": "MICS3", "year": 2006, "value": v, "n_unweighted": n})
    # MICS2 2000 (numeric 0/1 provider flags, cp1251 labels)
    wm2, _ = pyreadstat.read_sav(RAW / "mics/mics2_2000/UZWm.sav", encoding="cp1251",
                                 usecols=["CM11X", "MN2A", "MN2B", "MN2C", "MN3A", "MN3B", "MN3C", "WMWEIGHT"])
    w2 = wm2[wm2.CM11X == "Y"].copy()
    for code, cols_ in [("anc_skilled_any", ["MN2A", "MN2B", "MN2C"]), ("skilled_birth_attendance", ["MN3A", "MN3B", "MN3C"])]:
        valid = w2[cols_[0]].isin([0, 1])
        sub = w2[valid]
        mask = sum((sub[c] == 1).astype(int) for c in cols_) > 0
        v, n = wmean(mask.astype(float), sub.WMWEIGHT)
        out.append({"indicator_code": code, "survey": "MICS2", "year": 2000, "value": v, "n_unweighted": n})
    # MICS6 2021-22 national values for the same definitions
    m6 = {r["indicator_code"]: r for r in mics6_cache if r["region_soato"] == NATIONAL}
    for code, src in [("anc_skilled_any", "mics_anc_skilled_any"), ("skilled_birth_attendance", "mics_skilled_birth_attendance")]:
        out.append({"indicator_code": code, "survey": "MICS6", "year": 2021, "value": m6[src]["value"], "n_unweighted": m6[src]["n_unweighted"]})
    return out


def siat_rows() -> tuple[list[dict], dict]:
    rows, meta = [], {}
    for sid, (code, unit, layer) in {**SIAT_MAP, **{k: (v[0], v[1], "context") for k, v in SIAT_NATIONAL_MAP.items()}}.items():
        f = RAW / "siat" / f"{sid}.json"
        if not f.exists():
            continue
        d = json.loads(f.read_text(encoding="utf-8"))
        meta[code] = {"siat_id": sid, "siat_code": d["meta"]["code"], "name_en": d["meta"]["name_en"],
                      "name_uz": d["meta"]["name_uz"], "updated": d["meta"].get("updated_xlsx"), "fetched_at": d["fetched_at"]}
        for reg in d["table"]:
            soato = str(reg.get("code"))
            if soato != NATIONAL and soato not in {r[0] for r in REGIONS}:
                continue
            for cell in reg.get("data", []):
                for year, val in cell.items():
                    if val is None or val == "" or (isinstance(val, float) and math.isnan(val)):
                        continue
                    rows.append({"indicator_code": code, "region_soato": soato, "year": int(year), "value": float(val),
                                 "unit": unit, "layer": layer, "siat_id": sid, "siat_code": d["meta"]["code"]})
    return rows, meta


def geo() -> tuple[dict, dict]:
    adm1 = json.loads((RAW / "geo/uzb_ADM1.geojson").read_text(encoding="utf-8"))
    adm2 = json.loads((RAW / "geo/uzb_ADM2.geojson").read_text(encoding="utf-8"))
    reg_shapes = {}
    for f in adm1["features"]:
        soato = ISO_TO_SOATO[f["properties"]["shapeISO"]]
        r = next(x for x in REGIONS if x[0] == soato)
        f["properties"] = {"soato": soato, "iso": r[1], "name_uz": r[3], "name_ru": r[4], "name_en": r[5], "name_uzc": r[6]}
        reg_shapes[soato] = shape(f["geometry"])
    unmatched = 0
    feats = []
    counters: dict[str, int] = {}
    for f in adm2["features"]:
        g = shape(f["geometry"])
        pt = g.representative_point()
        soato = next((s for s, rg in reg_shapes.items() if rg.contains(pt)), None)
        if soato is None:  # fallback: largest overlap
            soato = max(reg_shapes, key=lambda s: reg_shapes[s].intersection(g).area)
            unmatched += 1
        counters[soato] = counters.get(soato, 0) + 1
        f["properties"] = {
            "district_code": f"{soato}-GB{counters[soato]:03d}",
            "code_source": "geoBoundaries shapeID (official SOATO district code mapping pending)",
            "geoboundaries_id": f["properties"]["shapeID"], "name": f["properties"]["shapeName"],
            "region_soato": soato, "centroid": [round(pt.x, 5), round(pt.y, 5)],
        }
        feats.append(f)
    adm2["features"] = feats
    print(f"districts: {len(feats)}, fallback-assigned: {unmatched}")
    return adm1, adm2


def who_rows() -> list[dict]:
    names = {"WHOSIS_000003": "neonatal_mortality_rate", "MDG_0000000026": "maternal_mortality_ratio_modelled",
             "MDG_0000000007": "under5_mortality_rate", "LBW_PREVALENCE": "low_birthweight_prevalence",
             "MDG_0000000025": "skilled_birth_attendance", "WHS4_154": "anc4_coverage"}
    out = []
    for f in (RAW / "who").glob("*.json"):
        code = names.get(f.stem)
        if not code:
            continue
        for x in json.loads(f.read_text(encoding="utf-8")).get("value", []):
            if x.get("Dim1") not in (None, "SEX_BTSX"):
                continue
            out.append({"indicator_code": code, "who_code": f.stem, "year": x["TimeDim"], "value": x["NumericValue"],
                        "low": x.get("Low"), "high": x.get("High")})
    return sorted(out, key=lambda r: (r["indicator_code"], r["year"]))


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    adm1, adm2 = geo()
    (OUT / "regions.geojson").write_text(json.dumps(adm1, ensure_ascii=False), encoding="utf-8")
    (OUT / "districts.geojson").write_text(json.dumps(adm2, ensure_ascii=False), encoding="utf-8")
    siat, siat_meta = siat_rows()
    write_csv(OUT / "siat_indicators.csv", siat)
    mics = mics6_indicators()
    write_csv(OUT / "mics6_region_indicators.csv", mics)
    write_csv(OUT / "mics_national_trend.csv", mics_national_trend(mics))
    write_csv(OUT / "who_national.csv", who_rows())
    shutil.copy(RAW / "uci/maternal_health_risk.csv", OUT / "uci_maternal_health_risk.csv")
    now = datetime.now(timezone.utc).isoformat()
    sources = {
        "built_at": now,
        "sources": [
            {"key": "siat", "name": "Milliy statistika qo'mitasi — SIAT", "url": "https://siat.stat.uz/",
             "license": "Open government data (stat.uz terms)", "kind": "official_statistics", "indicators": siat_meta},
            {"key": "mics6", "name": "UNICEF MICS6 Uzbekistan 2021-2022", "ref": "UZB_2021_MICS_v01_M",
             "citation": "United Nations Children's Fund (UNICEF). Uzbekistan - Multiple Indicator Cluster Survey 2021-2022, Round 6. Ref: UZB_2021_MICS_v01_M.",
             "kind": "household_survey", "note": "Aggregated to region; cluster-robust SE; n<25 suppressed."},
            {"key": "mics_trend", "name": "UNICEF MICS2 (2000), MICS3 (2006)", "kind": "household_survey",
             "note": "Only macro-zones in these rounds -> national trend only."},
            {"key": "geoboundaries", "name": "geoBoundaries gbOpen UZB ADM1/ADM2", "url": "https://www.geoboundaries.org/",
             "license": "ADM1: ODbL 1.0 (OSM); ADM2: CC BY 3.0 IGO", "kind": "geography",
             "note": "Not an official boundary source; district codes are provisional."},
            {"key": "who_gho", "name": "WHO Global Health Observatory", "url": "https://www.who.int/data/gho",
             "kind": "modelled_estimates", "note": "Different methodology from SIAT - never mixed in one series."},
            {"key": "uci_mhr", "name": "UCI Maternal Health Risk (id 863)", "license": "CC BY 4.0",
             "kind": "research_dataset", "note": "Non-Uzbek data; baseline/prototype ML only, shadow mode."},
        ],
    }
    (OUT / "sources.json").write_text(json.dumps(sources, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"siat rows {len(siat)}, mics6 rows {len(mics)} -> {OUT}")


if __name__ == "__main__":
    main()
