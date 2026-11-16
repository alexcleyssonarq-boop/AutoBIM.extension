# -*- coding: utf-8 -*-
__title__ = "Paginação\nde Piso"
__author__ = "AutoBIM"
__version__ = "Versão 1.0"
__doc__ = """Alinha a origem da paginação (hachura de modelo) do piso ao canto ou ponto clicado pelo usuário.

Como usar:
1. Selecione o piso (ou clique nele quando solicitado).
2. Clique no vértice/canto do ambiente onde o assentamento das peças deve iniciar.
A hachura de modelo do piso é deslocada para alinhar exatamente no ponto escolhido."""

import clr
import math
clr.AddReference("System")

from Autodesk.Revit.DB import *
from Autodesk.Revit.UI.Selection import ObjectType, ObjectSnapTypes, ISelectionFilter
from pyrevit import forms, script

doc   = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument
view  = doc.ActiveView

if not isinstance(view, ViewPlan):
    forms.alert(
        "Execute este comando em uma vista de Planta de Piso.",
        title="Vista Inválida",
        exitscript=True
    )


class FloorSelectionFilter(ISelectionFilter):
    def AllowElement(self, elem):
        return isinstance(elem, Floor)

    def AllowReference(self, ref, pt):
        return True


class SilentFailurePreprocessor(IFailuresPreprocessor):
    """Suprime avisos e cancela silenciosamente erros de cotas/alinhamentos temporários incompatíveis."""
    def PreprocessFailures(self, failuresAccessor):
        try:
            failures = failuresAccessor.GetFailureMessages()
            if not failures or len(failures) == 0:
                return FailureProcessingResult.Continue
            for f in failures:
                sev = f.GetSeverity()
                if sev == FailureSeverity.Warning:
                    failuresAccessor.DeleteWarning(f)
                else:
                    return FailureProcessingResult.ProceedWithRollBack
        except Exception:
            pass
        return FailureProcessingResult.Continue


def make_silent_transaction(doc_obj, name):
    t = Transaction(doc_obj, name)
    fho = t.GetFailureHandlingOptions()
    fho.SetFailuresPreprocessor(SilentFailurePreprocessor())
    fho.SetClearAfterRollback(True)
    t.SetFailureHandlingOptions(fho)
    return t


# =============================================================================
# ETAPA 1 — Selecionar o Piso
# =============================================================================
floor = None
for eid in uidoc.Selection.GetElementIds():
    el = doc.GetElement(eid)
    if isinstance(el, Floor):
        floor = el
        break

if floor is None:
    try:
        with forms.WarningBar(title="1º Passo: Clique no Piso que deseja paginar"):
            ref_fl = uidoc.Selection.PickObject(
                ObjectType.Element,
                FloorSelectionFilter(),
                "Selecione o Piso para alinhar a paginação"
            )
            floor = doc.GetElement(ref_fl)
    except Exception:
        script.exit()

if floor is None:
    script.exit()

# =============================================================================
# ETAPA 2 — Garantir SketchPlane e clicar no Ponto de Início da Paginação
# =============================================================================
z_view = view.GenLevel.Elevation if view.GenLevel else 0.0

if view.SketchPlane is None:
    with Transaction(doc, "Definir Plano da Vista") as t_sp:
        t_sp.Start()
        plane = Plane.CreateByNormalAndOrigin(XYZ.BasisZ, XYZ(0, 0, z_view))
        view.SketchPlane = SketchPlane.Create(doc, plane)
        t_sp.Commit()

snaps = (
    ObjectSnapTypes.Endpoints
    | ObjectSnapTypes.Intersections
    | ObjectSnapTypes.Midpoints
    | ObjectSnapTypes.Nearest
)

try:
    with forms.WarningBar(title="2º Passo: Clique no canto/vértice onde a paginação deve começar"):
        pt_origem = uidoc.Selection.PickPoint(
            snaps,
            "Clique no canto/ponto de início da paginação do piso"
        )
except Exception:
    script.exit()


