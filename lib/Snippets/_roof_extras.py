# -*- coding: utf-8 -*-
"""Ferramentas extras de Cobertura AutoBIM: Calhas/Rufos/Tabeiras 3D e Lista de Corte/Quantitativo."""

import os
import re
import csv
import math
import json
import codecs
import tempfile

try:
    unicode
except NameError:
    unicode = str

try:
    import clr
    clr.AddReference("System")
    clr.AddReference("PresentationFramework")
    clr.AddReference("PresentationCore")
    from System.Collections.Generic import List
    from System.Windows import Visibility as WpfVisibility
    from Autodesk.Revit.DB import (
        BuiltInCategory, BuiltInParameter, CategorySet, Color, Curve,
        CurveLoop, DirectShape, Element, ElementId, ElementTransformUtils,
        ExternalDefinitionCreationOptions, FamilyInstance, FilteredElementCollector,
        GeometryCreationUtilities, GeometryObject, HostObjectUtils, Line,
        LocationCurve, Material, Options, PlanarFace, RoofBase,
        ScheduleFieldDisplayType, ScheduleFieldType, ScheduleFilter,
        ScheduleFilterType, ScheduleSortGroupField, ScheduleSortOrder, Solid,
        SolidOptions, StorageType, Transaction, TransactionGroup, UV,
        ViewDetailLevel, ViewSchedule, Wall, XYZ
    )
    from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
    from pyrevit import forms, script
    HAS_REVIT = True
except Exception:
    HAS_REVIT = False

try:
    import Snippets._roof_framing as rf
except Exception:
    rf = None


doc = None
uidoc = None
view = None

FT = 1.0 / 0.3048
MM = 1.0 / 304.8
SETTINGS_PATH = os.path.join(os.path.dirname(__file__), "_roof_extras_settings.json")

MODOS_ARREDONDAMENTO = (
    u"Exato (1 mm)",
    u"Múltiplos de 1 cm (arred. p/ cima)",
    u"Múltiplos de 5 cm (arred. p/ cima)",
    u"Múltiplos de 10 cm (arred. p/ cima)",
)


def carregar_preferencias():
    try:
        if os.path.exists(SETTINGS_PATH):
            with codecs.open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def salvar_preferencias(dados):
    try:
        atual = carregar_preferencias()
        atual.update(dados)
        with codecs.open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(atual, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# =============================================================================
# FUNCOES PURAS DE GEOMETRIA, PERFIS 2D E LISTA DE CORTE (TESTAVEIS SEM REVIT)
# =============================================================================
def _v_sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _v_add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _v_scale(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def _v_dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _v_cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _v_len(a):
    return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])


def _v_norm(a):
    n = _v_len(a)
    if n < 1e-12:
        return (0.0, 0.0, 0.0)
    return (a[0] / n, a[1] / n, a[2] / n)


def _v_dist(a, b):
    return _v_len(_v_sub(a, b))


def _ensure_ccw_2d(poly):
    """Garante orientacao anti-horaria em poligono 2D [(u, v), ...]."""
    area2 = 0.0
    n = len(poly)
    for i in range(n):
        u1, v1 = poly[i]
        u2, v2 = poly[(i + 1) % n]
        area2 += (u1 * v2 - u2 * v1)
    if area2 < 0.0:
        return list(reversed(poly))
    return list(poly)


def classificar_borda_telhado(p1_xyz, p2_xyz, out_face_xyz, shared_interior=False, tol_z=0.015):
    """
    Classifica uma borda do contorno externo de uma agua de telhado:
      - 'interior'        : borda compartilhada com outra face do mesmo telhado (cumeeira/espigao/rincao)
      - 'beiral'          : borda horizontal na extremidade inferior da agua (ou telhado plano)
      - 'topo_horizontal' : borda horizontal no topo da agua (encontro superior meia-agua)
      - 'oitao'           : borda inclinada livre (empena / oitao)
    """
    if shared_interior:
        return "interior"
    dz = abs(p2_xyz[2] - p1_xyz[2])
    comp_xy = math.hypot(p2_xyz[0] - p1_xyz[0], p2_xyz[1] - p1_xyz[1])
    if dz <= tol_z and comp_xy > 1e-4:
        if out_face_xyz[2] > tol_z:
            return "topo_horizontal"
        return "beiral"
    return "oitao"


def cadeias_bordas_continuas(segs, tol=0.02):
    """
    Conecta segmentos orientados [{'p1': (x,y,z), 'p2': (x,y,z), ...}] em cadeias continuas.
    Retorna lista de dicts: {'segs': [...], 'is_closed': bool}.
    """
    restantes = [dict(s) for s in segs]
    cadeias = []

    def _invert(s):
        ns = dict(s)
        ns["p1"], ns["p2"] = s["p2"], s["p1"]
        return ns

    while restantes:
        atual = [restantes.pop(0)]
        mudou = True
        while mudou and restantes:
            mudou = False
            ini = atual[0]["p1"]
            fim = atual[-1]["p2"]
            best_idx = -1
            best_mode = None
            best_d = tol
            for i, cand in enumerate(restantes):
                d_f1 = _v_dist(fim, cand["p1"])
                if d_f1 < best_d:
                    best_d, best_idx, best_mode = d_f1, i, "append"
                d_f2 = _v_dist(fim, cand["p2"])
                if d_f2 < best_d:
                    best_d, best_idx, best_mode = d_f2, i, "append_inv"
                d_i2 = _v_dist(ini, cand["p2"])
                if d_i2 < best_d:
                    best_d, best_idx, best_mode = d_i2, i, "prepend"
                d_i1 = _v_dist(ini, cand["p1"])
                if d_i1 < best_d:
                    best_d, best_idx, best_mode = d_i1, i, "prepend_inv"

            if best_idx >= 0:
                chosen = restantes.pop(best_idx)
                if best_mode == "append":
                    atual.append(chosen)
                elif best_mode == "append_inv":
                    atual.append(_invert(chosen))
                elif best_mode == "prepend":
                    atual.insert(0, chosen)
                elif best_mode == "prepend_inv":
                    atual.insert(0, _invert(chosen))
                mudou = True

        fechada = False
        if len(atual) >= 3 and _v_dist(atual[0]["p1"], atual[-1]["p2"]) < tol:
            fechada = True
            # Snap exato do fechamento para evitar gap sub-milimetrico no Sweep
            atual[-1]["p2"] = atual[0]["p1"]
        # Snap de vertices intermediarios
        for k in range(len(atual) - 1):
            atual[k + 1]["p1"] = atual[k]["p2"]

        cadeias.append({"segs": atual, "is_closed": fechada})
    return cadeias


def perfil_2d_calha(tipo_idx, w_ft, h_ft, t_ft):
    """
    Gera poligono 2D fechado [(u, v), ...] no plano da secao da Calha:
      - u = horizontal para fora da cobertura (0 = face interna junto ao beiral/parede)
      - v = vertical para cima (0 = topo da calha)
    """
    t = max(0.003 * FT, min(t_ft, min(w_ft, h_ft) * 0.18))
    w = max(t * 5.0, w_ft)
    h = max(t * 5.0, h_ft)

    if tipo_idx == 1:
        # Semicircular / Meia-Cana (retangulo superior reduzido + arco inferior suave)
        r_ext = w * 0.5
        cy = -max(t * 0.5, h - r_ext)
        if cy > -t * 0.4:
            cy = -h * 0.35
            r_ext = min(w * 0.5, h - abs(cy))
        r_int = max(t * 0.8, r_ext - t)
        cx = w * 0.5
        n_arc = 12
        pts_ext = [(0.0, 0.0)]
        for i in range(n_arc + 1):
            ang = math.pi + (math.pi * float(i) / float(n_arc))  # pi -> 2*pi (esq -> baixo -> dir)
            pts_ext.append((cx + r_ext * math.cos(ang), cy + r_ext * math.sin(ang)))
        pts_ext.append((w, 0.0))

        pts_int = [(w - t, 0.0)]
        for i in range(n_arc, -1, -1):
            ang = math.pi + (math.pi * float(i) / float(n_arc))
            pts_int.append((cx + r_int * math.cos(ang), cy + r_int * math.sin(ang)))
        pts_int.append((t, 0.0))
        return _ensure_ccw_2d(pts_ext + pts_int)

    if tipo_idx == 2:
        # Moldura Colonial / Degrau Frontal
        deg_w = min(w * 0.22, t * 5.0 + 0.015 * FT)
        deg_h = min(h * 0.38, max(t * 2.5, h * 0.28))
        pts = [
            (0.0, 0.0),
            (0.0, -h),
            (w - deg_w, -h),
            (w - deg_w, -deg_h),
            (w, -deg_h),
            (w, 0.0),
            (w - t, 0.0),
            (w - t, -deg_h + t),
            (w - deg_w - t, -deg_h + t),
            (w - deg_w - t, -h + t),
            (t, -h + t),
            (t, 0.0),
        ]
        return _ensure_ccw_2d(pts)

    # Padrao 0: Quadrada / Retangular em Perfil U
    pts = [
        (0.0, 0.0),
        (0.0, -h),
        (w, -h),
        (w, 0.0),
        (w - t, 0.0),
        (w - t, -h + t),
        (t, -h + t),
        (t, 0.0),
    ]
    return _ensure_ccw_2d(pts)


def perfil_2d_rufo_capa(wall_w_ft, aba_lat_ft, pingadeira_ft, h_crista_ft, t_ft):
    """
    Gera perfil 2D fechado de Rufo Capa com crista central para topo de platibanda.
      - u = 0 no eixo central da parede
      - v = 0 na face superior da alvenaria/platibanda
    """
    t = max(0.003 * FT, min(t_ft, 0.02 * FT))
    hw = max(0.03 * FT, wall_w_ft * 0.5)
    ping = max(0.01 * FT, pingadeira_ft)
    aba = max(t * 2.5, aba_lat_ft)
    crista = max(0.002 * FT, h_crista_ft)

    u_ext = hw + ping
    u_int = u_ext - t
    pts = [
        (-u_ext, -aba),
        (-u_ext, 0.0),
        (0.0, crista),
        (u_ext, 0.0),
        (u_ext, -aba),
        (u_int, -aba),
        (u_int, -t),
        (0.0, crista - t),
        (-u_int, -t),
        (-u_int, -aba),
    ]
    return _ensure_ccw_2d(pts)


def perfil_2d_rufo_encosto(aba_vert_ft, aba_horiz_ft, dobra_topo_ft, t_ft):
    """
    Gera perfil 2D fechado de Rufo L de Encosto (Telhado x Parede / Oitao).
      - u = 0 na face da parede (+u avanca sobre a cobertura)
      - v = 0 na linha de apoio sobre a cobertura (+v sobe pela parede)
    """
    t = max(0.003 * FT, min(t_ft, 0.02 * FT))
    av = max(t * 4.0, aba_vert_ft)
    ah = max(t * 4.0, aba_horiz_ft)
    dobra = max(0.0, min(dobra_topo_ft, ah * 0.45))

    if dobra > t * 1.1:
        pts = [
            (0.0, 0.0),
            (ah, 0.0),
            (ah, t),
            (t, t),
            (t, av - t),
            (dobra, av - t),
            (dobra, av),
            (0.0, av),
        ]
    else:
        pts = [
            (0.0, 0.0),
            (ah, 0.0),
            (ah, t),
            (t, t),
            (t, av),
            (0.0, av),
        ]
    return _ensure_ccw_2d(pts)


def perfil_2d_tabeira(h_ft, t_ft):
    """Gera perfil 2D retangular fechado para Tabeira / Testeira de beiral ou oitao."""
    t = max(0.008 * FT, t_ft)
    h = max(0.04 * FT, h_ft)
    return _ensure_ccw_2d([(0.0, 0.0), (t, 0.0), (t, -h), (0.0, -h)])


def extrair_dimensoes_por_arestas_e_pontos(edges_segs, pts_xyz):
    """
    Calcula (comp_m, b_cm, h_cm) reais de qualquer barra/perfil 3D prismatico
    a partir de suas arestas retas [(p1, p2), ...] e vertices [(x,y,z), ...].
    Funciona em qualquer inclinacao ou rotacao no espaco 3D.
    """
    if not pts_xyz:
        return (0.0, 0.0, 0.0)

    # 1. Agrupa direcoes unitarias de arestas retas para achar eixo longitudinal
    buckets = []  # [{'dir': (dx,dy,dz), 'max_len': float, 'sum_len': float}]
    for p1, p2 in (edges_segs or []):
        vec = _v_sub(p2, p1)
        L = _v_len(vec)
        if L < 1e-5:
            continue
        d = _v_scale(vec, 1.0 / L)
        achou = False
        for bk in buckets:
            if abs(_v_dot(bk["dir"], d)) > 0.992:
                if L > bk["max_len"]:
                    bk["max_len"] = L
                bk["sum_len"] += L
                achou = True
                break
        if not achou:
            buckets.append({"dir": d, "max_len": L, "sum_len": L})

    if buckets:
        buckets.sort(key=lambda b: (round(b["max_len"], 4), b["sum_len"]), reverse=True)
        axis_u = buckets[0]["dir"]
    else:
        axis_u = (1.0, 0.0, 0.0)

    # 2. Encontra melhor eixo perpendicular para alinhar secao b x h
    cand_v = None
    best_perp_score = -1.0
    for bk in buckets[1:]:
        d = bk["dir"]
        proj = _v_sub(d, _v_scale(axis_u, _v_dot(d, axis_u)))
        plen = _v_len(proj)
        if plen > 0.7:
            score = bk["sum_len"] * plen
            if score > best_perp_score:
                best_perp_score = score
                cand_v = _v_norm(proj)

    if cand_v is None:
        aux = (1.0, 0.0, 0.0) if abs(axis_u[0]) < 0.85 else (0.0, 1.0, 0.0)
        cand_v = _v_norm(_v_cross(axis_u, aux))

    axis_v = cand_v
    axis_w = _v_norm(_v_cross(axis_u, axis_v))

    us = [_v_dot(p, axis_u) for p in pts_xyz]
    vs = [_v_dot(p, axis_v) for p in pts_xyz]
    ws = [_v_dot(p, axis_w) for p in pts_xyz]

    span_u = max(us) - min(us)
    span_v = max(vs) - min(vs)
    span_w = max(ws) - min(ws)

    comp_m = span_u * 0.3048
    d1_cm = span_v * 30.48
    d2_cm = span_w * 30.48
    b_cm, h_cm = (min(d1_cm, d2_cm), max(d1_cm, d2_cm))
    return (comp_m, b_cm, h_cm)


