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
GIORNI             = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"]
GIORNI_SHORT       = ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"]
VALORI_CELLA       = ["M", "P", "R", "F"]
EMOJI              = {"M": "🟡", "P": "🔵", "R": "⚪", "F": "🏖️", None: "—"}

# ─────────────────────────────────────────────────────────────────────────────
# GITHUB
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
            "saldo_mattine":   0,
            "saldo_pomeriggi": 0,
            "saldo_riposi":    0,
            "saldo_ferie":     0,
            "ultimi_riposi":   [],
            "ultimi_pattern":  []
        }
    s["_meta"] = {
        "ultimo_input_maurizio":          None,
        "ultimo_input_ferie":             [],
        "stato_pre_ultima_generazione":   None,
        "ultimo_turno_generato":          None,
        "ultimo_maurizio_map":            None,
    }
    return s

# ─────────────────────────────────────────────────────────────────────────────
# ANALISI FATTIBILITÀ
# ─────────────────────────────────────────────────────────────────────────────

def analizza_fattibilita(turni_maurizio, in_ferie):
    attivi = [d for d in DIPENDENTI_NORMALI if d not in in_ferie]
    N      = len(attivi)
    S      = sum(1 for m in turni_maurizio if m["modalita"] == "sostituzione")

    slot_norm = 7 * 4 - S
    slot_nec  = N * 6
    deficit   = slot_nec - slot_norm
    turni_da_3 = max(0, deficit)
    avanzo     = max(0, -deficit)

    if deficit == 0:
        msg = "✅ Configurazione perfetta — 1 riposo esatto per tutti, nessun turno da 3."
        ok  = True
    elif deficit > 0 and turni_da_3 <= 7:
        msg = (f"⚠️ Con questa configurazione servono **{turni_da_3} turni con 3 normali** "
               f"per garantire 1 riposo esatto a tutti.")
        ok  = True
    elif avanzo > 0:
        msg = (f"❌ Configurazione impossibile: ci sono **{avanzo} slot in eccesso** — "
               f"alcuni turni avrebbero solo 1 persona. "
               f"Aumenta i giorni di Maurizio in sostituzione o riduci le ferie.")
        ok  = False
    else:
        msg = "❌ Configurazione non gestibile."
        ok  = False

    return {
        "attivi": attivi, "N": N, "S": S,
        "deficit": deficit, "turni_da_3": turni_da_3, "avanzo": avanzo,
        "ok": ok, "messaggio": msg,
    }

# ─────────────────────────────────────────────────────────────────────────────
# LOGICA TURNI
# ─────────────────────────────────────────────────────────────────────────────

def _calcola_slot_normali(turni_maurizio, in_ferie):
    maurizio_map = {m["idx"]: m for m in turni_maurizio}
    attivi       = [d for d in DIPENDENTI_NORMALI if d not in in_ferie]
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
    return slot, maurizio_map, attivi

def _giorni_lavoro_target(slot_normali, stato, attivi):
    total_slots = sum(s["tot"] for s in slot_normali)
    base        = total_slots // len(attivi)
    resto       = total_slots % len(attivi)
    saldo       = {d: stato[d]["saldo_mattine"] + stato[d]["saldo_pomeriggi"]
                   for d in attivi}
    ordinati    = sorted(attivi, key=lambda x: saldo[x])
    target      = {d: base for d in attivi}
    for i in range(resto):
        target[ordinati[i]] += 1
    return target

