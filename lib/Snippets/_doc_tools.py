# -*- coding: utf-8 -*-
"""Ferramentas do Painel Documentacao do AutoBIM (Quadro de Acabamentos, Renumeracao por Clique, Tag Automatica)."""

import os
import re
import json
import math
import codecs
import clr
clr.AddReference("System")
clr.AddReference("System.Data")
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")

from System.Collections.Generic import List
from System.Data import DataTable
from System.Windows import Visibility as WpfVisibility
from Autodesk.Revit.DB import *
from Autodesk.Revit.DB.Architecture import Room, RoomTag
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
from pyrevit import forms, script

doc   = None
uidoc = None
view  = None

FT = 1.0 / 0.3048
MM = 1.0 / 304.8
SETTINGS_PATH = os.path.join(os.path.dirname(__file__), "_doc_tools_settings.json")


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


def _elem_name(el):
    if el is None:
        return u""
    try:
        n = Element.Name.GetValue(el)
        if n:
            return n
    except Exception:
        pass
    for bip in (BuiltInParameter.SYMBOL_NAME_PARAM, BuiltInParameter.ALL_MODEL_TYPE_NAME):
        try:
            p = el.get_Parameter(bip)
            if p is not None and p.AsString():
                return p.AsString()
        except Exception:
            pass
    try:
        return unicode(el.Name)
    except Exception:
        try:
            return str(el.Name)
        except Exception:
            return u""


def _mat_name(mat_id):
    if mat_id is None or mat_id == ElementId.InvalidElementId:
        return u""
    m = doc.GetElement(mat_id)
    if m is None:
        return u""
    return _elem_name(m)


def _room_name(room):
    try:
        p = room.get_Parameter(BuiltInParameter.ROOM_NAME)
        if p is not None and p.AsString():
            return p.AsString()
    except Exception:
        pass
    return _elem_name(room)


def _room_number(room):
    try:
        p = room.get_Parameter(BuiltInParameter.ROOM_NUMBER)
        if p is not None and p.AsString():
            return p.AsString()
    except Exception:
        pass
    return u""


# ==============================================================================
# 1. QUADRO DE ACABAMENTOS AUTOMATICO + LEGENDA NUMERADA (○ △ □)
# ==============================================================================

XAML_QUADRO = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Quadro de Acabamentos e Legenda Automática" Width="540" SizeToContent="Height" MaxHeight="760"
        WindowStartupLocation="CenterScreen" ResizeMode="NoResize" Background="#F7F8FA">
    <ScrollViewer VerticalScrollBarVisibility="Auto">
        <StackPanel Margin="14">
            <Border Background="#EBF3FC" BorderBrush="#B8D4F5" BorderThickness="1" CornerRadius="4" Padding="9" Margin="0,0,0,10">
                <TextBlock TextWrapping="Wrap" FontWeight="SemiBold" Foreground="#1F3A57"
                           Text="Detecta automaticamente Piso (○), Parede/Revestimento (△) e Forro (□) de cada ambiente, gera códigos numerados, preenche os parâmetros nativos da Tag e cria a Legenda Gráfica e Tabela."/>
            </Border>

            <Border Background="White" BorderBrush="#D0D7DE" BorderThickness="1" CornerRadius="4" Padding="10" Margin="0,0,0,8">
                <Grid>
                    <Grid.ColumnDefinitions>
                        <ColumnDefinition Width="210"/>
                        <ColumnDefinition Width="*"/>
                    </Grid.ColumnDefinitions>
                    <Grid.RowDefinitions>
                        <RowDefinition Height="28"/>
                        <RowDefinition Height="28"/>
                        <RowDefinition Height="28"/>
                        <RowDefinition Height="28"/>
                    </Grid.RowDefinitions>

                    <TextBlock Grid.Row="0" Grid.Column="0" Text="Ambientes a processar:" VerticalAlignment="Center"/>
                    <ComboBox  Grid.Row="0" Grid.Column="1" x:Name="cb_escopo" Height="24"/>

                    <TextBlock Grid.Row="1" Grid.Column="0" Text="Texto da descrição:" VerticalAlignment="Center"/>
                    <ComboBox  Grid.Row="1" Grid.Column="1" x:Name="cb_desc" Height="24"/>

                    <TextBlock Grid.Row="2" Grid.Column="0" Text="Sistema de numeração na Tag:" VerticalAlignment="Center"/>
                    <ComboBox  Grid.Row="2" Grid.Column="1" x:Name="cb_num_modo" Height="24"/>

                    <TextBlock Grid.Row="3" Grid.Column="0" Text="Múltiplos revest. de parede:" VerticalAlignment="Center"/>
                    <ComboBox  Grid.Row="3" Grid.Column="1" x:Name="cb_parede_modo" Height="24"/>
                </Grid>
            </Border>

            <Border Background="White" BorderBrush="#D0D7DE" BorderThickness="1" CornerRadius="4" Padding="10" Margin="0,0,0,10">
                <StackPanel>
                    <TextBlock Text="Saídas de Documentação:" FontWeight="SemiBold" Margin="0,0,0,6"/>
                    <CheckBox x:Name="ck_gravar_tag" Content="Preencher parâmetros nativos da Tag (Acabamento do piso / parede / forro)" IsChecked="True" Margin="0,0,0,4"/>
                    <CheckBox x:Name="ck_rodape"     Content="Detectar Rodapé e gravar em 'Acabamento base'" IsChecked="True" Margin="0,0,0,4"/>
                    <CheckBox x:Name="ck_legenda"    Content="Gerar / Atualizar Legenda Gráfica (Vista de Desenho com símbolos ○ △ □)" IsChecked="True" Margin="0,0,0,4"/>
                    <CheckBox x:Name="ck_tabela"     Content="Gerar / Atualizar Tabela de Acabamentos (ViewSchedule)" IsChecked="True" Margin="0,0,0,4"/>
                    <CheckBox x:Name="ck_abrir_leg"  Content="Abrir a Legenda Gráfica ao concluir" IsChecked="True"/>
                </StackPanel>
            </Border>

            <StackPanel Orientation="Horizontal" HorizontalAlignment="Right">
                <Button x:Name="btn_cancel" Content="Cancelar" Width="95" Height="30" Margin="0,0,8,0"/>
                <Button x:Name="btn_ok" Content="Gerar Quadro e Legenda" Width="185" Height="30"
                        IsDefault="True" Background="#0078D4" Foreground="White" FontWeight="SemiBold"/>
            </StackPanel>
        </StackPanel>
    </ScrollViewer>