def arredondar_comprimento_corte(comp_m, modo_idx):
    """Arredonda comprimento unitario em metros de acordo com a politica de corte."""
    c = max(0.0, float(comp_m))
    if modo_idx == 1:
        return round(math.ceil(c * 100.0 - 1e-6) / 100.0, 2)
    if modo_idx == 2:
        return round(math.ceil(c * 20.0 - 1e-6) / 20.0, 2)
    if modo_idx == 3:
        return round(math.ceil(c * 10.0 - 1e-6) / 10.0, 2)
    return round(c, 3)


def calcular_barras_comerciais_ffd(comprimentos_m, barra_m=6.0, perda_pct=0.0):
    """
    Otimizacao de corte 1D (First-Fit Decreasing) para barras comerciais.
    Tambem informa metragem linear com perda adicional (%).
    """
    itens = sorted([float(x) for x in comprimentos_m if x > 1e-4], reverse=True)
    if not itens:
        return {
            "qtd_barras": 0,
            "comp_liquido_m": 0.0,
            "comp_comprado_m": 0.0,
            "comp_com_perda_m": 0.0,
            "aproveitamento_pct": 0.0,
            "perda_corte_pct": 0.0,
            "pecas_maiores_que_barra": 0,
        }

    barra = max(0.5, float(barra_m))
    barras_restantes = []
    pecas_maiores = 0
    for c in itens:
        if c > barra + 1e-5:
            # Peca maior que 1 barra comercial: consome int(ceil(c / barra)) barras
            n_b = int(math.ceil(c / barra))
            pecas_maiores += 1
            for _ in range(n_b - 1):
                barras_restantes.append(0.0)
            sobra = n_b * barra - c
            barras_restantes.append(max(0.0, sobra))
            continue

        colocou = False
        for i in range(len(barras_restantes)):
            if barras_restantes[i] + 1e-6 >= c:
                barras_restantes[i] -= c
                colocou = True
                break
        if not colocou:
            barras_restantes.append(max(0.0, barra - c))

    qtd_barras = len(barras_restantes)
    comp_liq = sum(itens)
    comp_comp = qtd_barras * barra
    aprov = (comp_liq / comp_comp * 100.0) if comp_comp > 1e-6 else 0.0
    perda_corte = max(0.0, 100.0 - aprov)
    comp_perda = comp_liq * (1.0 + max(0.0, float(perda_pct)) / 100.0)
    return {
        "qtd_barras": qtd_barras,
        "comp_liquido_m": round(comp_liq, 2),
        "comp_comprado_m": round(comp_comp, 2),
        "comp_com_perda_m": round(comp_perda, 2),
        "aproveitamento_pct": round(aprov, 1),
        "perda_corte_pct": round(perda_corte, 1),
        "pecas_maiores_que_barra": pecas_maiores,
    }


def _normalizar_rotulo_peca(raw_comment, fam_name, type_name, b_cm, h_cm):
    """Limpa comentario/tipo para gerar nome de peca e secao padronizados na Lista de Corte."""
    txt = (raw_comment or u"").strip()
    # Remove sufixo de comprimento individual (C=3.45m) se houver, pois a lista agrupa por peca+secao
    txt = re.sub(r"\s*\(C\s*=\s*[\d\.,]+\s*m\)\s*", u" ", txt, flags=re.IGNORECASE).strip()

    if not txt:
        if type_name and fam_name and type_name.lower() not in fam_name.lower():
            txt = u"{} - {}".format(fam_name, type_name)
        else:
            txt = type_name or fam_name or u"Peça de Cobertura"

    secao = u"{:.1f} x {:.1f} cm".format(b_cm, h_cm) if (b_cm > 0.05 and h_cm > 0.05) else u"-"
    m_sec = re.search(r"(\d+(?:[\.,]\d+)?\s*[xX]\s*\d+(?:[\.,]\d+)?(?:\s*[xX]\s*\d+(?:[\.,]\d+)?)?\s*(?:cm|mm))", txt)
    if m_sec:
        secao = m_sec.group(1).replace("X", "x").strip()

    return txt, secao


def agregar_lista_de_corte(registros, modo_passo=2, barra_m=6.0, perda_pct=10.0):
    """
    Agrega registros [{'id': ..., 'grupo': ..., 'peca': ..., 'secao': ...,
                       'material': ..., 'comp_m': ...}]
    em:
      - linhas_corte: agrupadas por (grupo, peca, secao, material, comp_corte_m) com codigo C-01..
      - resumo_perfis: agrupadas por (grupo, peca, secao, material) com barras comerciais FFD
      - totais: dicionario geral
    """
    grupos_corte = {}
    grupos_perfil = {}

    for reg in registros:
        comp_real = float(reg.get("comp_m", 0.0))
        if comp_real <= 1e-4:
            continue
        comp_corte = arredondar_comprimento_corte(comp_real, modo_passo)
        grupo = reg.get("grupo") or u"Estrutura"
        peca = reg.get("peca") or u"Peça"
        secao = reg.get("secao") or u"-"
        material = reg.get("material") or u"-"

        k_corte = (grupo, peca, secao, material, comp_corte)
        if k_corte not in grupos_corte:
            grupos_corte[k_corte] = {
                "grupo": grupo,
                "peca": peca,
                "secao": secao,
                "material": material,
                "comp_corte_m": comp_corte,
                "comp_real_soma_m": 0.0,
                "qtd": 0,
                "elem_ids": [],
            }
        it = grupos_corte[k_corte]
        it["qtd"] += 1
        it["comp_real_soma_m"] += comp_real
        if reg.get("id") is not None:
            it["elem_ids"].append(reg["id"])

        k_perf = (grupo, peca, secao, material)
        if k_perf not in grupos_perfil:
            grupos_perfil[k_perf] = {
                "grupo": grupo,
                "peca": peca,
                "secao": secao,
                "material": material,
                "comprimentos_corte": [],
                "qtd": 0,
            }
        grupos_perfil[k_perf]["qtd"] += 1
        grupos_perfil[k_perf]["comprimentos_corte"].append(comp_corte)

    ordem_cat = {
        u"Madeiramento": 1,
        u"Estrutura Metálica": 2,
        u"Treliças Metálicas": 3,
        u"Tesouras e Pontaletes": 4,
        u"Vidraça Inclinada": 5,
        u"Calhas, Rufos e Tabeiras": 6,
    }

    linhas_ord = sorted(
        grupos_corte.values(),
        key=lambda x: (
            ordem_cat.get(x["grupo"], 9),
            x["grupo"],
            x["peca"],
            x["secao"],
            x["material"],
            -x["comp_corte_m"],
        ),
    )

    linhas_corte = []
    mapa_cod_elem = {}
    total_pecas = 0
    total_metros = 0.0

    for idx, ln in enumerate(linhas_ord, start=1):
        cod = u"C-{:02d}".format(idx)
        comp_tot = round(ln["comp_corte_m"] * ln["qtd"], 2)
        total_pecas += ln["qtd"]
        total_metros += comp_tot
        row = {
            "codigo": cod,
            "grupo": ln["grupo"],
            "peca": ln["peca"],
            "secao": ln["secao"],
            "material": ln["material"],
            "comp_unit_m": ln["comp_corte_m"],
            "qtd": ln["qtd"],
            "comp_total_m": comp_tot,
            "elem_ids": ln["elem_ids"],
        }
        linhas_corte.append(row)
        for eid in ln["elem_ids"]:
            mapa_cod_elem[eid] = row

    perfis_ord = sorted(
        grupos_perfil.values(),
        key=lambda x: (ordem_cat.get(x["grupo"], 9), x["grupo"], x["peca"], x["secao"], x["material"]),
    )
    resumo_perfis = []
    total_barras = 0
    for pf in perfis_ord:
        ffd = calcular_barras_comerciais_ffd(pf["comprimentos_corte"], barra_m=barra_m, perda_pct=perda_pct)
        total_barras += ffd["qtd_barras"]
        resumo_perfis.append({
            "grupo": pf["grupo"],
            "peca": pf["peca"],
            "secao": pf["secao"],
            "material": pf["material"],
            "qtd_pecas": pf["qtd"],
            "comp_liquido_m": ffd["comp_liquido_m"],
            "barra_m": round(float(barra_m), 2),
            "qtd_barras": ffd["qtd_barras"],
            "comp_comprado_m": ffd["comp_comprado_m"],
            "comp_com_perda_m": ffd["comp_com_perda_m"],
            "aproveitamento_pct": ffd["aproveitamento_pct"],
            "perda_corte_pct": ffd["perda_corte_pct"],
            "pecas_maiores_que_barra": ffd["pecas_maiores_que_barra"],
        })

    return {
        "linhas_corte": linhas_corte,
        "resumo_perfis": resumo_perfis,
        "mapa_cod_elem": mapa_cod_elem,
        "totais": {
            "total_pecas": total_pecas,
            "total_linhas_corte": len(linhas_corte),
            "total_perfis": len(resumo_perfis),
            "total_metros": round(total_metros, 2),
            "total_barras": total_barras,
        },
    }