def genera_turni(stato, turni_maurizio, in_ferie):
    slot_normali, maurizio_map, attivi = _calcola_slot_normali(turni_maurizio, in_ferie)
    griglia = {}
    for d in DIPENDENTI_NORMALI:
        griglia[d] = ["F"] * 7 if d in in_ferie else [None] * 7

    if not attivi:
        return griglia, slot_normali, maurizio_map

    giorni_target  = _giorni_lavoro_target(slot_normali, stato, attivi)
    riposi_rimasti = {d: 7 - giorni_target[d] for d in attivi}
    lavori_rimasti = dict(giorni_target)
    sett_m = {d: 0 for d in attivi}
    sett_p = {d: 0 for d in attivi}

    for i in range(7):
        slot_m        = slot_normali[i]["M"]
        slot_p        = slot_normali[i]["P"]
        tot           = slot_normali[i]["tot"]
        n_riposi_oggi = len(attivi) - tot
        giorni_dopo   = 7 - i - 1

        def puo_riposare(d):
            if riposi_rimasti[d] <= 0: return False
            if lavori_rimasti[d] > giorni_dopo: return False
            return True

        def deve_lavorare(d):
            return riposi_rimasti[d] > giorni_dopo

        candidati_riposo = [d for d in attivi if puo_riposare(d)]

        def score_rip(d):
            s = riposi_rimasti[d] * 2.0
            if GIORNI[i] in stato[d]["ultimi_riposi"]: s -= 5.0
            if stato[d]["ultimi_pattern"] and stato[d]["ultimi_pattern"][-1][i] == "R":
                s -= 2.0
            s += random.uniform(0, 0.4)
            return s

        candidati_riposo.sort(key=score_rip, reverse=True)

        riposano_oggi = []
        for d in candidati_riposo:
            if len(riposano_oggi) >= n_riposi_oggi: break
            non_rip = [x for x in attivi if x not in riposano_oggi and x != d]
            if len(non_rip) >= tot:
                riposano_oggi.append(d)

        for d in attivi:
            if d not in riposano_oggi and len(riposano_oggi) < n_riposi_oggi:
                if riposi_rimasti[d] > 0 and not deve_lavorare(d):
                    riposano_oggi.append(d)

        for d in riposano_oggi:
            griglia[d][i] = "R"
            riposi_rimasti[d] -= 1

        lavorano_oggi = [d for d in attivi if d not in riposano_oggi]

        elena_forzata = None
        if i in maurizio_map and "Elena" in lavorano_oggi:
            elena_forzata = "P" if maurizio_map[i]["turno"] == "M" else "M"

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
            if len(assegnati_m) < slot_m:   assegnati_m.append(d)
            elif len(assegnati_p) < slot_p: assegnati_p.append(d)

        for d in assegnati_m:
            griglia[d][i] = "M"; lavori_rimasti[d] -= 1; sett_m[d] += 1
        for d in assegnati_p:
            griglia[d][i] = "P"; lavori_rimasti[d] -= 1; sett_p[d] += 1

    return griglia, slot_normali, maurizio_map

def genera_migliore(stato, turni_maurizio, in_ferie, tentativi=60):
    miglior = {"griglia": None, "errori": [], "warnings": [], "score": 9999,
               "slot_normali": None, "maurizio_map": None}
    for _ in range(tentativi):
        griglia, slot_normali, maurizio_map = genera_turni(stato, turni_maurizio, in_ferie)
        errori, warnings = verifica_turni(griglia, slot_normali, maurizio_map, in_ferie)
        score = len(errori) * 100 + len(warnings)
        if score < miglior["score"]:
            miglior = {"griglia": griglia, "errori": errori, "warnings": warnings,
                       "score": score, "slot_normali": slot_normali, "maurizio_map": maurizio_map}
        if score == 0: break
    return miglior

