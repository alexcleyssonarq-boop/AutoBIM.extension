# -*- coding: utf-8 -*-
__title__ = "Cotar por\nAmbiente"
__author__ = "AutoBIM"
__version__ = "Versão 1.1"
__doc__ = """Posiciona cotas internas automaticamente nos ambientes selecionados na planta ativa.

Interior do Ambiente:
Identifica as paredes que delimitam cada ambiente selecionado e cria duas cadeias de cotas (largura e comprimento) posicionadas próximas às paredes laterais, deixando o centro do ambiente livre para tags e mobiliário.

As cotas são medidas diretamente nas faces internas de acabamento das paredes voltadas para o ambiente."""

import clr
import math
clr.AddReference("System")

from Autodesk.Revit.DB import *
from pyrevit import forms, revit, script
from Snippets._walls import WallGeometry, filter_collinear_walls

doc   = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument
view  = doc.ActiveView

# =============================================================================
# ETAPA 1 — Validar vista ativa
# =============================================================================
if not isinstance(view, ViewPlan):
    forms.alert(
        "Execute este comando em uma vista de Planta de Piso.",
        title="Vista Inválida",
        exitscript=True
    )

# =============================================================================
# ETAPA 2 — Selecionar Ambientes
# =============================================================================
with forms.WarningBar(title="Selecione os Ambientes para cotar e clique em Finish"):
    rooms = revit.pick_elements_by_category(BuiltInCategory.OST_Rooms)

if not rooms:
    forms.alert("Nenhum ambiente selecionado.", exitscript=True)

# =============================================================================
# FUNÇÕES AUXILIARES
# =============================================================================
# Distância padrão da linha de cota até a face interna da parede lateral (45 cm em pés)
OFFSET_LATERAL_PADRAO = 0.45 / 0.3048


def get_face_point(face):
    """Retorna um ponto 3D pertencente à face planar."""
    try:
        return face.Origin
    except:
        bbox = face.GetBoundingBox()
        uv_mid = UV((bbox.Min.U + bbox.Max.U) * 0.5, (bbox.Min.V + bbox.Max.V) * 0.5)
        return face.Evaluate(uv_mid)


def get_inner_face(wall, ref_point):
    """
    Retorna a face paralela da parede que está mais próxima do ponto de referência
    interno do ambiente (face interna voltada para o ambiente).
    """
    wg = WallGeometry(wall)
    faces = wg.get_parallel_faces()
    if not faces:
        return None

    best_face = None
    min_dist = float("inf")

    wall_dir = wg.get_wall_direction().Normalize()
    normal_xy = XYZ(-wall_dir.Y, wall_dir.X, 0).Normalize()

    for face in faces:
        if face.Reference is None:
            continue
        f_pt = get_face_point(face)
        vec = XYZ(ref_point.X - f_pt.X, ref_point.Y - f_pt.Y, 0)
        dist_perp = abs(vec.DotProduct(normal_xy))
        if dist_perp < min_dist:
            min_dist = dist_perp
            best_face = face

    return best_face


def get_room_center(room):
    """Obtém o ponto central do ambiente."""
    if room.Location and hasattr(room.Location, "Point") and room.Location.Point:
        return room.Location.Point
    bbox = room.get_BoundingBox(None)
    if bbox:
        return (bbox.Min + bbox.Max) * 0.5
    return None


def group_walls_by_direction(walls):
    """
    Agrupa paredes paralelas entre si, normalizando o vetor diretor para apontar
    sempre no sentido positivo (X > 0 ou Y > 0) para posicionamento consistente.
    """
    groups = []
    for wall in walls:
        try:
            curve = wall.Location.Curve
            if not isinstance(curve, Line):
                continue
            d1 = XYZ(curve.Direction.X, curve.Direction.Y, 0).Normalize()
            # Padronizar sentido do vetor diretor (para que o offset fique sempre no mesmo lado: inferior/esquerdo)
            if d1.X < -1e-4 or (abs(d1.X) <= 1e-4 and d1.Y < -1e-4):
                d1 = XYZ(-d1.X, -d1.Y, 0)
        except:
            continue

        placed = False
        for group_dir, group_list in groups:
            if d1.CrossProduct(group_dir).GetLength() < 1e-3:
                group_list.append(wall)
                placed = True
                break

        if not placed:
            groups.append((d1, [wall]))

    return groups