# =============================================================================
# FERRAMENTA 1: CALHAS, RUFOS E TABEIRAS AUTOMATICOS
# =============================================================================
XAML_CALHAS = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="AutoBIM - Calhas, Rufos e Tabeiras Automáticos"
        Width="545" SizeToContent="Height"
        WindowStartupLocation="CenterScreen" ResizeMode="NoResize"
        Background="#F3F3F3" FontFamily="Segoe UI" FontSize="12">
    <StackPanel Margin="16">
        <Border Background="#E3EEF9" BorderBrush="#0078D4" BorderThickness="0,0,0,2"
                Padding="10,8" Margin="0,0,0,10">
            <TextBlock TextWrapping="Wrap" FontWeight="SemiBold" Foreground="#1F3A57"
                       Text="Gera automaticamente Calhas (Beirais), Tabeiras / Testeiras (Beirais e Oitões) e Rufos (Capa de Platibanda e Encosto em L) em 3D a partir dos telhados e paredes."/>
        </Border>

        <Border Background="White" BorderBrush="#D0D7DE" BorderThickness="1" CornerRadius="4" Padding="10" Margin="0,0,0,8">
            <Grid>
                <Grid.ColumnDefinitions>
                    <ColumnDefinition Width="155"/>
                    <ColumnDefinition Width="*"/>
                </Grid.ColumnDefinitions>
                <TextBlock Text="Escopo de Telhados:" FontWeight="SemiBold" VerticalAlignment="Center"/>
                <ComboBox x:Name="cb_escopo" Grid.Column="1" Height="26"/>
            </Grid>
        </Border>

        <Border Background="White" BorderBrush="#D0D7DE" BorderThickness="1" CornerRadius="4" Padding="10" Margin="0,0,0,8">
            <StackPanel>
                <CheckBox x:Name="ck_calha" Content="Gerar Calhas nos Beirais (bordas horizontais inferiores)" FontWeight="SemiBold" IsChecked="True" Margin="0,0,0,6"/>
                <Grid x:Name="pn_calha">
                    <Grid.RowDefinitions>
                        <RowDefinition Height="Auto"/>
                        <RowDefinition Height="6"/>
                        <RowDefinition Height="Auto"/>
                    </Grid.RowDefinitions>
                    <Grid.ColumnDefinitions>
                        <ColumnDefinition Width="140"/>
                        <ColumnDefinition Width="*"/>
                        <ColumnDefinition Width="12"/>
                        <ColumnDefinition Width="95"/>
                        <ColumnDefinition Width="65"/>
                    </Grid.ColumnDefinitions>
                    <TextBlock Text="Perfil da Calha:" VerticalAlignment="Center"/>
                    <ComboBox x:Name="cb_calha_perfil" Grid.Column="1" Height="24"/>
                    <TextBlock Grid.Column="3" Text="Chapa (mm):" VerticalAlignment="Center"/>
                    <TextBox x:Name="tb_chapa_mm" Grid.Column="4" Height="24" VerticalContentAlignment="Center" Text="1.5"/>

                    <Grid Grid.Row="2" Grid.ColumnSpan="5">
                        <Grid.ColumnDefinitions>
                            <ColumnDefinition Width="*"/>
                            <ColumnDefinition Width="8"/>
                            <ColumnDefinition Width="*"/>
                            <ColumnDefinition Width="8"/>
                            <ColumnDefinition Width="*"/>
                            <ColumnDefinition Width="8"/>
                            <ColumnDefinition Width="*"/>
                        </Grid.ColumnDefinitions>
                        <StackPanel Grid.Column="0">
                            <TextBlock Text="Largura (cm)" FontSize="11" Foreground="#555"/>
                            <TextBox x:Name="tb_calha_w" Height="24" VerticalContentAlignment="Center" Text="15"/>
                        </StackPanel>
                        <StackPanel Grid.Column="2">
                            <TextBlock Text="Altura (cm)" FontSize="11" Foreground="#555"/>
                            <TextBox x:Name="tb_calha_h" Height="24" VerticalContentAlignment="Center" Text="10"/>
                        </StackPanel>
                        <StackPanel Grid.Column="4">
                            <TextBlock Text="Desloc. H (cm)" FontSize="11" Foreground="#555"/>
                            <TextBox x:Name="tb_calha_dx" Height="24" VerticalContentAlignment="Center" Text="-3"/>
                        </StackPanel>
                        <StackPanel Grid.Column="6">
                            <TextBlock Text="Desloc. Z (cm)" FontSize="11" Foreground="#555"/>
                            <TextBox x:Name="tb_calha_dz" Height="24" VerticalContentAlignment="Center" Text="-2"/>
                        </StackPanel>
                    </Grid>
                </Grid>
            </StackPanel>
        </Border>

        <Border Background="White" BorderBrush="#D0D7DE" BorderThickness="1" CornerRadius="4" Padding="10" Margin="0,0,0,8">
            <StackPanel>
                <CheckBox x:Name="ck_tabeira" Content="Gerar Tabeiras / Testeiras" FontWeight="SemiBold" IsChecked="True" Margin="0,0,0,6"/>
                <Grid x:Name="pn_tabeira">
                    <Grid.RowDefinitions>
                        <RowDefinition Height="Auto"/>
                        <RowDefinition Height="6"/>
                        <RowDefinition Height="Auto"/>
                    </Grid.RowDefinitions>
                    <Grid.ColumnDefinitions>
                        <ColumnDefinition Width="*"/>
                        <ColumnDefinition Width="10"/>
                        <ColumnDefinition Width="*"/>
                    </Grid.ColumnDefinitions>
                    <StackPanel Grid.Column="0">
                        <TextBlock Text="Aplicar em:" FontSize="11" Foreground="#555"/>
                        <ComboBox x:Name="cb_tab_bordas" Height="24"/>
                    </StackPanel>
                    <StackPanel Grid.Column="2">
                        <TextBlock Text="Material da Tabeira:" FontSize="11" Foreground="#555"/>
                        <ComboBox x:Name="cb_tab_mat" Height="24"/>
                    </StackPanel>
                    <Grid Grid.Row="2" Grid.ColumnSpan="3">
                        <Grid.ColumnDefinitions>
                            <ColumnDefinition Width="*"/>
                            <ColumnDefinition Width="8"/>
                            <ColumnDefinition Width="*"/>
                            <ColumnDefinition Width="8"/>
                            <ColumnDefinition Width="*"/>
                        </Grid.ColumnDefinitions>
                        <StackPanel Grid.Column="0">
                            <TextBlock Text="Altura Tabeira (cm)" FontSize="11" Foreground="#555"/>
                            <TextBox x:Name="tb_tab_h" Height="24" VerticalContentAlignment="Center" Text="20"/>
                        </StackPanel>
                        <StackPanel Grid.Column="2">
                            <TextBlock Text="Espessura (cm)" FontSize="11" Foreground="#555"/>
                            <TextBox x:Name="tb_tab_esp" Height="24" VerticalContentAlignment="Center" Text="2.5"/>
                        </StackPanel>
                        <StackPanel Grid.Column="4">
                            <TextBlock Text="Desloc. Z (cm)" FontSize="11" Foreground="#555"/>
                            <TextBox x:Name="tb_tab_dz" Height="24" VerticalContentAlignment="Center" Text="0"/>
                        </StackPanel>
                    </Grid>
                </Grid>
            </StackPanel>
        </Border>

        <Border Background="White" BorderBrush="#D0D7DE" BorderThickness="1" CornerRadius="4" Padding="10" Margin="0,0,0,8">
            <StackPanel>
                <TextBlock Text="Rufos (Platibandas e Encontros com Parede)" FontWeight="SemiBold" Margin="0,0,0,6"/>
                <CheckBox x:Name="ck_rufo_capa" Content="Rufo Capa / Pingadeira sobre Platibandas" IsChecked="True" Margin="0,0,0,4"/>
                <Grid x:Name="pn_rufo_capa" Margin="18,0,0,6">
                    <Grid.RowDefinitions>
                        <RowDefinition Height="Auto"/>
                        <RowDefinition Height="6"/>
                        <RowDefinition Height="Auto"/>
                    </Grid.RowDefinitions>
                    <ComboBox x:Name="cb_plat_modo" Height="24"/>
                    <Grid Grid.Row="2">
                        <Grid.ColumnDefinitions>
                            <ColumnDefinition Width="*"/>
                            <ColumnDefinition Width="8"/>
                            <ColumnDefinition Width="*"/>
                            <ColumnDefinition Width="8"/>
                            <ColumnDefinition Width="*"/>
                        </Grid.ColumnDefinitions>
                        <StackPanel Grid.Column="0">
                            <TextBlock Text="Pingadeira lat. (cm)" FontSize="11" Foreground="#555"/>
                            <TextBox x:Name="tb_capa_ping" Height="24" VerticalContentAlignment="Center" Text="3"/>
                        </StackPanel>
                        <StackPanel Grid.Column="2">
                            <TextBlock Text="Aba lateral (cm)" FontSize="11" Foreground="#555"/>
                            <TextBox x:Name="tb_capa_aba" Height="24" VerticalContentAlignment="Center" Text="5"/>
                        </StackPanel>
                        <StackPanel Grid.Column="4">
                            <TextBlock Text="Crista central (cm)" FontSize="11" Foreground="#555"/>
                            <TextBox x:Name="tb_capa_crista" Height="24" VerticalContentAlignment="Center" Text="2"/>
                        </StackPanel>
                    </Grid>
                </Grid>

                <CheckBox x:Name="ck_rufo_encosto" Content="Rufo L de Encosto (Encontro Telhado x Parede / Platibanda)" IsChecked="True" Margin="0,2,0,4"/>
                <StackPanel x:Name="pn_rufo_encosto" Margin="18,0,0,0">
                    <CheckBox x:Name="ck_rufo_oitao" Content="Aplicar Rufo L também nos Oitões / Empenas livres" IsChecked="False" Margin="0,0,0,6"/>
                    <Grid>
                        <Grid.ColumnDefinitions>
                            <ColumnDefinition Width="*"/>
                            <ColumnDefinition Width="8"/>
                            <ColumnDefinition Width="*"/>
                            <ColumnDefinition Width="8"/>
                            <ColumnDefinition Width="*"/>
                        </Grid.ColumnDefinitions>
                        <StackPanel Grid.Column="0">
                            <TextBlock Text="Aba Parede V (cm)" FontSize="11" Foreground="#555"/>
                            <TextBox x:Name="tb_enc_v" Height="24" VerticalContentAlignment="Center" Text="15"/>
                        </StackPanel>
                        <StackPanel Grid.Column="2">
                            <TextBlock Text="Aba Telhado H (cm)" FontSize="11" Foreground="#555"/>
                            <TextBox x:Name="tb_enc_h" Height="24" VerticalContentAlignment="Center" Text="12"/>
                        </StackPanel>
                        <StackPanel Grid.Column="4">
                            <TextBlock Text="Dobra topo (cm)" FontSize="11" Foreground="#555"/>
                            <TextBox x:Name="tb_enc_dobra" Height="24" VerticalContentAlignment="Center" Text="1.5"/>
                        </StackPanel>
                    </Grid>
                </StackPanel>
            </StackPanel>
        </Border>

        <Border Background="White" BorderBrush="#D0D7DE" BorderThickness="1" CornerRadius="4" Padding="10" Margin="0,0,0,8">
            <Grid>
                <Grid.ColumnDefinitions>
                    <ColumnDefinition Width="*"/>
                    <ColumnDefinition Width="8"/>
                    <ColumnDefinition Width="*"/>
                    <ColumnDefinition Width="8"/>
                    <ColumnDefinition Width="*"/>
                </Grid.ColumnDefinitions>
                <StackPanel Grid.Column="0">
                    <TextBlock Text="Material Calha / Rufo" FontSize="11" Foreground="#555"/>
                    <ComboBox x:Name="cb_mat_metal" Height="24"/>
                </StackPanel>
                <StackPanel Grid.Column="2">
                    <TextBlock Text="Categoria Revit" FontSize="11" Foreground="#555"/>
                    <ComboBox x:Name="cb_cat_revit" Height="24"/>
                </StackPanel>
                <StackPanel Grid.Column="4">
                    <TextBlock Text="Agrupamento" FontSize="11" Foreground="#555"/>
                    <ComboBox x:Name="cb_grp" Height="24"/>
                </StackPanel>
            </Grid>
        </Border>

        <StackPanel Orientation="Horizontal" HorizontalAlignment="Right" Margin="0,6,0,0">
            <Button x:Name="btn_cancel" Content="Cancelar" Width="90" Height="30" Margin="0,0,8,0"/>
            <Button x:Name="btn_ok" Content="Gerar Calhas, Rufos e Tabeiras" Width="210" Height="30"
                    IsDefault="True" Background="#0078D4" Foreground="White" FontWeight="SemiBold"/>
        </StackPanel>
    </StackPanel>