</Window>
"""


class QuadroAcabamentosWindow(forms.WPFWindow):
    def __init__(self, prefs):
        forms.WPFWindow.__init__(self, XAML_QUADRO, literal_string=True)
        self.confirmado = False
        self.resultado  = {}

        self._cb_escopo  = self.FindName("cb_escopo")
        self._cb_desc    = self.FindName("cb_desc")
        self._cb_num     = self.FindName("cb_num_modo")
        self._cb_par     = self.FindName("cb_parede_modo")
        self._ck_tag     = self.FindName("ck_gravar_tag")
        self._ck_rodape  = self.FindName("ck_rodape")
        self._ck_leg     = self.FindName("ck_legenda")
        self._ck_tab     = self.FindName("ck_tabela")
        self._ck_abrir   = self.FindName("ck_abrir_leg")

        self._cb_escopo.Items.Add(u"Todos os Ambientes colocados no projeto")
        self._cb_escopo.Items.Add(u"Ambientes visíveis na Vista Atual")
        self._cb_escopo.Items.Add(u"Selecionar Ambientes na vista")
        self._cb_escopo.SelectedIndex = max(0, min(2, int(prefs.get("qa_escopo", 1) or 1)))

        self._cb_desc.Items.Add(u"Material da face visível (fallback: Nome do Tipo)")
        self._cb_desc.Items.Add(u"Nome do Tipo do elemento (Floor / Wall / Ceiling Type)")
        self._cb_desc.Items.Add(u"Nome do Tipo + Material visível")
        self._cb_desc.SelectedIndex = max(0, min(2, int(prefs.get("qa_desc", 0) or 0)))

        self._cb_num.Items.Add(u"Por símbolo: 1, 2, 3... em ○ Piso, △ Parede e □ Forro")
        self._cb_num.Items.Add(u"Corrido único: 1, 2, 3... geral para todos")
        self._cb_num.SelectedIndex = max(0, min(1, int(prefs.get("qa_num_modo", 0) or 0)))

        self._cb_par.Items.Add(u"Listar todos os acabamentos da sala (ex: 1 / 2)")
        self._cb_par.Items.Add(u"Apenas o predominante (maior comprimento)")
        self._cb_par.SelectedIndex = max(0, min(1, int(prefs.get("qa_parede_modo", 0) or 0)))

        self._ck_tag.IsChecked    = bool(prefs.get("qa_tag", True))
        self._ck_rodape.IsChecked = bool(prefs.get("qa_rodape", True))
        self._ck_leg.IsChecked    = bool(prefs.get("qa_legenda", True))
        self._ck_tab.IsChecked    = bool(prefs.get("qa_tabela", True))
        self._ck_abrir.IsChecked  = bool(prefs.get("qa_abrir", True))

        self.FindName("btn_ok").Click     += self.on_ok
        self.FindName("btn_cancel").Click += self.on_cancel

    def on_cancel(self, sender, args):
        self.confirmado = False
        self.Close()

    def on_ok(self, sender, args):
        self.resultado = {
            "qa_escopo":      max(0, self._cb_escopo.SelectedIndex),
            "qa_desc":        max(0, self._cb_desc.SelectedIndex),
            "qa_num_modo":    max(0, self._cb_num.SelectedIndex),
            "qa_parede_modo": max(0, self._cb_par.SelectedIndex),
            "qa_tag":         bool(self._ck_tag.IsChecked),
            "qa_rodape":      bool(self._ck_rodape.IsChecked),
            "qa_legenda":     bool(self._ck_leg.IsChecked),
            "qa_tabela":      bool(self._ck_tab.IsChecked),
            "qa_abrir":       bool(self._ck_abrir.IsChecked),
        }
        self.confirmado = True
        self.Close()


class _RoomPickFilter(ISelectionFilter):
    def AllowElement(self, elem):
        return isinstance(elem, Room)

    def AllowReference(self, ref, pt):
        return True


def _selecionar_ambientes(escopo_idx):
    if escopo_idx == 2:
        try:
            with forms.WarningBar(title="Selecione os Ambientes (Rooms) e clique em Finish"):
                refs = uidoc.Selection.PickObjects(ObjectType.Element, _RoomPickFilter(), "Selecione os Ambientes")
            rms = [doc.GetElement(r) for r in refs if isinstance(doc.GetElement(r), Room)]
        except Exception:
            script.exit()
    elif escopo_idx == 1:
        rms = list(FilteredElementCollector(doc, view.Id).OfCategory(BuiltInCategory.OST_Rooms).WhereElementIsNotElementType().ToElements())
    else:
        rms = list(FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Rooms).WhereElementIsNotElementType().ToElements())

    validos = []
    for r in rms:
        try:
            if getattr(r, "Area", 0.0) > 1e-4 and getattr(r, "Location", None) is not None:
                validos.append(r)
        except Exception:
            pass
    if not validos:
        forms.alert(u"Nenhum Ambiente válido (colocado e fechado) encontrado.", exitscript=True)
    return validos


def _formatar_desc_acabamento(tipo_nome, mat_nome, desc_modo):
    t = (tipo_nome or u"").strip()
    m = (mat_nome or u"").strip()
    if desc_modo == 1:
        return t if t else m
    if desc_modo == 2:
        if t and m and m.lower() not in t.lower():
            return u"{} — {}".format(t, m)
        return t if t else m
    return m if m else t


def _material_face_ou_camada(el, face_refs, layer_idx, prefer_paint=True):
    """Extrai nome do material da face (considerando pintura) ou da estrutura composta."""
    mat_nome = u""
    if face_refs:
        for ref in face_refs:
            try:
                face = el.GetGeometryObjectFromReference(ref)
                if face is not None:
                    if prefer_paint:
                        try:
                            if doc.IsPainted(el.Id, face):
                                pmat = doc.GetPaintedMaterialId(el.Id, face)
                                mn = _mat_name(pmat)
                                if mn:
                                    return mn
                        except Exception:
                            pass
                    mid = getattr(face, "MaterialElementId", ElementId.InvalidElementId)
                    mn = _mat_name(mid)
                    if mn:
                        mat_nome = mn
                        break
            except Exception:
                pass
    if not mat_nome:
        try:
            etype = doc.GetElement(el.GetTypeId())
            cs = etype.GetCompoundStructure()
            if cs is not None:
                layers = list(cs.GetLayers())
                if layers:
                    idx = 0 if layer_idx == 0 else (len(layers) - 1)
                    mat_nome = _mat_name(layers[idx].MaterialId)
        except Exception:
            pass
    return mat_nome


def _pontos_amostra_room(room):
    pts = []
    loc_pt = getattr(getattr(room, "Location", None), "Point", None)
    if loc_pt is not None:
        pts.append(loc_pt)
    bb = room.get_BoundingBox(None)
    if bb is not None:
        z_ref = loc_pt.Z if loc_pt is not None else bb.Min.Z + 0.30 * FT
        cx = 0.5 * (bb.Min.X + bb.Max.X)
        cy = 0.5 * (bb.Min.Y + bb.Max.Y)
        dx = max(0.2 * FT, 0.22 * (bb.Max.X - bb.Min.X))
        dy = max(0.2 * FT, 0.22 * (bb.Max.Y - bb.Min.Y))
        for ox, oy in ((0.0, 0.0), (dx, 0.0), (-dx, 0.0), (0.0, dy), (0.0, -dy)):
            cand = XYZ(cx + ox, cy + oy, z_ref)
            try:
                if room.IsPointInRoom(cand):
                    pts.append(cand)
            except Exception:
                pass
    return pts


def _face_contem_xy(el, refs, pt_xy, z_hint):
    for ref in refs:
        try:
            face = el.GetGeometryObjectFromReference(ref)
            if face is None:
                continue
            res = face.Project(XYZ(pt_xy.X, pt_xy.Y, z_hint))
            if res is not None:
                pr = res.XYZPoint
                if math.hypot(pr.X - pt_xy.X, pr.Y - pt_xy.Y) < 0.05 * FT:
                    return pr.Z
        except Exception:
            pass
    return None


def _detectar_piso_room(room, pts_room, floors_info, desc_modo):
    if not pts_room or not floors_info:
        return None
    z_room = pts_room[0].Z
    bb_r = room.get_BoundingBox(None)
    melhor = None
    melhor_score = float("inf")

    for finfo in floors_info:
        bb_f = finfo["bb"]
        if bb_f is None or bb_r is None:
            continue
        if bb_f.Max.X < bb_r.Min.X or bb_f.Min.X > bb_r.Max.X:
            continue
        if bb_f.Max.Y < bb_r.Min.Y or bb_f.Min.Y > bb_r.Max.Y:
            continue
        if bb_f.Max.Z < z_room - 0.50 * FT or bb_f.Min.Z > z_room + 0.50 * FT:
            continue

        top_refs = finfo["top_refs"]
        for pt in pts_room:
            if pt.X < bb_f.Min.X - 0.05 or pt.X > bb_f.Max.X + 0.05:
                continue
            if pt.Y < bb_f.Min.Y - 0.05 or pt.Y > bb_f.Max.Y + 0.05:
                continue
            z_hit = _face_contem_xy(finfo["el"], top_refs, pt, bb_f.Max.Z)
            if z_hit is not None:
                dz = abs(z_hit - z_room)
                if dz < 0.45 * FT:
                    # Prefere pisos mais altos (camada de acabamento sobre laje) próximos ao nível
                    score = dz - 0.02 * (z_hit - z_room)
                    if score < melhor_score:
                        melhor_score = score
                        melhor = finfo
                    break

    if melhor is None:
        return None
    el = melhor["el"]
    t_nome = _elem_name(doc.GetElement(el.GetTypeId()))
    m_nome = _material_face_ou_camada(el, melhor["top_refs"], 0)
    desc = _formatar_desc_acabamento(t_nome, m_nome, desc_modo)
    return desc if desc else None


def _detectar_forro_room(room, pts_room, ceilings_info, desc_modo):
    if not pts_room or not ceilings_info:
        return None
    z_room = pts_room[0].Z
    bb_r = room.get_BoundingBox(None)
    melhor = None
    melhor_z = float("inf")

    for cinfo in ceilings_info:
        bb_c = cinfo["bb"]
        if bb_c is None or bb_r is None:
            continue
        if bb_c.Max.X < bb_r.Min.X or bb_c.Min.X > bb_r.Max.X:
            continue
        if bb_c.Max.Y < bb_r.Min.Y or bb_c.Min.Y > bb_r.Max.Y:
            continue
        if bb_c.Max.Z < z_room + 1.40 * FT or bb_c.Min.Z > z_room + 6.50 * FT:
            continue

        bot_refs = cinfo["bot_refs"]
        for pt in pts_room:
            if pt.X < bb_c.Min.X - 0.05 or pt.X > bb_c.Max.X + 0.05:
                continue
            if pt.Y < bb_c.Min.Y - 0.05 or pt.Y > bb_c.Max.Y + 0.05:
                continue
            z_hit = _face_contem_xy(cinfo["el"], bot_refs, pt, bb_c.Min.Z)
            if z_hit is not None and z_room + 1.40 * FT <= z_hit <= z_room + 6.50 * FT:
                if z_hit < melhor_z:
                    melhor_z = z_hit
                    melhor = cinfo
                break

    if melhor is None:
        return None
    el = melhor["el"]
    t_nome = _elem_name(doc.GetElement(el.GetTypeId()))
    m_nome = _material_face_ou_camada(el, melhor["bot_refs"], -1)
    desc = _formatar_desc_acabamento(t_nome, m_nome, desc_modo)
    return desc if desc else None


def _parede_altura_ft(wall):
    bb = wall.get_BoundingBox(None)
    if bb is not None:
        return bb.Max.Z - bb.Min.Z
    try:
        p = wall.get_Parameter(BuiltInParameter.WALL_USER_HEIGHT_PARAM)
        if p is not None:
            return p.AsDouble()
    except Exception:
        pass
    return 0.0


def _normal_interna_seg(room, p_mid, t_xy, z_room):
    n1 = XYZ(-t_xy.Y, t_xy.X, 0.0)
    n2 = XYZ(t_xy.Y, -t_xy.X, 0.0)
    for n in (n1, n2):
        for dist in (0.15 * FT, 0.05 * FT):
            try:
                if room.IsPointInRoom(XYZ(p_mid.X + n.X * dist, p_mid.Y + n.Y * dist, z_room)):
                    return n
            except Exception:
                pass
    return n1


def _acabamento_parede_face_interna(wall, inward_n, desc_modo):
    if wall is None or not isinstance(wall, Wall):
        return None
    t_nome = _elem_name(doc.GetElement(wall.GetTypeId()))

    # Identifica lado voltado para dentro da sala usando Wall.Orientation (Exterior normal)
    try:
        orient = wall.Orientation
        ext_norm = XYZ(orient.X, orient.Y, 0.0)
        dot_ext = ext_norm.DotProduct(inward_n)
    except Exception:
        dot_ext = -1.0

    shell = ShellLayerType.Exterior if dot_ext > 0.0 else ShellLayerType.Interior
    layer_idx = 0 if dot_ext > 0.0 else -1

    face_refs = []
    try:
        face_refs = list(HostObjectUtils.GetSideFaces(wall, shell))
    except Exception:
        pass

    m_nome = _material_face_ou_camada(wall, face_refs, layer_idx)
    desc = _formatar_desc_acabamento(t_nome, m_nome, desc_modo)
    return desc if desc else None


def _detectar_paredes_rodape_room(room, pts_room, thin_walls_info, desc_modo, wall_mode_idx):
    """Detecta revestimentos de parede (priorizando paredes finas sobrepostas não-RoomBounding) e rodapés."""
    z_room = pts_room[0].Z if pts_room else 0.0
    opts = SpatialElementBoundaryOptions()
    opts.SpatialElementBoundaryLocation = SpatialElementBoundaryLocation.Finish
    try:
        loops = room.GetBoundarySegments(opts)
    except Exception:
        return None, None
    if not loops:
        return None, None

    comp_por_parede = {}
    comp_por_rodape = {}

    for seg_list in loops:
        for seg in seg_list:
            try:
                crv = seg.GetCurve()
                slen = crv.Length
                if slen < 0.05 * FT:
                    continue
                p0 = crv.GetEndPoint(0)
                p1 = crv.GetEndPoint(1)
                t_vec = XYZ(p1.X - p0.X, p1.Y - p0.Y, 0.0)
                if t_vec.GetLength() < 1e-6:
                    continue
                t_xy = t_vec.Normalize()
                p_mid = crv.Evaluate(0.5, True)
                inward_n = _normal_interna_seg(room, p_mid, t_xy, z_room)

                # Procura paredes finas (revestimento ou rodapé gerados sem RoomBounding) rente ao segmento
                rev_wall = None
                rod_wall = None
                best_d_rev = 0.14 * FT
                best_d_rod = 0.14 * FT

                for winfo in thin_walls_info:
                    bb_w = winfo["bb"]
                    if bb_w is None:
                        continue
                    if p_mid.X < bb_w.Min.X - 0.15 * FT or p_mid.X > bb_w.Max.X + 0.15 * FT:
                        continue
                    if p_mid.Y < bb_w.Min.Y - 0.15 * FT or p_mid.Y > bb_w.Max.Y + 0.15 * FT:
                        continue
                    if bb_w.Max.Z < z_room + 0.03 * FT or bb_w.Min.Z > z_room + 2.50 * FT:
                        continue

                    w_crv = winfo["crv"]
                    if w_crv is None:
                        continue
                    w_p0, w_p1 = w_crv.GetEndPoint(0), w_crv.GetEndPoint(1)
                    w_dir = XYZ(w_p1.X - w_p0.X, w_p1.Y - w_p0.Y, 0.0)
                    if w_dir.GetLength() < 1e-6:
                        continue
                    w_dir = w_dir.Normalize()
                    if abs(w_dir.DotProduct(t_xy)) < 0.90:
                        continue

                    proj = w_crv.Project(XYZ(p_mid.X, p_mid.Y, w_p0.Z))
                    if proj is None:
                        continue
                    d_xy = math.hypot(proj.XYZPoint.X - p_mid.X, proj.XYZPoint.Y - p_mid.Y)
                    if winfo["h"] <= 0.35 * FT:
                        if d_xy < best_d_rod:
                            best_d_rod = d_xy
                            rod_wall = winfo["el"]
                    else:
                        if bb_w.Max.Z >= z_room + 0.45 * FT and d_xy < best_d_rev:
                            best_d_rev = d_xy
                            rev_wall = winfo["el"]

                host_wall = doc.GetElement(seg.ElementId)
                alvo_parede = rev_wall if rev_wall is not None else (host_wall if isinstance(host_wall, Wall) else None)
                desc_w = _acabamento_parede_face_interna(alvo_parede, inward_n, desc_modo)
                if desc_w:
                    comp_por_parede[desc_w] = comp_por_parede.get(desc_w, 0.0) + slen

                if rod_wall is not None:
                    desc_r = _acabamento_parede_face_interna(rod_wall, inward_n, desc_modo)
                    if desc_r:
                        comp_por_rodape[desc_r] = comp_por_rodape.get(desc_r, 0.0) + slen
            except Exception:
                pass

    p_ord = [k for k, _ in sorted(comp_por_parede.items(), key=lambda it: -it[1])]
    if wall_mode_idx == 1 and p_ord:
        p_ord = [p_ord[0]]
    r_ord = [k for k, _ in sorted(comp_por_rodape.items(), key=lambda it: -it[1])]
    if r_ord:
        r_ord = [r_ord[0]]

    return p_ord, (r_ord[0] if r_ord else None)


def _obter_ou_criar_vista_legenda(nome_vista):
    for vd in FilteredElementCollector(doc).OfClass(ViewDrafting).ToElements():
        try:
            if not vd.IsTemplate and vd.Name == nome_vista:
                # Limpa elementos antigos dentro da vista para atualizar sem perder posição em pranchas
                antigos = List[ElementId]()
                for el in FilteredElementCollector(doc, vd.Id).WhereElementIsNotElementType().ToElements():
                    if isinstance(el, (CurveElement, TextElement)):
                        antigos.Add(el.Id)
                if antigos.Count > 0:
                    doc.Delete(antigos)
                return vd
        except Exception:
            pass

    vft = None
    for vt in FilteredElementCollector(doc).OfClass(ViewFamilyType).ToElements():
        if vt.ViewFamily == ViewFamily.Drafting:
            vft = vt
            break
    if vft is None:
        return None
    vd = ViewDrafting.Create(doc, vft.Id)
    try:
        vd.Name = nome_vista
    except Exception:
        pass
    try:
        vd.Scale = 1
    except Exception:
        pass
    return vd


def _escolher_text_note_type():
    tipos = list(FilteredElementCollector(doc).OfClass(TextNoteType).ToElements())
    if not tipos:
        return None
    melhor = tipos[0]
    best_diff = 999.0
    for tp in tipos:
        try:
            p = tp.get_Parameter(BuiltInParameter.TEXT_SIZE)
            if p is not None:
                sz_mm = p.AsDouble() / MM
                diff = abs(sz_mm - 2.5)
                if diff < best_diff:
                    best_diff = diff
                    melhor = tp
        except Exception:
            pass
    return melhor


def _desenhar_legenda_grafica(vd, secoes):
    """Desenha tabela gráfica na ViewDrafting (escala 1:1) com ícones ○ Piso, △ Parede e □ Forro."""
    tnt = _escolher_text_note_type()
    if tnt is None or vd is None:
        return

    try:
        vd.Scale = 1
    except Exception:
        pass

    w_col_sim = 18.0 * MM
    w_col_des = 105.0 * MM
    w_col_amb = 62.0 * MM
    w_tot = w_col_sim + w_col_des + w_col_amb

    h_tit = 10.0 * MM
    h_sec = 7.5 * MM
    h_row = 8.5 * MM

    opts_left = TextNoteOptions(tnt.Id)
    opts_left.HorizontalAlignment = HorizontalTextAlignment.Left

    opts_mid_left = TextNoteOptions(tnt.Id)
    opts_mid_left.HorizontalAlignment = HorizontalTextAlignment.Left
    try:
        opts_mid_left.VerticalAlignment = VerticalTextAlignment.Middle
    except Exception:
        pass

    opts_ctr = TextNoteOptions(tnt.Id)
    opts_ctr.HorizontalAlignment = HorizontalTextAlignment.Center
    try:
        opts_ctr.VerticalAlignment = VerticalTextAlignment.Middle
    except Exception:
        pass

    centralizar_itens = []

    def linha(x0, y0, x1, y1):
        try:
            doc.Create.NewDetailCurve(vd, Line.CreateBound(XYZ(x0, y0, 0.0), XYZ(x1, y1, 0.0)))
        except Exception:
            pass

    def retangulo(x0, y0, x1, y1):
        linha(x0, y0, x1, y0)
        linha(x1, y0, x1, y1)
        linha(x1, y1, x0, y1)
        linha(x0, y1, x0, y0)

    def texto(x, y, txt, centered=False, mid_y=None):
        if not txt:
            return
        try:
            if centered:
                opts = opts_ctr
            elif mid_y is not None:
                opts = opts_mid_left
            else:
                opts = opts_left
            y_init = mid_y if mid_y is not None else y
            tn = TextNote.Create(doc, vd.Id, XYZ(x, y_init, 0.0), txt, opts)
            if tn is not None and mid_y is not None:
                try:
                    tn.VerticalAlignment = VerticalTextAlignment.Middle
                except Exception:
                    pass
                centralizar_itens.append((tn, x, mid_y, centered))
        except Exception:
            pass

    def simbolo(kind, cx, cy, codigo):
        if kind == "circ":
            r = 3.25 * MM
            try:
                arc1 = Arc.Create(XYZ(cx, cy, 0.0), r, 0.0, math.pi, XYZ.BasisX, XYZ.BasisY)
                arc2 = Arc.Create(XYZ(cx, cy, 0.0), r, math.pi, 2.0 * math.pi, XYZ.BasisX, XYZ.BasisY)
                doc.Create.NewDetailCurve(vd, arc1)
                doc.Create.NewDetailCurve(vd, arc2)
            except Exception:
                pass
            cy_txt = cy
        elif kind == "tri":
            p_top = XYZ(cx, cy + 3.3 * MM, 0.0)
            p_bl  = XYZ(cx - 4.0 * MM, cy - 2.9 * MM, 0.0)
            p_br  = XYZ(cx + 4.0 * MM, cy - 2.9 * MM, 0.0)
            for pa, pb in ((p_top, p_br), (p_br, p_bl), (p_bl, p_top)):
                try:
                    doc.Create.NewDetailCurve(vd, Line.CreateBound(pa, pb))
                except Exception:
                    pass
            cy_txt = cy - 0.75 * MM
        else:
            hs = 3.1 * MM
            retangulo(cx - hs, cy - hs, cx + hs, cy + hs)
            cy_txt = cy

        # Texto centralizado matematicamente dentro da forma geométrica
        texto(cx, cy_txt, unicode(codigo), centered=True, mid_y=cy_txt)

    def _wrap_text(txt, max_c):
        if not txt or len(txt) <= max_c:
            return txt or u""
        palavras = txt.split(u" ")
        linhas = []
        atual = u""
        for p in palavras:
            if not atual:
                atual = p
            elif len(atual) + 1 + len(p) <= max_c:
                atual = atual + u" " + p
            else:
                linhas.append(atual)
                atual = p
        if atual:
            linhas.append(atual)
        return u"\n".join(linhas)

    y = 0.0
    # Cabeçalho principal
    retangulo(0.0, y - h_tit, w_tot, y)
    texto(0.5 * w_tot, y - 0.5 * h_tit, u"LEGENDA DE ACABAMENTOS", centered=True, mid_y=y - 0.5 * h_tit)
    y -= h_tit

    for titulo_sec, kind, itens in secoes:
        if not itens:
            continue
        retangulo(0.0, y - h_sec, w_tot, y)
        texto(3.0 * MM, y - 0.5 * h_sec, titulo_sec, centered=False, mid_y=y - 0.5 * h_sec)
        y -= h_sec

        # Sub-cabeçalho colunas
        h_sub = 6.5 * MM
        retangulo(0.0, y - h_sub, w_tot, y)
        linha(w_col_sim, y - h_sub, w_col_sim, y)
        linha(w_col_sim + w_col_des, y - h_sub, w_col_sim + w_col_des, y)
        texto(0.5 * w_col_sim, y - 0.5 * h_sub, u"CÓD.", centered=True, mid_y=y - 0.5 * h_sub)
        texto(w_col_sim + 3.0 * MM, y - 0.5 * h_sub, u"DESCRIÇÃO DO ACABAMENTO", centered=False, mid_y=y - 0.5 * h_sub)
        texto(w_col_sim + w_col_des + 3.0 * MM, y - 0.5 * h_sub, u"AMBIENTES", centered=False, mid_y=y - 0.5 * h_sub)
        y -= h_sub

        for cod, desc, ambs in itens:
            desc_w = _wrap_text(desc, 44)
            amb_w  = _wrap_text(u", ".join(ambs), 24)
            n_linhas = max(1, desc_w.count(u"\n") + 1, amb_w.count(u"\n") + 1)
            hr = max(h_row, (4.5 + n_linhas * 3.8) * MM)
            retangulo(0.0, y - hr, w_tot, y)
            linha(w_col_sim, y - hr, w_col_sim, y)
            linha(w_col_sim + w_col_des, y - hr, w_col_sim + w_col_des, y)

            cy = y - 0.5 * hr
            simbolo(kind, 0.5 * w_col_sim, cy, cod)
            texto(w_col_sim + 3.0 * MM, cy, desc_w, centered=False, mid_y=cy)
            texto(w_col_sim + w_col_des + 3.0 * MM, cy, amb_w, centered=False, mid_y=cy)
            y -= hr

    # Ajuste fino pelo BoundingBox real do TextNote após regenerar a vista
    if centralizar_itens:
        try:
            doc.Regenerate()
        except Exception:
            pass
        for tn, tx, ty, ctr_x in centralizar_itens:
            try:
                bb = tn.get_BoundingBox(vd)
                if bb is not None:
                    cx_bb = 0.5 * (bb.Min.X + bb.Max.X)
                    cy_bb = 0.5 * (bb.Min.Y + bb.Max.Y)
                    dx = (tx - cx_bb) if ctr_x else 0.0
                    dy = (ty - cy_bb)
                    if abs(dx) > 0.01 * MM or abs(dy) > 0.01 * MM:
                        ElementTransformUtils.MoveElement(doc, tn.Id, XYZ(dx, dy, 0.0))
            except Exception:
                pass


def _obter_ou_criar_tabela_acabamentos(nome_tabela, incluir_rodape):
    sched = None
    for vs in FilteredElementCollector(doc).OfClass(ViewSchedule).ToElements():
        try:
            if vs.Name == nome_tabela:
                sched = vs
                break
        except Exception:
            pass

    if sched is None:
        sched = ViewSchedule.CreateSchedule(doc, ElementId(BuiltInCategory.OST_Rooms))
        try:
            sched.Name = nome_tabela
        except Exception:
            pass
    else:
        try:
            sched.Definition.ClearFields()
        except Exception:
            pass

    defn = sched.Definition
    sfields = list(defn.GetSchedulableFields())
    bips = [
        BuiltInParameter.ROOM_NUMBER,
        BuiltInParameter.ROOM_NAME,
        BuiltInParameter.ROOM_AREA,
        BuiltInParameter.ROOM_FINISH_FLOOR,
        BuiltInParameter.ROOM_FINISH_WALL,
        BuiltInParameter.ROOM_FINISH_CEILING,
    ]
    if incluir_rodape:
        bips.append(BuiltInParameter.ROOM_FINISH_BASE)

    for bip in bips:
        eid = ElementId(bip)
        for sf in sfields:
            try:
                if sf.ParameterId == eid:
                    defn.AddField(sf)
                    break
            except Exception:
                pass
    return sched


def run_quadro_acabamentos():
    global doc, uidoc, view
    uidoc = __revit__.ActiveUIDocument
    doc   = uidoc.Document
    view  = doc.ActiveView

    prefs = carregar_preferencias()
    win = QuadroAcabamentosWindow(prefs)
    win.ShowDialog()
    if not win.confirmado:
        script.exit()

    op = win.resultado
    salvar_preferencias(op)

    rooms = _selecionar_ambientes(op["qa_escopo"])

    # Pré-coleta Pisos, Forros e Paredes finas uma única vez (alta performance)
    floors_info = []
    for fl in FilteredElementCollector(doc).OfClass(Floor).WhereElementIsNotElementType().ToElements():
        try:
            floors_info.append({
                "el": fl,
                "bb": fl.get_BoundingBox(None),
                "top_refs": list(HostObjectUtils.GetTopFaces(fl)),
            })
        except Exception:
            pass

    ceilings_info = []
    for cl in FilteredElementCollector(doc).OfClass(Ceiling).WhereElementIsNotElementType().ToElements():
        try:
            ceilings_info.append({
                "el": cl,
                "bb": cl.get_BoundingBox(None),
                "bot_refs": list(HostObjectUtils.GetBottomFaces(cl)),
            })
        except Exception:
            pass

    thin_walls_info = []
    for w in FilteredElementCollector(doc).OfClass(Wall).WhereElementIsNotElementType().ToElements():
        try:
            if w.Width <= 0.065 * FT:
                loc = getattr(w, "Location", None)
                crv = loc.Curve if isinstance(loc, LocationCurve) else None
                thin_walls_info.append({
                    "el": w,
                    "bb": w.get_BoundingBox(None),
                    "crv": crv,
                    "h": _parede_altura_ft(w),
                })
        except Exception:
            pass

    # Varre todos os ambientes ordenados por Número / Nome
    rooms_sorted = sorted(rooms, key=lambda r: (_room_number(r), _room_name(r)))
    dados_rooms = []

    mapa_global = {}
    cnt_global = [0]

    mapa_cat = {"piso": {}, "parede": {}, "forro": {}, "rodape": {}}
    cnt_cat  = {"piso": 0, "parede": 0, "forro": 0, "rodape": 0}
    ambs_por_cod = {"piso": {}, "parede": {}, "forro": {}, "rodape": {}}

    def _alocar_codigo(cat_key, desc):
        if not desc:
            return None
        if op["qa_num_modo"] == 1:
            if desc not in mapa_global:
                cnt_global[0] += 1
                mapa_global[desc] = unicode(cnt_global[0])
            cod = mapa_global[desc]
            mapa_cat[cat_key][desc] = cod
        else:
            if desc not in mapa_cat[cat_key]:
                cnt_cat[cat_key] += 1
                mapa_cat[cat_key][desc] = unicode(cnt_cat[cat_key])
            cod = mapa_cat[cat_key][desc]
        return cod

    for r in rooms_sorted:
        pts_r = _pontos_amostra_room(r)
        r_lbl = (_room_number(r) + u" " + _room_name(r)).strip()

        desc_piso = _detectar_piso_room(r, pts_r, floors_info, op["qa_desc"])
        descs_par, desc_rod = _detectar_paredes_rodape_room(r, pts_r, thin_walls_info, op["qa_desc"], op["qa_parede_modo"])
        desc_forro = _detectar_forro_room(r, pts_r, ceilings_info, op["qa_desc"])

        cod_piso = _alocar_codigo("piso", desc_piso)
        if cod_piso:
            ambs_por_cod["piso"].setdefault((cod_piso, desc_piso), [])
            if r_lbl not in ambs_por_cod["piso"][(cod_piso, desc_piso)]:
                ambs_por_cod["piso"][(cod_piso, desc_piso)].append(r_lbl)

        cods_par = []
        for dp in (descs_par or []):
            cp = _alocar_codigo("parede", dp)
            if cp:
                cods_par.append(cp)
                ambs_por_cod["parede"].setdefault((cp, dp), [])
                if r_lbl not in ambs_por_cod["parede"][(cp, dp)]:
                    ambs_por_cod["parede"][(cp, dp)].append(r_lbl)

        cod_forro = _alocar_codigo("forro", desc_forro)
        if cod_forro:
            ambs_por_cod["forro"].setdefault((cod_forro, desc_forro), [])
            if r_lbl not in ambs_por_cod["forro"][(cod_forro, desc_forro)]:
                ambs_por_cod["forro"][(cod_forro, desc_forro)].append(r_lbl)

        cod_rod = _alocar_codigo("rodape", desc_rod) if op["qa_rodape"] else None
        if cod_rod:
            ambs_por_cod["rodape"].setdefault((cod_rod, desc_rod), [])
            if r_lbl not in ambs_por_cod["rodape"][(cod_rod, desc_rod)]:
                ambs_por_cod["rodape"][(cod_rod, desc_rod)].append(r_lbl)

        dados_rooms.append({
            "room": r,
            "piso": cod_piso or u"-",
            "parede": u" / ".join(cods_par) if cods_par else u"-",
            "forro": cod_forro or u"-",
            "rodape": cod_rod or u"-",
        })

    def _itens_ordenados(cat_key):
        items = []
        for (cod, desc), ambs in ambs_por_cod[cat_key].items():
            try:
                num_k = int(cod)
            except Exception:
                num_k = 9999
            items.append((num_k, cod, desc, ambs))
        items.sort(key=lambda it: (it[0], it[1]))
        return [(it[1], it[2], it[3]) for it in items]

    secoes = [
        (u"○ PISO (Acabamento do piso)", "circ", _itens_ordenados("piso")),
        (u"△ PAREDE / REVESTIMENTO (Acabamento da parede)", "tri", _itens_ordenados("parede")),
        (u"□ FORRO / TETO (Acabamento do forro)", "quad", _itens_ordenados("forro")),
    ]
    if op["qa_rodape"]:
        rod_items = _itens_ordenados("rodape")
        if rod_items:
            secoes.append((u"RODAPÉ (Acabamento base)", "quad", rod_items))

    vd_criada = None
    tab_criada = None
    qtd_gravadas = 0

    with Transaction(doc, "AutoBIM: Quadro de Acabamentos e Legenda") as t:
        t.Start()

        if op["qa_tag"]:
            for dr in dados_rooms:
                rm = dr["room"]
                pares = [
                    (BuiltInParameter.ROOM_FINISH_FLOOR,   dr["piso"]),
                    (BuiltInParameter.ROOM_FINISH_WALL,    dr["parede"]),
                    (BuiltInParameter.ROOM_FINISH_CEILING, dr["forro"]),
                ]
                if op["qa_rodape"]:
                    pares.append((BuiltInParameter.ROOM_FINISH_BASE, dr["rodape"]))
                ok_any = False
                for bip, val in pares:
                    p = rm.get_Parameter(bip)
                    if p is not None and not p.IsReadOnly:
                        p.Set(val)
                        ok_any = True
                if ok_any:
                    qtd_gravadas += 1

        if op["qa_legenda"]:
            vd_criada = _obter_ou_criar_vista_legenda(u"AutoBIM - Legenda de Acabamentos")
            if vd_criada is not None:
                _desenhar_legenda_grafica(vd_criada, secoes)

        if op["qa_tabela"]:
            tab_criada = _obter_ou_criar_tabela_acabamentos(u"AutoBIM - Quadro de Acabamentos", op["qa_rodape"])

        t.Commit()

    if vd_criada is not None and op["qa_abrir"]:
        try:
            uidoc.ActiveView = vd_criada
        except Exception:
            pass

    resumo = u"Quadro de Acabamentos concluído!\n\n"
    resumo += u"✔ Ambientes processados: {}\n".format(len(dados_rooms))
    if op["qa_tag"]:
        resumo += u"✔ Tags de Ambiente preenchidas: {}\n".format(qtd_gravadas)
    resumo += u"✔ Tipos identificados — ○ Piso: {} | △ Parede: {} | □ Forro: {}\n".format(
        len(secoes[0][2]), len(secoes[1][2]), len(secoes[2][2])
    )
    if vd_criada is not None:
        resumo += u"✔ Legenda Gráfica: '{}'\n".format(vd_criada.Name)
    if tab_criada is not None:
        resumo += u"✔ Tabela criada: '{}'\n".format(tab_criada.Name)
    forms.alert(resumo, title="Quadro de Acabamentos")


# ==============================================================================
# 2. RENUMERACAO RAPIDA POR CLIQUE
# ==============================================================================

XAML_RENUM = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Renumeração Rápida por Clique" Width="490" SizeToContent="Height"
        WindowStartupLocation="CenterScreen" ResizeMode="NoResize" Background="#F7F8FA">
    <StackPanel Margin="14">
        <Border Background="#EBF3FC" BorderBrush="#B8D4F5" BorderThickness="1" CornerRadius="4" Padding="9" Margin="0,0,0,10">
            <TextBlock TextWrapping="Wrap" FontWeight="SemiBold" Foreground="#1F3A57"
                       Text="Define o parâmetro alvo e clique sequencialmente nos elementos (ou nas suas Tags) para numerar ao vivo (ESC finaliza)."/>
        </Border>

        <Border Background="White" BorderBrush="#D0D7DE" BorderThickness="1" CornerRadius="4" Padding="10" Margin="0,0,0,8">
            <Grid>
                <Grid.ColumnDefinitions>
                    <ColumnDefinition Width="185"/>
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

                <TextBlock Grid.Row="0" Grid.Column="0" Text="Categoria dos elementos:" VerticalAlignment="Center"/>
                <ComboBox  Grid.Row="0" Grid.Column="1" x:Name="cb_cat" Height="24"/>

                <TextBlock Grid.Row="1" Grid.Column="0" Text="Parâmetro a numerar:" VerticalAlignment="Center"/>
                <ComboBox  Grid.Row="1" Grid.Column="1" x:Name="cb_alvo" Height="24"/>

                <TextBlock Grid.Row="2" Grid.Column="0" Text="Prefixo:" VerticalAlignment="Center"/>
                <TextBox   Grid.Row="2" Grid.Column="1" x:Name="tb_pref" Height="24" Text="P" VerticalContentAlignment="Center"/>

                <TextBlock Grid.Row="3" Grid.Column="0" Text="Número inicial:" VerticalAlignment="Center"/>
                <TextBox   Grid.Row="3" Grid.Column="1" x:Name="tb_ini" Height="24" Text="1" VerticalContentAlignment="Center"/>

                <TextBlock Grid.Row="4" Grid.Column="0" Text="Incremento:" VerticalAlignment="Center"/>
                <TextBox   Grid.Row="4" Grid.Column="1" x:Name="tb_passo" Height="24" Text="1" VerticalContentAlignment="Center"/>

                <TextBlock Grid.Row="5" Grid.Column="0" Text="Mínimo de dígitos (zeros):" VerticalAlignment="Center"/>
                <TextBox   Grid.Row="5" Grid.Column="1" x:Name="tb_dig" Height="24" Text="2" VerticalContentAlignment="Center"/>

                <TextBlock Grid.Row="6" Grid.Column="0" Text="Sufixo (opcional):" VerticalAlignment="Center"/>
                <TextBox   Grid.Row="6" Grid.Column="1" x:Name="tb_suf" Height="24" Text="" VerticalContentAlignment="Center"/>
            </Grid>
        </Border>

        <Border Background="#F0F4F8" BorderBrush="#CBD5E1" BorderThickness="1" CornerRadius="4" Padding="8" Margin="0,0,0,8">
            <TextBlock x:Name="tb_prev" Text="Pré-visualização: P01, P02, P03..." FontWeight="SemiBold" Foreground="#0F172A" HorizontalAlignment="Center"/>
        </Border>

        <CheckBox x:Name="ck_skip_tipo" Content="Ignorar Tipo já numerado nesta execução (evita sobrescrever em Marca de Tipo)" IsChecked="True" Margin="0,0,0,5"/>
        <CheckBox x:Name="ck_dup" Content="Silenciar aviso de Marca/Número duplicado ao clicar" IsChecked="True" Margin="0,0,0,12"/>

        <StackPanel Orientation="Horizontal" HorizontalAlignment="Right">
            <Button x:Name="btn_cancel" Content="Cancelar" Width="95" Height="30" Margin="0,0,8,0"/>
            <Button x:Name="btn_ok" Content="Iniciar Clique Sequencial" Width="175" Height="30"
                    IsDefault="True" Background="#0078D4" Foreground="White" FontWeight="SemiBold"/>
        </StackPanel>
    </StackPanel>
</Window>
"""