def verifica_turni(griglia, slot_normali, maurizio_map, in_ferie):
    errori, warnings = [], []
    attivi = [d for d in DIPENDENTI_NORMALI if d not in in_ferie]

    for i, g in enumerate(GIORNI):
        norm_m = [d for d in attivi if griglia[d][i] == "M"]
        norm_p = [d for d in attivi if griglia[d][i] == "P"]
        maur   = maurizio_map.get(i)
        tot_m  = len(norm_m) + (1 if maur and maur["turno"] == "M" else 0)
        tot_p  = len(norm_p) + (1 if maur and maur["turno"] == "P" else 0)

        if tot_m < 2: errori.append(f"❌ {g}: solo {tot_m} al mattino (min 2)")
        if tot_p < 2: errori.append(f"❌ {g}: solo {tot_p} al pomeriggio (min 2)")
        if tot_m > 3: errori.append(f"❌ {g}: {tot_m} al mattino (max 3)")
        if tot_p > 3: errori.append(f"❌ {g}: {tot_p} al pomeriggio (max 3)")
        if tot_m == 3: warnings.append(f"⚠️ {g}: 3 al mattino")
        if tot_p == 3: warnings.append(f"⚠️ {g}: 3 al pomeriggio")
        if maur and griglia.get("Elena", [None]*7)[i] not in ("F", None) and \
           griglia["Elena"][i] == maur["turno"]:
            errori.append(f"❌ {g}: Elena e Maurizio nello stesso turno!")

    for d in attivi:
        m = griglia[d].count("M")
        p = griglia[d].count("P")
        r = griglia[d].count("R")
        if r == 0: errori.append(f"❌ {d}: nessun giorno di riposo")
        if m + p > 6: errori.append(f"❌ {d}: lavora {m+p} giorni (max 6)")
        if m > 4: warnings.append(f"⚠️ {d}: {m} mattine questa settimana")
        if p > 4: warnings.append(f"⚠️ {d}: {p} pomeriggi questa settimana")

    return errori, warnings

def aggiorna_stato(stato_base, griglia, in_ferie):
    s = deepcopy(stato_base)
    for d in DIPENDENTI_NORMALI:
        if d in in_ferie:
            s[d]["saldo_ferie"] += 7
            continue
        s[d]["saldo_mattine"]   += griglia[d].count("M")
        s[d]["saldo_pomeriggi"] += griglia[d].count("P")
        s[d]["saldo_riposi"]    += griglia[d].count("R")
        nuovi_riposi = [GIORNI[i] for i in range(7) if griglia[d][i] == "R"]
        s[d]["ultimi_riposi"]  = (s[d]["ultimi_riposi"]  + nuovi_riposi)[-2:]
        s[d]["ultimi_pattern"] = (s[d]["ultimi_pattern"] + [list(griglia[d])])[-2:]
    return s

# ─────────────────────────────────────────────────────────────────────────────
# UI COMPONENTI
# ─────────────────────────────────────────────────────────────────────────────

def mostra_griglia(griglia, maurizio_map, in_ferie=None, readonly=True, key_prefix="view"):
    griglia_out = deepcopy(griglia)
    in_ferie    = in_ferie or []

    cols = st.columns([2] + [1] * 7)
    cols[0].markdown("**👤**")
    for j, g in enumerate(GIORNI_SHORT):
        cols[j+1].markdown(f"**{g}**")
    st.divider()

    for d in DIPENDENTI_NORMALI:
        cols = st.columns([2] + [1] * 7)
        nome_label = f"**{d}**" + (" 🏖️" if d in in_ferie else "")
        cols[0].markdown(nome_label)
        for j in range(7):
            v = griglia_out[d][j]
            if not readonly and d not in in_ferie:
                scelta = cols[j+1].selectbox(
                    label="", options=VALORI_CELLA,
                    index=VALORI_CELLA.index(v) if v in VALORI_CELLA else 0,
                    key=f"{key_prefix}_{d}_{j}",
                    label_visibility="collapsed"
                )
                griglia_out[d][j] = scelta
            else:
                cols[j+1].markdown(f"{EMOJI.get(v, '?')} {v or '—'}")

    cols = st.columns([2] + [1] * 7)
    cols[0].markdown("**Maurizio**")
    for j in range(7):
        if j in maurizio_map:
            t   = maurizio_map[j]["turno"]
            mod = "🔄" if maurizio_map[j]["modalita"] == "sostituzione" else "👥"
            cols[j+1].markdown(f"{EMOJI[t]} {t}{mod}")
        else:
            cols[j+1].markdown("·")

    st.divider()
    attivi = [d for d in DIPENDENTI_NORMALI if d not in in_ferie]
    st.markdown("**📊 Riepilogo**")
    for d in attivi:
        m = griglia_out[d].count("M")
        p = griglia_out[d].count("P")
        r = griglia_out[d].count("R")
        st.markdown(f"`{d:10}` &nbsp; 🟡 {m}M &nbsp; 🔵 {p}P &nbsp; ⚪ {r}R")
    for d in in_ferie:
        st.markdown(f"`{d:10}` &nbsp; 🏖️ Ferie / Malattia (intera settimana)")

    return griglia_out