</Window>
"""


def _parse_float(txt, default_val):
    try:
        return float((txt or u"").replace(",", ".").strip())
    except Exception:
        return float(default_val)


if HAS_REVIT:
    from System.Windows.Markup import XamlReader

    class _WallSelectionFilter(ISelectionFilter):
        def AllowElement(self, elem):
            return isinstance(elem, Wall)

        def AllowReference(self, ref, pt):
            return True

    class CalhasRufosWindow(object):
        def __init__(self, prefs):
            self.window = XamlReader.Parse(XAML_CALHAS)
            self.confirmado = False
            self.resultado = None
            w = self.window

            self._cb_escopo = w.FindName("cb_escopo")
            self._ck_calha = w.FindName("ck_calha")
            self._pn_calha = w.FindName("pn_calha")
            self._cb_calha_perfil = w.FindName("cb_calha_perfil")
            self._tb_chapa_mm = w.FindName("tb_chapa_mm")
            self._tb_calha_w = w.FindName("tb_calha_w")
            self._tb_calha_h = w.FindName("tb_calha_h")
            self._tb_calha_dx = w.FindName("tb_calha_dx")
            self._tb_calha_dz = w.FindName("tb_calha_dz")

            self._ck_tabeira = w.FindName("ck_tabeira")
            self._pn_tabeira = w.FindName("pn_tabeira")
            self._cb_tab_bordas = w.FindName("cb_tab_bordas")
            self._cb_tab_mat = w.FindName("cb_tab_mat")
            self._tb_tab_h = w.FindName("tb_tab_h")
            self._tb_tab_esp = w.FindName("tb_tab_esp")
            self._tb_tab_dz = w.FindName("tb_tab_dz")

            self._ck_rufo_capa = w.FindName("ck_rufo_capa")
            self._pn_rufo_capa = w.FindName("pn_rufo_capa")
            self._cb_plat_modo = w.FindName("cb_plat_modo")
            self._tb_capa_ping = w.FindName("tb_capa_ping")
            self._tb_capa_aba = w.FindName("tb_capa_aba")
            self._tb_capa_crista = w.FindName("tb_capa_crista")

            self._ck_rufo_encosto = w.FindName("ck_rufo_encosto")
            self._pn_rufo_encosto = w.FindName("pn_rufo_encosto")
            self._ck_rufo_oitao = w.FindName("ck_rufo_oitao")
            self._tb_enc_v = w.FindName("tb_enc_v")
            self._tb_enc_h = w.FindName("tb_enc_h")
            self._tb_enc_dobra = w.FindName("tb_enc_dobra")

            self._cb_mat_metal = w.FindName("cb_mat_metal")
            self._cb_cat_revit = w.FindName("cb_cat_revit")
            self._cb_grp = w.FindName("cb_grp")

            for item in (u"Telhados selecionados (ou selecionar agora)", u"Todos os telhados na vista ativa"):
                self._cb_escopo.Items.Add(item)
            self._cb_escopo.SelectedIndex = int(prefs.get("cr_escopo", 0))

            for item in (u"Quadrada / Retangular (U)", u"Semicircular (Meia-Cana)", u"Colonial com Pingadeira"):
                self._cb_calha_perfil.Items.Add(item)
            self._cb_calha_perfil.SelectedIndex = int(prefs.get("cr_calha_perfil", 0))

            for item in (u"Beirais e Oitões (Empenas)", u"Apenas Beirais (horizontais)", u"Apenas Oitões (inclinados)"):
                self._cb_tab_bordas.Items.Add(item)
            self._cb_tab_bordas.SelectedIndex = int(prefs.get("cr_tab_bordas", 0))

            for item in (u"Madeira (Cedrinho / Telhado)", u"Metálica (Mesmo material dos Rufos)"):
                self._cb_tab_mat.Items.Add(item)
            self._cb_tab_mat.SelectedIndex = int(prefs.get("cr_tab_mat", 0))

            for item in (u"Auto-detectar platibandas junto ao telhado", u"Selecionar paredes de platibanda manualmente"):
                self._cb_plat_modo.Items.Add(item)
            self._cb_plat_modo.SelectedIndex = int(prefs.get("cr_plat_modo", 0))

            for item in (u"Aço Galvanizado", u"Chapa Pintada Branco", u"Chapa Pintada Grafite", u"Alumínio Natural"):
                self._cb_mat_metal.Items.Add(item)
            self._cb_mat_metal.SelectedIndex = int(prefs.get("cr_mat_metal", 0))

            for item in (u"Telhados (OST_Roofs)", u"Modelos Genéricos (OST_GenericModel)"):
                self._cb_cat_revit.Items.Add(item)
            self._cb_cat_revit.SelectedIndex = int(prefs.get("cr_cat_revit", 0))

            for item in (u"Um grupo por tipo", u"Grupo único completo", u"Não agrupar"):
                self._cb_grp.Items.Add(item)
            self._cb_grp.SelectedIndex = int(prefs.get("cr_grp", 0))

            self._ck_calha.IsChecked = bool(prefs.get("cr_calha", True))
            self._ck_tabeira.IsChecked = bool(prefs.get("cr_tabeira", True))
            self._ck_rufo_capa.IsChecked = bool(prefs.get("cr_rufo_capa", True))
            self._ck_rufo_encosto.IsChecked = bool(prefs.get("cr_rufo_encosto", True))
            self._ck_rufo_oitao.IsChecked = bool(prefs.get("cr_rufo_oitao", False))

            self._tb_chapa_mm.Text = unicode(prefs.get("cr_chapa_mm", "1.5"))
            self._tb_calha_w.Text = unicode(prefs.get("cr_calha_w", "15"))
            self._tb_calha_h.Text = unicode(prefs.get("cr_calha_h", "10"))
            self._tb_calha_dx.Text = unicode(prefs.get("cr_calha_dx", "-3"))
            self._tb_calha_dz.Text = unicode(prefs.get("cr_calha_dz", "-2"))

            self._tb_tab_h.Text = unicode(prefs.get("cr_tab_h", "20"))
            self._tb_tab_esp.Text = unicode(prefs.get("cr_tab_esp", "2.5"))
            self._tb_tab_dz.Text = unicode(prefs.get("cr_tab_dz", "0"))

            self._tb_capa_ping.Text = unicode(prefs.get("cr_capa_ping", "3"))
            self._tb_capa_aba.Text = unicode(prefs.get("cr_capa_aba", "5"))
            self._tb_capa_crista.Text = unicode(prefs.get("cr_capa_crista", "2"))

            self._tb_enc_v.Text = unicode(prefs.get("cr_enc_v", "15"))
            self._tb_enc_h.Text = unicode(prefs.get("cr_enc_h", "12"))
            self._tb_enc_dobra.Text = unicode(prefs.get("cr_enc_dobra", "1.5"))

            self._ck_calha.Click += self._atualizar_vis
            self._ck_tabeira.Click += self._atualizar_vis
            self._ck_rufo_capa.Click += self._atualizar_vis
            self._ck_rufo_encosto.Click += self._atualizar_vis
            w.FindName("btn_ok").Click += self._ao_ok
            w.FindName("btn_cancel").Click += self._ao_cancelar
            self._atualizar_vis(None, None)

        def _atualizar_vis(self, s, a):
            self._pn_calha.Visibility = WpfVisibility.Visible if self._ck_calha.IsChecked else WpfVisibility.Collapsed
            self._pn_tabeira.Visibility = WpfVisibility.Visible if self._ck_tabeira.IsChecked else WpfVisibility.Collapsed
            self._pn_rufo_capa.Visibility = WpfVisibility.Visible if self._ck_rufo_capa.IsChecked else WpfVisibility.Collapsed
            self._pn_rufo_encosto.Visibility = WpfVisibility.Visible if self._ck_rufo_encosto.IsChecked else WpfVisibility.Collapsed

        def _ao_cancelar(self, s, a):
            self.window.Close()

        def _ao_ok(self, s, a):
            if not (self._ck_calha.IsChecked or self._ck_tabeira.IsChecked or
                    self._ck_rufo_capa.IsChecked or self._ck_rufo_encosto.IsChecked):
                forms.alert(u"Marque pelo menos um elemento (Calha, Tabeira ou Rufo).")
                return

            self.resultado = {
                "cr_escopo": max(0, int(self._cb_escopo.SelectedIndex)),
                "cr_calha": bool(self._ck_calha.IsChecked),
                "cr_calha_perfil": max(0, int(self._cb_calha_perfil.SelectedIndex)),
                "cr_chapa_mm": max(0.5, _parse_float(self._tb_chapa_mm.Text, 1.5)),
                "cr_calha_w": max(4.0, _parse_float(self._tb_calha_w.Text, 15.0)),
                "cr_calha_h": max(3.0, _parse_float(self._tb_calha_h.Text, 10.0)),
                "cr_calha_dx": _parse_float(self._tb_calha_dx.Text, -3.0),
                "cr_calha_dz": _parse_float(self._tb_calha_dz.Text, -2.0),
                "cr_tabeira": bool(self._ck_tabeira.IsChecked),
                "cr_tab_bordas": max(0, int(self._cb_tab_bordas.SelectedIndex)),
                "cr_tab_mat": max(0, int(self._cb_tab_mat.SelectedIndex)),
                "cr_tab_h": max(5.0, _parse_float(self._tb_tab_h.Text, 20.0)),
                "cr_tab_esp": max(0.8, _parse_float(self._tb_tab_esp.Text, 2.5)),
                "cr_tab_dz": _parse_float(self._tb_tab_dz.Text, 0.0),
                "cr_rufo_capa": bool(self._ck_rufo_capa.IsChecked),
                "cr_plat_modo": max(0, int(self._cb_plat_modo.SelectedIndex)),
                "cr_capa_ping": max(0.5, _parse_float(self._tb_capa_ping.Text, 3.0)),
                "cr_capa_aba": max(1.5, _parse_float(self._tb_capa_aba.Text, 5.0)),
                "cr_capa_crista": max(0.2, _parse_float(self._tb_capa_crista.Text, 2.0)),
                "cr_rufo_encosto": bool(self._ck_rufo_encosto.IsChecked),
                "cr_rufo_oitao": bool(self._ck_rufo_oitao.IsChecked),
                "cr_enc_v": max(3.0, _parse_float(self._tb_enc_v.Text, 15.0)),
                "cr_enc_h": max(3.0, _parse_float(self._tb_enc_h.Text, 12.0)),
                "cr_enc_dobra": max(0.0, _parse_float(self._tb_enc_dobra.Text, 1.5)),
                "cr_mat_metal": max(0, int(self._cb_mat_metal.SelectedIndex)),
                "cr_cat_revit": max(0, int(self._cb_cat_revit.SelectedIndex)),
                "cr_grp": max(0, int(self._cb_grp.SelectedIndex)),
            }
            self.confirmado = True
            self.window.Close()

        def ShowDialog(self):
            return self.window.ShowDialog()


def _obter_ou_criar_material_cor(nome_mat, rgb, fallback_keywords):
    for m in FilteredElementCollector(doc).OfClass(Material).ToElements():
        try:
            if m.Name == nome_mat:
                return m.Id
        except Exception:
            pass
    fallback_id = ElementId.InvalidElementId
    for m in FilteredElementCollector(doc).OfClass(Material).ToElements():
        try:
            nl = m.Name.lower()
            for kw in fallback_keywords:
                if kw in nl:
                    fallback_id = m.Id
                    break
            if fallback_id != ElementId.InvalidElementId:
                break
        except Exception:
            pass
    try:
        nid = Material.Create(doc, nome_mat)
        mat = doc.GetElement(nid)
        mat.Color = Color(rgb[0], rgb[1], rgb[2])
        return nid
    except Exception:
        return fallback_id


def _obter_material_metalico(idx):
    specs = {
        0: (u"Aço Galvanizado - Calhas/Rufos (AutoBIM)", (182, 188, 196), ("galvan", "aço", "aco", "steel")),
        1: (u"Chapa Pintada Branco - Calhas/Rufos (AutoBIM)", (240, 240, 240), ("branco", "white", "pintura")),
        2: (u"Chapa Pintada Grafite - Calhas/Rufos (AutoBIM)", (68, 74, 82), ("grafite", "preto", "dark")),
        3: (u"Alumínio Natural - Calhas/Rufos (AutoBIM)", (206, 212, 218), ("alumin", "aluminum", "metal")),
    }
    nome, rgb, kws = specs.get(idx, specs[0])
    return _obter_ou_criar_material_cor(nome, rgb, kws)


def _obter_material_tabeira(tab_mat_idx, metal_id):
    if tab_mat_idx == 1:
        return metal_id
    return _obter_ou_criar_material_cor(
        u"Madeira - Tabeira/Testeira (AutoBIM)",
        (168, 114, 62),
        ("madeira", "wood", "cedro", "pinus"),
    )


def _extrair_solidos_elemento(elem):
    sols = []
    if elem is None:
        return sols
    try:
        opt = Options()
        opt.DetailLevel = ViewDetailLevel.Fine if hasattr(ViewDetailLevel, "Fine") else opt.DetailLevel
        geom = elem.get_Geometry(opt)
    except Exception:
        try:
            geom = elem.get_Geometry(Options())
        except Exception:
            geom = None
    if geom is None:
        return sols

    def _walk(ge):
        for g in ge:
            if isinstance(g, Solid) and g.Volume > 1e-6:
                sols.append(g)
            elif hasattr(g, "GetInstanceGeometry"):
                try:
                    _walk(g.GetInstanceGeometry())
                except Exception:
                    pass

    _walk(geom)
    return sols


def _extrair_bordas_telhado(roof):
    """
    Extrai todas as bordas do contorno superior de cada agua do telhado,
    classificadas em ('beiral', 'oitao', 'topo_horizontal', 'interior'),
    junto com vetor normal horizontal externo ('out_xy').
    """
    faces_top = []
    try:
        for rf_ref in HostObjectUtils.GetTopFaces(roof):
            face = roof.GetGeometryObjectFromReference(rf_ref)
            if isinstance(face, PlanarFace):
                faces_top.append(face)
    except Exception:
        pass

    if not faces_top:
        for sol in _extrair_solidos_elemento(roof):
            for f in sol.Faces:
                if isinstance(f, PlanarFace) and f.FaceNormal.Z > 0.15:
                    faces_top.append(f)

    # Primeiro extrai todos os segmentos de todas as faces superiores
    face_segs = []
    for fidx, face in enumerate(faces_top):
        n = face.FaceNormal
        if n.Z < 0.05:
            continue
        # Eixo u/v na face para testar interioridade ou orientacao
        for loop in face.GetEdgesAsCurveLoops():
            pts = []
            for crv in loop:
                tess = crv.Tessellate()
                if tess and len(tess) >= 2:
                    if not pts:
                        pts.append(tess[0])
                    for k in range(1, len(tess)):
                        if tess[k].DistanceTo(pts[-1]) > 0.01 * FT:
                            pts.append(tess[k])
            if len(pts) >= 2 and pts[-1].DistanceTo(pts[0]) < 0.01 * FT:
                pts.pop()
            if len(pts) < 3:
                continue

            # Calcula area assinada projetada no plano da face para garantir orientacao CCW em relacao a n
            orig = pts[0]
            aux_u = (pts[1] - orig)
            if aux_u.GetLength() < 1e-6:
                continue
            aux_u = aux_u.Normalize()
            aux_v = n.CrossProduct(aux_u).Normalize()
            area2 = 0.0
            m = len(pts)
            for i in range(m):
                a = pts[i] - orig
                b = pts[(i + 1) % m] - orig
                ua, va = a.DotProduct(aux_u), a.DotProduct(aux_v)
                ub, vb = b.DotProduct(aux_u), b.DotProduct(aux_v)
                area2 += (ua * vb - ub * va)
            if area2 < 0:
                pts.reverse()

            for i in range(m):
                p1 = pts[i]
                p2 = pts[(i + 1) % m]
                seg = p2 - p1
                if seg.GetLength() < 0.05 * FT:
                    continue
                seg_u = seg.Normalize()
                # Em loop CCW relativo a n_up, o exterior no plano da face eh seg_u x n_up
                out_face = seg_u.CrossProduct(n).Normalize()
                out_xy = XYZ(out_face.X, out_face.Y, 0.0)
                if out_xy.GetLength() < 1e-4:
                    out_xy = seg_u.CrossProduct(XYZ.BasisZ)
                out_xy = out_xy.Normalize()
                mid = (p1 + p2) * 0.5
                face_segs.append({
                    "fidx": fidx,
                    "p1": (p1.X, p1.Y, p1.Z),
                    "p2": (p2.X, p2.Y, p2.Z),
                    "mid": (mid.X, mid.Y, mid.Z),
                    "out_face": (out_face.X, out_face.Y, out_face.Z),
                    "out_xy": (out_xy.X, out_xy.Y, 0.0),
                    "face_normal": (n.X, n.Y, n.Z),
                    "roof": roof,
                })

    # Detecta bordas internas compartilhadas entre duas faces do mesmo telhado
    bordas = []
    for i, s in enumerate(face_segs):
        shared = False
        for j, o in enumerate(face_segs):
            if i == j or o["fidx"] == s["fidx"]:
                continue
            # Segmentos opostos coincidem (midpoint proximo e vertices proximos)
            if _v_dist(s["mid"], o["mid"]) < 0.08 * FT:
                d1 = (_v_dist(s["p1"], o["p1"]) + _v_dist(s["p2"], o["p2"]))
                d2 = (_v_dist(s["p1"], o["p2"]) + _v_dist(s["p2"], o["p1"]))
                if min(d1, d2) < 0.15 * FT:
                    shared = True
                    break
        kind = classificar_borda_telhado(s["p1"], s["p2"], s["out_face"], shared_interior=shared, tol_z=0.025 * FT)
        s["kind"] = kind
        if kind != "interior":
            bordas.append(s)

    return bordas


def _coletar_paredes_proximas_telhados(roofs):
    """Encontra Walls do projeto que ficam ao redor/dentro da caixa XY expandida dos telhados."""
    boxes = []
    for r in roofs:
        bb = r.get_BoundingBox(None)
        if bb is not None:
            boxes.append((bb.Min.X - 1.5 * FT, bb.Max.X + 1.5 * FT, bb.Min.Y - 1.5 * FT, bb.Max.Y + 1.5 * FT, bb.Min.Z - 1.0 * FT))

    walls_info = []
    for w in FilteredElementCollector(doc).OfClass(Wall).WhereElementIsNotElementType().ToElements():
        try:
            loc = getattr(w, "Location", None)
            if not isinstance(loc, LocationCurve) or not isinstance(loc.Curve, Line):
                continue
            bb = w.get_BoundingBox(None)
            if bb is None:
                continue
            p0 = loc.Curve.GetEndPoint(0)
            p1 = loc.Curve.GetEndPoint(1)
            mid_x = 0.5 * (p0.X + p1.X)
            mid_y = 0.5 * (p0.Y + p1.Y)
            in_box = False
            for (x0, x1, y0, y1, zmin) in boxes:
                if (x0 <= max(p0.X, p1.X) and x1 >= min(p0.X, p1.X) and
                        y0 <= max(p0.Y, p1.Y) and y1 >= min(p0.Y, p1.Y) and
                        bb.Max.Z >= zmin):
                    in_box = True
                    break
            if in_box:
                walls_info.append({
                    "wall": w,
                    "p1": (p0.X, p0.Y, bb.Max.Z),
                    "p2": (p1.X, p1.Y, bb.Max.Z),
                    "top_z": bb.Max.Z,
                    "min_z": bb.Min.Z,
                    "width": w.Width,
                })
        except Exception:
            pass
    return walls_info


def _detectar_platibandas_acima_telhados(roofs, walls_info):
    """Filtra paredes proximas cujo topo ultrapassa a borda adjacente do telhado (platibandas)."""
    plat_segs = []
    roof_bbs = []
    for r in roofs:
        bb = r.get_BoundingBox(None)
        if bb is not None:
            roof_bbs.append((bb.Min.Z, bb.Max.Z, bb.Min.X, bb.Max.X, bb.Min.Y, bb.Max.Y))

    for wi in walls_info:
        p1 = wi["p1"]
        p2 = wi["p2"]
        # Verifica se a parede sobe acima de algum ponto do telhado (cota Z topo > Z min do telhado + 5cm)
        subiu = False
        for zmin, zmax, x0, x1, y0, y1 in roof_bbs:
            if (x0 - 1.2 * FT <= max(p1[0], p2[0]) and x1 + 1.2 * FT >= min(p1[0], p2[0]) and
                    y0 - 1.2 * FT <= max(p1[1], p2[1]) and y1 + 1.2 * FT >= min(p1[1], p2[1])):
                if wi["top_z"] > zmin + 0.08 * FT and wi["top_z"] < zmax + 6.0 * FT:
                    subiu = True
                    break
        if subiu:
            plat_segs.append(wi)
    return plat_segs


def _borda_encosta_parede(borda, walls_info):
    """Retorna info da parede se o ponto medio da borda encosta em uma parede mais alta que ela."""
    mx, my, mz = borda["mid"]
    best = None
    best_dist = 999.0
    for wi in walls_info:
        if wi["top_z"] <= mz + 0.06 * FT or wi["min_z"] >= mz + 1.0 * FT:
            continue
        ax, ay = wi["p1"][0], wi["p1"][1]
        bx, by = wi["p2"][0], wi["p2"][1]
        vx, vy = bx - ax, by - ay
        l2 = vx * vx + vy * vy
        if l2 < 1e-8:
            continue
        t = ((mx - ax) * vx + (my - ay) * vy) / l2
        if t < -0.15 or t > 1.15:
            continue
        px = ax + t * vx
        py = ay + t * vy
        dist = math.hypot(mx - px, my - py)
        tol_enc = wi["width"] * 0.5 + 0.28 * FT
        if dist <= tol_enc and dist < best_dist:
            best_dist = dist
            best = wi
    return best


def _solid_sweep_ou_extrude(chain_segs, is_closed, pts_2d, offset_u_ft, offset_v_ft,
                            cat_id, mat_id, comentario, add_end_caps=False):
    """
    Cria Solid(s) 3D via CreateSweptGeometry em cadeias planares continuas (esquadrias perfeitas)
    com fallback automatico por segmento via CreateExtrusionGeometry.
    """
    criados = []
    if not chain_segs:
        return criados

    solid_opt = SolidOptions(
        mat_id if mat_id else ElementId.InvalidElementId,
        ElementId.InvalidElementId,
    )

    # Verifica se cadeia eh horizontal/planar suficiente para CreateSweptGeometry limpo
    z0 = chain_segs[0]["p1"][2]
    is_horiz = all(abs(s["p1"][2] - z0) < 0.01 * FT and abs(s["p2"][2] - z0) < 0.01 * FT for s in chain_segs)

    swept_solids = []
    if is_horiz and len(chain_segs) >= 1:
        try:
            path_loop = CurveLoop()
            for s in chain_segs:
                a = XYZ(s["p1"][0], s["p1"][1], s["p1"][2])
                b = XYZ(s["p2"][0], s["p2"][1], s["p2"][2])
                if a.DistanceTo(b) > 0.01 * FT:
                    path_loop.Append(Line.CreateBound(a, b))

            s0 = chain_segs[0]
            p_start = XYZ(s0["p1"][0], s0["p1"][1], s0["p1"][2])
            p_end = XYZ(s0["p2"][0], s0["p2"][1], s0["p2"][2])
            tangent = (p_end - p_start).Normalize()
            up_vec = XYZ.BasisZ
            # Garante alinhamento com out_xy fornecido no segmento
            out_pref = XYZ(s0["out_xy"][0], s0["out_xy"][1], 0.0)
            if out_pref.GetLength() < 1e-4:
                out_pref = tangent.CrossProduct(up_vec)
            out_vec = out_pref - tangent * out_pref.DotProduct(tangent)
            if out_vec.GetLength() < 1e-4:
                out_vec = tangent.CrossProduct(up_vec)
            out_vec = out_vec.Normalize()

            pts_3d = [
                p_start + out_vec * (u + offset_u_ft) + up_vec * (v + offset_v_ft)
                for (u, v) in pts_2d
            ]
            # Ajusta sentido CCW em relacao a tangent para CreateSweptGeometry
            v1 = pts_3d[1] - pts_3d[0]
            v2 = pts_3d[2] - pts_3d[1]
            if v1.CrossProduct(v2).DotProduct(tangent) < 0:
                pts_3d.reverse()

            prof_curves = List[Curve]()
            np = len(pts_3d)
            for k in range(np):
                prof_curves.Add(Line.CreateBound(pts_3d[k], pts_3d[(k + 1) % np]))
            prof_loop = CurveLoop.Create(prof_curves)
            prof_loops = List[CurveLoop]()
            prof_loops.Add(prof_loop)

            sol_sw = GeometryCreationUtilities.CreateSweptGeometry(path_loop, 0, 0.0, prof_loops, solid_opt)
            if sol_sw is not None and sol_sw.Volume > 1e-7:
                swept_solids.append(sol_sw)
        except Exception:
            swept_solids = []

    if swept_solids:
        total_len_ft = sum(_v_dist(s["p1"], s["p2"]) for s in chain_segs)
        # Tampas nas pontas de calhas abertas (fechamento estanque)
        if add_end_caps and not is_closed:
            t_cap = 0.004 * FT
            us = [u for (u, _) in pts_2d]
            vs = [v for (_, v) in pts_2d]
            u_min, u_max = min(us) + offset_u_ft, max(us) + offset_u_ft
            v_min, v_max = min(vs) + offset_v_ft, max(vs) + offset_v_ft
            for end_seg, end_pt, sign in (
                (chain_segs[0], chain_segs[0]["p1"], -1.0),
                (chain_segs[-1], chain_segs[-1]["p2"], 1.0),
            ):
                try:
                    pe = XYZ(end_pt[0], end_pt[1], end_pt[2])
                    seg_d = (XYZ(end_seg["p2"][0], end_seg["p2"][1], end_seg["p2"][2]) -
                             XYZ(end_seg["p1"][0], end_seg["p1"][1], end_seg["p1"][2])).Normalize()
                    ext_dir = seg_d * sign
                    out_e = XYZ(end_seg["out_xy"][0], end_seg["out_xy"][1], 0.0).Normalize()
                    up_e = XYZ.BasisZ
                    cap_pts = [
                        pe + out_e * u_min + up_e * v_min,
                        pe + out_e * u_max + up_e * v_min,
                        pe + out_e * u_max + up_e * v_max,
                        pe + out_e * u_min + up_e * v_max,
                    ]
                    if (cap_pts[1] - cap_pts[0]).CrossProduct(cap_pts[2] - cap_pts[1]).DotProduct(ext_dir) < 0:
                        cap_pts.reverse()
                    c_crvs = List[Curve]()
                    for k in range(4):
                        c_crvs.Add(Line.CreateBound(cap_pts[k], cap_pts[(k + 1) % 4]))
                    c_loops = List[CurveLoop]()
                    c_loops.Add(CurveLoop.Create(c_crvs))
                    sol_cap = GeometryCreationUtilities.CreateExtrusionGeometry(c_loops, ext_dir, t_cap, solid_opt)
                    if sol_cap is not None and sol_cap.Volume > 1e-8:
                        swept_solids.append(sol_cap)
                except Exception:
                    pass

        try:
            ds = DirectShape.CreateElement(doc, ElementId(cat_id))
            geom_list = List[GeometryObject]()
            for ss in swept_solids:
                geom_list.Add(ss)
            ds.SetShape(geom_list)
            p_c = ds.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
            if p_c and not p_c.IsReadOnly:
                p_c.Set(u"{} (C={:.2f}m)".format(comentario, total_len_ft * 0.3048))
            criados.append(ds)
            return criados
        except Exception:
            pass

    # Fallback: Extrusao segmento a segmento (com suporte a oitao inclinado em 3D)
    for s in chain_segs:
        try:
            p1 = XYZ(s["p1"][0], s["p1"][1], s["p1"][2])
            p2 = XYZ(s["p2"][0], s["p2"][1], s["p2"][2])
            vec = p2 - p1
            comp = vec.GetLength()
            if comp < 0.03 * FT:
                continue
            axis = vec.Normalize()
            out_raw = XYZ(s["out_xy"][0], s["out_xy"][1], 0.0)
            out_vec = out_raw - axis * out_raw.DotProduct(axis)
            if out_vec.GetLength() < 1e-4:
                out_vec = axis.CrossProduct(XYZ.BasisZ)
            out_vec = out_vec.Normalize()
            # Para elementos horizontais e verticais plumb, mantemos vertical global XYZ.BasisZ quando possivel
            up_vec = XYZ.BasisZ if abs(axis.Z) < 0.02 else axis.CrossProduct(out_vec).Normalize()
            if up_vec.Z < 0:
                up_vec = -up_vec
            out_vec = axis.CrossProduct(up_vec).Normalize()
            if out_vec.DotProduct(out_raw) < 0:
                out_vec = -out_vec

            # Em bordas horizontais conectadas, estende ligeiramente para encontro fechado de quina
            ext_ini = 0.0
            ext_fim = 0.0
            max_u = max([u for (u, _) in pts_2d]) + offset_u_ft
            if is_horiz and max_u > 0.02 * FT and len(chain_segs) > 1:
                ext_ini = min(0.15 * FT, max_u * 0.5)
                ext_fim = min(0.15 * FT, max_u * 0.5)

            p1_use = p1 - axis * ext_ini
            comp_use = comp + ext_ini + ext_fim

            pts_3d = [
                p1_use + out_vec * (u + offset_u_ft) + up_vec * (v + offset_v_ft)
                for (u, v) in pts_2d
            ]
            if (pts_3d[1] - pts_3d[0]).CrossProduct(pts_3d[2] - pts_3d[1]).DotProduct(axis) < 0:
                pts_3d.reverse()

            crvs = List[Curve]()
            np = len(pts_3d)
            for k in range(np):
                crvs.Add(Line.CreateBound(pts_3d[k], pts_3d[(k + 1) % np]))
            loops = List[CurveLoop]()
            loops.Add(CurveLoop.Create(crvs))
            sol = GeometryCreationUtilities.CreateExtrusionGeometry(loops, axis, comp_use, solid_opt)
            if sol is None or sol.Volume <= 1e-7:
                continue
            ds = DirectShape.CreateElement(doc, ElementId(cat_id))
            glist = List[GeometryObject]()
            glist.Add(sol)
            ds.SetShape(glist)
            p_c = ds.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
            if p_c and not p_c.IsReadOnly:
                p_c.Set(u"{} (C={:.2f}m)".format(comentario, comp_use * 0.3048))
            criados.append(ds)
        except Exception:
            pass

    return criados


def _selecionar_telhados_calhas(escopo_idx):
    if escopo_idx == 1:
        roofs = [
            el for el in FilteredElementCollector(doc, view.Id).OfClass(RoofBase).WhereElementIsNotElementType().ToElements()
            if isinstance(el, RoofBase)
        ]
        if not roofs:
            forms.alert(u"Nenhum telhado encontrado na vista ativa.", exitscript=True)
        return roofs

    if rf is not None:
        rf.doc, rf.uidoc, rf.view = doc, uidoc, view
        return rf.selecionar_telhados()

    roofs = [doc.GetElement(eid) for eid in uidoc.Selection.GetElementIds() if isinstance(doc.GetElement(eid), RoofBase)]
    if not roofs:
        forms.alert(u"Selecione ao menos um telhado.", exitscript=True)
    return roofs


def run_calhas_rufos_tabeiras():
    global doc, uidoc, view
    uidoc = __revit__.ActiveUIDocument
    doc = uidoc.Document
    view = doc.ActiveView

    prefs = carregar_preferencias()
    win = CalhasRufosWindow(prefs)
    win.ShowDialog()
    if not win.confirmado:
        script.exit()

    op = win.resultado
    salvar_preferencias(op)

    roofs = _selecionar_telhados_calhas(op["cr_escopo"])

    plat_walls = []
    if op["cr_rufo_capa"] and op["cr_plat_modo"] == 1:
        # Usuario escolhe paredes de platibanda manualmente (aproveita pre-selecao de Wall se houver)
        for eid in uidoc.Selection.GetElementIds():
            el = doc.GetElement(eid)
            if isinstance(el, Wall):
                plat_walls.append(el)
        if not plat_walls:
            try:
                with forms.WarningBar(title=u"Selecione as Paredes de Platibanda e clique em Finish (ou Esc para pular)"):
                    refs = uidoc.Selection.PickObjects(
                        ObjectType.Element,
                        _WallSelectionFilter(),
                        u"Selecione as Paredes de Platibanda"
                    )
                plat_walls = [doc.GetElement(r) for r in refs if isinstance(doc.GetElement(r), Wall)]
            except Exception:
                plat_walls = []

    cat_id = BuiltInCategory.OST_Roofs if op["cr_cat_revit"] == 0 else BuiltInCategory.OST_GenericModel
    t_chapa_ft = op["cr_chapa_mm"] * MM

    ids_calhas = []
    ids_tabeiras = []
    ids_rufos = []
    grupos_criados = 0

    with TransactionGroup(doc, "AutoBIM: Calhas, Rufos e Tabeiras") as tg:
        tg.Start()
        with Transaction(doc, "Modelar Calhas, Rufos e Tabeiras") as t:
            t.Start()
            mat_metal = _obter_material_metalico(op["cr_mat_metal"])
            mat_tab = _obter_material_tabeira(op["cr_tab_mat"], mat_metal)

            walls_near = _coletar_paredes_proximas_telhados(roofs)

            # 1. Processa bordas de cada telhado
            beirais_segs = []
            tab_segs = []
            encosto_segs = []

            for roof in roofs:
                bordas = _extrair_bordas_telhado(roof)
                for b in bordas:
                    parede_enc = _borda_encosta_parede(b, walls_near)
                    if b["kind"] == "beiral" and parede_enc is None:
                        beirais_segs.append(b)
                    # Tabeiras: apenas em bordas livres (onde nao ha parede encostada)
                    if op["cr_tabeira"] and parede_enc is None:
                        if op["cr_tab_bordas"] == 0 and b["kind"] in ("beiral", "oitao"):
                            tab_segs.append(b)
                        elif op["cr_tab_bordas"] == 1 and b["kind"] == "beiral":
                            tab_segs.append(b)
                        elif op["cr_tab_bordas"] == 2 and b["kind"] == "oitao":
                            tab_segs.append(b)
                    # Rufo Encosto L: em bordas que encostam em paredes mais altas (ou oitao livre se marcado)
                    if op["cr_rufo_encosto"]:
                        if parede_enc is not None or (op["cr_rufo_oitao"] and b["kind"] == "oitao"):
                            bs = dict(b)
                            # Se encosta em parede, out_xy do rufo deve apontar DA parede PARA o telhado (-b['out_xy'])
                            if parede_enc is not None:
                                ox, oy, _ = b["out_xy"]
                                bs["out_xy"] = (-ox, -oy, 0.0)
                                bs["p1"], bs["p2"] = b["p2"], b["p1"]
                            encosto_segs.append(bs)

            # 1A. Calhas nos Beirais
            if op["cr_calha"] and beirais_segs:
                w_ft = op["cr_calha_w"] / 30.48
                h_ft = op["cr_calha_h"] / 30.48
                dx_ft = op["cr_calha_dx"] / 30.48
                dz_ft = op["cr_calha_dz"] / 30.48
                pts_calha = perfil_2d_calha(op["cr_calha_perfil"], w_ft, h_ft, t_chapa_ft)
                perf_nome = (u"Quadrada", u"Meia-Cana", u"Colonial")[op["cr_calha_perfil"]]
                comm_calha = u"Calha {} {:.0f}x{:.0f}cm".format(perf_nome, op["cr_calha_w"], op["cr_calha_h"])
                for ch in cadeias_bordas_continuas(beirais_segs, tol=0.05 * FT):
                    created = _solid_sweep_ou_extrude(
                        ch["segs"], ch["is_closed"], pts_calha,
                        dx_ft, dz_ft, cat_id, mat_metal, comm_calha, add_end_caps=True
                    )
                    ids_calhas.extend([el.Id for el in created])

            # 1B. Tabeiras / Testeiras
            if op["cr_tabeira"] and tab_segs:
                h_tab_ft = op["cr_tab_h"] / 30.48
                esp_tab_ft = op["cr_tab_esp"] / 30.48
                dz_tab_ft = op["cr_tab_dz"] / 30.48
                pts_tab = perfil_2d_tabeira(h_tab_ft, esp_tab_ft)
                comm_tab = u"Tabeira {:.0f}x{:.1f}cm".format(op["cr_tab_h"], op["cr_tab_esp"])
                tab_beiral = [s for s in tab_segs if s["kind"] == "beiral"]
                tab_oitao = [s for s in tab_segs if s["kind"] == "oitao"]
                for ch in cadeias_bordas_continuas(tab_beiral, tol=0.05 * FT):
                    created = _solid_sweep_ou_extrude(
                        ch["segs"], ch["is_closed"], pts_tab,
                        0.0, dz_tab_ft, cat_id, mat_tab, comm_tab, add_end_caps=False
                    )
                    ids_tabeiras.extend([el.Id for el in created])
                for s in tab_oitao:
                    created = _solid_sweep_ou_extrude(
                        [s], False, pts_tab,
                        0.0, dz_tab_ft, cat_id, mat_tab, comm_tab, add_end_caps=False
                    )
                    ids_tabeiras.extend([el.Id for el in created])

            # 1C. Rufo L de Encosto
            if op["cr_rufo_encosto"] and encosto_segs:
                av_ft = op["cr_enc_v"] / 30.48
                ah_ft = op["cr_enc_h"] / 30.48
                dob_ft = op["cr_enc_dobra"] / 30.48
                pts_enc = perfil_2d_rufo_encosto(av_ft, ah_ft, dob_ft, t_chapa_ft)
                comm_enc = u"Rufo Encosto L {:.0f}x{:.0f}cm".format(op["cr_enc_v"], op["cr_enc_h"])
                enc_horiz = [s for s in encosto_segs if abs(s["p2"][2] - s["p1"][2]) < 0.025 * FT]
                enc_incl = [s for s in encosto_segs if abs(s["p2"][2] - s["p1"][2]) >= 0.025 * FT]
                for ch in cadeias_bordas_continuas(enc_horiz, tol=0.05 * FT):
                    created = _solid_sweep_ou_extrude(
                        ch["segs"], ch["is_closed"], pts_enc,
                        0.0, 0.0, cat_id, mat_metal, comm_enc, add_end_caps=False
                    )
                    ids_rufos.extend([el.Id for el in created])
                for s in enc_incl:
                    created = _solid_sweep_ou_extrude(
                        [s], False, pts_enc,
                        0.0, 0.0, cat_id, mat_metal, comm_enc, add_end_caps=False
                    )
                    ids_rufos.extend([el.Id for el in created])

            # 2. Rufo Capa em Platibandas
            if op["cr_rufo_capa"]:
                plats_alvo = []
                if op["cr_plat_modo"] == 1:
                    for w in plat_walls:
                        try:
                            loc = w.Location
                            bb = w.get_BoundingBox(None)
                            if isinstance(loc, LocationCurve) and isinstance(loc.Curve, Line) and bb is not None:
                                p0 = loc.Curve.GetEndPoint(0)
                                p1 = loc.Curve.GetEndPoint(1)
                                plats_alvo.append({
                                    "wall": w,
                                    "p1": (p0.X, p0.Y, bb.Max.Z),
                                    "p2": (p1.X, p1.Y, bb.Max.Z),
                                    "top_z": bb.Max.Z,
                                    "width": w.Width,
                                })
                        except Exception:
                            pass
                else:
                    plats_alvo = _detectar_platibandas_acima_telhados(roofs, walls_near)

                ping_ft = op["cr_capa_ping"] / 30.48
                aba_ft = op["cr_capa_aba"] / 30.48
                crista_ft = op["cr_capa_crista"] / 30.48

                # Agrupa por (round(width_cm, 1), round(top_z, 3)) para conseguir esquadrias continuas nos cantos!
                buckets_plat = {}
                for pw in plats_alvo:
                    d_vec = _v_norm(_v_sub(pw["p2"], pw["p1"]))
                    out_xy = (-d_vec[1], d_vec[0], 0.0)
                    seg = {
                        "p1": pw["p1"],
                        "p2": pw["p2"],
                        "out_xy": out_xy,
                        "width": pw["width"],
                    }
                    key = (round(pw["width"] * 30.48, 1), round(pw["top_z"], 3))
                    buckets_plat.setdefault(key, []).append(seg)

                for (w_cm, _), segs_b in buckets_plat.items():
                    w_ft = w_cm / 30.48
                    pts_capa = perfil_2d_rufo_capa(w_ft, aba_ft, ping_ft, crista_ft, t_chapa_ft)
                    comm_capa = u"Rufo Capa Platibanda {:.0f}cm".format(w_cm + 2.0 * op["cr_capa_ping"])
                    for ch in cadeias_bordas_continuas(segs_b, tol=0.08 * FT):
                        created = _solid_sweep_ou_extrude(
                            ch["segs"], ch["is_closed"], pts_capa,
                            0.0, 0.0, cat_id, mat_metal, comm_capa, add_end_caps=False
                        )
                        ids_rufos.extend([el.Id for el in created])

            t.Commit()

        # Agrupamento opcional
        grupos_pend = []
        if op["cr_grp"] == 0:
            grupos_pend.append((ids_calhas, u"Cobertura - Calhas"))
            grupos_pend.append((ids_tabeiras, u"Cobertura - Tabeiras"))
            grupos_pend.append((ids_rufos, u"Cobertura - Rufos"))
        elif op["cr_grp"] == 1:
            grupos_pend.append((ids_calhas + ids_tabeiras + ids_rufos, u"Cobertura - Calhas, Rufos e Tabeiras"))

        if grupos_pend and rf is not None:
            rf.doc, rf.uidoc, rf.view = doc, uidoc, view
            grupos_criados = rf.agrupar_lote_em_transacao(grupos_pend, "Agrupar Calhas, Rufos e Tabeiras")

        tg.Assimilate()

    msg = u"Modelagem de Cobertura concluída!\n\n"
    msg += u"✔ Calhas criadas: {}\n".format(len(ids_calhas))
    msg += u"✔ Tabeiras / Testeiras criadas: {}\n".format(len(ids_tabeiras))
    msg += u"✔ Rufos (Capa / Encosto L) criados: {}\n".format(len(ids_rufos))
    if grupos_criados > 0:
        msg += u"✔ Grupos criados: {}\n".format(grupos_criados)
    if (len(ids_calhas) + len(ids_tabeiras) + len(ids_rufos)) == 0:
        msg += u"\n⚠ Nenhuma borda ou platibanda compatível foi encontrada para os critérios marcados."
    forms.alert(msg, title="AutoBIM: Calhas, Rufos e Tabeiras")


# =============================================================================
# FERRAMENTA 2: LISTA DE CORTE E QUANTITATIVO DA ESTRUTURA DE COBERTURA
# =============================================================================
XAML_QUANTITATIVO = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="AutoBIM - Lista de Corte e Quantitativo da Estrutura de Cobertura"
        Width="545" SizeToContent="Height"
        WindowStartupLocation="CenterScreen" ResizeMode="NoResize"
        Background="#F3F3F3" FontFamily="Segoe UI" FontSize="12">
    <StackPanel Margin="16">
        <Border Background="#E3EEF9" BorderBrush="#0078D4" BorderThickness="0,0,0,2"
                Padding="10,8" Margin="0,0,0,10">
            <TextBlock TextWrapping="Wrap" FontWeight="SemiBold" Foreground="#1F3A57"
                       Text="Lê todas as peças 3D, famílias, treliças e montantes de cobertura, calcula a Lista de Corte agrupada com otimização de barras comerciais e gera a Tabela nativa no Revit."/>
        </Border>

        <Border Background="White" BorderBrush="#D0D7DE" BorderThickness="1" CornerRadius="4" Padding="10" Margin="0,0,0,8">
            <StackPanel>
                <TextBlock Text="Escopo de Levantamento" FontWeight="SemiBold" Margin="0,0,0,4"/>
                <ComboBox x:Name="cb_q_escopo" Height="26" Margin="0,0,0,8"/>
                <TextBlock Text="Categorias e Elementos Incluídos:" FontWeight="SemiBold" Margin="0,0,0,4"/>
                <CheckBox x:Name="ck_q_framing" Content="Quadro Estrutural (Madeiramento, Metálica 3D, Pontaletes, Treliças)" IsChecked="True" Margin="0,2,0,3"/>
                <CheckBox x:Name="ck_q_mullions" Content="Montantes de Vidraça Inclinada (Sloped Glazing)" IsChecked="True" Margin="0,2,0,3"/>
                <CheckBox x:Name="ck_q_generic" Content="Tesouras / Peças AutoBIM em Modelos Genéricos" IsChecked="True" Margin="0,2,0,3"/>
                <CheckBox x:Name="ck_q_calhas" Content="Incluir Calhas, Rufos e Tabeiras (AutoBIM)" IsChecked="False" Margin="0,2,0,0"/>
            </StackPanel>
        </Border>

        <Border Background="White" BorderBrush="#D0D7DE" BorderThickness="1" CornerRadius="4" Padding="10" Margin="0,0,0,8">
            <StackPanel>
                <TextBlock Text="Parâmetros da Lista de Corte e Barras Comerciais" FontWeight="SemiBold" Margin="0,0,0,6"/>
                <Grid>
                    <Grid.ColumnDefinitions>
                        <ColumnDefinition Width="170"/>
                        <ColumnDefinition Width="*"/>
                    </Grid.ColumnDefinitions>
                    <TextBlock Text="Arredondamento de Corte:" VerticalAlignment="Center"/>
                    <ComboBox x:Name="cb_q_passo" Grid.Column="1" Height="24"/>
                </Grid>
                <Grid Margin="0,8,0,0">
                    <Grid.ColumnDefinitions>
                        <ColumnDefinition Width="*"/>
                        <ColumnDefinition Width="10"/>
                        <ColumnDefinition Width="*"/>
                    </Grid.ColumnDefinitions>
                    <StackPanel Grid.Column="0">
                        <TextBlock Text="Barra Comercial Padrão (m):" FontSize="11" Foreground="#555"/>
                        <TextBox x:Name="tb_q_barra" Height="24" VerticalContentAlignment="Center" Text="6.00"/>
                    </StackPanel>
                    <StackPanel Grid.Column="2">
                        <TextBlock Text="Perda Estimada / Margem (%):" FontSize="11" Foreground="#555"/>
                        <TextBox x:Name="tb_q_perda" Height="24" VerticalContentAlignment="Center" Text="10"/>
                    </StackPanel>
                </Grid>
            </StackPanel>
        </Border>

        <Border Background="White" BorderBrush="#D0D7DE" BorderThickness="1" CornerRadius="4" Padding="10" Margin="0,0,0,8">
            <StackPanel>
                <TextBlock Text="Saídas (Revit + Planilha)" FontWeight="SemiBold" Margin="0,0,0,4"/>
                <CheckBox x:Name="ck_q_tabela" Content="Gerar / Atualizar Tabela nativa no Revit ('AutoBIM - Lista de Corte (Cobertura)')" IsChecked="True" Margin="0,2,0,3"/>
                <CheckBox x:Name="ck_q_abrir" Content="Abrir Tabela no Revit ao concluir" IsChecked="True" Margin="18,0,0,4"/>
                <CheckBox x:Name="ck_q_csv" Content="Exportar Lista de Corte + Resumo de Barras Comerciais (.csv para Excel)" IsChecked="True" Margin="0,2,0,0"/>
            </StackPanel>
        </Border>

        <StackPanel Orientation="Horizontal" HorizontalAlignment="Right" Margin="0,6,0,0">
            <Button x:Name="btn_cancel" Content="Cancelar" Width="90" Height="30" Margin="0,0,8,0"/>
            <Button x:Name="btn_ok" Content="Calcular e Gerar Tabela" Width="190" Height="30"
                    IsDefault="True" Background="#0078D4" Foreground="White" FontWeight="SemiBold"/>
        </StackPanel>
    </StackPanel>
</Window>
"""


