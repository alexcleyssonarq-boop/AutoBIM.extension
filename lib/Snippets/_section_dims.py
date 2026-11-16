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

        # 5) Lajes de cobertura / telhados planos solidos cruzados no corte (ignora vidracas inclinadas)
        for roof in FilteredElementCollector(doc, view.Id).OfClass(RoofBase).WhereElementIsNotElementType().ToElements():
            if de._eh_vidraca_inclinada(roof):
                continue
            bb_uv = _bb_uv_em_vista(roof, view)
            if bb_uv is None or bb_uv[3] < v_lo or bb_uv[2] > v_hi:
                continue
            for vc, fc, rf in _faces_normais_direcao(roof, view, upv, min_dot=0.985):
                if v_lo <= vc <= v_hi and _face_cobre_ponto_xy(fc, org + rdir * _u_na_altura(vc)):
                    refs_cands.append((vc, rf, "RoofFlat"))

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

        for dt in dim_types:
            self._cb_tipo.Items.Add(de._elem_name(dt))
        self._cb_tipo.SelectedIndex = max(0, min(max(0, len(dim_types) - 1), int(prefs.get("pd_tipo", 0) or 0)))

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
        idx_t = max(0, self._cb_tipo.SelectedIndex)
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


def run_cotar_pe_direito():
    _verificar_view_section(u"Pé-Direito Automático no Corte")
    dim_types = _listar_tipos_cota_linear()
    prefs = _carregar_prefs()
    win = CotarPeDireitoWindow(prefs, dim_types)
    win.ShowDialog()
    if not win.confirmado:
        script.exit()

    op = win.resultado
    _salvar_prefs(dict((k, v) for k, v in op.items() if k != "dim_type"))

    rooms_corte = de._rooms_cortados_no_corte(view)
    if op["pd_escopo"] == 1:
        sel_ids = set(str(eid) for eid in uidoc.Selection.GetElementIds())
        for eid in list(uidoc.Selection.GetElementIds()):
            el = doc.GetElement(eid)
            if isinstance(el, RoomTag) and el.Room is not None:
                sel_ids.add(str(el.Room.Id))
        if sel_ids:
            rooms_corte = [rc for rc in rooms_corte if str(rc["room"].Id) in sel_ids]

    if not rooms_corte:
        forms.alert("Nenhum Ambiente (Room) cortado foi encontrado nesta vista.", title="Pé-Direito")
        script.exit()

    floors, ceilings = _coletar_pisos_forros_no_corte(view)
    org  = view.Origin
    rdir = view.RightDirection
    upv  = view.UpDirection
    esc_v = max(1.0, float(view.Scale or 50))
    off_tot = 6.5 * MM * esc_v

    criadas = 0
    ignoradas = 0

    with Transaction(doc, u"Cotar Pé-Direito no Corte") as t:
        t.Start()
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

            # Garante que u_pos esteja dentro do Room
            u_tentativas = [u_pos, umid, ua + 0.30 * span, ub - 0.30 * span]
            pt_room_xy = None
            u_ok = u_pos
            for uc in u_tentativas:
                px = rc["org"].X + uc * rc["rx"] + rc["t_v"] * rc["vx"]
                py = rc["org"].Y + uc * rc["ry"] + rc["t_v"] * rc["vy"]
                try:
                    if rc["room"].IsPointInRoom(XYZ(px, py, rc["z_probe"])):
                        pt_room_xy = XYZ(px, py, rc["z_min"])
                        u_ok = uc
                        break
                except Exception:
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

            refs_cadeia = [(v_bot, rf_bot, "Piso")]
            if op["pd_pf"] and rf_forro is not None:
                refs_cadeia.append((v_forro, rf_forro, "Forro"))
            if (op["pd_fl"] or (op["pd_pf"] and rf_forro is None)) and rf_laje is not None:
                refs_cadeia.append((v_laje, rf_laje, "Laje"))

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

        t.Commit()

    msg = u"Pé-Direito no Corte concluído!\n\n✔ Cotas inseridas: {}".format(criadas)
    if ignoradas:
        msg += u"\nℹ Ambientes sem forro/laje de fechamento acima: {}".format(ignoradas)
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

        for dt in dim_types:
            self._cb_tipo.Items.Add(de._elem_name(dt))
        self._cb_tipo.SelectedIndex = max(0, min(max(0, len(dim_types) - 1), int(prefs.get("eq_tipo", 0) or 0)))

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
        idx_t = max(0, self._cb_tipo.SelectedIndex)
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