_RENUM_CATS = [
    (u"Portas",                  BuiltInCategory.OST_Doors,             BuiltInParameter.ALL_MODEL_MARK, u"P", True),
    (u"Janelas",                 BuiltInCategory.OST_Windows,           BuiltInParameter.ALL_MODEL_MARK, u"J", True),
    (u"Ambientes / Rooms",       BuiltInCategory.OST_Rooms,             BuiltInParameter.ROOM_NUMBER,    u"",  False),
    (u"Vagas / Estacionamento",  BuiltInCategory.OST_Parking,           BuiltInParameter.ALL_MODEL_MARK, u"V", True),
    (u"Mobiliário",              BuiltInCategory.OST_Furniture,         BuiltInParameter.ALL_MODEL_MARK, u"M", True),
    (u"Pilares Estruturais",     BuiltInCategory.OST_StructuralColumns, BuiltInParameter.ALL_MODEL_MARK, u"P", True),
    (u"Qualquer Elemento",       None,                                  BuiltInParameter.ALL_MODEL_MARK, u"E", True),
]

_RENUM_ALVOS = [
    u"Marca de Tipo (Type Mark — Tags por Tipo)",
    u"Marca / Número da Instância (cada clique único)",
    u"Ambos (Marca da Instância + Marca de Tipo)",
]