if HAS_REVIT:
    class QuantitativoCoberturaWindow(object):
        def __init__(self, prefs):
            self.window = XamlReader.Parse(XAML_QUANTITATIVO)
            self.confirmado = False
            self.resultado = None
            w = self.window

            self._cb_escopo = w.FindName("cb_q_escopo")
            self._ck_framing = w.FindName("ck_q_framing")
            self._ck_mullions = w.FindName("ck_q_mullions")
            self._ck_generic = w.FindName("ck_q_generic")
            self._ck_calhas = w.FindName("ck_q_calhas")
            self._cb_passo = w.FindName("cb_q_passo")
            self._tb_barra = w.FindName("tb_q_barra")
            self._tb_perda = w.FindName("tb_q_perda")
            self._ck_tabela = w.FindName("ck_q_tabela")
            self._ck_abrir = w.FindName("ck_q_abrir")
            self._ck_csv = w.FindName("ck_q_csv")

            for item in (
                u"Todo o projeto (estrutura completa)",
                u"Apenas elementos visíveis na vista ativa",
                u"Apenas elementos pré-selecionados",
            ):
                self._cb_escopo.Items.Add(item)
            self._cb_escopo.SelectedIndex = int(prefs.get("q_escopo", 0))

            for item in MODOS_ARREDONDAMENTO:
                self._cb_passo.Items.Add(item)
            self._cb_passo.SelectedIndex = int(prefs.get("q_passo", 2))

            self._ck_framing.IsChecked = bool(prefs.get("q_framing", True))
            self._ck_mullions.IsChecked = bool(prefs.get("q_mullions", True))
            self._ck_generic.IsChecked = bool(prefs.get("q_generic", True))
            self._ck_calhas.IsChecked = bool(prefs.get("q_calhas", False))
            self._tb_barra.Text = unicode(prefs.get("q_barra", "6.00"))
            self._tb_perda.Text = unicode(prefs.get("q_perda", "10"))
            self._ck_tabela.IsChecked = bool(prefs.get("q_tabela", True))
            self._ck_abrir.IsChecked = bool(prefs.get("q_abrir", True))
            self._ck_csv.IsChecked = bool(prefs.get("q_csv", True))

            self._ck_tabela.Click += self._ao_mudar_tab
            w.FindName("btn_ok").Click += self._ao_ok
            w.FindName("btn_cancel").Click += self._ao_cancelar
            self._ao_mudar_tab(None, None)

        def _ao_mudar_tab(self, s, a):
            self._ck_abrir.IsEnabled = bool(self._ck_tabela.IsChecked)

        def _ao_cancelar(self, s, a):
            self.window.Close()

        def _ao_ok(self, s, a):
            if not (self._ck_framing.IsChecked or self._ck_mullions.IsChecked or
                    self._ck_generic.IsChecked or self._ck_calhas.IsChecked):
                forms.alert(u"Marque pelo menos uma categoria para levantar.")
                return
            self.resultado = {
                "q_escopo": max(0, int(self._cb_escopo.SelectedIndex)),
                "q_framing": bool(self._ck_framing.IsChecked),
                "q_mullions": bool(self._ck_mullions.IsChecked),
                "q_generic": bool(self._ck_generic.IsChecked),
                "q_calhas": bool(self._ck_calhas.IsChecked),
                "q_passo": max(0, int(self._cb_passo.SelectedIndex)),
                "q_barra": max(0.5, _parse_float(self._tb_barra.Text, 6.0)),
                "q_perda": max(0.0, _parse_float(self._tb_perda.Text, 10.0)),
                "q_tabela": bool(self._ck_tabela.IsChecked),
                "q_abrir": bool(self._ck_abrir.IsChecked),
                "q_csv": bool(self._ck_csv.IsChecked),
            }
            self.confirmado = True
            self.window.Close()

        def ShowDialog(self):
            return self.window.ShowDialog()


