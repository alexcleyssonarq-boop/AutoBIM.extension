# -*- coding: utf-8 -*-
__title__ = "Cota Rápida\npor Linha"
__author__ = "AutoBIM"
__version__ = "Versão 2.0"
__doc__ = """Posiciona uma cadeia contínua de cotas a partir de dois pontos clicados na planta ativa.

Dois cliques:
Clique no 1º ponto (antes da primeira parede) e depois no 2º ponto (após a última parede). Um marcador temporário aparece no 1º ponto enquanto você escolhe o 2º. Durante o comando o snap fica desligado, então hachuras e pisos não prendem o cursor.

O comando detecta todas as paredes cruzadas pela linha entre os dois pontos e posiciona uma cota em cadeia medindo espessuras e vãos livres."""

import clr
import math
clr.AddReference("System")

from Autodesk.Revit.DB import *
from Autodesk.Revit.UI.Selection import ObjectSnapTypes
from pyrevit import forms, script
from Snippets._walls import WallGeometry

doc   = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument
view  = doc.ActiveView

if not isinstance(view, ViewPlan):
    forms.alert(
        "Execute este comando em uma vista de Planta de Piso.",
        title="Vista Inválida",
        exitscript=True
    )

z_view = view.GenLevel.Elevation if view.GenLevel else 0.0

# "None" é palavra reservada no Python, por isso o acesso via getattr
SNAP_NENHUM = getattr(ObjectSnapTypes, "None")


def get_face_point(face):
    try:
        return face.Origin
    except:
        bbox = face.GetBoundingBox()
        uv_mid = UV((bbox.Min.U + bbox.Max.U) * 0.5, (bbox.Min.V + bbox.Max.V) * 0.5)
        return face.Evaluate(uv_mid)


def build_marker_curves(pt, z, with_ring):
    """Curvas do marcador: mira com 4 traços, e anel opcional ao redor do ponto."""
    ring_r = 0.12 / 0.3048    # raio do anel: 12 cm
    tick_in = 0.07 / 0.3048   # início dos traços: 7 cm do centro
    tick_out = 0.20 / 0.3048  # fim dos traços: 20 cm do centro
    curves = []

    if with_ring:
        # Anel formado por dois semicírculos (arco de 3 pontos)
        curves.append(Arc.Create(
            XYZ(pt.X + ring_r, pt.Y, z),
            XYZ(pt.X - ring_r, pt.Y, z),
            XYZ(pt.X, pt.Y + ring_r, z)))
        curves.append(Arc.Create(
            XYZ(pt.X + ring_r, pt.Y, z),
            XYZ(pt.X - ring_r, pt.Y, z),
            XYZ(pt.X, pt.Y - ring_r, z)))
        a, b = tick_in, tick_out
    else:
        # Sem anel: cruz simples passando pelo ponto
        a, b = 0.0, tick_out

    curves.append(Line.CreateBound(XYZ(pt.X + a, pt.Y, z), XYZ(pt.X + b, pt.Y, z)))
    curves.append(Line.CreateBound(XYZ(pt.X - a, pt.Y, z), XYZ(pt.X - b, pt.Y, z)))
    curves.append(Line.CreateBound(XYZ(pt.X, pt.Y + a, z), XYZ(pt.X, pt.Y + b, z)))
    curves.append(Line.CreateBound(XYZ(pt.X, pt.Y - a, z), XYZ(pt.X, pt.Y - b, z)))
    return curves


def create_temp_marker(pt, z, with_ring=True):
    """Marcador temporário no ponto clicado. Retorna os ids criados."""
    ids = []
    ogs = OverrideGraphicSettings()
    ogs.SetProjectionLineColor(Color(255, 95, 0))
    ogs.SetProjectionLineWeight(3)

    for crv in build_marker_curves(pt, z, with_ring):
        dc = doc.Create.NewDetailCurve(view, crv)
        view.SetElementOverrides(dc.Id, ogs)
        ids.append(dc.Id)
    return ids


# =============================================================================
# ETAPA 1 — Capturar 2 pontos (snap desligado) com marcador temporário
# =============================================================================
tg = TransactionGroup(doc, "Cota Rápida por Linha")
tg.Start()

# PickPoint precisa de um plano de trabalho na vista
if view.SketchPlane is None:
    with Transaction(doc, "Definir Plano da Vista") as t_sp:
        t_sp.Start()
        plane = Plane.CreateByNormalAndOrigin(XYZ.BasisZ, XYZ(0, 0, z_view))
        view.SketchPlane = SketchPlane.Create(doc, plane)
        t_sp.Commit()

marker_ids = []
pt1 = None
pt2 = None
cancelado = False