class RenumerarCliqueWindow(forms.WPFWindow):
    def __init__(self, prefs):
        forms.WPFWindow.__init__(self, XAML_RENUM, literal_string=True)
        self.confirmado = False
        self.resultado  = {}

        self._cb_cat    = self.FindName("cb_cat")
        self._cb_alvo   = self.FindName("cb_alvo")
        self._tb_pref   = self.FindName("tb_pref")
        self._tb_ini    = self.FindName("tb_ini")
        self._tb_passo  = self.FindName("tb_passo")
        self._tb_dig    = self.FindName("tb_dig")
        self._tb_suf    = self.FindName("tb_suf")
        self._tb_prev   = self.FindName("tb_prev")
        self._ck_skip_t = self.FindName("ck_skip_tipo")
        self._ck_dup    = self.FindName("ck_dup")

        for nome_c, _, _, _, _ in _RENUM_CATS:
            self._cb_cat.Items.Add(nome_c)
        self._cb_cat.SelectedIndex = max(0, min(len(_RENUM_CATS) - 1, int(prefs.get("rn_cat", 0) or 0)))

        for nome_a in _RENUM_ALVOS:
            self._cb_alvo.Items.Add(nome_a)
        self._cb_alvo.SelectedIndex = max(0, min(len(_RENUM_ALVOS) - 1, int(prefs.get("rn_alvo", 0) or 0)))

        self._tb_pref.Text  = prefs.get("rn_pref", u"P")
        self._tb_ini.Text   = str(prefs.get("rn_ini", "1"))
        self._tb_passo.Text = str(prefs.get("rn_passo", "1"))
        self._tb_dig.Text   = str(prefs.get("rn_dig", "2"))
        self._tb_suf.Text   = prefs.get("rn_suf", u"")
        self._ck_skip_t.IsChecked = bool(prefs.get("rn_skip_tipo", True))
        self._ck_dup.IsChecked    = bool(prefs.get("rn_sil_dup", True))

        self._cb_cat.SelectionChanged += self._ao_mudar_cat
        for tb in (self._tb_pref, self._tb_ini, self._tb_passo, self._tb_dig, self._tb_suf):
            tb.TextChanged += self._atualizar_preview
        self._atualizar_preview(None, None)

        self.FindName("btn_ok").Click     += self.on_ok
        self.FindName("btn_cancel").Click += self.on_cancel

    def _ao_mudar_cat(self, sender, args):
        idx = max(0, self._cb_cat.SelectedIndex)
        self._tb_pref.Text = _RENUM_CATS[idx][3]
        if not _RENUM_CATS[idx][4]:
            self._cb_alvo.SelectedIndex = 1
        self._atualizar_preview(None, None)

    def _atualizar_preview(self, sender, args):
        try:
            ini = int(self._tb_ini.Text)
            pas = int(self._tb_passo.Text)
            dig = max(1, min(6, int(self._tb_dig.Text)))
            pref = self._tb_pref.Text or u""
            suf  = self._tb_suf.Text or u""
            amostras = [u"{}{}{}".format(pref, str(ini + i * pas).zfill(dig), suf) for i in range(3)]
            self._tb_prev.Text = u"Pré-visualização: {}, {}...".format(u", ".join(amostras[:-1]), amostras[-1])
        except Exception:
            self._tb_prev.Text = u"Pré-visualização: (informe números inteiros)"

    def on_cancel(self, sender, args):
        self.confirmado = False
        self.Close()

    def on_ok(self, sender, args):
        try:
            res = {
                "rn_cat":       max(0, self._cb_cat.SelectedIndex),
                "rn_alvo":      max(0, self._cb_alvo.SelectedIndex),
                "rn_pref":      self._tb_pref.Text or u"",
                "rn_ini":       int(self._tb_ini.Text),
                "rn_passo":     int(self._tb_passo.Text),
                "rn_dig":       max(1, min(6, int(self._tb_dig.Text))),
                "rn_suf":       self._tb_suf.Text or u"",
                "rn_skip_tipo": bool(self._ck_skip_t.IsChecked),
                "rn_sil_dup":   bool(self._ck_dup.IsChecked),
            }
        except Exception:
            forms.alert("Informe números inteiros válidos para Início, Incremento e Dígitos.")
            return
        self.resultado = res
        self.confirmado = True
        self.Close()


def _resolver_elemento_clicado(el):
    if el is None:
        return None
    if isinstance(el, RoomTag):
        try:
            return el.Room
        except Exception:
            return None
    if isinstance(el, IndependentTag):
        try:
            if hasattr(el, "GetTaggedLocalElements"):
                for t_el in el.GetTaggedLocalElements():
                    if t_el is not None:
                        return t_el
        except Exception:
            pass
        try:
            tid = el.TaggedLocalElementId
            if tid is not None and tid != ElementId.InvalidElementId:
                return doc.GetElement(tid)
        except Exception:
            pass
        return None
    return el


class _CatSelectionFilter(ISelectionFilter):
    def __init__(self, bic):
        self._cat_id = ElementId(bic) if bic is not None else None
        self._is_room = (bic == BuiltInCategory.OST_Rooms)

    def AllowElement(self, elem):
        if elem is None:
            return False
        if self._cat_id is None:
            return True
        if self._is_room and isinstance(elem, RoomTag):
            return True
        if isinstance(elem, IndependentTag):
            alvo = _resolver_elemento_clicado(elem)
            return alvo is not None and alvo.Category is not None and alvo.Category.Id == self._cat_id
        if elem.Category is None:
            return False
        return elem.Category.Id == self._cat_id

    def AllowReference(self, ref, pt):
        return True


class _DupMarkPreprocessor(IFailuresPreprocessor):
    def PreprocessFailures(self, failuresAccessor):
        for fmsg in list(failuresAccessor.GetFailureMessages()):
            try:
                if fmsg.GetSeverity() == FailureSeverity.Warning:
                    failuresAccessor.DeleteWarning(fmsg)
            except Exception:
                pass
        return FailureProcessingResult.Continue


def run_renumerar_clique():
    global doc, uidoc, view
    uidoc = __revit__.ActiveUIDocument
    doc   = uidoc.Document
    view  = doc.ActiveView

    prefs = carregar_preferencias()
    win = RenumerarCliqueWindow(prefs)
    win.ShowDialog()
    if not win.confirmado:
        script.exit()

    op = win.resultado
    salvar_preferencias(op)

    _, bic, bip, _, tem_tipo = _RENUM_CATS[op["rn_cat"]]
    alvo_modo = op.get("rn_alvo", 0) if tem_tipo else 1  # 0=TypeMark, 1=Instance, 2=Ambos
    skip_tipo = bool(op.get("rn_skip_tipo", True))

    flt = _CatSelectionFilter(bic)
    n_atual = op["rn_ini"]
    numerados = []
    tipos_numerados = {}
    aviso_topo = u""

    while True:
        codigo = u"{}{}{}".format(op["rn_pref"], str(n_atual).zfill(op["rn_dig"]), op["rn_suf"])
        titulo_bar = u"Clique no elemento para numerar como [ {} ]  —  ESC encerra".format(codigo)
        if aviso_topo:
            titulo_bar = u"{} | Próximo: [ {} ] (ESC encerra)".format(aviso_topo, codigo)
        aviso_topo = u""

        try:
            with forms.WarningBar(title=titulo_bar):
                ref = uidoc.Selection.PickObject(
                    ObjectType.Element,
                    flt,
                    titulo_bar
                )
        except Exception:
            break

        if ref is None:
            break

        el = _resolver_elemento_clicado(doc.GetElement(ref.ElementId))
        if el is None:
            continue

        el_type = None
        tid_key = None
        if alvo_modo in (0, 2):
            try:
                tid = el.GetTypeId()
                if tid is not None and tid != ElementId.InvalidElementId:
                    el_type = doc.GetElement(tid)
                    tid_key = str(tid)
            except Exception:
                el_type = None

        if alvo_modo == 0 and skip_tipo and tid_key and tid_key in tipos_numerados:
            aviso_topo = u"Tipo '{}' já numerado como [{}]".format(
                _elem_name(el_type), tipos_numerados[tid_key]
            )
            continue

        params_para_gravar = []
        if alvo_modo in (1, 2) or (alvo_modo == 0 and el_type is None):
            p_inst = el.get_Parameter(bip)
            if p_inst is None or p_inst.IsReadOnly:
                p_inst = el.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
            if p_inst is not None and not p_inst.IsReadOnly:
                params_para_gravar.append(p_inst)

        if alvo_modo in (0, 2) and el_type is not None:
            p_tipo = el_type.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_MARK)
            if p_tipo is not None and not p_tipo.IsReadOnly:
                params_para_gravar.append(p_tipo)

        if not params_para_gravar:
            aviso_topo = u"Elemento/Tipo sem parâmetro editável"
            continue

        gravou_algum = False
        with Transaction(doc, u"Renumerar {}".format(codigo)) as t:
            t.Start()
            if op["rn_sil_dup"]:
                fopts = t.GetFailureHandlingOptions()
                fopts.SetFailuresPreprocessor(_DupMarkPreprocessor())
                t.SetFailureHandlingOptions(fopts)
            for pr in params_para_gravar:
                try:
                    if pr.Set(codigo):
                        gravou_algum = True
                except Exception:
                    pass
            t.Commit()

        if gravou_algum:
            try:
                uidoc.RefreshActiveView()
            except Exception:
                pass
            if tid_key and alvo_modo in (0, 2):
                tipos_numerados[tid_key] = codigo
            numerados.append(codigo)
            n_atual += op["rn_passo"]
        else:
            aviso_topo = u"Falha ao gravar código"

    if numerados:
        forms.alert(
            u"Renumeração concluída!\n\n✔ Cliques gravados: {}\n✔ Sequência aplicada: {} até {}".format(
                len(numerados), numerados[0], numerados[-1]
            ),
            title="Renumerar por Clique"
        )


# ==============================================================================
# 3. TAG AUTOMATICA INTELIGENTE EM LOTE (ANTI-SOBREPOSICAO)
# ==============================================================================

_TAG_CATS = [
    (u"Portas",                   BuiltInCategory.OST_Doors,             BuiltInCategory.OST_DoorTags,              False),
    (u"Janelas",                  BuiltInCategory.OST_Windows,           BuiltInCategory.OST_WindowTags,            False),
    (u"Ambientes (Rooms)",        BuiltInCategory.OST_Rooms,             BuiltInCategory.OST_RoomTags,              True),
    (u"Paredes",                  BuiltInCategory.OST_Walls,             BuiltInCategory.OST_WallTags,              False),
    (u"Mobiliário",               BuiltInCategory.OST_Furniture,         BuiltInCategory.OST_FurnitureTags,         False),
    (u"Vigas / Quadros Estrut.",  BuiltInCategory.OST_StructuralFraming, BuiltInCategory.OST_StructuralFramingTags, False),
    (u"Pilares Estruturais",      BuiltInCategory.OST_StructuralColumns, BuiltInCategory.OST_StructuralColumnTags,  False),
]

