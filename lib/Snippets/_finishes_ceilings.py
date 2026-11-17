# -*- coding: utf-8 -*-
"""
Engine compartilhado de Gesso, Molduras e Rodapes por Familia/Perfil - AutoBIM.
Compativel com IronPython 2.7 / pyRevit / Revit 2022-2026 (PT-BR / EN).
"""

import os
import json
import math
import codecs
import clr

clr.AddReference("System")
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from System import Int64
from System.Collections.Generic import List
from System.Windows.Markup import XamlReader
from Autodesk.Revit.DB import (
    BuiltInCategory,
    BuiltInParameter,
    CurveArrArray,
    CurveArray,
    CurveElement,
    CurveLoop,
    Element,
    ElementId,
    ElementTransformUtils,
    Family,
    FamilyInstance,
    FamilySource,
    FamilySymbol,
    FilteredElementCollector,
    Floor,
    Ceiling,
    CeilingType,
    Color,
    Group,
    IFailuresPreprocessor,
    IFamilyLoadOptions,
    InstanceVoidCutUtils,
    JoinGeometryUtils,
    Level,
    Line,
    Arc,
    Material,
    Options,
    PlanarFace,
    Plane,
    ProfilePlaneLocation,
    ReferenceArray,
    SaveAsOptions,
    Sketch,
    SketchEditScope,
    SketchPlane,
    Solid,
    SpatialElementBoundaryLocation,
    SpatialElementBoundaryOptions,
    StorageType,
    Transaction,
    TransactionGroup,
    View3D,
    ViewDetailLevel,
    Wall,
    WallType,
    XYZ,
)
from Autodesk.Revit.DB.Architecture import Room
from Autodesk.Revit.DB.Structure import StructuralType
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
from pyrevit import forms
import Snippets._ui_search as uis
try:
    reload(uis)
except Exception:
    pass

FT = 1.0 / 0.3048
CM_TO_FT = 0.01 * FT
FT_TO_CM = 30.48
RFA_TEMPLATES_DIRS = [
    r"G:\ProgramData\Autodesk\RVT 2026\Family Templates\Portuguese",
    r"C:\ProgramData\Autodesk\RVT 2026\Family Templates\Portuguese",
    r"G:\ProgramData\Autodesk\RVT 2025\Family Templates\Portuguese",
    r"C:\ProgramData\Autodesk\RVT 2025\Family Templates\Portuguese",
    r"G:\ProgramData\Autodesk\RVT 2024\Family Templates\Portuguese",
    r"C:\ProgramData\Autodesk\RVT 2024\Family Templates\Portuguese",
    r"C:\ProgramData\Autodesk\RVT 2026\Family Templates\English",
    r"C:\ProgramData\Autodesk\RVT 2026\Family Templates\Brazil",
]
LINE_BASED_RFT_NAMES = [
    u"Modelo genérico métrico com base na linha.rft",
    u"Metric Generic Model line based.rft",
]
CACHE_RFA_DIR = os.path.join(os.path.dirname(__file__), "_cache_rfa")
SETTINGS_PATH = os.path.join(os.path.dirname(__file__), "_finishes_ceilings_settings.json")


# ---------------------------------------------------------------------------
# Utilitarios basicos (IronPython-safe)
# ---------------------------------------------------------------------------

def _eid_int(eid):
    if eid is None:
        return -1
    try:
        return int(eid.Value)
    except Exception:
        try:
            return int(eid.IntegerValue)
        except Exception:
            return -1


def _ename(el):
    if el is None:
        return ""
    try:
        val = Element.Name.__get__(el)
        if val:
            return unicode(val) if "unicode" in dir(__builtins__) else str(val)
    except Exception:
        pass
    try:
        p = el.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME)
        if p and p.HasValue:
            return p.AsString() or ""
    except Exception:
        pass
    try:
        p = el.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
        if p and p.HasValue:
            return p.AsString() or ""
    except Exception:
        pass
    return ""


def _symbol_display(sym):
    fam_name = ""
    try:
        fam_name = _ename(sym.Family)
    except Exception:
        fam_name = getattr(sym, "FamilyName", "") or ""
    typ_name = _ename(sym)
    if fam_name and typ_name:
        return u"{0} :: {1}".format(fam_name, typ_name)
    return fam_name or typ_name or u"Sem nome"


def parse_float(texto, default=0.0):
    try:
        s = (texto or "").strip().replace(",", ".")
        if not s:
            return float(default)
        return float(s)
    except Exception:
        return float(default)