def compute_lateral_anchor(center, wall_dir, wall_segments_map, unique_walls, all_boundary_pts):
    """
    Calcula o ponto âncora para posicionar a linha de cota próxima à parede lateral
    (a ~45 cm da face interna da parede lateral, ou 22% do vão em ambientes pequenos).
    """
    # Coletar intervalos longitudinais (ao longo de wall_dir) de cada parede do grupo no contorno do ambiente
    intervals = []
    for w in unique_walls:
        segs = wall_segments_map.get(str(w.Id), [])
        if segs:
            s_vals = []
            for seg in segs:
                c = seg.GetCurve()
                s_vals.append((c.GetEndPoint(0) - center).DotProduct(wall_dir))
                s_vals.append((c.GetEndPoint(1) - center).DotProduct(wall_dir))
            intervals.append((min(s_vals), max(s_vals)))

    # Verificar se há faixa longitudinal comum entre as paredes opostas
    if len(intervals) >= 2:
        overlap_min = max(iv[0] for iv in intervals)
        overlap_max = min(iv[1] for iv in intervals)
        if overlap_max > overlap_min + 0.5:
            span = overlap_max - overlap_min
            offset = min(OFFSET_LATERAL_PADRAO, span * 0.22)
            s_target = overlap_min + offset
            return XYZ(
                center.X + wall_dir.X * s_target,
                center.Y + wall_dir.Y * s_target,
                center.Z
            )

    # Fallback: usar os limites gerais do contorno interno do ambiente ao longo de wall_dir
    if all_boundary_pts:
        projs = [(pt - center).DotProduct(wall_dir) for pt in all_boundary_pts]
        s_min = min(projs)
        s_max = max(projs)
        span = s_max - s_min
        if span > 0.5:
            offset = min(OFFSET_LATERAL_PADRAO, span * 0.22)
            s_target = s_min + offset
            return XYZ(
                center.X + wall_dir.X * s_target,
                center.Y + wall_dir.Y * s_target,
                center.Z
            )

    return center


# =============================================================================
# ETAPA 3 — Processar Ambientes e Criar Cotas nas Laterais
# =============================================================================
boundary_opts = SpatialElementBoundaryOptions()
boundary_opts.SpatialElementBoundaryLocation = SpatialElementBoundaryLocation.Finish

cotas_criadas = 0
ambientes_processados = 0
erros = 0

with Transaction(doc, "Cotar Paredes por Ambiente") as t:
    t.Start()

    for room in rooms:
        try:
            center = get_room_center(room)
            if not center:
                continue

            seg_lists = room.GetBoundarySegments(boundary_opts)
            if not seg_lists or seg_lists.Count == 0:
                continue

            # Coletar paredes únicas e mapear segmentos de contorno por parede
            walls_dict = {}
            wall_segments_map = {}
            all_boundary_pts = []

            for seg_list in seg_lists:
                for seg in seg_list:
                    c = seg.GetCurve()
                    all_boundary_pts.append(c.GetEndPoint(0))
                    all_boundary_pts.append(c.GetEndPoint(1))

                    el_id = seg.ElementId
                    if el_id and el_id != ElementId.InvalidElementId:
                        el = doc.GetElement(el_id)
                        if isinstance(el, Wall):
                            key = str(el.Id)
                            walls_dict[key] = el
                            if key not in wall_segments_map:
                                wall_segments_map[key] = []
                            wall_segments_map[key].append(seg)

            room_walls = list(walls_dict.values())
            if len(room_walls) < 2:
                continue

            # Agrupar paredes por direção (ex.: horizontais e verticais)
            dir_groups = group_walls_by_direction(room_walls)
            criou_no_ambiente = False

            for wall_dir, group_walls in dir_groups:
                unique_walls = filter_collinear_walls(group_walls)
                if len(unique_walls) < 2:
                    continue

                # Calcular ponto âncora próximo à parede lateral interna
                anchor_pt = compute_lateral_anchor(
                    center, wall_dir, wall_segments_map, unique_walls, all_boundary_pts
                )

                ref_array = ReferenceArray()
                proj_coords = []

                # Direção da linha de cota é perpendicular à direção das paredes
                dim_dir = XYZ(-wall_dir.Y, wall_dir.X, 0).Normalize()

                for w in unique_walls:
                    inner_face = get_inner_face(w, anchor_pt)
                    if inner_face and inner_face.Reference:
                        f_pt = get_face_point(inner_face)
                        proj = round((f_pt - anchor_pt).DotProduct(dim_dir), 3)
                        if proj not in proj_coords:
                            proj_coords.append(proj)
                            ref_array.Append(inner_face.Reference)

                if ref_array.Size >= 2:
                    p1 = XYZ(anchor_pt.X - dim_dir.X * 10.0, anchor_pt.Y - dim_dir.Y * 10.0, center.Z)
                    p2 = XYZ(anchor_pt.X + dim_dir.X * 10.0, anchor_pt.Y + dim_dir.Y * 10.0, center.Z)
                    dim_line = Line.CreateBound(p1, p2)

                    nova_cota = doc.Create.NewDimension(view, dim_line, ref_array)
                    if nova_cota:
                        cotas_criadas += 1
                        criou_no_ambiente = True

            if criou_no_ambiente:
                ambientes_processados += 1

        except Exception as e_room:
            erros += 1
            print("Erro ao cotar ambiente: {}".format(str(e_room)))

    t.Commit()

forms.alert(
    "Concluído!\n\n"
    "✔ Ambientes cotados: {}\n"
    "✔ Total de cotas criadas: {}\n"
    "✘ Ambientes com erro: {}".format(ambientes_processados, cotas_criadas, erros),
    title="Cotar por Ambiente",
    warn_icon=False
)