def mostra_esito(r):
    if r["errori"]:
        for e in r["errori"]: st.error(e)
    if r["warnings"]:
        for w in r["warnings"]: st.warning(w)
    if not r["errori"]:
        st.success("✅ Turni validi — nessun errore")

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

st.title("🍖 Turni Gastronomia")

stato, sha = carica_stato_github()

# migrazione stato vecchio
if "_meta" not in stato:
    stato["_meta"] = {
        "ultimo_input_maurizio":        None,
        "ultimo_input_ferie":           [],
        "stato_pre_ultima_generazione": None,
        "ultimo_turno_generato":        None,
        "ultimo_maurizio_map":          None,
    }
for d in DIPENDENTI_NORMALI:
    stato[d].setdefault("saldo_riposi", 0)
    stato[d].setdefault("saldo_ferie",  0)

tab_genera, tab_ultimo, tab_rigenera, tab_saldi, tab_reset = st.tabs([
    "📅 Genera", "📋 Ultimo turno", "🔄 Rigenera", "📈 Saldi", "🗑️ Reset"
])

# ══════════════════════════════════════════════════════════════════════════════
# TAB GENERA
# ══════════════════════════════════════════════════════════════════════════════
with tab_genera:

    st.subheader("1. Input Maurizio")
    quanti = st.number_input("Quanti giorni lavora Maurizio questa settimana?",
                             min_value=1, max_value=7, value=1, step=1)
    turni_maurizio = []
    giorni_usati   = set()

    for i in range(int(quanti)):
        with st.expander(f"📌 Giorno {i+1} di {int(quanti)}", expanded=True):
            opzioni   = [g for j, g in enumerate(GIORNI) if j not in giorni_usati]
            giorno    = st.selectbox("Giorno", opzioni, key=f"g_{i}")
            idx       = GIORNI.index(giorno)
            giorni_usati.add(idx)
            turno_raw = st.radio("Turno", ["🟡 Mattina (M)", "🔵 Pomeriggio (P)"],
                                 key=f"t_{i}", horizontal=True)
            turno_val = "M" if "Mattina" in turno_raw else "P"
            mod_raw   = st.radio("Modalità",
                                 ["👥 Concomitanza — turno sale a 3",
                                  "🔄 Sostituzione — Maurizio copre un'assenza"],
                                 key=f"m_{i}")
            mod_val   = "concomitanza" if "Concomitanza" in mod_raw else "sostituzione"
            turni_maurizio.append({"idx": idx, "giorno": giorno,
                                   "turno": turno_val, "modalita": mod_val})

    st.subheader("2. Ferie / Malattia")
    in_ferie = st.multiselect(
        "Dipendenti assenti questa settimana:",
        options=DIPENDENTI_NORMALI, placeholder="Nessun assente"
    )

    st.subheader("3. Verifica configurazione")
    fat = analizza_fattibilita(turni_maurizio, in_ferie)

    if fat["ok"]:
        if fat["turni_da_3"] == 0: st.success(fat["messaggio"])
        else:                       st.warning(fat["messaggio"])
    else:
        st.error(fat["messaggio"])

    st.caption(
        f"Attivi: **{', '.join(fat['attivi'])}** ({fat['N']})  |  "
        f"Maurizio sostituzione: **{fat['S']} giorni**  |  "
        f"Slot disponibili: **{28 - fat['S']}**  |  "
        f"Slot necessari (1R esatto): **{fat['N'] * 6}**"
    )

    scelta_fat = None
    if not fat["ok"]:
        st.markdown("---")
        st.markdown("**Cosa vuoi fare?**")
        scelta_fat = st.radio("", [
            "🔧 Modifica i parametri sopra",
            "⚠️ Genera comunque (qualche turno potrebbe avere meno di 2 persone)",
            "✏️ Genera e poi editing manuale"
        ], label_visibility="collapsed")

    can_genera = fat["ok"] or (scelta_fat is not None and "Modifica" not in scelta_fat)

    st.markdown("---")
    if st.button("🎲 Genera Turni", use_container_width=True,
                 type="primary", disabled=not can_genera):
        with st.spinner("Generazione in corso..."):
            risultato = genera_migliore(stato, turni_maurizio, in_ferie)
        st.session_state.update({
            "risultato":            risultato,
            "turni_maurizio_usati": turni_maurizio,
            "in_ferie_usate":       in_ferie,
            "stato_pre_gen":        deepcopy(stato),
            "sha_pre_gen":          sha,
            "editing_attivo":       scelta_fat is not None and "manuale" in scelta_fat,
            "griglia_edit":         deepcopy(risultato["griglia"]),
        })

    if "risultato" in st.session_state:
        r = st.session_state["risultato"]
        st.markdown("---")
        mostra_esito(r)

        if st.session_state.get("editing_attivo"):
            st.info("✏️ Modalità editing — modifica le celle poi clicca Verifica.")
            griglia_mod = mostra_griglia(
                st.session_state["griglia_edit"], r["maurizio_map"],
                in_ferie=st.session_state["in_ferie_usate"],
                readonly=False, key_prefix="edit_gen"
            )
            st.session_state["griglia_edit"] = griglia_mod
            if st.button("🔍 Verifica modifiche", use_container_width=True):
                e2, w2 = verifica_turni(griglia_mod, r["slot_normali"],
                                        r["maurizio_map"], st.session_state["in_ferie_usate"])
                st.session_state["risultato"]["griglia"]  = griglia_mod
                st.session_state["risultato"]["errori"]   = e2
                st.session_state["risultato"]["warnings"] = w2
                st.rerun()
        else:
            mostra_griglia(r["griglia"], r["maurizio_map"],
                           in_ferie=st.session_state.get("in_ferie_usate", []),
                           readonly=True)
            if st.button("✏️ Modifica manualmente", use_container_width=True):
                st.session_state["editing_attivo"] = True
                st.session_state["griglia_edit"]   = deepcopy(r["griglia"])
                st.rerun()

        col1, col2 = st.columns(2)
        with col1:
            if st.button("💾 Salva", use_container_width=True,
                         type="primary", disabled=bool(r["errori"])):
                try:
                    griglia_finale = (st.session_state["griglia_edit"]
                                      if st.session_state.get("editing_attivo")
                                      else r["griglia"])
                    stato_pre = st.session_state["stato_pre_gen"]
                    nuovo     = aggiorna_stato(stato_pre, griglia_finale,
                                              st.session_state["in_ferie_usate"])
                    mmap_ser  = {str(k): v for k, v in r["maurizio_map"].items()}
                    nuovo["_meta"].update({
                        "ultimo_input_maurizio":        st.session_state["turni_maurizio_usati"],
                        "ultimo_input_ferie":           st.session_state["in_ferie_usate"],
                        "stato_pre_ultima_generazione": {k: v for k, v in stato_pre.items()
                                                         if k != "_meta"},
                        "ultimo_turno_generato":        griglia_finale,
                        "ultimo_maurizio_map":          mmap_ser,
                    })
                    salva_stato_github(nuovo, st.session_state["sha_pre_gen"])
                    for k in ["risultato", "editing_attivo", "griglia_edit",
                              "turni_maurizio_usati", "in_ferie_usate",
                              "stato_pre_gen", "sha_pre_gen"]:
                        st.session_state.pop(k, None)
                    st.success("✅ Turni salvati!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Errore salvataggio: {e}")
        with col2:
            if st.button("🎲 Rigenera", use_container_width=True):
                with st.spinner("Rigenerazione..."):
                    nuovo_r = genera_migliore(
                        st.session_state["stato_pre_gen"],
                        st.session_state["turni_maurizio_usati"],
                        st.session_state["in_ferie_usate"]
                    )
                st.session_state.update({
                    "risultato":      nuovo_r,
                    "griglia_edit":   deepcopy(nuovo_r["griglia"]),
                    "editing_attivo": False,
                })
                st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# TAB ULTIMO TURNO
