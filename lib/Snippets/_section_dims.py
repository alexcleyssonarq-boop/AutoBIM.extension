# -*- coding: utf-8 -*-
"""Ferramentas de Cotas e Anotacoes Automaticas em Cortes e Elevacoes (AutoBIM):
1. CotarCorteLinha     - Cota Rapida no Corte (por Linha, 2 cliques, vertical ou horizontal)
2. CotarPeDireito      - Pe-Direito por Ambiente (Piso acabado -> Forro -> Laje / Total)
3. CotarEsquadriasCorte - Cotas Verticais (e Largura) de Portas e Janelas no Corte/Elevacao
4. InclinacaoTelhado   - Indicacao Automatica de Inclinacao de Telhado (% e graus)
"""

import math
import json
import codecs
import os
import clr
clr.AddReference("System")
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")

from Autodesk.Revit.DB import *
from Autodesk.Revit.DB.Architecture import Room, RoomTag
from Autodesk.Revit.UI.Selection import ObjectType, ObjectSnapTypes, ISelectionFilter
from pyrevit import forms, script
import Snippets._doc_extra as de
import Snippets._ui_search as uis
try:
    reload(uis)
except Exception:
    pass

doc   = None
uidoc = None
view  = None

FT = 1.0 / 0.3048
MM = 1.0 / 304.8
SETTINGS_PATH = os.path.join(os.path.dirname(__file__), "_section_dims_settings.json")
SNAP_NENHUM = getattr(ObjectSnapTypes, "None")


def _init_ctx():
    global doc, uidoc, view
    uidoc = __revit__.ActiveUIDocument
    doc   = uidoc.Document
    view  = doc.ActiveView
    de.doc   = doc
    de.uidoc = uidoc
    de.view  = view


def _eid_int(eid):
    """Compatibilidade Revit 2024/2026 (ElementId.Value) e anteriores (IntegerValue)."""
    if eid is None:
        return -1
    if hasattr(eid, "Value"):
        return int(eid.Value)
    return int(eid.IntegerValue)