def _elem_name_safe(el):
    if el is None:
        return u""
    try:
        n = Element.Name.GetValue(el)
        if n:
            return unicode(n)
    except Exception:
        pass
    try:
        return unicode(el.Name)
    except Exception:
        return u""


def _extrair_material_elemento(el):
    for bip in (BuiltInParameter.STRUCTURAL_MATERIAL_PARAM, BuiltInParameter.MATERIAL_ID_PARAM):
        try:
            p = el.get_Parameter(bip)
            if p is not None and p.StorageType == StorageType.ElementId:
                mid = p.AsElementId()
                if mid != ElementId.InvalidElementId:
                    m = doc.GetElement(mid)
                    if m is not None:
                        return _elem_name_safe(m)
        except Exception:
            pass
    for sol in _extrair_solidos_elemento(el):
        try:
            for f in sol.Faces:
                mid = f.MaterialElementId
                if mid != ElementId.InvalidElementId:
                    m = doc.GetElement(mid)
                    if m is not None:
                        return _elem_name_safe(m)
        except Exception:
            pass
    return u"Não definido"


def _classificar_grupo_peca(comment, cat_bip_id, mat_name):
    c_low = (comment or u"").lower()
    m_low = (mat_name or u"").lower()
    if any(k in c_low for k in (u"calha", u"rufo", u"tabeira")):
        return u"Calhas, Rufos e Tabeiras"
    if cat_bip_id == int(BuiltInCategory.OST_CurtainWallMullions):
        return u"Vidraça Inclinada"
    if u"treliç" in c_low or u"trelic" in c_low or u"banzo" in c_low or u"diagonal" in c_low or u"montante treli" in c_low:
        return u"Treliças Metálicas"
    if u"tesoura" in c_low or u"pontalete" in c_low or u"berço" in c_low or u"berco" in c_low:
        return u"Tesouras e Pontaletes"
    if (u"aço" in m_low or "aco" in m_low or "metal" in m_low or
            u"perfil " in c_low or u"metalon" in c_low or u"enrij" in c_low):
        return u"Estrutura Metálica"
    return u"Madeiramento"