# ══════════════════════════════════════════════════════════════════════════════
with tab_ultimo:
    st.subheader("📋 Ultimo turno salvato")
    meta      = stato.get("_meta", {})
    ult_grig  = meta.get("ultimo_turno_generato")
    ult_mmap  = meta.get("ultimo_maurizio_map")
    ult_ferie = meta.get("ultimo_input_ferie", [])

    if not ult_grig or not ult_mmap:
        st.info("Nessun turno ancora salvato.")
    else:
        mmap_int = {int(k): v for k, v in ult_mmap.items()}
        mostra_griglia(ult_grig, mmap_int, in_ferie=ult_ferie,
                       readonly=True, key_prefix="ultimo")
        if ult_ferie:
            st.caption(f"🏖️ Assenti quella settimana: {', '.join(ult_ferie)}")

# ══════════════════════════════════════════════════════════════════════════════
# TAB RIGENERA
# ══════════════════════════════════════════════════════════════════════════════
with tab_rigenera:
    st.subheader("🔄 Rigenera ultima settimana")
    st.info("Rigenera i turni dell'ultima settimana **senza modificare lo storico**. "
            "Utile in caso di errori o imprevisti.")

    meta        = stato.get("_meta", {})
    ult_input   = meta.get("ultimo_input_maurizio")
    ult_ferie_r = meta.get("ultimo_input_ferie", [])
    stato_pre_m = meta.get("stato_pre_ultima_generazione")

    if not ult_input or not stato_pre_m:
        st.warning("⚠️ Nessuna settimana precedente salvata ancora.")
    else:
        st.markdown("**Ultimo input Maurizio:**")
        for m in ult_input:
            st.markdown(f"- **{m['giorno']}** — `{m['turno']}` ({m['modalita']})")
        if ult_ferie_r:
            st.markdown(f"**Assenti:** {', '.join(ult_ferie_r)}")

        if st.button("🔄 Rigenera senza toccare lo storico",
                     use_container_width=True, type="primary"):
            base = {d: stato_pre_m[d] for d in DIPENDENTI_NORMALI if d in stato_pre_m}
            for d in base:
                base[d].setdefault("saldo_riposi", 0)
                base[d].setdefault("saldo_ferie",  0)
            base["_meta"] = {k: None for k in ["ultimo_input_maurizio",
                             "ultimo_input_ferie", "stato_pre_ultima_generazione",
                             "ultimo_turno_generato", "ultimo_maurizio_map"]}
            with st.spinner("Rigenerazione..."):
                r = genera_migliore(base, ult_input, ult_ferie_r)
            st.session_state["risultato_rigenera"]    = r
            st.session_state["editing_rigenera"]      = False
            st.session_state["griglia_edit_rig"]      = deepcopy(r["griglia"])

        if "risultato_rigenera" in st.session_state:
            r = st.session_state["risultato_rigenera"]
            mostra_esito(r)

            if st.session_state.get("editing_rigenera"):
                st.info("✏️ Modalità editing — le modifiche non toccano lo storico.")
                griglia_mod = mostra_griglia(
                    st.session_state["griglia_edit_rig"], r["maurizio_map"],
                    in_ferie=ult_ferie_r, readonly=False, key_prefix="edit_rig"
                )
                st.session_state["griglia_edit_rig"] = griglia_mod
                if st.button("🔍 Verifica modifiche (rigenera)", use_container_width=True):
                    e2, w2 = verifica_turni(griglia_mod, r["slot_normali"],
                                            r["maurizio_map"], ult_ferie_r)
                    st.session_state["risultato_rigenera"]["griglia"]  = griglia_mod
                    st.session_state["risultato_rigenera"]["errori"]   = e2
                    st.session_state["risultato_rigenera"]["warnings"] = w2
                    st.rerun()
            else:
                mostra_griglia(r["griglia"], r["maurizio_map"],
                               in_ferie=ult_ferie_r, readonly=True, key_prefix="rig_view")

            col1, col2 = st.columns(2)
            with col1:
                if st.button("🎲 Prova ancora", use_container_width=True):
                    base = {d: stato_pre_m[d] for d in DIPENDENTI_NORMALI if d in stato_pre_m}
                    for d in base:
                        base[d].setdefault("saldo_riposi", 0)
                        base[d].setdefault("saldo_ferie",  0)
                    base["_meta"] = {k: None for k in ["ultimo_input_maurizio",
                                     "ultimo_input_ferie", "stato_pre_ultima_generazione",
                                     "ultimo_turno_generato", "ultimo_maurizio_map"]}
                    with st.spinner("Rigenerazione..."):
                        r = genera_migliore(base, ult_input, ult_ferie_r)
                    st.session_state.update({
                        "risultato_rigenera": r,
                        "editing_rigenera":   False,
                        "griglia_edit_rig":   deepcopy(r["griglia"])
                    })
                    st.rerun()
            with col2:
                if st.button("✏️ Modifica manualmente ", use_container_width=True):
                    st.session_state["editing_rigenera"] = True
                    st.session_state["griglia_edit_rig"] = deepcopy(r["griglia"])
                    st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# TAB SALDI