try:
    pt1 = uidoc.Selection.PickPoint(
        SNAP_NENHUM,
        "Clique no 1º ponto (antes da primeira parede)"
    )

    # Marcador visual no 1º ponto: tenta o anel com mira; se o arco falhar,
    # usa a cruz simples; se tudo falhar, o comando continua sem marcador.
    for usar_anel in (True, False):
        try:
            with Transaction(doc, "Marcador Temporário") as t_mk:
                t_mk.Start()
                marker_ids = create_temp_marker(pt1, z_view, usar_anel)
                t_mk.Commit()
            uidoc.RefreshActiveView()
            break
        except Exception:
            marker_ids = []

    pt2 = uidoc.Selection.PickPoint(
        SNAP_NENHUM,
        "Clique no 2º ponto (após a última parede)"
    )
except Exception:
    # Esc ou cancelamento
    cancelado = True

if cancelado:
    tg.RollBack()
    script.exit()

p1_2d = XYZ(pt1.X, pt1.Y, z_view)
p2_2d = XYZ(pt2.X, pt2.Y, z_view)

if p1_2d.DistanceTo(p2_2d) < 0.1:
    tg.RollBack()
    forms.alert("Os dois pontos clicados estão muito próximos.", exitscript=True)

guide_line = Line.CreateBound(p1_2d, p2_2d)

# =============================================================================
# ETAPA 2 — Paredes da vista cruzadas pela linha entre os dois pontos
# =============================================================================
all_walls = (
    FilteredElementCollector(doc, view.Id)
    .OfClass(Wall)
    .WhereElementIsNotElementType()
    .ToElements()
)

intersected_walls = []

for wall in all_walls:
    try:
        curve = wall.Location.Curve
        if not isinstance(curve, Line):
            continue
        w0 = curve.GetEndPoint(0)
        w1 = curve.GetEndPoint(1)
        wall_line_2d = Line.CreateBound(XYZ(w0.X, w0.Y, z_view), XYZ(w1.X, w1.Y, z_view))

        if guide_line.Intersect(wall_line_2d) == SetComparisonResult.Overlap:
            intersected_walls.append(wall)
    except:
        pass

if not intersected_walls:
    tg.RollBack()
    forms.alert(
        "Nenhuma parede foi cruzada entre o 1º e o 2º ponto.",
        title="Cota Rápida por Linha",
        exitscript=True
    )

# =============================================================================
# ETAPA 3 — Agrupar paredes paralelas e criar a cota em cadeia
# =============================================================================
groups = []
for wall in intersected_walls:
    w_dir = XYZ(wall.Location.Curve.Direction.X, wall.Location.Curve.Direction.Y, 0).Normalize()
    placed = False
    for g_dir, g_list in groups:
        if w_dir.CrossProduct(g_dir).GetLength() < 1e-3:
            g_list.append(wall)
            placed = True
            break
    if not placed:
        groups.append((w_dir, [wall]))

groups.sort(key=lambda item: len(item[1]), reverse=True)
main_wall_dir, main_walls = groups[0]

dim_dir = XYZ(-main_wall_dir.Y, main_wall_dir.X, 0).Normalize()
mid_click = (p1_2d + p2_2d) * 0.5

# Faces coplanares são deduplicadas para não gerar segmentos zerados
stations = {}
for w in main_walls:
    wg = WallGeometry(w)
    for face in wg.get_parallel_faces():
        if face.Reference is None:
            continue
        f_pt = get_face_point(face)
        pos_perp = (f_pt - mid_click).DotProduct(dim_dir)
        key = round(pos_perp, 2)
        if key not in stations or face.Area > stations[key][0]:
            stations[key] = (face.Area, face, pos_perp)

if len(stations) < 2:
    tg.RollBack()
    forms.alert("Não foi possível obter referências suficientes das faces das paredes.", exitscript=True)

ref_array = ReferenceArray()
for _, face, _ in sorted(stations.values(), key=lambda x: x[2]):
    ref_array.Append(face.Reference)

with Transaction(doc, "Criar Cota e Remover Marcador") as t:
    t.Start()
    try:
        for mid in marker_ids:
            try:
                doc.Delete(mid)
            except:
                pass

        half_len = max(p1_2d.DistanceTo(p2_2d), 10.0)
        dim_p1 = XYZ(mid_click.X - dim_dir.X * half_len, mid_click.Y - dim_dir.Y * half_len, z_view)
        dim_p2 = XYZ(mid_click.X + dim_dir.X * half_len, mid_click.Y + dim_dir.Y * half_len, z_view)
        dim_line = Line.CreateBound(dim_p1, dim_p2)

        doc.Create.NewDimension(view, dim_line, ref_array)
        t.Commit()
    except Exception as e:
        t.RollBack()
        tg.RollBack()
        forms.alert("Erro ao criar cota:\n{}".format(str(e)), exitscript=True)

# Um único Ctrl+Z desfaz a cota inteira
tg.Assimilate()