XAML_TAG_AUTO = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Tag Automática Inteligente em Lote" Width="500" SizeToContent="Height"
        WindowStartupLocation="CenterScreen" ResizeMode="NoResize" Background="#F7F8FA">
    <StackPanel Margin="14">
        <Border Background="#EBF3FC" BorderBrush="#B8D4F5" BorderThickness="1" CornerRadius="4" Padding="9" Margin="0,0,0,10">
            <TextBlock TextWrapping="Wrap" FontWeight="SemiBold" Foreground="#1F3A57"
                       Text="Identifica todos os elementos da vista (ou selecionados) evitando tags duplicadas e deslocando automaticamente em caso de sobreposição."/>
        </Border>

        <Border Background="White" BorderBrush="#D0D7DE" BorderThickness="1" CornerRadius="4" Padding="10" Margin="0,0,0,8">
            <Grid>
                <Grid.ColumnDefinitions>
                    <ColumnDefinition Width="190"/>
                    <ColumnDefinition Width="*"/>
                </Grid.ColumnDefinitions>
                <Grid.RowDefinitions>
                    <RowDefinition Height="28"/>
                    <RowDefinition Height="28"/>
                    <RowDefinition Height="28"/>
                    <RowDefinition Height="28"/>
                    <RowDefinition Height="28"/>
                </Grid.RowDefinitions>

                <TextBlock Grid.Row="0" Grid.Column="0" Text="Categoria a identificar:" VerticalAlignment="Center"/>
                <ComboBox  Grid.Row="0" Grid.Column="1" x:Name="cb_cat" Height="24"/>

                <TextBlock Grid.Row="1" Grid.Column="0" Text="Família / Tipo da Tag:" VerticalAlignment="Center"/>
                <ComboBox  Grid.Row="1" Grid.Column="1" x:Name="cb_tag_tipo" Height="24"/>

                <TextBlock Grid.Row="2" Grid.Column="0" Text="Escopo na vista:" VerticalAlignment="Center"/>
                <ComboBox  Grid.Row="2" Grid.Column="1" x:Name="cb_escopo" Height="24"/>

                <TextBlock Grid.Row="3" Grid.Column="0" Text="Orientação da Tag:" VerticalAlignment="Center"/>
                <ComboBox  Grid.Row="3" Grid.Column="1" x:Name="cb_orient" Height="24"/>

                <TextBlock Grid.Row="4" Grid.Column="0" Text="Deslocamento inicial (mm):" VerticalAlignment="Center"/>
                <TextBox   Grid.Row="4" Grid.Column="1" x:Name="tb_desloc" Height="24" Text="0" VerticalContentAlignment="Center"/>
            </Grid>
        </Border>

        <Border Background="White" BorderBrush="#D0D7DE" BorderThickness="1" CornerRadius="4" Padding="10" Margin="0,0,0,12">
            <StackPanel>
                <CheckBox x:Name="ck_ignorar_ja" Content="Ignorar elementos que já possuem Tag nesta vista (Recomendado)" IsChecked="True" Margin="0,0,0,4"/>
                <CheckBox x:Name="ck_anti_sob"   Content="Deslocar automaticamente para evitar sobreposição com outras Tags" IsChecked="True" Margin="0,0,0,4"/>
                <CheckBox x:Name="ck_leader"     Content="Adicionar linha de chamada (Leader) quando houver deslocamento" IsChecked="False"/>
            </StackPanel>
        </Border>

        <StackPanel Orientation="Horizontal" HorizontalAlignment="Right">
            <Button x:Name="btn_cancel" Content="Cancelar" Width="95" Height="30" Margin="0,0,8,0"/>
            <Button x:Name="btn_ok" Content="Inserir Tags" Width="150" Height="30"
                    IsDefault="True" Background="#0078D4" Foreground="White" FontWeight="SemiBold"/>
        </StackPanel>
    </StackPanel>
