# -*- coding: utf-8 -*-
__title__ = "Cotar Parede\ne Aberturas"
__author__ = "AutoBIM"
__version__ = "Versão 1.0"
__doc__ = """Posiciona cotas longitudinais e de aberturas nas paredes selecionadas na planta ativa.

Vãos e Recortes:
Posiciona uma cadeia de 2 níveis ao longo da parede: o primeiro nível mede bonecas e vãos luz de portas e janelas, e o segundo nível mede o comprimento total da parede.

Eixos de Aberturas:
Mede a distância das extremidades da parede até os eixos centrais de portas e janelas inseridas, além da cota total.

Comprimento Total:
Posiciona apenas a cota de ponta a ponta em cada parede selecionada."""

import clr
clr.AddReference("System")

from Autodesk.Revit.DB import *
from pyrevit import forms, revit, script

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
# ETAPA 1 — Escolher sub-modo de cotagem longitudinal
# =============================================================================
MODO_VAOS  = "1. Vãos de Aberturas (Bonecas + Vão Luz) + Total"
MODO_EIXOS = "2. Eixos de Portas/Janelas + Total"
MODO_TOTAL = "3. Apenas Comprimento Total da Parede"

modo = forms.CommandSwitchWindow.show(
    [MODO_VAOS, MODO_EIXOS, MODO_TOTAL],
    message="Escolha o que deseja cotar ao longo das paredes:"
)

if not modo:
    script.exit()

# =============================================================================
# ETAPA 2 — Selecionar Paredes
# =============================================================================
with forms.WarningBar(title="Selecione as Paredes e clique em Finish"):
    walls = revit.pick_elements_by_category(BuiltInCategory.OST_Walls)

if not walls:
    forms.alert("Nenhuma parede selecionada.", exitscript=True)


# =============================================================================
# FUNÇÕES AUXILIARES
# =============================================================================
def get_face_point(face):
    try:
        return face.Origin
    except:
        bbox = face.GetBoundingBox()
        uv_mid = UV((bbox.Min.U + bbox.Max.U) * 0.5, (bbox.Min.V + bbox.Max.V) * 0.5)
        return face.Evaluate(uv_mid)


def get_perpendicular_wall_faces(wall, wall_dir, p0):
    """
    Retorna as faces verticais da parede que são perpendiculares ao eixo da parede
    (extremidades da parede e faces laterais/ombreiras de aberturas de portas e janelas),
    ordenadas ao longo da direção da parede e sem duplicatas na mesma posição longitudinal.
    """
    opt = Options()
    opt.ComputeReferences = True
    opt.DetailLevel = ViewDetailLevel.Coarse
    geom = wall.get_Geometry(opt)

    stations = {}  # chave: posição longitudinal arredondada -> (area, face, pos_real)

    for obj in geom:
        solids = [obj] if isinstance(obj, Solid) else []
        if isinstance(obj, GeometryInstance):
            solids.extend([g for g in obj.GetInstanceGeometry() if isinstance(g, Solid)])

        for solid in solids:
            if solid.Volume <= 0:
                continue
            for face in solid.Faces:
                if face.Reference is None:
                    continue
                try:
                    normal = face.FaceNormal
                    # Face vertical cuja normal é paralela à direção longitudinal da parede
                    if abs(normal.Z) < 1e-4 and abs(abs(normal.DotProduct(wall_dir)) - 1.0) < 1e-3:
                        f_pt = get_face_point(face)
                        pos = (f_pt - p0).DotProduct(wall_dir)
                        # Agrupar faces coplanares (tolerância de ~3 mm = 0.01 pés)
                        station_key = round(pos, 2)
                        if station_key not in stations or face.Area > stations[station_key][0]:
                            stations[station_key] = (face.Area, face, pos)
                except:
                    pass

    ordered = sorted(stations.values(), key=lambda item: item[2])
    return [item[1] for item in ordered]


def get_insert_center_refs(wall):
    """
    Retorna as referências de eixo central (CenterLeftRight) das portas e janelas
    hospedadas na parede.
    """
    center_refs = []
    try:
        insert_ids = wall.FindInserts(True, False, False, False)
        for ins_id in insert_ids:
            inst = doc.GetElement(ins_id)
            if isinstance(inst, FamilyInstance):
                try:
                    refs = inst.GetReferences(FamilyInstanceReferenceType.CenterLeftRight)
                    if refs and refs.Count > 0:
                        center_refs.append(refs[0])
                except:
                    pass
    except:
        pass
    return center_refs