def _medir_elemento_cobertura(el):
    """Extrai medidas geometricas reais e metadados de DirectShape, FamilyInstance ou Mullion."""
    if el is None or not el.IsValidObject:
        return None
    cat = getattr(el, "Category", None)
    if cat is None:
        return None
    cat_id_int = int(cat.Id.IntegerValue) if hasattr(cat.Id, "IntegerValue") else int(cat.Id.Value)

    comment = u""
    try:
        pc = el.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
        if pc is not None and pc.AsString():
            comment = unicode(pc.AsString()).strip()
    except Exception:
        pass

    fam_name = u""
    type_name = u""
    try:
        tid = el.GetTypeId()
        if tid != ElementId.InvalidElementId:
            tp = doc.GetElement(tid)
            if tp is not None:
                type_name = _elem_name_safe(tp)
                fam_name = unicode(getattr(tp, "FamilyName", u"") or u"")
    except Exception:
        pass

    # Filtro de seguranca para GenericModel / Roofs: evita contabilizar telhados em si ou moveis aleatorios
    if cat_id_int == int(BuiltInCategory.OST_Roofs):
        if isinstance(el, RoofBase):
            return None
        if not any(k in comment.lower() for k in (u"calha", u"rufo", u"tabeira")):
            return None
    elif cat_id_int == int(BuiltInCategory.OST_GenericModel):
        c_low = (comment + u" " + fam_name + u" " + type_name).lower()
        is_roof_piece = any(k in c_low for k in (
            u"tesoura", u"treliç", u"trelic", u"pontalete", u"caibro", u"ripa",
            u"terça", u"terca", u"frechal", u"cumeeira", u"espig", u"banzo",
            u"calha", u"rufo", u"tabeira", u"autobim"
        ))
        if not is_roof_piece:
            return None

    # Extracao do comprimento e secao
    comp_m = 0.0
    b_cm = 0.0
    h_cm = 0.0

    # 1. Tenta parametros nativos de comprimento em FamilyInstance / Mullion
    if cat_id_int == int(BuiltInCategory.OST_CurtainWallMullions):
        try:
            pl = el.get_Parameter(BuiltInParameter.CURVE_ELEM_LENGTH)
            if pl is not None and pl.AsDouble() > 1e-4:
                comp_m = pl.AsDouble() * 0.3048
        except Exception:
            pass
    elif isinstance(el, FamilyInstance):
        for bip in (BuiltInParameter.STRUCTURAL_FRAME_CUT_LENGTH, BuiltInParameter.INSTANCE_LENGTH_PARAM):
            try:
                pl = el.get_Parameter(bip)
                if pl is not None and pl.AsDouble() > 1e-4:
                    comp_m = pl.AsDouble() * 0.3048
                    break
            except Exception:
                pass

    # 2. Analisa o Solid 3D real para medir pecas inclinadas/rotacionadas (DirectShape ou fallback)
    sols = _extrair_solidos_elemento(el)
    if sols:
        edges_segs = []
        pts_xyz = []
        for sol in sols:
            try:
                for edge in sol.Edges:
                    tess = edge.Tessellate()
                    if not tess:
                        continue
                    for p in tess:
                        pts_xyz.append((p.X, p.Y, p.Z))
                    if len(tess) == 2:
                        edges_segs.append((
                            (tess[0].X, tess[0].Y, tess[0].Z),
                            (tess[1].X, tess[1].Y, tess[1].Z),
                        ))
                    elif len(tess) > 2:
                        edges_segs.append((
                            (tess[0].X, tess[0].Y, tess[0].Z),
                            (tess[-1].X, tess[-1].Y, tess[-1].Z),
                        ))
            except Exception:
                pass
        c_sol, b_sol, h_sol = extrair_dimensoes_por_arestas_e_pontos(edges_segs, pts_xyz)
        if comp_m <= 1e-4:
            comp_m = c_sol
        b_cm, h_cm = b_sol, h_sol

    # Fallback LocationCurve
    if comp_m <= 1e-4:
        try:
            loc = getattr(el, "Location", None)
            if isinstance(loc, LocationCurve) and loc.Curve is not None:
                comp_m = loc.Curve.Length * 0.3048
        except Exception:
            pass

    if comp_m <= 1e-3:
        return None

    mat_name = _extrair_material_elemento(el)
    peca, secao = _normalizar_rotulo_peca(comment, fam_name, type_name, b_cm, h_cm)
    grupo = _classificar_grupo_peca(comment + u" " + peca, cat_id_int, mat_name)

    return {
        "id": el.Id,
        "cat_id": cat_id_int,
        "grupo": grupo,
        "peca": peca,
        "secao": secao,
        "material": mat_name,
        "comp_m": comp_m,
    }


def _coletar_elementos_cobertura(op):
    escopo = op["q_escopo"]
    cats_alvo = []
    if op["q_framing"]:
        cats_alvo.append(BuiltInCategory.OST_StructuralFraming)
    if op["q_mullions"]:
        cats_alvo.append(BuiltInCategory.OST_CurtainWallMullions)
    if op["q_generic"]:
        cats_alvo.append(BuiltInCategory.OST_GenericModel)
    if op["q_calhas"]:
        cats_alvo.append(BuiltInCategory.OST_Roofs)
        if BuiltInCategory.OST_GenericModel not in cats_alvo:
            cats_alvo.append(BuiltInCategory.OST_GenericModel)

    allowed_cats = set(int(c) for c in cats_alvo)
    elems = []
    seen = set()

    if escopo == 2:
        for eid in uidoc.Selection.GetElementIds():
            el = doc.GetElement(eid)
            if el is None or el.Category is None:
                continue
            cid = int(el.Category.Id.IntegerValue) if hasattr(el.Category.Id, "IntegerValue") else int(el.Category.Id.Value)
            if cid in allowed_cats:
                elems.append(el)
        return elems

    for bic in cats_alvo:
        if escopo == 1:
            col = FilteredElementCollector(doc, view.Id).OfCategory(bic).WhereElementIsNotElementType().ToElements()
        else:
            col = FilteredElementCollector(doc).OfCategory(bic).WhereElementIsNotElementType().ToElements()
        for el in col:
            eid_val = int(el.Id.IntegerValue) if hasattr(el.Id, "IntegerValue") else int(el.Id.Value)
            if eid_val in seen:
                continue
            seen.add(eid_val)
            elems.append(el)
    return elems


_SP_SPECS = (
    ("AutoBIM_Cob_Codigo", "text"),
    ("AutoBIM_Cob_Grupo", "text"),
    ("AutoBIM_Cob_Peca", "text"),
    ("AutoBIM_Cob_Secao", "text"),
    ("AutoBIM_Cob_Material", "text"),
    ("AutoBIM_Cob_CompUnit_m", "number"),
    ("AutoBIM_Cob_CompTotal_m", "number"),
)


def _garantir_parametros_cobertura(cats_bics):
    """Cria/vincula parametros compartilhados de Cobertura preservando o arquivo SP do usuario."""
    app = doc.Application
    orig_sp = app.SharedParametersFilename
    tmp_path = os.path.join(tempfile.gettempdir(), "AutoBIM_Cobertura_SP.txt")
    if not os.path.exists(tmp_path):
        with codecs.open(tmp_path, "w", encoding="utf-8") as f:
            f.write("")

    cat_set = CategorySet()
    for bic in cats_bics:
        try:
            c = doc.Settings.Categories.get_Item(bic)
            if c is not None and c.AllowsBoundParameters:
                cat_set.Insert(c)
        except Exception:
            pass
    if cat_set.Size == 0:
        return False

    try:
        app.SharedParametersFilename = tmp_path
        sp_file = app.OpenSharedParameterFile()
        if sp_file is None:
            return False
        grp = sp_file.Groups.get_Item("AutoBIM_Cobertura")
        if grp is None:
            grp = sp_file.Groups.Create("AutoBIM_Cobertura")

        for pname, pkind in _SP_SPECS:
            defn = grp.Definitions.get_Item(pname)
            if defn is None:
                try:
                    from Autodesk.Revit.DB import SpecTypeId
                    spec = SpecTypeId.String.Text if pkind == "text" else SpecTypeId.Number
                    opts = ExternalDefinitionCreationOptions(pname, spec)
                except Exception:
                    from Autodesk.Revit.DB import ParameterType
                    pt = ParameterType.Text if pkind == "text" else ParameterType.Number
                    opts = ExternalDefinitionCreationOptions(pname, pt)
                opts.Visible = True
                defn = grp.Definitions.Create(opts)

            binding = app.Create.NewInstanceBinding(cat_set)
            try:
                from Autodesk.Revit.DB import GroupTypeId
                pg = GroupTypeId.Data
            except Exception:
                from Autodesk.Revit.DB import BuiltInParameterGroup
                pg = BuiltInParameterGroup.PG_DATA

            # Tenta ReInsert (ou Insert se nao existir)
            ok = False
            try:
                if doc.ParameterBindings.Contains(defn):
                    ok = doc.ParameterBindings.ReInsert(defn, binding, pg)
                else:
                    ok = doc.ParameterBindings.Insert(defn, binding, pg)
            except Exception:
                try:
                    ok = doc.ParameterBindings.Insert(defn, binding, pg)
                except Exception:
                    pass
    finally:
        try:
            app.SharedParametersFilename = orig_sp or ""
        except Exception:
            pass
    return True


def _escrever_parametros_nos_elementos(registros, mapa_cod_elem, limpar_outros_bics=None):
    if limpar_outros_bics:
        ativos = set(id_el for id_el in mapa_cod_elem.keys())
        for bic in limpar_outros_bics:
            for el in FilteredElementCollector(doc).OfCategory(bic).WhereElementIsNotElementType().ToElements():
                if el.Id in ativos:
                    continue
                try:
                    p = el.LookupParameter("AutoBIM_Cob_Codigo")
                    if p is not None and not p.IsReadOnly and p.AsString():
                        p.Set("")
                        pt = el.LookupParameter("AutoBIM_Cob_CompTotal_m")
                        if pt is not None and not pt.IsReadOnly:
                            pt.Set(0.0)
                except Exception:
                    pass

    for eid, row in mapa_cod_elem.items():
        el = doc.GetElement(eid)
        if el is None:
            continue
        vals = (
            ("AutoBIM_Cob_Codigo", row["codigo"]),
            ("AutoBIM_Cob_Grupo", row["grupo"]),
            ("AutoBIM_Cob_Peca", row["peca"]),
            ("AutoBIM_Cob_Secao", row["secao"]),
            ("AutoBIM_Cob_Material", row["material"]),
            ("AutoBIM_Cob_CompUnit_m", float(row["comp_unit_m"])),
            ("AutoBIM_Cob_CompTotal_m", float(row["comp_unit_m"])),
        )
        for pname, pval in vals:
            try:
                p = el.LookupParameter(pname)
                if p is not None and not p.IsReadOnly:
                    p.Set(pval)
            except Exception:
                pass