</Window>
"""


def _listar_tipos_tag(tag_bic):
    tipos = list(FilteredElementCollector(doc).OfCategory(tag_bic).OfClass(FamilySymbol).ToElements())
    out = []
    for sym in tipos:
        try:
            fam_n = getattr(sym, "FamilyName", u"")
            typ_n = _elem_name(sym)
            lbl = u"{}: {}".format(fam_n, typ_n) if fam_n else typ_n
            out.append((lbl, sym))
        except Exception:
            pass
    out.sort(key=lambda it: it[0])
    return out


class TagAutomaticaWindow(forms.WPFWindow):
    def __init__(self, prefs):
        forms.WPFWindow.__init__(self, XAML_TAG_AUTO, literal_string=True)
        self.confirmado = False
        self.resultado  = {}
        self._tipos_atuais = []

        self._cb_cat     = self.FindName("cb_cat")
        self._cb_tipo    = self.FindName("cb_tag_tipo")
        self._cb_escopo  = self.FindName("cb_escopo")
        self._cb_orient  = self.FindName("cb_orient")
        self._tb_desloc  = self.FindName("tb_desloc")
        self._ck_ign     = self.FindName("ck_ignorar_ja")
        self._ck_anti    = self.FindName("ck_anti_sob")
        self._ck_leader  = self.FindName("ck_leader")

        for nome_c, _, _, _ in _TAG_CATS:
            self._cb_cat.Items.Add(nome_c)
        self._cb_cat.SelectedIndex = max(0, min(len(_TAG_CATS) - 1, int(prefs.get("ta_cat", 0) or 0)))

        self._cb_escopo.Items.Add(u"Todos os elementos visíveis na vista ativa")
        self._cb_escopo.Items.Add(u"Apenas elementos selecionados na vista")
        self._cb_escopo.SelectedIndex = max(0, min(1, int(prefs.get("ta_escopo", 0) or 0)))

        self._cb_orient.Items.Add(u"Horizontal (padrão)")
        self._cb_orient.Items.Add(u"Alinhar com o elemento / parede")
        self._cb_orient.SelectedIndex = max(0, min(1, int(prefs.get("ta_orient", 1) or 1)))

        self._tb_desloc.Text     = str(prefs.get("ta_desloc", "0"))
        self._ck_ign.IsChecked   = bool(prefs.get("ta_ign_ja", True))
        self._ck_anti.IsChecked  = bool(prefs.get("ta_anti_sob", True))
        self._ck_leader.IsChecked = bool(prefs.get("ta_leader", False))

        self._cb_cat.SelectionChanged += self._carregar_tipos_tag
        self._carregar_tipos_tag(None, None)

        self.FindName("btn_ok").Click     += self.on_ok
        self.FindName("btn_cancel").Click += self.on_cancel

    def _carregar_tipos_tag(self, sender, args):
        idx = max(0, self._cb_cat.SelectedIndex)
        tag_bic = _TAG_CATS[idx][2]
        self._tipos_atuais = _listar_tipos_tag(tag_bic)
        self._cb_tipo.Items.Clear()
        if not self._tipos_atuais:
            self._cb_tipo.Items.Add(u"(Nenhum tipo de Tag carregado para esta categoria)")
            self._cb_tipo.SelectedIndex = 0
        else:
            for lbl, _ in self._tipos_atuais:
                self._cb_tipo.Items.Add(lbl)
            self._cb_tipo.SelectedIndex = 0

    def on_cancel(self, sender, args):
        self.confirmado = False
        self.Close()

    def on_ok(self, sender, args):
        if not self._tipos_atuais:
            forms.alert(u"Carregue uma família de Tag para a categoria escolhida antes de continuar.")
            return
        try:
            desloc_mm = float(self._tb_desloc.Text.replace(",", "."))
        except Exception:
            forms.alert("Informe um valor numérico válido para o deslocamento (mm).")
            return

        idx_t = max(0, min(len(self._tipos_atuais) - 1, self._cb_tipo.SelectedIndex))
        self.resultado = {
            "ta_cat":       max(0, self._cb_cat.SelectedIndex),
            "ta_tag_sym":   self._tipos_atuais[idx_t][1],
            "ta_escopo":    max(0, self._cb_escopo.SelectedIndex),
            "ta_orient":    max(0, self._cb_orient.SelectedIndex),
            "ta_desloc":    desloc_mm,
            "ta_ign_ja":    bool(self._ck_ign.IsChecked),
            "ta_anti_sob":  bool(self._ck_anti.IsChecked),
            "ta_leader":    bool(self._ck_leader.IsChecked),
        }
        self.confirmado = True
        self.Close()


def _proj_2d_vista(pt, origin, r_dir, u_dir):
    v = pt - origin
    return (v.DotProduct(r_dir), v.DotProduct(u_dir))


def _unproj_2d_vista(u, v, origin, r_dir, u_dir):
    return origin + r_dir * u + u_dir * v


def _colide_caixa_2d(cx, cy, hw, hh, caixas):
    for (x0, y0, x1, y1) in caixas:
        if (cx - hw) < x1 and (cx + hw) > x0 and (cy - hh) < y1 and (cy + hh) > y0:
            return True
    return False


def _ponto_ancora_elemento(el, desloc_ft, r_dir, u_dir):
    pt = None
    dir_elem = None
    facing = None

    loc = getattr(el, "Location", None)
    if isinstance(loc, LocationPoint) and loc.Point is not None:
        pt = loc.Point
        try:
            facing = el.FacingOrientation
        except Exception:
            pass
        try:
            dir_elem = el.HandOrientation
        except Exception:
            pass
    elif isinstance(loc, LocationCurve) and loc.Curve is not None:
        crv = loc.Curve
        pt = crv.Evaluate(0.5, True)
        try:
            p0, p1 = crv.GetEndPoint(0), crv.GetEndPoint(1)
            d = p1 - p0
            if d.GetLength() > 1e-6:
                dir_elem = d.Normalize()
        except Exception:
            pass

    if pt is None:
        bb = el.get_BoundingBox(view) or el.get_BoundingBox(None)
        if bb is not None:
            pt = 0.5 * (bb.Min + bb.Max)

    if pt is not None and abs(desloc_ft) > 1e-5:
        if facing is not None:
            pt = pt + facing.Normalize() * desloc_ft
        else:
            pt = pt + u_dir * desloc_ft

    return pt, dir_elem


def _tags_existentes_na_vista(vw):
    tagged_ids = set()
    caixas_2d  = []
    origin = vw.Origin
    r_dir  = vw.RightDirection
    u_dir  = vw.UpDirection

    for cls in (IndependentTag, SpatialElementTag):
        for tg in FilteredElementCollector(doc, vw.Id).OfClass(cls).ToElements():
            try:
                if isinstance(tg, RoomTag):
                    rm = tg.Room
                    if rm is not None:
                        tagged_ids.add(rm.Id)
                elif isinstance(tg, IndependentTag):
                    try:
                        for lid in tg.GetTaggedLocalElementIds():
                            tagged_ids.add(lid)
                    except Exception:
                        try:
                            tid = tg.TaggedLocalElementId
                            if tid != ElementId.InvalidElementId:
                                tagged_ids.add(tid)
                        except Exception:
                            pass
                bb = tg.get_BoundingBox(vw)
                if bb is not None:
                    c1 = _proj_2d_vista(bb.Min, origin, r_dir, u_dir)
                    c2 = _proj_2d_vista(bb.Max, origin, r_dir, u_dir)
                    caixas_2d.append((min(c1[0], c2[0]), min(c1[1], c2[1]), max(c1[0], c2[0]), max(c1[1], c2[1])))
            except Exception:
                pass
    return tagged_ids, caixas_2d


def run_tag_automatica():
    global doc, uidoc, view
    uidoc = __revit__.ActiveUIDocument
    doc   = uidoc.Document
    view  = doc.ActiveView

    if getattr(view, "ViewType", None) == ViewType.ThreeD:
        forms.alert(u"Execute este comando em uma vista 2D (Planta, Corte ou Elevação) desbloqueada.", exitscript=True)

    prefs = carregar_preferencias()
    win = TagAutomaticaWindow(prefs)
    win.ShowDialog()
    if not win.confirmado:
        script.exit()

    op = win.resultado
    salvar_preferencias({
        "ta_cat":      op["ta_cat"],
        "ta_escopo":   op["ta_escopo"],
        "ta_orient":   op["ta_orient"],
        "ta_desloc":   op["ta_desloc"],
        "ta_ign_ja":   op["ta_ign_ja"],
        "ta_anti_sob": op["ta_anti_sob"],
        "ta_leader":   op["ta_leader"],
    })

    _, elem_bic, _, is_room = _TAG_CATS[op["ta_cat"]]
    tag_sym = op["ta_tag_sym"]

    if op["ta_escopo"] == 1:
        elems = [
            doc.GetElement(eid) for eid in uidoc.Selection.GetElementIds()
            if doc.GetElement(eid) is not None and doc.GetElement(eid).Category is not None
            and doc.GetElement(eid).Category.Id == ElementId(elem_bic)
        ]
        if not elems:
            forms.alert(u"Nenhum elemento da categoria escolhida estava selecionado na vista.", exitscript=True)
    else:
        elems = list(
            FilteredElementCollector(doc, view.Id)
            .OfCategory(elem_bic)
            .WhereElementIsNotElementType()
            .ToElements()
        )

    if is_room:
        elems = [r for r in elems if getattr(r, "Area", 0.0) > 1e-4 and getattr(r, "Location", None) is not None]

    if not elems:
        forms.alert(u"Nenhum elemento válido encontrado nesta vista.", exitscript=True)

    tagged_ids, caixas_2d = _tags_existentes_na_vista(view)
    origin = view.Origin
    r_dir  = view.RightDirection
    u_dir  = view.UpDirection

    escala = max(1.0, float(view.Scale))
    desloc_ft = (op["ta_desloc"] * MM) * escala
    hw_tag = (7.0 * MM) * escala
    hh_tag = (3.2 * MM) * escala
    passo  = (4.8 * MM) * escala

    # Ordem espiral determinística para buscar espaço livre sem sobreposição
    offsets_espiral = [(0.0, 0.0)]
    for ring in range(1, 7):
        d = ring * passo
        offsets_espiral.extend([
            (0.0, d), (0.0, -d), (d, 0.0), (-d, 0.0),
            (d, d), (-d, d), (d, -d), (-d, -d),
            (2.0 * d, 0.0), (-2.0 * d, 0.0),
        ])

    criadas = 0
    puladas = 0
    erros   = 0

    with Transaction(doc, "AutoBIM: Tag Automática em Lote") as t:
        t.Start()
        if not tag_sym.IsActive:
            tag_sym.Activate()
            doc.Regenerate()

        for el in elems:
            if op["ta_ign_ja"] and el.Id in tagged_ids:
                puladas += 1
                continue

            pt_base, dir_el = _ponto_ancora_elemento(el, desloc_ft, r_dir, u_dir)
            if pt_base is None:
                continue

            cx0, cy0 = _proj_2d_vista(pt_base, origin, r_dir, u_dir)
            cx_fim, cy_fim = cx0, cy0
            deslocou = False

            if op["ta_anti_sob"]:
                for ox, oy in offsets_espiral:
                    if not _colide_caixa_2d(cx0 + ox, cy0 + oy, hw_tag, hh_tag, caixas_2d):
                        cx_fim, cy_fim = cx0 + ox, cy0 + oy
                        deslocou = (abs(ox) > 1e-6 or abs(oy) > 1e-6)
                        break

            pt_final = _unproj_2d_vista(cx_fim, cy_fim, origin, r_dir, u_dir)

            orient_enum = TagOrientation.Horizontal
            if op["ta_orient"] == 1 and dir_el is not None:
                d_r = abs(dir_el.DotProduct(r_dir))
                d_u = abs(dir_el.DotProduct(u_dir))
                if d_u > d_r * 1.15:
                    orient_enum = TagOrientation.Vertical

            try:
                usa_leader = bool(op["ta_leader"] and (deslocou or abs(desloc_ft) > 1e-5))
                if is_room:
                    rt = doc.Create.NewRoomTag(LinkElementId(el.Id), UV(pt_final.X, pt_final.Y), view.Id)
                    if rt is not None:
                        rt.ChangeTypeId(tag_sym.Id)
                        rt.HasLeader = usa_leader
                        criadas += 1
                else:
                    tag_new = IndependentTag.Create(
                        doc, view.Id, Reference(el), usa_leader, TagMode.TM_ADDBY_CATEGORY, orient_enum, pt_final
                    )
                    if tag_new is not None:
                        try:
                            tag_new.ChangeTypeId(tag_sym.Id)
                        except Exception:
                            pass
                        criadas += 1
                caixas_2d.append((cx_fim - hw_tag, cy_fim - hh_tag, cx_fim + hw_tag, cy_fim + hh_tag))
                tagged_ids.add(el.Id)
            except Exception:
                erros += 1

        t.Commit()

    msg = u"Tag Automática concluída!\n\n✔ Tags inseridas: {}\n".format(criadas)
    if puladas:
        msg += u"• Já possuíam Tag (ignorados): {}\n".format(puladas)
    if erros:
        msg += u"⚠ Não foi possível identificar: {}\n".format(erros)
    forms.alert(msg, title="Tag Automática em Lote")


# ==============================================================================
# 4. RENUMERACAO AUTOMATICA EM LOTE (POR LARGURA / ALTURA / TABELA)
# ==============================================================================

_LOTE_CRITERIOS = [
    ("none",      u"(Nenhum)"),
    ("width",     u"Largura"),
    ("height",    u"Altura"),
    ("sill",      u"Peitoril (Altura do peitoril)"),
    ("desc",      u"Descrição"),
    ("comments",  u"Comentários"),
    ("fam_type",  u"Família e Tipo"),
    ("type_name", u"Nome do Tipo"),
    ("level",     u"Nível / Pavimento"),
    ("room",      u"Ambiente (Room)"),
    ("pos_xy",    u"Posição em Planta (Y → X)"),
]

_LOTE_DIRS = [
    u"Crescente",
    u"Decrescente",
]

_LOTE_ESCOPOS = [
    u"Todos em uso no projeto (ignora biblioteca não inserida)",
    u"Somente na vista ativa",
    u"Somente elementos selecionados",
]

_LOTE_CATS = [
    (u"Portas",                  BuiltInCategory.OST_Doors,             BuiltInParameter.ALL_MODEL_MARK, u"P", True),
    (u"Janelas",                 BuiltInCategory.OST_Windows,           BuiltInParameter.ALL_MODEL_MARK, u"J", True),
    (u"Ambientes / Rooms",       BuiltInCategory.OST_Rooms,             BuiltInParameter.ROOM_NUMBER,    u"",  False),
    (u"Vagas / Estacionamento",  BuiltInCategory.OST_Parking,           BuiltInParameter.ALL_MODEL_MARK, u"V", True),
    (u"Mobiliário",              BuiltInCategory.OST_Furniture,         BuiltInParameter.ALL_MODEL_MARK, u"M", True),
    (u"Pilares Estruturais",     BuiltInCategory.OST_StructuralColumns, BuiltInParameter.ALL_MODEL_MARK, u"P", True),
]

XAML_RENUM_LOTE = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Renumerar Automático em Lote (Ordenação estilo Tabela)" Width="760" Height="690"
        WindowStartupLocation="CenterScreen" ResizeMode="CanResize" Background="#F7F8FA">
    <Grid Margin="14">
        <Grid.RowDefinitions>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="*"/>
            <RowDefinition Height="Auto"/>
        </Grid.RowDefinitions>

        <Border Grid.Row="0" Background="#EBF3FC" BorderBrush="#B8D4F5" BorderThickness="1" CornerRadius="4" Padding="9" Margin="0,0,0,8">
            <TextBlock TextWrapping="Wrap" FontWeight="SemiBold" Foreground="#1F3A57"
                       Text="Ordena e renumera automaticamente esquadrias ou ambientes (ex.: por Largura → Altura → Descrição → Comentários ou seguindo uma Tabela existente), com pré-visualização antes de aplicar."/>
        </Border>

        <!-- Origem / Categoria / Alvo -->
        <Border Grid.Row="1" Background="White" BorderBrush="#D0D7DE" BorderThickness="1" CornerRadius="4" Padding="10" Margin="0,0,0,8">
            <Grid>
                <Grid.ColumnDefinitions>
                    <ColumnDefinition Width="170"/>
                    <ColumnDefinition Width="*"/>
                    <ColumnDefinition Width="15"/>
                    <ColumnDefinition Width="145"/>
                    <ColumnDefinition Width="195"/>
                </Grid.ColumnDefinitions>
                <Grid.RowDefinitions>
                    <RowDefinition Height="28"/>
                    <RowDefinition Height="28"/>
                    <RowDefinition Height="28"/>
                </Grid.RowDefinitions>

                <TextBlock Grid.Row="0" Grid.Column="0" Text="Categoria dos elementos:" VerticalAlignment="Center"/>
                <ComboBox  Grid.Row="0" Grid.Column="1" x:Name="cb_cat" Height="24"/>
                <TextBlock Grid.Row="0" Grid.Column="3" Text="Prefixo / Início:" VerticalAlignment="Center"/>
                <Grid Grid.Row="0" Grid.Column="4">
                    <Grid.ColumnDefinitions>
                        <ColumnDefinition Width="*"/>
                        <ColumnDefinition Width="6"/>
                        <ColumnDefinition Width="*"/>
                    </Grid.ColumnDefinitions>
                    <TextBox Grid.Column="0" x:Name="tb_pref" Height="24" Text="P" VerticalContentAlignment="Center" ToolTip="Prefixo"/>
                    <TextBox Grid.Column="2" x:Name="tb_ini" Height="24" Text="1" VerticalContentAlignment="Center" ToolTip="Número inicial"/>
                </Grid>

                <TextBlock Grid.Row="1" Grid.Column="0" Text="Escopo de coleta:" VerticalAlignment="Center"/>
                <ComboBox  Grid.Row="1" Grid.Column="1" x:Name="cb_escopo" Height="24"/>
                <TextBlock Grid.Row="1" Grid.Column="3" Text="Passo / Dígitos / Suf.:" VerticalAlignment="Center"/>
                <Grid Grid.Row="1" Grid.Column="4">
                    <Grid.ColumnDefinitions>
                        <ColumnDefinition Width="*"/>
                        <ColumnDefinition Width="5"/>
                        <ColumnDefinition Width="*"/>
                        <ColumnDefinition Width="5"/>
                        <ColumnDefinition Width="*"/>
                    </Grid.ColumnDefinitions>
                    <TextBox Grid.Column="0" x:Name="tb_passo" Height="24" Text="1" VerticalContentAlignment="Center" ToolTip="Incremento"/>
                    <TextBox Grid.Column="2" x:Name="tb_dig" Height="24" Text="2" VerticalContentAlignment="Center" ToolTip="Mínimo de dígitos (zeros à esquerda)"/>
                    <TextBox Grid.Column="4" x:Name="tb_suf" Height="24" Text="" VerticalContentAlignment="Center" ToolTip="Sufixo opcional"/>
                </Grid>

                <TextBlock Grid.Row="2" Grid.Column="0" Text="Parâmetro / Agrupamento:" VerticalAlignment="Center"/>
                <ComboBox  Grid.Row="2" Grid.Column="1" x:Name="cb_alvo" Height="24"/>
                <TextBlock Grid.Row="2" Grid.Column="3" Text="Importar Tabela:" VerticalAlignment="Center"/>
                <ComboBox  Grid.Row="2" Grid.Column="4" x:Name="cb_sched" Height="24"/>
            </Grid>
        </Border>

        <!-- Classificar / Agrupar -->
        <Border Grid.Row="2" Background="White" BorderBrush="#D0D7DE" BorderThickness="1" CornerRadius="4" Padding="10" Margin="0,0,0,8">
            <Grid>
                <Grid.ColumnDefinitions>
                    <ColumnDefinition Width="115"/>
                    <ColumnDefinition Width="*"/>
                    <ColumnDefinition Width="115"/>
                    <ColumnDefinition Width="20"/>
                    <ColumnDefinition Width="115"/>
                    <ColumnDefinition Width="*"/>
                    <ColumnDefinition Width="115"/>
                </Grid.ColumnDefinitions>
                <Grid.RowDefinitions>
                    <RowDefinition Height="24"/>
                    <RowDefinition Height="28"/>
                    <RowDefinition Height="28"/>
                </Grid.RowDefinitions>

                <TextBlock Grid.Row="0" Grid.Column="0" Grid.ColumnSpan="7" Text="Classificar / Ordenar sequência por (lógica da Tabela):"
                           FontWeight="SemiBold" Foreground="#1E293B" VerticalAlignment="Center"/>

                <TextBlock Grid.Row="1" Grid.Column="0" Text="1º Classificar por:" VerticalAlignment="Center"/>
                <ComboBox  Grid.Row="1" Grid.Column="1" x:Name="cb_c1" Height="24" Margin="0,0,6,0"/>
                <ComboBox  Grid.Row="1" Grid.Column="2" x:Name="cb_d1" Height="24"/>

                <TextBlock Grid.Row="1" Grid.Column="4" Text="2º Depois por:" VerticalAlignment="Center"/>
                <ComboBox  Grid.Row="1" Grid.Column="5" x:Name="cb_c2" Height="24" Margin="0,0,6,0"/>
                <ComboBox  Grid.Row="1" Grid.Column="6" x:Name="cb_d2" Height="24"/>

                <TextBlock Grid.Row="2" Grid.Column="0" Text="3º Depois por:" VerticalAlignment="Center"/>
                <ComboBox  Grid.Row="2" Grid.Column="1" x:Name="cb_c3" Height="24" Margin="0,0,6,0"/>
                <ComboBox  Grid.Row="2" Grid.Column="2" x:Name="cb_d3" Height="24"/>

                <TextBlock Grid.Row="2" Grid.Column="4" Text="4º Depois por:" VerticalAlignment="Center"/>
                <ComboBox  Grid.Row="2" Grid.Column="5" x:Name="cb_c4" Height="24" Margin="0,0,6,0"/>
                <ComboBox  Grid.Row="2" Grid.Column="6" x:Name="cb_d4" Height="24"/>
            </Grid>
        </Border>

        <!-- Barra de resumo + opção filtro tabela -->
        <Grid Grid.Row="3" Margin="0,0,0,6">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="Auto"/>
            </Grid.ColumnDefinitions>
            <TextBlock Grid.Column="0" x:Name="tb_resumo" Text="Pré-visualização:" FontWeight="SemiBold" Foreground="#0F172A" VerticalAlignment="Center"/>
            <CheckBox  Grid.Column="1" x:Name="ck_sched_filt" Content="Usar filtros da Tabela selecionada (se aplicável)" IsChecked="True" VerticalAlignment="Center"/>
        </Grid>

        <!-- Grade de Pré-visualização -->
        <ListView Grid.Row="4" x:Name="lv_prev" Margin="0,0,0,8" BorderBrush="#CBD5E1" BorderThickness="1" Background="White">
            <ListView.View>
                <GridView>
                    <GridViewColumn Header="Novo Cód." Width="82"  DisplayMemberBinding="{Binding NovoCod}"/>
                    <GridViewColumn Header="Atual"     Width="75"  DisplayMemberBinding="{Binding AtualCod}"/>
                    <GridViewColumn Header="L × A (cm)" Width="105" DisplayMemberBinding="{Binding Dimensoes}"/>
                    <GridViewColumn Header="Família / Tipo / Descrição" Width="390" DisplayMemberBinding="{Binding Descricao}"/>
                    <GridViewColumn Header="Qtd."      Width="55"  DisplayMemberBinding="{Binding Qtd}"/>
                </GridView>
            </ListView.View>
        </ListView>

        <!-- Rodapé -->
        <Grid Grid.Row="5">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="Auto"/>
            </Grid.ColumnDefinitions>
            <CheckBox Grid.Column="0" x:Name="ck_dup" Content="Silenciar aviso de Marca/Número duplicado ao aplicar" IsChecked="True" VerticalAlignment="Center"/>
            <StackPanel Grid.Column="1" Orientation="Horizontal">
                <Button x:Name="btn_cancel" Content="Cancelar" Width="95" Height="30" Margin="0,0,8,0"/>
                <Button x:Name="btn_ok" Content="Aplicar Renumeração em Lote" Width="210" Height="30"
                        IsDefault="True" Background="#0078D4" Foreground="White" FontWeight="SemiBold"/>
            </StackPanel>
        </Grid>
    </Grid>
</Window>
"""


def _natural_key(texto):
    s = unicode(texto or u"").strip().lower()
    if not s:
        return [(1, u"")]
    partes = re.split(u"([0-9]+)", s)
    res = []
    for p in partes:
        if not p:
            continue
        if p.isdigit():
            res.append((0, int(p)))
        else:
            res.append((1, p))
    return res


def _safe_bip(nome_bip):
    try:
        return getattr(BuiltInParameter, nome_bip, None)
    except Exception:
        return None


def _ler_param_double_mm(elems_ou_tipos, bip_names, txt_names):
    for obj in elems_ou_tipos:
        if obj is None:
            continue
        for bname in bip_names:
            bip = _safe_bip(bname)
            if bip is None:
                continue
            try:
                p = obj.get_Parameter(bip)
                if p is not None and p.HasValue and p.StorageType == StorageType.Double:
                    return round(p.AsDouble() / MM, 3)
            except Exception:
                pass
        for tname in txt_names:
            tlow = tname.lower()
            try:
                for p in obj.Parameters:
                    defn = getattr(p, "Definition", None)
                    pname = getattr(defn, "Name", None)
                    if pname and pname.strip().lower() == tlow and p.HasValue:
                        if p.StorageType == StorageType.Double:
                            return round(p.AsDouble() / MM, 3)
            except Exception:
                pass
    return 0.0