# ══════════════════════════════════════════════════════════════════════════════
with tab_saldi:
    st.subheader("📈 Saldi storici")
    st.caption("Usati per bilanciare la distribuzione dei turni nel tempo.")

    for d in DIPENDENTI_NORMALI:
        s     = stato[d]
        diff  = s["saldo_mattine"] - s["saldo_pomeriggi"]
        segno = "+" if diff >= 0 else ""
        with st.container(border=True):
            st.markdown(f"**{d}**")
            c = st.columns(4)
            c[0].metric("🟡 Mattine",   s["saldo_mattine"])
            c[1].metric("🔵 Pomeriggi", s["saldo_pomeriggi"])
            c[2].metric("⚪ Riposi",     s["saldo_riposi"])
            c[3].metric("🏖️ Ferie/Mal", s["saldo_ferie"])
            note = f"M vs P: {segno}{diff}"
            if s["ultimi_riposi"]:
                note += f"  |  Ultimi riposi: {' · '.join(s['ultimi_riposi'])}"
            st.caption(note)

# ══════════════════════════════════════════════════════════════════════════════
# TAB RESET
# ══════════════════════════════════════════════════════════════════════════════
with tab_reset:
    st.subheader("🗑️ Reset storico")
    st.error("⚠️ Azzera tutti i saldi e lo storico. Operazione irreversibile.")
    conferma = st.text_input("Scrivi RESET per confermare:")
    if st.button("🗑️ Esegui Reset", use_container_width=True,
                 disabled=(conferma != "RESET"), type="primary"):
        try:
            salva_stato_github(stato_vuoto(), sha, "Reset completo storico")
            st.success("✅ Storico azzerato.")
            st.rerun()
        except Exception as e:
            st.error(f"Errore reset: {e}")