def _obter_ou_criar_tabela_cobertura(nome_tabela, cat_elem_id):
    for vs in FilteredElementCollector(doc).OfClass(ViewSchedule).ToElements():
        try:
            if not vs.IsTemplate and vs.Name == nome_tabela:
                # Verifica se corresponde a mesma categoria; se sim reutiliza limpando campos
                if vs.Definition.CategoryId == cat_elem_id:
                    return vs
        except Exception:
            pass
    vs = ViewSchedule.CreateSchedule(doc, cat_elem_id)
    try:
        vs.Name = nome_tabela
    except Exception:
        for k in range(2, 50):
            try:
                vs.Name = u"{} ({})".format(nome_tabela, k)
                break
            except Exception:
                pass
    return vs


def _configurar_schedule_lista_corte(vs, filtrar_mullion=False):
    defn = vs.Definition
    defn.ClearFields()
    try:
        defn.ClearFilters()
    except Exception:
        pass
    try:
        defn.ClearSortGroupFields()
    except Exception:
        pass

    sfields = list(defn.GetSchedulableFields())
    by_name = {}
    by_bip = {}
    for sf in sfields:
        try:
            by_name[sf.GetName(doc).strip()] = sf
        except Exception:
            pass
        try:
            pid_int = int(sf.ParameterId.IntegerValue) if hasattr(sf.ParameterId, "IntegerValue") else int(sf.ParameterId.Value)
            by_bip[pid_int] = sf
        except Exception:
            pass

    if filtrar_mullion:
        # Tabela dedicada para Montantes de Vidraca Inclinada caso nao aceite SP em parametro vinculado
        col_specs = [
            (by_bip.get(int(BuiltInParameter.ELEM_TYPE_PARAM)) or by_name.get(u"Tipo") or by_name.get(u"Type"), u"Perfil / Tipo", False, True),
            (by_bip.get(int(BuiltInParameter.CURVE_ELEM_LENGTH)) or by_name.get(u"Comprimento") or by_name.get(u"Length"), u"Comp. Unitário", True, False),
        ]
        sf_count = None
        for sf in sfields:
            if sf.FieldType == ScheduleFieldType.Count:
                sf_count = sf
                break
        f_tipo = None
        f_len = None
        for sf, heading, do_total, is_sort in col_specs:
            if sf is None:
                continue
            fld = defn.AddField(sf)
            fld.ColumnHeading = heading
            if is_sort:
                f_tipo = fld
            if do_total:
                f_len = fld
                try:
                    if fld.CanTotal():
                        fld.DisplayType = ScheduleFieldDisplayType.Totals
                except Exception:
                    pass
        if sf_count is not None:
            fc = defn.AddField(sf_count)
            fc.ColumnHeading = u"Qtd."
            try:
                if fc.CanTotal():
                    fc.DisplayType = ScheduleFieldDisplayType.Totals
            except Exception:
                pass
        if f_tipo is not None:
            defn.AddSortGroupField(ScheduleSortGroupField(f_tipo.FieldId, ScheduleSortOrder.Ascending))
        if f_len is not None:
            defn.AddSortGroupField(ScheduleSortGroupField(f_len.FieldId, ScheduleSortOrder.Descending))
        defn.IsItemized = False
        try:
            defn.ShowGrandTotal = True
            defn.ShowGrandTotalCount = True
            defn.ShowGrandTotalTitle = True
            defn.GrandTotalTitle = u"Total Vidraça Inclinada"
        except Exception:
            pass
        return True

    # Tabela Completa AutoBIM Lista de Corte usando Shared Parameters
    colunas = [
        ("AutoBIM_Cob_Codigo", u"Código", False, True),
        ("AutoBIM_Cob_Grupo", u"Categoria", False, False),
        ("AutoBIM_Cob_Peca", u"Peça / Perfil", False, True),
        ("AutoBIM_Cob_Secao", u"Seção", False, False),
        ("AutoBIM_Cob_Material", u"Material", False, False),
        ("AutoBIM_Cob_CompUnit_m", u"Comp. Corte (m)", False, False),
    ]
    fields_map = {}
    for sp_name, heading, _, _ in colunas:
        sf = by_name.get(sp_name)
        if sf is None:
            continue
        fld = defn.AddField(sf)
        fld.ColumnHeading = heading
        fields_map[sp_name] = fld

    # Campo nativo de Contagem (Qtd.)
    for sf in sfields:
        if sf.FieldType == ScheduleFieldType.Count:
            fc = defn.AddField(sf)
            fc.ColumnHeading = u"Qtd."
            try:
                if fc.CanTotal():
                    fc.DisplayType = ScheduleFieldDisplayType.Totals
            except Exception:
                pass
            break

    # Soma total de metros por linha + rodape
    sf_tot = by_name.get("AutoBIM_Cob_CompTotal_m")
    if sf_tot is not None:
        ftot = defn.AddField(sf_tot)
        ftot.ColumnHeading = u"Comp. Total (m)"
        try:
            if ftot.CanTotal():
                ftot.DisplayType = ScheduleFieldDisplayType.Totals
        except Exception:
            pass

    if "AutoBIM_Cob_Codigo" not in fields_map:
        return False

    # Filtra apenas elementos que receberam Codigo na Lista de Corte
    try:
        flt = ScheduleFilter(fields_map["AutoBIM_Cob_Codigo"].FieldId, ScheduleFilterType.HasValue)
        defn.AddFilter(flt)
    except Exception:
        pass

    # Agrupa por Codigo -> Peca
    try:
        defn.AddSortGroupField(ScheduleSortGroupField(fields_map["AutoBIM_Cob_Codigo"].FieldId, ScheduleSortOrder.Ascending))
        if "AutoBIM_Cob_Peca" in fields_map:
            defn.AddSortGroupField(ScheduleSortGroupField(fields_map["AutoBIM_Cob_Peca"].FieldId, ScheduleSortOrder.Ascending))
    except Exception:
        pass

    defn.IsItemized = False
    try:
        defn.ShowGrandTotal = True
        defn.ShowGrandTotalCount = True
        defn.ShowGrandTotalTitle = True
        defn.GrandTotalTitle = u"TOTAL GERAL ESTRUTURA DE COBERTURA"
    except Exception:
        pass
    return True


def _exportar_csv_lista_corte(dados_agg, pasta_destino):
    nome_arq = u"AutoBIM_Lista_de_Corte_Cobertura.csv"
    caminho = os.path.join(pasta_destino, nome_arq)

    # Escreve UTF-8 com BOM para abrir acentos perfeitamente no Excel PT-BR (separador ';')
    with codecs.open(caminho, "w", encoding="utf-8-sig") as f:
        def _w(cols):
            line = u";".join([u'"{}"'.format(unicode(c).replace(u'"', u'""')) for c in cols])
            f.write(line + u"\r\n")

        _w([u"LISTA DE CORTE DA ESTRUTURA DE COBERTURA - AUTOBIM"])
        _w([])
        _w([u"Código", u"Categoria", u"Peça / Perfil", u"Seção", u"Material", u"Comp. Corte (m)", u"Qtd.", u"Comp. Total (m)"])
        for row in dados_agg["linhas_corte"]:
            _w([
                row["codigo"],
                row["grupo"],
                row["peca"],
                row["secao"],
                row["material"],
                u"{:.2f}".format(row["comp_unit_m"]).replace(".", ","),
                row["qtd"],
                u"{:.2f}".format(row["comp_total_m"]).replace(".", ","),
            ])

        _w([])
        _w([u"RESUMO DE COMPRA E OTIMIZAÇÃO DE BARRAS COMERCIAIS"])
        _w([u"Categoria", u"Peça / Perfil", u"Seção", u"Material", u"Qtd. Peças", u"Comp. Líquido (m)",
            u"Comp. c/ Perda (m)", u"Barra (m)", u"Qtd. Barras (FFD)", u"Aproveitamento (%)"])
        for pf in dados_agg["resumo_perfis"]:
            _w([
                pf["grupo"],
                pf["peca"],
                pf["secao"],
                pf["material"],
                pf["qtd_pecas"],
                u"{:.2f}".format(pf["comp_liquido_m"]).replace(".", ","),
                u"{:.2f}".format(pf["comp_com_perda_m"]).replace(".", ","),
                u"{:.2f}".format(pf["barra_m"]).replace(".", ","),
                pf["qtd_barras"],
                u"{:.1f}%".format(pf["aproveitamento_pct"]).replace(".", ","),
            ])
    return caminho


def run_quantitativo_cobertura():
    global doc, uidoc, view
    uidoc = __revit__.ActiveUIDocument
    doc = uidoc.Document
    view = doc.ActiveView

    prefs = carregar_preferencias()
    win = QuantitativoCoberturaWindow(prefs)
    win.ShowDialog()
    if not win.confirmado:
        script.exit()

    op = win.resultado
    salvar_preferencias(op)

    elems = _coletar_elementos_cobertura(op)
    registros = []
    cats_presentes = set()
    for el in elems:
        med = _medir_elemento_cobertura(el)
        if med is not None:
            registros.append(med)
            cats_presentes.add(med["cat_id"])

    if not registros:
        forms.alert(
            u"Nenhuma peça de estrutura de cobertura encontrada para os critérios selecionados.",
            title="AutoBIM: Lista de Corte",
            exitscript=True,
        )

    dados_agg = agregar_lista_de_corte(
        registros,
        modo_passo=op["q_passo"],
        barra_m=op["q_barra"],
        perda_pct=op["q_perda"],
    )

    tabelas_criadas = []
    if op["q_tabela"]:
        bics_para_vincular = []
        mullion_apenas_nativo = False
        for cid_int in cats_presentes:
            if cid_int == int(BuiltInCategory.OST_StructuralFraming):
                bics_para_vincular.append(BuiltInCategory.OST_StructuralFraming)
            elif cid_int == int(BuiltInCategory.OST_GenericModel):
                bics_para_vincular.append(BuiltInCategory.OST_GenericModel)
            elif cid_int == int(BuiltInCategory.OST_Roofs):
                bics_para_vincular.append(BuiltInCategory.OST_Roofs)
            elif cid_int == int(BuiltInCategory.OST_CurtainWallMullions):
                c_m = doc.Settings.Categories.get_Item(BuiltInCategory.OST_CurtainWallMullions)
                if c_m is not None and c_m.AllowsBoundParameters:
                    bics_para_vincular.append(BuiltInCategory.OST_CurtainWallMullions)
                else:
                    mullion_apenas_nativo = True

        with Transaction(doc, "AutoBIM: Tabela Lista de Corte de Cobertura") as t:
            t.Start()
            if bics_para_vincular:
                _garantir_parametros_cobertura(bics_para_vincular)
                try:
                    doc.Regenerate()
                except Exception:
                    pass
                _escrever_parametros_nos_elementos(
                    registros,
                    dados_agg["mapa_cod_elem"],
                    limpar_outros_bics=bics_para_vincular,
                )

                # Tenta Tabela Multicategoria primeiro para unir Framing + GenericModel + Roofs em 1 tabela
                nome_main = u"AutoBIM - Lista de Corte (Cobertura)"
                vs_main = None
                try:
                    vs_main = _obter_ou_criar_tabela_cobertura(nome_main, ElementId.InvalidElementId)
                    ok_cfg = _configurar_schedule_lista_corte(vs_main, filtrar_mullion=False)
                    if not ok_cfg:
                        doc.Delete(vs_main.Id)
                        vs_main = None
                except Exception:
                    vs_main = None

                if vs_main is None and BuiltInCategory.OST_StructuralFraming in bics_para_vincular:
                    vs_main = _obter_ou_criar_tabela_cobertura(
                        u"AutoBIM - Lista de Corte (Quadro Estrutural)",
                        ElementId(BuiltInCategory.OST_StructuralFraming),
                    )
                    _configurar_schedule_lista_corte(vs_main, filtrar_mullion=False)

                if vs_main is not None:
                    tabelas_criadas.append(vs_main)

            if int(BuiltInCategory.OST_CurtainWallMullions) in cats_presentes and (mullion_apenas_nativo or not bics_para_vincular):
                vs_mul = _obter_ou_criar_tabela_cobertura(
                    u"AutoBIM - Lista de Corte (Vidraça Inclinada)",
                    ElementId(BuiltInCategory.OST_CurtainWallMullions),
                )
                _configurar_schedule_lista_corte(vs_mul, filtrar_mullion=True)
                tabelas_criadas.append(vs_mul)

            t.Commit()

        if op["q_abrir"] and tabelas_criadas:
            try:
                uidoc.ActiveView = tabelas_criadas[0]
            except Exception:
                pass

    csv_path = None
    if op["q_csv"]:
        pasta_base = None
        try:
            if doc.PathName:
                pasta_base = os.path.dirname(doc.PathName)
        except Exception:
            pass
        if not pasta_base or not os.path.isdir(pasta_base):
            pasta_base = os.path.join(os.path.expanduser("~"), "Desktop")
            if not os.path.isdir(pasta_base):
                pasta_base = tempfile.gettempdir()
        try:
            csv_path = _exportar_csv_lista_corte(dados_agg, pasta_base)
        except Exception:
            csv_path = None

    tot = dados_agg["totais"]
    linhas_msg = [
        u"Lista de Corte e Quantitativo concluídos!\n",
        u"✔ Total de peças levantadas: {}".format(tot["total_pecas"]),
        u"✔ Itens únicos de corte: {}".format(tot["total_linhas_corte"]),
        u"✔ Metragem linear total: {:.2f} m".format(tot["total_metros"]),
        u"✔ Estimativa em barras de {:.2f} m (FFD): {} barras\n".format(op["q_barra"], tot["total_barras"]),
    ]
    if tabelas_criadas:
        linhas_msg.append(u"✔ Tabela(s) Revit: " + u", ".join([vs.Name for vs in tabelas_criadas]))
    if csv_path:
        linhas_msg.append(u"✔ Planilha CSV exportada:\n   " + csv_path)

    linhas_msg.append(u"\n--- Resumo por Perfil ---")
    for pf in dados_agg["resumo_perfis"][:10]:
        linhas_msg.append(
            u"• {} [{}]: {} pçs | {:.2f} m → {} barras ({:.0f}% aprov.)".format(
                pf["peca"], pf["material"], pf["qtd_pecas"],
                pf["comp_liquido_m"], pf["qtd_barras"], pf["aproveitamento_pct"]
            )
        )
    if len(dados_agg["resumo_perfis"]) > 10:
        linhas_msg.append(u"... e mais {} perfis (ver Tabela / CSV).".format(len(dados_agg["resumo_perfis"]) - 10))

    forms.alert(u"\n".join(linhas_msg), title="AutoBIM: Lista de Corte e Quantitativo")