def _ler_param_str(elems_ou_tipos, bip_names, txt_names):
    for obj in elems_ou_tipos:
        if obj is None:
            continue
        for bname in bip_names:
            bip = _safe_bip(bname)
            if bip is None:
                continue
            try:
                p = obj.get_Parameter(bip)
                if p is not None and p.HasValue:
                    val = p.AsString()
                    if not val:
                        val = p.AsValueString()
                    if val:
                        return unicode(val).strip()
            except Exception:
                pass
        for tname in txt_names:
            tlow = tname.lower()
            try:
                for p in obj.Parameters:
                    defn = getattr(p, "Definition", None)
                    pname = getattr(defn, "Name", None)
                    if pname and pname.strip().lower() == tlow and p.HasValue:
                        val = p.AsString() or p.AsValueString()
                        if val:
                            return unicode(val).strip()
            except Exception:
                pass
    return u""


def _obter_fase_ref():
    try:
        if view is not None:
            p = view.get_Parameter(BuiltInParameter.VIEW_PHASE)
            if p is not None:
                ph = doc.GetElement(p.AsElementId())
                if ph is not None:
                    return ph
    except Exception:
        pass
    try:
        phases = list(doc.Phases)
        if phases:
            return phases[-1]
    except Exception:
        pass
    return None


def _nivel_info_elem(el):
    lvl = None
    try:
        lid = el.LevelId
        if lid is not None and lid != ElementId.InvalidElementId:
            lvl = doc.GetElement(lid)
    except Exception:
        pass
    if lvl is None:
        for bname in ("FAMILY_LEVEL_PARAM", "SCHEDULE_LEVEL_PARAM", "LEVEL_PARAM"):
            bip = _safe_bip(bname)
            if bip is None:
                continue
            try:
                p = el.get_Parameter(bip)
                if p is not None:
                    lvl = doc.GetElement(p.AsElementId())
                    if lvl is not None:
                        break
            except Exception:
                pass
    if lvl is not None:
        try:
            return (round(lvl.Elevation / MM, 2), _natural_key(_elem_name(lvl)))
        except Exception:
            pass
    try:
        bb = el.get_BoundingBox(None)
        if bb is not None:
            return (round(bb.Min.Z / MM, 2), [(1, u"")])
    except Exception:
        pass
    return (0.0, [(1, u"")])


def _room_info_elem(el, fase):
    if isinstance(el, Room):
        return (_natural_key(_room_number(el)), _natural_key(_room_name(el)))
    if fase is not None:
        for attr in ("ToRoom", "FromRoom", "Room"):
            try:
                prop = getattr(el, attr, None)
                if prop is not None:
                    r = prop[fase]
                    if r is not None:
                        return (_natural_key(_room_number(r)), _natural_key(_room_name(r)))
            except Exception:
                pass
    return ([(1, u"")], [(1, u"")])


def _pos_xy_elem(el):
    try:
        loc = getattr(el, "Location", None)
        if isinstance(loc, LocationPoint):
            pt = loc.Point
            return (round(-pt.Y / MM, 1), round(pt.X / MM, 1))
        if isinstance(loc, LocationCurve):
            pt = loc.Curve.Evaluate(0.5, True)
            return (round(-pt.Y / MM, 1), round(pt.X / MM, 1))
    except Exception:
        pass
    try:
        bb = el.get_BoundingBox(None)
        if bb is not None:
            cx = 0.5 * (bb.Min.X + bb.Max.X)
            cy = 0.5 * (bb.Min.Y + bb.Max.Y)
            return (round(-cy / MM, 1), round(cx / MM, 1))
    except Exception:
        pass
    return (0.0, 0.0)


def _ler_param_id_generico(el, el_type, param_id):
    if param_id is None or param_id == ElementId.InvalidElementId:
        return (1, [(1, u"")])
    for obj in (el, el_type):
        if obj is None:
            continue
        try:
            for p in obj.Parameters:
                if p.Id == param_id and p.HasValue:
                    if p.StorageType == StorageType.Double:
                        return (0, round(p.AsDouble(), 6))
                    if p.StorageType == StorageType.Integer:
                        return (0, float(p.AsInteger()))
                    val = p.AsString() or p.AsValueString() or u""
                    return (0, _natural_key(val))
        except Exception:
            pass
    return (1, [(1, u"")])


def _extrair_chave_criterio(crit_key, el_rep, el_type, fase, custom_pid=None):
    objs_t = (el_type, el_rep)
    objs_i = (el_rep, el_type)
    if crit_key == "width":
        return _ler_param_double_mm(
            objs_t,
            ("DOOR_WIDTH", "WINDOW_WIDTH", "FAMILY_WIDTH_PARAM", "CASEWORK_WIDTH", "FURNITURE_WIDTH"),
            (u"Largura", u"Width", u"Largura aproximada", u"Rough Width")
        )
    if crit_key == "height":
        return _ler_param_double_mm(
            objs_t,
            ("DOOR_HEIGHT", "WINDOW_HEIGHT", "FAMILY_HEIGHT_PARAM", "CASEWORK_HEIGHT", "FURNITURE_HEIGHT"),
            (u"Altura", u"Height", u"Altura aproximada", u"Rough Height")
        )
    if crit_key == "sill":
        return _ler_param_double_mm(
            objs_i,
            ("INSTANCE_SILL_HEIGHT_PARAM",),
            (u"Altura do peitoril", u"Peitoril", u"Sill Height")
        )
    if crit_key == "desc":
        return _natural_key(_ler_param_str(objs_t, ("ALL_MODEL_DESCRIPTION",), (u"Descrição", u"Description")))
    if crit_key == "comments":
        return _natural_key(_ler_param_str(objs_t, ("ALL_MODEL_TYPE_COMMENTS", "ALL_MODEL_INSTANCE_COMMENTS"),
                                           (u"Comentários", u"Comentários de tipo", u"Comments", u"Type Comments")))
    if crit_key == "fam_type":
        fn = u""
        try:
            fn = el_type.FamilyName if el_type is not None else u""
        except Exception:
            pass
        return (_natural_key(fn), _natural_key(_elem_name(el_type)))
    if crit_key == "type_name":
        return _natural_key(_elem_name(el_type))
    if crit_key == "level":
        return _nivel_info_elem(el_rep)
    if crit_key == "room":
        return _room_info_elem(el_rep, fase)
    if crit_key == "pos_xy":
        return _pos_xy_elem(el_rep)
    if crit_key == "custom" and custom_pid is not None:
        return _ler_param_id_generico(el_rep, el_type, custom_pid)
    return 0


def _tabelas_compativeis(bic):
    res = []
    cat_id = ElementId(bic) if bic is not None else None
    try:
        for vs in FilteredElementCollector(doc).OfClass(ViewSchedule).ToElements():
            try:
                if vs.IsTitleblockRevisionSchedule:
                    continue
                sc_cid = vs.Definition.CategoryId
                if cat_id is None or sc_cid == cat_id or sc_cid == ElementId.InvalidElementId:
                    res.append(vs)
            except Exception:
                pass
    except Exception:
        pass
    res.sort(key=lambda s: _natural_key(getattr(s, "Name", u"")))
    return res


def _mapear_field_para_criterio(sf):
    """Tenta mapear um ScheduleField para (crit_key, custom_pid_ou_None)."""
    try:
        pid = sf.ParameterId
    except Exception:
        pid = ElementId.InvalidElementId

    mapa_bips = [
        ("width",    ("DOOR_WIDTH", "WINDOW_WIDTH", "FAMILY_WIDTH_PARAM", "CASEWORK_WIDTH", "FURNITURE_WIDTH")),
        ("height",   ("DOOR_HEIGHT", "WINDOW_HEIGHT", "FAMILY_HEIGHT_PARAM", "CASEWORK_HEIGHT", "FURNITURE_HEIGHT")),
        ("sill",     ("INSTANCE_SILL_HEIGHT_PARAM",)),
        ("desc",     ("ALL_MODEL_DESCRIPTION",)),
        ("comments", ("ALL_MODEL_TYPE_COMMENTS", "ALL_MODEL_INSTANCE_COMMENTS")),
        ("fam_type", ("ELEM_FAMILY_AND_TYPE_PARAM",)),
        ("type_name",("ELEM_TYPE_PARAM", "SYMBOL_NAME_PARAM", "ALL_MODEL_TYPE_NAME")),
        ("level",    ("FAMILY_LEVEL_PARAM", "SCHEDULE_LEVEL_PARAM", "LEVEL_PARAM")),
    ]
    for ckey, bnames in mapa_bips:
        for bn in bnames:
            bip = _safe_bip(bn)
            if bip is not None and ElementId(bip) == pid:
                return (ckey, None)

    try:
        nm = (sf.GetName() or u"").strip().lower()
    except Exception:
        nm = u""

    if u"larg" in nm or u"width" in nm:
        return ("width", None)
    if u"alt" in nm or u"height" in nm:
        if u"peitoril" in nm or u"sill" in nm:
            return ("sill", None)
        return ("height", None)
    if u"peitoril" in nm or u"sill" in nm:
        return ("sill", None)
    if u"descri" in nm:
        return ("desc", None)
    if u"coment" in nm or u"comment" in nm:
        return ("comments", None)
    if u"fam" in nm and u"tipo" in nm:
        return ("fam_type", None)
    if nm in (u"tipo", u"type", u"nome do tipo"):
        return ("type_name", None)
    if u"nível" in nm or u"nivel" in nm or u"level" in nm or u"pavimento" in nm:
        return ("level", None)
    if u"ambiente" in nm or u"room" in nm:
        return ("room", None)

    if pid != ElementId.InvalidElementId:
        return ("custom", pid)
    return ("none", None)


def _coletar_elems_lote(bic, escopo_idx, sched=None, usar_filtros_sched=True):
    if escopo_idx == 2:
        sel = []
        cat_id = ElementId(bic) if bic is not None else None
        for eid in uidoc.Selection.GetElementIds():
            el = doc.GetElement(eid)
            if el is not None and el.Category is not None:
                if cat_id is None or el.Category.Id == cat_id:
                    sel.append(el)
        return sel

    col = None
    if escopo_idx == 0 and sched is not None and usar_filtros_sched:
        try:
            col = FilteredElementCollector(doc, sched.Id)
        except Exception:
            col = None
    if col is None:
        if escopo_idx == 1 and view is not None:
            col = FilteredElementCollector(doc, view.Id)
        else:
            col = FilteredElementCollector(doc)

    if bic is not None:
        col = col.OfCategory(bic)
    return list(col.WhereElementIsNotElementType().ToElements())


def _montar_itens_lote(elems, bic, bip, alvo_modo, crits_spec, custom_pids):
    """Agrupa (por Tipo ou Instância) e ordena conforme os 4 critérios estilo Tabela."""
    fase = _obter_fase_ref()
    grupos = {}
    ordem_chaves = []

    for el in elems:
        el_type = None
        tid = ElementId.InvalidElementId
        try:
            tid = el.GetTypeId()
            if tid is not None and tid != ElementId.InvalidElementId:
                el_type = doc.GetElement(tid)
        except Exception:
            el_type = None

        eh_por_tipo = (alvo_modo in (0, 2)) and (el_type is not None)
        if eh_por_tipo:
            gkey = ("T", str(tid))
        else:
            gkey = ("I", str(el.Id))

        if gkey not in grupos:
            grupos[gkey] = {
                "eh_por_tipo": eh_por_tipo,
                "el_type":     el_type,
                "elems":       [],
            }
            ordem_chaves.append(gkey)
        grupos[gkey]["elems"].append(el)

    itens = []
    for gkey in ordem_chaves:
        grp = grupos[gkey]
        el_rep = grp["elems"][0]
        el_type = grp["el_type"]
        eh_por_tipo = grp["eh_por_tipo"]

        # Dimensões L x A para exibição na grade
        w_mm = _ler_param_double_mm(
            (el_type, el_rep),
            ("DOOR_WIDTH", "WINDOW_WIDTH", "FAMILY_WIDTH_PARAM", "CASEWORK_WIDTH", "FURNITURE_WIDTH"),
            (u"Largura", u"Width")
        )
        h_mm = _ler_param_double_mm(
            (el_type, el_rep),
            ("DOOR_HEIGHT", "WINDOW_HEIGHT", "FAMILY_HEIGHT_PARAM", "CASEWORK_HEIGHT", "FURNITURE_HEIGHT"),
            (u"Altura", u"Height")
        )
        if w_mm > 0.0 and h_mm > 0.0:
            dim_txt = u"{:.0f} × {:.0f}".format(w_mm / 10.0, h_mm / 10.0)
        elif w_mm > 0.0:
            dim_txt = u"L {:.0f}".format(w_mm / 10.0)
        else:
            dim_txt = u"-"

        # Valor atual do parâmetro alvo
        atual_txt = u"-"
        if eh_por_tipo and el_type is not None:
            p_at = el_type.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_MARK)
            if p_at is not None and p_at.AsString():
                atual_txt = unicode(p_at.AsString())
        else:
            p_at = el_rep.get_Parameter(bip)
            if p_at is None:
                p_at = el_rep.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
            if p_at is not None and p_at.AsString():
                atual_txt = unicode(p_at.AsString())

        # Descrição legível
        if el_type is not None:
            fn = getattr(el_type, "FamilyName", u"") or u""
            tn = _elem_name(el_type)
            desc_p = _ler_param_str((el_type,), ("ALL_MODEL_DESCRIPTION",), (u"Descrição", u"Description"))
            base_desc = u"{} : {}".format(fn, tn).strip(u" :") if fn else tn
            if desc_p and desc_p.lower() not in base_desc.lower():
                desc_txt = u"{} — {}".format(desc_p, base_desc)
            else:
                desc_txt = base_desc or desc_p
        else:
            desc_txt = (u"{} {}".format(_room_number(el_rep), _room_name(el_rep))).strip() or _elem_name(el_rep)

        chaves_sort = []
        for idx, (ckey, _) in enumerate(crits_spec):
            cpid = custom_pids[idx] if idx < len(custom_pids) else None
            chaves_sort.append(_extrair_chave_criterio(ckey, el_rep, el_type, fase, cpid))

        itens.append({
            "eh_por_tipo": eh_por_tipo,
            "el_type":     el_type,
            "elems":       grp["elems"],
            "atual":       atual_txt,
            "dimensoes":   dim_txt,
            "descricao":   desc_txt,
            "qtd":         len(grp["elems"]),
            "keys":        chaves_sort,
            "tie":         (_natural_key(desc_txt), _pos_xy_elem(el_rep), el_rep.Id.Value if hasattr(el_rep.Id, "Value") else int(str(el_rep.Id))),
        })

    itens.sort(key=lambda x: x["tie"])
    for ci in (3, 2, 1, 0):
        ckey, desc_flag = crits_spec[ci]
        if ckey == "none":
            continue
        itens.sort(key=lambda x, k=ci: x["keys"][k], reverse=desc_flag)

    return itens


