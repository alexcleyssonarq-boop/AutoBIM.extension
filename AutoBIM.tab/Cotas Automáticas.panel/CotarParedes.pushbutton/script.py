# -*- coding: utf-8 -*-
__title__ = "Cotar Paredes\nSelecionadas"
__author__ = "AutoBIM"
__version__ = "Versão 1.1"
__doc__ = """Posiciona cotas externas em cadeia (espessuras + vãos) para todas as paredes selecionadas na planta ativa.

Cotagem Externa Automática:
Selecione quantas paredes desejar (inclusive com caixa de seleção na planta inteira). O comando agrupa as paredes por direção (horizontais, verticais ou inclinadas), coleta todas as faces de espessuras e vãos livres e posiciona as linhas de cota automaticamente pelo lado de fora da edificação."""

import clr
clr.AddReference("System")

from Autodesk.Revit.DB import *
from pyrevit import forms, revit, script
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

# =============================================================================
# ETAPA 1 — Obter paredes pré-selecionadas ou solicitar seleção na vista
# =============================================================================
pre_selected_ids = uidoc.Selection.GetElementIds()
walls = [doc.GetElement(eid) for eid in pre_selected_ids if isinstance(doc.GetElement(eid), Wall)]

if not walls:
    with forms.WarningBar(title="Selecione as Paredes (ex.: caixa de seleção) e clique em Finish"):
        walls = revit.pick_elements_by_category(BuiltInCategory.OST_Walls)

if not walls:
    forms.alert("Nenhuma parede selecionada.", exitscript=True)

# =============================================================================
# FUNÇÕES AUXILIARES
# =============================================================================
# Distâncias para fora do limite externo das paredes selecionadas (em pés)
OFFSET_CADEIA_EXT = 0.40 / 0.3048  # 0,40 m para fora da última parede
OFFSET_TOTAL_EXT  = 0.80 / 0.3048  # 0,80 m para fora (linha de cota total)


def get_face_point(face):
    try:
        return face.Origin
    except:
        bbox = face.GetBoundingBox()
        uv_mid = UV((bbox.Min.U + bbox.Max.U) * 0.5, (bbox.Min.V + bbox.Max.V) * 0.5)
        return face.Evaluate(uv_mid)


def group_walls_by_direction(wall_list):
    """
    Agrupa paredes paralelas entre si, padronizando o sentido do vetor diretor
    (X > 0 ou Y > 0) para posicionar as cotas consistentemente para fora.
    """
    groups = []
    for wall in wall_list:
        try:
            curve = wall.Location.Curve
            if not isinstance(curve, Line):
                continue
            d1 = XYZ(curve.Direction.X, curve.Direction.Y, 0).Normalize()
            if d1.X < -1e-4 or (abs(d1.X) <= 1e-4 and d1.Y < -1e-4):
                d1 = XYZ(-d1.X, -d1.Y, 0)
        except:
            continue

        placed = False
        for g_dir, g_list in groups:
            if d1.CrossProduct(g_dir).GetLength() < 1e-3:
                g_list.append(wall)
                placed = True
                break

        if not placed:
            groups.append((d1, [wall]))
    return groups


# Coletar todos os pontos extremos das paredes selecionadas para determinar o perímetro externo
all_pts = []
for w in walls:
    try:
        c = w.Location.Curve
        if isinstance(c, Line):
            all_pts.append(c.GetEndPoint(0))
            all_pts.append(c.GetEndPoint(1))
    except:
        pass

if not all_pts:
    forms.alert("Nenhuma parede linear válida encontrada na seleção.", exitscript=True)

z_view = view.GenLevel.Elevation if view.GenLevel else 0.0
global_center = XYZ(
    sum(p.X for p in all_pts) / len(all_pts),
    sum(p.Y for p in all_pts) / len(all_pts),
    z_view
)

# =============================================================================
# ETAPA 2 — Criar Cotas Externas (Espessuras + Vãos + Total) por Direção
# =============================================================================
cotas_criadas = 0
erros = 0

dir_groups = group_walls_by_direction(walls)