def _carregar_prefs():
    try:
        if os.path.exists(SETTINGS_PATH):
            with codecs.open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _salvar_prefs(dados):
    try:
        atual = _carregar_prefs()
        atual.update(dados)
        with codecs.open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(atual, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ==============================================================================
# HELPERS MATEMATICOS PUROS (TESTAVEIS SEM REVIT)
# ==============================================================================

def _dedup_refs_por_coord(itens, min_gap=0.0065):
    """Recebe lista de (coord, ref, tag) e retorna ordenada sem duplicatas < min_gap (default ~2mm)."""
    if not itens:
        return []
    ord_it = sorted(itens, key=lambda x: x[0])
    res = [ord_it[0]]
    for it in ord_it[1:]:
        if abs(it[0] - res[-1][0]) >= min_gap:
            res.append(it)
    return res


def _angulo_texto_legivel(tu, tv):
    """Retorna (angle_rad, dir_u, dir_v) em (-pi/2, pi/2] para que o texto nunca fique de ponta-cabeca."""
    mag = math.hypot(tu, tv)
    if mag < 1e-9:
        return (0.0, 1.0, 0.0)
    ux, vx = tu / mag, tv / mag
    ang = math.atan2(vx, ux)
    while ang <= -0.5 * math.pi + 1e-6:
        ang += math.pi
        ux, vx = -ux, -vx
    while ang > 0.5 * math.pi + 1e-6:
        ang -= math.pi
        ux, vx = -ux, -vx
    return (ang, ux, vx)


def _calcular_inclinacao_face(nx, ny, nz):
    """Retorna (percentual, graus) a partir da normal 3D de uma face plana inclinada."""
    nh = math.hypot(nx, ny)
    av = abs(nz)
    if av < 1e-6:
        return (0.0, 90.0)
    ratio = nh / av
    return (ratio * 100.0, math.degrees(math.atan(ratio)))


def _filtrar_refs_por_intervalo_u(u_lo, u_hi, u_probe, tol=0.35):
    return (u_lo - tol) <= u_probe <= (u_hi + tol)


def _extrair_vaos_entre_intervalos_u(intervalos_paredes, gap_min=0.45 / 0.3048):
    """Recebe intervalos ocupados por paredes [(u_lo, u_hi), ...] e retorna os vaos livres internos [(u_a, u_b), ...]."""
    if not intervalos_paredes or len(intervalos_paredes) < 2:
        return []
    ord_iv = sorted((min(a, b), max(a, b)) for a, b in intervalos_paredes)
    fundidos = [list(ord_iv[0])]
    for lo, hi in ord_iv[1:]:
        if lo <= fundidos[-1][1] + 0.05 / 0.3048:
            if hi > fundidos[-1][1]:
                fundidos[-1][1] = hi
        else:
            fundidos.append([lo, hi])
    vaos = []
    for i in range(len(fundidos) - 1):
        ua = fundidos[i][1]
        ub = fundidos[i + 1][0]
        if (ub - ua) >= gap_min:
            vaos.append((ua, ub))
    return vaos


def _proximo_slot_externo(base_coord, step, ocupados, sentido=1, tol=None):
    """Avanca base_coord para fora (+1 ou -1) enquanto colidir com cotas existentes em ocupados."""
    pos = float(base_coord)
    if not ocupados or step <= 1e-6:
        return pos
    if tol is None:
        tol = 0.45 * step
    sg = 1.0 if sentido >= 0 else -1.0
    for _ in range(16):
        colide = False
        for c in ocupados:
            if abs(pos - float(c)) <= tol:
                colide = True
                break
        if not colide:
            break
        pos += sg * step
    return pos


# ==============================================================================
# HELPERS REVIT PARA CORTE / ELEVACAO
# ==============================================================================

def _verificar_view_section(nome_cmd):
    _init_ctx()
    if not isinstance(view, ViewSection):
        forms.alert(
            u"Execute '{}' em uma vista de Corte ou Elevação.".format(nome_cmd),
            title="Vista Inválida"
        )
        script.exit()


def _garantir_sketch_plane_secao(v):
    if v.SketchPlane is not None:
        return v.SketchPlane
    pl = Plane.CreateByNormalAndOrigin(v.ViewDirection, v.Origin)
    sp = SketchPlane.Create(doc, pl)
    v.SketchPlane = sp
    return sp


def _listar_tipos_cota_linear():
    res = []
    for dt in FilteredElementCollector(doc).OfClass(DimensionType).ToElements():
        try:
            if dt.StyleType == DimensionStyleType.Linear:
                res.append(dt)
        except Exception:
            pass
    res.sort(key=lambda x: de._natural_key(de._elem_name(x)))
    return res


def _listar_tipos_texto():
    res = list(FilteredElementCollector(doc).OfClass(TextNoteType).ToElements())
    res.sort(key=lambda x: de._natural_key(de._elem_name(x)))
    return res


def _uv_em_vista(pt, org, rdir, upv):
    dx = pt.X - org.X
    dy = pt.Y - org.Y
    dz = pt.Z - org.Z
    return (
        dx * rdir.X + dy * rdir.Y + dz * rdir.Z,
        dx * upv.X  + dy * upv.Y  + dz * upv.Z,
    )


def _bb_uv_em_vista(el, v_view):
    bb = el.get_BoundingBox(v_view) or el.get_BoundingBox(None)
    if bb is None:
        return None
    org  = v_view.Origin
    rdir = v_view.RightDirection
    upv  = v_view.UpDirection
    us, vs = [], []
    for cx in (bb.Min.X, bb.Max.X):
        for cy in (bb.Min.Y, bb.Max.Y):
            for cz in (bb.Min.Z, bb.Max.Z):
                u, v = _uv_em_vista(XYZ(cx, cy, cz), org, rdir, upv)
                us.append(u)
                vs.append(v)
    return (min(us), max(us), min(vs), max(vs), bb)


def _crop_uv_em_vista(v_view):
    if not v_view.CropBoxActive:
        return None
    try:
        cb = v_view.CropBox
        tr = cb.Transform
        org  = v_view.Origin
        rdir = v_view.RightDirection
        upv  = v_view.UpDirection
        us, vs = [], []
        for cx in (cb.Min.X, cb.Max.X):
            for cy in (cb.Min.Y, cb.Max.Y):
                for cz in (cb.Min.Z, cb.Max.Z):
                    pw = tr.OfPoint(XYZ(cx, cy, cz))
                    u, v = _uv_em_vista(pw, org, rdir, upv)
                    us.append(u)
                    vs.append(v)
        return (min(us), max(us), min(vs), max(vs))
    except Exception:
        return None


def _criar_dim_segura(v_view, dim_line, coord_ref_itens, dim_type=None, min_gap=None):
    """Cria Dimension em ViewSection filtrando automaticamente referencias incompatíveis via SubTransaction."""
    if min_gap is None:
        min_gap = 6.0 * MM
    limpos = _dedup_refs_por_coord(coord_ref_itens, min_gap=min_gap)
    if len(limpos) < 2:
        return None

    ra = ReferenceArray()
    for _, rf, _ in limpos:
        if rf is not None:
            ra.Append(rf)
    if ra.Size < 2:
        return None

    st = SubTransaction(doc)
    try:
        st.Start()
        dim = doc.Create.NewDimension(v_view, dim_line, ra)
        if dim is not None:
            if dim_type is not None:
                try:
                    dim.ChangeTypeId(dim_type.Id)
                except Exception:
                    pass
            st.Commit()
            return dim
        st.RollBack()
    except Exception:
        try:
            st.RollBack()
        except Exception:
            pass

    # Fallback resiliente: testa pares para manter apenas referencias validas e paralelas na vista
    n = len(limpos)
    validos = []
    ancora = None
    for i in range(n):
        for j in range(i + 1, n):
            ra_p = ReferenceArray()
            ra_p.Append(limpos[i][1])
            ra_p.Append(limpos[j][1])
            stp = SubTransaction(doc)
            try:
                stp.Start()
                d_ok = doc.Create.NewDimension(v_view, dim_line, ra_p)
                stp.RollBack()
                if d_ok is not None:
                    ancora = limpos[i]
                    validos = [limpos[i], limpos[j]]
                    break
            except Exception:
                try:
                    stp.RollBack()
                except Exception:
                    pass
        if ancora is not None:
            break

    if ancora is None:
        return None

    vistos = set([id(validos[0][1]), id(validos[1][1])])
    for cand in limpos:
        if id(cand[1]) in vistos:
            continue
        if abs(cand[0] - ancora[0]) < min_gap:
            continue
        ra_t = ReferenceArray()
        ra_t.Append(ancora[1])
        ra_t.Append(cand[1])
        stp = SubTransaction(doc)
        try:
            stp.Start()
            d_ok = doc.Create.NewDimension(v_view, dim_line, ra_t)
            stp.RollBack()
            if d_ok is not None:
                validos.append(cand)
                vistos.add(id(cand[1]))
        except Exception:
            try:
                stp.RollBack()
            except Exception:
                pass

    validos = _dedup_refs_por_coord(validos, min_gap=min_gap)
    if len(validos) < 2:
        return None

    ra_f = ReferenceArray()
    for _, rf, _ in validos:
        ra_f.Append(rf)
    try:
        dim_f = doc.Create.NewDimension(v_view, dim_line, ra_f)
        if dim_f is not None and dim_type is not None:
            try:
                dim_f.ChangeTypeId(dim_type.Id)
            except Exception:
                pass
        return dim_f
    except Exception:
        return None


def _faces_normais_direcao(el, v_view, axis_dir, min_dot=0.985):
    """Retorna lista de (coord_ao_longo_eixo, PlanarFace, Reference) para faces paralelas/perpendiculares ao eixo."""
    opts = Options()
    opts.ComputeReferences = True
    opts.View = v_view
    org = v_view.Origin
    res = []
    try:
        geom = el.get_Geometry(opts)
    except Exception:
        geom = None
    for fc in de._iter_faces_solidas(geom):
        if not isinstance(fc, PlanarFace):
            continue
        rf = getattr(fc, "Reference", None)
        if rf is None:
            continue
        try:
            if abs(fc.FaceNormal.DotProduct(axis_dir)) >= min_dot:
                p = fc.Origin
                coord = (p.X - org.X) * axis_dir.X + (p.Y - org.Y) * axis_dir.Y + (p.Z - org.Z) * axis_dir.Z
                res.append((coord, fc, rf))
        except Exception:
            pass
    return res


def _coletar_pisos_forros_no_corte(v_view):
    """Coleta Floors e Ceilings visiveis no corte com suas faces horizontais superior e inferior."""
    floors = []
    for fl in FilteredElementCollector(doc, v_view.Id).OfClass(Floor).WhereElementIsNotElementType().ToElements():
        faces_h = _faces_normais_direcao(fl, v_view, v_view.UpDirection)
        if not faces_h:
            continue
        tops = [it for it in faces_h if it[1].FaceNormal.Z > 0.9]
        bots = [it for it in faces_h if it[1].FaceNormal.Z < -0.9]
        top_it = max(tops, key=lambda x: x[0]) if tops else None
        bot_it = min(bots, key=lambda x: x[0]) if bots else None
        floors.append({
            "el":        fl,
            "is_struct": de._eh_piso_estrutural(fl),
            "top":       top_it,
            "bot":       bot_it,
            "faces":     faces_h,
        })

    ceilings = []
    for cl in FilteredElementCollector(doc, v_view.Id).OfClass(Ceiling).WhereElementIsNotElementType().ToElements():
        faces_h = _faces_normais_direcao(cl, v_view, v_view.UpDirection)
        if not faces_h:
            continue
        bots = [it for it in faces_h if it[1].FaceNormal.Z < -0.9]
        tops = [it for it in faces_h if it[1].FaceNormal.Z > 0.9]
        ceilings.append({
            "el":    cl,
            "bot":   min(bots, key=lambda x: x[0]) if bots else None,
            "top":   max(tops, key=lambda x: x[0]) if tops else None,
            "faces": faces_h,
        })
    return floors, ceilings


def _face_cobre_ponto_xy(face, pt_xy):
    if de._ponto_sobre_face_em_xy(face, pt_xy) is not None:
        return True
    try:
        cand = XYZ(pt_xy.X, pt_xy.Y, face.Origin.Z)
        res = face.Project(cand)
        if res is not None and face.IsInside(res.UVPoint):
            return True
    except Exception:
        pass
    return False


def _melhor_piso_base_no_ponto(pt_xy, floors, z_min_alvo, z_max_alvo, priorizar_acab=True):
    """Retorna (v_coord, Reference, el) da face superior do piso que cobre pt_xy na faixa Z alvo."""
    cands_nao = []
    cands_est = []
    for fl in floors:
        top_it = fl.get("top")
        if top_it is None:
            continue
        v_coord, fc, rf = top_it
        z_f = fc.Origin.Z
        if z_f < z_min_alvo or z_f > z_max_alvo:
            continue
        if not _face_cobre_ponto_xy(fc, pt_xy):
            continue
        if fl["is_struct"]:
            cands_est.append((z_f, v_coord, rf, fl["el"]))
        else:
            cands_nao.append((z_f, v_coord, rf, fl["el"]))

    if priorizar_acab and cands_nao:
        cands_nao.sort(key=lambda x: x[0], reverse=True)
        return cands_nao[0][1], cands_nao[0][2], cands_nao[0][3]
    todos = cands_nao + cands_est
    if todos:
        todos.sort(key=lambda x: x[0], reverse=True)
        return todos[0][1], todos[0][2], todos[0][3]
    return (None, None, None)


def _forro_no_ponto(pt_xy, ceilings, z_min_busca, z_max_busca):
    """Retorna (v_coord, Reference, z_f) da face inferior do forro mais proximo acima de z_min_busca."""
    cands = []
    for cl in ceilings:
        bot_it = cl.get("bot")
        if bot_it is None:
            continue
        v_coord, fc, rf = bot_it
        z_f = fc.Origin.Z
        if z_f < z_min_busca or z_f > z_max_busca:
            continue
        if _face_cobre_ponto_xy(fc, pt_xy):
            cands.append((z_f, v_coord, rf))
    if cands:
        cands.sort(key=lambda x: x[0])
        return (cands[0][1], cands[0][2], cands[0][0])
    return (None, None, None)


def _laje_ou_topo_acima_no_ponto(pt_xy, floors, z_min_busca, z_max_busca):
    """Retorna (v_coord, Reference, z_f) do fundo da laje / piso superior que cobre pt_xy."""
    cands = []
    for fl in floors:
        it_ref = fl.get("bot") or fl.get("top")
        if it_ref is None:
            continue
        v_coord, fc, rf = it_ref
        z_f = fc.Origin.Z
        if z_f < z_min_busca or z_f > z_max_busca:
            continue
        if _face_cobre_ponto_xy(fc, pt_xy):
            cands.append((z_f, v_coord, rf))
    if cands:
        cands.sort(key=lambda x: x[0])
        return (cands[0][1], cands[0][2], cands[0][0])
    return (None, None, None)


def _nivel_proximo(z_alvo, tol=0.45 / 0.3048):
    best = None
    best_d = tol
    org = view.Origin
    upv = view.UpDirection
    for lv in FilteredElementCollector(doc).OfClass(Level).ToElements():
        dz = abs(lv.Elevation - z_alvo)
        if dz < best_d:
            best_d = dz
            best = lv
    if best is None:
        return (None, None)
    try:
        rf = best.GetPlaneReference()
        v_coord = (best.Elevation - org.Z) * upv.Z
        return (v_coord, rf)
    except Exception:
        return (None, None)


def _refs_verticais_esquadria(fi, v_view):
    """Retorna (v_bot, rf_bot, v_top, rf_top) para Porta ou Janela no corte/elevacao."""
    org = v_view.Origin
    upv = v_view.UpDirection
    bb_info = _bb_uv_em_vista(fi, v_view)
    if bb_info is None:
        return (None, None, None, None)
    u_min, u_max, v_min, v_max, bb = bb_info

    rf_bot = None
    rf_top = None

    # Camada 1: Reference Planes nativos da FamilyInstance (Bottom / Top)
    try:
        r_b = list(fi.GetReferences(FamilyInstanceReferenceType.Bottom))
        if r_b:
            rf_bot = r_b[0]
    except Exception:
        pass
    try:
        r_t = list(fi.GetReferences(FamilyInstanceReferenceType.Top))
        if r_t:
            rf_top = r_t[0]
    except Exception:
        pass

    # Camada 2: Faces horizontais do vao na parede hospedeira (Wall)
    if (rf_bot is None or rf_top is None) and isinstance(getattr(fi, "Host", None), Wall):
        host_w = fi.Host
        for coord, fc, rf in _faces_normais_direcao(host_w, v_view, upv, min_dot=0.98):
            orig = fc.Origin
            if (bb.Min.X - 0.5 * FT <= orig.X <= bb.Max.X + 0.5 * FT and
                    bb.Min.Y - 0.5 * FT <= orig.Y <= bb.Max.Y + 0.5 * FT):
                if rf_bot is None and abs(coord - v_min) <= 0.15 * FT and fc.FaceNormal.Z > 0.9:
                    rf_bot = rf
                elif rf_top is None and abs(coord - v_max) <= 0.15 * FT and fc.FaceNormal.Z < -0.9:
                    rf_top = rf

    # Camada 3: Faces horizontais da propria geometria da esquadria
    if rf_bot is None or rf_top is None:
        faces_fi = _faces_normais_direcao(fi, v_view, upv, min_dot=0.98)
        if faces_fi:
            faces_fi.sort(key=lambda x: x[0])
            if rf_bot is None:
                rf_bot = faces_fi[0][2]
            if rf_top is None:
                rf_top = faces_fi[-1][2]

    return (v_min, rf_bot, v_max, rf_top)


def _refs_horizontais_esquadria(fi, v_view):
    """Retorna (u_left, rf_left, u_right, rf_right) para largura da esquadria em elevacao frontal."""
    rdir = v_view.RightDirection
    bb_info = _bb_uv_em_vista(fi, v_view)
    if bb_info is None:
        return (None, None, None, None)
    u_min, u_max, _, _, bb = bb_info
    if (u_max - u_min) < 0.20 / 0.3048:
        return (None, None, None, None)

    rf_l = None
    rf_r = None
    try:
        r_ls = list(fi.GetReferences(FamilyInstanceReferenceType.Left))
        r_rs = list(fi.GetReferences(FamilyInstanceReferenceType.Right))
        if r_ls and r_rs:
            rf_l = r_ls[0]
            rf_r = r_rs[0]
    except Exception:
        pass

    if (rf_l is None or rf_r is None) and isinstance(getattr(fi, "Host", None), Wall):
        faces_w = _faces_normais_direcao(fi.Host, v_view, rdir, min_dot=0.98)
        for coord, fc, rf in faces_w:
            orig = fc.Origin
            if bb.Min.Z - 0.5 * FT <= orig.Z <= bb.Max.Z + 0.5 * FT:
                if rf_l is None and abs(coord - u_min) <= 0.15 * FT:
                    rf_l = rf
                elif rf_r is None and abs(coord - u_max) <= 0.15 * FT:
                    rf_r = rf

    if rf_l is None or rf_r is None:
        faces_fi = _faces_normais_direcao(fi, v_view, rdir, min_dot=0.98)
        if faces_fi:
            faces_fi.sort(key=lambda x: x[0])
            if rf_l is None:
                rf_l = faces_fi[0][2]
            if rf_r is None:
                rf_r = faces_fi[-1][2]

    return (u_min, rf_l, u_max, rf_r)


# ==============================================================================
# 1. COTA RAPIDA NO CORTE (POR LINHA — 2 CLIQUES)
# ==============================================================================

def _build_marker_section(pt, rdir, upv, with_ring):
    ring_r   = 0.12 / 0.3048
    tick_in  = 0.07 / 0.3048
    tick_out = 0.20 / 0.3048
    curves = []
    if with_ring:
        curves.append(Arc.Create(
            pt + rdir * ring_r, pt - rdir * ring_r, pt + upv * ring_r))
        curves.append(Arc.Create(
            pt + rdir * ring_r, pt - rdir * ring_r, pt - upv * ring_r))
        a, b = tick_in, tick_out
    else:
        a, b = 0.0, tick_out

    for d in (rdir, upv):
        curves.append(Line.CreateBound(pt + d * a, pt + d * b))
        curves.append(Line.CreateBound(pt - d * a, pt - d * b))
    return curves


def _criar_marcador_secao(pt, rdir, upv, with_ring=True):
    ids = []
    ogs = OverrideGraphicSettings()
    ogs.SetProjectionLineColor(Color(255, 95, 0))
    ogs.SetProjectionLineWeight(3)
    for crv in _build_marker_section(pt, rdir, upv, with_ring):
        dc = doc.Create.NewDetailCurve(view, crv)
        view.SetElementOverrides(dc.Id, ogs)
        ids.append(dc.Id)
    return ids


def _edges_horizontais_telhado_no_u(roof, v_view, u_alvo, z_min, z_max, u_tol):
    """Coleta Edge.Reference horizontais de telhados solidos proximos ao u_alvo e dentro [z_min, z_max]."""
    org  = v_view.Origin
    rdir = v_view.RightDirection
    upv  = v_view.UpDirection
    opts = Options()
    opts.ComputeReferences = True
    opts.View = v_view
    res = []
    try:
        geom = roof.get_Geometry(opts)
    except Exception:
        return res
    for obj in (geom or []):
        sols = [obj] if isinstance(obj, Solid) else []
        for s in sols:
            if s.Volume <= 1e-6:
                continue
            for ed in s.Edges:
                rf = getattr(ed, "Reference", None)
                if rf is None:
                    continue
                try:
                    crv = ed.AsCurve()
                    p0 = crv.GetEndPoint(0)
                    p1 = crv.GetEndPoint(1)
                    if abs(p1.Z - p0.Z) > 0.02 * FT:
                        continue
                    u0, z0 = _uv_em_vista(p0, org, rdir, upv)
                    u1, z1 = _uv_em_vista(p1, org, rdir, upv)
                    if z_min <= 0.5 * (z0 + z1) <= z_max:
                        lo_u, hi_u = min(u0, u1), max(u0, u1)
                        if (lo_u - u_tol) <= u_alvo <= (hi_u + u_tol):
                            res.append((0.5 * (z0 + z1), rf, "RoofEdge"))
                except Exception:
                    pass
    return res


def _extrair_refs_geom_cobertura_vertical(geom_el, tag, org, rdir, upv, u_alvo=None, u_tol=0.0, z_min=-1e9, z_max=1e9):
    """Extrai faces/edges horizontais e extremidades de arestas inclinadas de uma geometria no plano (u, z)."""
    res = []
    if geom_el is None:
        return res
    for obj in geom_el:
        if isinstance(obj, Solid) and obj.Volume > 1e-7:
            s = obj
            for fc in s.Faces:
                if isinstance(fc, PlanarFace) and getattr(fc, "Reference", None) is not None:
                    if abs(fc.FaceNormal.DotProduct(upv)) >= 0.985:
                        p = fc.Origin
                        _, zc = _uv_em_vista(p, org, rdir, upv)
                        if z_min <= zc <= z_max and (u_alvo is None or _face_cobre_ponto_xy(fc, org + rdir * u_alvo)):
                            res.append((zc, fc.Reference, tag + "Face"))
            for ed in s.Edges:
                rf_ed = getattr(ed, "Reference", None)
                try:
                    crv = ed.AsCurve()
                    p0 = crv.GetEndPoint(0)
                    p1 = crv.GetEndPoint(1)
                    u0, z0 = _uv_em_vista(p0, org, rdir, upv)
                    u1, z1 = _uv_em_vista(p1, org, rdir, upv)
                    if abs(z1 - z0) <= 0.02 * FT:
                        zm = 0.5 * (z0 + z1)
                        lo_u, hi_u = min(u0, u1), max(u0, u1)
                        if rf_ed is not None and z_min <= zm <= z_max and (u_alvo is None or (lo_u - u_tol) <= u_alvo <= (hi_u + u_tol)):
                            res.append((zm, rf_ed, tag + "Edge"))
                    else:
                        lo_u, hi_u = min(u0, u1), max(u0, u1)
                        if u_alvo is None or (lo_u - 0.05 * FT) <= u_alvo <= (hi_u + 0.05 * FT):
                            for idx_ep, (ue, ze) in ((0, (u0, z0)), (1, (u1, z1))):
                                if (u_alvo is None or abs(ue - u_alvo) <= max(u_tol, 0.35 * FT)) and z_min <= ze <= z_max:
                                    try:
                                        rf_pt = ed.GetEndPointReference(idx_ep)
                                        if rf_pt is not None:
                                            res.append((ze, rf_pt, tag + "Pt"))
                                    except Exception:
                                        pass
                except Exception:
                    pass
        elif isinstance(obj, GeometryInstance):
            try:
                res.extend(_extrair_refs_geom_cobertura_vertical(
                    obj.GetInstanceGeometry(), tag, org, rdir, upv, u_alvo, u_tol, z_min, z_max
                ))
            except Exception:
                pass
    return res


def _coletar_refs_cobertura_vertical(v_view, u_alvo, u_tol, z_min, z_max, incluir_madeira=True):
    """
    Coleta referencias verticais validas para telhados (planos ou inclinados) e madeiramento
    (caibros/tercas, ignorando ripas) proximos ao u_alvo dentro da faixa [z_min, z_max].
    Usa faces horizontais, arestas horizontais e vertices de extremidade de arestas inclinadas.
    """
    org  = v_view.Origin
    rdir = v_view.RightDirection
    upv  = v_view.UpDirection
    opts = Options()
    opts.ComputeReferences = True
    opts.View = v_view
    try:
        opts.DetailLevel = ViewDetailLevel.Fine
    except Exception:
        pass

    res = []

    for roof in FilteredElementCollector(doc, v_view.Id).OfClass(RoofBase).WhereElementIsNotElementType().ToElements():
        if de._eh_vidraca_inclinada(roof):
            continue
        bb_uv = _bb_uv_em_vista(roof, v_view)
        if bb_uv is None or bb_uv[1] < u_alvo - u_tol - 0.5 * FT or bb_uv[0] > u_alvo + u_tol + 0.5 * FT:
            continue
        if bb_uv[3] < z_min - 0.5 * FT or bb_uv[2] > z_max + 0.5 * FT:
            continue
        try:
            res.extend(_extrair_refs_geom_cobertura_vertical(
                roof.get_Geometry(opts), "Roof", org, rdir, upv, u_alvo, u_tol, z_min, z_max
            ))
        except Exception:
            pass

    if incluir_madeira:
        for bic in (BuiltInCategory.OST_StructuralFraming, BuiltInCategory.OST_CurtainWallMullions):
            for el in FilteredElementCollector(doc, v_view.Id).OfCategory(bic).WhereElementIsNotElementType().ToElements():
                try:
                    pc = el.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
                    if pc is not None and pc.AsString() and u"ripa" in unicode(pc.AsString()).lower():
                        continue
                except Exception:
                    pass
                bb_uv = _bb_uv_em_vista(el, v_view)
                if bb_uv is None or bb_uv[1] < u_alvo - u_tol or bb_uv[0] > u_alvo + u_tol:
                    continue
                if bb_uv[3] < z_min - 0.5 * FT or bb_uv[2] > z_max + 0.5 * FT:
                    continue
                try:
                    res.extend(_extrair_refs_geom_cobertura_vertical(
                        el.get_Geometry(opts), "Mad", org, rdir, upv, u_alvo, u_tol, z_min, z_max
                    ))
                except Exception:
                    pass

    return res


def _mesclar_camadas_piso(camadas, tol_gap=0.164, tol_u=2.62):
    """Agrupa camadas contiguas de piso/laje (sem depender da flag Estrutural) e retorna apenas base e topo finais."""
    itens = []
    for zb, rfb, zt, rft, uq in camadas:
        if zb is None and zt is None:
            continue
        z_lo = zb if zb is not None else zt
        z_hi = zt if zt is not None else zb
        itens.append((z_lo, rfb if zb is not None else None, z_hi, rft if zt is not None else None, float(uq)))

    itens.sort(key=lambda x: x[0])
    clusters = []
    for z_lo, rfb, z_hi, rft, uq in itens:
        fundido = False
        for cl in clusters:
            if abs(uq - cl[4]) <= tol_u and z_lo <= cl[2] + tol_gap and z_hi >= cl[0] - tol_gap:
                if rfb is not None and (cl[1] is None or z_lo < cl[0]):
                    cl[0] = z_lo
                    cl[1] = rfb
                elif z_lo < cl[0]:
                    cl[0] = z_lo
                if rft is not None and (cl[3] is None or z_hi > cl[2]):
                    cl[2] = z_hi
                    cl[3] = rft
                elif z_hi > cl[2]:
                    cl[2] = z_hi
                fundido = True
                break
        if not fundido:
            clusters.append([z_lo, rfb, z_hi, rft, uq])

    res = []
    for cl_zb, cl_rfb, cl_zt, cl_rft, _ in clusters:
        if cl_rfb is not None:
            res.append((cl_zb, cl_rfb, "LajeBase"))
        if cl_rft is not None:
            res.append((cl_zt, cl_rft, "PisoAcabTop"))
    return res


def _parede_corta_plano_secao(wall, v_view, tol_depth=None):
    """Retorna True se a linha da parede realmente cruza ou toca o plano de corte (ignora paredes ao fundo)."""
    if tol_depth is None:
        tol_depth = 0.25 / 0.3048
    try:
        loc = getattr(wall, "Location", None)
        crv = getattr(loc, "Curve", None) if loc is not None else None
        if crv is None:
            return False
        org  = v_view.Origin
        vdir = v_view.ViewDirection
        pts = list(crv.Tessellate())
        if len(pts) < 2:
            pts = [crv.GetEndPoint(0), crv.GetEndPoint(1)]
        ds = [(p.X - org.X) * vdir.X + (p.Y - org.Y) * vdir.Y for p in pts]
        return (min(ds) <= tol_depth and max(ds) >= -tol_depth)
    except Exception:
        return False


def run_cotar_corte_linha():
    _verificar_view_section(u"Cota Rápida no Corte")

    org  = view.Origin
    rdir = view.RightDirection
    upv  = view.UpDirection

    tg = TransactionGroup(doc, u"Cota Rápida no Corte")
    tg.Start()

    if view.SketchPlane is None:
        with Transaction(doc, u"Definir Plano do Corte") as tsp:
            tsp.Start()
            _garantir_sketch_plane_secao(view)
            tsp.Commit()

    marker_ids = []
    pt1 = None
    pt2 = None
    cancelado = False

    try:
        pt1 = uidoc.Selection.PickPoint(
            SNAP_NENHUM,
            u"Clique no 1º ponto da cota no Corte (vertical ou horizontal)"
        )
        for anel in (True, False):
            try:
                with Transaction(doc, u"Marcador Temporário") as tmk:
                    tmk.Start()
                    marker_ids = _criar_marcador_secao(pt1, rdir, upv, with_ring=anel)
                    tmk.Commit()
                uidoc.RefreshActiveView()
                break
            except Exception:
                marker_ids = []

        pt2 = uidoc.Selection.PickPoint(
            SNAP_NENHUM,
            u"Clique no 2º ponto para criar a cadeia de cotas no Corte"
        )
    except Exception:
        cancelado = True
    finally:
        if marker_ids:
            try:
                with Transaction(doc, u"Remover Marcador") as trm:
                    trm.Start()
                    for eid in marker_ids:
                        if doc.GetElement(eid) is not None:
                            doc.Delete(eid)
                    trm.Commit()
            except Exception:
                pass

    if cancelado or pt1 is None or pt2 is None:
        tg.RollBack()
        script.exit()

    u1, v1 = _uv_em_vista(pt1, org, rdir, upv)
    u2, v2 = _uv_em_vista(pt2, org, rdir, upv)
    du = abs(u2 - u1)
    dv = abs(v2 - v1)
    if max(du, dv) < 0.05 / 0.3048:
        tg.RollBack()
        forms.alert(u"Os dois pontos clicados estão muito próximos.", title="Aviso")
        script.exit()

    eh_vertical = (dv >= du)
    refs_cands = []
    dim_line = None

    if eh_vertical:
        v_lo, v_hi = min(v1, v2), max(v1, v2)
        u_line = 0.5 * (u1 + u2)
        dim_line = Line.CreateBound(
            org + rdir * u_line + upv * v_lo,
            org + rdir * u_line + upv * v_hi
        )

        def _u_na_altura(z_coord):
            if dv < 1e-6:
                return u_line
            t = (z_coord - v1) / float(v2 - v1)
            t = max(0.0, min(1.0, t))
            return u1 + t * (u2 - u1)

        # Pre-coleta paredes cortadas (usada tanto para checar se o clique caiu sobre parede quanto para topo)
        paredes_cortadas = []
        for wl in FilteredElementCollector(doc, view.Id).OfClass(Wall).WhereElementIsNotElementType().ToElements():
            if not _parede_corta_plano_secao(wl, view):
                continue
            bb_uv = _bb_uv_em_vista(wl, view)
            if bb_uv is None:
                continue
            paredes_cortadas.append((wl, bb_uv))

        def _clique_sobre_parede_em_u(u_test):
            for _, bb_uv in paredes_cortadas:
                if bb_uv[0] - 0.03 * FT <= u_test <= bb_uv[1] + 0.03 * FT:
                    return True
            return False

        # Pre-coleta Pisos/Lajes e Forros potencialmente cruzados pela faixa vertical
        pisos_info = []
        for fl in FilteredElementCollector(doc, view.Id).OfClass(Floor).WhereElementIsNotElementType().ToElements():
            bb_uv = _bb_uv_em_vista(fl, view)
            if bb_uv is None or bb_uv[3] < v_lo - 1.0 * FT or bb_uv[2] > v_hi + 1.0 * FT:
                continue
            faces_h = _faces_normais_direcao(fl, view, upv, min_dot=0.985)
            if faces_h:
                pisos_info.append((fl, bb_uv, faces_h))

        forros_info = []
        for cl in FilteredElementCollector(doc, view.Id).OfClass(Ceiling).WhereElementIsNotElementType().ToElements():
            bb_uv = _bb_uv_em_vista(cl, view)
            if bb_uv is None or bb_uv[3] < v_lo - 0.2 * FT or bb_uv[2] > v_hi + 0.2 * FT:
                continue
            bots_f = [it for it in _faces_normais_direcao(cl, view, upv, min_dot=0.985) if it[1].FaceNormal.Z < -0.9]
            if bots_f:
                forros_info.append((cl, bb_uv, bots_f))

        def _hits_piso_forro_no_offset(u_off):
            pisos_hit = {}
            forros_hit = {}
            tem_nao_est = False
            for fl, _, faces_h in pisos_info:
                tops_f = []
                bots_f = []
                u_ref_fl = None
                for vc, fc, rf in faces_h:
                    uq = _u_na_altura(vc) + u_off
                    if _face_cobre_ponto_xy(fc, org + rdir * uq):
                        u_ref_fl = uq
                        if fc.FaceNormal.Z > 0.9:
                            tops_f.append((vc, rf))
                        elif fc.FaceNormal.Z < -0.9:
                            bots_f.append((vc, rf))
                if tops_f or bots_f:
                    zb, rfb = min(bots_f, key=lambda x: x[0]) if bots_f else (None, None)
                    zt, rft = max(tops_f, key=lambda x: x[0]) if tops_f else (None, None)
                    pisos_hit[_eid_int(fl.Id)] = (zb, rfb, zt, rft, u_ref_fl)
                    if not de._eh_piso_estrutural(fl):
                        tem_nao_est = True

            for cl, _, bots_f in forros_info:
                for_cands = []
                for vc, fc, rf in bots_f:
                    if v_lo <= vc <= v_hi and _face_cobre_ponto_xy(fc, org + rdir * (_u_na_altura(vc) + u_off)):
                        for_cands.append((vc, rf))
                if for_cands:
                    vc_min, rf_min = min(for_cands, key=lambda x: x[0])
                    forros_hit[_eid_int(cl.Id)] = (vc_min, rf_min, "ForroBot")
            return pisos_hit, forros_hit, tem_nao_est

        # 1) Pega pisos e forros exatamente na linha clicada; se clicou fora da fachada ou sobre parede, busca do lado interno mais proximo
        pisos_0, forros_0, _ = _hits_piso_forro_no_offset(0.0)
        pisos_merged_dict = dict(pisos_0)
        forros_merged_dict = dict(forros_0)

        sobre_parede = _clique_sobre_parede_em_u(_u_na_altura(0.5 * (v_lo + v_hi)))
        if (not pisos_0 and not forros_0) or sobre_parede:
            passos = (0.20 * FT, 0.40 * FT, 0.65 * FT)
            lado_escolhido = None
            for d_off in passos:
                melhor_lado = None
                melhor_score = 0
                for sgn in (-1.0, 1.0):
                    ph, fh, has_acab = _hits_piso_forro_no_offset(sgn * d_off)
                    score = (2 if fh else 0) + (2 if has_acab else 0) + (1 if ph else 0)
                    if score > melhor_score:
                        melhor_score = score
                        melhor_lado = sgn
                if melhor_lado is not None and melhor_score >= 2:
                    lado_escolhido = melhor_lado
                    break
            if lado_escolhido is None:
                for d_off in passos:
                    for sgn in (-1.0, 1.0):
                        ph, fh, _ = _hits_piso_forro_no_offset(sgn * d_off)
                        if ph or fh:
                            lado_escolhido = sgn
                            break
                    if lado_escolhido is not None:
                        break
            if lado_escolhido is not None:
                for d_off in passos:
                    ph, fh, _ = _hits_piso_forro_no_offset(lado_escolhido * d_off)
                    for k, v in ph.items():
                        if k not in pisos_merged_dict:
                            pisos_merged_dict[k] = v
                    for k, v in fh.items():
                        if k not in forros_merged_dict:
                            forros_merged_dict[k] = v
                    if pisos_merged_dict and forros_merged_dict:
                        break

        for vc, rf, tag in _mesclar_camadas_piso(pisos_merged_dict.values(), tol_gap=0.05 * FT, tol_u=0.80 * FT):
            if v_lo <= vc <= v_hi:
                refs_cands.append((vc, rf, tag))

        # 2) Forros: apenas face inferior (evita espessura de 1cm do gesso)
        for vc, rf, tag in forros_merged_dict.values():
            if v_lo <= vc <= v_hi:
                refs_cands.append((vc, rf, tag))

        # 3) Paredes cortadas pelo plano do corte: pega APENAS o topo mais alto (nunca peitoris internos de janelas)
        wall_tol_pref = 1.00 * FT
        wall_tol_max  = 2.50 * FT
        wall_top_cands = []
        for wl, bb_uv in paredes_cortadas:
            u_el_lo, u_el_hi, v_el_lo, v_el_hi, _ = bb_uv
            if v_el_hi < v_lo - 0.1 * FT or v_el_lo > v_hi + 0.1 * FT:
                continue
            tops_w = [it for it in _faces_normais_direcao(wl, view, upv, min_dot=0.985) if it[1].FaceNormal.Z > 0.9]
            if not tops_w:
                continue
            vc, _, rf = max(tops_w, key=lambda x: x[0])
            if not (v_lo <= vc <= v_hi):
                continue
            u_q = _u_na_altura(vc)
            if u_el_lo <= u_q <= u_el_hi:
                dist_u = 0.0
            else:
                dist_u = min(abs(u_q - u_el_lo), abs(u_q - u_el_hi))
            if dist_u <= wall_tol_max:
                wall_top_cands.append((dist_u, vc, rf))

        if wall_top_cands:
            proximas = [(vc, rf) for d_u, vc, rf in wall_top_cands if d_u <= wall_tol_pref]
            if not proximas:
                min_d = min(it[0] for it in wall_top_cands)
                proximas = [(vc, rf) for d_u, vc, rf in wall_top_cands if d_u <= min_d + 0.15 * FT]
            for vc, rf in proximas:
                refs_cands.append((vc, rf, "WallTop"))

        # 4) Vigas estruturais efetivamente cruzadas no corte
        for bm in FilteredElementCollector(doc, view.Id).OfCategory(BuiltInCategory.OST_StructuralFraming).WhereElementIsNotElementType().ToElements():
            bb_uv = _bb_uv_em_vista(bm, view)
            if bb_uv is None or bb_uv[3] < v_lo or bb_uv[2] > v_hi:
                continue
            for vc, fc, rf in _faces_normais_direcao(bm, view, upv, min_dot=0.985):
                if v_lo <= vc <= v_hi and _face_cobre_ponto_xy(fc, org + rdir * _u_na_altura(vc)):
                    refs_cands.append((vc, rf, "BeamH"))

        # 5) Cobertura (Telhados planos ou inclinados) e Madeiramento (Caibros/Tercas) cruzados no u medio
        u_mid_v = 0.5 * (u1 + u2)
        u_tol_cob = max(0.35 * FT, 0.5 * du + 0.15 * FT)
        for zc, rfc, tagc in _coletar_refs_cobertura_vertical(view, u_mid_v, u_tol_cob, v_lo, v_hi, incluir_madeira=True):
            if v_lo <= zc <= v_hi:
                refs_cands.append((zc, rfc, tagc))

        # 6) Portas e Janelas cruzadas verticalmente pela faixa da linha (peitoril e verga)
        u_esq_lo, u_esq_hi = min(u1, u2) - 0.15 * FT, max(u1, u2) + 0.15 * FT
        for cat_e in (BuiltInCategory.OST_Windows, BuiltInCategory.OST_Doors):
            for fi in FilteredElementCollector(doc, view.Id).OfCategory(cat_e).WhereElementIsNotElementType().ToElements():
                if not isinstance(fi, FamilyInstance) or getattr(fi, "SuperComponent", None) is not None:
                    continue
                bb_uv = _bb_uv_em_vista(fi, view)
                if bb_uv is None:
                    continue
                if bb_uv[1] < u_esq_lo or bb_uv[0] > u_esq_hi or bb_uv[3] < v_lo or bb_uv[2] > v_hi:
                    continue
                vb, rfb, vt, rft = _refs_verticais_esquadria(fi, view)
                if rfb is not None and v_lo <= vb <= v_hi:
                    refs_cands.append((vb, rfb, "EsqBot"))
                if rft is not None and v_lo <= vt <= v_hi:
                    refs_cands.append((vt, rft, "EsqTop"))

        # 7) Se ainda faltarem referencias de base (ex.: linha cruzou nivel sem piso), inclui Levels dentro do trecho
        if len(refs_cands) < 2:
            for lv in FilteredElementCollector(doc).OfClass(Level).ToElements():
                try:
                    vc_lv = (lv.Elevation - org.Z) * upv.Z
                    if v_lo <= vc_lv <= v_hi:
                        rf_lv = lv.GetPlaneReference()
                        if rf_lv is not None:
                            refs_cands.append((vc_lv, rf_lv, "Level"))
                except Exception:
                    pass

    else:
        u_lo, u_hi = min(u1, u2), max(u1, u2)
        v_line = 0.5 * (v1 + v2)
        band_v = max(0.25 * FT, 0.5 * dv + 0.10 * FT)
        dim_line = Line.CreateBound(
            org + rdir * u_lo + upv * v_line,
            org + rdir * u_hi + upv * v_line
        )

        for el in FilteredElementCollector(doc, view.Id).OfClass(Wall).WhereElementIsNotElementType().ToElements():
            if not _parede_corta_plano_secao(el, view):
                continue
            bb_uv = _bb_uv_em_vista(el, view)
            if bb_uv is None:
                continue
            u_el_lo, u_el_hi, v_el_lo, v_el_hi, _ = bb_uv
            if u_el_hi < u_lo - 0.1 * FT or u_el_lo > u_hi + 0.1 * FT:
                continue
            if not _filtrar_refs_por_intervalo_u(v_el_lo, v_el_hi, v_line, tol=band_v):
                continue
            for uc, fc, rf in _faces_normais_direcao(el, view, rdir, min_dot=0.985):
                if u_lo <= uc <= u_hi:
                    refs_cands.append((uc, rf, "WallV"))

        for cat in (BuiltInCategory.OST_StructuralColumns, BuiltInCategory.OST_StructuralFraming):
            for el in FilteredElementCollector(doc, view.Id).OfCategory(cat).WhereElementIsNotElementType().ToElements():
                bb_uv = _bb_uv_em_vista(el, view)
                if bb_uv is None:
                    continue
                u_el_lo, u_el_hi, v_el_lo, v_el_hi, _ = bb_uv
                if u_el_hi < u_lo or u_el_lo > u_hi:
                    continue
                if not _filtrar_refs_por_intervalo_u(v_el_lo, v_el_hi, v_line, tol=band_v):
                    continue
                for uc, fc, rf in _faces_normais_direcao(el, view, rdir, min_dot=0.985):
                    if u_lo <= uc <= u_hi:
                        refs_cands.append((uc, rf, "ColV"))

        # Portas e Janelas cruzadas na altura da linha horizontal (ombreiras E/D)
        for cat_e in (BuiltInCategory.OST_Windows, BuiltInCategory.OST_Doors):
            for fi in FilteredElementCollector(doc, view.Id).OfCategory(cat_e).WhereElementIsNotElementType().ToElements():
                if not isinstance(fi, FamilyInstance) or getattr(fi, "SuperComponent", None) is not None:
                    continue
                bb_uv = _bb_uv_em_vista(fi, view)
                if bb_uv is None:
                    continue
                u_el_lo, u_el_hi, v_el_lo, v_el_hi, _ = bb_uv
                if u_el_hi < u_lo or u_el_lo > u_hi or not _filtrar_refs_por_intervalo_u(v_el_lo, v_el_hi, v_line, tol=band_v):
                    continue
                ul, rfl, ur, rfr = _refs_horizontais_esquadria(fi, view)
                if rfl is not None and u_lo <= ul <= u_hi:
                    refs_cands.append((ul, rfl, "EsqL"))
                if rfr is not None and u_lo <= ur <= u_hi:
                    refs_cands.append((ur, rfr, "EsqR"))

    dim_criada = None
    with Transaction(doc, u"Criar Cota no Corte") as td:
        td.Start()
        dim_criada = _criar_dim_segura(view, dim_line, refs_cands, min_gap=15.0 * MM)
        td.Commit()

    if dim_criada is not None:
        tg.Assimilate()
    else:
        tg.RollBack()
        forms.alert(
            u"Não foram encontradas pelo menos 2 faces paralelas válidas cruzadas pela linha traçada.\n"
            u"Tente cruzar claramente pisos, lajes, forros ou paredes cortadas.",
            title="Cota Rápida no Corte"
        )


# ==============================================================================
# 2. PE-DIREITO AUTOMATICO POR AMBIENTE NO CORTE
# ==============================================================================

XAML_PE_DIREITO = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Pé-Direito Automático no Corte" Width="510" SizeToContent="Height"
        WindowStartupLocation="CenterScreen" ResizeMode="NoResize" Background="#F7F8FA">
    <StackPanel Margin="14">
        <Border Background="#EBF3FC" BorderBrush="#B8D4F5" BorderThickness="1" CornerRadius="4" Padding="9" Margin="0,0,0,10">
            <TextBlock TextWrapping="Wrap" FontWeight="SemiBold" Foreground="#1F3A57"
                       Text="Identifica os Ambientes (Rooms) cortados na vista ativa e insere cotas verticais automáticas de pé-direito (Piso acabado → Forro → Laje / Topo)."/>
        </Border>

        <Border Background="White" BorderBrush="#D0D7DE" BorderThickness="1" CornerRadius="4" Padding="10" Margin="0,0,0,8">
            <Grid>
                <Grid.ColumnDefinitions>
                    <ColumnDefinition Width="195"/>
                    <ColumnDefinition Width="*"/>
                </Grid.ColumnDefinitions>
                <Grid.RowDefinitions>
                    <RowDefinition Height="28"/>
                    <RowDefinition Height="28"/>
                    <RowDefinition Height="28"/>
                </Grid.RowDefinitions>

                <TextBlock Grid.Row="0" Grid.Column="0" Text="Ambientes a cotar:" VerticalAlignment="Center"/>
                <ComboBox  Grid.Row="0" Grid.Column="1" x:Name="cb_escopo" Height="24"/>

                <TextBlock Grid.Row="1" Grid.Column="0" Text="Posição horizontal da cota:" VerticalAlignment="Center"/>
                <ComboBox  Grid.Row="1" Grid.Column="1" x:Name="cb_pos" Height="24"/>

                <TextBlock Grid.Row="2" Grid.Column="0" Text="Tipo de cota linear:" VerticalAlignment="Center"/>
                <ComboBox  Grid.Row="2" Grid.Column="1" x:Name="cb_tipo" Height="24"/>
            </Grid>
        </Border>

        <Border Background="White" BorderBrush="#D0D7DE" BorderThickness="1" CornerRadius="4" Padding="10" Margin="0,0,0,10">
            <StackPanel>
                <TextBlock Text="Segmentos e Comportamento:" FontWeight="SemiBold" Margin="0,0,0,6"/>
                <CheckBox x:Name="ck_piso_forro"  Content="Cota de pé-direito útil (Piso acabado → Forro)" IsChecked="True" Margin="0,0,0,5"/>
                <CheckBox x:Name="ck_forro_laje"  Content="Incluir trecho Forro → Laje superior (quando houver laje acima)" IsChecked="True" Margin="0,0,0,5"/>
                <CheckBox x:Name="ck_cota_total"  Content="Adicionar cota paralela de altura total (Piso → Laje superior)" IsChecked="False" Margin="0,0,0,5"/>
                <CheckBox x:Name="ck_prio_acab"   Content="Priorizar topo do Piso acabado sobre a laje estrutural" IsChecked="True" Margin="0,0,0,5"/>
                <CheckBox x:Name="ck_usar_level"  Content="Usar Nível (Level) como referência se faltar Piso no ponto" IsChecked="True"/>
            </StackPanel>
        </Border>

        <StackPanel Orientation="Horizontal" HorizontalAlignment="Right">
            <Button x:Name="btn_cancel" Content="Cancelar" Width="95" Height="30" Margin="0,0,8,0"/>
            <Button x:Name="btn_ok" Content="Cotar Pé-Direito" Width="165" Height="30"
                    IsDefault="True" Background="#0078D4" Foreground="White" FontWeight="SemiBold"/>
        </StackPanel>
    </StackPanel>
</Window>
"""


class CotarPeDireitoWindow(forms.WPFWindow):
    def __init__(self, prefs, dim_types):
        forms.WPFWindow.__init__(self, XAML_PE_DIREITO, literal_string=True)
        self.confirmado = False
        self.resultado  = {}
        self._dim_types = dim_types

        self._cb_esc   = self.FindName("cb_escopo")
        self._cb_pos   = self.FindName("cb_pos")
        self._cb_tipo  = self.FindName("cb_tipo")
        self._ck_pf    = self.FindName("ck_piso_forro")
        self._ck_fl    = self.FindName("ck_forro_laje")
        self._ck_tot   = self.FindName("ck_cota_total")
        self._ck_acab  = self.FindName("ck_prio_acab")
        self._ck_lvl   = self.FindName("ck_usar_level")

        for s in (
            u"Todos os Ambientes cortados na vista ativa",
            u"Somente Ambientes selecionados (ou pré-seleção)",
        ):
            self._cb_esc.Items.Add(s)
        self._cb_esc.SelectedIndex = max(0, min(1, int(prefs.get("pd_escopo", 0) or 0)))

        for p in (
            u"Deslocada do centro (não sobrepõe Tag / Cota de Nível)",
            u"Centro exato do Ambiente",
            u"Próxima à parede esquerda do Ambiente",
            u"Próxima à parede direita do Ambiente",
        ):
            self._cb_pos.Items.Add(p)
        self._cb_pos.SelectedIndex = max(0, min(3, int(prefs.get("pd_pos", 0) or 0)))

        nomes_dt = [de._elem_name(dt) for dt in dim_types]
        idx_dt_ini = max(0, min(max(0, len(dim_types) - 1), int(prefs.get("pd_tipo", 0) or 0)))
        self._sc_tipo = uis.SearchableCombo.attach_auto(
            self._cb_tipo, nomes_dt, placeholder=u"Pesquisar tipo de cota...", initial_index=idx_dt_ini
        )

        self._ck_pf.IsChecked   = bool(prefs.get("pd_pf", True))
        self._ck_fl.IsChecked   = bool(prefs.get("pd_fl", True))
        self._ck_tot.IsChecked  = bool(prefs.get("pd_tot", False))
        self._ck_acab.IsChecked = bool(prefs.get("pd_acab", True))
        self._ck_lvl.IsChecked  = bool(prefs.get("pd_lvl", True))

        self.FindName("btn_ok").Click     += self.on_ok
        self.FindName("btn_cancel").Click += self.on_cancel

    def on_cancel(self, sender, args):
        self.confirmado = False
        self.Close()

    def on_ok(self, sender, args):
        if not (self._ck_pf.IsChecked or self._ck_fl.IsChecked or self._ck_tot.IsChecked):
            forms.alert("Marque ao menos uma opção de cota vertical.", title="Aviso")
            return
        idx_t = max(0, self._sc_tipo.selected_index())
        self.resultado = {
            "pd_escopo": max(0, self._cb_esc.SelectedIndex),
            "pd_pos":    max(0, self._cb_pos.SelectedIndex),
            "pd_tipo":   idx_t,
            "pd_pf":     bool(self._ck_pf.IsChecked),
            "pd_fl":     bool(self._ck_fl.IsChecked),
            "pd_tot":    bool(self._ck_tot.IsChecked),
            "pd_acab":   bool(self._ck_acab.IsChecked),
            "pd_lvl":    bool(self._ck_lvl.IsChecked),
            "dim_type":  self._dim_types[idx_t] if self._dim_types else None,
        }
        self.confirmado = True
        self.Close()


def _resolver_opcoes_pe_direito(prefs, dim_types, abrir_config):
    if abrir_config:
        win = CotarPeDireitoWindow(prefs, dim_types)
        win.ShowDialog()
        if not win.confirmado:
            script.exit()
        op = win.resultado
        _salvar_prefs(dict((k, v) for k, v in op.items() if k != "dim_type"))
        return op

    idx_t = max(0, min(max(0, len(dim_types) - 1), int(prefs.get("pd_tipo", 0) or 0)))
    return {
        "pd_escopo": max(0, min(1, int(prefs.get("pd_escopo", 0) or 0))),
        "pd_pos":    max(0, min(3, int(prefs.get("pd_pos", 0) or 0))),
        "pd_tipo":   idx_t,
        "pd_pf":     bool(prefs.get("pd_pf", True)),
        "pd_fl":     bool(prefs.get("pd_fl", True)),
        "pd_tot":    bool(prefs.get("pd_tot", False)),
        "pd_acab":   bool(prefs.get("pd_acab", True)),
        "pd_lvl":    bool(prefs.get("pd_lvl", True)),
        "dim_type":  dim_types[idx_t] if dim_types else None,
    }


def _vaos_sem_room_no_corte(v_view):
    """Detecta vaos internos entre paredes cortadas na vista caso nao existam Rooms modelados."""
    org  = v_view.Origin
    rdir = v_view.RightDirection
    upv  = v_view.UpDirection
    crop_uv = _crop_uv_em_vista(v_view)

    iv_walls = []
    v_mins = []
    v_maxs = []
    for wl in FilteredElementCollector(doc, v_view.Id).OfClass(Wall).WhereElementIsNotElementType().ToElements():
        if not _parede_corta_plano_secao(wl, v_view):
            continue
        bb_uv = _bb_uv_em_vista(wl, v_view)
        if bb_uv is None:
            continue
        u0, u1, v0, v1, _ = bb_uv
        if crop_uv is not None:
            if u1 < crop_uv[0] or u0 > crop_uv[1] or v1 < crop_uv[2] or v0 > crop_uv[3]:
                continue
        iv_walls.append((u0, u1))
        v_mins.append(v0)
        v_maxs.append(v1)

    vaos = _extrair_vaos_entre_intervalos_u(iv_walls)
    if not vaos:
        return []

    z_min_g = org.Z + (min(v_mins) if v_mins else 0.0) * upv.Z
    z_max_g = org.Z + (max(v_maxs) if v_maxs else 3.0 * FT) * upv.Z
    vx, vy = v_view.ViewDirection.X, v_view.ViewDirection.Y
    rx, ry = rdir.X, rdir.Y

    res = []
    for ua, ub in vaos:
        res.append({
            "room":    None,
            "u_a":     ua,
            "u_b":     ub,
            "u_mid":   0.5 * (ua + ub),
            "z_min":   z_min_g,
            "z_max":   z_max_g,
            "z_probe": 0.5 * (z_min_g + z_max_g),
            "org":     org,
            "rx":      rx,
            "ry":      ry,
            "vx":      vx,
            "vy":      vy,
            "t_v":     0.0,
        })
    return res


def _executar_cotas_pe_direito(op, floors=None, ceilings=None, rooms_pre_filtro=None):
    rooms_corte = de._rooms_cortados_no_corte(view)
    sel_ids = set(str(eid) for eid in uidoc.Selection.GetElementIds())
    for eid in list(uidoc.Selection.GetElementIds()):
        el = doc.GetElement(eid)
        if isinstance(el, RoomTag) and el.Room is not None:
            sel_ids.add(str(el.Room.Id))

    if rooms_pre_filtro is not None:
        rooms_corte = rooms_pre_filtro
    elif sel_ids and op.get("pd_escopo", 0) == 1:
        rooms_corte = [rc for rc in rooms_corte if str(rc["room"].Id) in sel_ids]
    elif sel_ids:
        matched = [rc for rc in rooms_corte if str(rc["room"].Id) in sel_ids]
        if matched:
            rooms_corte = matched

    usando_vaos_fallback = False
    if not rooms_corte:
        rooms_corte = _vaos_sem_room_no_corte(view)
        usando_vaos_fallback = True
    if not rooms_corte:
        return 0, 0, True

    if floors is None or ceilings is None:
        floors, ceilings = _coletar_pisos_forros_no_corte(view)

    org  = view.Origin
    rdir = view.RightDirection
    upv  = view.UpDirection
    esc_v = max(1.0, float(view.Scale or 50))
    off_tot = 6.5 * MM * esc_v

    criadas = 0
    ignoradas = 0

    for rc in rooms_corte:
        ua, ub, umid = rc["u_a"], rc["u_b"], rc["u_mid"]
        span = ub - ua
        margem = min(0.35 / 0.3048, 0.18 * span)

        if op["pd_pos"] == 0:
            u_pos = umid + min(0.50 / 0.3048, 0.24 * span)
        elif op["pd_pos"] == 1:
            u_pos = umid
        elif op["pd_pos"] == 2:
            u_pos = ua + margem
        else:
            u_pos = ub - margem
        u_pos = max(ua + 0.12 / 0.3048, min(ub - 0.12 / 0.3048, u_pos))

        u_tentativas = [u_pos, umid, ua + 0.30 * span, ub - 0.30 * span]
        pt_room_xy = None
        u_ok = u_pos
        for uc in u_tentativas:
            px = rc["org"].X + uc * rc["rx"] + rc["t_v"] * rc["vx"]
            py = rc["org"].Y + uc * rc["ry"] + rc["t_v"] * rc["vy"]
            if rc["room"] is not None:
                try:
                    if rc["room"].IsPointInRoom(XYZ(px, py, rc["z_probe"])):
                        pt_room_xy = XYZ(px, py, rc["z_min"])
                        u_ok = uc
                        break
                except Exception:
                    pt_room_xy = XYZ(px, py, rc["z_min"])
                    u_ok = uc
                    break
            else:
                pt_room_xy = XYZ(px, py, rc["z_min"])
                u_ok = uc
                break

        if pt_room_xy is None:
            ignoradas += 1
            continue

        v_bot, rf_bot, _ = _melhor_piso_base_no_ponto(
            pt_room_xy, floors,
            rc["z_min"] - 0.50 / 0.3048,
            rc["z_min"] + 1.20 / 0.3048,
            priorizar_acab=op["pd_acab"]
        )
        if rf_bot is None and op["pd_lvl"]:
            v_bot, rf_bot = _nivel_proximo(rc["z_min"])

        if rf_bot is None:
            ignoradas += 1
            continue

        z_base = rc["z_min"]
        v_forro, rf_forro, z_forro = _forro_no_ponto(
            pt_room_xy, ceilings,
            z_base + 1.50 / 0.3048,
            max(rc["z_max"], z_base + 4.50 / 0.3048) + 1.00 / 0.3048
        )
        z_busca_laje = (z_forro + 0.03 / 0.3048) if z_forro is not None else (z_base + 1.80 / 0.3048)
        v_laje, rf_laje, _ = _laje_ou_topo_acima_no_ponto(
            pt_room_xy, floors,
            z_busca_laje,
            z_base + 6.50 / 0.3048
        )

        # Se nao ha forro nem laje plana acima, busca Telhado / Madeiramento na faixa do vao
        v_cob, rf_cob = None, None
        if rf_forro is None and rf_laje is None:
            cands_cob = _coletar_refs_cobertura_vertical(
                view, u_ok, max(0.35 * FT, 0.25 * span),
                v_bot + 1.50 * FT, v_bot + 10.0 * FT,
                incluir_madeira=True
            )
            if cands_cob:
                # Prefere ponto de menor altura compativel (madeiramento / face inferior) dentro do u_ok
                cands_cob.sort(key=lambda x: x[0])
                v_cob, rf_cob = cands_cob[0][0], cands_cob[0][1]

        refs_cadeia = [(v_bot, rf_bot, "Piso")]
        if op["pd_pf"] and rf_forro is not None:
            refs_cadeia.append((v_forro, rf_forro, "Forro"))
        if (op["pd_fl"] or (op["pd_pf"] and rf_forro is None)) and rf_laje is not None:
            refs_cadeia.append((v_laje, rf_laje, "Laje"))
        elif rf_forro is None and rf_laje is None and rf_cob is not None:
            refs_cadeia.append((v_cob, rf_cob, "Cob"))

        if len(refs_cadeia) >= 2:
            v_lo = min(x[0] for x in refs_cadeia)
            v_hi = max(x[0] for x in refs_cadeia)
            line_pd = Line.CreateBound(
                org + rdir * u_ok + upv * v_lo,
                org + rdir * u_ok + upv * v_hi
            )
            d_pd = _criar_dim_segura(view, line_pd, refs_cadeia, dim_type=op["dim_type"], min_gap=20.0 * MM)
            if d_pd is not None:
                criadas += 1
            else:
                ignoradas += 1
        else:
            ignoradas += 1

        if op["pd_tot"] and rf_laje is not None and len(refs_cadeia) >= 3:
            refs_tot = [(v_bot, rf_bot, "Piso"), (v_laje, rf_laje, "Laje")]
            line_tot = Line.CreateBound(
                org + rdir * (u_ok + off_tot) + upv * min(v_bot, v_laje),
                org + rdir * (u_ok + off_tot) + upv * max(v_bot, v_laje)
            )
            if _criar_dim_segura(view, line_tot, refs_tot, dim_type=op["dim_type"], min_gap=20.0 * MM) is not None:
                criadas += 1

    return criadas, ignoradas, (False if not usando_vaos_fallback else False)


def run_cotar_pe_direito(abrir_config=True):
    _verificar_view_section(u"Pé-Direito Automático no Corte")
    dim_types = _listar_tipos_cota_linear()
    prefs = _carregar_prefs()
    op = _resolver_opcoes_pe_direito(prefs, dim_types, bool(abrir_config))

    floors, ceilings = _coletar_pisos_forros_no_corte(view)
    with Transaction(doc, u"Cotar Pé-Direito no Corte") as t:
        t.Start()
        criadas, ignoradas, vazio = _executar_cotas_pe_direito(op, floors=floors, ceilings=ceilings)
        t.Commit()

    if vazio:
        forms.alert("Nenhum Ambiente (Room) ou vão entre paredes cortadas foi encontrado nesta vista.", title="Pé-Direito")
    elif criadas == 0:
        msg = u"Nenhuma cota vertical de pé-direito pôde ser criada."
        if ignoradas:
            msg += u"\nℹ Ambientes/vãos sem forro, laje ou cobertura acima: {}".format(ignoradas)
        forms.alert(msg, title="Pé-Direito Automático no Corte")


# ==============================================================================
# 3. COTAS DE ESQUADRIAS NO CORTE / ELEVACAO (PORTAS E JANELAS)
# ==============================================================================

XAML_ESQUADRIAS_CORTE = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Cotas de Esquadrias no Corte / Elevação" Width="510" SizeToContent="Height"
        WindowStartupLocation="CenterScreen" ResizeMode="NoResize" Background="#F7F8FA">
    <StackPanel Margin="14">
        <Border Background="#EBF3FC" BorderBrush="#B8D4F5" BorderThickness="1" CornerRadius="4" Padding="9" Margin="0,0,0,10">
            <TextBlock TextWrapping="Wrap" FontWeight="SemiBold" Foreground="#1F3A57"
                       Text="Cota automaticamente Peitoril, Altura e Boneca superior (até Forro ou Laje) de Portas e Janelas visíveis em Cortes ou Elevações."/>
        </Border>

        <Border Background="White" BorderBrush="#D0D7DE" BorderThickness="1" CornerRadius="4" Padding="10" Margin="0,0,0,8">
            <Grid>
                <Grid.ColumnDefinitions>
                    <ColumnDefinition Width="195"/>
                    <ColumnDefinition Width="*"/>
                </Grid.ColumnDefinitions>
                <Grid.RowDefinitions>
                    <RowDefinition Height="28"/>
                    <RowDefinition Height="28"/>
                    <RowDefinition Height="28"/>
                </Grid.RowDefinitions>

                <TextBlock Grid.Row="0" Grid.Column="0" Text="Escopo:" VerticalAlignment="Center"/>
                <ComboBox  Grid.Row="0" Grid.Column="1" x:Name="cb_escopo" Height="24"/>

                <TextBlock Grid.Row="1" Grid.Column="0" Text="Posição lateral da cota:" VerticalAlignment="Center"/>
                <ComboBox  Grid.Row="1" Grid.Column="1" x:Name="cb_lado" Height="24"/>

                <TextBlock Grid.Row="2" Grid.Column="0" Text="Tipo de cota linear:" VerticalAlignment="Center"/>
                <ComboBox  Grid.Row="2" Grid.Column="1" x:Name="cb_tipo" Height="24"/>
            </Grid>
        </Border>

        <Border Background="White" BorderBrush="#D0D7DE" BorderThickness="1" CornerRadius="4" Padding="10" Margin="0,0,0,10">
            <StackPanel>
                <TextBlock Text="Categorias e Trechos:" FontWeight="SemiBold" Margin="0,0,0,6"/>
                <CheckBox x:Name="ck_janelas"   Content="Janelas" IsChecked="True" Margin="0,0,0,5"/>
                <CheckBox x:Name="ck_portas"    Content="Portas" IsChecked="True" Margin="0,0,0,5"/>
                <CheckBox x:Name="ck_peitoril"  Content="Incluir cota inferior até Piso / Nível (Peitoril de janelas)" IsChecked="True" Margin="0,0,0,5"/>
                <CheckBox x:Name="ck_altura"    Content="Incluir altura própria da esquadria" IsChecked="True" Margin="0,0,0,5"/>
                <CheckBox x:Name="ck_superior"  Content="Incluir cota superior até Forro / Fundo da Laje" IsChecked="True" Margin="0,0,0,5"/>
                <CheckBox x:Name="ck_largura"   Content="Adicionar cota horizontal de largura (quando vista de frente)" IsChecked="False"/>
            </StackPanel>
        </Border>

        <StackPanel Orientation="Horizontal" HorizontalAlignment="Right">
            <Button x:Name="btn_cancel" Content="Cancelar" Width="95" Height="30" Margin="0,0,8,0"/>
            <Button x:Name="btn_ok" Content="Cotar Esquadrias" Width="165" Height="30"
                    IsDefault="True" Background="#0078D4" Foreground="White" FontWeight="SemiBold"/>
        </StackPanel>
    </StackPanel>
</Window>
"""


class CotarEsquadriasCorteWindow(forms.WPFWindow):
    def __init__(self, prefs, dim_types):
        forms.WPFWindow.__init__(self, XAML_ESQUADRIAS_CORTE, literal_string=True)
        self.confirmado = False
        self.resultado  = {}
        self._dim_types = dim_types

        self._cb_esc   = self.FindName("cb_escopo")
        self._cb_lado  = self.FindName("cb_lado")
        self._cb_tipo  = self.FindName("cb_tipo")
        self._ck_jan   = self.FindName("ck_janelas")
        self._ck_por   = self.FindName("ck_portas")
        self._ck_pei   = self.FindName("ck_peitoril")
        self._ck_alt   = self.FindName("ck_altura")
        self._ck_sup   = self.FindName("ck_superior")
        self._ck_lar   = self.FindName("ck_largura")

        for s in (
            u"Todas as Esquadrias visíveis no Corte/Elevação",
            u"Somente Esquadrias selecionadas",
        ):
            self._cb_esc.Items.Add(s)
        self._cb_esc.SelectedIndex = max(0, min(1, int(prefs.get("eq_escopo", 0) or 0)))

        for ld in (
            u"Automático (logo ao lado da esquadria)",
            u"Sempre à direita",
            u"Sempre à esquerda",
        ):
            self._cb_lado.Items.Add(ld)
        self._cb_lado.SelectedIndex = max(0, min(2, int(prefs.get("eq_lado", 0) or 0)))

        nomes_dt = [de._elem_name(dt) for dt in dim_types]
        idx_dt_ini = max(0, min(max(0, len(dim_types) - 1), int(prefs.get("eq_tipo", 0) or 0)))
        self._sc_tipo = uis.SearchableCombo.attach_auto(
            self._cb_tipo, nomes_dt, placeholder=u"Pesquisar tipo de cota...", initial_index=idx_dt_ini
        )

        self._ck_jan.IsChecked = bool(prefs.get("eq_jan", True))
        self._ck_por.IsChecked = bool(prefs.get("eq_por", True))
        self._ck_pei.IsChecked = bool(prefs.get("eq_pei", True))
        self._ck_alt.IsChecked = bool(prefs.get("eq_alt", True))
        self._ck_sup.IsChecked = bool(prefs.get("eq_sup", True))
        self._ck_lar.IsChecked = bool(prefs.get("eq_lar", False))

        self.FindName("btn_ok").Click     += self.on_ok
        self.FindName("btn_cancel").Click += self.on_cancel

    def on_cancel(self, sender, args):
        self.confirmado = False
        self.Close()

    def on_ok(self, sender, args):
        if not (self._ck_jan.IsChecked or self._ck_por.IsChecked):
            forms.alert("Marque ao menos Janelas ou Portas.", title="Aviso")
            return
        idx_t = max(0, self._sc_tipo.selected_index())
        self.resultado = {
            "eq_escopo": max(0, self._cb_esc.SelectedIndex),
            "eq_lado":   max(0, self._cb_lado.SelectedIndex),
            "eq_tipo":   idx_t,
            "eq_jan":    bool(self._ck_jan.IsChecked),
            "eq_por":    bool(self._ck_por.IsChecked),
            "eq_pei":    bool(self._ck_pei.IsChecked),
            "eq_alt":    bool(self._ck_alt.IsChecked),
            "eq_sup":    bool(self._ck_sup.IsChecked),
            "eq_lar":    bool(self._ck_lar.IsChecked),
            "dim_type":  self._dim_types[idx_t] if self._dim_types else None,
        }
        self.confirmado = True
        self.Close()


class _EsquadriaFilter(ISelectionFilter):
    def AllowElement(self, el):
        try:
            cid = _eid_int(el.Category.Id)
            return cid in (int(BuiltInCategory.OST_Windows), int(BuiltInCategory.OST_Doors))
        except Exception:
            return False

    def AllowReference(self, ref, pt):
        return False


def _amostrar_pontos_uv_visibilidade(u_lo, u_hi, v_lo, v_hi):
    """Retorna 5 pontos internos (centro + 4 quadrantes a 30%/70%) para teste de oclusao na vista."""
    du = u_hi - u_lo
    dv = v_hi - v_lo
    if du <= 1e-6 or dv <= 1e-6:
        return []
    return [
        (u_lo + 0.50 * du, v_lo + 0.50 * dv),
        (u_lo + 0.30 * du, v_lo + 0.30 * dv),
        (u_lo + 0.70 * du, v_lo + 0.30 * dv),
        (u_lo + 0.30 * du, v_lo + 0.70 * dv),
        (u_lo + 0.70 * du, v_lo + 0.70 * dv),
    ]


def _coletar_solidos_elemento(el, v_view):
    """Extrai Solid(s) visiveis na vista para teste geometrico de oclusao."""
    opts = Options()
    opts.View = v_view
    sols = []
    try:
        geom = el.get_Geometry(opts)
    except Exception:
        return sols

    def _walk(g):
        if g is None:
            return
        for obj in g:
            if isinstance(obj, Solid) and obj.Volume > 1e-6:
                sols.append(obj)
            elif isinstance(obj, GeometryInstance):
                try:
                    _walk(obj.GetInstanceGeometry())
                except Exception:
                    pass

    _walk(geom)
    return sols


def _bb_uvd_em_vista(el, v_view):
    """Retorna (u_min, u_max, v_min, v_max, w_min, w_max, bb) em coordenadas locais da vista."""
    bb = el.get_BoundingBox(v_view) or el.get_BoundingBox(None)
    if bb is None:
        return None
    org  = v_view.Origin
    rdir = v_view.RightDirection
    upv  = v_view.UpDirection
    vdir = v_view.ViewDirection
    us, vs, ws = [], [], []
    for cx in (bb.Min.X, bb.Max.X):
        for cy in (bb.Min.Y, bb.Max.Y):
            for cz in (bb.Min.Z, bb.Max.Z):
                dx, dy, dz = cx - org.X, cy - org.Y, cz - org.Z
                us.append(dx * rdir.X + dy * rdir.Y + dz * rdir.Z)
                vs.append(dx * upv.X  + dy * upv.Y  + dz * upv.Z)
                ws.append(dx * vdir.X + dy * vdir.Y + dz * vdir.Z)
    return (min(us), max(us), min(vs), max(vs), min(ws), max(ws), bb)


def _coletar_oclusores_vista(v_view):
    """Lista elementos potencialmente oclusores visiveis na faixa do corte com BBox em (u, v, w)."""
    oclusores = []
    vistos = set()
    coletores = [
        FilteredElementCollector(doc, v_view.Id).OfClass(Wall),
        FilteredElementCollector(doc, v_view.Id).OfClass(Floor),
        FilteredElementCollector(doc, v_view.Id).OfClass(Ceiling),
        FilteredElementCollector(doc, v_view.Id).OfClass(RoofBase),
        FilteredElementCollector(doc, v_view.Id).OfCategory(BuiltInCategory.OST_StructuralColumns),
        FilteredElementCollector(doc, v_view.Id).OfCategory(BuiltInCategory.OST_Doors),
    ]
    for col in coletores:
        for el in col.WhereElementIsNotElementType().ToElements():
            eid = _eid_int(el.Id)
            if eid in vistos:
                continue
            vistos.add(eid)
            uvd = _bb_uvd_em_vista(el, v_view)
            if uvd is not None:
                oclusores.append((eid, el, uvd))
    return oclusores


def _esquadria_visivel_na_vista(fi, v_view, bb_uv, crop_uv, oclusores, solidos_cache):
    """Retorna True se a esquadria esta efetivamente visivel (nao oculta por paredes/portas a frente)."""
    u_min, u_max, v_min, v_max, bb = bb_uv
    if crop_uv is not None:
        u_min = max(u_min, crop_uv[0])
        u_max = min(u_max, crop_uv[1])
        v_min = max(v_min, crop_uv[2])
        v_max = min(v_max, crop_uv[3])
    if (u_max - u_min) <= 0.05 * FT or (v_max - v_min) <= 0.05 * FT:
        return False

    org  = v_view.Origin
    rdir = v_view.RightDirection
    upv  = v_view.UpDirection
    vdir = v_view.ViewDirection

    loc = getattr(fi, "Location", None)
    pt_fi = getattr(loc, "Point", None) if loc is not None else None
    if pt_fi is None:
        pt_fi = 0.5 * (bb.Min + bb.Max)

    w_fi = (pt_fi.X - org.X) * vdir.X + (pt_fi.Y - org.Y) * vdir.Y + (pt_fi.Z - org.Z) * vdir.Z
    host_w = fi.Host if isinstance(getattr(fi, "Host", None), Wall) else None
    host_id = _eid_int(host_w.Id) if host_w is not None else -1
    host_half = 0.5 * host_w.Width if host_w is not None else 0.10 * FT

    # Esquadria diretamente no plano de corte esta sempre visivel
    if abs(w_fi) <= host_half + 0.15 * FT:
        return True

    sgn = 1.0 if w_fi > 0.0 else -1.0
    ray_dir = XYZ(vdir.X * sgn, vdir.Y * sgn, vdir.Z * sgn)
    dist_fi = abs(w_fi)

    facing_dot = 1.0
    try:
        fo = fi.FacingOrientation
        facing_dot = abs(fo.X * ray_dir.X + fo.Y * ray_dir.Y + fo.Z * ray_dir.Z)
    except Exception:
        pass

    if facing_dot > 0.35:
        excluir_host = True
        dist_end = dist_fi - host_half - 0.05 * FT
    else:
        excluir_host = False
        uvd_fi = _bb_uvd_em_vista(fi, v_view)
        if uvd_fi is not None:
            d_near = uvd_fi[4] if sgn > 0.0 else -uvd_fi[5]
            dist_end = d_near - 0.05 * FT
        else:
            dist_end = dist_fi - 0.10 * FT

    if dist_end <= 0.05 * FT:
        return True

    pts_uv = _amostrar_pontos_uv_visibilidade(u_min, u_max, v_min, v_max)
    if not pts_uv:
        return False

    fi_id = _eid_int(fi.Id)
    cands_occ = []
    for occ_id, occ_el, (ou0, ou1, ov0, ov1, ow0, ow1, _) in oclusores:
        if occ_id == fi_id:
            continue
        if excluir_host and occ_id == host_id:
            continue
        if ou1 < u_min or ou0 > u_max or ov1 < v_min or ov0 > v_max:
            continue
        d_occ_min = ow0 if sgn > 0.0 else -ow1
        if d_occ_min >= dist_end:
            continue
        cands_occ.append((occ_id, occ_el, ou0, ou1, ov0, ov1))

    if not cands_occ:
        return True

    sci_opts = SolidCurveIntersectionOptions()
    sci_opts.ResultType = SolidCurveIntersectionMode.CurveSegmentsInside
    d_start = 0.005 * FT
    clear_cnt = 0

    for u_pt, v_pt in pts_uv:
        p0 = org + rdir * u_pt + upv * v_pt + ray_dir * d_start
        p1 = org + rdir * u_pt + upv * v_pt + ray_dir * dist_end
        try:
            ray = Line.CreateBound(p0, p1)
        except Exception:
            continue

        bloqueado = False
        for occ_id, occ_el, ou0, ou1, ov0, ov1 in cands_occ:
            if not (ou0 <= u_pt <= ou1 and ov0 <= v_pt <= ov1):
                continue
            if occ_id not in solidos_cache:
                solidos_cache[occ_id] = _coletar_solidos_elemento(occ_el, v_view)
            for sol in solidos_cache[occ_id]:
                try:
                    res = sol.IntersectWithCurve(ray, sci_opts)
                    if res is not None and res.SegmentCount > 0:
                        bloqueado = True
                        break
                except Exception:
                    pass
            if bloqueado:
                break

        if not bloqueado:
            clear_cnt += 1
            if clear_cnt >= 2:
                return True

    return False


def _resolver_opcoes_esquadrias(prefs, dim_types, abrir_config):
    if abrir_config:
        win = CotarEsquadriasCorteWindow(prefs, dim_types)
        win.ShowDialog()
        if not win.confirmado:
            script.exit()
        op = win.resultado
        _salvar_prefs(dict((k, v) for k, v in op.items() if k != "dim_type"))
        return op

    idx_t = max(0, min(max(0, len(dim_types) - 1), int(prefs.get("eq_tipo", 0) or 0)))
    return {
        "eq_escopo": max(0, min(1, int(prefs.get("eq_escopo", 0) or 0))),
        "eq_lado":   max(0, min(2, int(prefs.get("eq_lado", 0) or 0))),
        "eq_tipo":   idx_t,
        "eq_jan":    bool(prefs.get("eq_jan", True)),
        "eq_por":    bool(prefs.get("eq_por", True)),
        "eq_pei":    bool(prefs.get("eq_pei", True)),
        "eq_alt":    bool(prefs.get("eq_alt", True)),
        "eq_sup":    bool(prefs.get("eq_sup", True)),
        "eq_lar":    bool(prefs.get("eq_lar", False)),
        "dim_type":  dim_types[idx_t] if dim_types else None,
    }


def _executar_cotas_esquadrias(op, floors=None, ceilings=None, esq_pre=None):
    cats = []
    if op["eq_jan"]:
        cats.append(BuiltInCategory.OST_Windows)
    if op["eq_por"]:
        cats.append(BuiltInCategory.OST_Doors)

    esquadrias = list(esq_pre) if esq_pre is not None else []
    usou_selecao = (op.get("eq_escopo", 0) == 1)
    if esq_pre is None:
        sel_esq = []
        for eid in uidoc.Selection.GetElementIds():
            el = doc.GetElement(eid)
            if isinstance(el, FamilyInstance) and el.Category is not None:
                cid = _eid_int(el.Category.Id)
                if (op["eq_jan"] and cid == int(BuiltInCategory.OST_Windows)) or (op["eq_por"] and cid == int(BuiltInCategory.OST_Doors)):
                    sel_esq.append(el)
        if sel_esq:
            esquadrias = sel_esq
            usou_selecao = True
        elif op["eq_escopo"] == 1:
            try:
                refs = uidoc.Selection.PickObjects(ObjectType.Element, _EsquadriaFilter(), u"Selecione as Portas/Janelas e clique em Concluir")
                esquadrias = [doc.GetElement(rf.ElementId) for rf in refs]
            except Exception:
                return 0, 0, True
        else:
            for cat in cats:
                for fi in FilteredElementCollector(doc, view.Id).OfCategory(cat).WhereElementIsNotElementType().ToElements():
                    if isinstance(fi, FamilyInstance) and getattr(fi, "SuperComponent", None) is None:
                        esquadrias.append(fi)

    if not esquadrias:
        return 0, 0, True

    if floors is None or ceilings is None:
        floors, ceilings = _coletar_pisos_forros_no_corte(view)
    crop_uv = _crop_uv_em_vista(view)
    oclusores = _coletar_oclusores_vista(view) if not usou_selecao else []
    solidos_cache = {}
    org   = view.Origin
    rdir  = view.RightDirection
    upv   = view.UpDirection
    esc_v = max(1.0, float(view.Scale or 50))
    off_u = 4.5 * MM * esc_v
    off_w = 3.5 * MM * esc_v

    criadas = 0
    erros   = 0

    for fi in esquadrias:
        bb_uv = _bb_uv_em_vista(fi, view)
        if bb_uv is None:
            continue
        u_min, u_max, v_min, v_max, bb = bb_uv
        if crop_uv is not None:
            if u_max < crop_uv[0] or u_min > crop_uv[1] or v_max < crop_uv[2] or v_min > crop_uv[3]:
                continue
        if not usou_selecao and not _esquadria_visivel_na_vista(fi, view, bb_uv, crop_uv, oclusores, solidos_cache):
            continue

        vb, rfb, vt, rft = _refs_verticais_esquadria(fi, view)
        if rfb is None and rft is None:
            erros += 1
            continue

        pt_mid = 0.5 * (bb.Min + bb.Max)
        lados_xy = []
        try:
            f_ori = fi.FacingOrientation
            lados_xy.append(pt_mid + f_ori * (0.35 / 0.3048))
            lados_xy.append(pt_mid - f_ori * (0.35 / 0.3048))
        except Exception:
            pass
        if not lados_xy:
            vdir_v = view.ViewDirection
            lados_xy.append(pt_mid + vdir_v * (0.35 / 0.3048))
            lados_xy.append(pt_mid - vdir_v * (0.35 / 0.3048))

        z_lvl_est = bb.Min.Z
        try:
            lv_el = doc.GetElement(fi.LevelId)
            if lv_el is not None:
                z_lvl_est = lv_el.Elevation
        except Exception:
            pass

        phase_v = None
        try:
            p_ph = view.get_Parameter(BuiltInParameter.VIEW_PHASE)
            if p_ph is not None:
                phase_v = doc.GetElement(p_ph.AsElementId())
        except Exception:
            pass

        z_room_probe = max(z_lvl_est + 0.50 * FT, min(bb.Max.Z - 0.10 * FT, z_lvl_est + 1.20 * FT))
        vdir_v = view.ViewDirection

        def _avaliar_lado(pxy):
            vp, rfp, fl_p = _melhor_piso_base_no_ponto(
                pxy, floors, z_lvl_est - 0.50 / 0.3048, bb.Min.Z + 0.10 / 0.3048, priorizar_acab=True
            )
            vcf, rff, _ = _forro_no_ponto(pxy, ceilings, bb.Max.Z + 0.02 / 0.3048, bb.Max.Z + 3.50 / 0.3048)
            vcl, rfl, _ = _laje_ou_topo_acima_no_ponto(pxy, floors, bb.Max.Z + 0.02 / 0.3048, bb.Max.Z + 4.50 / 0.3048)

            tem_room = False
            try:
                p_rm = XYZ(pxy.X, pxy.Y, z_room_probe)
                rm_hit = doc.GetRoomAtPoint(p_rm, phase_v) if phase_v is not None else doc.GetRoomAtPoint(p_rm)
                tem_room = (rm_hit is not None and getattr(rm_hit, "Area", 0.0) > 1e-3)
            except Exception:
                pass

            eh_acab = (fl_p is not None and not de._eh_piso_estrutural(fl_p))
            voltado_vista = ((pxy.X - pt_mid.X) * vdir_v.X + (pxy.Y - pt_mid.Y) * vdir_v.Y) > 0.0
            score = (
                (10.0 if tem_room else 0.0)
                + (5.0 if rff is not None else 0.0)
                + (3.0 if eh_acab else (1.0 if rfp is not None else 0.0))
                + (0.5 if voltado_vista else 0.0)
            )
            return (score, vp, rfp, vcf, rff, vcl, rfl)

        avaliados = [_avaliar_lado(pxy) for pxy in lados_xy]
        avaliados.sort(key=lambda x: x[0], reverse=True)

        v_piso, rf_piso = None, None
        for _, vp, rfp, _, _, _, _ in avaliados:
            if rfp is not None:
                v_piso, rf_piso = vp, rfp
                break
        if rf_piso is None:
            v_piso, rf_piso, _ = _melhor_piso_base_no_ponto(
                pt_mid, floors, z_lvl_est - 0.50 / 0.3048, bb.Min.Z + 0.10 / 0.3048, priorizar_acab=True
            )
        if rf_piso is None:
            v_piso, rf_piso = _nivel_proximo(z_lvl_est)

        v_sup, rf_sup = None, None
        for _, _, _, vc_f, rf_f, vc_l, rf_l in avaliados:
            if rf_f is not None and rf_l is not None:
                v_sup, rf_sup = (vc_f, rf_f) if vc_f <= vc_l else (vc_l, rf_l)
                break
            elif rf_f is not None:
                v_sup, rf_sup = vc_f, rf_f
                break
            elif rf_l is not None:
                v_sup, rf_sup = vc_l, rf_l
                break
        if rf_sup is None:
            vc_f, rf_f, _ = _forro_no_ponto(pt_mid, ceilings, bb.Max.Z + 0.02 / 0.3048, bb.Max.Z + 3.50 / 0.3048)
            vc_l, rf_l, _ = _laje_ou_topo_acima_no_ponto(pt_mid, floors, bb.Max.Z + 0.02 / 0.3048, bb.Max.Z + 4.50 / 0.3048)
            if rf_f is not None and rf_l is not None:
                v_sup, rf_sup = (vc_f, rf_f) if vc_f <= vc_l else (vc_l, rf_l)
            elif rf_f is not None:
                v_sup, rf_sup = vc_f, rf_f
            elif rf_l is not None:
                v_sup, rf_sup = vc_l, rf_l

        eh_porta = (_eid_int(fi.Category.Id) == int(BuiltInCategory.OST_Doors))
        refs_v = []
        if op["eq_pei"] and rf_piso is not None:
            refs_v.append((v_piso, rf_piso, "Piso"))

        if eh_porta:
            if ((not op["eq_pei"]) or rf_piso is None) and rfb is not None:
                refs_v.append((vb, rfb, "PortaBot"))
            if op["eq_alt"] and rft is not None:
                refs_v.append((vt, rft, "PortaTop"))
        else:
            if (op["eq_alt"] or op["eq_pei"]) and rfb is not None:
                refs_v.append((vb, rfb, "JanPeit"))
            if (op["eq_alt"] or op["eq_sup"]) and rft is not None:
                refs_v.append((vt, rft, "JanTop"))

        if op["eq_sup"] and rf_sup is not None:
            refs_v.append((v_sup, rf_sup, "Sup"))

        refs_v = _dedup_refs_por_coord(refs_v, min_gap=20.0 * MM)
        if len(refs_v) >= 2:
            if op["eq_lado"] == 1:
                u_dim = u_max + off_u
            elif op["eq_lado"] == 2:
                u_dim = u_min - off_u
            else:
                u_dim = u_max + off_u
                if crop_uv is not None and u_dim > crop_uv[1] - 0.15 * FT:
                    u_dim = u_min - off_u

            line_v = Line.CreateBound(
                org + rdir * u_dim + upv * refs_v[0][0],
                org + rdir * u_dim + upv * refs_v[-1][0]
            )
            if _criar_dim_segura(view, line_v, refs_v, dim_type=op["dim_type"], min_gap=20.0 * MM) is not None:
                criadas += 1
            else:
                erros += 1

        if op["eq_lar"]:
            ul, rfl, ur, rfr = _refs_horizontais_esquadria(fi, view)
            if rfl is not None and rfr is not None:
                v_dim_w = v_max + off_w
                line_w = Line.CreateBound(
                    org + rdir * ul + upv * v_dim_w,
                    org + rdir * ur + upv * v_dim_w
                )
                if _criar_dim_segura(view, line_w, [(ul, rfl, "L"), (ur, rfr, "R")], dim_type=op["dim_type"], min_gap=20.0 * MM) is not None:
                    criadas += 1

    return criadas, erros, False


def run_cotar_esquadrias_corte(abrir_config=True):
    _verificar_view_section(u"Cotas de Esquadrias no Corte")
    dim_types = _listar_tipos_cota_linear()
    prefs = _carregar_prefs()
    op = _resolver_opcoes_esquadrias(prefs, dim_types, bool(abrir_config))

    floors, ceilings = _coletar_pisos_forros_no_corte(view)
    with Transaction(doc, u"Cotar Esquadrias no Corte") as t:
        t.Start()
        criadas, erros, vazio = _executar_cotas_esquadrias(op, floors=floors, ceilings=ceilings)
        t.Commit()

    if vazio:
        forms.alert("Nenhuma Porta ou Janela visível foi encontrada neste Corte/Elevação.", title="Cotas de Esquadrias")
    elif criadas == 0 and erros:
        forms.alert(u"Não foi possível gerar cotas para as esquadrias encontradas.", title="Cotas de Esquadrias")


# ==============================================================================
# 4. INDICACAO AUTOMATICA DE INCLINACAO DE TELHADO NO CORTE
# ==============================================================================

XAML_INCLINACAO_TELHADO = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Inclinação de Telhado no Corte" Width="515" SizeToContent="Height"
        WindowStartupLocation="CenterScreen" ResizeMode="NoResize" Background="#F7F8FA">
    <StackPanel Margin="14">
        <Border Background="#EBF3FC" BorderBrush="#B8D4F5" BorderThickness="1" CornerRadius="4" Padding="9" Margin="0,0,0,10">
            <TextBlock TextWrapping="Wrap" FontWeight="SemiBold" Foreground="#1F3A57"
                       Text="Lê automaticamente o caimento de cada água dos Telhados sólidos no Corte/Elevação (ignorando Vidraças Inclinadas de madeiramento) e desenha seta + nota de inclinação."/>
        </Border>

        <Border Background="White" BorderBrush="#D0D7DE" BorderThickness="1" CornerRadius="4" Padding="10" Margin="0,0,0,8">
            <Grid>
                <Grid.ColumnDefinitions>
                    <ColumnDefinition Width="205"/>
                    <ColumnDefinition Width="*"/>
                </Grid.ColumnDefinitions>
                <Grid.RowDefinitions>
                    <RowDefinition Height="28"/>
                    <RowDefinition Height="28"/>
                    <RowDefinition Height="28"/>
                    <RowDefinition Height="28"/>
                    <RowDefinition Height="28"/>
                    <RowDefinition Height="28"/>
                    <RowDefinition Height="28"/>
                </Grid.RowDefinitions>

                <TextBlock Grid.Row="0" Grid.Column="0" Text="Escopo dos Telhados:" VerticalAlignment="Center"/>
                <ComboBox  Grid.Row="0" Grid.Column="1" x:Name="cb_escopo" Height="24"/>

                <TextBlock Grid.Row="1" Grid.Column="0" Text="Estilo da indicação:" VerticalAlignment="Center"/>
                <ComboBox  Grid.Row="1" Grid.Column="1" x:Name="cb_estilo" Height="24"/>

                <TextBlock Grid.Row="2" Grid.Column="0" Text="Formato do valor:" VerticalAlignment="Center"/>
                <ComboBox  Grid.Row="2" Grid.Column="1" x:Name="cb_fmt" Height="24"/>

                <TextBlock Grid.Row="3" Grid.Column="0" Text="Sentido da seta:" VerticalAlignment="Center"/>
                <ComboBox  Grid.Row="3" Grid.Column="1" x:Name="cb_dir" Height="24"/>

                <TextBlock Grid.Row="4" Grid.Column="0" Text="Tipo de Texto (TextNote):" VerticalAlignment="Center"/>
                <ComboBox  Grid.Row="4" Grid.Column="1" x:Name="cb_text_type" Height="24"/>

                <TextBlock Grid.Row="5" Grid.Column="0" Text="Distância acima da água (mm):" VerticalAlignment="Center"/>
                <TextBox   Grid.Row="5" Grid.Column="1" x:Name="tb_off_mm" Height="24" Text="3.5" VerticalContentAlignment="Center"/>

                <TextBlock Grid.Row="6" Grid.Column="0" Text="Comprimento da seta (mm):" VerticalAlignment="Center"/>
                <TextBox   Grid.Row="6" Grid.Column="1" x:Name="tb_len_mm" Height="24" Text="14.0" VerticalContentAlignment="Center"/>
            </Grid>
        </Border>

        <StackPanel Orientation="Horizontal" HorizontalAlignment="Right">
            <Button x:Name="btn_cancel" Content="Cancelar" Width="95" Height="30" Margin="0,0,8,0"/>
            <Button x:Name="btn_ok" Content="Aplicar Inclinações" Width="170" Height="30"
                    IsDefault="True" Background="#0078D4" Foreground="White" FontWeight="SemiBold"/>
        </StackPanel>
    </StackPanel>
</Window>
"""


class InclinacaoTelhadoWindow(forms.WPFWindow):
    def __init__(self, prefs, text_types):
        forms.WPFWindow.__init__(self, XAML_INCLINACAO_TELHADO, literal_string=True)
        self.confirmado  = False
        self.resultado   = {}
        self._text_types = text_types

        self._cb_esc = self.FindName("cb_escopo")
        self._cb_est = self.FindName("cb_estilo")
        self._cb_fmt = self.FindName("cb_fmt")
        self._cb_dir = self.FindName("cb_dir")
        self._cb_txt = self.FindName("cb_text_type")
        self._tb_off = self.FindName("tb_off_mm")
        self._tb_len = self.FindName("tb_len_mm")

        for s in (
            u"Todos os Telhados sólidos visíveis no Corte",
            u"Somente Telhados selecionados",
        ):
            self._cb_esc.Items.Add(s)
        self._cb_esc.SelectedIndex = max(0, min(1, int(prefs.get("it_esc", 0) or 0)))

        for e in (
            u"Seta paralela à água + Texto inclinado",
            u"Triângulo retângulo de inclinação + Texto",
            u"Apenas Texto alinhado à água",
        ):
            self._cb_est.Items.Add(e)
        self._cb_est.SelectedIndex = max(0, min(2, int(prefs.get("it_est", 0) or 0)))

        for f in (
            u"i = 30%",
            u"30%",
            u"i = 30% (16,7°)",
            u"16,7°",
        ):
            self._cb_fmt.Items.Add(f)
        self._cb_fmt.SelectedIndex = max(0, min(3, int(prefs.get("it_fmt", 0) or 0)))

        for d in (
            u"Caimento (descendo p/ o beiral)",
            u"Aclive (subindo p/ a cumeeira)",
        ):
            self._cb_dir.Items.Add(d)
        self._cb_dir.SelectedIndex = max(0, min(1, int(prefs.get("it_dir", 0) or 0)))

        nomes_tt = [de._elem_name(tt) for tt in text_types]
        idx_tt_ini = max(0, min(max(0, len(text_types) - 1), int(prefs.get("it_txt", 0) or 0)))
        self._sc_txt = uis.SearchableCombo.attach_auto(
            self._cb_txt, nomes_tt, placeholder=u"Pesquisar tipo de texto...", initial_index=idx_tt_ini
        )

        self._tb_off.Text = str(prefs.get("it_off", "3.5"))
        self._tb_len.Text = str(prefs.get("it_len", "14.0"))

        self.FindName("btn_ok").Click     += self.on_ok
        self.FindName("btn_cancel").Click += self.on_cancel

    def on_cancel(self, sender, args):
        self.confirmado = False
        self.Close()

    def on_ok(self, sender, args):
        if not self._text_types:
            forms.alert("Nenhum TextNoteType encontrado neste projeto.", title="Aviso")
            return
        try:
            off_mm = float((self._tb_off.Text or "3.5").replace(",", "."))
            len_mm = max(4.0, float((self._tb_len.Text or "14.0").replace(",", ".")))
        except Exception:
            forms.alert("Informe números válidos para distância e comprimento em mm.", title="Aviso")
            return
        idx_t = self._sc_txt.selected_index()
        if idx_t < 0:
            forms.alert("Selecione um TextNoteType.", title="Aviso")
            return
        self.resultado = {
            "it_esc":    max(0, self._cb_esc.SelectedIndex),
            "it_est":    max(0, self._cb_est.SelectedIndex),
            "it_fmt":    max(0, self._cb_fmt.SelectedIndex),
            "it_dir":    max(0, self._cb_dir.SelectedIndex),
            "it_txt":    idx_t,
            "it_off":    off_mm,
            "it_len":    len_mm,
            "text_type": self._text_types[idx_t],
        }
        self.confirmado = True
        self.Close()


def _formatar_texto_inclinacao(pct, deg, fmt_idx):
    p_str = (u"{:.0f}".format(pct)) if abs(pct - round(pct)) < 0.05 else (u"{:.1f}".format(pct).replace(".", ","))
    d_str = u"{:.1f}".format(deg).replace(".", ",")
    if fmt_idx == 0:
        return u"i = {}%".format(p_str)
    if fmt_idx == 1:
        return u"{}%".format(p_str)
    if fmt_idx == 2:
        return u"i = {}% ({}°)".format(p_str, d_str)
    return u"{}°".format(d_str)


def _centro_visivel_face_telhado(fc, v_view):
    """Retorna XYZ medio da face do telhado sobre o corte (priorizando cruzamento com plano do corte)."""
    org  = v_view.Origin
    vdir = v_view.ViewDirection
    has_cb = bool(v_view.CropBoxActive)
    cb = v_view.CropBox if has_cb else None
    inv_tr = cb.Transform.Inverse if cb is not None else None

    def _depth(pw):
        return (pw.X - org.X) * vdir.X + (pw.Y - org.Y) * vdir.Y + (pw.Z - org.Z) * vdir.Z

    def _enquadrado(pw):
        if has_cb and inv_tr is not None and cb is not None:
            pl = inv_tr.OfPoint(pw)
            if pl.X < cb.Min.X - 0.1 * FT or pl.X > cb.Max.X + 0.1 * FT:
                return False
            if pl.Y < cb.Min.Y - 0.1 * FT or pl.Y > cb.Max.Y + 0.1 * FT:
                return False
            if pl.Z < cb.Min.Z - 0.25 * FT or pl.Z > cb.Max.Z + 0.25 * FT:
                return False
        return True

    try:
        mesh = fc.Triangulate()
    except Exception:
        mesh = None
    if mesh is None:
        return None

    pts_cut = []
    pts_vis = []
    for ti in range(mesh.NumTriangles):
        tri = mesh.get_Triangle(ti)
        vs = [tri.get_Vertex(0), tri.get_Vertex(1), tri.get_Vertex(2)]
        ds = [_depth(v) for v in vs]
        for px, py, pz in de._cruzar_triangulo_plano_v(
            (vs[0].X, vs[0].Y, vs[0].Z),
            (vs[1].X, vs[1].Y, vs[1].Z),
            (vs[2].X, vs[2].Y, vs[2].Z),
            ds[0], ds[1], ds[2]
        ):
            pw = XYZ(px, py, pz)
            if _enquadrado(pw):
                pts_cut.append(pw)
        for v in vs:
            if _enquadrado(v):
                pts_vis.append(v)

    alvos = pts_cut if pts_cut else pts_vis
    if not alvos:
        return None
    rdir = v_view.RightDirection
    alvos.sort(key=lambda p: (p.X - org.X) * rdir.X + (p.Y - org.Y) * rdir.Y)
    # Pega ponto mediano da extensao horizontal visivel da agua (projetado na propria face)
    p_lo, p_hi = alvos[0], alvos[-1]
    if p_lo.DistanceTo(p_hi) < 0.25 / 0.3048:
        return None
    p_mid = 0.5 * (p_lo + p_hi)
    try:
        res = fc.Project(p_mid)
        if res is not None:
            return res.XYZPoint
    except Exception:
        pass
    return p_mid


def run_inclinacao_telhado():
    _verificar_view_section(u"Inclinação de Telhado no Corte")
    text_types = _listar_tipos_texto()
    prefs = _carregar_prefs()
    win = InclinacaoTelhadoWindow(prefs, text_types)
    win.ShowDialog()
    if not win.confirmado:
        script.exit()

    op = win.resultado
    _salvar_prefs(dict((k, v) for k, v in op.items() if k != "text_type"))

    if op["it_esc"] == 1:
        telhados = []
        for eid in uidoc.Selection.GetElementIds():
            el = doc.GetElement(eid)
            if isinstance(el, RoofBase):
                telhados.append(el)
        if not telhados:
            try:
                refs = uidoc.Selection.PickObjects(ObjectType.Element, u"Selecione os Telhados e clique em Concluir")
                telhados = [doc.GetElement(rf.ElementId) for rf in refs if isinstance(doc.GetElement(rf.ElementId), RoofBase)]
            except Exception:
                script.exit()
    else:
        telhados = list(FilteredElementCollector(doc, view.Id).OfClass(RoofBase).WhereElementIsNotElementType().ToElements())

    telhados = [rf for rf in telhados if not de._eh_vidraca_inclinada(rf)]
    if not telhados:
        forms.alert("Nenhum Telhado sólido (sem Vidraça Inclinada) foi encontrado nesta vista.", title="Inclinação de Telhado")
        script.exit()

    org   = view.Origin
    rdir  = view.RightDirection
    upv   = view.UpDirection
    vdir  = view.ViewDirection
    esc_v = max(1.0, float(view.Scale or 50))
    off_ft = op["it_off"] * MM * esc_v
    len_ft = op["it_len"] * MM * esc_v
    head_ft = 2.6 * MM * esc_v
    txt_gap_ft = 2.8 * MM * esc_v

    ttype = op["text_type"]
    min_w = TextNote.GetMinimumAllowedWidth(doc, ttype.Id)
    note_w = max(min_w, len_ft * 1.4)

    criadas = 0
    vistos_mid = []

    with Transaction(doc, u"Inclinação de Telhado no Corte") as t:
        t.Start()
        _garantir_sketch_plane_secao(view)

        for roof in telhados:
            for fc, _rf in de._faces_superiores_telhado(roof, view):
                if not isinstance(fc, PlanarFace):
                    continue
                n = fc.FaceNormal
                pct, deg = _calcular_inclinacao_face(n.X, n.Y, n.Z)
                if pct < 0.2:
                    continue

                p_mid = _centro_visivel_face_telhado(fc, view)
                if p_mid is None:
                    continue

                # Projeta p_mid para o plano da vista
                u_m, v_m = _uv_em_vista(p_mid, org, rdir, upv)
                if any(math.hypot(u_m - u0, v_m - v0) < 0.40 / 0.3048 for u0, v0 in vistos_mid):
                    continue
                vistos_mid.append((u_m, v_m))
                p_base = org + rdir * u_m + upv * v_m

                # Direcao do caimento no plano da vista
                t_cut = n.CrossProduct(vdir)
                tu = t_cut.DotProduct(rdir)
                tv = t_cut.DotProduct(upv)
                tmag = math.hypot(tu, tv)
                if tmag < 1e-6:
                    tu, tv = 1.0, 0.0
                else:
                    tu, tv = tu / tmag, tv / tmag

                eh_horizontal_em_corte = (abs(tv) < 0.02)
                if not eh_horizontal_em_corte:
                    if op["it_dir"] == 0 and tv > 0.0:
                        tu, tv = -tu, -tv
                    elif op["it_dir"] == 1 and tv < 0.0:
                        tu, tv = -tu, -tv
                elif tu < 0.0:
                    tu = -tu

                dir_v = rdir * tu + upv * tv
                # Normal externa apontando para cima na vista
                nu, nv = -tv, tu
                if nv < 0.0:
                    nu, nv = -nu, -nv
                out_v = rdir * nu + upv * nv

                p_ctr = p_base + out_v * off_ft

                # Desenha simbolo (Seta ou Triangulo)
                if op["it_est"] == 0:
                    p_ini = p_ctr - dir_v * (0.5 * len_ft)
                    p_fim = p_ctr + dir_v * (0.5 * len_ft)
                    doc.Create.NewDetailCurve(view, Line.CreateBound(p_ini, p_fim))
                    asa_1 = p_fim - dir_v * head_ft + out_v * (0.45 * head_ft)
                    asa_2 = p_fim - dir_v * head_ft - out_v * (0.45 * head_ft)
                    doc.Create.NewDetailCurve(view, Line.CreateBound(p_fim, asa_1))
                    doc.Create.NewDetailCurve(view, Line.CreateBound(p_fim, asa_2))
                elif op["it_est"] == 1:
                    h_ratio = abs(tv / tu) if abs(tu) > 0.05 else (pct / 100.0)
                    b_len = len_ft * 0.85
                    h_len = max(2.0 * MM * esc_v, min(b_len * 0.75, b_len * h_ratio))
                    sinal_u = 1.0 if (tu * tv >= 0.0) else -1.0
                    # Cateto horizontal + vertical fechando triangulo acima do plano
                    p_esq = p_ctr - rdir * (0.5 * b_len)
                    p_dir = p_ctr + rdir * (0.5 * b_len)
                    if sinal_u > 0.0:
                        p_hip_a, p_hip_b = p_esq, p_dir + upv * h_len
                        p_cant = p_dir
                    else:
                        p_hip_a, p_hip_b = p_esq + upv * h_len, p_dir
                        p_cant = p_esq
                    doc.Create.NewDetailCurve(view, Line.CreateBound(p_esq, p_dir))
                    doc.Create.NewDetailCurve(view, Line.CreateBound(p_cant, p_hip_b if sinal_u > 0 else p_hip_a))
                    doc.Create.NewDetailCurve(view, Line.CreateBound(p_hip_a, p_hip_b))

                # Texto alinhado
                rot_rad, _, _ = _angulo_texto_legivel(tu, tv)
                lbl = _formatar_texto_inclinacao(pct, deg, op["it_fmt"])
                txt_off = txt_gap_ft + (2.0 * MM * esc_v if op["it_est"] == 1 else 0.0)
                p_txt = p_ctr + out_v * txt_off

                tn_opts = TextNoteOptions(ttype.Id)
                tn_opts.HorizontalAlignment = HorizontalTextAlignment.Center
                tn_opts.Rotation = rot_rad
                try:
                    tn_opts.KeepRotatedTextReadable = True
                except Exception:
                    pass
                TextNote.Create(doc, view.Id, p_txt, note_w, lbl, tn_opts)
                criadas += 1

        t.Commit()

    if criadas:
        forms.alert(u"Inclinações aplicadas em {} água(s) de telhado!".format(criadas), title="Inclinação de Telhado")
    else:
        forms.alert(u"Nenhuma água inclinada de telhado sólido visível neste corte (coberturas planas 0% e vidraças inclinadas são ignoradas).", title="Inclinação de Telhado")


# ==============================================================================
# 5. COTAR CORTE COMPLETO AUTOMATICO (1 CLIQUE — ESTILO PLANTAS)
# ==============================================================================

XAML_CORTE_AUTO = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Configurações — Cota Automática no Corte" Width="510" SizeToContent="Height"
        WindowStartupLocation="CenterScreen" ResizeMode="NoResize" Background="#F7F8FA">
    <StackPanel Margin="14">
        <Border Background="#EBF3FC" BorderBrush="#B8D4F5" BorderThickness="1" CornerRadius="4" Padding="9" Margin="0,0,0,10">
            <TextBlock TextWrapping="Wrap" FontWeight="SemiBold" Foreground="#1F3A57"
                       Text="Cota todo o Corte na vertical em 1 clique: verticais externas (lajes/forro/cumeeira + altura total), pés-direitos internos e esquadrias."/>
        </Border>

        <Border Background="White" BorderBrush="#D0D7DE" BorderThickness="1" CornerRadius="4" Padding="10" Margin="0,0,0,8">
            <StackPanel>
                <TextBlock Text="Cotas verticais a gerar automaticamente:" FontWeight="SemiBold" Margin="0,0,0,6"/>
                <CheckBox x:Name="ck_ca_vert"  Content="Cotas Verticais Externas (pisos/lajes/forro/cumeeira + Altura Total)" IsChecked="True" Margin="0,0,0,5"/>
                <CheckBox x:Name="ck_ca_pd"    Content="Pé-Direitos Internos (Ambientes / vãos até Forro, Laje ou Madeiramento)" IsChecked="True" Margin="0,0,0,5"/>
                <CheckBox x:Name="ck_ca_esq"   Content="Cotas de Esquadrias visíveis (Peitoril + Altura + Boneca)" IsChecked="True"/>
            </StackPanel>
        </Border>

        <Border Background="White" BorderBrush="#D0D7DE" BorderThickness="1" CornerRadius="4" Padding="10" Margin="0,0,0,10">
            <Grid>
                <Grid.ColumnDefinitions>
                    <ColumnDefinition Width="195"/>
                    <ColumnDefinition Width="*"/>
                </Grid.ColumnDefinitions>
                <Grid.RowDefinitions>
                    <RowDefinition Height="28"/>
                    <RowDefinition Height="28"/>
                </Grid.RowDefinitions>

                <TextBlock Grid.Row="0" Grid.Column="0" Text="Lado da cota vertical externa:" VerticalAlignment="Center"/>
                <ComboBox  Grid.Row="0" Grid.Column="1" x:Name="cb_ca_lado" Height="24"/>

                <TextBlock Grid.Row="1" Grid.Column="0" Text="Tipo de cota linear:" VerticalAlignment="Center"/>
                <ComboBox  Grid.Row="1" Grid.Column="1" x:Name="cb_ca_tipo" Height="24"/>
            </Grid>
        </Border>

        <StackPanel Orientation="Horizontal" HorizontalAlignment="Right">
            <Button x:Name="btn_cancel" Content="Cancelar" Width="95" Height="30" Margin="0,0,8,0"/>
            <Button x:Name="btn_ok" Content="Salvar e Cotar Corte" Width="170" Height="30"
                    IsDefault="True" Background="#0078D4" Foreground="White" FontWeight="SemiBold"/>
        </StackPanel>
    </StackPanel>
</Window>
"""


class CotarCorteAutoWindow(forms.WPFWindow):
    def __init__(self, prefs, dim_types):
        forms.WPFWindow.__init__(self, XAML_CORTE_AUTO, literal_string=True)
        self.confirmado = False
        self.resultado  = {}
        self._dim_types = dim_types

        self._ck_v    = self.FindName("ck_ca_vert")
        self._ck_pd   = self.FindName("ck_ca_pd")
        self._ck_esq  = self.FindName("ck_ca_esq")
        self._cb_lado = self.FindName("cb_ca_lado")
        self._cb_tipo = self.FindName("cb_ca_tipo")

        for item in (
            u"Automático (lado com mais espaço na vista)",
            u"Sempre à direita",
            u"Sempre à esquerda",
        ):
            self._cb_lado.Items.Add(item)
        self._cb_lado.SelectedIndex = max(0, min(2, int(prefs.get("ca_lado", 0) or 0)))

        for dt in dim_types:
            self._cb_tipo.Items.Add(de._elem_name(dt))
        self._cb_tipo.SelectedIndex = max(0, min(max(0, len(dim_types) - 1), int(prefs.get("ca_tipo", 0) or 0)))

        self._ck_v.IsChecked   = bool(prefs.get("ca_vert", True))
        self._ck_pd.IsChecked  = bool(prefs.get("ca_pd", True))
        self._ck_esq.IsChecked = bool(prefs.get("ca_esq", True))

        self.FindName("btn_ok").Click     += self.on_ok
        self.FindName("btn_cancel").Click += self.on_cancel

    def on_cancel(self, sender, args):
        self.confirmado = False
        self.Close()

    def on_ok(self, sender, args):
        if not (self._ck_v.IsChecked or self._ck_pd.IsChecked or self._ck_esq.IsChecked):
            forms.alert("Marque ao menos uma opção de cota.", title="Aviso")
            return
        idx_t = max(0, self._cb_tipo.SelectedIndex)
        self.resultado = {
            "ca_vert":  bool(self._ck_v.IsChecked),
            "ca_pd":    bool(self._ck_pd.IsChecked),
            "ca_esq":   bool(self._ck_esq.IsChecked),
            "ca_lado":  max(0, self._cb_lado.SelectedIndex),
            "ca_tipo":  idx_t,
            "dim_type": self._dim_types[idx_t] if self._dim_types else None,
        }
        self.confirmado = True
        self.Close()


def _limites_modelo_corte(v_view, crop_uv):
    """Retorna (u_min, u_max, v_min, v_max, walls_cortadas, cols_cortados) visiveis no corte."""
    us_lo, us_hi, vs_lo, vs_hi = [], [], [], []
    walls_c = []
    cols_c  = []

    for wl in FilteredElementCollector(doc, v_view.Id).OfClass(Wall).WhereElementIsNotElementType().ToElements():
        if not _parede_corta_plano_secao(wl, v_view):
            continue
        bb_uv = _bb_uv_em_vista(wl, v_view)
        if bb_uv is None:
            continue
        u0, u1, v0, v1, _ = bb_uv
        if crop_uv is not None and (u1 < crop_uv[0] or u0 > crop_uv[1] or v1 < crop_uv[2] or v0 > crop_uv[3]):
            continue
        walls_c.append((wl, bb_uv))
        us_lo.append(u0); us_hi.append(u1); vs_lo.append(v0); vs_hi.append(v1)

    for cat in (BuiltInCategory.OST_StructuralColumns, BuiltInCategory.OST_StructuralFraming):
        for el in FilteredElementCollector(doc, v_view.Id).OfCategory(cat).WhereElementIsNotElementType().ToElements():
            bb_uv = _bb_uv_em_vista(el, v_view)
            if bb_uv is None:
                continue
            u0, u1, v0, v1, _ = bb_uv
            if crop_uv is not None and (u1 < crop_uv[0] or u0 > crop_uv[1] or v1 < crop_uv[2] or v0 > crop_uv[3]):
                continue
            if cat == BuiltInCategory.OST_StructuralColumns:
                cols_c.append((el, bb_uv))
            us_lo.append(u0); us_hi.append(u1); vs_lo.append(v0); vs_hi.append(v1)

    for cl_t in (Floor, Ceiling, RoofBase):
        for el in FilteredElementCollector(doc, v_view.Id).OfClass(cl_t).WhereElementIsNotElementType().ToElements():
            bb_uv = _bb_uv_em_vista(el, v_view)
            if bb_uv is None:
                continue
            u0, u1, v0, v1, _ = bb_uv
            if crop_uv is not None and (u1 < crop_uv[0] or u0 > crop_uv[1] or v1 < crop_uv[2] or v0 > crop_uv[3]):
                continue
            us_lo.append(u0); us_hi.append(u1); vs_lo.append(v0); vs_hi.append(v1)

    if not us_lo:
        return None
    return (min(us_lo), max(us_hi), min(vs_lo), max(vs_hi), walls_c, cols_c)


def run_cotar_corte_auto(abrir_config=False):
    _verificar_view_section(u"Cota Automática no Corte")
    dim_types = _listar_tipos_cota_linear()
    prefs = _carregar_prefs()

    if abrir_config:
        win = CotarCorteAutoWindow(prefs, dim_types)
        win.ShowDialog()
        if not win.confirmado:
            script.exit()
        op = win.resultado
        _salvar_prefs(dict((k, v) for k, v in op.items() if k != "dim_type"))
    else:
        idx_t = max(0, min(max(0, len(dim_types) - 1), int(prefs.get("ca_tipo", 0) or 0)))
        op = {
            "ca_vert":  bool(prefs.get("ca_vert", True)),
            "ca_pd":    bool(prefs.get("ca_pd", True)),
            "ca_esq":   bool(prefs.get("ca_esq", True)),
            "ca_lado":  max(0, min(2, int(prefs.get("ca_lado", 0) or 0))),
            "ca_tipo":  idx_t,
            "dim_type": dim_types[idx_t] if dim_types else None,
        }

    crop_uv = _crop_uv_em_vista(view)
    lim = _limites_modelo_corte(view, crop_uv)
    if lim is None:
        forms.alert("Nenhum elemento cortado ou visível encontrado neste Corte/Elevação.", title="Cota Automática no Corte")
        script.exit()

    u_min_m, u_max_m, v_min_m, v_max_m, walls_c, _ = lim
    floors, ceilings = _coletar_pisos_forros_no_corte(view)

    org   = view.Origin
    rdir  = view.RightDirection
    upv   = view.UpDirection
    esc_v = max(1.0, float(view.Scale or 50))
    dim_t = op["dim_type"]

    cnt_v = 0
    cnt_pd = 0
    cnt_eq = 0

    with Transaction(doc, u"Cota Automática no Corte") as t:
        t.Start()

        # 1. Cotas Verticais Externas (pisos/lajes/forro/topo parede/telhado + Altura Total)
        if op["ca_vert"]:
            usar_dir = True
            if op["ca_lado"] == 2:
                usar_dir = False
            elif op["ca_lado"] == 0 and crop_uv is not None:
                folga_dir = crop_uv[1] - u_max_m
                folga_esq = u_min_m - crop_uv[0]
                usar_dir = (folga_dir >= folga_esq)

            if usar_dir:
                u_v1 = u_max_m + 6.0 * MM * esc_v
                u_v2 = u_v1 + 6.0 * MM * esc_v
                u_probe_ext = u_max_m
            else:
                u_v1 = u_min_m - 6.0 * MM * esc_v
                u_v2 = u_v1 - 6.0 * MM * esc_v
                u_probe_ext = u_min_m

            refs_v = []
            # Pisos/Lajes cortados (base e topo) proximos a lateral ou cruzando
            for fl in floors:
                for key in ("bot", "top"):
                    it = fl.get(key)
                    if it is not None and (v_min_m - 0.5 * FT <= it[0] <= v_max_m + 0.5 * FT):
                        refs_v.append((it[0], it[2], "Fl"))
            # Forros
            for cl in ceilings:
                it = cl.get("bot")
                if it is not None and (v_min_m <= it[0] <= v_max_m):
                    refs_v.append((it[0], it[2], "Ceil"))
            # Topos das paredes externas/principais cortadas
            for wl, bb_uv in walls_c:
                tops_w = [it for it in _faces_normais_direcao(wl, view, upv, min_dot=0.985) if it[1].FaceNormal.Z > 0.9]
                if tops_w:
                    vc, _, rf = max(tops_w, key=lambda x: x[0])
                    refs_v.append((vc, rf, "WTop"))
            # Beiral e Cumeeira de Telhados solidos
            roof_ext = _coletar_refs_cobertura_vertical(view, u_probe_ext, max(3.5 * FT, 0.5 * (u_max_m - u_min_m)), v_min_m, v_max_m + 1.0 * FT, incluir_madeira=False)
            if roof_ext:
                roof_h = [x for x in roof_ext if x[2].endswith("Face") or x[2].endswith("Edge")]
                refs_v.extend(roof_h)
                refs_v.append(min(roof_ext, key=lambda x: x[0]))
                refs_v.append(max(roof_ext, key=lambda x: x[0]))

            refs_v = _dedup_refs_por_coord(refs_v, min_gap=25.0 * MM)
            if len(refs_v) >= 2:
                v0_v, v1_v = refs_v[0][0], refs_v[-1][0]
                if len(refs_v) > 2:
                    ln_vc = Line.CreateBound(org + rdir * u_v1 + upv * v0_v, org + rdir * u_v1 + upv * v1_v)
                    if _criar_dim_segura(view, ln_vc, refs_v, dim_type=dim_t, min_gap=25.0 * MM) is not None:
                        cnt_v += 1
                    ln_vt = Line.CreateBound(org + rdir * u_v2 + upv * v0_v, org + rdir * u_v2 + upv * v1_v)
                    if _criar_dim_segura(view, ln_vt, [refs_v[0], refs_v[-1]], dim_type=dim_t, min_gap=25.0 * MM) is not None:
                        cnt_v += 1
                else:
                    ln_vt = Line.CreateBound(org + rdir * u_v1 + upv * v0_v, org + rdir * u_v1 + upv * v1_v)
                    if _criar_dim_segura(view, ln_vt, [refs_v[0], refs_v[-1]], dim_type=dim_t, min_gap=25.0 * MM) is not None:
                        cnt_v += 1

        # 3. Pe-Direitos Internos por Ambiente / Vao
        if op["ca_pd"]:
            op_pd = _resolver_opcoes_pe_direito(prefs, dim_types, abrir_config=False)
            if dim_t is not None:
                op_pd["dim_type"] = dim_t
            cnt_pd, _, _ = _executar_cotas_pe_direito(op_pd, floors=floors, ceilings=ceilings, rooms_pre_filtro=None)

        # 4. Cotas de Esquadrias Visiveis
        if op["ca_esq"]:
            op_eq = _resolver_opcoes_esquadrias(prefs, dim_types, abrir_config=False)
            if dim_t is not None:
                op_eq["dim_type"] = dim_t
            cnt_eq, _, _ = _executar_cotas_esquadrias(op_eq, floors=floors, ceilings=ceilings, esq_pre=None)

        t.Commit()

    if (cnt_v + cnt_pd + cnt_eq) == 0:
        forms.alert(u"Nenhuma cota automática pôde ser criada nesta vista.", title="Cota Automática no Corte")
    else:
        resumo = u"Corte cotado automaticamente!\n\n"
        if op["ca_vert"]:
            resumo += u"✔ Cotas Verticais Externas (Níveis/Total): {}\n".format(cnt_v)
        if op["ca_pd"]:
            resumo += u"✔ Cotas de Pé-Direito Interno: {}\n".format(cnt_pd)
        if op["ca_esq"]:
            resumo += u"✔ Cotas de Esquadrias: {}".format(cnt_eq)
        forms.alert(resumo, title="AutoBIM: Cota Automática no Corte")


# ==============================================================================
# 6. COTAR ELEMENTOS SELECIONADOS NO CORTE (ESTILO COTAR PAREDES)
# ==============================================================================

XAML_SELECAO_CORTE = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Configurações — Cotar Seleção no Corte" Width="530" SizeToContent="Height"
        WindowStartupLocation="CenterScreen" ResizeMode="NoResize" Background="#F7F8FA">
    <StackPanel Margin="14">
        <Border Background="#EBF3FC" BorderBrush="#B8D4F5" BorderThickness="1" CornerRadius="4" Padding="9" Margin="0,0,0,10">
            <TextBlock TextWrapping="Wrap" FontWeight="SemiBold" Foreground="#1F3A57"
                       Text="Selecione (ou pré-selecione com caixa) pisos, forros, telhados, vigas ou paredes no corte e leve a cadeia vertical + altura total automaticamente para fora do desenho."/>
        </Border>

        <Border Background="White" BorderBrush="#D0D7DE" BorderThickness="1" CornerRadius="4" Padding="10" Margin="0,0,0,8">
            <StackPanel>
                <TextBlock Text="Comportamento da cadeia vertical:" FontWeight="SemiBold" Margin="0,0,0,6"/>
                <CheckBox x:Name="ck_sc_tot"          Content="Adicionar linha paralela de Altura Total externa" IsChecked="True" Margin="0,0,0,5"/>
                <CheckBox x:Name="ck_sc_mesclar_piso" Content="Agrupar camadas contíguas de Piso acabado + Laje (espessura única)" IsChecked="True" Margin="0,0,0,5"/>
                <CheckBox x:Name="ck_sc_esq"          Content="Incluir Portas e Janelas selecionadas na caixa de seleção" IsChecked="False"/>
            </StackPanel>
        </Border>

        <Border Background="White" BorderBrush="#D0D7DE" BorderThickness="1" CornerRadius="4" Padding="10" Margin="0,0,0,10">
            <Grid>
                <Grid.ColumnDefinitions>
                    <ColumnDefinition Width="200"/>
                    <ColumnDefinition Width="*"/>
                </Grid.ColumnDefinitions>
                <Grid.RowDefinitions>
                    <RowDefinition Height="28"/>
                    <RowDefinition Height="28"/>
                </Grid.RowDefinitions>

                <TextBlock Grid.Row="0" Grid.Column="0" Text="Lado da cota vertical:" VerticalAlignment="Center"/>
                <ComboBox  Grid.Row="0" Grid.Column="1" x:Name="cb_sc_lado_v" Height="24"/>

                <TextBlock Grid.Row="1" Grid.Column="0" Text="Tipo de cota linear:" VerticalAlignment="Center"/>
                <ComboBox  Grid.Row="1" Grid.Column="1" x:Name="cb_sc_tipo" Height="24"/>
            </Grid>
        </Border>

        <StackPanel Orientation="Horizontal" HorizontalAlignment="Right">
            <Button x:Name="btn_cancel" Content="Cancelar" Width="95" Height="30" Margin="0,0,8,0"/>
            <Button x:Name="btn_ok" Content="Salvar e Selecionar" Width="170" Height="30"
                    IsDefault="True" Background="#0078D4" Foreground="White" FontWeight="SemiBold"/>
        </StackPanel>
    </StackPanel>
</Window>
"""


class CotarSelecaoCorteWindow(forms.WPFWindow):
    def __init__(self, prefs, dim_types):
        forms.WPFWindow.__init__(self, XAML_SELECAO_CORTE, literal_string=True)
        self.confirmado = False
        self.resultado  = {}
        self._dim_types = dim_types

        self._ck_tot = self.FindName("ck_sc_tot")
        self._ck_mp  = self.FindName("ck_sc_mesclar_piso")
        self._ck_esq = self.FindName("ck_sc_esq")
        self._cb_lv  = self.FindName("cb_sc_lado_v")
        self._cb_tp  = self.FindName("cb_sc_tipo")

        for it in (
            u"Automático (lado mais próximo da seleção / com mais espaço)",
            u"Sempre à direita do desenho",
            u"Sempre à esquerda do desenho",
        ):
            self._cb_lv.Items.Add(it)
        self._cb_lv.SelectedIndex = max(0, min(2, int(prefs.get("sc_lado_v", 0) or 0)))

        for dt in dim_types:
            self._cb_tp.Items.Add(de._elem_name(dt))
        self._cb_tp.SelectedIndex = max(0, min(max(0, len(dim_types) - 1), int(prefs.get("sc_tipo", 0) or 0)))

        self._ck_tot.IsChecked = bool(prefs.get("sc_tot", True))
        self._ck_mp.IsChecked  = bool(prefs.get("sc_mesclar_piso", True))
        self._ck_esq.IsChecked = bool(prefs.get("sc_esq", False))

        self.FindName("btn_ok").Click     += self.on_ok
        self.FindName("btn_cancel").Click += self.on_cancel

    def on_cancel(self, sender, args):
        self.confirmado = False
        self.Close()

    def on_ok(self, sender, args):
        idx_t = max(0, self._cb_tp.SelectedIndex)
        self.resultado = {
            "sc_tot":          bool(self._ck_tot.IsChecked),
            "sc_mesclar_piso": bool(self._ck_mp.IsChecked),
            "sc_esq":          bool(self._ck_esq.IsChecked),
            "sc_lado_v":       max(0, self._cb_lv.SelectedIndex),
            "sc_tipo":         idx_t,
            "dim_type":        self._dim_types[idx_t] if self._dim_types else None,
        }
        self.confirmado = True
        self.Close()


_CATS_SELECAO_CORTE_BASE = set([
    int(BuiltInCategory.OST_Walls),
    int(BuiltInCategory.OST_Floors),
    int(BuiltInCategory.OST_Ceilings),
    int(BuiltInCategory.OST_Roofs),
    int(BuiltInCategory.OST_StructuralFraming),
    int(BuiltInCategory.OST_StructuralColumns),
    int(BuiltInCategory.OST_Columns),
    int(BuiltInCategory.OST_GenericModel),
    int(BuiltInCategory.OST_Stairs),
    int(BuiltInCategory.OST_Ramps),
    int(BuiltInCategory.OST_Parts),
    int(BuiltInCategory.OST_CurtainWallMullions),
    int(BuiltInCategory.OST_CurtainWallPanels),
])

_CATS_SELECAO_CORTE_ESQ = set([
    int(BuiltInCategory.OST_Doors),
    int(BuiltInCategory.OST_Windows),
])


def _eh_elemento_cotavel_corte(el, incluir_esq=False):
    if el is None:
        return False
    cat = getattr(el, "Category", None)
    if cat is None:
        return False
    cid = _eid_int(cat.Id)
    if cid in _CATS_SELECAO_CORTE_ESQ:
        if not incluir_esq:
            return False
        return isinstance(el, FamilyInstance) and getattr(el, "SuperComponent", None) is None
    if cid not in _CATS_SELECAO_CORTE_BASE:
        return False
    if isinstance(el, RoofBase) and de._eh_vidraca_inclinada(el):
        return False
    if cid in (int(BuiltInCategory.OST_StructuralFraming), int(BuiltInCategory.OST_CurtainWallMullions)):
        try:
            pc = el.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
            if pc is not None and pc.AsString() and u"ripa" in unicode(pc.AsString()).lower():
                return False
        except Exception:
            pass
    return True


class _SelecaoCorteFilter(ISelectionFilter):
    def __init__(self, incluir_esq=False):
        self._incluir_esq = bool(incluir_esq)

    def AllowElement(self, elem):
        return _eh_elemento_cotavel_corte(elem, incluir_esq=self._incluir_esq)

    def AllowReference(self, reference, position):
        return False


def _mesclar_pisos_selecionados(piso_itens, tol_gap=0.05 / 0.3048, tol_u=0.05 / 0.3048):
    """Agrupa camadas contiguas de pisos selecionados quando sobrepoem em U e tocam em Z."""
    if not piso_itens:
        return []
    ord_it = sorted(piso_itens, key=lambda x: x[2])
    clusters = []
    for u_lo, u_hi, z_lo, rfb, z_hi, rft in ord_it:
        u0, u1 = min(u_lo, u_hi), max(u_lo, u_hi)
        fundido = False
        for cl in clusters:
            overlap_u = (min(u1, cl[1]) >= max(u0, cl[0]) - tol_u)
            touch_z   = (z_lo <= cl[4] + tol_gap and z_hi >= cl[2] - tol_gap)
            if overlap_u and touch_z:
                cl[0] = min(cl[0], u0)
                cl[1] = max(cl[1], u1)
                if rfb is not None and (cl[3] is None or z_lo < cl[2]):
                    cl[2] = z_lo
                    cl[3] = rfb
                elif z_lo < cl[2]:
                    cl[2] = z_lo
                if rft is not None and (cl[5] is None or z_hi > cl[4]):
                    cl[4] = z_hi
                    cl[5] = rft
                elif z_hi > cl[4]:
                    cl[4] = z_hi
                fundido = True
                break
        if not fundido:
            clusters.append([u0, u1, z_lo, rfb, z_hi, rft])

    res = []
    for _, _, zb, rfb, zt, rft in clusters:
        if rfb is not None:
            res.append((zb, rfb, "LajeBase"))
        if rft is not None:
            res.append((zt, rft, "PisoAcabTop"))
    return res


def _coletar_dims_existentes_uv(v_view, rdir, upv, org):
    """Retorna {'h': [(v_coord, u0, u1), ...], 'v': [(u_coord, v0, v1), ...]} de cotas lineares na vista."""
    res = {"h": [], "v": []}
    for dm in FilteredElementCollector(doc, v_view.Id).OfClass(Dimension).WhereElementIsNotElementType().ToElements():
        try:
            crv = getattr(dm, "Curve", None)
            if not isinstance(crv, Line):
                continue
            ddir = crv.Direction
            u_c, v_c = _uv_em_vista(crv.Origin, org, rdir, upv)
            bb_uv = _bb_uv_em_vista(dm, v_view)
            if abs(ddir.DotProduct(rdir)) >= 0.98:
                u0 = bb_uv[0] if bb_uv else (u_c - 5.0 * FT)
                u1 = bb_uv[1] if bb_uv else (u_c + 5.0 * FT)
                res["h"].append((v_c, u0, u1))
            elif abs(ddir.DotProduct(upv)) >= 0.98:
                v0 = bb_uv[2] if bb_uv else (v_c - 5.0 * FT)
                v1 = bb_uv[3] if bb_uv else (v_c + 5.0 * FT)
                res["v"].append((u_c, v0, v1))
        except Exception:
            pass
    return res


def run_cotar_selecao_corte(abrir_config=False):
    _verificar_view_section(u"Cotar Seleção no Corte")
    dim_types = _listar_tipos_cota_linear()
    prefs = _carregar_prefs()

    if abrir_config:
        win = CotarSelecaoCorteWindow(prefs, dim_types)
        win.ShowDialog()
        if not win.confirmado:
            script.exit()
        op = win.resultado
        _salvar_prefs(dict((k, v) for k, v in op.items() if k != "dim_type"))
    else:
        idx_t = max(0, min(max(0, len(dim_types) - 1), int(prefs.get("sc_tipo", 0) or 0)))
        op = {
            "sc_tot":          bool(prefs.get("sc_tot", True)),
            "sc_mesclar_piso": bool(prefs.get("sc_mesclar_piso", True)),
            "sc_esq":          bool(prefs.get("sc_esq", False)),
            "sc_lado_v":       max(0, min(2, int(prefs.get("sc_lado_v", 0) or 0))),
            "sc_tipo":         idx_t,
            "dim_type":        dim_types[idx_t] if dim_types else None,
        }

    # Etapa 1: Pré-seleção ou caixa de seleção (igual ao Cotar Paredes Selecionadas)
    pre_ids = list(uidoc.Selection.GetElementIds())
    elementos = []
    if pre_ids:
        for eid in pre_ids:
            el = doc.GetElement(eid)
            if _eh_elemento_cotavel_corte(el, incluir_esq=True):
                elementos.append(el)

    if not elementos:
        try:
            with forms.WarningBar(title=u"Selecione os elementos no Corte (ex.: caixa de seleção) e clique em Concluir (Finish)"):
                refs_pick = uidoc.Selection.PickObjects(
                    ObjectType.Element,
                    _SelecaoCorteFilter(incluir_esq=op["sc_esq"]),
                    u"Selecione paredes, pisos, forros, telhados, vigas ou pilares e clique em Finish"
                )
            for rf in (refs_pick or []):
                el = doc.GetElement(rf.ElementId)
                if el is not None:
                    elementos.append(el)
        except Exception:
            script.exit()

    if not elementos:
        forms.alert(u"Nenhum elemento válido selecionado no Corte.", title="Cotar Seleção no Corte")
        script.exit()

    # Remover duplicatas preservando ordem
    vistos_id = set()
    uniq_els = []
    for el in elementos:
        eid_i = _eid_int(el.Id)
        if eid_i not in vistos_id:
            vistos_id.add(eid_i)
            uniq_els.append(el)
    elementos = uniq_els

    # Classificar elementos selecionados
    sel_walls, sel_cols, sel_floors, sel_ceilings = [], [], [], []
    sel_roofs, sel_framing, sel_esq, sel_other = [], [], [], []
    us_s0, us_s1, vs_s0, vs_s1 = [], [], [], []

    for el in elementos:
        bb_uv = _bb_uv_em_vista(el, view)
        if bb_uv is not None:
            us_s0.append(bb_uv[0]); us_s1.append(bb_uv[1])
            vs_s0.append(bb_uv[2]); vs_s1.append(bb_uv[3])

        cid = _eid_int(el.Category.Id) if getattr(el, "Category", None) is not None else -1
        if isinstance(el, Wall):
            sel_walls.append(el)
        elif isinstance(el, Floor):
            sel_floors.append((el, bb_uv))
        elif isinstance(el, Ceiling):
            sel_ceilings.append((el, bb_uv))
        elif isinstance(el, RoofBase):
            sel_roofs.append(el)
        elif cid in _CATS_SELECAO_CORTE_ESQ:
            sel_esq.append(el)
        elif cid == int(BuiltInCategory.OST_StructuralFraming):
            sel_framing.append(el)
        elif cid in (int(BuiltInCategory.OST_StructuralColumns), int(BuiltInCategory.OST_Columns)):
            sel_cols.append(el)
        else:
            sel_other.append(el)

    if not us_s0:
        forms.alert(u"Os elementos selecionados não possuem geometria visível nesta vista.", title="Cotar Seleção no Corte")
        script.exit()

    sel_uv = (min(us_s0), max(us_s1), min(vs_s0), max(vs_s1))
    crop_uv = _crop_uv_em_vista(view)
    lim_mod = _limites_modelo_corte(view, crop_uv)
    if lim_mod is not None:
        u_env_min = min(sel_uv[0], lim_mod[0])
        u_env_max = max(sel_uv[1], lim_mod[1])
        v_env_min = min(sel_uv[2], lim_mod[2])
        v_env_max = max(sel_uv[3], lim_mod[3])
    else:
        u_env_min, u_env_max, v_env_min, v_env_max = sel_uv

    # Prioriza paredes cortadas quando houver mistura com paredes ao fundo (se nenhuma cortada, usa todas)
    cut_walls = [w for w in sel_walls if _parede_corta_plano_secao(w, view)]
    walls_uso = cut_walls if cut_walls else sel_walls

    incluir_esq_agora = op["sc_esq"] or (
        bool(sel_esq) and not (sel_walls or sel_cols or sel_floors or sel_ceilings or sel_roofs or sel_framing or sel_other)
    )

    org   = view.Origin
    rdir  = view.RightDirection
    upv   = view.UpDirection
    esc_v = max(1.0, float(view.Scale or 50))
    dim_t = op["dim_type"]

    # --------------------------------------------------------------------------
    # Extracao de Referencias Verticais (Normal // upv + Telhado inclinado)
    # --------------------------------------------------------------------------
    refs_v = []
    piso_itens = []
    for fl, bb_uv in sel_floors:
        faces_h = _faces_normais_direcao(fl, view, upv, min_dot=0.985)
        if not faces_h:
            continue
        tops = [it for it in faces_h if it[1].FaceNormal.Z > 0.9]
        bots = [it for it in faces_h if it[1].FaceNormal.Z < -0.9]
        top_it = max(tops, key=lambda x: x[0]) if tops else None
        bot_it = min(bots, key=lambda x: x[0]) if bots else None
        u_lo = bb_uv[0] if bb_uv else 0.0
        u_hi = bb_uv[1] if bb_uv else 0.0
        z_lo = bot_it[0] if bot_it else top_it[0]
        z_hi = top_it[0] if top_it else bot_it[0]
        rfb  = bot_it[2] if bot_it else None
        rft  = top_it[2] if top_it else None
        piso_itens.append((u_lo, u_hi, z_lo, rfb, z_hi, rft))

    if op["sc_mesclar_piso"]:
        refs_v.extend(_mesclar_pisos_selecionados(piso_itens))
    else:
        for _, _, z_lo, rfb, z_hi, rft in piso_itens:
            if rfb is not None:
                refs_v.append((z_lo, rfb, "FlBot"))
            if rft is not None:
                refs_v.append((z_hi, rft, "FlTop"))

    so_forro = bool(sel_ceilings) and not (sel_floors or walls_uso or sel_cols or sel_framing or sel_roofs or sel_other)
    for cl, _ in sel_ceilings:
        faces_h = _faces_normais_direcao(cl, view, upv, min_dot=0.985)
        bots = [it for it in faces_h if it[1].FaceNormal.Z < -0.9]
        tops = [it for it in faces_h if it[1].FaceNormal.Z > 0.9]
        bot_it = min(bots, key=lambda x: x[0]) if bots else None
        top_it = max(tops, key=lambda x: x[0]) if tops else None
        if bot_it is not None:
            refs_v.append((bot_it[0], bot_it[2], "CeilBot"))
        if top_it is not None and (so_forro or bot_it is None or abs(top_it[0] - bot_it[0]) >= 25.0 * MM):
            refs_v.append((top_it[0], top_it[2], "CeilTop"))

    # Paredes: apenas base e topo extremos de cada parede (ignora peitoris internos de abertura)
    for wl in walls_uso:
        faces_h = _faces_normais_direcao(wl, view, upv, min_dot=0.985)
        bots = [it for it in faces_h if it[1].FaceNormal.Z < -0.9]
        tops = [it for it in faces_h if it[1].FaceNormal.Z > 0.9]
        if bots:
            b_it = min(bots, key=lambda x: x[0])
            refs_v.append((b_it[0], b_it[2], "WallBot"))
        if tops:
            t_it = max(tops, key=lambda x: x[0])
            refs_v.append((t_it[0], t_it[2], "WallTop"))

    for el in (sel_framing + sel_cols + sel_other):
        faces_h = _faces_normais_direcao(el, view, upv, min_dot=0.985)
        if faces_h:
            faces_h.sort(key=lambda x: x[0])
            refs_v.append((faces_h[0][0], faces_h[0][2], "ElemBot"))
            refs_v.append((faces_h[-1][0], faces_h[-1][2], "ElemTop"))

    opts_r = Options()
    opts_r.ComputeReferences = True
    opts_r.View = view
    try:
        opts_r.DetailLevel = ViewDetailLevel.Fine
    except Exception:
        pass
    for rf_el in sel_roofs:
        try:
            rc = _extrair_refs_geom_cobertura_vertical(
                rf_el.get_Geometry(opts_r), "Roof", org, rdir, upv, u_alvo=None
            )
            if rc:
                rc_h = [x for x in rc if x[2].endswith("Face") or x[2].endswith("Edge")]
                refs_v.extend(rc_h)
                refs_v.append(min(rc, key=lambda x: x[0]))
                refs_v.append(max(rc, key=lambda x: x[0]))
        except Exception:
            pass

    if incluir_esq_agora:
        for fi in sel_esq:
            vb, rfb, vt, rft = _refs_verticais_esquadria(fi, view)
            if rfb is not None:
                refs_v.append((vb, rfb, "EsqBot"))
            if rft is not None:
                refs_v.append((vt, rft, "EsqTop"))

    refs_v = _dedup_refs_por_coord(refs_v, min_gap=20.0 * MM)

    if len(refs_v) < 2:
        forms.alert(
            u"Não foram encontradas pelo menos 2 faces horizontais válidas nos elementos selecionados para criar a cota vertical.",
            title="Cotar Seleção no Corte"
        )
        script.exit()

    dims_ex = _coletar_dims_existentes_uv(view, rdir, upv, org)
    gap_ext  = 6.5 * MM * esc_v
    step_ext = 6.0 * MM * esc_v

    cnt_v = 0

    with Transaction(doc, u"Cotar Seleção no Corte") as t:
        t.Start()

        # Cadeia Vertical + Altura Total fora do desenho
        v0_v, v1_v = refs_v[0][0], refs_v[-1][0]
        usar_dir = True
        if op["sc_lado_v"] == 2:
            usar_dir = False
        elif op["sc_lado_v"] == 0:
            env_w = max(1e-3, u_env_max - u_env_min)
            sel_w = sel_uv[1] - sel_uv[0]
            if sel_w < 0.65 * env_w:
                sel_mid_u = 0.5 * (sel_uv[0] + sel_uv[1])
                usar_dir = (abs(u_env_max - sel_mid_u) <= abs(sel_mid_u - u_env_min))
            elif crop_uv is not None:
                usar_dir = ((crop_uv[1] - u_env_max) >= (u_env_min - crop_uv[0]))

        dims_v_rel = [uc for uc, va, vb in dims_ex["v"] if min(vb, v1_v) >= max(va, v0_v) - 1.0 * FT]
        if usar_dir:
            u_c1 = _proximo_slot_externo(u_env_max + gap_ext, step_ext, dims_v_rel, sentido=1)
            u_c2 = _proximo_slot_externo(u_c1 + step_ext, step_ext, dims_v_rel, sentido=1)
        else:
            u_c1 = _proximo_slot_externo(u_env_min - gap_ext, step_ext, dims_v_rel, sentido=-1)
            u_c2 = _proximo_slot_externo(u_c1 - step_ext, step_ext, dims_v_rel, sentido=-1)

        if len(refs_v) > 2:
            ln_vc = Line.CreateBound(org + rdir * u_c1 + upv * v0_v, org + rdir * u_c1 + upv * v1_v)
            if _criar_dim_segura(view, ln_vc, refs_v, dim_type=dim_t, min_gap=20.0 * MM) is not None:
                cnt_v += 1
            if op["sc_tot"]:
                ln_vt = Line.CreateBound(org + rdir * u_c2 + upv * v0_v, org + rdir * u_c2 + upv * v1_v)
                if _criar_dim_segura(view, ln_vt, [refs_v[0], refs_v[-1]], dim_type=dim_t, min_gap=20.0 * MM) is not None:
                    cnt_v += 1
        else:
            ln_vt = Line.CreateBound(org + rdir * u_c1 + upv * v0_v, org + rdir * u_c1 + upv * v1_v)
            if _criar_dim_segura(view, ln_vt, [refs_v[0], refs_v[-1]], dim_type=dim_t, min_gap=20.0 * MM) is not None:
                cnt_v += 1

        t.Commit()

    if cnt_v == 0:
        forms.alert(u"Nenhuma cota vertical pôde ser criada para os elementos selecionados.", title="Cotar Seleção no Corte")
    else:
        resumo = u"Seleção cotada fora do desenho!\n\n"
        resumo += u"✔ Elementos considerados: {}\n".format(len(elementos))
        resumo += u"✔ Linhas de cota verticais criadas: {}".format(cnt_v)
        forms.alert(resumo, title="AutoBIM: Cotar Seleção no Corte")