class RenumerarLoteWindow(forms.WPFWindow):
    def __init__(self, prefs):
        forms.WPFWindow.__init__(self, XAML_RENUM_LOTE, literal_string=True)
        self.confirmado = False
        self.resultado  = {}
        self.itens_ordenados = []
        self._bloqueado = True
        self._scheds_lista = []
        self._custom_pids = [None, None, None, None]

        self._cb_cat    = self.FindName("cb_cat")
        self._cb_escopo = self.FindName("cb_escopo")
        self._cb_alvo   = self.FindName("cb_alvo")
        self._cb_sched  = self.FindName("cb_sched")
        self._tb_pref   = self.FindName("tb_pref")
        self._tb_ini    = self.FindName("tb_ini")
        self._tb_passo  = self.FindName("tb_passo")
        self._tb_dig    = self.FindName("tb_dig")
        self._tb_suf    = self.FindName("tb_suf")
        self._tb_resumo = self.FindName("tb_resumo")
        self._ck_sf     = self.FindName("ck_sched_filt")
        self._ck_dup    = self.FindName("ck_dup")
        self._lv_prev   = self.FindName("lv_prev")

        self._cbs_crit = [self.FindName("cb_c1"), self.FindName("cb_c2"), self.FindName("cb_c3"), self.FindName("cb_c4")]
        self._cbs_dir  = [self.FindName("cb_d1"), self.FindName("cb_d2"), self.FindName("cb_d3"), self.FindName("cb_d4")]

        for nome_c, _, _, _, _ in _LOTE_CATS:
            self._cb_cat.Items.Add(nome_c)
        self._cb_cat.SelectedIndex = max(0, min(len(_LOTE_CATS) - 1, int(prefs.get("rl_cat", 0) or 0)))

        for esc in _LOTE_ESCOPOS:
            self._cb_escopo.Items.Add(esc)
        self._cb_escopo.SelectedIndex = max(0, min(len(_LOTE_ESCOPOS) - 1, int(prefs.get("rl_esc", 0) or 0)))

        for nome_a in _RENUM_ALVOS:
            self._cb_alvo.Items.Add(nome_a)
        self._cb_alvo.SelectedIndex = max(0, min(len(_RENUM_ALVOS) - 1, int(prefs.get("rl_alvo", 0) or 0)))

        defs_crit = [prefs.get("rl_c1", 1), prefs.get("rl_c2", 2), prefs.get("rl_c3", 4), prefs.get("rl_c4", 5)]
        defs_dir  = [prefs.get("rl_d1", 0), prefs.get("rl_d2", 0), prefs.get("rl_d3", 0), prefs.get("rl_d4", 0)]
        for i in range(4):
            for _, rot in _LOTE_CRITERIOS:
                self._cbs_crit[i].Items.Add(rot)
            self._cbs_crit[i].SelectedIndex = max(0, min(len(_LOTE_CRITERIOS) - 1, int(defs_crit[i] or 0)))
            for d in _LOTE_DIRS:
                self._cbs_dir[i].Items.Add(d)
            self._cbs_dir[i].SelectedIndex = max(0, min(1, int(defs_dir[i] or 0)))

        self._tb_pref.Text  = prefs.get("rl_pref", _LOTE_CATS[self._cb_cat.SelectedIndex][3])
        self._tb_ini.Text   = str(prefs.get("rl_ini", "1"))
        self._tb_passo.Text = str(prefs.get("rl_passo", "1"))
        self._tb_dig.Text   = str(prefs.get("rl_dig", "2"))
        self._tb_suf.Text   = prefs.get("rl_suf", u"")
        self._ck_sf.IsChecked  = bool(prefs.get("rl_sf", True))
        self._ck_dup.IsChecked = bool(prefs.get("rl_sil_dup", True))

        self._popular_tabelas_cat()

        self._cb_cat.SelectionChanged    += self._ao_mudar_cat
        self._cb_sched.SelectionChanged  += self._ao_escolher_tabela
        self._cb_escopo.SelectionChanged += self._ao_alterar_filtro
        self._cb_alvo.SelectionChanged   += self._ao_alterar_filtro
        self._ck_sf.Click                += self._ao_alterar_filtro
        for i in range(4):
            self._cbs_crit[i].SelectionChanged += self._ao_alterar_criterio_manual
            self._cbs_dir[i].SelectionChanged  += self._ao_alterar_filtro
        for tb in (self._tb_pref, self._tb_ini, self._tb_passo, self._tb_dig, self._tb_suf):
            tb.TextChanged += self._ao_alterar_filtro

        self.FindName("btn_ok").Click     += self.on_ok
        self.FindName("btn_cancel").Click += self.on_cancel

        self._bloqueado = False
        self._atualizar_preview_lista()

    def _popular_tabelas_cat(self):
        idx = max(0, self._cb_cat.SelectedIndex)
        bic = _LOTE_CATS[idx][1]
        self._scheds_lista = _tabelas_compativeis(bic)
        self._cb_sched.Items.Clear()
        self._cb_sched.Items.Add(u"(Critérios manuais abaixo)")
        for sc in self._scheds_lista:
            self._cb_sched.Items.Add(getattr(sc, "Name", u"Tabela"))
        self._cb_sched.SelectedIndex = 0

    def _ao_mudar_cat(self, sender, args):
        self._bloqueado = True
        idx = max(0, self._cb_cat.SelectedIndex)
        self._tb_pref.Text = _LOTE_CATS[idx][3]
        if not _LOTE_CATS[idx][4]:
            self._cb_alvo.SelectedIndex = 1
        self._custom_pids = [None, None, None, None]
        self._popular_tabelas_cat()
        self._bloqueado = False
        self._atualizar_preview_lista()

    def _ao_escolher_tabela(self, sender, args):
        if self._bloqueado:
            return
        sidx = self._cb_sched.SelectedIndex
        if sidx <= 0 or sidx - 1 >= len(self._scheds_lista):
            self._custom_pids = [None, None, None, None]
            self._atualizar_preview_lista()
            return

        sched = self._scheds_lista[sidx - 1]
        self._bloqueado = True
        try:
            defn = sched.Definition
            idx_cat = max(0, self._cb_cat.SelectedIndex)
            if _LOTE_CATS[idx_cat][4]:
                self._cb_alvo.SelectedIndex = 1 if defn.IsItemized else 0

            sgfields = list(defn.GetSortGroupFields())
            self._custom_pids = [None, None, None, None]
            crit_map_idx = dict((ck, i) for i, (ck, _) in enumerate(_LOTE_CRITERIOS))

            for slot in range(4):
                if slot < len(sgfields):
                    sgf = sgfields[slot]
                    sf = defn.GetField(sgf.FieldId)
                    ckey, cpid = _mapear_field_para_criterio(sf)
                    if ckey == "custom":
                        self._custom_pids[slot] = cpid
                        self._cbs_crit[slot].SelectedIndex = crit_map_idx.get("type_name", 0)
                    else:
                        self._cbs_crit[slot].SelectedIndex = crit_map_idx.get(ckey, 0)
                    self._cbs_dir[slot].SelectedIndex = 1 if sgf.SortOrder == ScheduleSortOrder.Descending else 0
                else:
                    self._cbs_crit[slot].SelectedIndex = 0
                    self._cbs_dir[slot].SelectedIndex = 0
        except Exception:
            pass
        self._bloqueado = False
        self._atualizar_preview_lista()

    def _ao_alterar_criterio_manual(self, sender, args):
        if self._bloqueado:
            return
        for i, cb in enumerate(self._cbs_crit):
            if cb is sender:
                self._custom_pids[i] = None
        self._atualizar_preview_lista()

    def _ao_alterar_filtro(self, sender, args):
        if self._bloqueado:
            return
        self._atualizar_preview_lista()

    def _ler_formato(self):
        ini = int(self._tb_ini.Text)
        pas = int(self._tb_passo.Text)
        dig = max(1, min(6, int(self._tb_dig.Text)))
        pref = self._tb_pref.Text or u""
        suf  = self._tb_suf.Text or u""
        return (pref, ini, pas, dig, suf)

    def _atualizar_preview_lista(self):
        dt = DataTable()
        for col in ("NovoCod", "AtualCod", "Dimensoes", "Descricao", "Qtd"):
            dt.Columns.Add(col)

        try:
            pref, ini, pas, dig, suf = self._ler_formato()
        except Exception:
            self._tb_resumo.Text = u"Pré-visualização: informe números inteiros em Início / Passo / Dígitos."
            self._lv_prev.ItemsSource = dt.DefaultView
            self.itens_ordenados = []
            return

        idx_cat = max(0, self._cb_cat.SelectedIndex)
        _, bic, bip, _, tem_tipo = _LOTE_CATS[idx_cat]
        escopo_idx = max(0, self._cb_escopo.SelectedIndex)
        alvo_modo = max(0, self._cb_alvo.SelectedIndex) if tem_tipo else 1

        sidx = self._cb_sched.SelectedIndex
        sched = self._scheds_lista[sidx - 1] if (sidx > 0 and sidx - 1 < len(self._scheds_lista)) else None

        crits_spec = []
        for i in range(4):
            ci = max(0, self._cbs_crit[i].SelectedIndex)
            ckey = "custom" if self._custom_pids[i] is not None else _LOTE_CRITERIOS[ci][0]
            desc_flag = (self._cbs_dir[i].SelectedIndex == 1)
            crits_spec.append((ckey, desc_flag))

        elems = _coletar_elems_lote(bic, escopo_idx, sched, bool(self._ck_sf.IsChecked))
        itens = _montar_itens_lote(elems, bic, bip, alvo_modo, crits_spec, self._custom_pids)

        n_cur = ini
        for it in itens:
            cod = u"{}{}{}".format(pref, str(n_cur).zfill(dig), suf)
            it["novo_cod"] = cod
            dt.Rows.Add(cod, it["atual"], it["dimensoes"], it["descricao"], unicode(it["qtd"]))
            n_cur += pas

        self.itens_ordenados = itens
        self._lv_prev.ItemsSource = dt.DefaultView
        if itens:
            self._tb_resumo.Text = u"Pré-visualização: {} grupos/itens ({} → {})".format(
                len(itens), itens[0]["novo_cod"], itens[-1]["novo_cod"]
            )
        else:
            self._tb_resumo.Text = u"Pré-visualização: nenhum elemento encontrado neste escopo."

    def on_cancel(self, sender, args):
        self.confirmado = False
        self.Close()

    def on_ok(self, sender, args):
        if not self.itens_ordenados:
            forms.alert("Não há elementos para renumerar neste escopo.", title="Aviso")
            return
        try:
            pref, ini, pas, dig, suf = self._ler_formato()
        except Exception:
            forms.alert("Informe números inteiros válidos para Início, Passo e Dígitos.", title="Aviso")
            return

        self.resultado = {
            "rl_cat":     max(0, self._cb_cat.SelectedIndex),
            "rl_esc":     max(0, self._cb_escopo.SelectedIndex),
            "rl_alvo":    max(0, self._cb_alvo.SelectedIndex),
            "rl_c1":      max(0, self._cbs_crit[0].SelectedIndex),
            "rl_c2":      max(0, self._cbs_crit[1].SelectedIndex),
            "rl_c3":      max(0, self._cbs_crit[2].SelectedIndex),
            "rl_c4":      max(0, self._cbs_crit[3].SelectedIndex),
            "rl_d1":      max(0, self._cbs_dir[0].SelectedIndex),
            "rl_d2":      max(0, self._cbs_dir[1].SelectedIndex),
            "rl_d3":      max(0, self._cbs_dir[2].SelectedIndex),
            "rl_d4":      max(0, self._cbs_dir[3].SelectedIndex),
            "rl_pref":    pref,
            "rl_ini":     ini,
            "rl_passo":   pas,
            "rl_dig":     dig,
            "rl_suf":     suf,
            "rl_sf":      bool(self._ck_sf.IsChecked),
            "rl_sil_dup": bool(self._ck_dup.IsChecked),
        }
        self.confirmado = True
        self.Close()


def run_renumerar_lote():
    global doc, uidoc, view
    uidoc = __revit__.ActiveUIDocument
    doc   = uidoc.Document
    view  = doc.ActiveView

    prefs = carregar_preferencias()
    win = RenumerarLoteWindow(prefs)
    win.ShowDialog()
    if not win.confirmado or not win.itens_ordenados:
        script.exit()

    op = win.resultado
    salvar_preferencias(op)

    _, bic, bip, _, tem_tipo = _LOTE_CATS[op["rl_cat"]]
    alvo_modo = op["rl_alvo"] if tem_tipo else 1

    atualizados_grupos = 0
    atualizados_params = 0
    erros = 0

    with Transaction(doc, u"Renumerar Automático em Lote") as t:
        t.Start()
        if op["rl_sil_dup"]:
            fopts = t.GetFailureHandlingOptions()
            fopts.SetFailuresPreprocessor(_DupMarkPreprocessor())
            t.SetFailureHandlingOptions(fopts)

        for it in win.itens_ordenados:
            cod = it["novo_cod"]
            params = []
            if it["eh_por_tipo"] and it["el_type"] is not None:
                pt = it["el_type"].get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_MARK)
                if pt is not None and not pt.IsReadOnly:
                    params.append(pt)
                if alvo_modo == 2:
                    for el in it["elems"]:
                        pi = el.get_Parameter(bip)
                        if pi is None or pi.IsReadOnly:
                            pi = el.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
                        if pi is not None and not pi.IsReadOnly:
                            params.append(pi)
            else:
                for el in it["elems"]:
                    pi = el.get_Parameter(bip)
                    if pi is None or pi.IsReadOnly:
                        pi = el.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
                    if pi is not None and not pi.IsReadOnly:
                        params.append(pi)

            gravou = False
            for pr in params:
                try:
                    if pr.Set(cod):
                        atualizados_params += 1
                        gravou = True
                except Exception:
                    erros += 1
            if gravou:
                atualizados_grupos += 1

        t.Commit()

    try:
        uidoc.RefreshActiveView()
    except Exception:
        pass

    msg = u"Renumeração em Lote concluída!\n\n✔ Itens/Tipos renumerados: {}\n✔ Sequência aplicada: {} até {}".format(
        atualizados_grupos,
        win.itens_ordenados[0]["novo_cod"],
        win.itens_ordenados[-1]["novo_cod"]
    )
    if erros:
        msg += u"\n⚠ Ocorrências ignoradas (somente leitura): {}".format(erros)
    forms.alert(msg, title="Renumerar Automático em Lote")
