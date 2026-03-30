#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app.py — Sistema Turni Gastronomia (Streamlit)
"""

import streamlit as st
import json
import random
import requests
import base64
from copy import deepcopy

st.set_page_config(page_title="Turni Gastronomia", page_icon="🍖", layout="centered")

DIPENDENTI_NORMALI = ["Martina", "Alessia", "Elena", "Carla", "Matteo"]
GIORNI       = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"]
GIORNI_SHORT = ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"]

# ─────────────────────────────────────────────────────────────────────────────
# GITHUB API
# ─────────────────────────────────────────────────────────────────────────────

def gh_headers():
    return {
        "Authorization": f"token {st.secrets['github']['token']}",
        "Accept": "application/vnd.github.v3+json"
    }

def gh_url():
    repo = st.secrets["github"]["repo"]
    path = st.secrets["github"].get("file_path", "stato.json")
    return f"https://api.github.com/repos/{repo}/contents/{path}"

@st.cache_data(ttl=30)
def carica_stato_github():
    try:
        r = requests.get(gh_url(), headers=gh_headers(), timeout=10)
        if r.status_code == 404:
            return stato_vuoto(), None
        r.raise_for_status()
        data    = r.json()
        content = base64.b64decode(data["content"]).decode("utf-8")
        return json.loads(content), data["sha"]
    except Exception as e:
        st.error(f"Errore lettura GitHub: {e}")
        return stato_vuoto(), None

def salva_stato_github(stato, sha, messaggio="Aggiorna turni"):
    content = json.dumps(stato, ensure_ascii=False, indent=2)
    encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")
    payload = {"message": messaggio, "content": encoded}
    if sha:
        payload["sha"] = sha
    r = requests.put(gh_url(), headers=gh_headers(), json=payload, timeout=10)
    r.raise_for_status()
    st.cache_data.clear()
    return r.json()["content"]["sha"]

def stato_vuoto():
    s = {}
    for d in DIPENDENTI_NORMALI:
        s[d] = {
            "saldo_mattine": 0,
            "saldo_pomeriggi": 0,
            "ultimi_riposi": [],
            "ultimi_pattern": []
        }
    s["_meta"] = {
        "ultimo_input_maurizio": None,
        "stato_pre_ultima_generazione": None
    }
    return s

# ─────────────────────────────────────────────────────────────────────────────
# LOGICA TURNI
# ─────────────────────────────────────────────────────────────────────────────

def _calcola_slot_normali(turni_maurizio):
    maurizio_map = {m["idx"]: m for m in turni_maurizio}
    slot = []
    for i in range(7):
        if i in maurizio_map:
            m = maurizio_map[i]
            if m["modalita"] == "sostituzione":
                sm = 1 if m["turno"] == "M" else 2
                sp = 1 if m["turno"] == "P" else 2
            else:
                sm, sp = 2, 2
        else:
            sm, sp = 2, 2
        slot.append({"M": sm, "P": sp, "tot": sm + sp})
    return slot, maurizio_map

def _giorni_lavoro_target(slot_normali, stato):
    total_slots = sum(s["tot"] for s in slot_normali)
    base        = total_slots // 5
    resto       = total_slots % 5
    saldo       = {d: stato[d]["saldo_mattine"] + stato[d]["saldo_pomeriggi"]
                   for d in DIPENDENTI_NORMALI}
    ordinati    = sorted(DIPENDENTI_NORMALI, key=lambda x: saldo[x])
    target      = {d: base for d in DIPENDENTI_NORMALI}
    for i in range(resto):
        target[ordinati[i]] += 1
    return target

def genera_turni(stato, turni_maurizio):
    slot_normali, maurizio_map = _calcola_slot_normali(turni_maurizio)
    giorni_target = _giorni_lavoro_target(slot_normali, stato)

    riposi_rimasti = {d: 7 - giorni_target[d] for d in DIPENDENTI_NORMALI}
    lavori_rimasti = dict(giorni_target)
    griglia        = {d: [None] * 7 for d in DIPENDENTI_NORMALI}
    sett_m         = {d: 0 for d in DIPENDENTI_NORMALI}
    sett_p         = {d: 0 for d in DIPENDENTI_NORMALI}

    for i in range(7):
        slot_m        = slot_normali[i]["M"]
        slot_p        = slot_normali[i]["P"]
        tot           = slot_normali[i]["tot"]
        n_riposi_oggi = 5 - tot
        giorni_dopo   = 7 - i - 1

        def puo_riposare(d):
            if riposi_rimasti[d] <= 0: return False
            if lavori_rimasti[d] > giorni_dopo: return False
            return True

        def deve_lavorare(d):
            return riposi_rimasti[d] > giorni_dopo

        candidati_riposo = [d for d in DIPENDENTI_NORMALI if puo_riposare(d)]

        def score_rip(d):
            s = riposi_rimasti[d] * 2.0
            if GIORNI[i] in stato[d]["ultimi_riposi"]: s -= 5.0
            if stato[d]["ultimi_pattern"] and stato[d]["ultimi_pattern"][-1][i] == "R": s -= 2.0
            s += random.uniform(0, 0.4)
            return s

        candidati_riposo.sort(key=score_rip, reverse=True)

        riposano_oggi = []
        for d in candidati_riposo:
            if len(riposano_oggi) >= n_riposi_oggi: break
            non_rip = [x for x in DIPENDENTI_NORMALI if x not in riposano_oggi and x != d]
            if len(non_rip) >= tot:
                riposano_oggi.append(d)

        for d in DIPENDENTI_NORMALI:
            if d not in riposano_oggi and len(riposano_oggi) < n_riposi_oggi:
                if riposi_rimasti[d] > 0 and not deve_lavorare(d):
                    riposano_oggi.append(d)

        for d in riposano_oggi:
            griglia[d][i] = "R"
            riposi_rimasti[d] -= 1

        lavorano_oggi = [d for d in DIPENDENTI_NORMALI if d not in riposano_oggi]

        elena_forzata = None
        if i in maurizio_map and "Elena" in lavorano_oggi:
            turno_maur    = maurizio_map[i]["turno"]
            elena_forzata = "P" if turno_maur == "M" else "M"

        assegnati_m, assegnati_p = [], []
        if elena_forzata == "M":
            assegnati_m.append("Elena")
            candidati = [d for d in lavorano_oggi if d != "Elena"]
        elif elena_forzata == "P":
            assegnati_p.append("Elena")
            candidati = [d for d in lavorano_oggi if d != "Elena"]
        else:
            candidati = list(lavorano_oggi)

        def score_mattina(d):
            storico  = stato[d]["saldo_pomeriggi"] - stato[d]["saldo_mattine"]
            corrente = sett_p[d] - sett_m[d]
            penale_m = max(0, sett_m[d] - 2) * 3.0
            return storico + corrente * 2.0 - penale_m + random.uniform(0, 0.4)

        candidati.sort(key=score_mattina, reverse=True)

        for d in candidati:
            if len(assegnati_m) < slot_m: assegnati_m.append(d)
            elif len(assegnati_p) < slot_p: assegnati_p.append(d)

        for d in assegnati_m:
            griglia[d][i] = "M"; lavori_rimasti[d] -= 1; sett_m[d] += 1
        for d in assegnati_p:
            griglia[d][i] = "P"; lavori_rimasti[d] -= 1; sett_p[d] += 1

    return griglia, slot_normali, maurizio_map

def genera_migliore(stato, turni_maurizio, tentativi=60):
    miglior = {"griglia": None, "errori": [], "warnings": [], "score": 9999,
               "slot_normali": None, "maurizio_map": None}
    for _ in range(tentativi):
        griglia, slot_normali, maurizio_map = genera_turni(stato, turni_maurizio)
        errori, warnings = verifica_turni(griglia, slot_normali, maurizio_map)
        score = len(errori) * 100 + len(warnings)
        if score < miglior["score"]:
            miglior = {"griglia": griglia, "errori": errori, "warnings": warnings,
                       "score": score, "slot_normali": slot_normali, "maurizio_map": maurizio_map}
        if score == 0: break
    return miglior

def verifica_turni(griglia, slot_normali, maurizio_map):
    errori, warnings = [], []
    for i, g in enumerate(GIORNI):
        norm_m = [d for d in DIPENDENTI_NORMALI if griglia[d][i] == "M"]
        norm_p = [d for d in DIPENDENTI_NORMALI if griglia[d][i] == "P"]
        maur   = maurizio_map.get(i)
        tot_m  = len(norm_m) + (1 if maur and maur["turno"] == "M" else 0)
        tot_p  = len(norm_p) + (1 if maur and maur["turno"] == "P" else 0)
        if tot_m < 2: errori.append(f"❌ {g}: solo {tot_m} al mattino (min 2)")
        if tot_p < 2: errori.append(f"❌ {g}: solo {tot_p} al pomeriggio (min 2)")
        if tot_m > 3: errori.append(f"❌ {g}: {tot_m} al mattino (max 3)")
        if tot_p > 3: errori.append(f"❌ {g}: {tot_p} al pomeriggio (max 3)")
        if tot_m == 3: warnings.append(f"⚠️ {g}: 3 al mattino (Maurizio in concomitanza)")
        if tot_p == 3: warnings.append(f"⚠️ {g}: 3 al pomeriggio (Maurizio in concomitanza)")
        if maur and griglia["Elena"][i] == maur["turno"]:
            errori.append(f"❌ {g}: Elena e Maurizio nello stesso turno!")
    for d in DIPENDENTI_NORMALI:
        m = griglia[d].count("M")
        p = griglia[d].count("P")
        r = griglia[d].count("R")
        if r == 0: errori.append(f"❌ {d}: nessun giorno di riposo")
        if m + p > 6: errori.append(f"❌ {d}: lavora {m+p} giorni (max 6)")
        if m > 4: warnings.append(f"⚠️ {d}: {m} mattine questa settimana")
        if p > 4: warnings.append(f"⚠️ {d}: {p} pomeriggi questa settimana")
    return errori, warnings

def aggiorna_stato(stato_base, griglia):
    s = deepcopy(stato_base)
    for d in DIPENDENTI_NORMALI:
        s[d]["saldo_mattine"]   += griglia[d].count("M")
        s[d]["saldo_pomeriggi"] += griglia[d].count("P")
        nuovi_riposi = [GIORNI[i] for i in range(7) if griglia[d][i] == "R"]
        s[d]["ultimi_riposi"]  = (s[d]["ultimi_riposi"]  + nuovi_riposi)[-2:]
        s[d]["ultimi_pattern"] = (s[d]["ultimi_pattern"] + [list(griglia[d])])[-2:]
    return s

# ─────────────────────────────────────────────────────────────────────────────
# UI
# ─────────────────────────────────────────────────────────────────────────────

EMOJI = {"M": "🟡", "P": "🔵", "R": "⚪"}

def mostra_griglia(griglia, maurizio_map):
    # Intestazione
    cols = st.columns([2] + [1]*7)
    cols[0].markdown("**Nome**")
    for j, g in enumerate(GIORNI_SHORT):
        cols[j+1].markdown(f"**{g}**")

    st.divider()

    for d in DIPENDENTI_NORMALI:
        cols = st.columns([2] + [1]*7)
        cols[0].markdown(f"**{d}**")
        for j in range(7):
            v = griglia[d][j]
            cols[j+1].markdown(f"{EMOJI.get(v,'?')} {v}")

    # Riga Maurizio
    cols = st.columns([2] + [1]*7)
    cols[0].markdown("**Maurizio**")
    for j in range(7):
        if j in maurizio_map:
            t = maurizio_map[j]["turno"]
            cols[j+1].markdown(f"{EMOJI[t]} {t}")
        else:
            cols[j+1].markdown("/ ")

    st.divider()
    st.markdown("**📊 Riepilogo**")
    for d in DIPENDENTI_NORMALI:
        m = griglia[d].count("M")
        p = griglia[d].count("P")
        r = griglia[d].count("R")
        st.markdown(f"`{d:10}` &nbsp; 🟡 {m}M &nbsp; 🔵 {p}P &nbsp; ⚪ {r}R")

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

st.title("🍖 Turni Gastronomia")

stato, sha = carica_stato_github()
if "_meta" not in stato:
    stato["_meta"] = {"ultimo_input_maurizio": None, "stato_pre_ultima_generazione": None}

tab_genera, tab_rigenera, tab_saldi, tab_reset = st.tabs([
    "📅 Genera", "🔄 Rigenera", "📈 Saldi", "🗑️ Reset"
])

# ── TAB GENERA ──────────────────────────────────────────────────────────────
with tab_genera:
    st.subheader("Input Maurizio")

    quanti = st.number_input("Quanti giorni lavora Maurizio questa settimana?",
                             min_value=1, max_value=7, value=1, step=1)

    turni_maurizio = []
    giorni_usati   = set()

    for i in range(int(quanti)):
        with st.expander(f"📌 Giorno {i+1} di {int(quanti)}", expanded=True):
            opzioni = [g for j, g in enumerate(GIORNI) if j not in giorni_usati]
            giorno  = st.selectbox("Giorno", opzioni, key=f"giorno_{i}")
            idx     = GIORNI.index(giorno)
            giorni_usati.add(idx)

            turno_raw = st.radio("Turno", ["🟡 Mattina (M)", "🔵 Pomeriggio (P)"],
                                 key=f"turno_{i}", horizontal=True)
            turno_val = "M" if "Mattina" in turno_raw else "P"

            mod_raw = st.radio("Modalità",
                               ["👥 Concomitanza — turno sale a 3",
                                "🔄 Sostituzione — Maurizio copre un'assenza"],
                               key=f"mod_{i}")
            mod_val = "concomitanza" if "Concomitanza" in mod_raw else "sostituzione"

            turni_maurizio.append({
                "idx": idx, "giorno": giorno,
                "turno": turno_val, "modalita": mod_val
            })

    st.markdown("---")

    if st.button("🎲 Genera Turni", use_container_width=True, type="primary"):
        with st.spinner("Generazione turni..."):
            risultato = genera_migliore(stato, turni_maurizio)
        st.session_state["risultato"]             = risultato
        st.session_state["turni_maurizio_usati"]  = turni_maurizio
        st.session_state["stato_pre_gen"]         = deepcopy(stato)
        st.session_state["sha_pre_gen"]           = sha

    if "risultato" in st.session_state:
        r = st.session_state["risultato"]

        if r["errori"]:
            for e in r["errori"]: st.error(e)
        if r["warnings"]:
            for w in r["warnings"]: st.warning(w)
        if not r["errori"]:
            st.success("✅ Turni validi — nessun errore")

        mostra_griglia(r["griglia"], r["maurizio_map"])

        col1, col2 = st.columns(2)
        with col1:
            salva_disabled = bool(r["errori"])
            if st.button("💾 Salva", use_container_width=True,
                         type="primary", disabled=salva_disabled):
                try:
                    stato_pre  = st.session_state["stato_pre_gen"]
                    nuovo      = aggiorna_stato(stato_pre, r["griglia"])
                    nuovo["_meta"]["ultimo_input_maurizio"]        = st.session_state["turni_maurizio_usati"]
                    nuovo["_meta"]["stato_pre_ultima_generazione"] = {
                        k: v for k, v in stato_pre.items() if k != "_meta"
                    }
                    salva_stato_github(nuovo, st.session_state["sha_pre_gen"])
                    st.session_state.pop("risultato", None)
                    st.success("✅ Turni salvati!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Errore salvataggio: {e}")

        with col2:
            if st.button("🎲 Rigenera", use_container_width=True):
                with st.spinner("Rigenerazione..."):
                    nuovo_r = genera_migliore(
                        st.session_state["stato_pre_gen"],
                        st.session_state["turni_maurizio_usati"]
                    )
                st.session_state["risultato"] = nuovo_r
                st.rerun()

# ── TAB RIGENERA ────────────────────────────────────────────────────────────
with tab_rigenera:
    st.subheader("🔄 Rigenera ultima settimana")
    st.info("Rigenera i turni dell'ultima settimana **senza modificare lo storico**. "
            "Utile in caso di errori o imprevisti.")

    meta        = stato.get("_meta", {})
    ult_input   = meta.get("ultimo_input_maurizio")
    stato_pre_m = meta.get("stato_pre_ultima_generazione")

    if not ult_input or not stato_pre_m:
        st.warning("⚠️ Nessuna settimana precedente salvata ancora.")
    else:
        st.markdown("**Ultimo input Maurizio utilizzato:**")
        for m in ult_input:
            mod = "concomitanza" if m["modalita"] == "concomitanza" else "sostituzione"
            st.markdown(f"- **{m['giorno']}** — turno `{m['turno']}` ({mod})")

        if st.button("🔄 Rigenera senza toccare lo storico",
                     use_container_width=True, type="primary"):
            base = {d: stato_pre_m[d] for d in DIPENDENTI_NORMALI if d in stato_pre_m}
            base["_meta"] = {"ultimo_input_maurizio": None,
                             "stato_pre_ultima_generazione": None}
            with st.spinner("Rigenerazione..."):
                r = genera_migliore(base, ult_input)
            st.session_state["risultato_rigenera"] = r

        if "risultato_rigenera" in st.session_state:
            r = st.session_state["risultato_rigenera"]
            if r["errori"]:
                for e in r["errori"]: st.error(e)
            if r["warnings"]:
                for w in r["warnings"]: st.warning(w)
            if not r["errori"]:
                st.success("✅ Turni rigenerati (storico invariato)")

            mostra_griglia(r["griglia"], r["maurizio_map"])

            if st.button("🎲 Prova ancora", use_container_width=True):
                base = {d: stato_pre_m[d] for d in DIPENDENTI_NORMALI if d in stato_pre_m}
                base["_meta"] = {"ultimo_input_maurizio": None,
                                 "stato_pre_ultima_generazione": None}
                with st.spinner("Rigenerazione..."):
                    r = genera_migliore(base, ult_input)
                st.session_state["risultato_rigenera"] = r
                st.rerun()

# ── TAB SALDI ───────────────────────────────────────────────────────────────
with tab_saldi:
    st.subheader("📈 Saldi storici")
    st.caption("Usati per bilanciare la distribuzione mattine/pomeriggi nel tempo.")

    for d in DIPENDENTI_NORMALI:
        s    = stato[d]
        diff = s["saldo_mattine"] - s["saldo_pomeriggi"]
        segno = "+" if diff >= 0 else ""
        with st.container(border=True):
            cols = st.columns([2, 1, 1, 1])
            cols[0].markdown(f"**{d}**")
            cols[1].markdown(f"🟡 M: **{s['saldo_mattine']}**")
            cols[2].markdown(f"🔵 P: **{s['saldo_pomeriggi']}**")
            cols[3].markdown(f"diff: `{segno}{diff}`")
            if s["ultimi_riposi"]:
                st.caption(f"Ultimi riposi: {' · '.join(s['ultimi_riposi'])}")

# ── TAB RESET ───────────────────────────────────────────────────────────────
with tab_reset:
    st.subheader("🗑️ Reset storico")
    st.error("⚠️ Azzera tutti i saldi e lo storico. Operazione irreversibile.")

    conferma = st.text_input("Scrivi **RESET** per confermare:")
    if st.button("🗑️ Esegui Reset", use_container_width=True,
                 disabled=(conferma != "RESET"), type="primary"):
        try:
            salva_stato_github(stato_vuoto(), sha, "Reset completo storico")
            st.success("✅ Storico azzerato.")
            st.rerun()
        except Exception as e:
            st.error(f"Errore reset: {e}")