def run_cotar_esquadrias_corte():
    _verificar_view_section(u"Cotas de Esquadrias no Corte")
    dim_types = _listar_tipos_cota_linear()
    prefs = _carregar_prefs()
    win = CotarEsquadriasCorteWindow(prefs, dim_types)
    win.ShowDialog()
    if not win.confirmado:
        script.exit()

    op = win.resultado
    _salvar_prefs(dict((k, v) for k, v in op.items() if k != "dim_type"))

    cats = []
    if op["eq_jan"]:
        cats.append(BuiltInCategory.OST_Windows)
    if op["eq_por"]:
        cats.append(BuiltInCategory.OST_Doors)

    esquadrias = []
    if op["eq_escopo"] == 1:
        for eid in uidoc.Selection.GetElementIds():
            el = doc.GetElement(eid)
            if isinstance(el, FamilyInstance) and el.Category is not None:
                cid = _eid_int(el.Category.Id)
                if (op["eq_jan"] and cid == int(BuiltInCategory.OST_Windows)) or (op["eq_por"] and cid == int(BuiltInCategory.OST_Doors)):
                    esquadrias.append(el)
        if not esquadrias:
            try:
                refs = uidoc.Selection.PickObjects(ObjectType.Element, _EsquadriaFilter(), u"Selecione as Portas/Janelas e clique em Concluir")
                esquadrias = [doc.GetElement(rf.ElementId) for rf in refs]
            except Exception:
                script.exit()
    else:
        for cat in cats:
            for fi in FilteredElementCollector(doc, view.Id).OfCategory(cat).WhereElementIsNotElementType().ToElements():
                if isinstance(fi, FamilyInstance) and getattr(fi, "SuperComponent", None) is None:
                    esquadrias.append(fi)

    if not esquadrias:
        forms.alert("Nenhuma Porta ou Janela visível foi encontrada neste Corte/Elevação.", title="Cotas de Esquadrias")
        script.exit()

    floors, ceilings = _coletar_pisos_forros_no_corte(view)
    crop_uv = _crop_uv_em_vista(view)
    oclusores = _coletar_oclusores_vista(view) if op["eq_escopo"] == 0 else []
    solidos_cache = {}
    org   = view.Origin
    rdir  = view.RightDirection
    upv   = view.UpDirection
    esc_v = max(1.0, float(view.Scale or 50))
    off_u = 4.5 * MM * esc_v
    off_w = 3.5 * MM * esc_v

    criadas = 0
    erros   = 0

    with Transaction(doc, u"Cotar Esquadrias no Corte") as t:
        t.Start()
        for fi in esquadrias:
            bb_uv = _bb_uv_em_vista(fi, view)
            if bb_uv is None:
                continue
            u_min, u_max, v_min, v_max, bb = bb_uv
            if crop_uv is not None:
                if u_max < crop_uv[0] or u_min > crop_uv[1] or v_max < crop_uv[2] or v_min > crop_uv[3]:
                    continue
            if op["eq_escopo"] == 0 and not _esquadria_visivel_na_vista(fi, view, bb_uv, crop_uv, oclusores, solidos_cache):
                continue

            vb, rfb, vt, rft = _refs_verticais_esquadria(fi, view)
            if rfb is None and rft is None:
                erros += 1
                continue

            # Determina pontos XY ao redor da esquadria, priorizando o lado interno (ambiente visivel) antes do eixo da parede
            pt_mid = 0.5 * (bb.Min + bb.Max)
            lados_xy = []
            try:
                f_ori = fi.FacingOrientation
                lados_xy.append(pt_mid + f_ori * (0.35 / 0.3048))
                lados_xy.append(pt_mid - f_ori * (0.35 / 0.3048))
            except Exception:
                pass
            # Fallback adicional ao longo da normal da vista (caso FacingOrientation falhe ou esquadria seja perpendicular)
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

            # Cota horizontal opcional de largura em vista frontal
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

        t.Commit()

    msg = u"Cotas de Esquadrias concluídas!\n\n✔ Cotas criadas: {}".format(criadas)
    if erros:
        msg += u"\n⚠ Esquadrias sem faces compatíveis: {}".format(erros)
    forms.alert(msg, title="Cotas de Esquadrias no Corte")


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

        for tt in text_types:
            self._cb_txt.Items.Add(de._elem_name(tt))
        self._cb_txt.SelectedIndex = max(0, min(max(0, len(text_types) - 1), int(prefs.get("it_txt", 0) or 0)))

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
        idx_t = max(0, self._cb_txt.SelectedIndex)
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