def carregar_prefs(secao):
    try:
        if os.path.exists(SETTINGS_PATH):
            with codecs.open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get(secao, {}) if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def salvar_prefs(secao, dados):
    try:
        tudo = {}
        if os.path.exists(SETTINGS_PATH):
            with codecs.open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                tudo = json.load(f) or {}
        if not isinstance(tudo, dict):
            tudo = {}
        atual = tudo.get(secao, {})
        if not isinstance(atual, dict):
            atual = {}
        atual.update(dados)
        tudo[secao] = atual
        with codecs.open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(tudo, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


class _SwallowFailures(IFailuresPreprocessor):
    def PreprocessFailures(self, failuresAccessor):
        try:
            for msg in failuresAccessor.GetFailureMessages():
                failuresAccessor.DeleteWarning(msg)
        except Exception:
            pass
        return 0


class _FamilyLoadOptions(IFamilyLoadOptions):
    def OnFamilyFound(self, familyInUse, overwriteParameterValues):
        overwriteParameterValues = False
        return True

    def OnSharedFamilyFound(self, sharedFamily, familyInUse, source, overwriteParameterValues):
        source = FamilySource.Family
        overwriteParameterValues = False
        return True


class _CatSelectionFilter(ISelectionFilter):
    def __init__(self, allowed_fn):
        self._fn = allowed_fn

    def AllowElement(self, elem):
        try:
            return bool(self._fn(elem))
        except Exception:
            return False

    def AllowReference(self, ref, pos):
        return False


def _selecionar_elementos_por_escopo(doc, uidoc, view, modo_idx, filter_fn, collector_fn, prompt_msg):
    """modo_idx: 0 = Todos da vista ativa, 1 = Pre-selecionados ou selecionar, 2 = Selecionar agora."""
    if modo_idx == 0:
        return collector_fn(doc, view)

    if modo_idx == 1:
        sel = []
        try:
            for eid in uidoc.Selection.GetElementIds():
                el = doc.GetElement(eid)
                if el and filter_fn(el):
                    sel.append(el)
        except Exception:
            pass
        if sel:
            return sel

    try:
        refs = uidoc.Selection.PickObjects(
            ObjectType.Element,
            _CatSelectionFilter(filter_fn),
            prompt_msg
        )
        return [doc.GetElement(r.ElementId) for r in refs if doc.GetElement(r.ElementId) is not None]
    except Exception:
        return []


def _is_room(el):
    return isinstance(el, Room) and getattr(el, "Area", 0.0) > 1e-6


def _is_ceiling(el):
    return isinstance(el, Ceiling)


def _is_opening(el):
    if not isinstance(el, FamilyInstance):
        return False
    cat = el.Category
    if cat is None:
        return False
    cid = _eid_int(cat.Id)
    return cid in (int(BuiltInCategory.OST_Windows), int(BuiltInCategory.OST_Doors))


def _coletar_rooms_vista(doc, view):
    col = FilteredElementCollector(doc, view.Id).OfCategory(BuiltInCategory.OST_Rooms).WhereElementIsNotElementType()
    return [r for r in col if _is_room(r)]


def _coletar_ceilings_vista(doc, view):
    col = FilteredElementCollector(doc, view.Id).OfClass(Ceiling).WhereElementIsNotElementType()
    return list(col)


def _coletar_janelas_vista(doc, view):
    out = []
    for bic in (BuiltInCategory.OST_Windows, BuiltInCategory.OST_Doors):
        col = FilteredElementCollector(doc, view.Id).OfCategory(bic).WhereElementIsNotElementType()
        for el in col:
            if bic == BuiltInCategory.OST_Windows:
                out.append(el)
            else:
                # Portas apenas se forem balcao/envidracadas quando usuario selecionou todas janelas: nao incluir portas internas comuns
                pass
    return out


# ---------------------------------------------------------------------------
# Coletor de tipos e parametros
# ---------------------------------------------------------------------------

def _coletar_wall_types(doc):
    tipos = list(FilteredElementCollector(doc).OfClass(WallType))
    validos = []
    for wt in tipos:
        try:
            kind_str = str(wt.Kind)
            if kind_str in ("Basic", "WallKind.Basic"):
                validos.append(wt)
        except Exception:
            validos.append(wt)

    def _score(wt):
        nm = _ename(wt).lower()
        w = getattr(wt, "Width", 1.0)
        if "forro" in nm or "gesso" in nm or "tabica" in nm or "sanca" in nm:
            return (0, w, nm)
        if "acabamento" in nm or "rodape" in nm or "rodap" in nm or "revest" in nm:
            return (1, w, nm)
        return (2, w, nm)

    validos.sort(key=_score)
    return validos


def _coletar_ceiling_types(doc):
    tipos = list(FilteredElementCollector(doc).OfClass(CeilingType))
    tipos.sort(key=lambda t: _ename(t).lower())
    return tipos


def _coletar_curve_based_symbols(doc, prefer_keywords=None):
    prefer_keywords = [k.lower() for k in (prefer_keywords or [])]
    col = FilteredElementCollector(doc).OfClass(FamilySymbol).OfCategory(BuiltInCategory.OST_GenericModel)
    out = []
    for sym in col:
        try:
            fam = sym.Family
            if fam is None:
                continue
            p_type = str(getattr(fam, "FamilyPlacementType", ""))
            if "CurveBased" not in p_type:
                continue
            out.append(sym)
        except Exception:
            pass

    def _rank(sym):
        label = _symbol_display(sym).lower()
        for idx, kw in enumerate(prefer_keywords):
            if kw in label:
                return (0, idx, label)
        if label.startswith("oc_"):
            return (1, 0, label)
        if "autobim" in label:
            return (2, 0, label)
        return (3, 0, label)

    out.sort(key=_rank)
    return out


def _melhor_indice_por_palavras(items_labels, saved_label=None, keywords=None):
    if not items_labels:
        return -1
    if saved_label and saved_label in items_labels:
        return items_labels.index(saved_label)
    if keywords:
        for kw in keywords:
            kl = kw.lower()
            for i, lbl in enumerate(items_labels):
                if kl in lbl.lower():
                    return i
    return 0


def _selecionar_combo_por_palavras(cb, items_labels, saved_label=None, keywords=None):
    idx = _melhor_indice_por_palavras(items_labels, saved_label=saved_label, keywords=keywords)
    if idx >= 0:
        cb.SelectedIndex = idx


def _anexar_busca_combo_palavras(cb, items_labels, saved_label=None, keywords=None, placeholder=u"Pesquisar..."):
    idx = max(0, _melhor_indice_por_palavras(items_labels, saved_label=saved_label, keywords=keywords))
    return uis.SearchableCombo.attach_auto(cb, items_labels, placeholder=placeholder, initial_index=idx)


def _set_param_double(el, names, val):
    for nm in names:
        try:
            p = el.LookupParameter(nm)
            if p and not p.IsReadOnly:
                p.Set(float(val))
                return True
        except Exception:
            pass
    return False


def _set_param_int(el, names, val):
    for nm in names:
        try:
            p = el.LookupParameter(nm)
            if p and not p.IsReadOnly:
                p.Set(int(val))
                return True
        except Exception:
            pass
    return False


def _set_bip_double(el, bip, val):
    try:
        p = el.get_Parameter(bip)
        if p and not p.IsReadOnly:
            p.Set(float(val))
            return True
    except Exception:
        pass
    return False


_MATERIAL_SPECS = {
    "tabica": {
        "keywords": [u"tabica", u"pintura branca", u"tinta branca", u"branco", u"alum", u"metal", u"gesso"],
        "fallback_name": u"AutoBIM - Tabica Metálica Branca",
        "rgb": (242, 242, 242),
    },
    "cortineiro": {
        "keywords": [u"gesso", u"drywall", u"forro", u"pintura branca", u"branco"],
        "fallback_name": u"AutoBIM - Gesso Acartonado Branco",
        "rgb": (246, 246, 244),
    },
    "sanca": {
        "keywords": [u"gesso", u"drywall", u"forro", u"pintura branca", u"branco"],
        "fallback_name": u"AutoBIM - Gesso Acartonado Branco",
        "rgb": (246, 246, 244),
    },
    "rodameio": {
        "keywords": [u"poliestireno", u"santa luzia", u"rodameio", u"boiserie", u"pintura branca", u"tinta branca", u"branco", u"gesso"],
        "fallback_name": u"AutoBIM - Poliestireno / Pintura Branca",
        "rgb": (245, 245, 243),
    },
    "rodape": {
        "keywords": [u"rodap", u"poliestireno", u"santa luzia", u"pintura branca", u"tinta branca", u"branco", u"mdf", u"pvc"],
        "fallback_name": u"AutoBIM - Poliestireno / Rodapé Branco",
        "rgb": (245, 245, 243),
    },
}


def _coletar_materiais(doc):
    out = []
    for m in FilteredElementCollector(doc).OfClass(Material):
        nm = _ename(m)
        if nm:
            out.append((nm, m))
    out.sort(key=lambda item: item[0].lower())
    return out


def _popular_combo_materiais(cb_mat, mats_data, saved_name=None):
    if cb_mat is None:
        return None
    itens = [u"Automático (detectar no projeto ou criar padrão)"]
    sel_idx = 0
    for idx, (nm, _) in enumerate(mats_data):
        itens.append(nm)
        if saved_name and nm == saved_name:
            sel_idx = idx + 1
    return uis.SearchableCombo.attach_auto(
        cb_mat, itens, placeholder=u"Pesquisar material...", initial_index=sel_idx
    )


def _resolver_material_padrao(doc, spec_key):
    spec = _MATERIAL_SPECS.get(spec_key) or _MATERIAL_SPECS["rodameio"]
    mats = _coletar_materiais(doc)
    fb_name = spec["fallback_name"]
    fb_low = fb_name.lower()

    for nm, m in mats:
        if nm.lower() == fb_low:
            return m.Id

    ignore_words = (u"vazio", u"void", u"invis", u"vidro", u"glass", u"agua", u"água", u"default", u"padrão", u"padrao")
    for kw in spec["keywords"]:
        kl = kw.lower()
        for nm, m in mats:
            nl = nm.lower()
            if any(iw in nl for iw in ignore_words):
                continue
            if kl in nl:
                return m.Id

    try:
        mat_id = Material.Create(doc, fb_name)
        m = doc.GetElement(mat_id)
        if m is not None:
            r, g, b = spec["rgb"]
            try:
                m.Color = Color(int(r), int(g), int(b))
            except Exception:
                pass
            try:
                m.UseRenderAppearanceForShading = False
            except Exception:
                pass
            try:
                m.Smoothness = 60
                m.Shininess = 35
            except Exception:
                pass
        return mat_id
    except Exception:
        return mats[0][1].Id if mats else ElementId.InvalidElementId


def _resolver_material_escolha(doc, cb_or_sc_mat, mats_data, spec_key):
    """Retorna (mat_id, user_explicit) dentro de Transaction ativa."""
    if cb_or_sc_mat is not None:
        sel = cb_or_sc_mat.selected_index() if hasattr(cb_or_sc_mat, "selected_index") else cb_or_sc_mat.SelectedIndex
        if sel > 0:
            idx = sel - 1
            if 0 <= idx < len(mats_data):
                return mats_data[idx][1].Id, True
    return _resolver_material_padrao(doc, spec_key), False


def _e_parametro_material(p):
    if p is None or p.IsReadOnly:
        return False
    try:
        if p.StorageType != StorageType.ElementId:
            return False
    except Exception:
        return False

    defn = getattr(p, "Definition", None)
    if defn is None:
        return False
    try:
        if getattr(defn, "BuiltInParameter", None) == BuiltInParameter.MATERIAL_ID_PARAM:
            return True
    except Exception:
        pass
    try:
        from Autodesk.Revit.DB import SpecTypeId
        if defn.GetDataType() == SpecTypeId.Reference.Material:
            return True
    except Exception:
        pass
    try:
        from Autodesk.Revit.DB import ParameterType
        if getattr(defn, "ParameterType", None) == ParameterType.Material:
            return True
    except Exception:
        pass

    nm = (getattr(defn, "Name", "") or "").strip().lower()
    if not nm:
        return False
    if any(bad in nm for bad in (u"família", u"familia", u"tipo", u"fase", u"imagem", u"nível", u"nivel", u"ifc")):
        return False
    return (nm.startswith("material") or u"material de acabamento" in nm or nm == u"acabamento")


def _aplicar_material_elemento(doc, inst, mat_id, overwrite_autobim=False, cache_sym_mats=None):
    """
    Aplica `mat_id` nos parametros de Material da instancia e do tipo quando estiverem
    sem material (<Por categoria> / <= 0), mantendo intactos os que ja possuem material.
    Em familias AutoBIM, sobrescreve caso `overwrite_autobim` seja True (escolha explicita na UI).
    """
    if inst is None or mat_id is None or _eid_int(mat_id) <= 0:
        return 0

    sym = getattr(inst, "Symbol", None)
    if sym is None:
        try:
            sym = doc.GetElement(inst.GetTypeId())
        except Exception:
            sym = None

    is_autobim = False
    if sym is not None:
        try:
            fam_n = _ename(sym.Family).lower()
            is_autobim = fam_n.startswith("autobim_")
        except Exception:
            pass

    allow_overwrite = bool(overwrite_autobim and is_autobim)
    applied = 0

    try:
        for p in inst.Parameters:
            if not _e_parametro_material(p):
                continue
            cur = p.AsElementId()
            if allow_overwrite or cur is None or _eid_int(cur) <= 0:
                try:
                    p.Set(mat_id)
                    applied += 1
                except Exception:
                    pass
    except Exception:
        pass

    if sym is not None:
        cache_key = (_eid_int(sym.Id), _eid_int(mat_id), allow_overwrite)
        if cache_sym_mats is None or cache_key not in cache_sym_mats:
            try:
                for p in sym.Parameters:
                    if not _e_parametro_material(p):
                        continue
                    cur = p.AsElementId()
                    if allow_overwrite or cur is None or _eid_int(cur) <= 0:
                        try:
                            p.Set(mat_id)
                            applied += 1
                        except Exception:
                            pass
            except Exception:
                pass
            if cache_sym_mats is not None:
                cache_sym_mats.add(cache_key)

    return applied


# ---------------------------------------------------------------------------
# Matematica 2D/3D pura (testavel fora do Revit)
# ---------------------------------------------------------------------------

def _signed_area_2d(pts):
    """Area assinada no plano XY (+ => CCW, - => CW)."""
    n = len(pts)
    if n < 3:
        return 0.0
    area = 0.0
    for i in range(n):
        x1, y1 = pts[i][0], pts[i][1]
        x2, y2 = pts[(i + 1) % n][0], pts[(i + 1) % n][1]
        area += (x1 * y2 - x2 * y1)
    return 0.5 * area


def _orient_loop_xy(pts, want_ccw=True):
    area = _signed_area_2d(pts)
    is_ccw = (area >= 0.0)
    if is_ccw == want_ccw:
        return list(pts)
    return list(reversed(pts))


def _dist_point_to_seg_2d(px, py, ax, ay, bx, by):
    dx = bx - ax
    dy = by - ay
    L2 = dx * dx + dy * dy
    if L2 < 1e-12:
        return math.hypot(px - ax, py - ay), 0.0
    t = ((px - ax) * dx + (py - ay) * dy) / L2
    if t < 0.0:
        t = 0.0
    elif t > 1.0:
        t = 1.0
    cx = ax + t * dx
    cy = ay + t * dy
    return math.hypot(px - cx, py - cy), t


def _subtrair_aberturas_segmento(seg_len, openings_1d, min_len=0.02):
    """
    Dado comprimento total seg_len > 0 e lista de intervalos [(a, b), ...] de aberturas,
    retorna lista de dicionarios mantendo apenas os trechos cheios:
      {"start": s, "end": e, "cut_start": bool, "cut_end": bool}
    Onde cut_start=True significa que o inicio foi cortado por uma abertura (nao e canto de parede).
    """
    if seg_len <= min_len:
        return []
    merged = []
    for a, b in openings_1d:
        aa = max(0.0, min(float(a), float(b)))
        bb = min(seg_len, max(float(a), float(b)))
        if bb - aa <= 1e-5:
            continue
        merged.append((aa, bb))
    merged.sort()

    consolidated = []
    for aa, bb in merged:
        if not consolidated or aa > consolidated[-1][1] + 1e-5:
            consolidated.append([aa, bb])
        else:
            if bb > consolidated[-1][1]:
                consolidated[-1][1] = bb

    pieces = []
    cursor = 0.0
    for aa, bb in consolidated:
        if aa - cursor >= min_len:
            pieces.append({
                "start": cursor,
                "end": aa,
                "cut_start": (cursor > 1e-4),
                "cut_end": True,
            })
        if bb > cursor:
            cursor = bb

    if seg_len - cursor >= min_len:
        pieces.append({
            "start": cursor,
            "end": seg_len,
            "cut_start": (cursor > 1e-4),
            "cut_end": False,
        })
    return pieces


def _recuar_aresta_poligono(pts_2d, edge_idx, s0, s1, inward_normal_xy, depth):
    """
    Recua a aresta `edge_idx` (pts_2d[i] -> pts_2d[(i+1)%n]) entre parametros s0..s1 (em metros/pes)
    para dentro do poligono em `depth`, retornando novo contorno fechado [(x, y), ...].
    """
    n = len(pts_2d)
    if n < 3 or depth <= 1e-5 or s1 <= s0 + 1e-5:
        return list(pts_2d)
    a = pts_2d[edge_idx]
    b = pts_2d[(edge_idx + 1) % n]
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    L = math.hypot(dx, dy)
    if L < 1e-6:
        return list(pts_2d)
    tx, ty = dx / L, dy / L
    s0c = max(0.0, min(s0, L))
    s1c = max(0.0, min(s1, L))
    if s1c - s0c <= 1e-5:
        return list(pts_2d)

    nx, ny = inward_normal_xy[0], inward_normal_xy[1]
    p_s0 = (a[0] + tx * s0c, a[1] + ty * s0c)
    p_s1 = (a[0] + tx * s1c, a[1] + ty * s1c)
    p_in0 = (p_s0[0] + nx * depth, p_s0[1] + ny * depth)
    p_in1 = (p_s1[0] + nx * depth, p_s1[1] + ny * depth)

    nxt_idx = (edge_idx + 1) % n
    skip_start = (s0c <= 1e-4)
    skip_end = (s1c >= L - 1e-4)

    out = []
    for idx in range(n):
        if idx == edge_idx:
            if not skip_start:
                out.append(pts_2d[idx])
                out.append(p_s0)
            out.append(p_in0)
            out.append(p_in1)
            if not skip_end:
                out.append(p_s1)
        elif skip_end and idx == nxt_idx:
            continue
        else:
            out.append(pts_2d[idx])

    dedup = []
    for v in out:
        if not dedup or math.hypot(v[0] - dedup[-1][0], v[1] - dedup[-1][1]) > 1e-4:
            dedup.append(v)
    if len(dedup) >= 2 and math.hypot(dedup[0][0] - dedup[-1][0], dedup[0][1] - dedup[-1][1]) <= 1e-4:
        dedup.pop()
    return dedup


def _deslocar_poligono_seletivo(pts_2d, edge_offsets):
    """
    Desloca seletivamente cada aresta i -> (i+1)%n de um poligono CCW (ou orientado de modo que
    inward = (-ty, tx)) para dentro conforme `edge_offsets[i]`, recalculando os vertices por
    intersecao reta-reta.
    """
    n = len(pts_2d)
    if n < 3 or len(edge_offsets) != n:
        return list(pts_2d)
    if all(abs(d) < 1e-6 for d in edge_offsets):
        return list(pts_2d)

    area = _signed_area_2d(pts_2d)
    sign_in = 1.0 if area >= 0.0 else -1.0

    lines = []
    for i in range(n):
        a = pts_2d[i]
        b = pts_2d[(i + 1) % n]
        dx = b[0] - a[0]
        dy = b[1] - a[1]
        L = math.hypot(dx, dy)
        if L < 1e-7:
            lines.append((a[0], a[1], 1.0, 0.0))
            continue
        tx, ty = dx / L, dy / L
        nx, ny = -ty * sign_in, tx * sign_in
        d = float(edge_offsets[i])
        lines.append((a[0] + nx * d, a[1] + ny * d, tx, ty))

    new_pts = []
    for i in range(n):
        p1x, p1y, d1x, d1y = lines[(i - 1) % n]
        p2x, p2y, d2x, d2y = lines[i]
        det = d1x * d2y - d1y * d2x
        if abs(det) > 1e-6:
            rx = p2x - p1x
            ry = p2y - p1y
            t = (rx * d2y - ry * d2x) / det
            new_pts.append((p1x + t * d1x, p1y + t * d1y))
        else:
            new_pts.append((p2x, p2y))
    return new_pts


def _mesclar_arestas_colineares_xy(edges_xy, tol_gap=0.05, dot_min=0.999):
    """
    Mescla arestas consecutivas colineares (inclusive fechamento ultimo->primeiro) em uma lista de ((x0, y0), (x1, y1)).
    Evita V-notches e divisoes falsas quando a face do forro esta quebrada por portas ou juncoes em T.
    """
    if not edges_xy:
        return []
    merged = []
    for a, b in edges_xy:
        dx = b[0] - a[0]
        dy = b[1] - a[1]
        L = math.hypot(dx, dy)
        if L < 1e-4:
            continue
        tx, ty = dx / L, dy / L
        if merged:
            pa, pb, ptx, pty = merged[-1]
            if math.hypot(pb[0] - a[0], pb[1] - a[1]) <= tol_gap and (ptx * tx + pty * ty) > dot_min:
                ndx = b[0] - pa[0]
                ndy = b[1] - pa[1]
                nL = math.hypot(ndx, ndy)
                if nL > 1e-4:
                    merged[-1] = (pa, b, ndx / nL, ndy / nL)
                    continue
        merged.append((a, b, tx, ty))

    if len(merged) >= 2:
        fa, fb, ftx, fty = merged[0]
        la, lb, ltx, lty = merged[-1]
        if math.hypot(lb[0] - fa[0], lb[1] - fa[1]) <= tol_gap and (ltx * ftx + lty * fty) > dot_min:
            ndx = fb[0] - la[0]
            ndy = fb[1] - la[1]
            nL = math.hypot(ndx, ndy)
            if nL > 1e-4:
                merged[0] = (la, fb, ndx / nL, ndy / nL)
                merged.pop()

    return [(a, b) for (a, b, _, _) in merged]


# ---------------------------------------------------------------------------
# Sondagem de pisadas, aberturas de parede, forros e distancias no Revit
# ---------------------------------------------------------------------------

def _obter_nivel_e_z_room(doc, room):
    lvl = doc.GetElement(room.LevelId)
    lvl_elev = lvl.Elevation if lvl else 0.0
    base_off = 0.0
    try:
        p = room.get_Parameter(BuiltInParameter.ROOM_LOWER_OFFSET)
        if p and p.HasValue:
            base_off = p.AsDouble()
    except Exception:
        pass
    return lvl, lvl_elev, lvl_elev + base_off


def _detectar_topo_piso_ambiente(doc, room, default_z):
    """Encontra o piso acabado imediatamente sob o LocationPoint do Room e devolve seu Z superior."""
    loc = getattr(room, "Location", None)
    if loc is None or not hasattr(loc, "Point"):
        return default_z
    pt = loc.Point
    px, py = pt.X, pt.Y
    floors = FilteredElementCollector(doc).OfClass(Floor).WhereElementIsNotElementType()
    best_z = None
    best_dist = 1e9
    for fl in floors:
        try:
            bb = fl.get_BoundingBox(None)
            if bb is None:
                continue
            if px < bb.Min.X - 0.05 or px > bb.Max.X + 0.05 or py < bb.Min.Y - 0.05 or py > bb.Max.Y + 0.05:
                continue
            if bb.Max.Z < default_z - 0.50 or bb.Max.Z > default_z + 1.50:
                continue
            # Refinar via Faces superiores dos Solids
            opt = Options()
            opt.DetailLevel = ViewDetailLevel.Coarse
            geom = fl.get_Geometry(opt)
            top_z = bb.Max.Z
            if geom:
                for obj in geom:
                    if isinstance(obj, Solid) and obj.Volume > 1e-6:
                        for face in obj.Faces:
                            if isinstance(face, PlanarFace) and face.FaceNormal.Z > 0.9:
                                fbb = face.GetBoundingBox()
                                u_mid = 0.5 * (fbb.Min.U + fbb.Max.U)
                                v_mid = 0.5 * (fbb.Min.V + fbb.Max.V)
                                fpt = face.Evaluate(UV_OR_ZERO(u_mid, v_mid)) if False else face.Origin
                                if abs(fpt.Z - default_z) < best_dist:
                                    top_z = fpt.Z
            d = abs(top_z - default_z)
            if d < best_dist:
                best_dist = d
                best_z = top_z
        except Exception:
            pass
    return best_z if best_z is not None else default_z


def _coletar_loops_ambiente(room, incluir_ilhas=True):
    """
    Retorna lista de loops orientados de modo que (+T rotated +90 deg CCW) aponte
    SEMPRE PARA DENTRO do ambiente:
      loop 0 (externo) = CCW
      loops >= 1 (ilhas/pilares) = CW
    Cada item da lista retornada e:
      [{"p0": XYZ, "p1": XYZ, "curve": Curve, "elem_id": ElementId, "is_sep": bool, "t": XYZ, "inward": XYZ, "length": float}, ...]
    """
    opts = SpatialElementBoundaryOptions()
    opts.SpatialElementBoundaryLocation = SpatialElementBoundaryLocation.Finish
    segs_loops = room.GetBoundarySegments(opts)
    if not segs_loops:
        return []

    doc = room.Document
    out_loops = []
    for l_idx, seg_list in enumerate(segs_loops):
        if l_idx > 0 and not incluir_ilhas:
            continue
        raw = []
        pts_xy = []
        for bseg in seg_list:
            crv = bseg.GetCurve()
            if crv is None:
                continue
            # Tesselar arcos em pequenos trechos lineares se nao for linha reta
            if isinstance(crv, Line):
                p0 = crv.GetEndPoint(0)
                p1 = crv.GetEndPoint(1)
                if p0.DistanceTo(p1) > 1e-4:
                    raw.append((p0, p1, bseg.ElementId))
                    pts_xy.append((p0.X, p0.Y))
            else:
                pts = crv.Tessellate()
                for k in range(len(pts) - 1):
                    if pts[k].DistanceTo(pts[k + 1]) > 1e-4:
                        raw.append((pts[k], pts[k + 1], bseg.ElementId))
                        pts_xy.append((pts[k].X, pts[k].Y))
        if len(raw) < 3:
            continue
        area = _signed_area_2d(pts_xy)
        want_ccw = (l_idx == 0)
        is_ccw = (area >= 0.0)
        if is_ccw != want_ccw:
            raw = [(p1, p0, eid) for (p0, p1, eid) in reversed(raw)]

        loop_items = []
        for (p0, p1, eid) in raw:
            p0z = XYZ(p0.X, p0.Y, 0.0)
            p1z = XYZ(p1.X, p1.Y, 0.0)
            L = p0z.DistanceTo(p1z)
            if L < 1e-4:
                continue
            t = (p1z - p0z).Normalize()
            inward = XYZ(-t.Y, t.X, 0.0)  # +90 deg CCW de +T = PARA DENTRO da sala
            el = doc.GetElement(eid) if _eid_int(eid) > 0 else None
            is_sep = False
            if el is None:
                is_sep = True
            else:
                try:
                    cat = el.Category
                    if cat and _eid_int(cat.Id) == int(BuiltInCategory.OST_RoomSeparationLines):
                        is_sep = True
                except Exception:
                    pass
            loop_items.append({
                "p0": p0z,
                "p1": p1z,
                "elem_id": eid,
                "host_wall": el if isinstance(el, Wall) else None,
                "host_walls": [el] if isinstance(el, Wall) else [],
                "is_sep": is_sep,
                "t": t,
                "inward": inward,
                "length": L,
            })
        if loop_items:
            # Mesclar segmentos colineares consecutivos (evita emendas em paredes divididas em T)
            merged = []
            for seg in loop_items:
                if merged:
                    prev = merged[-1]
                    if (
                        not prev["is_sep"]
                        and not seg["is_sep"]
                        and prev["p1"].DistanceTo(seg["p0"]) <= 0.08
                        and prev["t"].DotProduct(seg["t"]) > 0.999
                    ):
                        prev["p1"] = seg["p1"]
                        prev["length"] = prev["p0"].DistanceTo(prev["p1"])
                        if prev["length"] > 1e-4:
                            prev["t"] = (prev["p1"] - prev["p0"]).Normalize()
                            prev["inward"] = XYZ(-prev["t"].Y, prev["t"].X, 0.0)
                        for hw in seg["host_walls"]:
                            if hw not in prev["host_walls"]:
                                prev["host_walls"].append(hw)
                        continue
                merged.append(seg)
            if len(merged) >= 2:
                first = merged[0]
                last = merged[-1]
                if (
                    not first["is_sep"]
                    and not last["is_sep"]
                    and last["p1"].DistanceTo(first["p0"]) <= 0.08
                    and last["t"].DotProduct(first["t"]) > 0.999
                ):
                    first["p0"] = last["p0"]
                    first["length"] = first["p0"].DistanceTo(first["p1"])
                    if first["length"] > 1e-4:
                        first["t"] = (first["p1"] - first["p0"]).Normalize()
                        first["inward"] = XYZ(-first["t"].Y, first["t"].X, 0.0)
                    for hw in last["host_walls"]:
                        if hw not in first["host_walls"]:
                            first["host_walls"].append(hw)
                    merged.pop()
            out_loops.append(merged)
    return out_loops


def _clip_seg_z_e_projetar_xy(ax, ay, az, bx, by, bz, p0x, p0y, tx, ty, z_bot, z_top):
    """
    Dado segmento 3D (a -> b), retorna projecoes 1D ao longo de (p0, t) apenas da parte
    que intercepta a faixa vertical [z_bot, z_top]. Retorna [] se nao interceptar.
    """
    zmin_e = az if az <= bz else bz
    zmax_e = bz if bz >= az else az
    if zmax_e < z_bot + 0.002 or zmin_e > z_top - 0.002:
        return []
    dz = bz - az
    if abs(dz) < 1e-6:
        return [
            (ax - p0x) * tx + (ay - p0y) * ty,
            (bx - p0x) * tx + (by - p0y) * ty,
        ]
    u0 = (z_bot - az) / dz
    u1 = (z_top - az) / dz
    ua = max(0.0, min(1.0, min(u0, u1)))
    ub = max(0.0, min(1.0, max(u0, u1)))
    if ub - ua < 1e-6 and (zmax_e - zmin_e) > 1e-4:
        return []
    pxa = ax + (bx - ax) * ua
    pya = ay + (by - ay) * ua
    pxb = ax + (bx - ax) * ub
    pyb = ay + (by - ay) * ub
    return [
        (pxa - p0x) * tx + (pya - p0y) * ty,
        (pxb - p0x) * tx + (pyb - p0y) * ty,
    ]


def _obter_vista_3d(doc):
    try:
        av = getattr(doc, "ActiveView", None)
        if isinstance(av, View3D) and not av.IsTemplate:
            return av
    except Exception:
        pass
    try:
        for v in FilteredElementCollector(doc).OfClass(View3D):
            if not v.IsTemplate:
                return v
    except Exception:
        pass
    return None


def _intervalo_solidos_esquadria_1d(ins, p0, t_dir, z_bot, z_top, doc=None, view_3d=None):
    """
    Mede os limites mais externos reais (marco/guarnicao 3D visivel, sem linhas/planos 2D invisiveis)
    de uma porta/janela dentro da faixa de altura [z_bot, z_top].
    Retorna (has_solids, (min_s, max_s) ou None).
    """
    opt = Options()
    if view_3d is not None:
        opt.View = view_3d
    else:
        opt.DetailLevel = ViewDetailLevel.Fine
    opt.IncludeNonVisibleObjects = False
    try:
        geom = ins.get_Geometry(opt)
    except Exception:
        return False, None
    if geom is None:
        return False, None

    bad_subcats = (u"abertura", u"opening", u"vão", u"vao", u"folga", u"clear", u"swing", u"arco", u"proj", u"ocult", u"invis", u"simb")
    solids = []
    stack = [geom]
    while stack:
        g = stack.pop()
        for obj in g:
            if isinstance(obj, Solid) and obj.Volume > 1e-8:
                keep = True
                if doc is not None:
                    try:
                        gs_id = getattr(obj, "GraphicsStyleId", ElementId.InvalidElementId)
                        if _eid_int(gs_id) > 0:
                            gs = doc.GetElement(gs_id)
                            gcat = getattr(gs, "GraphicsStyleCategory", None) if gs else None
                            cname = (getattr(gcat, "Name", "") or "").lower()
                            if any(b in cname for b in bad_subcats):
                                keep = False
                            elif view_3d is not None and gcat is not None:
                                try:
                                    if not gcat.get_Visible(view_3d):
                                        keep = False
                                except Exception:
                                    pass
                    except Exception:
                        pass
                if keep:
                    solids.append(obj)
            elif hasattr(obj, "GetInstanceGeometry"):
                try:
                    stack.append(obj.GetInstanceGeometry())
                except Exception:
                    pass

    if not solids:
        return False, None

    p0x, p0y = p0.X, p0.Y
    tx, ty = t_dir.X, t_dir.Y
    s_min = 1e9
    s_max = -1e9
    got = False

    for sol in solids:
        try:
            for ed in sol.Edges:
                pts = ed.Tessellate()
                for k in range(len(pts) - 1):
                    a = pts[k]
                    b = pts[k + 1]
                    pr_list = _clip_seg_z_e_projetar_xy(
                        a.X, a.Y, a.Z, b.X, b.Y, b.Z,
                        p0x, p0y, tx, ty, z_bot, z_top
                    )
                    for s_val in pr_list:
                        if s_val < s_min:
                            s_min = s_val
                        if s_val > s_max:
                            s_max = s_val
                        got = True
        except Exception:
            pass

    if not got:
        return True, None
    return True, (s_min, s_max)


def _intervalos_aberturas_no_segmento(doc, wall_or_walls, p0, p1, z_elem_bot, z_elem_top, folga_ft=0.0):
    """
    Projeta portas/janelas hospedadas em `wall_or_walls` sobre o segmento [p0, p1] (XY),
    usando a geometria solida 3D mais externa na faixa vertical [z_elem_bot, z_elem_top]
    (com fallback para BoundingBox quando a abertura nao possuir solidos 3D).
    """
    if wall_or_walls is None:
        return []
    walls = wall_or_walls if isinstance(wall_or_walls, (list, tuple)) else [wall_or_walls]
    walls = [w for w in walls if isinstance(w, Wall)]
    if not walls:
        return []
    seg_vec = XYZ(p1.X - p0.X, p1.Y - p0.Y, 0.0)
    seg_len = seg_vec.GetLength()
    if seg_len < 1e-4:
        return []
    t_dir = seg_vec.Normalize()

    inserts = []
    seen_ids = set()
    for w in walls:
        try:
            for iid in w.FindInserts(True, False, False, False):
                ii = _eid_int(iid)
                if ii not in seen_ids:
                    seen_ids.add(ii)
                    inserts.append(iid)
        except Exception:
            pass

    view_3d = _obter_vista_3d(doc)
    intervals = []
    for iid in inserts:
        ins = doc.GetElement(iid)
        if ins is None:
            continue

        has_solids, solid_span = _intervalo_solidos_esquadria_1d(
            ins, p0, t_dir, z_elem_bot, z_elem_top, doc=doc, view_3d=view_3d
        )
        if has_solids:
            if solid_span is None:
                continue
            a = solid_span[0] - folga_ft
            b = solid_span[1] + folga_ft
            if b > 0.0 and a < seg_len:
                intervals.append((a, b))
            continue

        # Fallback para aberturas puras / Opening sem solidos 3D
        bb = None
        try:
            bb = ins.get_BoundingBox(None)
        except Exception:
            bb = None
        if bb is None:
            continue
        if bb.Max.Z <= z_elem_bot + 0.01 or bb.Min.Z >= z_elem_top - 0.01:
            continue
        xs = (bb.Min.X, bb.Max.X)
        ys = (bb.Min.Y, bb.Max.Y)
        projs = []
        for x in xs:
            for y in ys:
                projs.append((x - p0.X) * t_dir.X + (y - p0.Y) * t_dir.Y)
        a = min(projs) - folga_ft
        b = max(projs) + folga_ft
        if b > 0.0 and a < seg_len:
            intervals.append((a, b))
    return intervals


def _obter_info_forro(ceiling):
    """Extrai loops externos/internos da face inferior do Ceiling e sua cota Z inferior."""
    opt = Options()
    opt.DetailLevel = ViewDetailLevel.Coarse
    opt.ComputeReferences = False
    geom = ceiling.get_Geometry(opt)
    best_face = None
    best_area = 0.0
    bottom_z = None

    def _scan_geom(g):
        nonlocal_best = [best_face, best_area, bottom_z]
        for obj in g:
            if isinstance(obj, Solid) and obj.Volume > 1e-6:
                for face in obj.Faces:
                    if isinstance(face, PlanarFace) and face.FaceNormal.Z < -0.9:
                        ar = face.Area
                        if ar > nonlocal_best[1]:
                            nonlocal_best[1] = ar
                            nonlocal_best[0] = face
                            nonlocal_best[2] = face.Origin.Z
            elif hasattr(obj, "GetInstanceGeometry"):
                res = _scan_geom(obj.GetInstanceGeometry())
                if res[1] > nonlocal_best[1]:
                    nonlocal_best = res
        return nonlocal_best

    if geom:
        best_face, best_area, bottom_z = _scan_geom(geom)

    if best_face is None or bottom_z is None:
        return None, []

    loops_out = []
    for cl in best_face.GetEdgesAsCurveLoops():
        pts = []
        for crv in cl:
            tpts = crv.Tessellate()
            for k in range(len(tpts) - 1):
                p0 = XYZ(tpts[k].X, tpts[k].Y, bottom_z)
                p1 = XYZ(tpts[k + 1].X, tpts[k + 1].Y, bottom_z)
                if p0.DistanceTo(p1) > 1e-4:
                    pts.append((p0, p1))
        if len(pts) < 3:
            continue
        area = _signed_area_2d([(a.X, a.Y) for (a, _) in pts])
        # Queremos CCW para externo (index 0 depois de ordenar por abs area)
        loops_out.append((abs(area), area >= 0.0, pts))

    loops_out.sort(key=lambda x: -x[0])
    formatted_loops = []
    for idx, (_, is_ccw, edges) in enumerate(loops_out):
        want_ccw = (idx == 0)
        if is_ccw != want_ccw:
            edges = [(b, a) for (a, b) in reversed(edges)]
        merged_xy = _mesclar_arestas_colineares_xy(
            [((a.X, a.Y), (b.X, b.Y)) for (a, b) in edges],
            tol_gap=0.05,
            dot_min=0.999,
        )
        clean_edges = []
        for axy, bxy in merged_xy:
            a = XYZ(axy[0], axy[1], bottom_z)
            b = XYZ(bxy[0], bxy[1], bottom_z)
            L = a.DistanceTo(b)
            if L < 1e-4:
                continue
            t = (b - a).Normalize()
            inward = XYZ(-t.Y, t.X, 0.0)   # aponta PARA DENTRO do forro
            outward = XYZ(t.Y, -t.X, 0.0)  # aponta PARA FORA do forro (em direcao a parede)
            clean_edges.append({
                "p0": a,
                "p1": b,
                "t": t,
                "inward": inward,
                "outward": outward,
                "length": L,
            })
        if clean_edges:
            formatted_loops.append(clean_edges)

    return bottom_z, formatted_loops


def _medir_recuo_parede_borda(walls_data, edge, bottom_z, max_search_ft):
    """
    Mede a folga livre real (em pes) entre a borda do forro (`p0->p1`) e a face interna
    da parede mais proxima no lado `outward`. Retorna (gap_ft, nearest_wall) ou (None, None).
    """
    mid = (edge["p0"] + edge["p1"]) * 0.5
    out = edge["outward"]
    best_gap = None
    best_wall = None

    for wd in walls_data:
        if bottom_z < wd["zmin"] - 0.5 or bottom_z > wd["zmax"] + 0.5:
            continue
        # Distancia do ponto medio da borda ate o eixo da parede
        dist_axis, t = _dist_point_to_seg_2d(
            mid.X, mid.Y, wd["ax"], wd["ay"], wd["bx"], wd["by"]
        )
        # Verificar se o ponto esta realmente na projecao longitudinal da parede (margem pequena)
        # e se o vetor ate a parede alinha com `outward`
        wx = wd["ax"] + t * (wd["bx"] - wd["ax"])
        wy = wd["ay"] + t * (wd["by"] - wd["ay"])
        vx = wx - mid.X
        vy = wy - mid.Y
        vlen = math.hypot(vx, vy)
        if vlen < 1e-5:
            continue
        dot = (vx * out.X + vy * out.Y) / vlen
        if dot < 0.70:  # parede precisa estar na frente de outward (~<=45 deg)
            continue
        # Checar tambem alinhamento angular aproximado da borda com a parede
        wtx = (wd["bx"] - wd["ax"])
        wty = (wd["by"] - wd["ay"])
        wl = math.hypot(wtx, wty)
        if wl > 1e-5:
            par_dot = abs((edge["t"].X * wtx + edge["t"].Y * wty) / wl)
            if par_dot < 0.75:
                continue
        clear_gap = vlen - 0.5 * wd["width"]
        if clear_gap < -0.05 or clear_gap > max_search_ft:
            continue
        clear_gap = max(0.0, clear_gap)
        if best_gap is None or clear_gap < best_gap:
            best_gap = clear_gap
            best_wall = wd["wall"]

    return best_gap, best_wall


def _coletar_walls_data(doc):
    walls = FilteredElementCollector(doc).OfClass(Wall).WhereElementIsNotElementType()
    out = []
    for w in walls:
        try:
            loc = w.Location
            if loc is None or not hasattr(loc, "Curve"):
                continue
            crv = loc.Curve
            if crv is None:
                continue
            p0 = crv.GetEndPoint(0)
            p1 = crv.GetEndPoint(1)
            bb = w.get_BoundingBox(None)
            zmin = bb.Min.Z if bb else -100.0
            zmax = bb.Max.Z if bb else 100.0
            out.append({
                "wall": w,
                "ax": p0.X,
                "ay": p0.Y,
                "bx": p1.X,
                "by": p1.Y,
                "width": getattr(w.WallType, "Width", 0.49),
                "zmin": zmin,
                "zmax": zmax,
            })
        except Exception:
            pass
    return out


# ---------------------------------------------------------------------------
# Colocacao inteligente e calibracao de orientacao para CurveBased GenericModel
# ---------------------------------------------------------------------------

def _solid_centroid_xy_along(inst, origin, dir_vec):
    """Mede onde fica o centroide de volume da instancia em relacao a `origin` ao longo de `dir_vec`."""
    opt = Options()
    opt.DetailLevel = ViewDetailLevel.Fine
    geom = inst.get_Geometry(opt)
    if geom is None:
        return None

    total_vol = 0.0
    weighted = 0.0
    min_proj = 1e9
    max_proj = -1e9
    min_z = 1e9
    max_z = -1e9

    solids = []
    stack = [geom]
    while stack:
        g = stack.pop()
        for obj in g:
            if isinstance(obj, Solid) and obj.Volume > 1e-8:
                solids.append(obj)
            elif hasattr(obj, "GetInstanceGeometry"):
                try:
                    stack.append(obj.GetInstanceGeometry())
                except Exception:
                    pass

    if not solids:
        return None

    styled = [s for s in solids if _eid_int(getattr(s, "GraphicsStyleId", None)) > 0]
    use_solids = styled if styled else solids

    def _acc_pt(wp):
        pr = (wp.X - origin.X) * dir_vec.X + (wp.Y - origin.Y) * dir_vec.Y
        return pr, wp.Z

    for obj in use_solids:
        try:
            c = obj.ComputeCentroid()
            v = obj.Volume
            proj = (c.X - origin.X) * dir_vec.X + (c.Y - origin.Y) * dir_vec.Y
            total_vol += v
            weighted += proj * v

            got_pts = False
            try:
                for face in obj.Faces:
                    try:
                        mesh = face.Triangulate()
                        if mesh is not None:
                            for wp in mesh.Vertices:
                                pr, wz = _acc_pt(wp)
                                if pr < min_proj:
                                    min_proj = pr
                                if pr > max_proj:
                                    max_proj = pr
                                if wz < min_z:
                                    min_z = wz
                                if wz > max_z:
                                    max_z = wz
                                got_pts = True
                    except Exception:
                        pass
            except Exception:
                pass
            if not got_pts:
                try:
                    for ed in obj.Edges:
                        for wp in ed.Tessellate():
                            pr, wz = _acc_pt(wp)
                            if pr < min_proj:
                                min_proj = pr
                            if pr > max_proj:
                                max_proj = pr
                            if wz < min_z:
                                min_z = wz
                            if wz > max_z:
                                max_z = wz
                            got_pts = True
                except Exception:
                    pass
            if not got_pts:
                bb = obj.GetBoundingBox()
                if bb:
                    tr = bb.Transform
                    for dx in (bb.Min.X, bb.Max.X):
                        for dy in (bb.Min.Y, bb.Max.Y):
                            for dz in (bb.Min.Z, bb.Max.Z):
                                pr, wz = _acc_pt(tr.OfPoint(XYZ(dx, dy, dz)))
                                if pr < min_proj:
                                    min_proj = pr
                                if pr > max_proj:
                                    max_proj = pr
                                if wz < min_z:
                                    min_z = wz
                                if wz > max_z:
                                    max_z = wz
        except Exception:
            pass

    if total_vol <= 1e-9 or min_z > 1e8:
        return None
    return {
        "centroid_proj": weighted / total_vol,
        "min_proj": min_proj,
        "max_proj": max_proj,
        "min_z": min_z,
        "max_z": max_z,
    }


def _medir_spread_chanfro_ponta(inst, use_p0, use_p1, at_ca):
    """
    Mede o chanfro (diferenca longitudinal em s) apenas na ponta CA (s_min) ou CB (s_max) de `inst`.
    Retorna spread em pes (< 0.012 = corte reto 90 deg; > 0.02 = chanfro 45 deg), ou None se indisponivel.
    """
    vec = XYZ(use_p1.X - use_p0.X, use_p1.Y - use_p0.Y, 0.0)
    L = vec.GetLength()
    if L < 0.05:
        return None
    t_dir = vec.Normalize()
    opt = Options()
    opt.DetailLevel = ViewDetailLevel.Fine
    geom = inst.get_Geometry(opt)
    if geom is None:
        return None

    solids = []
    stack = [geom]
    while stack:
        g = stack.pop()
        for obj in g:
            if isinstance(obj, Solid) and obj.Volume > 1e-8:
                solids.append(obj)
            elif hasattr(obj, "GetInstanceGeometry"):
                try:
                    stack.append(obj.GetInstanceGeometry())
                except Exception:
                    pass
    if not solids:
        return None

    styled = [s for s in solids if _eid_int(getattr(s, "GraphicsStyleId", None)) > 0]
    use_solids = styled if styled else solids

    s_vals = []
    for obj in use_solids:
        try:
            for face in obj.Faces:
                try:
                    mesh = face.Triangulate()
                    if mesh is not None:
                        for wp in mesh.Vertices:
                            s = (wp.X - use_p0.X) * t_dir.X + (wp.Y - use_p0.Y) * t_dir.Y
                            s_vals.append(s)
                except Exception:
                    pass
        except Exception:
            pass
        try:
            for ed in obj.Edges:
                for wp in ed.Tessellate():
                    s = (wp.X - use_p0.X) * t_dir.X + (wp.Y - use_p0.Y) * t_dir.Y
                    s_vals.append(s)
        except Exception:
            pass
    if not s_vals:
        return None
    s_min_all = min(s_vals)
    s_max_all = max(s_vals)
    win_len = min(0.20, 0.35 * max(s_max_all - s_min_all, 0.05))
    if at_ca:
        end_s = [s for s in s_vals if s <= s_min_all + win_len]
    else:
        end_s = [s for s in s_vals if s >= s_max_all - win_len]
    if not end_s:
        return None
    return max(end_s) - min(end_s)


def _aplicar_angulo_canto_seguro(inst, is_start, deg, doc=None, use_p0=None, use_p1=None, cache_flip_dict=None, sym_id_int=None):
    """
    Aplica OC_Angulo CA (inicio) ou CB (fim) diretamente em radianos:
      - 0.0 deg para pontas retas (portas/vaos)
      - +45.0 deg para cantos internos (concavos)
      - -45.0 deg para cantos externos (convexos)
    """
    if deg is None:
        return False

    exact_names = [u"OC_Ângulo CA", u"OC_Angulo CA"] if is_start else [u"OC_Ângulo CB", u"OC_Angulo CB"]
    target_suffix = u"ca" if is_start else u"cb"

    def _find_param():
        for nm in exact_names:
            try:
                p = inst.LookupParameter(nm)
                if p is not None and not p.IsReadOnly:
                    return p
            except Exception:
                pass
        try:
            for p in inst.Parameters:
                nm = (p.Definition.Name or "").strip().lower()
                if not p.IsReadOnly and (u"âng" in nm or u"ang" in nm or "corte" in nm) and nm.endswith(target_suffix):
                    return p
        except Exception:
            pass
        return None

    p = _find_param()
    if p is None:
        return False

    try:
        alvo_rad = math.radians(float(deg))
        ok = p.Set(float(alvo_rad))
        return (ok is not False)
    except Exception:
        return False


def _instancia_suporta_angulo_canto(inst):
    if inst is None:
        return False
    for nm in (u"OC_Ângulo CA", u"OC_Angulo CA", u"OC_Ângulo CB", u"OC_Angulo CB"):
        try:
            p = inst.LookupParameter(nm)
            if p is not None and not p.IsReadOnly:
                return True
        except Exception:
            pass
    try:
        for p in inst.Parameters:
            nm = (p.Definition.Name or "").strip().lower()
            if not p.IsReadOnly and (u"âng" in nm or u"ang" in nm) and (nm.endswith("ca") or nm.endswith("cb")):
                return True
    except Exception:
        pass
    return False


def _set_perfil_angulo(inst, deg):
    if inst is None or deg is None:
        return False
    rad = math.radians(float(deg))
    for nm in (u"Ângulo perfil", u"Ângulo do perfil", u"Angulo perfil", u"Angulo do perfil"):
        try:
            p = inst.LookupParameter(nm)
            if p is not None and not p.IsReadOnly:
                ok = p.Set(float(rad))
                if ok is not False:
                    return True
        except Exception:
            pass
    try:
        for p in inst.Parameters:
            nm = (p.Definition.Name or "").strip().lower()
            if not p.IsReadOnly and (u"âng" in nm or u"ang" in nm) and u"perfil" in nm:
                ok = p.Set(float(rad))
                if ok is not False:
                    return True
    except Exception:
        pass
    return False


def _set_inverter_perfil(inst, flag):
    if inst is None or flag is None:
        return False
    ival = 1 if flag else 0
    for nm in (u"Inverter perfil", u"Inverter Perfil"):
        try:
            p = inst.LookupParameter(nm)
            if p is not None and not p.IsReadOnly:
                p.Set(int(ival))
                return True
        except Exception:
            pass
    try:
        for p in inst.Parameters:
            nm = (p.Definition.Name or "").strip().lower()
            if not p.IsReadOnly and u"invert" in nm and u"perfil" in nm:
                p.Set(int(ival))
                return True
    except Exception:
        pass
    return False


def _aplicar_parametros_instancia(inst, rot_deg=None, inv_perfil=None, ceil_thick_ft=None):
    if inv_perfil is not None:
        _set_inverter_perfil(inst, inv_perfil)
    if rot_deg is not None:
        _set_perfil_angulo(inst, rot_deg)
    if ceil_thick_ft is not None and ceil_thick_ft > 0.001:
        _set_param_double(inst, [u"Forro_Espessura", u"Espessura Forro"], ceil_thick_ft)


def _colocar_curva_familia(
    doc,
    symbol,
    level,
    p0,
    p1,
    target_z,
    inward_dir,
    cache_flip_dict,
    invert_manual=False,
    expect_on_wall_side=False,
    start_miter_deg=None,
    end_miter_deg=None,
    host_elem_for_void=None,
    try_void_cut=False,
    rot_deg=None,
    inv_perfil=None,
    auto_upright=False,
    ceil_thick_ft=None,
    material_id=None,
    overwrite_autobim_mat=False,
    cache_sym_mats=None,
):
    """
    Insere uma instancia CurveBased de `symbol` alinhando:
      - parametros iniciais (rotacao do perfil, Inverter perfil, Forro_Espessura) antes da calibracao,
      - orientacao lateral automaticamente pela geometria 3D real da familia (calibrada sem invert_manual!),
      - cota vertical para `target_z` (relativa a `level.Elevation`, tolerancia ~0.1 mm),
      - angulos `OC_Ângulo CA/CB` quando aplicavel,
      - corte com vazio `AddInstanceVoidCut` se solicitado e a familia tiver vazio.
    """
    if not symbol.IsActive:
        symbol.Activate()
        doc.Regenerate()

    sym_key = (
        _eid_int(symbol.Id),
        bool(expect_on_wall_side),
        None if rot_deg is None else round(float(rot_deg), 2),
        None if inv_perfil is None else bool(inv_perfil),
        bool(auto_upright),
    )
    flip_dir = cache_flip_dict.get(sym_key, None)

    lvl_elev = level.Elevation if level else 0.0
    z_off = target_z - lvl_elev

    if flip_dir is None:
        # Calibrar SEMPRE na direcao padrao (p0 -> p1) sem invert_manual para nunca anular o checkbox!
        line_test = Line.CreateBound(XYZ(p0.X, p0.Y, lvl_elev), XYZ(p1.X, p1.Y, lvl_elev))
        inst = doc.Create.NewFamilyInstance(line_test, symbol, level, StructuralType.NonStructural)
        _set_bip_double(inst, BuiltInParameter.INSTANCE_FREE_HOST_OFFSET_PARAM, z_off)
        _aplicar_parametros_instancia(inst, rot_deg=rot_deg, inv_perfil=inv_perfil, ceil_thick_ft=ceil_thick_ft)
        doc.Regenerate()

        mid_xy = XYZ(0.5 * (p0.X + p1.X), 0.5 * (p0.Y + p1.Y), target_z)
        metrics = _solid_centroid_xy_along(inst, mid_xy, inward_dir)
        chosen_rot = rot_deg
        if auto_upright and rot_deg is None and metrics is not None:
            proj_span = metrics["max_proj"] - metrics["min_proj"]
            z_span = metrics["max_z"] - metrics["min_z"]
            if proj_span > max(z_span * 1.4, 0.01) and _set_perfil_angulo(inst, 90.0):
                doc.Regenerate()
                m_rot = _solid_centroid_xy_along(inst, mid_xy, inward_dir)
                if m_rot is not None and (m_rot["max_z"] - m_rot["min_z"]) > (m_rot["max_proj"] - m_rot["min_proj"]):
                    chosen_rot = 90.0
                    metrics = m_rot
                else:
                    _set_perfil_angulo(inst, 0.0)
                    doc.Regenerate()
        cache_flip_dict[("autorot", sym_key)] = chosen_rot

        should_flip = False
        if metrics is not None:
            proj = metrics["centroid_proj"]
            if not expect_on_wall_side and proj < -0.002:
                should_flip = True
            elif expect_on_wall_side and proj > 0.002:
                should_flip = True
        cache_flip_dict[sym_key] = should_flip

        final_flip = bool(should_flip) ^ bool(invert_manual)
        if final_flip:
            doc.Delete(inst.Id)
            line2 = Line.CreateBound(XYZ(p1.X, p1.Y, lvl_elev), XYZ(p0.X, p0.Y, lvl_elev))
            inst = doc.Create.NewFamilyInstance(line2, symbol, level, StructuralType.NonStructural)
            _set_bip_double(inst, BuiltInParameter.INSTANCE_FREE_HOST_OFFSET_PARAM, z_off)
            _aplicar_parametros_instancia(inst, rot_deg=chosen_rot, inv_perfil=inv_perfil, ceil_thick_ft=ceil_thick_ft)
            doc.Regenerate()

        ref_pt = p1 if final_flip else p0
        m2 = _solid_centroid_xy_along(inst, XYZ(ref_pt.X, ref_pt.Y, target_z), inward_dir)
        if m2 is not None:
            cache_flip_dict[("max_proj", sym_key)] = max(0.0, abs(m2["max_proj"]))
            if abs(m2["min_z"] - target_z) > 0.0003:
                corr = target_z - m2["min_z"]
                _set_bip_double(inst, BuiltInParameter.INSTANCE_FREE_HOST_OFFSET_PARAM, z_off + corr)
                cache_flip_dict[("zcorr", sym_key)] = corr
            else:
                cache_flip_dict[("zcorr", sym_key)] = 0.0
        else:
            cache_flip_dict[("zcorr", sym_key)] = 0.0
    else:
        final_flip = bool(flip_dir) ^ bool(invert_manual)
        use_p0, use_p1 = (p1, p0) if final_flip else (p0, p1)
        line = Line.CreateBound(XYZ(use_p0.X, use_p0.Y, lvl_elev), XYZ(use_p1.X, use_p1.Y, lvl_elev))
        inst = doc.Create.NewFamilyInstance(line, symbol, level, StructuralType.NonStructural)
        zcorr = cache_flip_dict.get(("zcorr", sym_key), 0.0)
        chosen_rot = cache_flip_dict.get(("autorot", sym_key), rot_deg)
        _set_bip_double(inst, BuiltInParameter.INSTANCE_FREE_HOST_OFFSET_PARAM, z_off + zcorr)
        _aplicar_parametros_instancia(inst, rot_deg=chosen_rot, inv_perfil=inv_perfil, ceil_thick_ft=ceil_thick_ft)

    # Angulos de esquadria OC_Angulo CA / CB
    ca_deg = end_miter_deg if final_flip else start_miter_deg
    cb_deg = start_miter_deg if final_flip else end_miter_deg
    cur_p0, cur_p1 = (p1, p0) if final_flip else (p0, p1)
    sym_id_i = _eid_int(symbol.Id)
    _aplicar_angulo_canto_seguro(inst, True, ca_deg, doc=doc, use_p0=cur_p0, use_p1=cur_p1, cache_flip_dict=cache_flip_dict, sym_id_int=sym_id_i)
    _aplicar_angulo_canto_seguro(inst, False, cb_deg, doc=doc, use_p0=cur_p0, use_p1=cur_p1, cache_flip_dict=cache_flip_dict, sym_id_int=sym_id_i)

    # Void cut automatico
    void_cut_ok = False
    if try_void_cut and host_elem_for_void is not None:
        hosts = host_elem_for_void if isinstance(host_elem_for_void, (list, tuple)) else [host_elem_for_void]
        try:
            if InstanceVoidCutUtils.IsVoidInstanceCuttingFamily(inst):
                for h_el in hosts:
                    if h_el is not None and InstanceVoidCutUtils.CanBeCutWithVoid(h_el):
                        try:
                            InstanceVoidCutUtils.AddInstanceVoidCut(doc, h_el, inst)
                            void_cut_ok = True
                        except Exception:
                            pass
        except Exception:
            pass

    if material_id is not None and _eid_int(material_id) > 0:
        _aplicar_material_elemento(
            doc, inst, material_id,
            overwrite_autobim=overwrite_autobim_mat,
            cache_sym_mats=cache_sym_mats,
        )

    return inst, void_cut_ok


# ---------------------------------------------------------------------------
# Gerador Parametrico de Familias Baseadas em Linha (.rfa) do AutoBIM
# ---------------------------------------------------------------------------

def _encontrar_template_linha():
    for d in RFA_TEMPLATES_DIRS:
        if not os.path.isdir(d):
            continue
        for nm in LINE_BASED_RFT_NAMES:
            p = os.path.join(d, nm)
            if os.path.isfile(p):
                return p
    return None


def _intersecar_retas_xy(p1, d1, p2, d2):
    """Intersecao 2D no plano XY das retas (p1 + t*d1) e (p2 + u*d2). Retorna XYZ no Z de p1 ou None se paralelas."""
    det = d1.X * d2.Y - d1.Y * d2.X
    if abs(det) < 0.1:
        return None
    rx = p2.X - p1.X
    ry = p2.Y - p1.Y
    t = (rx * d2.Y - ry * d2.X) / det
    return XYZ(p1.X + t * d1.X, p1.Y + t * d1.Y, p1.Z)


def _try_join_pair(doc, el_a, el_b):
    """Tenta unir dois elementos no Revit via JoinGeometryUtils sem propagar falhas."""
    if el_a is None or el_b is None or el_a.Id == el_b.Id:
        return False
    try:
        if not JoinGeometryUtils.AreElementsJoined(doc, el_a, el_b):
            JoinGeometryUtils.JoinGeometry(doc, el_a, el_b)
        return True
    except Exception:
        return False


def _unir_instancias_cantos_loop(doc, loop_items, tol_dist=0.35):
    """
    Une via JoinGeometryUtils.JoinGeometry pares consecutivos de instancias FamilyInstance
    que se encontram nos cantos de um loop fechado (incluindo fechamento ultimo->primeiro).
    Retorna a quantidade de unioes aplicadas.
    """
    n = len(loop_items)
    if n < 2:
        return 0
    joined = 0
    for i in range(n):
        pa_a, pb_a, inst_a = loop_items[i]
        pa_b, pb_b, inst_b = loop_items[(i + 1) % n]
        if inst_a is None or inst_b is None or inst_a.Id == inst_b.Id:
            continue
        if pb_a.DistanceTo(pa_b) > tol_dist:
            continue
        try:
            if not JoinGeometryUtils.AreElementsJoined(doc, inst_a, inst_b):
                JoinGeometryUtils.JoinGeometry(doc, inst_a, inst_b)
                joined += 1
        except Exception:
            pass
    return joined


def _diagnosticar_familia_oc(doc, symbol):
    """Inspeciona (somente leitura) parametros, formulas, formas solidas/vazias e cotas de uma familia OC."""
    if doc is None or symbol is None or getattr(symbol, "Family", None) is None:
        return
    dbg_dir = r"C:\Users\Alex\.gemini\antigravity\brain\aece5e12-b21b-4b3c-b801-2bf89559d5d3\scratch"
    if not os.path.isdir(dbg_dir):
        return
    out_path = os.path.join(dbg_dir, "oc_family_inspect.txt")
    fdoc = None
    lines = []
    try:
        from Autodesk.Revit.DB import Dimension as _RvtDim, GenericForm as _RvtGF
        fam = symbol.Family
        lines.append(u"=== FAMILY: {0} | Symbol: {1} ===".format(_ename(fam), _ename(symbol)))
        fdoc = doc.EditFamily(fam)
        fm = fdoc.FamilyManager
        lines.append(u"[FamilyParameters]")
        ct = fm.CurrentType
        for fp in fm.Parameters:
            nm = fp.Definition.Name
            inst_str = "INST" if fp.IsInstance else "TYPE"
            form_str = fp.Formula if fp.IsDeterminedByFormula else ""
            val_str = ""
            try:
                if ct is not None:
                    vs = ct.AsValueString(fp)
                    if vs is not None:
                        val_str = vs
                    elif str(fp.StorageType) == "Double":
                        val_str = str(round(ct.AsDouble(fp), 5))
                    elif str(fp.StorageType) == "Integer":
                        val_str = str(ct.AsInteger(fp))
                    elif str(fp.StorageType) == "String":
                        val_str = ct.AsString(fp) or ""
            except Exception:
                pass
            lines.append(u"  * {0} [{1}|{2}] val={3} formula={4}".format(
                nm, inst_str, fp.StorageType, val_str, form_str
            ))

        lines.append(u"[GenericForms]")
        for gf in FilteredElementCollector(fdoc).OfClass(_RvtGF):
            try:
                is_sol = getattr(gf, "IsSolid", None)
                subcat = ""
                try:
                    sc = gf.Subcategory
                    if sc is not None:
                        subcat = sc.Name
                except Exception:
                    pass
                lines.append(u"  * {0} (id={1}) IsSolid={2} Subcat={3}".format(
                    type(gf).__name__, _eid_int(gf.Id), is_sol, subcat
                ))
            except Exception:
                pass

        lines.append(u"[NestedFamilyInstances]")
        for fi in FilteredElementCollector(fdoc).OfClass(FamilyInstance):
            try:
                snm = _ename(fi.Symbol.Family) if getattr(fi, "Symbol", None) else "?"
                tnm = _ename(fi.Symbol) if getattr(fi, "Symbol", None) else "?"
                lines.append(u"  * {0} : {1} (id={2})".format(snm, tnm, _eid_int(fi.Id)))
                for p in fi.Parameters:
                    try:
                        pnm = p.Definition.Name
                        if any(k in pnm.lower() for k in (u"ang", u"âng", u"corte", u"ca", u"cb", u"larg", u"alt", u"comp")):
                            lines.append(u"      param {0} = {1}".format(pnm, p.AsValueString() or ""))
                    except Exception:
                        pass
            except Exception:
                pass

        lines.append(u"[LabeledDimensions]")
        for dm in FilteredElementCollector(fdoc).OfClass(_RvtDim):
            try:
                lbl = dm.FamilyLabel
                if lbl is not None:
                    lines.append(u"  * {0} -> val={1} (dim={2})".format(
                        lbl.Definition.Name, dm.ValueString, _ename(dm.DimensionType)
                    ))
            except Exception:
                pass
    except Exception as ex:
        lines.append(u"[ERROR] {0}".format(ex))
    finally:
        if fdoc is not None:
            try:
                fdoc.Close(False)
            except Exception:
                pass
    try:
        with codecs.open(out_path, "w", "utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception:
        pass


def _desunir_instancias_cantos_loop(doc, loop_items, tol_dist=0.35):
    """
    Garante que pares consecutivos de instancias FamilyInstance nos cantos NAO fiquem
    unidos automaticamente (UnjoinGeometry caso o Revit tenha unido no posicionamento).
    """
    n = len(loop_items)
    if n < 2:
        return 0
    unjoined = 0
    for i in range(n):
        pa_a, pb_a, inst_a = loop_items[i]
        pa_b, pb_b, inst_b = loop_items[(i + 1) % n]
        if inst_a is None or inst_b is None or inst_a.Id == inst_b.Id:
            continue
        if pb_a.DistanceTo(pa_b) > tol_dist:
            continue
        try:
            if JoinGeometryUtils.AreElementsJoined(doc, inst_a, inst_b):
                JoinGeometryUtils.UnjoinGeometry(doc, inst_a, inst_b)
                unjoined += 1
        except Exception:
            pass
    return unjoined


def _tabica_z_params(p):
    esp = max(p.get("esp_ft", 1.0 * 0.001 * FT), 0.0015)
    w = max(p.get("w_ft", 2.1 * CM_TO_FT), esp + 0.008)
    h_enc = max(p.get("encaixe_ft", 1.4 * CM_TO_FT), 0.005)
    h_reb = max(p.get("h_ft", 1.4 * CM_TO_FT), 0.005)
    aba_par = max(p.get("aba_par_ft", 2.0 * CM_TO_FT), esp + 0.01)
    aba_forro = max(p.get("aba_ft", 2.1 * CM_TO_FT), esp + 0.01)
    y_mid = h_enc + h_reb
    y_top = y_mid + aba_par
    x_shelf_end = w + esp + aba_forro
    return esp, w, h_enc, h_reb, aba_par, aba_forro, y_mid, y_top, x_shelf_end


def _profile_loops_pts(kind, p):
    """
    Retorna lista de pontos 2D (X = projecao perpendicular em pes, Y = altura Z em pes).
    +X aponta na direcao desejada (para fora/dentro conforme familia); +Y aponta para CIMA.
    """
    esp = max(p.get("esp_ft", 0.005), 0.0015)
    h = max(p.get("h_ft", 0.15), 0.01)
    w = max(p.get("w_ft", 0.10), 0.01)

    if kind == "Tabica_Z":
        # Tabica Z Print 1: aba vertical na parede (X=0), ponte do vao (0..w), desce (y_mid..0),
        # e prateleira horizontal do forro em Y=h_enc (cota inferior vertical = h_enc, ex. 14 mm)
        esp_t, w_eff, h_enc, _, _, _, y_mid, y_top, x_shelf_end = _tabica_z_params(p)
        return [
            (w_eff, 0.0),
            (w_eff + esp_t, 0.0),
            (w_eff + esp_t, h_enc),
            (x_shelf_end, h_enc),
            (x_shelf_end, h_enc + esp_t),
            (w_eff + esp_t, h_enc + esp_t),
            (w_eff + esp_t, y_mid + esp_t),
            (esp_t, y_mid + esp_t),
            (esp_t, y_top),
            (0.0, y_top),
            (0.0, y_mid),
            (w_eff, y_mid),
        ]
    elif kind == "Cortineiro_L":
        # L invertido: tampo horizontal superior encostado na parede + testeira vertical frontal (aberto embaixo)
        return [
            (w - esp, 0.0),
            (w, 0.0),
            (w, h),
            (0.0, h),
            (0.0, h - esp),
            (w - esp, h - esp),
        ]
    elif kind == "Cortineiro_U":
        # Caixa U invertida completa (teto + frente + retorno tras)
        return [
            (0.0, 0.0),
            (esp, 0.0),
            (esp, h - esp),
            (w - esp, h - esp),
            (w - esp, 0.0),
            (w, 0.0),
            (w, h),
            (0.0, h),
        ]
    elif kind == "Cortineiro_Tampa":
        # Tampa lateral centralizada no eixo da linha de retorno
        hw = 0.5 * w
        return [
            (-hw, 0.0),
            (hw, 0.0),
            (hw, h),
            (-hw, h),
        ]
    elif kind == "Sanca_Chanfrada":
        return [
            (0.0, 0.0),
            (w, 0.0),
            (w, 0.30 * h),
            (0.30 * w, h),
            (0.0, h),
        ]
    elif kind == "Sanca_Degrau":
        return [
            (0.0, 0.0),
            (w, 0.0),
            (w, 0.50 * h),
            (0.50 * w, 0.50 * h),
            (0.50 * w, h),
            (0.0, h),
        ]
    elif kind == "Sanca_MeiaCana":
        # Aproximacao poligonal suave (8 segmentos) em concavo elegante
        pts = [(0.0, 0.0), (w, 0.0)]
        steps = 8
        for i in range(steps + 1):
            ang = 0.5 * math.pi * (float(i) / steps)
            cx = w * math.cos(ang)
            cy = h * math.sin(ang)
            if i > 0:
                pts.append((cx, cy))
        return pts
    elif kind == "SantaLuzia_VersaLED":
        # Santa Luzia VersaLED (desenho tecnico 70x17 mm): frente plana para sala (X=w),
        # abas LED recuadas 13mm x 9mm sup/inf e 2 canais traseiros 2x3mm contra a parede (X=0)
        x_step = (9.0 / 17.0) * w
        x_fenda = (2.0 / 17.0) * w
        y1 = (13.0 / 70.0) * h
        y2 = (23.0 / 70.0) * h
        y3 = (26.0 / 70.0) * h
        y4 = (44.0 / 70.0) * h
        y5 = (47.0 / 70.0) * h
        y6 = (57.0 / 70.0) * h
        return [
            (x_step, 0.0),
            (w, 0.0),
            (w, h),
            (x_step, h),
            (x_step, y6),
            (0.0, y6),
            (0.0, y5),
            (x_fenda, y5),
            (x_fenda, y4),
            (0.0, y4),
            (0.0, y3),
            (x_fenda, y3),
            (x_fenda, y2),
            (0.0, y2),
            (0.0, y1),
            (x_step, y1),
        ]
    elif kind == "SantaLuzia_466":
        # Santa Luzia Ref. 466 (30x16 mm): face frontal reta e cavidade traseira central contra a parede (0.40..0.60h x 0.46w)
        return [
            (0.0, 0.0),
            (w, 0.0),
            (w, h),
            (0.0, h),
            (0.0, 0.60 * h),
            (0.46 * w, 0.60 * h),
            (0.46 * w, 0.40 * h),
            (0.0, 0.40 * h),
        ]
    elif kind == "SantaLuzia_468":
        # Santa Luzia Ref. 468 (27x11 mm): ajuste fino por silhueta oficial
        # Base reta 0.70w, bojo convexo suave ate 1.00w (y=0.35h), filete vertical 0.67w, concavo ate topo 0.44w
        return [
            (0.0, 0.0),
            (0.70 * w, 0.0),
            (0.70 * w, 0.045 * h),
            (0.81 * w, 0.095 * h),
            (0.91 * w, 0.171 * h),
            (0.98 * w, 0.267 * h),
            (1.00 * w, 0.350 * h),
            (0.99 * w, 0.405 * h),
            (0.95 * w, 0.467 * h),
            (0.88 * w, 0.533 * h),
            (0.79 * w, 0.600 * h),
            (0.67 * w, 0.667 * h),
            (0.67 * w, 0.733 * h),
            (0.54 * w, 0.800 * h),
            (0.47 * w, 0.855 * h),
            (0.44 * w, 0.900 * h),
            (0.44 * w, h),
            (0.0, h),
        ]
    elif kind == "Moldura_Chanfrada":
        c = min(0.35 * h, 0.65 * w)
        return [
            (0.0, 0.0),
            (w, c),
            (w, h - c),
            (0.0, h),
        ]
    elif kind == "Moldura_Frisada":
        f1 = 0.25 * h
        f2 = 0.50 * h
        f3 = 0.75 * h
        d = 0.35 * w
        return [
            (0.0, 0.0),
            (w, 0.0),
            (w, f1),
            (w - d, f1 + 0.05 * h),
            (w, f2 - 0.05 * h),
            (w, f2 + 0.05 * h),
            (w - d, f3 - 0.05 * h),
            (w, f3),
            (w, h),
            (0.0, h),
        ]
    elif kind == "Moldura_Boleada":
        pts = [(0.0, 0.0), (w * 0.55, 0.0)]
        steps = 7
        for i in range(1, steps):
            ang = -0.5 * math.pi + math.pi * (float(i) / steps)
            px = w * 0.55 + w * 0.45 * math.cos(ang)
            py = 0.5 * h + 0.5 * h * math.sin(ang)
            pts.append((px, py))
        pts.append((w * 0.55, h))
        pts.append((0.0, h))
        return pts
    elif kind == "SantaLuzia_493":
        # Santa Luzia Classico 2 Frisos (493/505/547): base reta + 2 frisos superiores + topo boleado
        return [
            (0.0, 0.0),
            (w, 0.0),
            (w, 0.72 * h),
            (0.82 * w, 0.72 * h),
            (0.82 * w, 0.78 * h),
            (0.95 * w, 0.78 * h),
            (0.95 * w, 0.80 * h),
            (0.72 * w, 0.80 * h),
            (0.72 * w, 0.85 * h),
            (0.86 * w, 0.85 * h),
            (0.76 * w, 0.94 * h),
            (0.58 * w, 0.99 * h),
            (0.38 * w, h),
            (0.0, h),
        ]
    elif kind == "SantaLuzia_Boleado":
        return [
            (0.0, 0.0),
            (w, 0.0),
            (w, 0.84 * h),
            (0.90 * w, 0.93 * h),
            (0.68 * w, 0.99 * h),
            (0.35 * w, h),
            (0.0, h),
        ]
    elif kind == "Rodape_Invertido_L":
        t_aba = max(min(0.18 * w, 0.25 * h), 0.008)
        return [
            (0.0, 0.0),
            (t_aba, 0.0),
            (t_aba, h - t_aba),
            (w, h - t_aba),
            (w, h),
            (0.0, h),
        ]
    elif kind in ("Rodape_Reto", "Rodape_Embutido"):
        return [(0.0, 0.0), (w, 0.0), (w, h), (0.0, h)]
    # Fallback Reto
    return [(0.0, 0.0), (w, 0.0), (w, h), (0.0, h)]


def _profile_curve_specs(kind, p):
    """
    Retorna especificacao exata de curvas 2D fechadas:
      ("line", (x0, y0), (x1, y1)) ou ("arc", (x0, y0), (x1, y1), (xm, ym)).
    Permite arcos circulares verdadeiros (Arc.Create) sem facetas poligonais.
    """
    h = max(p.get("h_ft", 0.15), 0.01)
    w = max(p.get("w_ft", 0.10), 0.01)
    if kind == "SantaLuzia_493":
        return [
            ("line", (0.0, 0.0), (w, 0.0)),
            ("line", (w, 0.0), (w, 0.72 * h)),
            ("line", (w, 0.72 * h), (0.82 * w, 0.72 * h)),
            ("line", (0.82 * w, 0.72 * h), (0.82 * w, 0.78 * h)),
            ("line", (0.82 * w, 0.78 * h), (0.95 * w, 0.78 * h)),
            ("line", (0.95 * w, 0.78 * h), (0.95 * w, 0.80 * h)),
            ("line", (0.95 * w, 0.80 * h), (0.72 * w, 0.80 * h)),
            ("line", (0.72 * w, 0.80 * h), (0.72 * w, 0.85 * h)),
            ("line", (0.72 * w, 0.85 * h), (0.86 * w, 0.85 * h)),
            ("arc", (0.86 * w, 0.85 * h), (0.38 * w, h), (0.76 * w, 0.96 * h)),
            ("line", (0.38 * w, h), (0.0, h)),
            ("line", (0.0, h), (0.0, 0.0)),
        ]
    if kind == "SantaLuzia_Boleado":
        return [
            ("line", (0.0, 0.0), (w, 0.0)),
            ("line", (w, 0.0), (w, 0.84 * h)),
            ("arc", (w, 0.84 * h), (0.35 * w, h), (0.82 * w, 0.96 * h)),
            ("line", (0.35 * w, h), (0.0, h)),
            ("line", (0.0, h), (0.0, 0.0)),
        ]
    if kind == "SantaLuzia_468":
        return [
            ("line", (0.0, 0.0), (0.70 * w, 0.0)),
            ("line", (0.70 * w, 0.0), (0.70 * w, 0.055 * h)),
            ("arc", (0.70 * w, 0.055 * h), (0.70 * w, 0.625 * h), (1.00 * w, 0.340 * h)),
            ("line", (0.70 * w, 0.625 * h), (0.70 * w, 0.680 * h)),
            ("arc", (0.70 * w, 0.680 * h), (0.44 * w, 0.945 * h), (0.5075 * w, 0.802 * h)),
            ("line", (0.44 * w, 0.945 * h), (0.44 * w, h)),
            ("line", (0.44 * w, h), (0.0, h)),
            ("line", (0.0, h), (0.0, 0.0)),
        ]
    pts_list = _profile_loops_pts(kind, p)
    specs = []
    for idx in range(len(pts_list)):
        specs.append(("line", pts_list[idx], pts_list[(idx + 1) % len(pts_list)]))
    return specs


def _obter_ou_criar_familia_autobim(doc, fam_name, perfil_kind, params_dict):
    """
    Retorna um FamilySymbol ativo do tipo `fam_name` no projeto atual.
    Se ainda nao existir no projeto, gera um `.rfa` parametrico baseado em linha via `NewSweep`
    a partir de `Modelo genérico métrico com base na linha.rft` e carrega no projeto.
    """
    safe_name = "".join(ch if (ch.isalnum() or ch in ("_", "-")) else "_" for ch in fam_name)

    # 1. Verificar se ja existe no projeto
    for sym in FilteredElementCollector(doc).OfClass(FamilySymbol).OfCategory(BuiltInCategory.OST_GenericModel):
        try:
            if _ename(sym.Family) in (fam_name, safe_name):
                if not sym.IsActive:
                    sym.Activate()
                return sym
        except Exception:
            pass

    rft_path = _encontrar_template_linha()
    if not rft_path:
        raise Exception(u"Template 'Modelo genérico métrico com base na linha.rft' não encontrado na pasta padrão do Revit.")

    if not os.path.isdir(CACHE_RFA_DIR):
        os.makedirs(CACHE_RFA_DIR)

    rfa_path = os.path.join(CACHE_RFA_DIR, safe_name + ".rfa")

    app = doc.Application
    fdoc = app.NewFamilyDocument(rft_path)
    try:
        # Localizar ReferenceLine do template baseado em linha
        ref_line = None
        for ce in FilteredElementCollector(fdoc).OfClass(CurveElement):
            tn = type(ce).__name__
            if "ReferenceLine" in tn or getattr(ce, "IsReferenceLine", False):
                ref_line = ce
                break
        if ref_line is None:
            for ce in FilteredElementCollector(fdoc).OfClass(CurveElement):
                if isinstance(ce.GeometryCurve, Line) and ce.GeometryCurve.Reference is not None:
                    ref_line = ce
                    break
        if ref_line is None:
            raise Exception(u"Não foi possível localizar a ReferenceLine no template baseado em linha.")

        path_refs = ReferenceArray()
        path_refs.Append(ref_line.GeometryCurve.Reference)

        def _specs_to_sweep(curve_specs):
            carr_loc = CurveArray()
            for item in curve_specs:
                ctype = item[0]
                a, b = item[1], item[2]
                pa = XYZ(a[0], a[1], 0.0)
                pb = XYZ(b[0], b[1], 0.0)
                if pa.DistanceTo(pb) <= 1e-6:
                    continue
                if ctype == "arc":
                    m = item[3]
                    pm = XYZ(m[0], m[1], 0.0)
                    try:
                        carr_loc.Append(Arc.Create(pa, pb, pm))
                    except Exception:
                        carr_loc.Append(Line.CreateBound(pa, pb))
                else:
                    carr_loc.Append(Line.CreateBound(pa, pb))
            ca_loc = CurveArrArray()
            ca_loc.Append(carr_loc)
            prof_loc = app.Create.NewCurveLoopsProfile(ca_loc)
            return fdoc.FamilyCreate.NewSweep(True, path_refs, prof_loc, 0, ProfilePlaneLocation.Start)

        def _pts_to_sweep(pts_list):
            specs_loc = [("line", pts_list[i], pts_list[(i + 1) % len(pts_list)]) for i in range(len(pts_list))]
            return _specs_to_sweep(specs_loc)

        def _find_vert_offset_param(sw_el):
            for prm in sw_el.Parameters:
                try:
                    if not fdoc.FamilyManager.CanElementParameterBeAssociated(prm):
                        continue
                    if prm.StorageType != StorageType.Double:
                        continue
                    pbip = str(getattr(prm.Definition, "BuiltInParameter", "")).upper()
                    pname = (prm.Definition.Name or "").lower()
                    if ("PROFILE" in pbip and "VERTICAL" in pbip and "OFFSET" in pbip) or (
                        "vertical" in pname and ("deslocamento" in pname or "offset" in pname)
                    ):
                        return prm
                except Exception:
                    pass
            return None

        def _add_fam_len_param(pname, val_ft):
            fp = None
            try:
                from Autodesk.Revit.DB import GroupTypeId, SpecTypeId
                fp = fdoc.FamilyManager.AddParameter(pname, GroupTypeId.Geometry, SpecTypeId.Length, False)
            except Exception:
                try:
                    from Autodesk.Revit.DB import BuiltInParameterGroup, ParameterType
                    fp = fdoc.FamilyManager.AddParameter(pname, BuiltInParameterGroup.PG_GEOMETRY, ParameterType.Length, False)
                except Exception:
                    pass
            if fp is not None:
                try:
                    fdoc.FamilyManager.Set(fp, float(val_ft))
                except Exception:
                    pass
            return fp

        tf = Transaction(fdoc, "AutoBIM Criar Sweep")
        tf.Start()
        try:
            sweeps_created = []
            if perfil_kind == "Tabica_Z":
                esp_t, w_eff, h_enc, _, _, _, y_mid, y_top, x_shelf_end = _tabica_z_params(params_dict)
                pts_spine = [
                    (w_eff, 0.0),
                    (w_eff + esp_t, 0.0),
                    (w_eff + esp_t, y_mid + esp_t),
                    (esp_t, y_mid + esp_t),
                    (esp_t, y_top),
                    (0.0, y_top),
                    (0.0, y_mid),
                    (w_eff, y_mid),
                ]
                pts_aba = [
                    (w_eff + 0.5 * esp_t, 0.0),
                    (x_shelf_end, 0.0),
                    (x_shelf_end, esp_t),
                    (w_eff + 0.5 * esp_t, esp_t),
                ]
                sw_spine = _pts_to_sweep(pts_spine)
                sw_aba = _pts_to_sweep(pts_aba)
                fdoc.Regenerate()
                ok_param_aba = False
                p_voff = _find_vert_offset_param(sw_aba)
                if p_voff is not None:
                    try:
                        p_voff.Set(float(h_enc))
                        fdoc.Regenerate()
                        bb_t = sw_aba.get_BoundingBox(None)
                        if bb_t is not None and abs(bb_t.Min.Z - h_enc) < 1e-3:
                            fam_enc = _add_fam_len_param("Espessura_Forro_Encaixe", h_enc)
                            if fam_enc is not None:
                                fdoc.FamilyManager.AssociateElementParameterToFamilyParameter(p_voff, fam_enc)
                                ok_param_aba = True
                    except Exception:
                        ok_param_aba = False
                if ok_param_aba:
                    sweeps_created = [sw_spine, sw_aba]
                else:
                    fdoc.Delete(sw_spine.Id)
                    fdoc.Delete(sw_aba.Id)
                    sweeps_created = [_pts_to_sweep(_profile_loops_pts(perfil_kind, params_dict))]
                    fdoc.Regenerate()
            else:
                sweeps_created = [_specs_to_sweep(_profile_curve_specs(perfil_kind, params_dict))]
                fdoc.Regenerate()

            # Criar parametro de Material para o usuario editar no projeto se quiser
            try:
                fam_mat = None
                for sw in sweeps_created:
                    p_mat = sw.get_Parameter(BuiltInParameter.MATERIAL_ID_PARAM)
                    if p_mat and fdoc.FamilyManager.CanElementParameterBeAssociated(p_mat):
                        if fam_mat is None:
                            try:
                                from Autodesk.Revit.DB import GroupTypeId, SpecTypeId
                                fam_mat = fdoc.FamilyManager.AddParameter("Material_AutoBIM", GroupTypeId.Materials, SpecTypeId.Reference.Material, False)
                            except Exception:
                                try:
                                    from Autodesk.Revit.DB import BuiltInParameterGroup, ParameterType
                                    fam_mat = fdoc.FamilyManager.AddParameter("Material_AutoBIM", BuiltInParameterGroup.PG_MATERIALS, ParameterType.Material, False)
                                except Exception:
                                    pass
                        if fam_mat is not None:
                            fdoc.FamilyManager.AssociateElementParameterToFamilyParameter(p_mat, fam_mat)
            except Exception:
                pass
            tf.Commit()
        except Exception:
            if tf.HasStarted():
                tf.RollBack()
            raise

        sopt = SaveAsOptions()
        sopt.OverwriteExistingFile = True
        fdoc.SaveAs(rfa_path, sopt)
    finally:
        fdoc.Close(False)

    loaded_fam = clr.Reference[Family]()
    doc.LoadFamily(rfa_path, _FamilyLoadOptions(), loaded_fam)
    fam_obj = loaded_fam.Value
    if fam_obj is None:
        # Procurar pelo nome recem-carregado
        for sym in FilteredElementCollector(doc).OfClass(FamilySymbol).OfCategory(BuiltInCategory.OST_GenericModel):
            if _ename(sym.Family) == safe_name or _ename(sym.Family) == fam_name:
                if not sym.IsActive:
                    sym.Activate()
                return sym
        raise Exception(u"Falha ao carregar família gerada '{0}'.".format(safe_name))

    sym_ids = list(fam_obj.GetFamilySymbolIds())
    if not sym_ids:
        raise Exception(u"Família '{0}' sem tipos.".format(safe_name))
    sym = doc.GetElement(sym_ids[0])
    if not sym.IsActive:
        sym.Activate()
    return sym


# ---------------------------------------------------------------------------
# Engine: Ferramenta 1 - TABICAS (Forro Solto)
# ---------------------------------------------------------------------------

XAML_TABICAS = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Tabicas para Forro Solto (AutoBIM)" Width="470" SizeToContent="Height"
        WindowStartupLocation="CenterScreen" ResizeMode="NoResize"
        Background="#F4F5F7" FontFamily="Segoe UI" FontSize="12">
    <StackPanel Margin="18">
        <TextBlock Text="Modo de seleção dos forros" FontWeight="SemiBold"/>
        <ComboBox x:Name="cb_escopo" Margin="0,4,0,10" Height="27"/>

        <TextBlock Text="Sistema / Família de Tabica" FontWeight="SemiBold"/>
        <ComboBox x:Name="cb_modo" Margin="0,4,0,10" Height="27"/>

        <TextBlock Text="Família carregada (quando usar família do projeto)" FontWeight="SemiBold"/>
        <ComboBox x:Name="cb_familia" Margin="0,4,0,10" Height="27"/>

        <Grid Margin="0,0,0,10">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="Auto"/>
                <ColumnDefinition Width="10"/>
                <ColumnDefinition Width="*"/>
            </Grid.ColumnDefinitions>
            <TextBlock Grid.Column="0" Text="Material (AutoBIM / sem material):" VerticalAlignment="Center" FontWeight="SemiBold"/>
            <ComboBox x:Name="cb_material" Grid.Column="2" Height="26"/>
        </Grid>

        <Grid Margin="0,0,0,10">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="12"/>
                <ColumnDefinition Width="*"/>
            </Grid.ColumnDefinitions>
            <StackPanel Grid.Column="0">
                <TextBlock Text="Vão mínimo/máximo parede (cm)" FontWeight="SemiBold"/>
                <Grid Margin="0,4,0,0">
                    <Grid.ColumnDefinitions>
                        <ColumnDefinition Width="*"/>
                        <ColumnDefinition Width="8"/>
                        <ColumnDefinition Width="*"/>
                    </Grid.ColumnDefinitions>
                    <TextBox x:Name="tb_gap_min" Grid.Column="0" Height="26" VerticalContentAlignment="Center" Text="0,0"/>
                    <TextBox x:Name="tb_gap_max" Grid.Column="2" Height="26" VerticalContentAlignment="Center" Text="15,0"/>
                </Grid>
            </StackPanel>
            <StackPanel Grid.Column="2">
                <TextBlock Text="Recuo padrão / encostado (cm)" FontWeight="SemiBold"/>
                <TextBox x:Name="tb_gap_padrao" Margin="0,4,0,0" Height="26" VerticalContentAlignment="Center" Text="2,1"/>
            </StackPanel>
        </Grid>

        <Grid Margin="0,0,0,10">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="10"/>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="10"/>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="10"/>
                <ColumnDefinition Width="*"/>
            </Grid.ColumnDefinitions>
            <StackPanel Grid.Column="0">
                <TextBlock Text="Encaixe forro (mm)" FontWeight="SemiBold"/>
                <TextBox x:Name="tb_encaixe_mm" Margin="0,4,0,0" Height="26" VerticalContentAlignment="Center" Text="14,0"/>
            </StackPanel>
            <StackPanel Grid.Column="2">
                <TextBlock Text="Rebaixo vert. (mm)" FontWeight="SemiBold"/>
                <TextBox x:Name="tb_altura" Margin="0,4,0,0" Height="26" VerticalContentAlignment="Center" Text="14,0"/>
            </StackPanel>
            <StackPanel Grid.Column="4">
                <TextBlock Text="Chapa (mm)" FontWeight="SemiBold"/>
                <TextBox x:Name="tb_esp_mm" Margin="0,4,0,0" Height="26" VerticalContentAlignment="Center" Text="1,0"/>
            </StackPanel>
            <StackPanel Grid.Column="6">
                <TextBlock Text="Desloc. Z (cm)" FontWeight="SemiBold"/>
                <TextBox x:Name="tb_desloc_z" Margin="0,4,0,0" Height="26" VerticalContentAlignment="Center" Text="0,0"/>
            </StackPanel>
        </Grid>

        <CheckBox x:Name="chk_recuar_encostado" IsChecked="True" Margin="0,2,0,4"
                  Content="Recuar automaticamente a borda do forro quando estiver encostado na parede"/>
        <CheckBox x:Name="chk_apenas_com_parede" IsChecked="True" Margin="0,0,0,4"
                  Content="Aplicar apenas em bordas próximas a paredes (ignorar vãos livres/sancas)"/>
        <CheckBox x:Name="chk_void_cut" IsChecked="True" Margin="0,0,0,4"
                  Content="Executar corte com vazio (AddInstanceVoidCut) se família possuir vazio"/>
        <CheckBox x:Name="chk_inverter" IsChecked="False" Margin="0,0,0,14"
                  Content="Inverter lado da tabica (dentro / fora)"/>

        <StackPanel Orientation="Horizontal" HorizontalAlignment="Right">
            <Button x:Name="btn_cancelar" Content="Cancelar" Width="95" Height="30" Margin="0,0,10,0"/>
            <Button x:Name="btn_ok" Content="Criar Tabicas" Width="150" Height="30"
                    Background="#0078D4" Foreground="White" FontWeight="SemiBold"/>
        </StackPanel>
    </StackPanel>
</Window>
"""


def _recuar_arestas_forro_encostadas(doc, ceil, walls_data, recuo_ft, max_search_ft):
    """
    Se o forro possuir bordas encostadas em paredes (folga < 1.5 mm), recua seletivamente apenas
    essas bordas no Sketch do Ceiling via SketchEditScope.
    """
    sk_id = getattr(ceil, "SketchId", ElementId.InvalidElementId)
    if _eid_int(sk_id) <= 0 or recuo_ft <= 1e-4:
        return False
    sk = doc.GetElement(sk_id)
    if not isinstance(sk, Sketch):
        return False

    bottom_z, _ = _obter_info_forro(ceil)
    if bottom_z is None:
        return False

    # Trabalhar sobre o maior loop poligonal do Sketch (externo), independente de sentido CW/CCW
    profile = sk.Profile
    cand_loops = []
    for cl in profile:
        pts = []
        ok_poly = True
        for crv in cl:
            if not isinstance(crv, Line):
                ok_poly = False
                break
            pts.append((crv.GetEndPoint(0).X, crv.GetEndPoint(0).Y))
        if ok_poly and len(pts) >= 3:
            cand_loops.append((abs(_signed_area_2d(pts)), pts))

    if not cand_loops:
        return False
    cand_loops.sort(key=lambda x: -x[0])

    alteracoes = []
    _, raw_pts = cand_loops[0]
    ccw_pts = _orient_loop_xy(raw_pts, want_ccw=True)
    offsets = []
    n = len(ccw_pts)
    any_touch = False
    for i in range(n):
        a = XYZ(ccw_pts[i][0], ccw_pts[i][1], bottom_z)
        b = XYZ(ccw_pts[(i + 1) % n][0], ccw_pts[(i + 1) % n][1], bottom_z)
        L = a.DistanceTo(b)
        if L < 1e-4:
            offsets.append(0.0)
            continue
        t = (b - a).Normalize()
        edge = {"p0": a, "p1": b, "t": t, "outward": XYZ(t.Y, -t.X, 0.0), "length": L}
        gap_ft, near_wall = _medir_recuo_parede_borda(walls_data, edge, bottom_z, max_search_ft)
        if near_wall is not None and gap_ft is not None and gap_ft < 0.005:
            offsets.append(recuo_ft)
            any_touch = True
        else:
            offsets.append(0.0)
    if any_touch:
        new_pts = _deslocar_poligono_seletivo(ccw_pts, offsets)
        if len(new_pts) >= 3:
            alteracoes.append((raw_pts, new_pts))

    if not alteracoes:
        return False

    plane_z = sk.SketchPlane.GetPlane().Origin.Z if sk.SketchPlane else 0.0
    all_sketch_mcs = list(sk.GetAllElements())

    scope = SketchEditScope(doc, "AutoBIM - Recuar Forro para Tabica")
    scope.Start(sk_id)
    try:
        to_del = List[ElementId]()
        for eid in all_sketch_mcs:
            el = doc.GetElement(eid)
            if isinstance(el, CurveElement) and isinstance(el.GeometryCurve, Line):
                m = el.GeometryCurve.Evaluate(0.5, True)
                for old_pts, _ in alteracoes:
                    matched = False
                    for i in range(len(old_pts)):
                        ax, ay = old_pts[i]
                        bx, by = old_pts[(i + 1) % len(old_pts)]
                        d, _ = _dist_point_to_seg_2d(m.X, m.Y, ax, ay, bx, by)
                        if d < 1e-3:
                            to_del.Add(eid)
                            matched = True
                            break
                    if matched:
                        break
        if to_del.Count > 0:
            doc.Delete(to_del)
        for _, new_pts in alteracoes:
            for i in range(len(new_pts)):
                a = new_pts[i]
                b = new_pts[(i + 1) % len(new_pts)]
                pa = XYZ(a[0], a[1], plane_z)
                pb = XYZ(b[0], b[1], plane_z)
                if pa.DistanceTo(pb) > 1e-4:
                    doc.Create.NewModelCurve(Line.CreateBound(pa, pb), sk.SketchPlane)
        scope.Commit(_SwallowFailures())
        return True
    except Exception:
        try:
            scope.Cancel()
        except Exception:
            pass
        return False


def run_tabicas(doc, uidoc, view):
    ceilings = _coletar_ceilings_vista(doc, view)
    prefs = carregar_prefs("tabicas")
    syms = _coletar_curve_based_symbols(doc, ["tabica", "oc_tabica"])
    sym_labels = [_symbol_display(s) for s in syms]
    mats_data = _coletar_materiais(doc)

    win = XamlReader.Parse(XAML_TABICAS)
    cb_escopo = win.FindName("cb_escopo")
    cb_modo = win.FindName("cb_modo")
    cb_familia = win.FindName("cb_familia")
    cb_mat = win.FindName("cb_material")
    tb_gap_min = win.FindName("tb_gap_min")
    tb_gap_max = win.FindName("tb_gap_max")
    tb_gap_padrao = win.FindName("tb_gap_padrao")
    tb_encaixe = win.FindName("tb_encaixe_mm")
    tb_altura = win.FindName("tb_altura")
    tb_esp_mm = win.FindName("tb_esp_mm")
    tb_desloc_z = win.FindName("tb_desloc_z")
    chk_recuar = win.FindName("chk_recuar_encostado")
    chk_apenas = win.FindName("chk_apenas_com_parede")
    chk_void = win.FindName("chk_void_cut")
    chk_inv = win.FindName("chk_inverter")
    btn_ok = win.FindName("btn_ok")
    btn_cancel = win.FindName("btn_cancelar")

    for op in [
        u"Todos os forros da vista ativa ({0} encontrados)".format(len(ceilings)),
        u"Usar forros pré-selecionados (ou selecionar na vista)",
        u"Selecionar forros manualmente na vista agora",
    ]:
        cb_escopo.Items.Add(op)
    cb_escopo.SelectedIndex = prefs.get("escopo_idx", 1 if uidoc.Selection.GetElementIds().Count > 0 else 0)

    for op in [
        u"AutoBIM Paramétrica — Tabica Z (Perfil Z com Encaixe)",
        u"Família carregada no projeto (CurveBased)",
    ]:
        cb_modo.Items.Add(op)
    cb_modo.SelectedIndex = min(int(prefs.get("modo_v3_idx", 0)), 1)

    sc_familia = _anexar_busca_combo_palavras(
        cb_familia, sym_labels, prefs.get("familia"), ["tabica"], placeholder=u"Pesquisar família de tabica..."
    )
    sc_mat = _popular_combo_materiais(cb_mat, mats_data, prefs.get("material"))

    tb_gap_min.Text = prefs.get("gap_min_v2", "0,0")
    tb_gap_max.Text = prefs.get("gap_max", "15,0")
    tb_gap_padrao.Text = prefs.get("gap_padrao_v3", "2,1")
    tb_encaixe.Text = prefs.get("encaixe_mm", "14,0")
    tb_altura.Text = prefs.get("altura_mm_v3", "14,0")
    tb_esp_mm.Text = prefs.get("esp_mm_v3", "1,0")
    tb_desloc_z.Text = prefs.get("desloc_z", "0,0")
    chk_recuar.IsChecked = bool(prefs.get("recuar_encostado", True))
    chk_apenas.IsChecked = bool(prefs.get("apenas_parede", True))
    chk_void.IsChecked = bool(prefs.get("void_cut", True))
    chk_inv.IsChecked = bool(prefs.get("inverter", False))

    def _toggle_combos(s=None, e=None):
        is_proj = (cb_modo.SelectedIndex == 1)
        cb_familia.IsEnabled = is_proj and (len(syms) > 0)

    cb_modo.SelectionChanged += _toggle_combos
    _toggle_combos()

    estado = {"ok": False}

    def _on_ok(s, e):
        if cb_modo.SelectedIndex == 1 and len(syms) == 0:
            forms.alert(u"Nenhuma família CurveBased de Tabica carregada no projeto. Use a opção AutoBIM Paramétrica ou carregue uma família.", title=u"Tabicas")
            return
        if cb_modo.SelectedIndex == 1 and sc_familia.selected_index() < 0:
            forms.alert(u"Selecione uma família carregada na lista.", title=u"Tabicas")
            return
        estado["ok"] = True
        win.Close()

    btn_ok.Click += _on_ok
    btn_cancel.Click += lambda s, e: win.Close()
    win.ShowDialog()
    if not estado["ok"]:
        return

    fam_idx_t = sc_familia.selected_index()
    mat_idx_t = sc_mat.selected_index() if sc_mat is not None else 0
    salvar_prefs("tabicas", {
        "escopo_idx": cb_escopo.SelectedIndex,
        "modo_v3_idx": cb_modo.SelectedIndex,
        "familia": sym_labels[fam_idx_t] if fam_idx_t >= 0 and sym_labels else "",
        "material": mats_data[mat_idx_t - 1][0] if mat_idx_t > 0 and (mat_idx_t - 1) < len(mats_data) else "",
        "gap_min_v2": tb_gap_min.Text,
        "gap_max": tb_gap_max.Text,
        "gap_padrao_v3": tb_gap_padrao.Text,
        "encaixe_mm": tb_encaixe.Text,
        "altura_mm_v3": tb_altura.Text,
        "esp_mm_v3": tb_esp_mm.Text,
        "desloc_z": tb_desloc_z.Text,
        "recuar_encostado": bool(chk_recuar.IsChecked),
        "apenas_parede": bool(chk_apenas.IsChecked),
        "void_cut": bool(chk_void.IsChecked),
        "inverter": bool(chk_inv.IsChecked),
    })

    alvos = _selecionar_elementos_por_escopo(
        doc, uidoc, view, cb_escopo.SelectedIndex, _is_ceiling, _coletar_ceilings_vista,
        u"Selecione os forros para gerar Tabicas e pressione Concluir"
    )
    if not alvos:
        forms.alert(u"Nenhum forro encontrado ou selecionado.", title=u"Tabicas")
        return

    gap_min_ft = max(0.0, parse_float(tb_gap_min.Text, 0.0) * CM_TO_FT)
    gap_max_ft = max(gap_min_ft + 0.01, parse_float(tb_gap_max.Text, 15.0) * CM_TO_FT)
    gap_def_cm = max(0.5, parse_float(tb_gap_padrao.Text, 2.1))
    encaixe_mm = max(2.0, parse_float(tb_encaixe.Text, 14.0))
    rebaixo_mm = max(2.0, parse_float(tb_altura.Text, 14.0))
    esp_mm = max(0.5, parse_float(tb_esp_mm.Text, 1.0))
    encaixe_ft = encaixe_mm * 0.001 * FT
    h_ft = rebaixo_mm * 0.001 * FT
    esp_ft = esp_mm * 0.001 * FT
    desloc_z_ft = parse_float(tb_desloc_z.Text, 0.0) * CM_TO_FT
    auto_recuar = bool(chk_recuar.IsChecked)
    apenas_parede = bool(chk_apenas.IsChecked)
    try_void = bool(chk_void.IsChecked)
    invert = bool(chk_inv.IsChecked)
    modo_idx = cb_modo.SelectedIndex

    walls_data = _coletar_walls_data(doc)
    criados = 0
    voids_ok = 0
    cantos_ok = 0
    recuados_ok = 0
    ignorados = 0
    erros = []
    cache_flip = {}

    tg = TransactionGroup(doc, "AutoBIM - Tabicas")
    tg.Start()
    try:
        # Etapa 1: recuar forros encostados na parede antes da Transaction principal (via SketchEditScope)
        if auto_recuar:
            for ceil in alvos:
                if _recuar_arestas_forro_encostadas(doc, ceil, walls_data, gap_def_cm * CM_TO_FT, gap_max_ft):
                    recuados_ok += 1

        t = Transaction(doc, "AutoBIM - Modelar Tabicas")
        opt_f = t.GetFailureHandlingOptions()
        opt_f.SetFailuresPreprocessor(_SwallowFailures())
        t.SetFailureHandlingOptions(opt_f)
        t.Start()

        proj_sym = syms[fam_idx_t] if (modo_idx == 1 and fam_idx_t >= 0) else None
        autobim_cache = {}
        mat_id, mat_explicit = _resolver_material_escolha(doc, sc_mat, mats_data, "tabica")
        cache_sym_mats = set()

        for ceil in alvos:
            lvl = doc.GetElement(ceil.LevelId)
            bottom_z, loops = _obter_info_forro(ceil)
            if bottom_z is None or not loops:
                ignorados += 1
                continue
            ceil_thick_ft = None
            try:
                ctype = doc.GetElement(ceil.GetTypeId())
                if ctype is not None:
                    cs = ctype.GetCompoundStructure()
                    if cs is not None:
                        ceil_thick_ft = cs.GetWidth()
            except Exception:
                pass
            target_z = bottom_z + desloc_z_ft
            for loop in loops:
                n_e = len(loop)
                edge_gaps = []
                for edge in loop:
                    if edge["length"] < 0.02:
                        edge_gaps.append(None)
                        continue
                    gap_ft, near_wall = _medir_recuo_parede_borda(walls_data, edge, bottom_z, gap_max_ft)
                    if gap_ft is None or gap_ft < gap_min_ft:
                        if apenas_parede:
                            edge_gaps.append(None)
                            continue
                        gap_cm = gap_def_cm
                        gap_place_ft = gap_def_cm * CM_TO_FT
                    else:
                        gap_place_ft = max(gap_ft, 0.0)
                        gap_cm = max(1.0, round(gap_place_ft * FT_TO_CM * 2.0) / 2.0) if gap_place_ft >= 0.005 else gap_def_cm
                    edge_gaps.append((gap_place_ft, gap_cm, near_wall))

                loop_insts = []
                for idx, edge in enumerate(loop):
                    info_g = edge_gaps[idx]
                    if info_g is None:
                        continue
                    gap_place_ft, gap_cm, near_wall = info_g

                    prev_idx = (idx - 1) % n_e
                    next_idx = (idx + 1) % n_e
                    prev_e = loop[prev_idx]
                    next_e = loop[next_idx]
                    same_col_prev = (prev_e["p1"].DistanceTo(edge["p0"]) <= 0.08 and edge["t"].DotProduct(prev_e["t"]) > 0.98)
                    same_col_next = (edge["p1"].DistanceTo(next_e["p0"]) <= 0.08 and edge["t"].DotProduct(next_e["t"]) > 0.98)
                    start_miter = 0.0 if same_col_prev else 45.0
                    end_miter = 0.0 if same_col_next else 45.0

                    # Deslocar linha de colocacao para a face da parede e estender ate os cantos reais das paredes
                    shift = edge["outward"] * gap_place_ft
                    p_a = edge["p0"] + shift
                    p_b = edge["p1"] + shift
                    prev_info = edge_gaps[prev_idx]
                    next_info = edge_gaps[next_idx]
                    if prev_info is not None and (not same_col_prev) and prev_e["p1"].DistanceTo(edge["p0"]) <= 0.08:
                        ip_a = _intersecar_retas_xy(prev_e["p1"] + prev_e["outward"] * prev_info[0], prev_e["t"], p_a, edge["t"])
                        if ip_a is not None:
                            p_a = ip_a
                    if next_info is not None and (not same_col_next) and edge["p1"].DistanceTo(next_e["p0"]) <= 0.08:
                        ip_b = _intersecar_retas_xy(p_b, edge["t"], next_e["p0"] + next_e["outward"] * next_info[0], next_e["t"])
                        if ip_b is not None:
                            p_b = ip_b
                    if p_a.DistanceTo(p_b) < 0.02:
                        continue

                    exp_dir = edge["inward"]

                    if modo_idx == 0:
                        key = ("Tabica_Z", gap_cm, round(encaixe_mm, 1), round(rebaixo_mm, 1))
                        sym = autobim_cache.get(key)
                        if sym is None:
                            fam_nm = u"AutoBIM_Tabica_Z_v3_{0}x{1}x{2}mm".format(
                                int(round(gap_cm * 10.0)),
                                int(round(rebaixo_mm)),
                                int(round(encaixe_mm)),
                            )
                            sym = _obter_ou_criar_familia_autobim(
                                doc, fam_nm, "Tabica_Z",
                                {
                                    "w_ft": gap_cm * CM_TO_FT,
                                    "h_ft": h_ft,
                                    "encaixe_ft": encaixe_ft,
                                    "esp_ft": esp_ft,
                                    "aba_par_ft": 2.0 * CM_TO_FT,
                                    "aba_ft": 2.1 * CM_TO_FT,
                                }
                            )
                            autobim_cache[key] = sym
                    else:
                        sym = proj_sym

                    try:
                        host_void = ceil if (try_void and proj_sym is not None) else near_wall
                        inst, v_ok = _colocar_curva_familia(
                            doc, sym, lvl, p_a, p_b, target_z,
                            exp_dir, cache_flip,
                            invert_manual=invert,
                            expect_on_wall_side=False,
                            start_miter_deg=start_miter,
                            end_miter_deg=end_miter,
                            host_elem_for_void=host_void if host_void else ceil,
                            try_void_cut=try_void,
                            ceil_thick_ft=ceil_thick_ft,
                            material_id=mat_id,
                            overwrite_autobim_mat=mat_explicit,
                            cache_sym_mats=cache_sym_mats,
                        )
                        if inst is not None:
                            criados += 1
                            loop_insts.append((p_a, p_b, inst))
                            if v_ok:
                                voids_ok += 1
                    except Exception as ex:
                        erros.append(unicode(ex))

                if modo_idx == 0 and len(loop_insts) >= 2:
                    cantos_ok += _unir_instancias_cantos_loop(doc, loop_insts, tol_dist=0.35)

        t.Commit()
        tg.Assimilate()
    except Exception:
        tg.RollBack()
        raise

    msg = u"Tabicas criadas: {0}\nForros recuados automaticamente: {1}\nCantos unidos (JoinGeometry): {2}\nCortes aplicados (AddInstanceVoidCut): {3}\nForros sem contorno válido: {4}".format(
        criados, recuados_ok, cantos_ok, voids_ok, ignorados
    )
    if erros:
        msg += u"\n\nAvisos ({0}): {1}".format(len(erros), erros[0])
    forms.alert(msg, title=u"Tabicas - Resultado")


# ---------------------------------------------------------------------------
# Engine: Ferramenta 2 - CORTINEIROS
# ---------------------------------------------------------------------------

XAML_CORTINEIROS = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Cortineiros Automáticos (AutoBIM)" Width="480" SizeToContent="Height"
        WindowStartupLocation="CenterScreen" ResizeMode="NoResize"
        Background="#F4F5F7" FontFamily="Segoe UI" FontSize="12">
    <StackPanel Margin="18">
        <TextBlock Text="Modo de seleção das esquadrias" FontWeight="SemiBold"/>
        <ComboBox x:Name="cb_escopo" Margin="0,4,0,10" Height="27"/>

        <TextBlock Text="Sistema Construtivo do Cortineiro" FontWeight="SemiBold"/>
        <ComboBox x:Name="cb_sistema" Margin="0,4,0,10" Height="27"/>

        <TextBlock Text="Tipo de Parede da Testeira (quando Parede Fina Suspensa)" FontWeight="SemiBold"/>
        <ComboBox x:Name="cb_wall_type" Margin="0,4,0,10" Height="27"/>

        <TextBlock Text="Família CurveBased (quando usar família do projeto)" FontWeight="SemiBold"/>
        <ComboBox x:Name="cb_familia" Margin="0,4,0,10" Height="27"/>

        <Grid Margin="0,0,0,10">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="Auto"/>
                <ColumnDefinition Width="10"/>
                <ColumnDefinition Width="*"/>
            </Grid.ColumnDefinitions>
            <TextBlock Grid.Column="0" Text="Material (AutoBIM / sem material):" VerticalAlignment="Center" FontWeight="SemiBold"/>
            <ComboBox x:Name="cb_material" Grid.Column="2" Height="26"/>
        </Grid>

        <Grid Margin="0,0,0,10">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="10"/>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="10"/>
                <ColumnDefinition Width="*"/>
            </Grid.ColumnDefinitions>
            <StackPanel Grid.Column="0">
                <TextBlock Text="Sobra lateral (cm)" FontWeight="SemiBold"/>
                <TextBox x:Name="tb_sobra" Margin="0,4,0,0" Height="26" VerticalContentAlignment="Center" Text="20"/>
            </StackPanel>
            <StackPanel Grid.Column="2">
                <TextBlock Text="Profundidade (cm)" FontWeight="SemiBold"/>
                <TextBox x:Name="tb_prof" Margin="0,4,0,0" Height="26" VerticalContentAlignment="Center" Text="15"/>
            </StackPanel>
            <StackPanel Grid.Column="4">
                <TextBlock Text="Altura testeira (cm)" FontWeight="SemiBold"/>
                <TextBox x:Name="tb_alt" Margin="0,4,0,0" Height="26" VerticalContentAlignment="Center" Text="18"/>
            </StackPanel>
        </Grid>

        <Grid Margin="0,0,0,8">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="10"/>
                <ColumnDefinition Width="85"/>
            </Grid.ColumnDefinitions>
            <TextBlock Grid.Column="0" VerticalAlignment="Center" TextWrapping="Wrap"
                       Text="Cota padrão do forro se não houver forro próximo (m)" FontWeight="SemiBold"/>
            <TextBox x:Name="tb_cota_forro" Grid.Column="2" Height="26" VerticalContentAlignment="Center" Text="2,60"/>
        </Grid>

        <CheckBox x:Name="chk_recuar_forro" IsChecked="True" Margin="0,2,0,4"
                  Content="Recuar borda do Forro na zona do cortineiro (SketchEditScope)"/>
        <CheckBox x:Name="chk_laterais" IsChecked="True" Margin="0,0,0,4"
                  Content="Fechar as laterais da testeira (retorno até a parede hospedeira)"/>
        <CheckBox x:Name="chk_topo" IsChecked="True" Margin="0,0,0,4"
                  Content="Fechar o topo do cortineiro (tampo horizontal com Forro)"/>
        <CheckBox x:Name="chk_inverter" IsChecked="False" Margin="0,0,0,14"
                  Content="Inverter lado da esquadria (se o cortineiro sair para o exterior)"/>

        <StackPanel Orientation="Horizontal" HorizontalAlignment="Right">
            <Button x:Name="btn_cancelar" Content="Cancelar" Width="95" Height="30" Margin="0,0,10,0"/>
            <Button x:Name="btn_ok" Content="Criar Cortineiros" Width="155" Height="30"
                    Background="#0078D4" Foreground="White" FontWeight="SemiBold"/>
        </StackPanel>
    </StackPanel>
</Window>
"""


def _modificar_sketch_forro_cortineiro(doc, ceil, p_a_xy, p_b_xy, inward_dir, depth_ft):
    """Recua a aresta do Sketch de `ceil` que contem p_a_xy..p_b_xy usando SketchEditScope."""
    sk_id = getattr(ceil, "SketchId", ElementId.InvalidElementId)
    if _eid_int(sk_id) <= 0:
        return False
    sk = doc.GetElement(sk_id)
    if not isinstance(sk, Sketch):
        return False

    # Identificar loop externo e curva coincidente
    target_loop = None
    target_idx = -1
    s0_val = 0.0
    s1_val = 0.0
    mid_ab = XYZ(0.5 * (p_a_xy.X + p_b_xy.X), 0.5 * (p_a_xy.Y + p_b_xy.Y), 0.0)
    ab_dir = (p_b_xy - p_a_xy).Normalize()

    # Coletar linhas de ModelCurve do Sketch diretamente
    profile = sk.Profile
    best_dist = 0.40 * FT  # tolerancia maxima ate a borda do forro (~40 cm)
    for cl in profile:
        pts = []
        ok_poly = True
        for crv in cl:
            if not isinstance(crv, Line):
                ok_poly = False
                break
            pts.append((crv.GetEndPoint(0).X, crv.GetEndPoint(0).Y))
        if not ok_poly or len(pts) < 3:
            continue
        for i in range(len(pts)):
            ax, ay = pts[i]
            bx, by = pts[(i + 1) % len(pts)]
            L = math.hypot(bx - ax, by - ay)
            if L < 0.05:
                continue
            tx, ty = (bx - ax) / L, (by - ay) / L
            if abs(tx * ab_dir.X + ty * ab_dir.Y) < 0.90:
                continue
            d, _ = _dist_point_to_seg_2d(mid_ab.X, mid_ab.Y, ax, ay, bx, by)
            if d < best_dist:
                best_dist = d
                target_loop = pts
                target_idx = i
                pA_t = (p_a_xy.X - ax) * tx + (p_a_xy.Y - ay) * ty
                pB_t = (p_b_xy.X - ax) * tx + (p_b_xy.Y - ay) * ty
                s0_val = max(0.0, min(pA_t, pB_t))
                s1_val = min(L, max(pA_t, pB_t))

    if target_loop is None or target_idx < 0 or s1_val - s0_val < 0.1:
        return False

    new_pts = _recuar_aresta_poligono(target_loop, target_idx, s0_val, s1_val, (inward_dir.X, inward_dir.Y), depth_ft)
    if len(new_pts) < 3:
        return False

    # Descobrir Z do sketch e suas ModelCurves originais para limpar do loop alterado
    # Identificar quais ModelCurves correspondem ao `target_loop`
    all_sketch_mcs = list(sk.GetAllElements())
    plane_z = 0.0
    if sk.SketchPlane:
        plane_z = sk.SketchPlane.GetPlane().Origin.Z

    scope = SketchEditScope(doc, "AutoBIM - Recuar Forro para Cortineiro")
    scope.Start(sk_id)
    try:
        # Apagar ModelCurves pertencentes ao loop alvo (ou checar proximidade com target_loop)
        to_del = List[ElementId]()
        for eid in all_sketch_mcs:
            el = doc.GetElement(eid)
            if isinstance(el, CurveElement):
                crv = el.GeometryCurve
                if isinstance(crv, Line):
                    m = crv.Evaluate(0.5, True)
                    for i in range(len(target_loop)):
                        ax, ay = target_loop[i]
                        bx, by = target_loop[(i + 1) % len(target_loop)]
                        d, _ = _dist_point_to_seg_2d(m.X, m.Y, ax, ay, bx, by)
                        if d < 1e-3:
                            to_del.Add(eid)
                            break
        if to_del.Count > 0:
            doc.Delete(to_del)
        for i in range(len(new_pts)):
            a = new_pts[i]
            b = new_pts[(i + 1) % len(new_pts)]
            l_new = Line.CreateBound(XYZ(a[0], a[1], plane_z), XYZ(b[0], b[1], plane_z))
            doc.Create.NewModelCurve(l_new, sk.SketchPlane)
        scope.Commit(_SwallowFailures())
        return True
    except Exception:
        try:
            scope.Cancel()
        except Exception:
            pass
        return False


def _detectar_forro_e_lado_esquadria(doc, inst, rooms_list):
    """Retorna (host_wall, span_start, span_end, inward_dir, z_base_forro, ceiling_obj)."""
    wall = inst.Host if isinstance(inst.Host, Wall) else None
    if wall is None or not hasattr(wall.Location, "Curve"):
        return None
    wcrv = wall.Location.Curve
    wp0 = wcrv.GetEndPoint(0)
    wp1 = wcrv.GetEndPoint(1)
    t_wall = XYZ(wp1.X - wp0.X, wp1.Y - wp0.Y, 0.0).Normalize()
    half_w = 0.5 * getattr(wall.WallType, "Width", 0.49)

    bb = inst.get_BoundingBox(None)
    if bb is None:
        return None
    xs = (bb.Min.X, bb.Max.X)
    ys = (bb.Min.Y, bb.Max.Y)
    projs = [((x - wp0.X) * t_wall.X + (y - wp0.Y) * t_wall.Y) for x in xs for y in ys]
    s_min = min(projs)
    s_max = max(projs)
    p_mid_axis = wp0 + t_wall * (0.5 * (s_min + s_max))

    # Descobrir lado interno usando Rooms do ambiente ou FacingOrientation da esquadria
    n1 = XYZ(-t_wall.Y, t_wall.X, 0.0)
    n2 = XYZ(t_wall.Y, -t_wall.X, 0.0)
    inward = None
    probe_dist = half_w + 0.8 * FT
    p1_probe = XYZ(p_mid_axis.X + n1.X * probe_dist, p_mid_axis.Y + n1.Y * probe_dist, bb.Min.Z + 1.0)
    p2_probe = XYZ(p_mid_axis.X + n2.X * probe_dist, p_mid_axis.Y + n2.Y * probe_dist, bb.Min.Z + 1.0)
    for rm in rooms_list:
        try:
            if rm.IsPointInRoom(p1_probe):
                inward = n1
                break
            if rm.IsPointInRoom(p2_probe):
                inward = n2
                break
        except Exception:
            pass
    if inward is None:
        # Fallback: oposto ao FacingOrientation da janela (que normalmente aponta para fora)
        fo = getattr(inst, "FacingOrientation", n1)
        fo_xy = XYZ(fo.X, fo.Y, 0.0)
        if fo_xy.GetLength() > 1e-4:
            inward = n1 if n1.DotProduct(-fo_xy.Normalize()) > 0 else n2
        else:
            inward = n1

    return {
        "wall": wall,
        "wp0": XYZ(wp0.X, wp0.Y, 0.0),
        "t_wall": t_wall,
        "w_len": XYZ(wp1.X - wp0.X, wp1.Y - wp0.Y, 0.0).GetLength(),
        "half_w": half_w,
        "s_min": s_min,
        "s_max": s_max,
        "inward": inward,
        "probe_in": XYZ(p_mid_axis.X + inward.X * (half_w + 0.8 * FT), p_mid_axis.Y + inward.Y * (half_w + 0.8 * FT), 0.0),
    }


def _encontrar_forro_sobre_ponto(doc, ceil_list, x, y, min_z):
    best_c = None
    best_z = None
    for c in ceil_list:
        bb = c.get_BoundingBox(None)
        if bb is None:
            continue
        if x >= bb.Min.X - 0.5 and x <= bb.Max.X + 0.5 and y >= bb.Min.Y - 0.5 and y <= bb.Max.Y + 0.5:
            if bb.Min.Z >= min_z - 0.5:
                if best_z is None or bb.Min.Z < best_z:
                    best_z = bb.Min.Z
                    best_c = c
    return best_c, best_z


def run_cortineiros(doc, uidoc, view):
    prefs = carregar_prefs("cortineiros")
    wall_types = _coletar_wall_types(doc)
    wt_labels = [_ename(wt) for wt in wall_types]
    syms = _coletar_curve_based_symbols(doc, ["cortineiro", "tabica", "rodameio"])
    sym_labels = [_symbol_display(s) for s in syms]
    mats_data = _coletar_materiais(doc)

    win = XamlReader.Parse(XAML_CORTINEIROS)
    cb_escopo = win.FindName("cb_escopo")
    cb_sistema = win.FindName("cb_sistema")
    cb_wall = win.FindName("cb_wall_type")
    cb_familia = win.FindName("cb_familia")
    cb_mat = win.FindName("cb_material")
    tb_sobra = win.FindName("tb_sobra")
    tb_prof = win.FindName("tb_prof")
    tb_alt = win.FindName("tb_alt")
    tb_cota_forro = win.FindName("tb_cota_forro")
    chk_recuar = win.FindName("chk_recuar_forro")
    chk_lat = win.FindName("chk_laterais")
    chk_topo = win.FindName("chk_topo")
    chk_inv = win.FindName("chk_inverter")
    btn_ok = win.FindName("btn_ok")
    btn_cancel = win.FindName("btn_cancelar")

    for op in [
        u"Todas as janelas da vista ativa",
        u"Usar Janelas/Portas pré-selecionadas (ou selecionar na vista)",
        u"Selecionar Janelas/Portas manualmente agora",
    ]:
        cb_escopo.Items.Add(op)
    cb_escopo.SelectedIndex = prefs.get("escopo_idx", 1 if uidoc.Selection.GetElementIds().Count > 0 else 2)

    for op in [
        u"Parede Fina Suspensa (testeira) + Recuo no Forro",
        u"Família AutoBIM Paramétrica — Perfil L",
        u"Família AutoBIM Paramétrica — Perfil U (bolsa fechada)",
        u"Família carregada do projeto (CurveBased)",
    ]:
        cb_sistema.Items.Add(op)
    cb_sistema.SelectedIndex = prefs.get("sistema_idx", 0)

    sc_wall = _anexar_busca_combo_palavras(
        cb_wall, wt_labels, prefs.get("wall_type"), ["oc_forro", "gesso", "st"], placeholder=u"Pesquisar tipo de parede..."
    )
    sc_familia = _anexar_busca_combo_palavras(
        cb_familia, sym_labels, prefs.get("familia"), ["cortineiro", "tabica"], placeholder=u"Pesquisar família..."
    )
    sc_mat = _popular_combo_materiais(cb_mat, mats_data, prefs.get("material"))

    tb_sobra.Text = prefs.get("sobra", "20")
    tb_prof.Text = prefs.get("prof", "15")
    tb_alt.Text = prefs.get("alt", "18")
    tb_cota_forro.Text = prefs.get("cota_forro", "2,60")
    chk_recuar.IsChecked = bool(prefs.get("recuar_forro", True))
    chk_lat.IsChecked = bool(prefs.get("laterais", True))
    chk_topo.IsChecked = bool(prefs.get("topo", True))
    chk_inv.IsChecked = bool(prefs.get("inverter", False))

    def _toggle(s=None, e=None):
        idx = cb_sistema.SelectedIndex
        cb_wall.IsEnabled = (idx == 0)
        chk_lat.IsEnabled = (idx in (0, 1, 2))
        chk_topo.IsEnabled = (idx == 0)
        cb_familia.IsEnabled = (idx == 3 and len(syms) > 0)
        cb_mat.IsEnabled = (idx != 0)

    cb_sistema.SelectionChanged += _toggle
    _toggle()

    estado = {"ok": False}

    def _on_ok(s, e):
        if cb_sistema.SelectedIndex == 3 and len(syms) == 0:
            forms.alert(u"Nenhuma família CurveBased carregada.", title=u"Cortineiros")
            return
        if cb_sistema.SelectedIndex == 3 and sc_familia.selected_index() < 0:
            forms.alert(u"Selecione uma família carregada.", title=u"Cortineiros")
            return
        if cb_sistema.SelectedIndex == 0 and wall_types and sc_wall.selected_index() < 0:
            forms.alert(u"Selecione um tipo de parede.", title=u"Cortineiros")
            return
        estado["ok"] = True
        win.Close()

    btn_ok.Click += _on_ok
    btn_cancel.Click += lambda s, e: win.Close()
    win.ShowDialog()
    if not estado["ok"]:
        return

    wt_idx_c  = sc_wall.selected_index()
    fam_idx_c = sc_familia.selected_index()
    mat_idx_c = sc_mat.selected_index() if sc_mat is not None else 0
    salvar_prefs("cortineiros", {
        "escopo_idx": cb_escopo.SelectedIndex,
        "sistema_idx": cb_sistema.SelectedIndex,
        "wall_type": wt_labels[wt_idx_c] if wt_idx_c >= 0 and wt_labels else "",
        "familia": sym_labels[fam_idx_c] if fam_idx_c >= 0 and sym_labels else "",
        "material": mats_data[mat_idx_c - 1][0] if mat_idx_c > 0 and (mat_idx_c - 1) < len(mats_data) else "",
        "sobra": tb_sobra.Text,
        "prof": tb_prof.Text,
        "alt": tb_alt.Text,
        "cota_forro": tb_cota_forro.Text,
        "recuar_forro": bool(chk_recuar.IsChecked),
        "laterais": bool(chk_lat.IsChecked),
        "topo": bool(chk_topo.IsChecked),
        "inverter": bool(chk_inv.IsChecked),
    })

    esquadrias = _selecionar_elementos_por_escopo(
        doc, uidoc, view, cb_escopo.SelectedIndex, _is_opening, _coletar_janelas_vista,
        u"Selecione as Janelas ou Portas-Balcão para gerar Cortineiros"
    )
    if not esquadrias:
        forms.alert(u"Nenhuma janela ou porta selecionada.", title=u"Cortineiros")
        return

    sobra_ft = max(0.0, parse_float(tb_sobra.Text, 20.0)) * CM_TO_FT
    prof_ft = max(5.0, parse_float(tb_prof.Text, 15.0)) * CM_TO_FT
    alt_ft = max(5.0, parse_float(tb_alt.Text, 18.0)) * CM_TO_FT
    cota_forro_def = parse_float(tb_cota_forro.Text, 2.60) * FT
    recuar_forro = bool(chk_recuar.IsChecked)
    fechar_lat = bool(chk_lat.IsChecked)
    fechar_topo = bool(chk_topo.IsChecked)
    invert = bool(chk_inv.IsChecked)
    sis_idx = cb_sistema.SelectedIndex

    rooms = list(FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Rooms).WhereElementIsNotElementType())
    rooms = [r for r in rooms if _is_room(r)]
    ceils = list(FilteredElementCollector(doc).OfClass(Ceiling).WhereElementIsNotElementType())
    ceil_types_all = _coletar_ceiling_types(doc) if fechar_topo else []

    wt = wall_types[wt_idx_c] if (wt_idx_c >= 0 and wall_types) else None
    wt_w = getattr(wt, "Width", 0.04) if wt else 0.04
    proj_sym = syms[fam_idx_c] if (sis_idx == 3 and fam_idx_c >= 0) else None

    # Preparar lista de geometrias de cortineiro
    itens = []
    is_parede_fina = (sis_idx == 0 and wt is not None)
    esp_prof_ft = 1.2 * CM_TO_FT
    for esq in esquadrias:
        info = _detectar_forro_e_lado_esquadria(doc, esq, rooms)
        if info is None:
            continue
        inward = -info["inward"] if invert else info["inward"]
        s0 = max(0.0, info["s_min"] - sobra_ft)
        s1 = min(info["w_len"], info["s_max"] + sobra_ft)
        if s1 - s0 < 0.2:
            continue

        # Compensar espessura da testeira/tampas para que Sobra lateral e Profundidade sejam medidas LIVRES internas
        lat_w_use = wt_w if is_parede_fina else esp_prof_ft
        if sis_idx in (0, 1, 2) and fechar_lat:
            lat0_s = (s0 - 0.5 * lat_w_use) if (s0 >= lat_w_use) else s0
            lat1_s = (s1 + 0.5 * lat_w_use) if (s1 <= info["w_len"] - lat_w_use) else s1
            s0_cut = max(0.0, lat0_s - 0.5 * lat_w_use)
            s1_cut = min(info["w_len"], lat1_s + 0.5 * lat_w_use)
        else:
            lat0_s, lat1_s = s0, s1
            s0_cut, s1_cut = s0, s1

        if is_parede_fina:
            prof_cut = prof_ft + wt_w
        elif sis_idx == 1:
            prof_cut = prof_ft + esp_prof_ft
        elif sis_idx == 2:
            prof_cut = prof_ft + 2.0 * esp_prof_ft
        else:
            prof_cut = prof_ft

        # Face interna da parede hospedeira ao longo do vao
        off_face = info["half_w"]
        p_face_0 = info["wp0"] + info["t_wall"] * s0 + inward * off_face
        p_face_1 = info["wp0"] + info["t_wall"] * s1 + inward * off_face
        p_lat_0 = info["wp0"] + info["t_wall"] * lat0_s + inward * off_face
        p_lat_1 = info["wp0"] + info["t_wall"] * lat1_s + inward * off_face
        p_cut_0 = info["wp0"] + info["t_wall"] * s0_cut + inward * off_face
        p_cut_1 = info["wp0"] + info["t_wall"] * s1_cut + inward * off_face
        probe_x = 0.5 * (p_face_0.X + p_face_1.X) + inward.X * (prof_cut + 0.5)
        probe_y = 0.5 * (p_face_0.Y + p_face_1.Y) + inward.Y * (prof_cut + 0.5)
        lvl = doc.GetElement(info["wall"].LevelId)
        lvl_elev = lvl.Elevation if lvl else 0.0
        ceil, c_bot_z = _encontrar_forro_sobre_ponto(doc, ceils, probe_x, probe_y, lvl_elev)
        base_z = c_bot_z if c_bot_z is not None else (lvl_elev + cota_forro_def)

        itens.append({
            "wall": info["wall"],
            "level": lvl,
            "p_face_0": p_face_0,
            "p_face_1": p_face_1,
            "p_lat_0": p_lat_0,
            "p_lat_1": p_lat_1,
            "p_cut_0": p_cut_0,
            "p_cut_1": p_cut_1,
            "prof_cut": prof_cut,
            "inward": inward,
            "t_wall": info["t_wall"],
            "ceil": ceil,
            "base_z": base_z,
        })

    if not itens:
        forms.alert(u"Não foi possível extrair posição de parede para as esquadrias.", title=u"Cortineiros")
        return

    tg = TransactionGroup(doc, "AutoBIM - Cortineiros")
    tg.Start()
    recuos_ok = 0
    criados = 0
    try:
        # Etapa 1: SketchEditScope nos forros (fora de Transaction normal)
        if recuar_forro:
            for it in itens:
                if it["ceil"] is not None:
                    if _modificar_sketch_forro_cortineiro(doc, it["ceil"], it["p_cut_0"], it["p_cut_1"], it["inward"], it["prof_cut"]):
                        recuos_ok += 1

        # Etapa 2: Criar testeira (Wall suspensa) ou Familia
        t = Transaction(doc, "AutoBIM - Criar Cortineiros")
        opt_f = t.GetFailureHandlingOptions()
        opt_f.SetFailuresPreprocessor(_SwallowFailures())
        t.SetFailureHandlingOptions(opt_f)
        t.Start()

        sym_auto = None
        sym_tampa = None
        w_fam_ft = prof_ft
        h_fam_ft = alt_ft
        if sis_idx in (1, 2):
            kind = "Cortineiro_L" if sis_idx == 1 else "Cortineiro_U"
            w_fam_ft = prof_ft + (2.0 * esp_prof_ft if sis_idx == 2 else esp_prof_ft)
            h_fam_ft = alt_ft + esp_prof_ft
            ver_tag = "_v2" if kind == "Cortineiro_L" else ""
            fam_nm = u"AutoBIM_{0}{1}_{2}x{3}cm".format(kind, ver_tag, int(round(w_fam_ft * FT_TO_CM)), int(round(h_fam_ft * FT_TO_CM)))
            sym_auto = _obter_ou_criar_familia_autobim(
                doc, fam_nm, kind,
                {"w_ft": w_fam_ft, "h_ft": h_fam_ft, "esp_ft": esp_prof_ft}
            )
            if fechar_lat:
                tampa_nm = u"AutoBIM_Cortineiro_Tampa_{0}cm".format(int(round(h_fam_ft * FT_TO_CM)))
                sym_tampa = _obter_ou_criar_familia_autobim(
                    doc, tampa_nm, "Cortineiro_Tampa",
                    {"w_ft": esp_prof_ft, "h_ft": h_fam_ft}
                )
        elif sis_idx == 3:
            sym_auto = proj_sym

        mat_id, mat_explicit = _resolver_material_escolha(doc, sc_mat, mats_data, "cortineiro")
        cache_sym_mats = set()
        cache_flip = {}
        for it in itens:
            lvl = it["level"]
            lvl_elev = lvl.Elevation if lvl else 0.0
            inw = it["inward"]
            if sis_idx == 0 and wt is not None:
                # Resolver tipo e espessura do tampo superior antes de criar testeira/laterais
                ct_topo = None
                ct_thick = 0.0
                if fechar_topo:
                    if it["ceil"] is not None:
                        try:
                            ct_topo = doc.GetElement(it["ceil"].GetTypeId())
                        except Exception:
                            ct_topo = None
                    if ct_topo is None and ceil_types_all:
                        ct_topo = ceil_types_all[0]
                    if ct_topo is not None:
                        ct_thick = 0.04
                        try:
                            cs = ct_topo.GetCompoundStructure()
                            if cs is not None and cs.GetWidth() > 0.005:
                                ct_thick = cs.GetWidth()
                        except Exception:
                            pass

                wall_h = alt_ft + ct_thick
                # Face interna da testeira em `prof_ft` livre -> eixo em `prof_ft + 0.5*wt_w`
                c_off = prof_ft + 0.5 * wt_w
                pf0 = it["p_lat_0"] + inw * c_off
                pf1 = it["p_lat_1"] + inw * c_off
                l_frente = Line.CreateBound(XYZ(pf0.X, pf0.Y, lvl_elev), XYZ(pf1.X, pf1.Y, lvl_elev))
                w_front = Wall.Create(doc, l_frente, wt.Id, lvl.Id, wall_h, it["base_z"] - lvl_elev, False, False)
                _set_param_int(w_front, [u"Delimitação de ambiente"], 0)
                _set_bip_double(w_front, BuiltInParameter.WALL_ATTR_ROOM_BOUNDING, 0)
                criados += 1
                w_lats = []
                if fechar_lat:
                    for end_pt in (it["p_lat_0"], it["p_lat_1"]):
                        p_end_out = end_pt + inw * prof_ft
                        if end_pt.DistanceTo(p_end_out) > 0.03:
                            l_lat = Line.CreateBound(XYZ(end_pt.X, end_pt.Y, lvl_elev), XYZ(p_end_out.X, p_end_out.Y, lvl_elev))
                            w_lat = Wall.Create(doc, l_lat, wt.Id, lvl.Id, wall_h, it["base_z"] - lvl_elev, False, False)
                            _set_bip_double(w_lat, BuiltInParameter.WALL_ATTR_ROOM_BOUNDING, 0)
                            criados += 1
                            w_lats.append(w_lat)
                walls_cort = [w_front] + w_lats
                c_topo = None
                if fechar_topo and ct_topo is not None:
                    z_topo_bot = it["base_z"] + alt_ft
                    q0 = it["p_cut_0"]
                    q1 = it["p_cut_1"]
                    q2 = q1 + inw * it["prof_cut"]
                    q3 = q0 + inw * it["prof_cut"]
                    q_pts = [q0, q1, q2, q3]
                    try:
                        cl_topo = CurveLoop()
                        for qi in range(4):
                            qa = q_pts[qi]
                            qb = q_pts[(qi + 1) % 4]
                            cl_topo.Append(Line.CreateBound(XYZ(qa.X, qa.Y, z_topo_bot), XYZ(qb.X, qb.Y, z_topo_bot)))
                        cl_list = List[CurveLoop]()
                        cl_list.Add(cl_topo)
                        c_topo = Ceiling.Create(doc, cl_list, ct_topo.Id, lvl.Id)
                        _set_bip_double(c_topo, BuiltInParameter.CEILING_HEIGHTABOVELEVEL_PARAM, z_topo_bot - lvl_elev)
                        criados += 1
                    except Exception:
                        c_topo = None
                doc.Regenerate()
                for w_lat in w_lats:
                    _try_join_pair(doc, w_front, w_lat)
                if c_topo is not None:
                    for wc in walls_cort:
                        _try_join_pair(doc, wc, c_topo)
            else:
                # Familia L/U ou do projeto iniciando na face da parede e projetando +inw
                inst, _ = _colocar_curva_familia(
                    doc, sym_auto, lvl, it["p_face_0"], it["p_face_1"],
                    it["base_z"], inw, cache_flip,
                    invert_manual=False,
                    expect_on_wall_side=False,
                    material_id=mat_id,
                    overwrite_autobim_mat=mat_explicit,
                    cache_sym_mats=cache_sym_mats,
                )
                if inst is not None:
                    criados += 1
                    caps_created = []
                    if fechar_lat and sym_tampa is not None:
                        for idx_cap, end_pt in enumerate((it["p_lat_0"], it["p_lat_1"])):
                            p_end_out = end_pt + inw * w_fam_ft
                            cap_inw = -it["t_wall"] if idx_cap == 0 else it["t_wall"]
                            cap_inst, _ = _colocar_curva_familia(
                                doc, sym_tampa, lvl, end_pt, p_end_out,
                                it["base_z"], cap_inw, cache_flip,
                                invert_manual=False,
                                expect_on_wall_side=False,
                                material_id=mat_id,
                                overwrite_autobim_mat=mat_explicit,
                                cache_sym_mats=cache_sym_mats,
                            )
                            if cap_inst is not None:
                                criados += 1
                                caps_created.append(cap_inst)
                    if caps_created:
                        doc.Regenerate()
                        for cap_inst in caps_created:
                            _try_join_pair(doc, inst, cap_inst)

        t.Commit()
        tg.Assimilate()
    except Exception:
        tg.RollBack()
        raise

    forms.alert(
        u"Cortineiros modelados: {0} elementos\nForros recuados automaticamente: {1}".format(criados, recuos_ok),
        title=u"Cortineiros - Resultado"
    )


# ---------------------------------------------------------------------------
# Engine: Ferramenta 3 - SANCAS POR AMBIENTE
# ---------------------------------------------------------------------------

XAML_SANCAS = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Sancas por Ambiente (AutoBIM)" Width="470" SizeToContent="Height"
        WindowStartupLocation="CenterScreen" ResizeMode="NoResize"
        Background="#F4F5F7" FontFamily="Segoe UI" FontSize="12">
    <StackPanel Margin="18">
        <TextBlock Text="Modo de seleção dos ambientes" FontWeight="SemiBold"/>
        <ComboBox x:Name="cb_escopo" Margin="0,4,0,10" Height="27"/>

        <TextBlock Text="Modelo / Tipo de Sanca" FontWeight="SemiBold"/>
        <ComboBox x:Name="cb_tipo_sanca" Margin="0,4,0,10" Height="27"/>

        <TextBlock Text="Tipo de Parede Fina (para Sanca Fechada / Aberta nativa)" FontWeight="SemiBold"/>
        <ComboBox x:Name="cb_wall_type" Margin="0,4,0,10" Height="27"/>

        <TextBlock Text="Tipo de Forro (tampo horizontal da sanca nativa)" FontWeight="SemiBold"/>
        <ComboBox x:Name="cb_ceiling_type" Margin="0,4,0,10" Height="27"/>

        <TextBlock Text="Família CurveBased (quando usar família do projeto)" FontWeight="SemiBold"/>
        <ComboBox x:Name="cb_familia" Margin="0,4,0,10" Height="27"/>

        <Grid Margin="0,0,0,10">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="Auto"/>
                <ColumnDefinition Width="10"/>
                <ColumnDefinition Width="*"/>
            </Grid.ColumnDefinitions>
            <TextBlock Grid.Column="0" Text="Material (AutoBIM / sem material):" VerticalAlignment="Center" FontWeight="SemiBold"/>
            <ComboBox x:Name="cb_material" Grid.Column="2" Height="26"/>
        </Grid>

        <Grid Margin="0,0,0,10">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="10"/>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="10"/>
                <ColumnDefinition Width="*"/>
            </Grid.ColumnDefinitions>
            <StackPanel Grid.Column="0">
                <TextBlock Text="Largura sanca (cm)" FontWeight="SemiBold"/>
                <TextBox x:Name="tb_larg" Margin="0,4,0,0" Height="26" VerticalContentAlignment="Center" Text="35"/>
            </StackPanel>
            <StackPanel Grid.Column="2">
                <TextBlock Text="Altura / Queda (cm)" FontWeight="SemiBold"/>
                <TextBox x:Name="tb_alt" Margin="0,4,0,0" Height="26" VerticalContentAlignment="Center" Text="15"/>
            </StackPanel>
            <StackPanel Grid.Column="4">
                <TextBlock Text="Cota base forro (m)" FontWeight="SemiBold"/>
                <TextBox x:Name="tb_cota" Margin="0,4,0,0" Height="26" VerticalContentAlignment="Center" Text="2,60"/>
            </StackPanel>
        </Grid>

        <Grid Margin="0,0,0,8">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="10"/>
                <ColumnDefinition Width="85"/>
                <ColumnDefinition Width="10"/>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="10"/>
                <ColumnDefinition Width="75"/>
            </Grid.ColumnDefinitions>
            <TextBlock Grid.Column="0" VerticalAlignment="Center" TextWrapping="Wrap"
                       Text="Aba / Recuo LED (cm):" FontWeight="SemiBold"/>
            <TextBox x:Name="tb_aba_led" Grid.Column="2" Height="26" VerticalContentAlignment="Center" Text="6"/>
            <TextBlock Grid.Column="4" VerticalAlignment="Center" TextWrapping="Wrap"
                       Text="Cortineiro (cm):" FontWeight="SemiBold"/>
            <TextBox x:Name="tb_cort_prof" Grid.Column="6" Height="26" VerticalContentAlignment="Center" Text="15"/>
        </Grid>

        <CheckBox x:Name="chk_invertida" IsChecked="True" Margin="0,2,0,4"
                  Content="Sanca Invertida — subir acima do forro e recuar Forro (SketchEditScope)"/>
        <CheckBox x:Name="chk_ignorar_sep" IsChecked="True" Margin="0,0,0,4"
                  Content="Ignorar linhas de separação de ambiente (ex.: cozinha integrada)"/>
        <CheckBox x:Name="chk_inverter" IsChecked="False" Margin="0,0,0,14"
                  Content="Inverter lado da família (se aplicável)"/>

        <StackPanel Orientation="Horizontal" HorizontalAlignment="Right">
            <Button x:Name="btn_cancelar" Content="Cancelar" Width="95" Height="30" Margin="0,0,10,0"/>
            <Button x:Name="btn_ok" Content="Criar Sancas" Width="150" Height="30"
                    Background="#0078D4" Foreground="White" FontWeight="SemiBold"/>
        </StackPanel>
    </StackPanel>
</Window>
"""


def _offset_loop_para_curveloop(pts_xyz, offset_ft, z_val):
    """Cria um CurveLoop fechado a partir de vertices CCW (outer)."""
    cl = CurveLoop()
    n = len(pts_xyz)
    for i in range(n):
        a = pts_xyz[i]
        b = pts_xyz[(i + 1) % n]
        cl.Append(Line.CreateBound(XYZ(a.X, a.Y, z_val), XYZ(b.X, b.Y, z_val)))
    if abs(offset_ft) > 1e-5:
        return CurveLoop.CreateViaOffset(cl, offset_ft, XYZ.BasisZ)
    return cl


def _modificar_sketch_forro_sanca(doc, ceil, outer_pts, offset_in_ft):
    """Recua o contorno externo do Sketch de `ceil` para dentro em `offset_in_ft` (Sanca Invertida)."""
    if ceil is None or not outer_pts or len(outer_pts) < 3 or offset_in_ft <= 1e-4:
        return False
    sk_id = getattr(ceil, "SketchId", ElementId.InvalidElementId)
    if _eid_int(sk_id) <= 0:
        return False
    sk = doc.GetElement(sk_id)
    if not isinstance(sk, Sketch):
        return False

    # Area aproximada da sala para evitar recortar forro unico compartilhado por varios ambientes
    rm_xs = [p.X for p in outer_pts]
    rm_ys = [p.Y for p in outer_pts]
    rm_area = max(0.1, (max(rm_xs) - min(rm_xs)) * (max(rm_ys) - min(rm_ys)))

    profile = sk.Profile
    best_loop = None
    best_area = -1.0
    for cl in profile:
        pts = []
        ok_poly = True
        for crv in cl:
            if not isinstance(crv, Line):
                ok_poly = False
                break
            pts.append((crv.GetEndPoint(0).X, crv.GetEndPoint(0).Y))
        if not ok_poly or len(pts) < 3:
            continue
        xs = [q[0] for q in pts]
        ys = [q[1] for q in pts]
        area = (max(xs) - min(xs)) * (max(ys) - min(ys))
        if area > best_area:
            best_area = area
            best_loop = pts

    if best_loop is None or best_area > 1.35 * rm_area:
        return False

    plane_z = 0.0
    if sk.SketchPlane:
        plane_z = sk.SketchPlane.GetPlane().Origin.Z

    try:
        cl_new = _offset_loop_para_curveloop(outer_pts, -offset_in_ft, plane_z)
    except Exception:
        return False

    all_sketch_mcs = list(sk.GetAllElements())
    scope = SketchEditScope(doc, "AutoBIM - Recuar Forro para Sanca Invertida")
    scope.Start(sk_id)
    try:
        to_del = List[ElementId]()
        for eid in all_sketch_mcs:
            el = doc.GetElement(eid)
            if isinstance(el, CurveElement):
                crv = el.GeometryCurve
                if isinstance(crv, Line):
                    m = crv.Evaluate(0.5, True)
                    for i in range(len(best_loop)):
                        ax, ay = best_loop[i]
                        bx, by = best_loop[(i + 1) % len(best_loop)]
                        d, _ = _dist_point_to_seg_2d(m.X, m.Y, ax, ay, bx, by)
                        if d < 1e-3:
                            to_del.Add(eid)
                            break
        if to_del.Count > 0:
            doc.Delete(to_del)
        for crv in cl_new:
            doc.Create.NewModelCurve(crv, sk.SketchPlane)
        scope.Commit(_SwallowFailures())
        return True
    except Exception:
        try:
            scope.Cancel()
        except Exception:
            pass
        return False


def run_sancas(doc, uidoc, view):
    rooms_v = _coletar_rooms_vista(doc, view)
    prefs = carregar_prefs("sancas")
    wall_types = _coletar_wall_types(doc)
    wt_labels = [_ename(wt) for wt in wall_types]
    ceil_types = _coletar_ceiling_types(doc)
    ct_labels = [_ename(ct) for ct in ceil_types]
    syms = _coletar_curve_based_symbols(doc, ["sanca", "moldura", "tabica", "rodameio"])
    sym_labels = [_symbol_display(s) for s in syms]
    mats_data = _coletar_materiais(doc)

    win = XamlReader.Parse(XAML_SANCAS)
    cb_escopo = win.FindName("cb_escopo")
    cb_tipo = win.FindName("cb_tipo_sanca")
    cb_wall = win.FindName("cb_wall_type")
    cb_ceil = win.FindName("cb_ceiling_type")
    cb_fam = win.FindName("cb_familia")
    cb_mat = win.FindName("cb_material")
    tb_larg = win.FindName("tb_larg")
    tb_alt = win.FindName("tb_alt")
    tb_cota = win.FindName("tb_cota")
    tb_aba = win.FindName("tb_aba_led")
    tb_cort_prof = win.FindName("tb_cort_prof")
    chk_invertida = win.FindName("chk_invertida")
    chk_sep = win.FindName("chk_ignorar_sep")
    chk_inv = win.FindName("chk_inverter")
    btn_ok = win.FindName("btn_ok")
    btn_cancel = win.FindName("btn_cancelar")

    for op in [
        u"Todos os ambientes da vista ativa ({0} encontrados)".format(len(rooms_v)),
        u"Usar ambientes pré-selecionados (ou selecionar na vista)",
        u"Selecionar ambientes manualmente agora",
    ]:
        cb_escopo.Items.Add(op)
    cb_escopo.SelectedIndex = prefs.get("escopo_idx", 1 if uidoc.Selection.GetElementIds().Count > 0 else 0)

    for op in [
        u"Sanca Fechada (Parede testeira + Forro perimetral)",
        u"Sanca Aberta Iluminada p/ Parede (Forro avançado sob tampo superior)",
        u"Sanca Aberta Iluminada (Bandeja LED p/ cima sob teto contínuo)",
        u"Sanca Invertida com Cortineiro (Nicho LED + Cortineiro)",
        u"AutoBIM Perfil — Sanca Chanfrada 45° (Baseada em Linha)",
        u"AutoBIM Perfil — Sanca Meia-Cana Curva (Baseada em Linha)",
        u"AutoBIM Perfil — Sanca em Degrau Duplo (Baseada em Linha)",
        u"Família carregada do projeto (CurveBased)",
    ]:
        cb_tipo.Items.Add(op)
    def_tipo = prefs.get("tipo_idx", 0)
    if def_tipo >= cb_tipo.Items.Count:
        def_tipo = 0
    cb_tipo.SelectedIndex = def_tipo

    sc_wall = _anexar_busca_combo_palavras(
        cb_wall, wt_labels, prefs.get("wall_type"), ["oc_forro", "gesso", "st"], placeholder=u"Pesquisar tipo de parede..."
    )
    sc_ceil = _anexar_busca_combo_palavras(
        cb_ceil, ct_labels, prefs.get("ceil_type"), ["gesso", "forro", "tabica"], placeholder=u"Pesquisar tipo de forro..."
    )
    sc_fam = _anexar_busca_combo_palavras(
        cb_fam, sym_labels, prefs.get("familia"), ["sanca", "rodameio"], placeholder=u"Pesquisar família..."
    )
    sc_mat = _popular_combo_materiais(cb_mat, mats_data, prefs.get("material"))

    tb_larg.Text = prefs.get("larg", "35")
    tb_alt.Text = prefs.get("alt", "15")
    tb_cota.Text = prefs.get("cota", "2,60")
    tb_aba.Text = prefs.get("aba_led", "6")
    tb_cort_prof.Text = prefs.get("cort_prof", "15")
    chk_invertida.IsChecked = bool(prefs.get("invertida", True))
    chk_sep.IsChecked = bool(prefs.get("ignorar_sep", True))
    chk_inv.IsChecked = bool(prefs.get("inverter", False))

    def _toggle(s=None, e=None):
        idx = cb_tipo.SelectedIndex
        is_nat = idx in (0, 1, 2, 3)
        cb_wall.IsEnabled = is_nat
        cb_ceil.IsEnabled = is_nat
        cb_fam.IsEnabled = (idx == 7 and len(syms) > 0)
        cb_mat.IsEnabled = not is_nat
        tb_aba.IsEnabled = idx in (1, 2, 3)
        tb_cort_prof.IsEnabled = (idx == 3)
        chk_invertida.IsEnabled = idx in (0, 4, 5, 6, 7)

    cb_tipo.SelectionChanged += _toggle
    _toggle()

    estado = {"ok": False}

    def _on_ok(s, e):
        if cb_tipo.SelectedIndex == 7 and len(syms) == 0:
            forms.alert(u"Nenhuma família CurveBased carregada.", title=u"Sancas")
            return
        if cb_tipo.SelectedIndex == 7 and sc_fam.selected_index() < 0:
            forms.alert(u"Selecione uma família carregada.", title=u"Sancas")
            return
        estado["ok"] = True
        win.Close()

    btn_ok.Click += _on_ok
    btn_cancel.Click += lambda s, e: win.Close()
    win.ShowDialog()
    if not estado["ok"]:
        return

    wt_idx_s   = sc_wall.selected_index()
    ct_idx_s   = sc_ceil.selected_index()
    fam_idx_s  = sc_fam.selected_index()
    mat_idx_s  = sc_mat.selected_index() if sc_mat is not None else 0
    salvar_prefs("sancas", {
        "escopo_idx": cb_escopo.SelectedIndex,
        "tipo_idx": cb_tipo.SelectedIndex,
        "wall_type": wt_labels[wt_idx_s] if wt_idx_s >= 0 and wt_labels else "",
        "ceil_type": ct_labels[ct_idx_s] if ct_idx_s >= 0 and ct_labels else "",
        "familia": sym_labels[fam_idx_s] if fam_idx_s >= 0 and sym_labels else "",
        "material": mats_data[mat_idx_s - 1][0] if mat_idx_s > 0 and (mat_idx_s - 1) < len(mats_data) else "",
        "larg": tb_larg.Text,
        "alt": tb_alt.Text,
        "cota": tb_cota.Text,
        "aba_led": tb_aba.Text,
        "cort_prof": tb_cort_prof.Text,
        "invertida": bool(chk_invertida.IsChecked),
        "ignorar_sep": bool(chk_sep.IsChecked),
        "inverter": bool(chk_inv.IsChecked),
    })

    rooms = _selecionar_elementos_por_escopo(
        doc, uidoc, view, cb_escopo.SelectedIndex, _is_room, _coletar_rooms_vista,
        u"Selecione os ambientes para modelar Sancas"
    )
    if not rooms:
        forms.alert(u"Nenhum ambiente encontrado.", title=u"Sancas")
        return

    larg_ft = max(5.0, parse_float(tb_larg.Text, 35.0)) * CM_TO_FT
    alt_ft = max(3.0, parse_float(tb_alt.Text, 15.0)) * CM_TO_FT
    cota_def = parse_float(tb_cota.Text, 2.60) * FT
    aba_ft = max(2.0, parse_float(tb_aba.Text, 6.0)) * CM_TO_FT
    cort_ft = max(0.0, parse_float(tb_cort_prof.Text, 15.0)) * CM_TO_FT
    invertida = bool(chk_invertida.IsChecked)
    ign_sep = bool(chk_sep.IsChecked)
    invert = bool(chk_inv.IsChecked)
    tipo_idx = cb_tipo.SelectedIndex

    wt = wall_types[wt_idx_s] if (wt_idx_s >= 0 and wall_types) else None
    wt_w = getattr(wt, "Width", 0.04) if wt else 0.04
    ct = ceil_types[ct_idx_s] if (ct_idx_s >= 0 and ceil_types) else None
    proj_sym = syms[fam_idx_s] if (tipo_idx == 7 and fam_idx_s >= 0) else None
    ceils_all = list(FilteredElementCollector(doc).OfClass(Ceiling).WhereElementIsNotElementType())

    ct_thick = 0.04
    if ct is not None:
        try:
            cs_s = ct.GetCompoundStructure()
            if cs_s is not None and cs_s.GetWidth() > 0.005:
                ct_thick = cs_s.GetWidth()
        except Exception:
            pass

    wall_off = larg_ft + 0.5 * wt_w
    ceil_in_off = larg_ft + wt_w
    # Recuo especifico do forro principal conforme modelo escolhido
    if tipo_idx == 1:
        # Sanca Aberta Iluminada p/ Parede: recuo junto a parede = aba_ft (rasgo lateral da luz)
        sanca_start = 0.0
        recuo_sketch_ft = aba_ft
    elif tipo_idx == 2:
        # Sanca Aberta Iluminada: teto continuo na parte superior, sem recuar c_main
        sanca_start = 0.0
        recuo_sketch_ft = 0.0
    elif tipo_idx == 3:
        # Sanca Invertida com Cortineiro (D02): forro principal recua apenas `cort_ft` formando prateleira LED + tampo superior
        sanca_start = cort_ft
        recuo_sketch_ft = cort_ft
    else:
        sanca_start = 0.0
        recuo_sketch_ft = ceil_in_off

    precisa_recuar_forro = (
        (tipo_idx in (1, 3) and wt is not None and ct is not None)
        or (invertida and tipo_idx == 0 and wt is not None and ct is not None)
    )

    room_itens = []
    for rm in rooms:
        lvl, lvl_elev, _ = _obter_nivel_e_z_room(doc, rm)
        if lvl is None:
            continue
        loops = _coletar_loops_ambiente(rm, incluir_ilhas=False)
        if not loops:
            continue
        outer = loops[0]
        outer_pts = [it["p0"] for it in outer]
        rm_pt = rm.Location.Point if (getattr(rm, "Location", None) and hasattr(rm.Location, "Point")) else outer[0]["p0"]
        c_main, c_bot = _encontrar_forro_sobre_ponto(doc, ceils_all, rm_pt.X, rm_pt.Y, lvl_elev)
        forro_z = c_bot if c_bot is not None else (lvl_elev + cota_def)
        room_itens.append({
            "rm": rm,
            "lvl": lvl,
            "lvl_elev": lvl_elev,
            "outer": outer,
            "outer_pts": outer_pts,
            "c_main": c_main,
            "forro_z": forro_z,
        })

    if not room_itens:
        forms.alert(u"Nenhum ambiente válido com contorno fechado encontrado.", title=u"Sancas")
        return

    tg = TransactionGroup(doc, "AutoBIM - Sancas por Ambiente")
    tg.Start()
    recuos_ok = 0
    criados = 0
    try:
        # Etapa 1: recuar contorno externo do forro principal (SketchEditScope)
        if precisa_recuar_forro:
            seen_c_ids = set()
            for rit in room_itens:
                cm = rit["c_main"]
                if cm is not None and _eid_int(cm.Id) not in seen_c_ids:
                    if _modificar_sketch_forro_sanca(doc, cm, rit["outer_pts"], recuo_sketch_ft):
                        seen_c_ids.add(_eid_int(cm.Id))
                        recuos_ok += 1

        # Etapa 2: Criar Sancas dentro da Transaction
        t = Transaction(doc, "AutoBIM - Criar Sancas")
        opt_f = t.GetFailureHandlingOptions()
        opt_f.SetFailuresPreprocessor(_SwallowFailures())
        t.SetFailureHandlingOptions(opt_f)
        t.Start()

        sym_use = None
        if tipo_idx in (4, 5, 6):
            kind_map = {4: "Sanca_Chanfrada", 5: "Sanca_MeiaCana", 6: "Sanca_Degrau"}
            kind = kind_map[tipo_idx]
            fam_nm = u"AutoBIM_{0}_{1}x{2}cm".format(kind, int(round(larg_ft * FT_TO_CM)), int(round(alt_ft * FT_TO_CM)))
            sym_use = _obter_ou_criar_familia_autobim(doc, fam_nm, kind, {"w_ft": larg_ft, "h_ft": alt_ft})
        elif tipo_idx == 7:
            sym_use = proj_sym

        def _criar_paredes_ao_longo_offset(off_dist, w_base_z, w_height_ft, lvl_obj, lvl_el):
            out_walls = []
            cl_w = _offset_loop_para_curveloop(outer_pts, -off_dist, w_base_z)
            for crv in list(cl_w):
                pa = crv.GetEndPoint(0)
                pb = crv.GetEndPoint(1)
                if pa.DistanceTo(pb) < 0.05:
                    continue
                nw = Wall.Create(doc, Line.CreateBound(XYZ(pa.X, pa.Y, lvl_el), XYZ(pb.X, pb.Y, lvl_el)),
                                 wt.Id, lvl_obj.Id, w_height_ft, w_base_z - lvl_el, False, False)
                _set_bip_double(nw, BuiltInParameter.WALL_ATTR_ROOM_BOUNDING, 0)
                out_walls.append(nw)
            return out_walls

        mat_id, mat_explicit = _resolver_material_escolha(doc, sc_mat, mats_data, "sanca")
        cache_sym_mats = set()
        cache_flip = {}
        for rit in room_itens:
            lvl = rit["lvl"]
            lvl_elev = rit["lvl_elev"]
            outer = rit["outer"]
            outer_pts = rit["outer_pts"]
            forro_z = rit["forro_z"]

            c_main_thick = ct_thick
            if rit["c_main"] is not None:
                try:
                    cm_t = doc.GetElement(rit["c_main"].GetTypeId())
                    cs_m = cm_t.GetCompoundStructure() if cm_t is not None else None
                    if cs_m is not None and cs_m.GetWidth() > 0.005:
                        c_main_thick = cs_m.GetWidth()
                except Exception:
                    pass

            if tipo_idx == 1 and wt is not None and ct is not None:
                # Sanca Aberta Iluminada p/ Parede: forro principal recuado em aba_ft formando prateleira sob tampo superior
                z_shelf_top = forro_z + c_main_thick
                z_topo_bot = z_shelf_top + alt_ft
                outer_sanca_off = aba_ft + larg_ft + wt_w
                try:
                    # 1. Tampo superior completo (0 ate outer_sanca_off)
                    cl_top_out = _offset_loop_para_curveloop(outer_pts, 0.0, z_topo_bot)
                    cl_top_in = _offset_loop_para_curveloop(outer_pts, -outer_sanca_off, z_topo_bot)
                    cl_top_list = List[CurveLoop]()
                    cl_top_list.Add(cl_top_out)
                    cl_top_list.Add(cl_top_in)
                    c_topo = Ceiling.Create(doc, cl_top_list, ct.Id, lvl.Id)
                    _set_bip_double(c_topo, BuiltInParameter.CEILING_HEIGHTABOVELEVEL_PARAM, z_topo_bot - lvl_elev)
                    criados += 1

                    # Se nao houver forro principal no ambiente, criar a prateleira inferior de forro
                    c_prat = None
                    if rit["c_main"] is None:
                        cl_pr_out = _offset_loop_para_curveloop(outer_pts, -aba_ft, forro_z)
                        cl_pr_in = _offset_loop_para_curveloop(outer_pts, -outer_sanca_off, forro_z)
                        cl_pr_list = List[CurveLoop]()
                        cl_pr_list.Add(cl_pr_out)
                        cl_pr_list.Add(cl_pr_in)
                        c_prat = Ceiling.Create(doc, cl_pr_list, ct.Id, lvl.Id)
                        _set_bip_double(c_prat, BuiltInParameter.CEILING_HEIGHTABOVELEVEL_PARAM, forro_z - lvl_elev)
                        criados += 1

                    # 2. Parede vertical subindo do topo do forro inferior ate o topo do tampo superior
                    w_list = _criar_paredes_ao_longo_offset(aba_ft + larg_ft + 0.5 * wt_w, z_shelf_top, alt_ft + ct_thick, lvl, lvl_elev)
                    criados += len(w_list)

                    doc.Regenerate()
                    for ws in w_list:
                        _try_join_pair(doc, c_topo, ws)
                        if c_prat is not None:
                            _try_join_pair(doc, c_prat, ws)
                    for wi in range(len(w_list)):
                        if len(w_list) >= 2:
                            _try_join_pair(doc, w_list[wi], w_list[(wi + 1) % len(w_list)])
                except Exception:
                    pass
            elif tipo_idx == 2 and wt is not None and ct is not None:
                # Sanca Aberta Iluminada (bandeja sob teto continuo + montante recuado ate o teto + aba frontal LED)
                sanca_bot_z = forro_z - alt_ft - ct_thick
                try:
                    cl_out = _offset_loop_para_curveloop(outer_pts, 0.0, sanca_bot_z)
                    cl_in = _offset_loop_para_curveloop(outer_pts, -ceil_in_off, sanca_bot_z)
                    cl_list = List[CurveLoop]()
                    cl_list.Add(cl_out)
                    cl_list.Add(cl_in)
                    c_sanca = Ceiling.Create(doc, cl_list, ct.Id, lvl.Id)
                    _set_bip_double(c_sanca, BuiltInParameter.CEILING_HEIGHTABOVELEVEL_PARAM, sanca_bot_z - lvl_elev)
                    criados += 1

                    w_front_h = min(max(alt_ft - 2.0 * CM_TO_FT, 1.0 * CM_TO_FT), aba_ft) + ct_thick
                    w_back_off = (0.35 * larg_ft) if larg_ft >= 20.0 * CM_TO_FT else (0.5 * wt_w)
                    w_front_list = _criar_paredes_ao_longo_offset(wall_off, sanca_bot_z, w_front_h, lvl, lvl_elev)
                    w_back_list = _criar_paredes_ao_longo_offset(w_back_off, sanca_bot_z, alt_ft + ct_thick, lvl, lvl_elev)
                    criados += len(w_front_list) + len(w_back_list)

                    doc.Regenerate()
                    for wlist_cur in (w_front_list, w_back_list):
                        for ws in wlist_cur:
                            _try_join_pair(doc, c_sanca, ws)
                        for wi in range(len(wlist_cur)):
                            if len(wlist_cur) >= 2:
                                _try_join_pair(doc, wlist_cur[wi], wlist_cur[(wi + 1) % len(wlist_cur)])
                except Exception:
                    pass
            elif tipo_idx == 3 and wt is not None and ct is not None:
                # Sanca Invertida com Cortineiro (D02):
                # 0..cort_ft aberto (cortina) -> aba LED em cort_ft subindo sobre c_main -> nicho LED (larg_ft x alt_ft) -> parede traseira -> tampo superior ate parede
                z_shelf_top = forro_z + c_main_thick
                z_topo_bot = z_shelf_top + alt_ft
                top_in_off = cort_ft + larg_ft + 2.0 * wt_w
                try:
                    # 1. Tampo superior completo (0 ate face externa da parede traseira do nicho)
                    cl_top_out = _offset_loop_para_curveloop(outer_pts, 0.0, z_topo_bot)
                    cl_top_in = _offset_loop_para_curveloop(outer_pts, -top_in_off, z_topo_bot)
                    cl_top_list = List[CurveLoop]()
                    cl_top_list.Add(cl_top_out)
                    cl_top_list.Add(cl_top_in)
                    c_topo = Ceiling.Create(doc, cl_top_list, ct.Id, lvl.Id)
                    _set_bip_double(c_topo, BuiltInParameter.CEILING_HEIGHTABOVELEVEL_PARAM, z_topo_bot - lvl_elev)
                    criados += 1

                    # Se nao houver forro principal no ambiente, criar a prateleira inferior a partir de cort_ft
                    c_fundo = None
                    if rit["c_main"] is None:
                        cl_sh_out = _offset_loop_para_curveloop(outer_pts, -cort_ft, forro_z)
                        cl_sh_in = _offset_loop_para_curveloop(outer_pts, -top_in_off, forro_z)
                        cl_sh_list = List[CurveLoop]()
                        cl_sh_list.Add(cl_sh_out)
                        cl_sh_list.Add(cl_sh_in)
                        c_fundo = Ceiling.Create(doc, cl_sh_list, ct.Id, lvl.Id)
                        _set_bip_double(c_fundo, BuiltInParameter.CEILING_HEIGHTABOVELEVEL_PARAM, forro_z - lvl_elev)
                        criados += 1

                    # 2. Aba LED na borda do cortineiro e parede traseira do nicho sobre o forro inferior
                    aba_h_use = min(max(alt_ft - 2.0 * CM_TO_FT, 1.0 * CM_TO_FT), aba_ft)
                    w_aba_list = _criar_paredes_ao_longo_offset(cort_ft + 0.5 * wt_w, z_shelf_top, aba_h_use, lvl, lvl_elev)
                    w_back_list = _criar_paredes_ao_longo_offset(cort_ft + wt_w + larg_ft + 0.5 * wt_w, z_shelf_top, alt_ft + ct_thick, lvl, lvl_elev)
                    criados += len(w_aba_list) + len(w_back_list)

                    doc.Regenerate()
                    for ws in w_back_list:
                        _try_join_pair(doc, c_topo, ws)
                    if c_fundo is not None:
                        for ws in w_aba_list + w_back_list:
                            _try_join_pair(doc, c_fundo, ws)
                    for wlist_cur in (w_aba_list, w_back_list):
                        for wi in range(len(wlist_cur)):
                            if len(wlist_cur) >= 2:
                                _try_join_pair(doc, wlist_cur[wi], wlist_cur[(wi + 1) % len(wlist_cur)])
                except Exception:
                    pass
            elif tipo_idx == 0 and wt is not None and ct is not None:
                # Sanca Fechada (Invertida: tampo sobe acima de forro_z; Rebaixada: placa desce abaixo)
                c_sanca_z = (forro_z + alt_ft) if invertida else (forro_z - alt_ft - ct_thick)
                try:
                    cl_out = _offset_loop_para_curveloop(outer_pts, 0.0, c_sanca_z)
                    # Em CCW, offset positivo expande para fora e negativo contrai para dentro
                    cl_in_ceil = _offset_loop_para_curveloop(outer_pts, -ceil_in_off, c_sanca_z)
                    cl_in_wall = _offset_loop_para_curveloop(outer_pts, -wall_off, c_sanca_z)
                    cl_list = List[CurveLoop]()
                    cl_list.Add(cl_out)
                    cl_list.Add(cl_in_ceil)
                    c_sanca = Ceiling.Create(doc, cl_list, ct.Id, lvl.Id)
                    _set_bip_double(c_sanca, BuiltInParameter.CEILING_HEIGHTABOVELEVEL_PARAM, c_sanca_z - lvl_elev)
                    criados += 1

                    # Parede(s) testeira vertical na borda interna
                    inner_curves = list(cl_in_wall)
                    w_list = []
                    w_base = forro_z if invertida else c_sanca_z
                    w_h = alt_ft + ct_thick
                    for crv in inner_curves:
                        pa = crv.GetEndPoint(0)
                        pb = crv.GetEndPoint(1)
                        if pa.DistanceTo(pb) < 0.05:
                            continue
                        w_s = Wall.Create(doc, Line.CreateBound(XYZ(pa.X, pa.Y, lvl_elev), XYZ(pb.X, pb.Y, lvl_elev)),
                                          wt.Id, lvl.Id, w_h, w_base - lvl_elev, False, False)
                        _set_bip_double(w_s, BuiltInParameter.WALL_ATTR_ROOM_BOUNDING, 0)
                        criados += 1
                        w_list.append(w_s)
                    doc.Regenerate()
                    for w_s in w_list:
                        _try_join_pair(doc, c_sanca, w_s)
                    for wi in range(len(w_list)):
                        if len(w_list) >= 2:
                            _try_join_pair(doc, w_list[wi], w_list[(wi + 1) % len(w_list)])
                except Exception:
                    pass
            else:
                # Familia CurveBased ao longo das bordas de parede da sala
                z_fam = forro_z if invertida else (forro_z - alt_ft)
                loop_insts = []
                for edge in outer:
                    if ign_sep and edge["is_sep"]:
                        continue
                    inst, _ = _colocar_curva_familia(
                        doc, sym_use, lvl, edge["p0"], edge["p1"],
                        z_fam, edge["inward"], cache_flip,
                        invert_manual=invert,
                        expect_on_wall_side=False,
                        material_id=mat_id,
                        overwrite_autobim_mat=mat_explicit,
                        cache_sym_mats=cache_sym_mats,
                    )
                    if inst is not None:
                        criados += 1
                        loop_insts.append((edge["p0"], edge["p1"], inst))
                if len(loop_insts) >= 2:
                    _unir_instancias_cantos_loop(doc, loop_insts, tol_dist=0.35)
        t.Commit()
        tg.Assimilate()
    except Exception:
        tg.RollBack()
        raise

    msg = u"Sancas modeladas com sucesso: {0} elementos criados.".format(criados)
    if recuos_ok > 0:
        msg += u"\nForros principais recuados automaticamente: {0}".format(recuos_ok)
    forms.alert(msg, title=u"Sancas - Resultado")


# ---------------------------------------------------------------------------
# Engine: Ferramenta 4 - RODAMEIOS / BOISERIE POR AMBIENTE
# ---------------------------------------------------------------------------

XAML_RODAMEIOS = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Rodameios e Molduras por Ambiente (AutoBIM)" Width="480" SizeToContent="Height"
        WindowStartupLocation="CenterScreen" ResizeMode="NoResize"
        Background="#F4F5F7" FontFamily="Segoe UI" FontSize="12">
    <StackPanel Margin="18">
        <TextBlock Text="Modo de seleção dos ambientes" FontWeight="SemiBold"/>
        <ComboBox x:Name="cb_escopo" Margin="0,4,0,10" Height="27"/>

        <TextBlock Text="Sistema / Perfil de Rodameio" FontWeight="SemiBold"/>
        <ComboBox x:Name="cb_sistema" Margin="0,4,0,10" Height="27"/>

        <TextBlock Text="Família carregada (Rodameio / Boiserie)" FontWeight="SemiBold"/>
        <ComboBox x:Name="cb_familia" Margin="0,4,0,10" Height="27"/>

        <TextBlock Text="Tipo de Parede Fina (quando Parede Fina Nativa)" FontWeight="SemiBold"/>
        <ComboBox x:Name="cb_wall_type" Margin="0,4,0,10" Height="27"/>

        <Grid Margin="0,0,0,10">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="Auto"/>
                <ColumnDefinition Width="10"/>
                <ColumnDefinition Width="*"/>
            </Grid.ColumnDefinitions>
            <TextBlock Grid.Column="0" Text="Material (AutoBIM / sem material):" VerticalAlignment="Center" FontWeight="SemiBold"/>
            <ComboBox x:Name="cb_material" Grid.Column="2" Height="26"/>
        </Grid>

        <Grid Margin="0,0,0,10">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="10"/>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="10"/>
                <ColumnDefinition Width="*"/>
            </Grid.ColumnDefinitions>
            <StackPanel Grid.Column="0">
                <TextBlock Text="Altura base (cm)" FontWeight="SemiBold"/>
                <TextBox x:Name="tb_elev" Margin="0,4,0,0" Height="26" VerticalContentAlignment="Center" Text="110"/>
            </StackPanel>
            <StackPanel Grid.Column="2">
                <TextBlock Text="Projeção / Esp. (cm)" FontWeight="SemiBold"/>
                <TextBox x:Name="tb_esp" Margin="0,4,0,0" Height="26" VerticalContentAlignment="Center" Text="2,5"/>
            </StackPanel>
            <StackPanel Grid.Column="4">
                <TextBlock Text="Altura perfil (cm)" FontWeight="SemiBold"/>
                <TextBox x:Name="tb_h" Margin="0,4,0,0" Height="26" VerticalContentAlignment="Center" Text="6,0"/>
            </StackPanel>
        </Grid>

        <CheckBox x:Name="chk_piso" IsChecked="True" Margin="0,2,0,4"
                  Content="Medir altura a partir do piso acabado do ambiente (quando existir)"/>
        <CheckBox x:Name="chk_aberturas" IsChecked="True" Margin="0,0,0,4"
                  Content="Descontar automaticamente Portas e Janelas que cruzam esta altura"/>
        <CheckBox x:Name="chk_join_cantos" IsChecked="True" Margin="0,0,0,4"
                  Content="Unir cantos automaticamente (JoinGeometry / fechar quinas externas)"/>
        <CheckBox x:Name="chk_join" IsChecked="True" Margin="0,0,0,4"
                  Content="Unir geometria (JoinGeometry) no modo Parede Fina"/>
        <CheckBox x:Name="chk_sep" IsChecked="True" Margin="0,0,0,6"
                  Content="Ignorar linhas de separação de ambiente"/>

        <Grid Margin="0,0,0,6">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="Auto"/>
                <ColumnDefinition Width="10"/>
                <ColumnDefinition Width="*"/>
            </Grid.ColumnDefinitions>
            <TextBlock Grid.Column="0" Text="Rotação do perfil (família carregada):" VerticalAlignment="Center" FontWeight="SemiBold"/>
            <ComboBox x:Name="cb_rot_perfil" Grid.Column="2" Height="26"/>
        </Grid>

        <CheckBox x:Name="chk_inv_perfil" IsChecked="False" Margin="0,0,0,4"
                  Content="Inverter perfil (parâmetro interno da família)"/>
        <CheckBox x:Name="chk_inv" IsChecked="False" Margin="0,0,0,14"
                  Content="Inverter lado (dentro / fora)"/>

        <StackPanel Orientation="Horizontal" HorizontalAlignment="Right">
            <Button x:Name="btn_cancelar" Content="Cancelar" Width="95" Height="30" Margin="0,0,10,0"/>
            <Button x:Name="btn_ok" Content="Criar Rodameios" Width="155" Height="30"
                    Background="#0078D4" Foreground="White" FontWeight="SemiBold"/>
        </StackPanel>
    </StackPanel>
</Window>
"""


def _rodar_molding_por_ambiente(
    doc,
    rooms,
    is_wall_mode,
    wall_type,
    symbol,
    base_cm,
    perfil_h_ft,
    use_finished_floor,
    cut_openings,
    do_join,
    ignore_sep,
    invert_side,
    expect_on_wall_side=False,
    try_void_cut=False,
    apply_miters=False,
    rot_deg=None,
    inv_perfil=None,
    auto_upright=False,
    perfil_w_ft=None,
    auto_join_corners=True,
    material_id=None,
    overwrite_autobim_mat=False,
):
    criados = 0
    voids_ok = 0
    cache_flip = {}
    cache_sym_mats = set()

    for rm in rooms:
        lvl, lvl_elev, default_z = _obter_nivel_e_z_room(doc, rm)
        if lvl is None:
            continue
        floor_z = _detectar_topo_piso_ambiente(doc, rm, default_z) if use_finished_floor else default_z
        target_z = floor_z + base_cm * CM_TO_FT

        loops = _coletar_loops_ambiente(rm, incluir_ilhas=True)
        for loop in loops:
            n_seg = len(loop)
            loop_insts = []
            for idx, seg in enumerate(loop):
                if ignore_sep and seg["is_sep"]:
                    continue

                if cut_openings:
                    hw_list = seg.get("host_walls") or ([seg["host_wall"]] if seg["host_wall"] else [])
                    iv_list = _intervalos_aberturas_no_segmento(
                        doc, hw_list, seg["p0"], seg["p1"], target_z, target_z + perfil_h_ft
                    )
                    pieces = _subtrair_aberturas_segmento(seg["length"], iv_list, min_len=0.05)
                else:
                    pieces = [{"start": 0.0, "end": seg["length"], "cut_start": False, "cut_end": False}]

                prev_seg = loop[(idx - 1) % n_seg]
                next_seg = loop[(idx + 1) % n_seg]
                same_col_prev = (abs(seg["t"].DotProduct(prev_seg["t"])) > 0.98)
                same_col_next = (abs(seg["t"].DotProduct(next_seg["t"])) > 0.98)
                cross_prev = prev_seg["t"].X * seg["t"].Y - prev_seg["t"].Y * seg["t"].X
                cross_next = seg["t"].X * next_seg["t"].Y - seg["t"].Y * next_seg["t"].X
                prev_is_sep = bool(ignore_sep and prev_seg["is_sep"])
                next_is_sep = bool(ignore_sep and next_seg["is_sep"])

                for pc in pieces:
                    pa = seg["p0"] + seg["t"] * pc["start"]
                    pb = seg["p0"] + seg["t"] * pc["end"]

                    # Em cantos externos (convexos, ex. quina/pilar), estender pela espessura para fechar o encontro
                    on_wall = bool(expect_on_wall_side) ^ bool(invert_side)
                    is_convex_start = (not pc["cut_start"]) and (not same_col_prev) and (not prev_is_sep) and (cross_prev < -0.2)
                    is_convex_end = (not pc["cut_end"]) and (not same_col_next) and (not next_is_sep) and (cross_next < -0.2)
                    if not is_wall_mode and auto_join_corners and not on_wall:
                        ext_w = perfil_w_ft or 0.0
                        if ext_w <= 1e-4 and symbol is not None:
                            for k_mp, v_mp in cache_flip.items():
                                if isinstance(k_mp, tuple) and len(k_mp) == 2 and k_mp[0] == "max_proj":
                                    ext_w = v_mp
                                    break
                        if ext_w > 1e-4:
                            if is_convex_start:
                                pa = pa - seg["t"] * ext_w
                            if is_convex_end:
                                pb = pb + seg["t"] * ext_w

                    if pa.DistanceTo(pb) < 0.05:
                        continue

                    if is_wall_mode and wall_type is not None:
                        w_half = 0.5 * getattr(wall_type, "Width", 0.06)
                        wa = pa + seg["inward"] * w_half
                        wb = pb + seg["inward"] * w_half
                        wline = Line.CreateBound(XYZ(wa.X, wa.Y, lvl_elev), XYZ(wb.X, wb.Y, lvl_elev))
                        nw = Wall.Create(doc, wline, wall_type.Id, lvl.Id, perfil_h_ft, target_z - lvl_elev, False, False)
                        _set_bip_double(nw, BuiltInParameter.WALL_ATTR_ROOM_BOUNDING, 0)
                        if do_join:
                            for hw in (seg.get("host_walls") or []):
                                try:
                                    if not JoinGeometryUtils.AreElementsJoined(doc, nw, hw):
                                        JoinGeometryUtils.JoinGeometry(doc, nw, hw)
                                except Exception:
                                    pass
                        criados += 1
                    else:
                        ca_deg = None
                        cb_deg = None
                        if apply_miters:
                            is_straight_start = pc["cut_start"] or same_col_prev or prev_is_sep
                            is_straight_end = pc["cut_end"] or same_col_next or next_is_sep
                            ca_deg = 0.0 if is_straight_start else (-45.0 if is_convex_start else 45.0)
                            cb_deg = 0.0 if is_straight_end else (-45.0 if is_convex_end else 45.0)

                        inst, v_ok = _colocar_curva_familia(
                            doc, symbol, lvl, pa, pb, target_z, seg["inward"], cache_flip,
                            invert_manual=invert_side,
                            expect_on_wall_side=expect_on_wall_side,
                            start_miter_deg=ca_deg,
                            end_miter_deg=cb_deg,
                            host_elem_for_void=seg.get("host_walls") or seg["host_wall"],
                            try_void_cut=try_void_cut,
                            rot_deg=rot_deg,
                            inv_perfil=inv_perfil,
                            auto_upright=auto_upright,
                            material_id=material_id,
                            overwrite_autobim_mat=overwrite_autobim_mat,
                            cache_sym_mats=cache_sym_mats,
                        )
                        if inst is not None:
                            criados += 1
                            loop_insts.append((pa, pb, inst))
                            if v_ok:
                                voids_ok += 1

            if not is_wall_mode and len(loop_insts) >= 2:
                if auto_join_corners:
                    _unir_instancias_cantos_loop(doc, loop_insts, tol_dist=0.35)
                else:
                    _desunir_instancias_cantos_loop(doc, loop_insts, tol_dist=0.35)
    return criados, voids_ok


def run_rodameios(doc, uidoc, view):
    rooms_v = _coletar_rooms_vista(doc, view)
    prefs = carregar_prefs("rodameios")
    syms = _coletar_curve_based_symbols(doc, ["rodameio", "boiserie", "moldura"])
    sym_labels = [_symbol_display(s) for s in syms]
    wall_types = _coletar_wall_types(doc)
    wt_labels = [_ename(wt) for wt in wall_types]
    mats_data = _coletar_materiais(doc)

    win = XamlReader.Parse(XAML_RODAMEIOS)
    cb_escopo = win.FindName("cb_escopo")
    cb_sis = win.FindName("cb_sistema")
    cb_fam = win.FindName("cb_familia")
    cb_wall = win.FindName("cb_wall_type")
    cb_mat = win.FindName("cb_material")
    tb_elev = win.FindName("tb_elev")
    tb_esp = win.FindName("tb_esp")
    tb_h = win.FindName("tb_h")
    chk_piso = win.FindName("chk_piso")
    chk_ab = win.FindName("chk_aberturas")
    chk_join_cantos = win.FindName("chk_join_cantos")
    chk_join = win.FindName("chk_join")
    chk_sep = win.FindName("chk_sep")
    cb_rot = win.FindName("cb_rot_perfil")
    chk_inv_perfil = win.FindName("chk_inv_perfil")
    chk_inv = win.FindName("chk_inv")
    btn_ok = win.FindName("btn_ok")
    btn_cancel = win.FindName("btn_cancelar")

    for op in [
        u"Todos os ambientes da vista ativa ({0} encontrados)".format(len(rooms_v)),
        u"Usar ambientes pré-selecionados (ou selecionar na vista)",
        u"Selecionar ambientes manualmente agora",
    ]:
        cb_escopo.Items.Add(op)
    cb_escopo.SelectedIndex = prefs.get("escopo_idx", 1 if uidoc.Selection.GetElementIds().Count > 0 else 0)

    for op in [
        u"Família carregada no projeto (Rodameio / Boiserie)",
        u"AutoBIM Santa Luzia — VersaLED (70 x 17 mm)",
        u"AutoBIM Santa Luzia — Ref. 466 Reto (30 x 16 mm)",
        u"AutoBIM Santa Luzia — Ref. 468 Clássico (27 x 11 mm)",
        u"Parede Fina Nativa + JoinGeometry",
    ]:
        cb_sis.Items.Add(op)
    cb_sis.SelectedIndex = prefs.get("sis_idx", 0 if syms else 1)

    sc_fam = _anexar_busca_combo_palavras(
        cb_fam, sym_labels, prefs.get("familia"), ["rodameio", "boiserie"], placeholder=u"Pesquisar família..."
    )
    sc_wall = _anexar_busca_combo_palavras(
        cb_wall, wt_labels, prefs.get("wall_type"), ["acabamento", "revest", "gesso"], placeholder=u"Pesquisar tipo de parede..."
    )
    sc_mat = _popular_combo_materiais(cb_mat, mats_data, prefs.get("material"))

    for op in [
        u"Automático (corrigir perfil deitado para vertical)",
        u"0° (original da família)",
        u"90°",
        u"180°",
        u"270°",
    ]:
        cb_rot.Items.Add(op)
    cb_rot.SelectedIndex = prefs.get("rot_idx", 0)

    tb_elev.Text = prefs.get("elev", "110")
    tb_esp.Text = prefs.get("esp", "1,7")
    tb_h.Text = prefs.get("h", "7,0")
    chk_piso.IsChecked = bool(prefs.get("piso", True))
    chk_ab.IsChecked = bool(prefs.get("aberturas", True))
    chk_join_cantos.IsChecked = bool(prefs.get("join_cantos", prefs.get("miter", True)))
    chk_join.IsChecked = bool(prefs.get("join", True))
    chk_sep.IsChecked = bool(prefs.get("sep", True))
    chk_inv_perfil.IsChecked = bool(prefs.get("inv_perfil", False))
    chk_inv.IsChecked = bool(prefs.get("inv", False))

    ui_ready = {"ok": False}

    def _toggle(s=None, e=None):
        idx = cb_sis.SelectedIndex
        cb_fam.IsEnabled = (idx == 0 and len(syms) > 0)
        cb_rot.IsEnabled = (idx == 0 and len(syms) > 0)
        chk_inv_perfil.IsEnabled = (idx == 0 and len(syms) > 0)
        cb_wall.IsEnabled = (idx == 4)
        chk_join.IsEnabled = (idx == 4)
        chk_join_cantos.IsEnabled = (idx != 4)
        cb_mat.IsEnabled = (idx != 4)
        if ui_ready["ok"]:
            sl_dims = {1: ("1,7", "7,0"), 2: ("1,6", "3,0"), 3: ("1,1", "2,7")}
            if idx in sl_dims:
                tb_esp.Text, tb_h.Text = sl_dims[idx]

    cb_sis.SelectionChanged += _toggle
    _toggle()
    ui_ready["ok"] = True

    estado = {"ok": False}

    def _on_ok(s, e):
        if cb_sis.SelectedIndex == 0 and len(syms) == 0:
            forms.alert(u"Nenhuma família de Rodameio carregada no projeto. Escolha um perfil AutoBIM ou Parede Fina.", title=u"Rodameios")
            return
        if cb_sis.SelectedIndex == 0 and sc_fam.selected_index() < 0:
            forms.alert(u"Selecione uma família carregada.", title=u"Rodameios")
            return
        if cb_sis.SelectedIndex == 4 and sc_wall.selected_index() < 0:
            forms.alert(u"Selecione um tipo de parede.", title=u"Rodameios")
            return
        estado["ok"] = True
        win.Close()

    btn_ok.Click += _on_ok
    btn_cancel.Click += lambda s, e: win.Close()
    win.ShowDialog()
    if not estado["ok"]:
        return

    fam_idx_r  = sc_fam.selected_index()
    wt_idx_r   = sc_wall.selected_index()
    mat_idx_r  = sc_mat.selected_index() if sc_mat is not None else 0
    salvar_prefs("rodameios", {
        "escopo_idx": cb_escopo.SelectedIndex,
        "sis_idx": cb_sis.SelectedIndex,
        "familia": sym_labels[fam_idx_r] if fam_idx_r >= 0 and sym_labels else "",
        "wall_type": wt_labels[wt_idx_r] if wt_idx_r >= 0 and wt_labels else "",
        "material": mats_data[mat_idx_r - 1][0] if mat_idx_r > 0 and (mat_idx_r - 1) < len(mats_data) else "",
        "rot_idx": cb_rot.SelectedIndex,
        "elev": tb_elev.Text,
        "esp": tb_esp.Text,
        "h": tb_h.Text,
        "piso": bool(chk_piso.IsChecked),
        "aberturas": bool(chk_ab.IsChecked),
        "join_cantos": bool(chk_join_cantos.IsChecked),
        "join": bool(chk_join.IsChecked),
        "sep": bool(chk_sep.IsChecked),
        "inv_perfil": bool(chk_inv_perfil.IsChecked),
        "inv": bool(chk_inv.IsChecked),
    })

    rooms = _selecionar_elementos_por_escopo(
        doc, uidoc, view, cb_escopo.SelectedIndex, _is_room, _coletar_rooms_vista,
        u"Selecione os ambientes para criar Rodameios"
    )
    if not rooms:
        forms.alert(u"Nenhum ambiente encontrado.", title=u"Rodameios")
        return

    base_cm = parse_float(tb_elev.Text, 110.0)
    esp_ft = max(0.5, parse_float(tb_esp.Text, 1.7)) * CM_TO_FT
    h_ft = max(1.0, parse_float(tb_h.Text, 7.0)) * CM_TO_FT
    sis_idx = cb_sis.SelectedIndex

    t = Transaction(doc, "AutoBIM - Rodameios")
    opt_f = t.GetFailureHandlingOptions()
    opt_f.SetFailuresPreprocessor(_SwallowFailures())
    t.SetFailureHandlingOptions(opt_f)
    t.Start()

    try:
        sym = None
        wt = None
        if sis_idx == 0:
            sym = syms[fam_idx_r]
        elif sis_idx in (1, 2, 3):
            kmap = {1: "SantaLuzia_VersaLED", 2: "SantaLuzia_466", 3: "SantaLuzia_468"}
            kind = kmap[sis_idx]
            ver_tag = "v3" if kind == "SantaLuzia_468" else "v2"
            fam_nm = u"AutoBIM_{0}_{1}_{2}x{3}cm".format(
                kind,
                ver_tag,
                str(round(esp_ft * FT_TO_CM, 1)).replace(".", "_"),
                str(round(h_ft * FT_TO_CM, 1)).replace(".", "_"),
            )
            sym = _obter_ou_criar_familia_autobim(doc, fam_nm, kind, {"w_ft": esp_ft, "h_ft": h_ft})
        else:
            wt = wall_types[wt_idx_r]

        rot_map = {0: None, 1: 0.0, 2: 90.0, 3: 180.0, 4: 270.0}
        sel_rot_idx = cb_rot.SelectedIndex if cb_rot.SelectedIndex >= 0 else 0
        rot_val = rot_map.get(sel_rot_idx, None) if sis_idx == 0 else None
        auto_up = (sis_idx == 0 and sel_rot_idx == 0)
        inv_p_val = bool(chk_inv_perfil.IsChecked) if sis_idx == 0 else None
        mat_id, mat_explicit = _resolver_material_escolha(doc, sc_mat, mats_data, "rodameio")

        criados, _ = _rodar_molding_por_ambiente(
            doc, rooms,
            is_wall_mode=(sis_idx == 4),
            wall_type=wt,
            symbol=sym,
            base_cm=base_cm,
            perfil_h_ft=h_ft,
            use_finished_floor=bool(chk_piso.IsChecked),
            cut_openings=bool(chk_ab.IsChecked),
            do_join=bool(chk_join.IsChecked),
            ignore_sep=bool(chk_sep.IsChecked),
            invert_side=bool(chk_inv.IsChecked),
            apply_miters=bool(chk_join_cantos.IsChecked),
            rot_deg=rot_val,
            inv_perfil=inv_p_val,
            auto_upright=auto_up,
            perfil_w_ft=esp_ft,
            auto_join_corners=bool(chk_join_cantos.IsChecked),
            material_id=mat_id,
            overwrite_autobim_mat=mat_explicit,
        )
        t.Commit()
    except Exception:
        if t.HasStarted():
            t.RollBack()
        raise

    forms.alert(u"Rodameios modelados com sucesso: {0} trechos criados.".format(criados), title=u"Rodameios - Resultado")


# ---------------------------------------------------------------------------
# Engine: Ferramenta 5 - RODAPES POR FAMILIA / PERFIS
# ---------------------------------------------------------------------------

XAML_RODAPES_FAM = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Rodapés por Família / Perfis (AutoBIM)" Width="485" SizeToContent="Height"
        WindowStartupLocation="CenterScreen" ResizeMode="NoResize"
        Background="#F4F5F7" FontFamily="Segoe UI" FontSize="12">
    <StackPanel Margin="18">
        <TextBlock Text="Modo de seleção dos ambientes" FontWeight="SemiBold"/>
        <ComboBox x:Name="cb_escopo" Margin="0,4,0,10" Height="27"/>

        <TextBlock Text="Origem do Perfil de Rodapé" FontWeight="SemiBold"/>
        <ComboBox x:Name="cb_origem" Margin="0,4,0,10" Height="27"/>

        <TextBlock Text="Família e Tipo de Rodapé (Embutido / Invertido / Reto / Santa Luzia)" FontWeight="SemiBold"/>
        <ComboBox x:Name="cb_familia" Margin="0,4,0,10" Height="27"/>

        <Grid Margin="0,0,0,10">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="Auto"/>
                <ColumnDefinition Width="10"/>
                <ColumnDefinition Width="*"/>
            </Grid.ColumnDefinitions>
            <TextBlock Grid.Column="0" Text="Material (AutoBIM / sem material):" VerticalAlignment="Center" FontWeight="SemiBold"/>
            <ComboBox x:Name="cb_material" Grid.Column="2" Height="26"/>
        </Grid>

        <Grid Margin="0,0,0,10">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="10"/>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="10"/>
                <ColumnDefinition Width="*"/>
            </Grid.ColumnDefinitions>
            <StackPanel Grid.Column="0">
                <TextBlock Text="Desloc. piso (cm)" FontWeight="SemiBold"/>
                <TextBox x:Name="tb_desloc" Margin="0,4,0,0" Height="26" VerticalContentAlignment="Center" Text="0,0"/>
            </StackPanel>
            <StackPanel Grid.Column="2">
                <TextBlock Text="Espessura AutoBIM (cm)" FontWeight="SemiBold"/>
                <TextBox x:Name="tb_esp" Margin="0,4,0,0" Height="26" VerticalContentAlignment="Center" Text="1,5"/>
            </StackPanel>
            <StackPanel Grid.Column="4">
                <TextBlock Text="Altura ref./AutoBIM (cm)" FontWeight="SemiBold"/>
                <TextBox x:Name="tb_h" Margin="0,4,0,0" Height="26" VerticalContentAlignment="Center" Text="10,0"/>
            </StackPanel>
        </Grid>

        <CheckBox x:Name="chk_piso" IsChecked="True" Margin="0,2,0,4"
                  Content="Alinhar automaticamente à face superior do piso acabado do ambiente"/>
        <CheckBox x:Name="chk_aberturas" IsChecked="True" Margin="0,0,0,4"
                  Content="Descontar Portas e Janelas que alcançam a altura do rodapé"/>
        <CheckBox x:Name="chk_void_cut" IsChecked="True" Margin="0,0,0,4"
                  Content="Cortar parede hospedeira (AddInstanceVoidCut) para Embutido / Invertido"/>
        <CheckBox x:Name="chk_miter" IsChecked="True" Margin="0,0,0,4"
                  Content="Aplicar 45° nos cantos e 0° junto a portas/vãos"/>
        <CheckBox x:Name="chk_sep" IsChecked="True" Margin="0,0,0,4"
                  Content="Ignorar linhas de separação de ambiente"/>
        <CheckBox x:Name="chk_inv" IsChecked="False" Margin="0,0,0,14"
                  Content="Inverter lado do perfil (dentro / fora da parede)"/>

        <StackPanel Orientation="Horizontal" HorizontalAlignment="Right">
            <Button x:Name="btn_cancelar" Content="Cancelar" Width="95" Height="30" Margin="0,0,10,0"/>
            <Button x:Name="btn_ok" Content="Criar Rodapés" Width="155" Height="30"
                    Background="#0078D4" Foreground="White" FontWeight="SemiBold"/>
        </StackPanel>
    </StackPanel>
</Window>
"""


def run_rodapes_familia(doc, uidoc, view):
    rooms_v = _coletar_rooms_vista(doc, view)
    prefs = carregar_prefs("rodapes_familia")
    syms = _coletar_curve_based_symbols(doc, ["rodapé", "rodape", "santa luzia", "embutido", "invertido"])
    sym_labels = [_symbol_display(s) for s in syms]
    mats_data = _coletar_materiais(doc)

    win = XamlReader.Parse(XAML_RODAPES_FAM)
    cb_escopo = win.FindName("cb_escopo")
    cb_orig = win.FindName("cb_origem")
    cb_fam = win.FindName("cb_familia")
    cb_mat = win.FindName("cb_material")
    tb_desloc = win.FindName("tb_desloc")
    tb_esp = win.FindName("tb_esp")
    tb_h = win.FindName("tb_h")
    chk_piso = win.FindName("chk_piso")
    chk_ab = win.FindName("chk_aberturas")
    chk_void = win.FindName("chk_void_cut")
    chk_miter = win.FindName("chk_miter")
    chk_sep = win.FindName("chk_sep")
    chk_inv = win.FindName("chk_inv")
    btn_ok = win.FindName("btn_ok")
    btn_cancel = win.FindName("btn_cancelar")

    for op in [
        u"Todos os ambientes da vista ativa ({0} encontrados)".format(len(rooms_v)),
        u"Usar ambientes pré-selecionados (ou selecionar na vista)",
        u"Selecionar ambientes manualmente agora",
    ]:
        cb_escopo.Items.Add(op)
    cb_escopo.SelectedIndex = prefs.get("escopo_idx", 1 if uidoc.Selection.GetElementIds().Count > 0 else 0)

    for op in [
        u"Família carregada no projeto (Embutido / Invertido / Santa Luzia / Reto)",
        u"AutoBIM Santa Luzia — Clássico 2 Frisos (493 / 505 / 547)",
        u"AutoBIM Santa Luzia — Boleado Moderno (455 / 456 / 457)",
        u"AutoBIM Santa Luzia — Reto Liso / Porcelanato (466 / Reto)",
        u"AutoBIM — Rodapé Invertido (Perfil L)",
        u"AutoBIM — Rodapé Embutido (Retangular)",
    ]:
        cb_orig.Items.Add(op)
    def_orig = prefs.get("orig_idx", 1)
    if def_orig >= cb_orig.Items.Count:
        def_orig = 1
    cb_orig.SelectedIndex = def_orig

    sc_fam = _anexar_busca_combo_palavras(
        cb_fam, sym_labels, prefs.get("familia"), ["rodap", "santa luzia"], placeholder=u"Pesquisar família / tipo de rodapé..."
    )
    sc_mat = _popular_combo_materiais(cb_mat, mats_data, prefs.get("material"))

    tb_desloc.Text = prefs.get("desloc", "0,0")
    tb_esp.Text = prefs.get("esp", "1,6")
    tb_h.Text = prefs.get("h", "12,0")
    chk_piso.IsChecked = bool(prefs.get("piso", True))
    chk_ab.IsChecked = bool(prefs.get("aberturas", True))
    chk_void.IsChecked = bool(prefs.get("void_cut", True))
    chk_miter.IsChecked = bool(prefs.get("miter", True))
    chk_sep.IsChecked = bool(prefs.get("sep", True))
    chk_inv.IsChecked = bool(prefs.get("inv", False))

    ui_ready = {"ok": False}

    def _toggle(s=None, e=None):
        idx = cb_orig.SelectedIndex
        is_proj = (idx == 0)
        cb_fam.IsEnabled = is_proj and (len(syms) > 0)
        if ui_ready["ok"]:
            preset_dims = {
                1: ("1,6", "12,0"),
                2: ("1,6", "10,0"),
                3: ("1,5", "10,0"),
                4: ("2,0", "2,5"),
                5: ("2,0", "4,0"),
            }
            if idx in preset_dims:
                tb_esp.Text, tb_h.Text = preset_dims[idx]

    cb_orig.SelectionChanged += _toggle
    _toggle()
    ui_ready["ok"] = True

    estado = {"ok": False}

    def _on_ok(s, e):
        if cb_orig.SelectedIndex == 0 and len(syms) == 0:
            forms.alert(u"Nenhuma família de Rodapé carregada no projeto. Selecione uma opção AutoBIM Paramétrica.", title=u"Rodapés")
            return
        if cb_orig.SelectedIndex == 0 and sc_fam.selected_index() < 0:
            forms.alert(u"Selecione uma família/tipo de rodapé.", title=u"Rodapés")
            return
        estado["ok"] = True
        win.Close()

    btn_ok.Click += _on_ok
    btn_cancel.Click += lambda s, e: win.Close()
    win.ShowDialog()
    if not estado["ok"]:
        return

    fam_idx_rp = sc_fam.selected_index()
    mat_idx_rp = sc_mat.selected_index() if sc_mat is not None else 0
    salvar_prefs("rodapes_familia", {
        "escopo_idx": cb_escopo.SelectedIndex,
        "orig_idx": cb_orig.SelectedIndex,
        "familia": sym_labels[fam_idx_rp] if fam_idx_rp >= 0 and sym_labels else "",
        "material": mats_data[mat_idx_rp - 1][0] if mat_idx_rp > 0 and (mat_idx_rp - 1) < len(mats_data) else "",
        "desloc": tb_desloc.Text,
        "esp": tb_esp.Text,
        "h": tb_h.Text,
        "piso": bool(chk_piso.IsChecked),
        "aberturas": bool(chk_ab.IsChecked),
        "void_cut": bool(chk_void.IsChecked),
        "miter": bool(chk_miter.IsChecked),
        "sep": bool(chk_sep.IsChecked),
        "inv": bool(chk_inv.IsChecked),
    })

    rooms = _selecionar_elementos_por_escopo(
        doc, uidoc, view, cb_escopo.SelectedIndex, _is_room, _coletar_rooms_vista,
        u"Selecione os ambientes para criar Rodapés"
    )
    if not rooms:
        forms.alert(u"Nenhum ambiente encontrado.", title=u"Rodapés")
        return

    desloc_cm = parse_float(tb_desloc.Text, 0.0)
    esp_ft = max(0.5, parse_float(tb_esp.Text, 1.5)) * CM_TO_FT
    h_ft = max(2.0, parse_float(tb_h.Text, 10.0)) * CM_TO_FT
    orig_idx = cb_orig.SelectedIndex

    # Para Embutido / Invertido, a parte principal/vazio entra NA PAREDE!
    fam_sel_lbl = sym_labels[fam_idx_rp].lower() if (orig_idx == 0 and fam_idx_rp >= 0) else ""
    is_embedded = (orig_idx in (4, 5)) or ("embutido" in fam_sel_lbl or "invertido" in fam_sel_lbl)

    t = Transaction(doc, "AutoBIM - Rodapés por Família")
    opt_f = t.GetFailureHandlingOptions()
    opt_f.SetFailuresPreprocessor(_SwallowFailures())
    t.SetFailureHandlingOptions(opt_f)
    t.Start()

    is_oc_fam = (orig_idx == 0)
    perfil_w_use = esp_ft
    try:
        if is_oc_fam:
            for _dbg_fn in ("rodape_angle_debug.txt", "rodape_oc_instances_debug.txt"):
                _dbg_p = os.path.join(r"C:\Users\Alex\.gemini\antigravity\brain\aece5e12-b21b-4b3c-b801-2bf89559d5d3\scratch", _dbg_fn)
                if os.path.isfile(_dbg_p):
                    os.remove(_dbg_p)
    except Exception:
        pass

    try:
        if is_oc_fam:
            sym = syms[fam_idx_rp]
            # Se familia tiver altura/largura no tipo (ex OC_Altura do Elemento, Largura), ler para filtro e quinas
            for pnm in (u"OC_Altura do Elemento", u"Altura"):
                try:
                    tp = sym.LookupParameter(pnm)
                    if tp and tp.HasValue and tp.AsDouble() > 0.01:
                        h_ft = tp.AsDouble()
                        break
                except Exception:
                    pass
            for pnm in (u"OC_Largura do Elemento", u"OC_Largura", u"Largura", u"Espessura"):
                try:
                    tp = sym.LookupParameter(pnm)
                    if tp and tp.HasValue and tp.AsDouble() > 0.001:
                        perfil_w_use = tp.AsDouble()
                        break
                except Exception:
                    pass
        else:
            kmap = {
                1: "SantaLuzia_493",
                2: "SantaLuzia_Boleado",
                3: "Rodape_Reto",
                4: "Rodape_Invertido_L",
                5: "Rodape_Embutido",
            }
            kind = kmap.get(orig_idx, "SantaLuzia_493")
            fam_nm = u"AutoBIM_Rodape_{0}_{1}x{2}cm".format(
                kind.replace("Moldura_", "").replace("Rodape_", ""),
                str(round(esp_ft * FT_TO_CM, 1)).replace(".", "_"),
                str(round(h_ft * FT_TO_CM, 1)).replace(".", "_"),
            )
            sym = _obter_ou_criar_familia_autobim(doc, fam_nm, kind, {"w_ft": esp_ft, "h_ft": h_ft})

        mat_id, mat_explicit = _resolver_material_escolha(doc, sc_mat, mats_data, "rodape")
        criados, voids_ok = _rodar_molding_por_ambiente(
            doc, rooms,
            is_wall_mode=False,
            wall_type=None,
            symbol=sym,
            base_cm=desloc_cm,
            perfil_h_ft=h_ft,
            use_finished_floor=bool(chk_piso.IsChecked),
            cut_openings=bool(chk_ab.IsChecked),
            do_join=False,
            ignore_sep=bool(chk_sep.IsChecked),
            invert_side=bool(chk_inv.IsChecked),
            expect_on_wall_side=is_embedded,
            try_void_cut=bool(chk_void.IsChecked),
            apply_miters=bool(chk_miter.IsChecked),
            perfil_w_ft=perfil_w_use,
            auto_join_corners=(False if is_oc_fam else bool(chk_miter.IsChecked)),
            material_id=mat_id,
            overwrite_autobim_mat=mat_explicit,
        )
        t.Commit()
    except Exception:
        if t.HasStarted():
            t.RollBack()
        raise

    msg = u"Rodapés modelados com sucesso: {0} trechos criados.".format(criados)
    if voids_ok > 0:
        msg += u"\nCortes na parede hospedeira (AddInstanceVoidCut): {0}".format(voids_ok)
    forms.alert(msg, title=u"Rodapés por Família - Resultado")