# =============================================================================
# ETAPA 3 — Obter a Face Superior do Piso e as Referências da Hachura de Modelo
# =============================================================================
def get_top_face_and_stable_bases(fl):
    face_obj = None
    stable_list = []

    # 1. Tentar via HostObjectUtils.GetTopFaces
    try:
        top_refs = HostObjectUtils.GetTopFaces(fl)
        if top_refs and len(top_refs) > 0:
            ref0 = top_refs[0]
            s_rep = ref0.ConvertToStableRepresentation(doc)
            if s_rep and s_rep not in stable_list:
                stable_list.append(s_rep)
            f_cand = fl.GetGeometryObjectFromReference(ref0)
            if isinstance(f_cand, PlanarFace):
                face_obj = f_cand
    except Exception:
        pass

    # 2. Tentar via geometria da vista ativa (opt.View = view)
    try:
        opt_v = Options()
        opt_v.ComputeReferences = True
        opt_v.View = view
        geom_v = fl.get_Geometry(opt_v)
        if geom_v is not None:
            melhor_v = None
            for obj in geom_v:
                if isinstance(obj, Solid) and obj.Volume > 0:
                    for f in obj.Faces:
                        if isinstance(f, PlanarFace) and f.FaceNormal.Z > 0.99 and f.Reference is not None:
                            if melhor_v is None or f.Area > melhor_v.Area:
                                melhor_v = f
            if melhor_v is not None:
                if face_obj is None:
                    face_obj = melhor_v
                s_rep = melhor_v.Reference.ConvertToStableRepresentation(doc)
                if s_rep and s_rep not in stable_list:
                    stable_list.append(s_rep)
    except Exception:
        pass

    # 3. Tentar via geometria geral (DetailLevel.Fine)
    try:
        opt_f = Options()
        opt_f.ComputeReferences = True
        opt_f.DetailLevel = ViewDetailLevel.Fine
        geom_f = fl.get_Geometry(opt_f)
        if geom_f is not None:
            melhor_f = None
            for obj in geom_f:
                if isinstance(obj, Solid) and obj.Volume > 0:
                    for f in obj.Faces:
                        if isinstance(f, PlanarFace) and f.FaceNormal.Z > 0.99 and f.Reference is not None:
                            if melhor_f is None or f.Area > melhor_f.Area:
                                melhor_f = f
            if melhor_f is not None:
                if face_obj is None:
                    face_obj = melhor_f
                s_rep = melhor_f.Reference.ConvertToStableRepresentation(doc)
                if s_rep and s_rep not in stable_list:
                    stable_list.append(s_rep)
    except Exception:
        pass

    return face_obj, stable_list


top_face, stable_bases = get_top_face_and_stable_bases(floor)
if top_face is None or not stable_bases:
    forms.alert("Não foi possível localizar a face superior planar do piso.", exitscript=True)

# Verificar se o material da face superior possui hachura do tipo "Modelo" (Model Pattern)
mat_id = top_face.MaterialElementId
if mat_id is None or mat_id == ElementId.InvalidElementId:
    forms.alert(
        "O piso selecionado não possui material associado à face superior.",
        title="Paginação de Piso",
        exitscript=True
    )

mat = doc.GetElement(mat_id)
pat_id = mat.SurfaceForegroundPatternId if mat else ElementId.InvalidElementId
pat_el = doc.GetElement(pat_id) if (pat_id and pat_id != ElementId.InvalidElementId) else None

if pat_el is None or pat_el.GetFillPattern().Target != FillPatternTarget.Model:
    forms.alert(
        "O material deste piso não utiliza uma Hachura de Modelo (Model Pattern) no primeiro plano da superfície.\n\n"
        "Apenas hachuras do tipo 'Modelo' (ex.: 60x60, 80x80, réguas) possuem origem real alinhável no Revit.",
        title="Paginação de Piso",
        exitscript=True
    )

fill_pat = pat_el.GetFillPattern()
grids = list(fill_pat.GetFillGrids())
grid_count = len(grids)
if grid_count <= 0:
    forms.alert("A hachura deste material não possui linhas de grade (FillGrids).", exitscript=True)

# Construir direções candidatas para os eixos da hachura (UV da face + FillGrids + Eixos Globais)
x_vec = XYZ(top_face.XVector.X, top_face.XVector.Y, 0).Normalize()
y_vec = XYZ(top_face.YVector.X, top_face.YVector.Y, 0).Normalize()

direcoes_candidatas = []


def add_unique_dir(v):
    if v is None or v.GetLength() < 1e-6:
        return
    vn = XYZ(v.X, v.Y, 0).Normalize()
    for d_exist in direcoes_candidatas:
        if abs(vn.CrossProduct(d_exist).GetLength()) < 1e-3:
            return
    direcoes_candidatas.append(vn)