# =============================================================================
# ETAPA 3 — Criar Cotas Longitudinais
# =============================================================================
cotas_criadas = 0
erros = 0
z_view = view.GenLevel.Elevation if view.GenLevel else 0.0

# Distâncias de afastamento da linha de cota em relação ao eixo da parede (em pés)
OFFSET_CADEIA = (0.55 / 0.3048)  # 55 cm
OFFSET_TOTAL  = (0.90 / 0.3048)  # 90 cm

with Transaction(doc, "Cotar Parede e Aberturas") as t:
    t.Start()

    for wall in walls:
        try:
            curve = wall.Location.Curve
            if not isinstance(curve, Line):
                continue

            p0 = curve.GetEndPoint(0)
            p1 = curve.GetEndPoint(1)
            wall_dir = XYZ(curve.Direction.X, curve.Direction.Y, 0).Normalize()

            # Direção perpendicular para afastar a linha de cota da parede
            try:
                out_dir = XYZ(wall.Orientation.X, wall.Orientation.Y, 0).Normalize()
            except:
                out_dir = XYZ(-wall_dir.Y, wall_dir.X, 0).Normalize()

            perp_faces = get_perpendicular_wall_faces(wall, wall_dir, p0)
            if len(perp_faces) < 2:
                continue

            first_face = perp_faces[0]
            last_face  = perp_faces[-1]

            # 1) Cota em cadeia (vãos/bonecas ou eixos)
            if modo == MODO_VAOS and len(perp_faces) > 2:
                refs_vaos = ReferenceArray()
                for f in perp_faces:
                    refs_vaos.Append(f.Reference)

                pt_a = XYZ(p0.X + out_dir.X * OFFSET_CADEIA, p0.Y + out_dir.Y * OFFSET_CADEIA, z_view)
                pt_b = XYZ(p1.X + out_dir.X * OFFSET_CADEIA, p1.Y + out_dir.Y * OFFSET_CADEIA, z_view)
                line_vaos = Line.CreateBound(pt_a, pt_b)

                cota_v = doc.Create.NewDimension(view, line_vaos, refs_vaos)
                if cota_v:
                    cotas_criadas += 1

            elif modo == MODO_EIXOS:
                center_refs = get_insert_center_refs(wall)
                if center_refs:
                    refs_eixos = ReferenceArray()
                    refs_eixos.Append(first_face.Reference)
                    for c_ref in center_refs:
                        refs_eixos.Append(c_ref)
                    refs_eixos.Append(last_face.Reference)

                    pt_a = XYZ(p0.X + out_dir.X * OFFSET_CADEIA, p0.Y + out_dir.Y * OFFSET_CADEIA, z_view)
                    pt_b = XYZ(p1.X + out_dir.X * OFFSET_CADEIA, p1.Y + out_dir.Y * OFFSET_CADEIA, z_view)
                    line_eixos = Line.CreateBound(pt_a, pt_b)

                    cota_e = doc.Create.NewDimension(view, line_eixos, refs_eixos)
                    if cota_e:
                        cotas_criadas += 1

            # 2) Cota do comprimento total da parede
            refs_total = ReferenceArray()
            refs_total.Append(first_face.Reference)
            refs_total.Append(last_face.Reference)

            offset_usado = OFFSET_CADEIA if (modo == MODO_TOTAL or len(perp_faces) == 2) else OFFSET_TOTAL
            pt_t1 = XYZ(p0.X + out_dir.X * offset_usado, p0.Y + out_dir.Y * offset_usado, z_view)
            pt_t2 = XYZ(p1.X + out_dir.X * offset_usado, p1.Y + out_dir.Y * offset_usado, z_view)
            line_total = Line.CreateBound(pt_t1, pt_t2)

            cota_t = doc.Create.NewDimension(view, line_total, refs_total)
            if cota_t:
                cotas_criadas += 1

        except Exception as e:
            erros += 1
            print("Erro na parede {}: {}".format(wall.Id, str(e)))

    t.Commit()

forms.alert(
    "Concluído!\n\n"
    "✔ Cotas criadas: {}\n"
    "✘ Paredes com erro: {}".format(cotas_criadas, erros),
    title="Cotar Parede e Aberturas",
    warn_icon=False
)