with Transaction(doc, "Cotar Paredes Externamente (Espessuras e Vãos)") as t:
    t.Start()

    for wall_dir, group_walls in dir_groups:
        try:
            # Direção da linha de cota é perpendicular às paredes do grupo
            dim_dir = XYZ(-wall_dir.Y, wall_dir.X, 0).Normalize()

            # Coletar todas as faces paralelas de todas as paredes do grupo,
            # eliminando faces coplanares duplicadas (mesma coordenada ao longo de dim_dir)
            stations = {}  # chave arredondada -> (area, face, pos_real)

            for w in group_walls:
                wg = WallGeometry(w)
                for face in wg.get_parallel_faces():
                    if face.Reference is None:
                        continue
                    f_pt = get_face_point(face)
                    pos_perp = (f_pt - global_center).DotProduct(dim_dir)
                    # Tolerância de ~3 mm (0.01 pés) para faces exatamente alinhadas/colineares
                    key = round(pos_perp, 2)
                    if key not in stations or face.Area > stations[key][0]:
                        stations[key] = (face.Area, face, pos_perp)

            if len(stations) < 2:
                continue

            ordered_faces = [item[1] for item in sorted(stations.values(), key=lambda x: x[2])]

            # Determinar o limite externo ao longo de wall_dir (lado "de fora" das paredes)
            projs_along_wall = [(pt - global_center).DotProduct(wall_dir) for pt in all_pts]
            s_min = min(projs_along_wall)
            s_max = max(projs_along_wall)

            # Para paredes verticais (wall_dir ~ Y+), jogar para cima (s_max + offset);
            # Para paredes horizontais (wall_dir ~ X+), jogar para a esquerda (s_min - offset)
            if abs(wall_dir.Y) >= abs(wall_dir.X):
                s_cadeia = s_max + OFFSET_CADEIA_EXT
                s_total  = s_max + OFFSET_TOTAL_EXT
            else:
                s_cadeia = s_min - OFFSET_CADEIA_EXT
                s_total  = s_min - OFFSET_TOTAL_EXT

            anchor_cadeia = XYZ(
                global_center.X + wall_dir.X * s_cadeia,
                global_center.Y + wall_dir.Y * s_cadeia,
                z_view
            )

            # 1) Linha de Cota em Cadeia (todas as espessuras + todos os vãos)
            ref_cadeia = ReferenceArray()
            for f in ordered_faces:
                ref_cadeia.Append(f.Reference)

            p1 = XYZ(anchor_cadeia.X - dim_dir.X * 50.0, anchor_cadeia.Y - dim_dir.Y * 50.0, z_view)
            p2 = XYZ(anchor_cadeia.X + dim_dir.X * 50.0, anchor_cadeia.Y + dim_dir.Y * 50.0, z_view)
            line_cadeia = Line.CreateBound(p1, p2)

            cota_c = doc.Create.NewDimension(view, line_cadeia, ref_cadeia)
            if cota_c:
                cotas_criadas += 1

            # 2) Linha de Cota Total Externa (da primeira face externa até a última face externa)
            if len(ordered_faces) > 2:
                anchor_total = XYZ(
                    global_center.X + wall_dir.X * s_total,
                    global_center.Y + wall_dir.Y * s_total,
                    z_view
                )
                ref_total = ReferenceArray()
                ref_total.Append(ordered_faces[0].Reference)
                ref_total.Append(ordered_faces[-1].Reference)

                pt1 = XYZ(anchor_total.X - dim_dir.X * 50.0, anchor_total.Y - dim_dir.Y * 50.0, z_view)
                pt2 = XYZ(anchor_total.X + dim_dir.X * 50.0, anchor_total.Y + dim_dir.Y * 50.0, z_view)
                line_total = Line.CreateBound(pt1, pt2)

                cota_t = doc.Create.NewDimension(view, line_total, ref_total)
                if cota_t:
                    cotas_criadas += 1

        except Exception as e_grp:
            erros += 1
            print("Erro ao cotar grupo de paredes: {}".format(str(e_grp)))

    t.Commit()

forms.alert(
    "Concluído!\n\n"
    "✔ Direções cotadas: {}\n"
    "✔ Total de linhas de cota criadas: {}\n"
    "✘ Erros: {}".format(len(dir_groups), cotas_criadas, erros),
    title="Cotar Paredes Selecionadas",
    warn_icon=False
)