for g in grids:
    ang = g.Angle
    # Direção relativa aos vetores UV da face planar
    d_uv = XYZ(
        x_vec.X * math.cos(ang) + y_vec.X * math.sin(ang),
        x_vec.Y * math.cos(ang) + y_vec.Y * math.sin(ang),
        0
    )
    add_unique_dir(d_uv)
    # Direção relativa ao sistema global XY
    d_xy = XYZ(math.cos(ang), math.sin(ang), 0)
    add_unique_dir(d_xy)

add_unique_dir(x_vec)
add_unique_dir(y_vec)
add_unique_dir(XYZ.BasisX)
add_unique_dir(XYZ.BasisY)

max_idx_busca = max(grid_count * 4 + 2, 10)
eixos_alinhados = 0

tg = TransactionGroup(doc, "Alinhar Paginação de Piso")
tg.Start()

try:
    for d_vec in direcoes_candidatas:
        if eixos_alinhados >= grid_count:
            break

        n_vec = XYZ(-d_vec.Y, d_vec.X, 0).Normalize()

        # Recoletar referências atualizadas das linhas da hachura (índices >= 1)
        hatch_refs = []
        for s_base in stable_bases:
            for idx in range(1, max_idx_busca):
                chave_ref = "{}/{}".format(s_base, idx)
                try:
                    r_h = Reference.ParseFromStableRepresentation(doc, chave_ref)
                    if r_h is not None:
                        hatch_refs.append((chave_ref, r_h))
                except Exception:
                    pass

        for chave_ref, ref_hatch in hatch_refs:
            # -----------------------------------------------------------------
            # Transação 1: Testar se ref_hatch é estritamente paralela a d_vec,
            # medir a distância até pt_origem e colocar o ReferencePlane sobre ref_hatch
            # -----------------------------------------------------------------
            rp = None
            shift_to_target = None
            ja_alinhado = False

            with make_silent_transaction(doc, "Localizar Eixo da Hachura") as t1:
                t1.Start()
                try:
                    p_a = XYZ(pt_origem.X - d_vec.X * 100.0, pt_origem.Y - d_vec.Y * 100.0, z_view)
                    p_b = XYZ(pt_origem.X + d_vec.X * 100.0, pt_origem.Y + d_vec.Y * 100.0, z_view)
                    rp = doc.Create.NewReferencePlane(p_a, p_b, XYZ.BasisZ, view)
                    doc.Regenerate()

                    try:
                        rp_ref = rp.GetReference()
                    except Exception:
                        rp_ref = Reference.ParseFromStableRepresentation(
                            doc, "{}:0:SURFACE".format(rp.UniqueId)
                        )

                    ra_dist = ReferenceArray()
                    ra_dist.Append(rp_ref)
                    ra_dist.Append(ref_hatch)

                    line_perp = Line.CreateBound(
                        XYZ(pt_origem.X, pt_origem.Y, z_view),
                        XYZ(pt_origem.X + n_vec.X * 10.0, pt_origem.Y + n_vec.Y * 10.0, z_view)
                    )
                    dim1 = doc.Create.NewDimension(view, line_perp, ra_dist)
                    doc.Regenerate()

                    dist = dim1.Value if dim1 is not None else None
                    if dim1 is not None:
                        doc.Delete(dim1.Id)

                    if dist is None:
                        t1.RollBack()
                        continue

                    if dist < 1e-4:
                        # Apagamos dim1 ANTES de mover rp para o Revit nunca tentar
                        # recalcular uma cota em referências não paralelas durante o MoveElement!
                        passo_teste = 0.25
                        ElementTransformUtils.MoveElement(doc, rp.Id, n_vec * passo_teste)
                        doc.Regenerate()

                        ra_dist2 = ReferenceArray()
                        ra_dist2.Append(rp_ref)
                        ra_dist2.Append(ref_hatch)
                        dim2 = doc.Create.NewDimension(view, line_perp, ra_dist2)
                        doc.Regenerate()
                        dist_teste = dim2.Value if dim2 is not None else None
                        if dim2 is not None:
                            doc.Delete(dim2.Id)

                        t1.RollBack()

                        if dist_teste is not None and abs(dist_teste - passo_teste) < 1e-3:
                            ja_alinhado = True
                        else:
                            continue
                    else:
                        # dim1 já foi apagada acima; movemos rp livremente sem cota presa a ele
                        ElementTransformUtils.MoveElement(doc, rp.Id, n_vec * (dist * 0.5))
                        doc.Regenerate()

                        ra_dist2 = ReferenceArray()
                        ra_dist2.Append(rp_ref)
                        ra_dist2.Append(ref_hatch)
                        dim2 = doc.Create.NewDimension(view, line_perp, ra_dist2)
                        doc.Regenerate()
                        new_dist = dim2.Value if dim2 is not None else None
                        if dim2 is not None:
                            doc.Delete(dim2.Id)

                        if new_dist is None:
                            t1.RollBack()
                            continue

                        aproximou = abs(new_dist - (dist * 0.5)) < 1e-3
                        afastou   = abs(new_dist - (dist * 1.5)) < 1e-3

                        if not (aproximou or afastou):
                            # Não é paralela a d_vec!
                            t1.RollBack()
                            continue

                        if aproximou:
                            # ref_hatch está em +n_vec * dist; avançar mais +0.5 * dist
                            ElementTransformUtils.MoveElement(doc, rp.Id, n_vec * (dist * 0.5))
                            shift_to_target = -n_vec * dist
                        else:
                            # ref_hatch está em -n_vec * dist; recuar -1.5 * dist
                            ElementTransformUtils.MoveElement(doc, rp.Id, -n_vec * (dist * 1.5))
                            shift_to_target = n_vec * dist

                        doc.Regenerate()
                        t1.Commit()
                except Exception:
                    if t1.HasStarted():
                        t1.RollBack()
                    rp = None
                    shift_to_target = None

            if ja_alinhado:
                eixos_alinhados += 1
                break

            if rp is None or shift_to_target is None or not rp.IsValidObject:
                continue

            # -----------------------------------------------------------------
            # Transação 2: Criar vínculo NewAlignment entre rp e ref_hatch
            # (Agora ambos estão exatamente colineares)
            # -----------------------------------------------------------------
            alinhou = False
            with make_silent_transaction(doc, "Vincular Eixo da Hachura") as t2:
                t2.Start()
                refs_rp_tentativa = []
                try:
                    refs_rp_tentativa.append(
                        Reference.ParseFromStableRepresentation(doc, "{}:0:SURFACE".format(rp.UniqueId))
                    )
                except Exception:
                    pass
                try:
                    refs_rp_tentativa.append(rp.GetReference())
                except Exception:
                    pass

                for r_plane in refs_rp_tentativa:
                    if r_plane is None:
                        continue
                    try:
                        doc.Create.NewAlignment(view, r_plane, ref_hatch)
                        alinhou = True
                        break
                    except Exception:
                        try:
                            doc.Create.NewAlignment(view, ref_hatch, r_plane)
                            alinhou = True
                            break
                        except Exception:
                            pass

                if alinhou:
                    if t2.Commit() != TransactionStatus.Committed:
                        alinhou = False
                else:
                    if t2.HasStarted():
                        t2.RollBack()

            # -----------------------------------------------------------------
            # Transação 3: Mover o ReferencePlane até pt_origem (arrastando a hachura)
            # -----------------------------------------------------------------
            moveu = False
            if alinhou:
                with make_silent_transaction(doc, "Deslocar Eixo da Hachura") as t3:
                    t3.Start()
                    try:
                        ElementTransformUtils.MoveElement(doc, rp.Id, shift_to_target)
                        if t3.Commit() == TransactionStatus.Committed:
                            moveu = True
                    except Exception:
                        if t3.HasStarted():
                            t3.RollBack()

            # -----------------------------------------------------------------
            # Transação 4: Remover o ReferencePlane temporário
            # -----------------------------------------------------------------
            with make_silent_transaction(doc, "Limpar Plano Temporário") as t4:
                t4.Start()
                try:
                    if rp.IsValidObject:
                        doc.Delete(rp.Id)
                    t4.Commit()
                except Exception:
                    if t4.HasStarted():
                        t4.RollBack()

            if moveu:
                eixos_alinhados += 1
                break

    tg.Assimilate()
except Exception:
    if tg.HasStarted():
        tg.RollBack()

if eixos_alinhados == 0:
    forms.alert(
        "Não foi possível alinhar automaticamente os eixos desta hachura.",
        title="Paginação de Piso",
        exitscript=True
    )
