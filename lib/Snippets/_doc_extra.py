# -*- coding: utf-8 -*-
"""Ferramentas adicionais do Painel Documentacao do AutoBIM:
1. Criar Vistas 2D / 3D / Elevacoes Internas por Ambiente
2. Centralizar Tags e Pontos de Referencia de Ambientes (Rooms)
3. Cotas de Nivel (Spot Elevation) Automaticas (Plantas e Cortes)
4. Duplicar Pranchas Completa + Auto-Montagem de Pranchas
"""

import re
import math
import json
import codecs
import os
import clr
clr.AddReference("System")
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")

from System.Collections.Generic import List
from System.Windows.Controls import CheckBox as WpfCheckBox
from Autodesk.Revit.DB import *
from Autodesk.Revit.DB.Architecture import Room, RoomTag
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
from pyrevit import forms, script

doc   = None
uidoc = None
view  = None

FT = 1.0 / 0.3048
MM = 1.0 / 304.8
SETTINGS_PATH = os.path.join(os.path.dirname(__file__), "_doc_extra_settings.json")


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


def _elem_name(el):
    if el is None:
        return u""
    try:
        n = Element.Name.GetValue(el)
        if n:
            return unicode(n)
    except Exception:
        pass
    for bip in (BuiltInParameter.SYMBOL_NAME_PARAM, BuiltInParameter.ALL_MODEL_TYPE_NAME):
        try:
            p = el.get_Parameter(bip)
            if p is not None and p.AsString():
                return unicode(p.AsString())
        except Exception:
            pass
    try:
        return unicode(el.Name)
    except Exception:
        return u""


def _room_number(r):
    try:
        p = r.get_Parameter(BuiltInParameter.ROOM_NUMBER)
        if p is not None and p.AsString():
            return unicode(p.AsString()).strip()
    except Exception:
        pass
    try:
        return unicode(getattr(r, "Number", u"") or u"").strip()
    except Exception:
        return u""


def _room_name(r):
    try:
        p = r.get_Parameter(BuiltInParameter.ROOM_NAME)
        if p is not None and p.AsString():
            return unicode(p.AsString()).strip()
    except Exception:
        pass
    try:
        return unicode(getattr(r, "Name", u"") or u"").strip()
    except Exception:
        return u""


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


def _nome_vista_unico(nomes_usados, base_nome):
    cand = base_nome.strip()
    for ch in (u"\\", u":", u"{", u"}", u"[", u"]", u"|", u";", u"<", u">", u"?", u"`", u"~"):
        cand = cand.replace(ch, u"-")
    if cand not in nomes_usados:
        nomes_usados.add(cand)
        return cand
    i = 2
    while True:
        alt = u"{} ({})".format(cand, i)
        if alt not in nomes_usados:
            nomes_usados.add(alt)
            return alt
        i += 1


def _coletar_nomes_vistas():
    nomes = set()
    try:
        for v in FilteredElementCollector(doc).OfClass(View).ToElements():
            try:
                if v.Name:
                    nomes.add(unicode(v.Name))
            except Exception:
                pass
    except Exception:
        pass
    return nomes


class _RoomOrTagFilter(ISelectionFilter):
    def AllowElement(self, elem):
        return isinstance(elem, (Room, RoomTag))

    def AllowReference(self, ref, pt):
        return True


def _selecionar_rooms(escopo_idx):
    """escopo_idx: 0=selecionados/clicar, 1=vista ativa, 2=todo projeto."""
    rooms = []
    seen = set()

    def _add_r(r):
        if isinstance(r, Room) and getattr(r, "Area", 0.0) > 1e-4:
            k = str(r.Id)
            if k not in seen:
                seen.add(k)
                rooms.append(r)

    if escopo_idx == 0:
        for eid in uidoc.Selection.GetElementIds():
            el = doc.GetElement(eid)
            if isinstance(el, RoomTag):
                el = el.Room
            _add_r(el)
        if not rooms:
            try:
                refs = uidoc.Selection.PickObjects(
                    ObjectType.Element, _RoomOrTagFilter(),
                    u"Selecione os Ambientes (ou Room Tags) e clique em Concluir"
                )
                for rf in refs:
                    el = doc.GetElement(rf.ElementId)
                    if isinstance(el, RoomTag):
                        el = el.Room
                    _add_r(el)
            except Exception:
                return []
    elif escopo_idx == 1:
        for el in FilteredElementCollector(doc, view.Id).OfCategory(BuiltInCategory.OST_Rooms).ToElements():
            _add_r(el)
    else:
        for el in FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Rooms).ToElements():
            _add_r(el)

    rooms.sort(key=lambda r: (_natural_key(_room_number(r)), _natural_key(_room_name(r))))
    return rooms


def _dist_ponto_segmento_2d(px, py, ax, ay, bx, by):
    vx = bx - ax
    vy = by - ay
    wx = px - ax
    wy = py - ay
    c1 = wx * vx + wy * vy
    if c1 <= 0.0:
        return math.hypot(px - ax, py - ay)
    c2 = vx * vx + vy * vy
    if c2 <= c1:
        return math.hypot(px - bx, py - by)
    t = c1 / c2
    return math.hypot(px - (ax + t * vx), py - (ay + t * vy))


def _melhor_ponto_interior_poly(pts_poly, bb_bounds, fn_inside):
    """Pura logica geometrica para achar o centro interior mais seguro (inclusive em L/U)."""
    min_x, max_x, min_y, max_y = bb_bounds
    cands = [(0.5 * (min_x + max_x), 0.5 * (min_y + max_y))]

    if pts_poly and len(pts_poly) >= 3:
        sx = sum(p[0] for p in pts_poly) / float(len(pts_poly))
        sy = sum(p[1] for p in pts_poly) / float(len(pts_poly))
        cands.append((sx, sy))

    nx = 9
    ny = 9
    dx = (max_x - min_x) / float(nx + 1)
    dy = (max_y - min_y) / float(ny + 1)
    if dx > 1e-6 and dy > 1e-6:
        for ix in range(1, nx + 1):
            for iy in range(1, ny + 1):
                cands.append((min_x + ix * dx, min_y + iy * dy))

    segs = []
    if pts_poly and len(pts_poly) >= 2:
        n = len(pts_poly)
        for i in range(n):
            a = pts_poly[i]
            b = pts_poly[(i + 1) % n]
            segs.append((a[0], a[1], b[0], b[1]))

    melhor = None
    melhor_score = -1e18
    cx_bb = 0.5 * (min_x + max_x)
    cy_bb = 0.5 * (min_y + max_y)

    for px, py in cands:
        if not fn_inside(px, py):
            continue
        if segs:
            dmin = min(_dist_ponto_segmento_2d(px, py, s[0], s[1], s[2], s[3]) for s in segs)
        else:
            dmin = min(px - min_x, max_x - px, py - min_y, max_y - py)
        # Prefere maior folga das paredes; em empate, favorece o centro do bounding box
        dist_centro = math.hypot(px - cx_bb, py - cy_bb)
        score = dmin - 0.05 * dist_centro
        if score > melhor_score:
            melhor_score = score
            melhor = (px, py)

    return melhor


def _centro_seguro_room(room):
    """Retorna XYZ garantidamente dentro da Room, afastado das paredes (seguro p/ L/U)."""
    bb = room.get_BoundingBox(None)
    loc_pt = None
    try:
        if isinstance(room.Location, LocationPoint):
            loc_pt = room.Location.Point
    except Exception:
        loc_pt = None

    z_lvl = loc_pt.Z if loc_pt is not None else (bb.Min.Z if bb is not None else 0.0)
    z_test = z_lvl + 0.5 * FT
    if bb is not None and bb.Max.Z > bb.Min.Z + 0.2 * FT:
        z_test = bb.Min.Z + 0.35 * (bb.Max.Z - bb.Min.Z)

    pts_poly = []
    try:
        loops = room.GetBoundarySegments(SpatialElementBoundaryOptions())
        if loops and len(loops) > 0:
            for seg in loops[0]:
                crv = seg.GetCurve()
                p0 = crv.GetEndPoint(0)
                pts_poly.append((p0.X, p0.Y))
    except Exception:
        pts_poly = []

    if bb is None:
        return loc_pt

    def _inside(x, y):
        try:
            return bool(room.IsPointInRoom(XYZ(x, y, z_test)))
        except Exception:
            return False

    best = _melhor_ponto_interior_poly(pts_poly, (bb.Min.X, bb.Max.X, bb.Min.Y, bb.Max.Y), _inside)
    if best is not None:
        return XYZ(best[0], best[1], z_lvl)
    if loc_pt is not None:
        return loc_pt
    return XYZ(0.5 * (bb.Min.X + bb.Max.X), 0.5 * (bb.Min.Y + bb.Max.Y), z_lvl)


# ==============================================================================
# 1. CRIAR VISTAS 2D / 3D / ELEVACOES INTERNAS POR AMBIENTE
# ==============================================================================

_VISTAS_ESCALAS = [
    (u"1:20", 20),
    (u"1:25", 25),
    (u"1:50", 50),
    (u"1:75", 75),
    (u"1:100", 100),
]

XAML_VISTAS_AMB = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Criar Vistas 2D / 3D / Elevações por Ambiente" Width="500" SizeToContent="Height"
        WindowStartupLocation="CenterScreen" ResizeMode="NoResize" Background="#F7F8FA">
    <StackPanel Margin="14">
        <Border Background="#EBF3FC" BorderBrush="#B8D4F5" BorderThickness="1" CornerRadius="4" Padding="9" Margin="0,0,0,10">
            <TextBlock TextWrapping="Wrap" FontWeight="SemiBold" Foreground="#1F3A57"
                       Text="Gera automaticamente plantas recortadas (CropBox), vistas 3D isométricas (Section Box) e as 4 elevações internas de cada ambiente."/>
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
                </Grid.RowDefinitions>

                <TextBlock Grid.Row="0" Grid.Column="0" Text="Ambientes a processar:" VerticalAlignment="Center"/>
                <ComboBox  Grid.Row="0" Grid.Column="1" x:Name="cb_escopo" Height="24"/>

                <TextBlock Grid.Row="1" Grid.Column="0" Text="Escala das vistas:" VerticalAlignment="Center"/>
                <ComboBox  Grid.Row="1" Grid.Column="1" x:Name="cb_escala" Height="24"/>

                <TextBlock Grid.Row="2" Grid.Column="0" Text="Margem de recorte (cm):" VerticalAlignment="Center"/>
                <TextBox   Grid.Row="2" Grid.Column="1" x:Name="tb_margem" Height="24" Text="35" VerticalContentAlignment="Center"/>

                <TextBlock Grid.Row="3" Grid.Column="0" Text="Prefixo no nome das vistas:" VerticalAlignment="Center"/>
                <TextBox   Grid.Row="3" Grid.Column="1" x:Name="tb_pref" Height="24" Text="DET-" VerticalContentAlignment="Center"/>
            </Grid>
        </Border>

        <Border Background="White" BorderBrush="#D0D7DE" BorderThickness="1" CornerRadius="4" Padding="10" Margin="0,0,0,8">
            <StackPanel>
                <TextBlock Text="Tipos de vista a gerar:" FontWeight="SemiBold" Margin="0,0,0,6"/>
                <CheckBox x:Name="ck_planta" Content="Planta Baixa recortada (CropBox ao redor do ambiente)" IsChecked="True" Margin="0,0,0,5"/>
                <CheckBox x:Name="ck_3d"     Content="Vista 3D Isométrica recortada (Section Box do ambiente)" IsChecked="True" Margin="0,0,0,5"/>
                <CheckBox x:Name="ck_elev"   Content="4 Elevações Internas (Norte, Sul, Leste e Oeste)" IsChecked="True" Margin="0,0,0,5"/>
                <CheckBox x:Name="ck_cropv"  Content="Manter borda da região de recorte visível (CropBoxVisible)" IsChecked="False"/>
            </StackPanel>
        </Border>

        <Border Background="White" BorderBrush="#D0D7DE" BorderThickness="1" CornerRadius="4" Padding="10" Margin="0,0,0,10">
            <Grid>
                <Grid.ColumnDefinitions>
                    <ColumnDefinition Width="190"/>
                    <ColumnDefinition Width="*"/>
                </Grid.ColumnDefinitions>
                <Grid.RowDefinitions>
                    <RowDefinition Height="28"/>
                    <RowDefinition Height="28"/>
                </Grid.RowDefinitions>
                <TextBlock Grid.Row="0" Grid.Column="0" Text="Modelo de Vista (Planta):" VerticalAlignment="Center"/>
                <ComboBox  Grid.Row="0" Grid.Column="1" x:Name="cb_tpl_pl" Height="24"/>

                <TextBlock Grid.Row="1" Grid.Column="0" Text="Modelo de Vista (Elevação):" VerticalAlignment="Center"/>
                <ComboBox  Grid.Row="1" Grid.Column="1" x:Name="cb_tpl_el" Height="24"/>
            </Grid>
        </Border>

        <StackPanel Orientation="Horizontal" HorizontalAlignment="Right">
            <Button x:Name="btn_cancel" Content="Cancelar" Width="95" Height="30" Margin="0,0,8,0"/>
            <Button x:Name="btn_ok" Content="Gerar Vistas dos Ambientes" Width="195" Height="30"
                    IsDefault="True" Background="#0078D4" Foreground="White" FontWeight="SemiBold"/>
        </StackPanel>
    </StackPanel>
</Window>
"""


def _listar_view_templates(vtype_set):
    res = []
    try:
        for v in FilteredElementCollector(doc).OfClass(View).ToElements():
            try:
                if v.IsTemplate and v.ViewType in vtype_set:
                    res.append(v)
            except Exception:
                pass
    except Exception:
        pass
    res.sort(key=lambda x: _natural_key(x.Name))
    return res


class VistasAmbienteWindow(forms.WPFWindow):
    def __init__(self, prefs, tpls_pl, tpls_el):
        forms.WPFWindow.__init__(self, XAML_VISTAS_AMB, literal_string=True)
        self.confirmado = False
        self.resultado  = {}
        self._tpls_pl = tpls_pl
        self._tpls_el = tpls_el

        self._cb_esc = self.FindName("cb_escopo")
        self._cb_sca = self.FindName("cb_escala")
        self._tb_mar = self.FindName("tb_margem")
        self._tb_prf = self.FindName("tb_pref")
        self._ck_pl  = self.FindName("ck_planta")
        self._ck_3d  = self.FindName("ck_3d")
        self._ck_el  = self.FindName("ck_elev")
        self._ck_cv  = self.FindName("ck_cropv")
        self._cb_tpl_pl = self.FindName("cb_tpl_pl")
        self._cb_tpl_el = self.FindName("cb_tpl_el")

        for s in (u"Selecionados (ou clicar na vista)", u"Todos da vista ativa", u"Todos do projeto"):
            self._cb_esc.Items.Add(s)
        self._cb_esc.SelectedIndex = max(0, min(2, int(prefs.get("va_esc", 0) or 0)))

        for lbl, _ in _VISTAS_ESCALAS:
            self._cb_sca.Items.Add(lbl)
        self._cb_sca.SelectedIndex = max(0, min(len(_VISTAS_ESCALAS) - 1, int(prefs.get("va_sca", 1) or 1)))

        self._tb_mar.Text = str(prefs.get("va_mar", "35"))
        self._tb_prf.Text = prefs.get("va_prf", u"DET-")
        self._ck_pl.IsChecked = bool(prefs.get("va_pl", True))
        self._ck_3d.IsChecked = bool(prefs.get("va_3d", True))
        self._ck_el.IsChecked = bool(prefs.get("va_el", True))
        self._ck_cv.IsChecked = bool(prefs.get("va_cv", False))

        self._cb_tpl_pl.Items.Add(u"(Sem modelo / padrão)")
        for t in tpls_pl:
            self._cb_tpl_pl.Items.Add(t.Name)
        self._cb_tpl_pl.SelectedIndex = 0

        self._cb_tpl_el.Items.Add(u"(Sem modelo / padrão)")
        for t in tpls_el:
            self._cb_tpl_el.Items.Add(t.Name)
        self._cb_tpl_el.SelectedIndex = 0

        self.FindName("btn_ok").Click     += self.on_ok
        self.FindName("btn_cancel").Click += self.on_cancel

    def on_cancel(self, sender, args):
        self.confirmado = False
        self.Close()

    def on_ok(self, sender, args):
        if not (self._ck_pl.IsChecked or self._ck_3d.IsChecked or self._ck_el.IsChecked):
            forms.alert("Marque ao menos um tipo de vista (Planta, 3D ou Elevações).", title="Aviso")
            return
        try:
            mar_cm = max(0.0, float((self._tb_mar.Text or "35").replace(",", ".")))
        except Exception:
            forms.alert("Informe um número válido para a margem (cm).", title="Aviso")
            return

        ip = self._cb_tpl_pl.SelectedIndex
        ie = self._cb_tpl_el.SelectedIndex
        self.resultado = {
            "va_esc":    max(0, self._cb_esc.SelectedIndex),
            "va_sca":    max(0, self._cb_sca.SelectedIndex),
            "va_mar":    mar_cm,
            "va_prf":    self._tb_prf.Text or u"",
            "va_pl":     bool(self._ck_pl.IsChecked),
            "va_3d":     bool(self._ck_3d.IsChecked),
            "va_el":     bool(self._ck_el.IsChecked),
            "va_cv":     bool(self._ck_cv.IsChecked),
            "tpl_pl":    self._tpls_pl[ip - 1] if ip > 0 and ip - 1 < len(self._tpls_pl) else None,
            "tpl_el":    self._tpls_el[ie - 1] if ie > 0 and ie - 1 < len(self._tpls_el) else None,
        }
        self.confirmado = True
        self.Close()


def _encontrar_vft(vf_enum):
    for vt in FilteredElementCollector(doc).OfClass(ViewFamilyType).ToElements():
        try:
            if vt.ViewFamily == vf_enum:
                return vt
        except Exception:
            pass
    return None


def _plano_para_nivel(level_id, vft_plan):
    if isinstance(view, ViewPlan) and view.ViewType == ViewType.FloorPlan and not view.IsTemplate:
        try:
            if view.GenLevel is not None and view.GenLevel.Id == level_id:
                return view
        except Exception:
            pass
    for vp in FilteredElementCollector(doc).OfClass(ViewPlan).ToElements():
        try:
            if not vp.IsTemplate and vp.ViewType == ViewType.FloorPlan:
                if vp.GenLevel is not None and vp.GenLevel.Id == level_id:
                    return vp
        except Exception:
            pass
    if vft_plan is not None and level_id is not None and level_id != ElementId.InvalidElementId:
        try:
            return ViewPlan.Create(doc, vft_plan.Id, level_id)
        except Exception:
            pass
    return None


def _rotulo_direcao_elev(vdir):
    if abs(vdir.Y) >= abs(vdir.X):
        return u"Norte" if vdir.Y > 0.0 else u"Sul"
    return u"Leste" if vdir.X > 0.0 else u"Oeste"


def _ajustar_crop_elevacao(elev, bb_mundo, marker_pt, mar_ft):
    """Recorta a elevação exatamente para os limites do ambiente + margem e ajusta Far Clip."""
    try:
        elev.CropBoxActive = True
        orig_bb = elev.CropBox
        t_inv = orig_bb.Transform.Inverse

        corners = []
        for x in (bb_mundo.Min.X - mar_ft, bb_mundo.Max.X + mar_ft):
            for y in (bb_mundo.Min.Y - mar_ft, bb_mundo.Max.Y + mar_ft):
                for z in (bb_mundo.Min.Z - mar_ft, bb_mundo.Max.Z + mar_ft):
                    corners.append(t_inv.OfPoint(XYZ(x, y, z)))

        novo_bb = BoundingBoxXYZ()
        novo_bb.Transform = orig_bb.Transform
        novo_bb.Min = XYZ(
            min(c.X for c in corners),
            min(c.Y for c in corners),
            orig_bb.Min.Z
        )
        novo_bb.Max = XYZ(
            max(c.X for c in corners),
            max(c.Y for c in corners),
            orig_bb.Max.Z
        )
        elev.CropBox = novo_bb

        # Profundidade (Far Clip) do marcador até a face do ambiente olhada
        vd = elev.ViewDirection
        dists = []
        for x in (bb_mundo.Min.X, bb_mundo.Max.X):
            for y in (bb_mundo.Min.Y, bb_mundo.Max.Y):
                dists.append((x - marker_pt.X) * vd.X + (y - marker_pt.Y) * vd.Y)
        far_dist = max(0.5 * FT, max(dists) + mar_ft)
        p_fc = elev.get_Parameter(BuiltInParameter.VIEWER_BOUND_OFFSET_FAR)
        if p_fc is not None and not p_fc.IsReadOnly:
            p_fc.Set(far_dist)
        p_fclip = elev.get_Parameter(BuiltInParameter.VIEWER_BOUND_FAR_CLIPPING)
        if p_fclip is not None and not p_fclip.IsReadOnly:
            p_fclip.Set(1)
    except Exception:
        pass


def run_vistas_ambiente():
    global doc, uidoc, view
    uidoc = __revit__.ActiveUIDocument
    doc   = uidoc.Document
    view  = doc.ActiveView

    prefs = _carregar_prefs()
    tpls_pl = _listar_view_templates(set([ViewType.FloorPlan]))
    tpls_el = _listar_view_templates(set([ViewType.Elevation]))

    win = VistasAmbienteWindow(prefs, tpls_pl, tpls_el)
    win.ShowDialog()
    if not win.confirmado:
        script.exit()

    op = win.resultado
    _salvar_prefs(dict((k, v) for k, v in op.items() if k not in ("tpl_pl", "tpl_el")))

    rooms = _selecionar_rooms(op["va_esc"])
    if not rooms:
        forms.alert("Nenhum ambiente válido foi selecionado ou encontrado.", title="Aviso")
        script.exit()

    vft_pl = _encontrar_vft(ViewFamily.FloorPlan)
    vft_3d = _encontrar_vft(ViewFamily.ThreeDimensional)
    vft_el = _encontrar_vft(ViewFamily.Elevation)
    escala = _VISTAS_ESCALAS[op["va_sca"]][1]
    mar_ft = (op["va_mar"] * 10.0) * MM
    nomes_usados = _coletar_nomes_vistas()

    criadas = {"pl": 0, "3d": 0, "el": 0}
    erros = 0

    with Transaction(doc, u"Criar Vistas por Ambiente") as t:
        t.Start()
        planos_cache = {}

        for r in rooms:
            bb = r.get_BoundingBox(None)
            if bb is None:
                continue
            r_num = _room_number(r)
            r_nom = _room_name(r) or u"Ambiente"
            base = u"{}{}".format(op["va_prf"], (u"{} - {}".format(r_num, r_nom) if r_num else r_nom))
            lvl_id = r.LevelId

            # 1) Planta Baixa Recortada
            if op["va_pl"] and vft_pl is not None and lvl_id != ElementId.InvalidElementId:
                try:
                    vp = ViewPlan.Create(doc, vft_pl.Id, lvl_id)
                    vp.Name = _nome_vista_unico(nomes_usados, u"{} - Planta".format(base))
                    if op["tpl_pl"] is not None:
                        vp.ViewTemplateId = op["tpl_pl"].Id
                    try:
                        vp.Scale = escala
                    except Exception:
                        pass
                    vp.CropBoxActive = True
                    vp.CropBoxVisible = op["va_cv"]
                    old_cb = vp.CropBox
                    cb = BoundingBoxXYZ()
                    cb.Transform = old_cb.Transform
                    cb.Min = XYZ(bb.Min.X - mar_ft, bb.Min.Y - mar_ft, old_cb.Min.Z)
                    cb.Max = XYZ(bb.Max.X + mar_ft, bb.Max.Y + mar_ft, old_cb.Max.Z)
                    vp.CropBox = cb
                    criadas["pl"] += 1
                except Exception:
                    erros += 1

            # 2) Vista 3D Isométrica Recortada
            if op["va_3d"] and vft_3d is not None:
                try:
                    v3d = View3D.CreateIsometric(doc, vft_3d.Id)
                    v3d.Name = _nome_vista_unico(nomes_usados, u"{} - 3D".format(base))
                    try:
                        v3d.Scale = escala
                    except Exception:
                        pass
                    sb = BoundingBoxXYZ()
                    z_max = max(bb.Max.Z, bb.Min.Z + 2.8 / 0.3048)
                    sb.Min = XYZ(bb.Min.X - mar_ft, bb.Min.Y - mar_ft, bb.Min.Z - 0.3 / 0.3048)
                    sb.Max = XYZ(bb.Max.X + mar_ft, bb.Max.Y + mar_ft, z_max + 0.3 / 0.3048)
                    v3d.SetSectionBox(sb)
                    v3d.IsSectionBoxActive = True
                    try:
                        ctr = XYZ(0.5 * (sb.Min.X + sb.Max.X), 0.5 * (sb.Min.Y + sb.Max.Y), 0.5 * (sb.Min.Z + sb.Max.Z))
                        fwd = XYZ(-1.0, 1.0, -0.75).Normalize()
                        upv = XYZ(-1.0, 1.0, 2.0).Normalize()
                        v3d.SetOrientation(ViewOrientation3D(ctr - fwd.Multiply(20.0), upv, fwd))
                    except Exception:
                        pass
                    criadas["3d"] += 1
                except Exception:
                    erros += 1

            # 3) 4 Elevações Internas (Norte, Sul, Leste, Oeste)
            if op["va_el"] and vft_el is not None:
                lkey = str(lvl_id)
                if lkey not in planos_cache:
                    planos_cache[lkey] = _plano_para_nivel(lvl_id, vft_pl)
                pl_ref = planos_cache[lkey]
                if pl_ref is not None:
                    try:
                        c_pt = _centro_seguro_room(r)
                        marker = ElevationMarker.CreateElevationMarker(doc, vft_el.Id, c_pt, escala)
                        for slot in range(4):
                            el_v = marker.CreateElevation(doc, pl_ref.Id, slot)
                            if el_v is not None:
                                rot_dir = _rotulo_direcao_elev(el_v.ViewDirection)
                                el_v.Name = _nome_vista_unico(nomes_usados, u"{} - Elev. {}".format(base, rot_dir))
                                if op["tpl_el"] is not None:
                                    try:
                                        el_v.ViewTemplateId = op["tpl_el"].Id
                                    except Exception:
                                        pass
                                try:
                                    el_v.Scale = escala
                                except Exception:
                                    pass
                                el_v.CropBoxVisible = op["va_cv"]
                                _ajustar_crop_elevacao(el_v, bb, c_pt, mar_ft)
                                criadas["el"] += 1
                    except Exception:
                        erros += 1
                else:
                    erros += 1

        t.Commit()

    msg = (
        u"Vistas por Ambiente criadas!\n\n"
        u"✔ Ambientes processados: {}\n"
        u"✔ Plantas baixas: {}\n"
        u"✔ Vistas 3D Isométricas: {}\n"
        u"✔ Elevações internas: {}"
    ).format(len(rooms), criadas["pl"], criadas["3d"], criadas["el"])
    if erros:
        msg += u"\n⚠ Ocorrências ignoradas: {}".format(erros)
    forms.alert(msg, title="Vistas por Ambiente")


# ==============================================================================
# 2. CENTRALIZAR TAGS DE AMBIENTE (ROOMS)
# ==============================================================================

XAML_CENT_TAGS = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Centralizar Tags de Ambiente (Rooms)" Width="470" SizeToContent="Height"
        WindowStartupLocation="CenterScreen" ResizeMode="NoResize" Background="#F7F8FA">
    <StackPanel Margin="14">
        <Border Background="#EBF3FC" BorderBrush="#B8D4F5" BorderThickness="1" CornerRadius="4" Padding="9" Margin="0,0,0,10">
            <TextBlock TextWrapping="Wrap" FontWeight="SemiBold" Foreground="#1F3A57"
                       Text="Reposiciona os pontos de referência das Rooms e suas Room Tags para o centro interno seguro de cada ambiente (inclusive formatos em L/U)."/>
        </Border>

        <Border Background="White" BorderBrush="#D0D7DE" BorderThickness="1" CornerRadius="4" Padding="10" Margin="0,0,0,10">
            <StackPanel>
                <TextBlock Text="Escopo de atuação:" FontWeight="SemiBold" Margin="0,0,0,4"/>
                <ComboBox x:Name="cb_escopo" Height="24" Margin="0,0,0,10"/>

                <CheckBox x:Name="ck_room_pt" Content="Centralizar também o ponto de referência (cruzinha) da Room" IsChecked="True" Margin="0,0,0,6"/>
                <CheckBox x:Name="ck_tags"    Content="Centralizar todas as Room Tags correspondentes" IsChecked="True" Margin="0,0,0,6"/>
                <CheckBox x:Name="ck_leader"  Content="Desligar linha de chamada (Leader) ao puxar para o centro" IsChecked="True"/>
            </StackPanel>
        </Border>

        <StackPanel Orientation="Horizontal" HorizontalAlignment="Right">
            <Button x:Name="btn_cancel" Content="Cancelar" Width="95" Height="30" Margin="0,0,8,0"/>
            <Button x:Name="btn_ok" Content="Centralizar Agora" Width="160" Height="30"
                    IsDefault="True" Background="#0078D4" Foreground="White" FontWeight="SemiBold"/>
        </StackPanel>
    </StackPanel>
</Window>
"""


class CentralizarTagsWindow(forms.WPFWindow):
    def __init__(self, prefs):
        forms.WPFWindow.__init__(self, XAML_CENT_TAGS, literal_string=True)
        self.confirmado = False
        self.resultado  = {}

        self._cb_esc  = self.FindName("cb_escopo")
        self._ck_rpt  = self.FindName("ck_room_pt")
        self._ck_tags = self.FindName("ck_tags")
        self._ck_lead = self.FindName("ck_leader")

        for s in (
            u"Tags visíveis na vista ativa",
            u"Todas as vistas do projeto (todas as plantas)",
            u"Somente ambientes selecionados (vista ativa)",
        ):
            self._cb_esc.Items.Add(s)
        self._cb_esc.SelectedIndex = max(0, min(2, int(prefs.get("ct_esc", 0) or 0)))
        self._ck_rpt.IsChecked  = bool(prefs.get("ct_rpt", True))
        self._ck_tags.IsChecked = bool(prefs.get("ct_tags", True))
        self._ck_lead.IsChecked = bool(prefs.get("ct_lead", True))

        self.FindName("btn_ok").Click     += self.on_ok
        self.FindName("btn_cancel").Click += self.on_cancel

    def on_cancel(self, sender, args):
        self.confirmado = False
        self.Close()

    def on_ok(self, sender, args):
        if not (self._ck_rpt.IsChecked or self._ck_tags.IsChecked):
            forms.alert("Marque ao menos uma ação (Room ou Room Tag).", title="Aviso")
            return
        self.resultado = {
            "ct_esc":  max(0, self._cb_esc.SelectedIndex),
            "ct_rpt":  bool(self._ck_rpt.IsChecked),
            "ct_tags": bool(self._ck_tags.IsChecked),
            "ct_lead": bool(self._ck_lead.IsChecked),
        }
        self.confirmado = True
        self.Close()


def run_centralizar_tags():
    global doc, uidoc, view
    uidoc = __revit__.ActiveUIDocument
    doc   = uidoc.Document
    view  = doc.ActiveView

    prefs = _carregar_prefs()
    win = CentralizarTagsWindow(prefs)
    win.ShowDialog()
    if not win.confirmado:
        script.exit()

    op = win.resultado
    _salvar_prefs(op)

    esc = op["ct_esc"]
    if esc == 0:
        tags = list(FilteredElementCollector(doc, view.Id).OfClass(SpatialElementTag).ToElements())
    elif esc == 1:
        tags = list(FilteredElementCollector(doc).OfClass(SpatialElementTag).ToElements())
    else:
        sel_rids = set()
        for eid in uidoc.Selection.GetElementIds():
            el = doc.GetElement(eid)
            if isinstance(el, RoomTag) and el.Room is not None:
                sel_rids.add(el.Room.Id)
            elif isinstance(el, Room):
                sel_rids.add(el.Id)
        if not sel_rids:
            forms.alert("Selecione ao menos um Ambiente ou Room Tag antes de usar o modo de seleção.", title="Aviso")
            script.exit()
        tags = []
        for tg in FilteredElementCollector(doc, view.Id).OfClass(SpatialElementTag).ToElements():
            if isinstance(tg, RoomTag) and tg.Room is not None and tg.Room.Id in sel_rids:
                tags.append(tg)

    centros_cache = {}
    rooms_movidos = set()
    n_rooms = 0
    n_tags  = 0

    with Transaction(doc, u"Centralizar Tags de Ambiente") as t:
        t.Start()

        if esc == 2 and op["ct_rpt"]:
            for eid in uidoc.Selection.GetElementIds():
                el = doc.GetElement(eid)
                if isinstance(el, RoomTag):
                    el = el.Room
                if isinstance(el, Room) and getattr(el, "Area", 0.0) > 1e-4:
                    rid = str(el.Id)
                    if rid not in centros_cache:
                        centros_cache[rid] = _centro_seguro_room(el)
                    ctr = centros_cache[rid]
                    if rid not in rooms_movidos and isinstance(el.Location, LocationPoint):
                        lp = el.Location.Point
                        dx, dy = ctr.X - lp.X, ctr.Y - lp.Y
                        if math.hypot(dx, dy) > 1.0 * MM:
                            el.Location.Move(XYZ(dx, dy, 0.0))
                            n_rooms += 1
                        rooms_movidos.add(rid)

        for tg in tags:
            if not isinstance(tg, RoomTag):
                continue
            r = tg.Room
            if r is None or getattr(r, "Area", 0.0) <= 1e-4:
                continue
            rid = str(r.Id)
            if rid not in centros_cache:
                centros_cache[rid] = _centro_seguro_room(r)
            ctr = centros_cache[rid]

            if op["ct_rpt"] and rid not in rooms_movidos:
                try:
                    if isinstance(r.Location, LocationPoint):
                        lp = r.Location.Point
                        dx, dy = ctr.X - lp.X, ctr.Y - lp.Y
                        if math.hypot(dx, dy) > 1.0 * MM:
                            r.Location.Move(XYZ(dx, dy, 0.0))
                            n_rooms += 1
                        rooms_movidos.add(rid)
                except Exception:
                    pass

            if op["ct_tags"]:
                try:
                    if op["ct_lead"]:
                        try:
                            tg.HasLeader = False
                        except Exception:
                            pass
                    if isinstance(tg.Location, LocationPoint):
                        tp = tg.Location.Point
                        dx, dy = ctr.X - tp.X, ctr.Y - tp.Y
                        if math.hypot(dx, dy) > 1.0 * MM:
                            tg.Location.Move(XYZ(dx, dy, 0.0))
                            n_tags += 1
                except Exception:
                    pass

        t.Commit()

    forms.alert(
        u"Centralização concluída!\n\n✔ Pontos de Room ajustados: {}\n✔ Room Tags centralizadas: {}".format(
            n_rooms, n_tags
        ),
        title="Centralizar Tags de Ambiente"
    )


# ==============================================================================
# 3. COTAS DE NIVEL (SPOT ELEVATION) — PULLDOWN: EM PLANTA E EM CORTE
# ==============================================================================

_MODOS_PISO_CAMADA = [
    u"Somente Pisos de Acabamento (camada superior sobre a laje)",
    u"Todos os Pisos e Lajes (inclusive estruturais)",
    u"Somente Lajes Estruturais (Estrutural marcado)",
]

XAML_COTAS_NIVEL_PLANTA = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Cotas de Nível em Planta" Width="505" SizeToContent="Height"
        WindowStartupLocation="CenterScreen" ResizeMode="NoResize" Background="#F7F8FA">
    <StackPanel Margin="14">
        <Border Background="#EBF3FC" BorderBrush="#B8D4F5" BorderThickness="1" CornerRadius="4" Padding="9" Margin="0,0,0,10">
            <TextBlock TextWrapping="Wrap" FontWeight="SemiBold" Foreground="#1F3A57"
                       Text="Insere Cotas de Nível em Plantas Baixas posicionando logo abaixo da Room Tag real de cada ambiente (ou com deslocamento fixo do centro), sempre priorizando o piso acabado superior."/>
        </Border>

        <Border Background="White" BorderBrush="#D0D7DE" BorderThickness="1" CornerRadius="4" Padding="10" Margin="0,0,0,8">
            <Grid>
                <Grid.ColumnDefinitions>
                    <ColumnDefinition Width="215"/>
                    <ColumnDefinition Width="*"/>
                </Grid.ColumnDefinitions>
                <Grid.RowDefinitions>
                    <RowDefinition Height="28"/>
                    <RowDefinition Height="28"/>
                    <RowDefinition Height="28"/>
                    <RowDefinition Height="28"/>
                    <RowDefinition Height="28"/>
                    <RowDefinition Height="28"/>
                </Grid.RowDefinitions>

                <TextBlock Grid.Row="0" Grid.Column="0" Text="Tipo de Cota de Nível:" VerticalAlignment="Center"/>
                <ComboBox  Grid.Row="0" Grid.Column="1" x:Name="cb_tipo" Height="24"/>

                <TextBlock Grid.Row="1" Grid.Column="0" Text="Referência de posicionamento:" VerticalAlignment="Center"/>
                <ComboBox  Grid.Row="1" Grid.Column="1" x:Name="cb_ancora" Height="24"/>

                <TextBlock Grid.Row="2" Grid.Column="0" Text="Folga abaixo da Tag (mm no papel):" VerticalAlignment="Center"/>
                <TextBox   Grid.Row="2" Grid.Column="1" x:Name="tb_folga_tag" Height="24" Text="2.0" VerticalContentAlignment="Center"
                           ToolTip="Distância visual abaixo da borda inferior da Room Tag (independente da escala e se a Tag tem ou não símbolos ○ △ □)"/>

                <TextBlock Grid.Row="3" Grid.Column="0" Text="Ajuste horizontal X (mm no papel):" VerticalAlignment="Center"/>
                <TextBox   Grid.Row="3" Grid.Column="1" x:Name="tb_dx_mm" Height="24" Text="-2.5" VerticalContentAlignment="Center"
                           ToolTip="Pequeno deslocamento horizontal para centralizar o conjunto símbolo + texto abaixo da Tag"/>

                <TextBlock Grid.Row="4" Grid.Column="0" Text="Desloc. Y do centro da Room (cm):" VerticalAlignment="Center"/>
                <TextBox   Grid.Row="4" Grid.Column="1" x:Name="tb_dy_centro" Height="24" Text="-95" VerticalContentAlignment="Center"
                           ToolTip="Usado no modo 'Centro do Ambiente' ou quando o ambiente não possui Room Tag visível na planta"/>

                <TextBlock Grid.Row="5" Grid.Column="0" Text="Camada de Piso alvo:" VerticalAlignment="Center"/>
                <ComboBox  Grid.Row="5" Grid.Column="1" x:Name="cb_camada" Height="24"/>
            </Grid>
        </Border>

        <Border Background="White" BorderBrush="#D0D7DE" BorderThickness="1" CornerRadius="4" Padding="10" Margin="0,0,0,10">
            <StackPanel>
                <CheckBox x:Name="ck_leader" Content="Usar linha de chamada (Leader)" IsChecked="False" Margin="0,0,0,5"/>
                <CheckBox x:Name="ck_evitar_dup" Content="Evitar criar cota se já existir outra a menos de 15 cm no ambiente" IsChecked="True"/>
            </StackPanel>
        </Border>

        <StackPanel Orientation="Horizontal" HorizontalAlignment="Right">
            <Button x:Name="btn_cancel" Content="Cancelar" Width="95" Height="30" Margin="0,0,8,0"/>
            <Button x:Name="btn_ok" Content="Aplicar em Planta" Width="165" Height="30"
                    IsDefault="True" Background="#0078D4" Foreground="White" FontWeight="SemiBold"/>
        </StackPanel>
    </StackPanel>
</Window>
"""

XAML_COTAS_NIVEL_CORTE = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Cotas de Nível em Corte / Elevação" Width="520" SizeToContent="Height"
        WindowStartupLocation="CenterScreen" ResizeMode="NoResize" Background="#F7F8FA">
    <StackPanel Margin="14">
        <Border Background="#EBF3FC" BorderBrush="#B8D4F5" BorderThickness="1" CornerRadius="4" Padding="9" Margin="0,0,0,10">
            <TextBlock TextWrapping="Wrap" FontWeight="SemiBold" Foreground="#1F3A57"
                       Text="Insere Cotas de Nível em Cortes e Elevações centralizadas em cada Ambiente cortado (mesmo quando vários ambientes compartilham o mesmo Piso) e identificando o piso acabado sobre a laje."/>
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
                </Grid.RowDefinitions>

                <TextBlock Grid.Row="0" Grid.Column="0" Text="Tipo de Cota de Nível:" VerticalAlignment="Center"/>
                <ComboBox  Grid.Row="0" Grid.Column="1" x:Name="cb_tipo" Height="24"/>

                <TextBlock Grid.Row="1" Grid.Column="0" Text="Camada de Piso (Piso × Laje):" VerticalAlignment="Center"/>
                <ComboBox  Grid.Row="1" Grid.Column="1" x:Name="cb_camada" Height="24"/>

                <TextBlock Grid.Row="2" Grid.Column="0" Text="Distribuição no Corte:" VerticalAlignment="Center"/>
                <ComboBox  Grid.Row="2" Grid.Column="1" x:Name="cb_dist" Height="24"/>

                <TextBlock Grid.Row="3" Grid.Column="0" Text="Tolerância p/ nível Z (cm):" VerticalAlignment="Center"/>
                <TextBox   Grid.Row="3" Grid.Column="1" x:Name="tb_tol_z" Height="24" Text="1.0" VerticalContentAlignment="Center"
                           ToolTip="Usada no modo por nível Z distinto ou para evitar cotas duplicadas no mesmo local (ex.: 1,0 cm)"/>
            </Grid>
        </Border>

        <Border Background="White" BorderBrush="#D0D7DE" BorderThickness="1" CornerRadius="4" Padding="10" Margin="0,0,0,10">
            <StackPanel>
                <TextBlock Text="Categorias e Opções:" FontWeight="SemiBold" Margin="0,0,0,6"/>
                <CheckBox x:Name="ck_pisos"  Content="Pisos (face superior acabada)" IsChecked="True" Margin="0,0,0,5"/>
                <CheckBox x:Name="ck_telhas" Content="Telhado mais alto (apenas ponto de cumeeira, sem vidraças inclinadas)" IsChecked="False" Margin="0,0,0,5"/>
                <CheckBox x:Name="ck_leader" Content="Usar linha de chamada (Leader)" IsChecked="False"/>
            </StackPanel>
        </Border>

        <StackPanel Orientation="Horizontal" HorizontalAlignment="Right">
            <Button x:Name="btn_cancel" Content="Cancelar" Width="95" Height="30" Margin="0,0,8,0"/>
            <Button x:Name="btn_ok" Content="Aplicar em Corte" Width="165" Height="30"
                    IsDefault="True" Background="#0078D4" Foreground="White" FontWeight="SemiBold"/>
        </StackPanel>
    </StackPanel>
</Window>
"""


def _listar_tipos_spot_elevation():
    res = []
    for st in FilteredElementCollector(doc).OfClass(SpotDimensionType).ToElements():
        try:
            if st.StyleType == DimensionStyleType.SpotElevation:
                res.append(st)
        except Exception:
            pass
    res.sort(key=lambda x: _natural_key(_elem_name(x)))
    return res


class CotasNivelPlantaWindow(forms.WPFWindow):
    def __init__(self, prefs, tipos):
        forms.WPFWindow.__init__(self, XAML_COTAS_NIVEL_PLANTA, literal_string=True)
        self.confirmado = False
        self.resultado  = {}
        self._tipos     = tipos

        self._cb_tipo   = self.FindName("cb_tipo")
        self._cb_ancora = self.FindName("cb_ancora")
        self._tb_folga  = self.FindName("tb_folga_tag")
        self._tb_dx     = self.FindName("tb_dx_mm")
        self._tb_dy     = self.FindName("tb_dy_centro")
        self._cb_camada = self.FindName("cb_camada")
        self._ck_ld     = self.FindName("ck_leader")
        self._ck_dup    = self.FindName("ck_evitar_dup")

        for tp in tipos:
            self._cb_tipo.Items.Add(_elem_name(tp))
        self._cb_tipo.SelectedIndex = max(0, min(len(tipos) - 1, int(prefs.get("cnp_tipo", 0) or 0)))

        for a_lbl in (
            u"Logo abaixo da Room Tag real na vista",
            u"Deslocamento fixo do centro do Ambiente (cm)",
            u"Centro de cada Piso visível (sem depender de Rooms)",
        ):
            self._cb_ancora.Items.Add(a_lbl)
        self._cb_ancora.SelectedIndex = max(0, min(2, int(prefs.get("cnp_ancora", 0) or 0)))

        for c_lbl in _MODOS_PISO_CAMADA:
            self._cb_camada.Items.Add(c_lbl)
        self._cb_camada.SelectedIndex = max(0, min(2, int(prefs.get("cnp_camada", 0) or 0)))

        self._tb_folga.Text   = str(prefs.get("cnp_folga", "2.0"))
        self._tb_dx.Text      = str(prefs.get("cnp_dx", "-2.5"))
        self._tb_dy.Text      = str(prefs.get("cnp_dy", "-95"))
        self._ck_ld.IsChecked  = bool(prefs.get("cnp_ld", False))
        self._ck_dup.IsChecked = bool(prefs.get("cnp_dup", True))

        self.FindName("btn_ok").Click     += self.on_ok
        self.FindName("btn_cancel").Click += self.on_cancel

    def on_cancel(self, sender, args):
        self.confirmado = False
        self.Close()

    def on_ok(self, sender, args):
        if not self._tipos:
            forms.alert("Nenhum tipo de Spot Elevation encontrado neste projeto.", title="Aviso")
            return
        try:
            folga_mm = float((self._tb_folga.Text or "2.0").replace(",", "."))
            dx_mm    = float((self._tb_dx.Text or "-2.5").replace(",", "."))
            dy_cm    = float((self._tb_dy.Text or "-95").replace(",", "."))
        except Exception:
            forms.alert("Informe números válidos para folga e deslocamentos.", title="Aviso")
            return

        idx_t = max(0, self._cb_tipo.SelectedIndex)
        self.resultado = {
            "cnp_tipo":   idx_t,
            "cnp_ancora": max(0, self._cb_ancora.SelectedIndex),
            "cnp_folga":  folga_mm,
            "cnp_dx":     dx_mm,
            "cnp_dy":     dy_cm,
            "cnp_camada": max(0, self._cb_camada.SelectedIndex),
            "cnp_ld":     bool(self._ck_ld.IsChecked),
            "cnp_dup":    bool(self._ck_dup.IsChecked),
            "spot_type":  self._tipos[idx_t],
        }
        self.confirmado = True
        self.Close()


class CotasNivelCorteWindow(forms.WPFWindow):
    def __init__(self, prefs, tipos):
        forms.WPFWindow.__init__(self, XAML_COTAS_NIVEL_CORTE, literal_string=True)
        self.confirmado = False
        self.resultado  = {}
        self._tipos     = tipos

        self._cb_tipo   = self.FindName("cb_tipo")
        self._cb_camada = self.FindName("cb_camada")
        self._cb_dist   = self.FindName("cb_dist")
        self._tb_tol_z  = self.FindName("tb_tol_z")
        self._ck_pi     = self.FindName("ck_pisos")
        self._ck_te     = self.FindName("ck_telhas")
        self._ck_ld     = self.FindName("ck_leader")

        for tp in tipos:
            self._cb_tipo.Items.Add(_elem_name(tp))
        self._cb_tipo.SelectedIndex = max(0, min(len(tipos) - 1, int(prefs.get("cnc_tipo", 0) or 0)))

        for c_lbl in _MODOS_PISO_CAMADA:
            self._cb_camada.Items.Add(c_lbl)
        self._cb_camada.SelectedIndex = max(0, min(2, int(prefs.get("cnc_camada", 0) or 0)))

        for d_lbl in (
            u"Centralizada em cada Ambiente (mesmo piso compartilhado)",
            u"1 cota por nível Z distinto (sem depender de Ambientes)",
            u"1 cota no centro visível de cada Piso cortado",
        ):
            self._cb_dist.Items.Add(d_lbl)
        self._cb_dist.SelectedIndex = max(0, min(2, int(prefs.get("cnc_dist", 0) or 0)))

        self._tb_tol_z.Text   = str(prefs.get("cnc_tol_z", "1.0"))
        self._ck_pi.IsChecked = bool(prefs.get("cnc_pi", True))
        self._ck_te.IsChecked = bool(prefs.get("cnc_te", False))
        self._ck_ld.IsChecked = bool(prefs.get("cnc_leader", False))

        self.FindName("btn_ok").Click     += self.on_ok
        self.FindName("btn_cancel").Click += self.on_cancel

    def on_cancel(self, sender, args):
        self.confirmado = False
        self.Close()

    def on_ok(self, sender, args):
        if not self._tipos:
            forms.alert("Nenhum tipo de Spot Elevation encontrado neste projeto.", title="Aviso")
            return
        if not (self._ck_pi.IsChecked or self._ck_te.IsChecked):
            forms.alert("Marque ao menos uma categoria (Pisos ou Telhados).", title="Aviso")
            return
        try:
            tol_cm = max(0.1, float((self._tb_tol_z.Text or "1.0").replace(",", ".")))
        except Exception:
            forms.alert("Informe um número válido para a tolerância Z (cm).", title="Aviso")
            return

        idx_t = max(0, self._cb_tipo.SelectedIndex)
        self.resultado = {
            "cnc_tipo":   idx_t,
            "cnc_camada": max(0, self._cb_camada.SelectedIndex),
            "cnc_dist":   max(0, self._cb_dist.SelectedIndex),
            "cnc_tol_z":  tol_cm,
            "cnc_pi":     bool(self._ck_pi.IsChecked),
            "cnc_te":     bool(self._ck_te.IsChecked),
            "cnc_leader": bool(self._ck_ld.IsChecked),
            "spot_type":  self._tipos[idx_t],
        }
        self.confirmado = True
        self.Close()


def _eh_piso_estrutural(fl):
    try:
        p = fl.get_Parameter(BuiltInParameter.FLOOR_PARAM_IS_STRUCTURAL)
        if p is not None and p.AsInteger() == 1:
            return True
    except Exception:
        pass
    return False


def _iter_faces_solidas(geom_elem):
    faces = []
    if geom_elem is None:
        return faces
    for obj in geom_elem:
        if isinstance(obj, Solid) and obj.Volume > 1e-6:
            for f in obj.Faces:
                faces.append(f)
        elif isinstance(obj, GeometryInstance):
            try:
                faces.extend(_iter_faces_solidas(obj.GetInstanceGeometry()))
            except Exception:
                pass
    return faces


def _melhor_face_horizontal(el, pref_top=True):
    """Retorna (Face, Reference) horizontal superior (ou inferior) com referencia valida."""
    if pref_top and isinstance(el, (Floor, RoofBase)):
        try:
            refs = list(HostObjectUtils.GetTopFaces(el))
            melhor_r = None
            melhor_f = None
            best_z = -1e9
            for rf in refs:
                f = el.GetGeometryObjectFromReference(rf)
                if isinstance(f, PlanarFace) and f.FaceNormal.Z > 0.7:
                    if f.Origin.Z > best_z:
                        best_z = f.Origin.Z
                        melhor_f = f
                        melhor_r = rf
            if melhor_f is not None and melhor_r is not None:
                return (melhor_f, melhor_r)
        except Exception:
            pass

    if not pref_top and isinstance(el, Ceiling):
        try:
            refs = list(HostObjectUtils.GetBottomFaces(el))
            for rf in refs:
                f = el.GetGeometryObjectFromReference(rf)
                if isinstance(f, PlanarFace) and abs(f.FaceNormal.Z) > 0.7:
                    return (f, rf)
        except Exception:
            pass

    opts = Options()
    opts.ComputeReferences = True
    opts.View = view
    best_f = None
    best_z = -1e9 if pref_top else 1e9
    for f in _iter_faces_solidas(el.get_Geometry(opts)):
        if isinstance(f, PlanarFace) and getattr(f, "Reference", None) is not None:
            if pref_top and f.FaceNormal.Z > 0.7 and f.Origin.Z > best_z:
                best_z = f.Origin.Z
                best_f = f
            elif (not pref_top) and f.FaceNormal.Z < -0.7 and f.Origin.Z < best_z:
                best_z = f.Origin.Z
                best_f = f
    if best_f is not None:
        return (best_f, best_f.Reference)
    return (None, None)


def _ponto_sobre_face_em_xy(face, pt_xy):
    try:
        z_plan = face.Origin.Z
        cand = XYZ(pt_xy.X, pt_xy.Y, z_plan)
        res = face.Project(cand)
        if res is not None:
            if res.Distance < 0.5 * FT and face.IsInside(res.UVPoint):
                return res.XYZPoint
    except Exception:
        pass
    return None


def _melhor_piso_no_ponto(pt_xy, floors_info, modo_camada):
    """Seleciona a camada de piso correta (ex.: sempre o piso acabado MAIS ALTO sobre a laje)."""
    cands_nao_est = []
    cands_est = []
    for item in floors_info:
        pt_hit = _ponto_sobre_face_em_xy(item["f"], pt_xy)
        if pt_hit is None:
            continue
        if item["is_struct"]:
            cands_est.append((pt_hit.Z, item["rf"], pt_hit))
        else:
            cands_nao_est.append((pt_hit.Z, item["rf"], pt_hit))

    if modo_camada == 0:
        if cands_nao_est:
            cands_nao_est.sort(key=lambda x: x[0], reverse=True)
            return cands_nao_est[0][1], cands_nao_est[0][2]
        return None, None
    elif modo_camada == 2:
        if cands_est:
            cands_est.sort(key=lambda x: x[0], reverse=True)
            return cands_est[0][1], cands_est[0][2]
        return None, None
    else:
        todos = cands_nao_est + cands_est
        if todos:
            todos.sort(key=lambda x: x[0], reverse=True)
            return todos[0][1], todos[0][2]
        return None, None


def _ponto_no_corte_para_face(face, el, v_view, outros_pisos=None):
    """Encontra um XYZ visivel no corte sobre a face (preferindo ponto NAO coberto por camada acima)."""
    try:
        bb = el.get_BoundingBox(v_view) or el.get_BoundingBox(None)
        if bb is None:
            return None
        org = v_view.Origin
        vdir = v_view.ViewDirection
        rdir = v_view.RightDirection
        z_f = face.Origin.Z

        # Calcula intervalo horizontal (right) do elemento no corte para amostrar no centro real
        u_vals = []
        for x in (bb.Min.X, bb.Max.X):
            for y in (bb.Min.Y, bb.Max.Y):
                u_vals.append((x - org.X) * rdir.X + (y - org.Y) * rdir.Y)
        u_mid = 0.5 * (min(u_vals) + max(u_vals))
        u_span = max(1.0 * FT, max(u_vals) - min(u_vals))

        u_offsets = [u_mid, u_mid + 0.15 * u_span, u_mid - 0.15 * u_span,
                     u_mid + 0.30 * u_span, u_mid - 0.30 * u_span, 0.0, 1.5 * FT, -1.5 * FT]
        v_offsets = [0.0, 0.4 * FT, -0.4 * FT, 1.2 * FT, -1.2 * FT]

        fallback_hit = None
        for t_v in v_offsets:
            for t_u in u_offsets:
                px = org.X + vdir.X * t_v + rdir.X * t_u
                py = org.Y + vdir.Y * t_v + rdir.Y * t_u
                res = face.Project(XYZ(px, py, z_f))
                if res is not None and face.IsInside(res.UVPoint):
                    pt_hit = res.XYZPoint
                    coberto = False
                    if outros_pisos:
                        for op_it in outros_pisos:
                            if op_it["el"].Id == el.Id:
                                continue
                            dz = op_it["z_top"] - pt_hit.Z
                            if 1.0 * MM < dz <= 0.35 / 0.3048:
                                if _ponto_sobre_face_em_xy(op_it["f"], pt_hit) is not None:
                                    coberto = True
                                    break
                    if not coberto:
                        return pt_hit
                    if fallback_hit is None:
                        fallback_hit = pt_hit

        cx = 0.5 * (bb.Min.X + bb.Max.X)
        cy = 0.5 * (bb.Min.Y + bb.Max.Y)
        res = face.Project(XYZ(cx, cy, z_f))
        if res is not None and face.IsInside(res.UVPoint):
            pt_hit = res.XYZPoint
            if not outros_pisos:
                return pt_hit
            coberto = False
            for op_it in outros_pisos:
                if op_it["el"].Id != el.Id and 1.0 * MM < (op_it["z_top"] - pt_hit.Z) <= 0.35 / 0.3048:
                    if _ponto_sobre_face_em_xy(op_it["f"], pt_hit) is not None:
                        coberto = True
                        break
            if not coberto:
                return pt_hit
        return fallback_hit
    except Exception:
        pass
    return None


def _coletar_pisos_para_nivel():
    floors_info = []
    for fl in FilteredElementCollector(doc, view.Id).OfClass(Floor).WhereElementIsNotElementType().ToElements():
        f, rf = _melhor_face_horizontal(fl, pref_top=True)
        if f is not None and rf is not None:
            floors_info.append({
                "el":        fl,
                "f":         f,
                "rf":        rf,
                "z_top":     f.Origin.Z,
                "is_struct": _eh_piso_estrutural(fl),
            })
    return floors_info


def run_cotas_nivel_planta():
    global doc, uidoc, view
    uidoc = __revit__.ActiveUIDocument
    doc   = uidoc.Document
    view  = doc.ActiveView

    if not isinstance(view, ViewPlan):
        forms.alert("Abra uma Planta Baixa ou Planta de Forro para usar Cotas de Nível em Planta.", title="Aviso")
        script.exit()

    tipos = _listar_tipos_spot_elevation()
    prefs = _carregar_prefs()
    win = CotasNivelPlantaWindow(prefs, tipos)
    win.ShowDialog()
    if not win.confirmado:
        script.exit()

    op = win.resultado
    _salvar_prefs(dict((k, v) for k, v in op.items() if k != "spot_type"))

    floors_info = _coletar_pisos_para_nivel()
    if not floors_info:
        forms.alert("Nenhum Piso visível foi encontrado nesta planta.", title="Aviso")
        script.exit()

    spot_type = op["spot_type"]
    esc_v     = max(1.0, float(view.Scale or 50))
    folga_ft  = (op["cnp_folga"] + 2.5) * MM * esc_v
    dx_tag_ft = op["cnp_dx"] * MM * esc_v
    dy_ctr_ft = (op["cnp_dy"] * 10.0) * MM
    dup_tol   = 150.0 * MM
    has_l     = bool(op["cnp_ld"])

    # Cotas de nivel ja existentes na vista para evitar duplicacao
    pts_exist = []
    if op["cnp_dup"]:
        for sd in FilteredElementCollector(doc, view.Id).OfClass(SpotDimension).ToElements():
            try:
                pts_exist.append(sd.Origin)
            except Exception:
                pass

    # Mapa RoomId -> RoomTag visivel nesta planta
    tags_por_room = {}
    for tg in FilteredElementCollector(doc, view.Id).OfClass(SpatialElementTag).ToElements():
        if isinstance(tg, RoomTag) and tg.Room is not None:
            tags_por_room[str(tg.Room.Id)] = tg

    criadas = 0
    erros   = 0

    with Transaction(doc, u"Cotas de Nível em Planta") as t:
        t.Start()

        if op["cnp_ancora"] in (0, 1):
            rooms = [r for r in FilteredElementCollector(doc, view.Id).OfCategory(BuiltInCategory.OST_Rooms).ToElements()
                     if isinstance(r, Room) and getattr(r, "Area", 0.0) > 1e-4]
            if not rooms:
                forms.alert("Nenhum Ambiente (Room) delimitado nesta vista.", title="Aviso")
                t.RollBack()
                script.exit()

            for r in rooms:
                ctr = _centro_seguro_room(r)
                tg = tags_por_room.get(str(r.Id))
                cands_xy = []

                if op["cnp_ancora"] == 0 and tg is not None:
                    bb_tg = None
                    try:
                        bb_tg = tg.get_BoundingBox(view)
                    except Exception:
                        bb_tg = None
                    if bb_tg is not None:
                        tx = 0.5 * (bb_tg.Min.X + bb_tg.Max.X) + dx_tag_ft
                        ty = bb_tg.Min.Y - folga_ft
                        cands_xy.append(XYZ(tx, ty, ctr.Z))
                        cands_xy.append(XYZ(tx, bb_tg.Max.Y + folga_ft, ctr.Z))

                cands_xy.append(XYZ(ctr.X + dx_tag_ft, ctr.Y + dy_ctr_ft, ctr.Z))
                cands_xy.append(ctr)

                achou = False
                for pt_try in cands_xy:
                    # Garante que nao saiu para outro ambiente/corredor
                    try:
                        z_t = pt_try.Z + 0.5 * FT
                        if not r.IsPointInRoom(XYZ(pt_try.X, pt_try.Y, z_t)):
                            continue
                    except Exception:
                        pass

                    rf, pt_hit = _melhor_piso_no_ponto(pt_try, floors_info, op["cnp_camada"])
                    if rf is None or pt_hit is None:
                        continue
                    if op["cnp_dup"] and any(math.hypot(pt_hit.X - p0.X, pt_hit.Y - p0.Y) < dup_tol for p0 in pts_exist):
                        achou = True
                        break

                    try:
                        bend = pt_hit + XYZ(0.35 * FT, 0.35 * FT, 0.0) if has_l else pt_hit
                        endp = bend + XYZ(0.55 * FT, 0.0, 0.0) if has_l else pt_hit
                        sd = doc.Create.NewSpotElevation(view, rf, pt_hit, bend, endp, pt_hit, has_l)
                        if sd is not None:
                            try:
                                sd.ChangeTypeId(spot_type.Id)
                            except Exception:
                                pass
                            pts_exist.append(pt_hit)
                            criadas += 1
                            achou = True
                    except Exception:
                        erros += 1
                    break

                # Fallback final no centro seguro se todas tentativas anteriores falharam
                if not achou:
                    rf, pt_hit = _melhor_piso_no_ponto(ctr, floors_info, op["cnp_camada"])
                    if rf is not None and pt_hit is not None:
                        if not (op["cnp_dup"] and any(math.hypot(pt_hit.X - p0.X, pt_hit.Y - p0.Y) < dup_tol for p0 in pts_exist)):
                            try:
                                bend = pt_hit + XYZ(0.35 * FT, 0.35 * FT, 0.0) if has_l else pt_hit
                                endp = bend + XYZ(0.55 * FT, 0.0, 0.0) if has_l else pt_hit
                                sd = doc.Create.NewSpotElevation(view, rf, pt_hit, bend, endp, pt_hit, has_l)
                                if sd is not None:
                                    try:
                                        sd.ChangeTypeId(spot_type.Id)
                                    except Exception:
                                        pass
                                    pts_exist.append(pt_hit)
                                    criadas += 1
                            except Exception:
                                erros += 1
        else:
            # Sem Rooms: 1 cota no centro de cada piso compativel
            for item in floors_info:
                if op["cnp_camada"] == 0 and item["is_struct"]:
                    continue
                if op["cnp_camada"] == 2 and not item["is_struct"]:
                    continue
                fl = item["el"]
                bb = fl.get_BoundingBox(view) or fl.get_BoundingBox(None)
                if bb is None:
                    continue
                pt_mid = XYZ(0.5 * (bb.Min.X + bb.Max.X), 0.5 * (bb.Min.Y + bb.Max.Y), item["z_top"])
                rf, pt_hit = _melhor_piso_no_ponto(pt_mid, floors_info, op["cnp_camada"])
                if rf is None or pt_hit is None:
                    pt_hit = _ponto_sobre_face_em_xy(item["f"], pt_mid) or item["f"].Origin
                    rf = item["rf"]
                if op["cnp_dup"] and any(math.hypot(pt_hit.X - p0.X, pt_hit.Y - p0.Y) < dup_tol for p0 in pts_exist):
                    continue
                try:
                    bend = pt_hit + XYZ(0.35 * FT, 0.35 * FT, 0.0) if has_l else pt_hit
                    endp = bend + XYZ(0.55 * FT, 0.0, 0.0) if has_l else pt_hit
                    sd = doc.Create.NewSpotElevation(view, rf, pt_hit, bend, endp, pt_hit, has_l)
                    if sd is not None:
                        try:
                            sd.ChangeTypeId(spot_type.Id)
                        except Exception:
                            pass
                        pts_exist.append(pt_hit)
                        criadas += 1
                except Exception:
                    erros += 1

        t.Commit()

    msg = u"Cotas de Nível em Planta concluídas!\n\n✔ Cotas inseridas: {}".format(criadas)
    if erros:
        msg += u"\n⚠ Pontos sem referência válida: {}".format(erros)
    forms.alert(msg, title="Cotas de Nível em Planta")


def _intervalos_corte_segmentos_2d(segs_uv, t_v, u_bounds=None, min_len=0.30):
    """Retorna os intervalos [u_a, u_b] onde a linha v = t_v corta o poligono em coordenadas (u, v)."""
    u_cross = []
    for u1, v1, u2, v2 in segs_uv:
        if (v1 <= t_v < v2) or (v2 <= t_v < v1):
            alpha = (t_v - v1) / float(v2 - v1)
            u_cross.append(u1 + alpha * (u2 - u1))
    if len(u_cross) < 2:
        return []
    u_cross.sort()
    res = []
    for i in range(0, len(u_cross) - 1, 2):
        ua = u_cross[i]
        ub = u_cross[i + 1]
        if u_bounds is not None:
            ua = max(ua, u_bounds[0])
            ub = min(ub, u_bounds[1])
        if (ub - ua) >= min_len:
            res.append((ua, ub))
    res.sort(key=lambda ab: ab[1] - ab[0], reverse=True)
    return res


def _rooms_cortados_no_corte(v_view):
    """Encontra os Ambientes (Rooms) efetivamente cortados pelo plano do corte e seu trecho horizontal [u_a, u_b]."""
    org = v_view.Origin
    rdir = v_view.RightDirection
    vdir = v_view.ViewDirection
    r_len = math.hypot(rdir.X, rdir.Y)
    v_len = math.hypot(vdir.X, vdir.Y)
    if r_len < 1e-6 or v_len < 1e-6:
        return []

    rx, ry = rdir.X / r_len, rdir.Y / r_len
    vx, vy = vdir.X / v_len, vdir.Y / v_len

    u_bounds = None
    z_bounds = None
    try:
        if v_view.CropBoxActive:
            cb = v_view.CropBox
            tr = cb.Transform
            us = []
            zs = []
            for cx in (cb.Min.X, cb.Max.X):
                for cy in (cb.Min.Y, cb.Max.Y):
                    for cz in (cb.Min.Z, cb.Max.Z):
                        pw = tr.OfPoint(XYZ(cx, cy, cz))
                        us.append((pw.X - org.X) * rx + (pw.Y - org.Y) * ry)
                        zs.append(pw.Z)
            u_bounds = (min(us), max(us))
            z_bounds = (min(zs), max(zs))
    except Exception:
        u_bounds = None
        z_bounds = None

    b_opts = SpatialElementBoundaryOptions()
    cand_rooms = []
    seen_rids = set()

    def _push_room(rm):
        if isinstance(rm, Room) and getattr(rm, "Area", 0.0) > 1e-4:
            k = str(rm.Id)
            if k not in seen_rids:
                seen_rids.add(k)
                cand_rooms.append(rm)

    for el in FilteredElementCollector(doc, v_view.Id).OfCategory(BuiltInCategory.OST_Rooms).ToElements():
        _push_room(el)
    for tg in FilteredElementCollector(doc, v_view.Id).OfClass(SpatialElementTag).ToElements():
        if isinstance(tg, RoomTag) and tg.Room is not None:
            _push_room(tg.Room)

    if not cand_rooms:
        v_phase = None
        try:
            pv = v_view.get_Parameter(BuiltInParameter.VIEW_PHASE)
            if pv is not None:
                v_phase = pv.AsElementId()
        except Exception:
            v_phase = None
        for el in FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Rooms).ToElements():
            if v_phase is not None and v_phase != ElementId.InvalidElementId:
                try:
                    pr = el.get_Parameter(BuiltInParameter.ROOM_PHASE)
                    if pr is not None and pr.AsElementId() != v_phase:
                        continue
                except Exception:
                    pass
            _push_room(el)

    rooms_res = []
    for r in cand_rooms:
        bb = r.get_BoundingBox(None)
        if bb is None:
            continue
        if z_bounds is not None and (bb.Max.Z < z_bounds[0] or bb.Min.Z > z_bounds[1]):
            continue

        segs_uv = []
        try:
            loops = r.GetBoundarySegments(b_opts)
            if loops:
                for loop in loops:
                    for seg in loop:
                        pts = list(seg.GetCurve().Tessellate())
                        for k in range(len(pts) - 1):
                            p0, p1 = pts[k], pts[k + 1]
                            u1 = (p0.X - org.X) * rx + (p0.Y - org.Y) * ry
                            v1 = (p0.X - org.X) * vx + (p0.Y - org.Y) * vy
                            u2 = (p1.X - org.X) * rx + (p1.Y - org.Y) * ry
                            v2 = (p1.X - org.X) * vx + (p1.Y - org.Y) * vy
                            segs_uv.append((u1, v1, u2, v2))
        except Exception:
            segs_uv = []
        if not segs_uv:
            continue

        z_probe = bb.Min.Z + 0.5 * FT
        if bb.Max.Z > bb.Min.Z + 0.2 * FT:
            z_probe = min(bb.Max.Z - 0.05 * FT, bb.Min.Z + max(0.35 * FT, 0.25 * (bb.Max.Z - bb.Min.Z)))

        melhor = None
        for t_v in (0.0, -0.15 * FT, 0.15 * FT):
            ivs = _intervalos_corte_segmentos_2d(segs_uv, t_v, u_bounds=u_bounds, min_len=0.30 * FT)
            for ua, ub in ivs:
                umid = 0.5 * (ua + ub)
                px = org.X + umid * rx + t_v * vx
                py = org.Y + umid * ry + t_v * vy
                try:
                    if r.IsPointInRoom(XYZ(px, py, z_probe)):
                        melhor = (ua, ub, umid, t_v)
                        break
                except Exception:
                    melhor = (ua, ub, umid, t_v)
                    break
            if melhor is not None:
                break

        if melhor is not None:
            rooms_res.append({
                "room":    r,
                "u_a":     melhor[0],
                "u_b":     melhor[1],
                "u_mid":   melhor[2],
                "t_v":     melhor[3],
                "z_min":   bb.Min.Z,
                "z_max":   bb.Max.Z,
                "z_probe": z_probe,
                "rx": rx, "ry": ry, "vx": vx, "vy": vy, "org": org,
            })

    rooms_res.sort(key=lambda it: (it["z_min"], it["u_mid"]))
    return rooms_res


def _cruzar_triangulo_plano_v(v0, v1, v2, d0, d1, d2):
    """Retorna pontos (x, y, z) onde as arestas do triangulo cruzam o plano d == 0."""
    pts = []
    pares = ((v0, d0, v1, d1), (v1, d1, v2, d2), (v2, d2, v0, d0))
    for a, da, b, db in pares:
        if (da <= 0.0 <= db) or (db <= 0.0 <= da):
            diff = db - da
            if abs(diff) < 1e-9:
                if abs(da) <= 1e-6:
                    pts.append((a[0], a[1], a[2]))
                if abs(db) <= 1e-6:
                    pts.append((b[0], b[1], b[2]))
            else:
                t = -da / float(diff)
                pts.append((
                    a[0] + t * (b[0] - a[0]),
                    a[1] + t * (b[1] - a[1]),
                    a[2] + t * (b[2] - a[2]),
                ))
    return pts


def _eh_vidraca_inclinada(roof):
    """Retorna True se o RoofBase for uma Vidraca Inclinada (Sloped Glazing / CurtainRoof)."""
    try:
        if getattr(roof, "CurtainGrid", None) is not None:
            return True
    except Exception:
        pass
    try:
        rt = doc.GetElement(roof.GetTypeId())
        if rt is not None:
            if hasattr(rt, "GetCompoundStructure") and rt.GetCompoundStructure() is None:
                return True
            fn = (getattr(rt, "FamilyName", u"") or u"").lower()
            tn = _elem_name(rt).lower()
            for kw in (u"vidra", u"glaz", u"curtain", u"cortina"):
                if kw in fn or kw in tn:
                    return True
    except Exception:
        pass
    return False


def _faces_superiores_telhado(roof, v_view):
    """Retorna lista de (Face, Reference) das faces superiores solidas do telhado."""
    res = []
    seen_refs = set()

    def _normal_up_ok(fc):
        try:
            if isinstance(fc, PlanarFace):
                return fc.FaceNormal.Z > 0.15
            bb_uv = fc.GetBoundingBox()
            if bb_uv is not None:
                uv_mid = 0.5 * (bb_uv.Min + bb_uv.Max)
                return fc.ComputeNormal(uv_mid).Z > 0.15
        except Exception:
            pass
        return False

    try:
        for rf in HostObjectUtils.GetTopFaces(roof):
            fc = roof.GetGeometryObjectFromReference(rf)
            if fc is not None and _normal_up_ok(fc):
                key = rf.ConvertToStableRepresentation(doc) if hasattr(rf, "ConvertToStableRepresentation") else str(id(fc))
                if key not in seen_refs:
                    seen_refs.add(key)
                    res.append((fc, rf))
    except Exception:
        pass

    if res:
        return res

    opts = Options()
    opts.ComputeReferences = True
    opts.View = v_view
    for fc in _iter_faces_solidas(roof.get_Geometry(opts)):
        rf = getattr(fc, "Reference", None)
        if rf is not None and _normal_up_ok(fc):
            res.append((fc, rf))
    return res


def _ponto_mais_alto_telhado_no_corte(v_view):
    """Encontra apenas o ponto mais alto visivel entre telhados solidos (ignorando vidracas inclinadas)."""
    org = v_view.Origin
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

    candidatos = []
    for roof in FilteredElementCollector(doc, v_view.Id).OfClass(RoofBase).WhereElementIsNotElementType().ToElements():
        if _eh_vidraca_inclinada(roof):
            continue
        faces_top = _faces_superiores_telhado(roof, v_view)
        for fc, rf in faces_top:
            try:
                mesh = fc.Triangulate()
            except Exception:
                mesh = None
            if mesh is None:
                continue

            for ti in range(mesh.NumTriangles):
                tri = mesh.get_Triangle(ti)
                v0 = tri.get_Vertex(0)
                v1 = tri.get_Vertex(1)
                v2 = tri.get_Vertex(2)
                c_tri = XYZ(
                    (v0.X + v1.X + v2.X) / 3.0,
                    (v0.Y + v1.Y + v2.Y) / 3.0,
                    (v0.Z + v1.Z + v2.Z) / 3.0,
                )
                d0, d1, d2 = _depth(v0), _depth(v1), _depth(v2)

                pts_corte = _cruzar_triangulo_plano_v(
                    (v0.X, v0.Y, v0.Z), (v1.X, v1.Y, v1.Z), (v2.X, v2.Y, v2.Z),
                    d0, d1, d2
                )
                for px, py, pz in pts_corte:
                    pw = XYZ(px, py, pz)
                    if _enquadrado(pw):
                        candidatos.append((pz, 1, pw, c_tri, fc, rf))

                for vx in (v0, v1, v2):
                    if _enquadrado(vx):
                        on_cut = 1 if abs(_depth(vx)) <= 0.10 * FT else 0
                        candidatos.append((vx.Z, on_cut, vx, c_tri, fc, rf))

    if not candidatos:
        return (None, None)

    candidatos.sort(key=lambda item: (round(item[0], 5), item[1]), reverse=True)
    for z_val, _on_cut, pw, c_tri, fc, rf in candidatos:
        tentativas = [pw]
        vec_in = c_tri - pw
        if vec_in.GetLength() > 1e-6:
            tentativas.append(pw + vec_in.Normalize() * (2.0 * MM))
        for p_try in tentativas:
            try:
                res = fc.Project(p_try)
                if res is not None and fc.IsInside(res.UVPoint):
                    return (rf, res.XYZPoint)
            except Exception:
                pass
    return (None, None)


def run_cotas_nivel_corte():
    global doc, uidoc, view
    uidoc = __revit__.ActiveUIDocument
    doc   = uidoc.Document
    view  = doc.ActiveView

    if not isinstance(view, ViewSection):
        forms.alert("Abra um Corte ou Elevação para usar Cotas de Nível em Corte.", title="Aviso")
        script.exit()

    tipos = _listar_tipos_spot_elevation()
    prefs = _carregar_prefs()
    win = CotasNivelCorteWindow(prefs, tipos)
    win.ShowDialog()
    if not win.confirmado:
        script.exit()

    op = win.resultado
    _salvar_prefs(dict((k, v) for k, v in op.items() if k != "spot_type"))

    spot_type = op["spot_type"]
    tol_z_ft  = (op["cnc_tol_z"] * 10.0) * MM
    has_l     = bool(op["cnc_leader"])
    criadas   = 0
    erros     = 0

    todos_pisos = _coletar_pisos_para_nivel() if op["cnc_pi"] else []
    rdir = view.RightDirection
    upv  = view.UpDirection
    org  = view.Origin

    def _u_em_vista(pt):
        return (pt.X - org.X) * rdir.X + (pt.Y - org.Y) * rdir.Y

    alvos = []
    usou_rooms = False

    if op["cnc_pi"]:
        modo_c = op["cnc_camada"]
        rooms_corte = _rooms_cortados_no_corte(view) if op["cnc_dist"] == 0 else []

        if op["cnc_dist"] == 0 and rooms_corte:
            usou_rooms = True
            for rc in rooms_corte:
                pisos_lvl = [
                    it for it in todos_pisos
                    if (rc["z_min"] - 0.60 / 0.3048) <= it["z_top"] <= (rc["z_min"] + 1.20 / 0.3048)
                ]
                if not pisos_lvl:
                    pisos_lvl = [
                        it for it in todos_pisos
                        if (rc["z_min"] - 0.60 / 0.3048) <= it["z_top"] <= rc["z_max"]
                    ] or todos_pisos

                span = rc["u_b"] - rc["u_a"]
                u_mid = rc["u_mid"]
                u_cands = [
                    u_mid,
                    u_mid - 0.12 * span,
                    u_mid + 0.12 * span,
                    u_mid - 0.28 * span,
                    u_mid + 0.28 * span,
                ]
                tv_cands = [rc["t_v"]]
                for tv_alt in (0.0, -0.25 * FT, 0.25 * FT):
                    if all(abs(tv_alt - tv0) > 1e-4 for tv0 in tv_cands):
                        tv_cands.append(tv_alt)

                achou_rc = False
                for tv in tv_cands:
                    for uc in u_cands:
                        px = rc["org"].X + uc * rc["rx"] + tv * rc["vx"]
                        py = rc["org"].Y + uc * rc["ry"] + tv * rc["vy"]
                        try:
                            if not rc["room"].IsPointInRoom(XYZ(px, py, rc["z_probe"])):
                                continue
                        except Exception:
                            pass
                        rf, pt_hit = _melhor_piso_no_ponto(XYZ(px, py, rc["z_min"]), pisos_lvl, modo_c)
                        if rf is None or pt_hit is None:
                            continue
                        if modo_c == 0 and any(
                            (not o["is_struct"])
                            and 1.0 * MM < (o["z_top"] - pt_hit.Z) <= 0.35 / 0.3048
                            and _ponto_sobre_face_em_xy(o["f"], pt_hit) is not None
                            for o in todos_pisos
                        ):
                            continue
                        alvos.append((pt_hit.Z, _u_em_vista(pt_hit), rf, pt_hit, True))
                        achou_rc = True
                        break
                    if achou_rc:
                        break

        if not usou_rooms:
            for it in todos_pisos:
                if modo_c == 0 and it["is_struct"]:
                    continue
                if modo_c == 2 and not it["is_struct"]:
                    continue
                outros = todos_pisos if modo_c == 0 else None
                pt_hit = _ponto_no_corte_para_face(it["f"], it["el"], view, outros_pisos=outros)
                if pt_hit is not None:
                    if modo_c == 0 and any(
                        o["el"].Id != it["el"].Id
                        and (not o["is_struct"])
                        and 1.0 * MM < (o["z_top"] - pt_hit.Z) <= 0.35 / 0.3048
                        and _ponto_sobre_face_em_xy(o["f"], pt_hit) is not None
                        for o in todos_pisos
                    ):
                        continue
                    alvos.append((pt_hit.Z, _u_em_vista(pt_hit), it["rf"], pt_hit, False))

    if op["cnc_te"]:
        rf_t, pt_t = _ponto_mais_alto_telhado_no_corte(view)
        if rf_t is not None and pt_t is not None:
            alvos.append((pt_t.Z, _u_em_vista(pt_t), rf_t, pt_t, False))

    if not alvos:
        forms.alert("Nenhum Piso acabado ou Telhado visível foi encontrado neste Corte.", title="Aviso")
        script.exit()

    alvos.sort(key=lambda item: (item[0], item[1]))
    esc_v   = max(1.0, float(view.Scale or 50))
    off_h   = 4.0 * MM * esc_v
    off_x   = 10.0 * MM * esc_v
    off_end = 10.0 * MM * esc_v
    dup_u   = 350.0 * MM

    criadas_pts = []
    zs_usados = []
    agrupar_z = (op["cnc_dist"] == 1) or (op["cnc_dist"] == 0 and not usou_rooms)

    with Transaction(doc, u"Cotas de Nível em Corte") as t:
        t.Start()
        for z_val, u_val, rf, pt_hit, from_room in alvos:
            if from_room:
                if any(abs(z_val - z0) < tol_z_ft and abs(u_val - u0) < dup_u for z0, u0 in criadas_pts):
                    continue
            elif agrupar_z:
                if any(abs(z_val - z0) < tol_z_ft for z0 in zs_usados):
                    continue
            try:
                bend = pt_hit + upv * off_h + rdir * off_x if has_l else pt_hit
                endp = bend + rdir * off_end if has_l else pt_hit
                sd = doc.Create.NewSpotElevation(view, rf, pt_hit, bend, endp, pt_hit, has_l)
                if sd is not None:
                    try:
                        sd.ChangeTypeId(spot_type.Id)
                    except Exception:
                        pass
                    criadas_pts.append((z_val, u_val))
                    if not from_room:
                        zs_usados.append(z_val)
                    criadas += 1
            except Exception:
                erros += 1
        t.Commit()

    msg = u"Cotas de Nível em Corte concluídas!\n\n✔ Cotas inseridas: {}".format(criadas)
    if erros:
        msg += u"\n⚠ Pontos sem referência válida: {}".format(erros)
    forms.alert(msg, title="Cotas de Nível em Corte")


def run_cotas_nivel():
    """Compatibilidade: direciona automaticamente para Planta ou Corte conforme vista ativa."""
    v = __revit__.ActiveUIDocument.Document.ActiveView
    if isinstance(v, ViewSection):
        run_cotas_nivel_corte()
    else:
        run_cotas_nivel_planta()


# ==============================================================================
# 4. DUPLICAR PRANCHAS COMPLETA + AUTO-MONTAGEM DE PRANCHAS
# ==============================================================================

_DUP_MODOS = [
    (u"Duplicar vistas com detalhamento (WithDetailing)", ViewDuplicateOption.WithDetailing),
    (u"Duplicar como dependentes (AsDependent)",         ViewDuplicateOption.AsDependent),
    (u"Duplicar sem detalhamento (Duplicate)",           ViewDuplicateOption.Duplicate),
    (u"Não duplicar vistas (só carimbo, tabelas, legendas e textos)", None),
]

XAML_PRANCHAS = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Pranchas: Duplicar Completa e Auto-Montagem" Width="620" Height="600"
        WindowStartupLocation="CenterScreen" ResizeMode="CanResize" Background="#F7F8FA">
    <Grid Margin="14">
        <Grid.RowDefinitions>
            <RowDefinition Height="*"/>
            <RowDefinition Height="Auto"/>
        </Grid.RowDefinitions>

        <TabControl x:Name="tabs_modo" Grid.Row="0" Margin="0,0,0,10">
            <!-- ABA 1: DUPLICAR PRANCHA -->
            <TabItem Header="Duplicar Prancha Completa">
                <StackPanel Margin="10">
                    <Border Background="#EBF3FC" BorderBrush="#B8D4F5" BorderThickness="1" CornerRadius="4" Padding="9" Margin="0,0,0,10">
                        <TextBlock TextWrapping="Wrap" FontWeight="SemiBold" Foreground="#1F3A57"
                                   Text="Copia a folha mantendo o Carimbo, Tabelas, Legendas, Textos/Linhas na folha e duplicando as Vistas nas posições originais."/>
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
                            </Grid.RowDefinitions>

                            <TextBlock Grid.Row="0" Grid.Column="0" Text="Prancha de origem:" VerticalAlignment="Center"/>
                            <ComboBox  Grid.Row="0" Grid.Column="1" x:Name="cb_orig_sheet" Height="24"/>

                            <TextBlock Grid.Row="1" Grid.Column="0" Text="Número inicial da cópia:" VerticalAlignment="Center"/>
                            <TextBox   Grid.Row="1" Grid.Column="1" x:Name="tb_dup_num" Height="24" Text="A02" VerticalContentAlignment="Center"/>

                            <TextBlock Grid.Row="2" Grid.Column="0" Text="Nome da nova prancha:" VerticalAlignment="Center"/>
                            <TextBox   Grid.Row="2" Grid.Column="1" x:Name="tb_dup_nome" Height="24" Text="Planta - Cópia" VerticalContentAlignment="Center"/>

                            <TextBlock Grid.Row="3" Grid.Column="0" Text="Quantidade de cópias:" VerticalAlignment="Center"/>
                            <TextBox   Grid.Row="3" Grid.Column="1" x:Name="tb_dup_qtd" Height="24" Text="1" VerticalContentAlignment="Center"/>

                            <TextBlock Grid.Row="4" Grid.Column="0" Text="Modo das Vistas (Viewports):" VerticalAlignment="Center"/>
                            <ComboBox  Grid.Row="4" Grid.Column="1" x:Name="cb_dup_vmodo" Height="24"/>
                        </Grid>
                    </Border>

                    <Border Background="White" BorderBrush="#D0D7DE" BorderThickness="1" CornerRadius="4" Padding="10">
                        <StackPanel>
                            <CheckBox x:Name="ck_cp_sched" Content="Copiar Tabelas (Schedules) na mesma posição" IsChecked="True" Margin="0,0,0,5"/>
                            <CheckBox x:Name="ck_cp_leg"   Content="Copiar Legendas (Legends) na mesma posição" IsChecked="True" Margin="0,0,0,5"/>
                            <CheckBox x:Name="ck_cp_anot"  Content="Copiar Textos, Linhas e Símbolos desenhados diretamente na folha" IsChecked="True"/>
                        </StackPanel>
                    </Border>
                </StackPanel>
            </TabItem>

            <!-- ABA 2: AUTO-MONTAGEM -->
            <TabItem Header="Auto-Montagem de Vistas em Pranchas">
                <Grid Margin="10">
                    <Grid.RowDefinitions>
                        <RowDefinition Height="Auto"/>
                        <RowDefinition Height="Auto"/>
                        <RowDefinition Height="Auto"/>
                        <RowDefinition Height="*"/>
                    </Grid.RowDefinitions>

                    <Border Grid.Row="0" Background="#EBF3FC" BorderBrush="#B8D4F5" BorderThickness="1" CornerRadius="4" Padding="9" Margin="0,0,0,8">
                        <TextBlock TextWrapping="Wrap" FontWeight="SemiBold" Foreground="#1F3A57"
                                   Text="Distribui automaticamente múltiplas vistas selecionadas em novas pranchas em grade organizada, respeitando a área do carimbo."/>
                    </Border>

                    <Border Grid.Row="1" Background="White" BorderBrush="#D0D7DE" BorderThickness="1" CornerRadius="4" Padding="10" Margin="0,0,0,8">
                        <Grid>
                            <Grid.ColumnDefinitions>
                                <ColumnDefinition Width="175"/>
                                <ColumnDefinition Width="*"/>
                            </Grid.ColumnDefinitions>
                            <Grid.RowDefinitions>
                                <RowDefinition Height="28"/>
                                <RowDefinition Height="28"/>
                                <RowDefinition Height="28"/>
                                <RowDefinition Height="28"/>
                            </Grid.RowDefinitions>

                            <TextBlock Grid.Row="0" Grid.Column="0" Text="Carimbo (TitleBlock):" VerticalAlignment="Center"/>
                            <ComboBox  Grid.Row="0" Grid.Column="1" x:Name="cb_tb" Height="24"/>

                            <TextBlock Grid.Row="1" Grid.Column="0" Text="Número / Prefixo pranchas:" VerticalAlignment="Center"/>
                            <Grid Grid.Row="1" Grid.Column="1">
                                <Grid.ColumnDefinitions>
                                    <ColumnDefinition Width="110"/>
                                    <ColumnDefinition Width="6"/>
                                    <ColumnDefinition Width="*"/>
                                </Grid.ColumnDefinitions>
                                <TextBox Grid.Column="0" x:Name="tb_am_num" Height="24" Text="PR-01" VerticalContentAlignment="Center"/>
                                <TextBox Grid.Column="2" x:Name="tb_am_nome" Height="24" Text="Detalhamento Automático" VerticalContentAlignment="Center"/>
                            </Grid>

                            <TextBlock Grid.Row="2" Grid.Column="0" Text="Reserva direita p/ carimbo (cm):" VerticalAlignment="Center"/>
                            <Grid Grid.Row="2" Grid.Column="1">
                                <Grid.ColumnDefinitions>
                                    <ColumnDefinition Width="80"/>
                                    <ColumnDefinition Width="12"/>
                                    <ColumnDefinition Width="Auto"/>
                                    <ColumnDefinition Width="6"/>
                                    <ColumnDefinition Width="60"/>
                                </Grid.ColumnDefinitions>
                                <TextBox   Grid.Column="0" x:Name="tb_am_car" Height="24" Text="18" VerticalContentAlignment="Center"/>
                                <TextBlock Grid.Column="2" Text="Folga entre vistas (cm):" VerticalAlignment="Center"/>
                                <TextBox   Grid.Column="4" x:Name="tb_am_gap" Height="24" Text="2" VerticalContentAlignment="Center"/>
                            </Grid>

                            <TextBlock Grid.Row="3" Grid.Column="0" Text="Filtrar vistas disponíveis:" VerticalAlignment="Center"/>
                            <ComboBox  Grid.Row="3" Grid.Column="1" x:Name="cb_am_filtro" Height="24"/>
                        </Grid>
                    </Border>

                    <StackPanel Grid.Row="2" Orientation="Horizontal" Margin="0,0,0,4">
                        <TextBlock Text="Selecione as vistas para distribuir:" FontWeight="SemiBold" VerticalAlignment="Center" Margin="0,0,12,0"/>
                        <Button x:Name="btn_all"  Content="Marcar Todas" Width="92" Height="22" Margin="0,0,6,0"/>
                        <Button x:Name="btn_none" Content="Desmarcar"    Width="82" Height="22"/>
                    </StackPanel>

                    <ListBox Grid.Row="3" x:Name="lb_vistas" BorderBrush="#CBD5E1" BorderThickness="1" Background="White"/>
                </Grid>
            </TabItem>
        </TabControl>

        <StackPanel Grid.Row="1" Orientation="Horizontal" HorizontalAlignment="Right">
            <Button x:Name="btn_cancel" Content="Cancelar" Width="95" Height="30" Margin="0,0,8,0"/>
            <Button x:Name="btn_ok" Content="Executar" Width="165" Height="30"
                    IsDefault="True" Background="#0078D4" Foreground="White" FontWeight="SemiBold"/>
        </StackPanel>
    </Grid>
</Window>
"""


def _listar_sheets():
    res = []
    for vs in FilteredElementCollector(doc).OfClass(ViewSheet).ToElements():
        try:
            if not vs.IsPlaceholder:
                res.append(vs)
        except Exception:
            pass
    res.sort(key=lambda s: _natural_key(s.SheetNumber))
    return res


def _listar_titleblocks():
    res = []
    for fs in FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_TitleBlocks).WhereElementIsElementType().ToElements():
        res.append(fs)
    res.sort(key=lambda f: _natural_key(_elem_name(f)))
    return res


def _listar_vistas_disponiveis(filtro_idx):
    vtypes_permit = set([
        ViewType.FloorPlan, ViewType.CeilingPlan, ViewType.Elevation,
        ViewType.Section, ViewType.ThreeD, ViewType.DraftingView, ViewType.Detail
    ])
    vistas = []
    for v in FilteredElementCollector(doc).OfClass(View).ToElements():
        try:
            if v.IsTemplate or v.ViewType not in vtypes_permit:
                continue
            if filtro_idx == 2 and v.ViewType != ViewType.FloorPlan:
                continue
            if filtro_idx == 3 and v.ViewType not in (ViewType.Elevation, ViewType.Section):
                continue
            if filtro_idx == 4 and v.ViewType != ViewType.ThreeD:
                continue
            if filtro_idx == 5 and v.ViewType != ViewType.DraftingView:
                continue
            vistas.append(v)
        except Exception:
            pass

    if filtro_idx == 1:
        # Remove vistas já colocadas em alguma prancha
        usadas = set()
        for vp in FilteredElementCollector(doc).OfClass(Viewport).ToElements():
            usadas.add(str(vp.ViewId))
        vistas = [v for v in vistas if str(v.Id) not in usadas]

    vistas.sort(key=lambda x: (_natural_key(str(x.ViewType)), _natural_key(x.Name)))
    return vistas


def _incrementar_codigo(base_cod, offset):
    if offset == 0:
        return base_cod
    m = re.match(u"^(.*?)([0-9]+)$", base_cod.strip())
    if m:
        pref = m.group(1)
        num_str = m.group(2)
        novo = int(num_str) + offset
        return u"{}{}".format(pref, str(novo).zfill(len(num_str)))
    return u"{}-{}".format(base_cod, offset + 1)


class PranchasAutoWindow(forms.WPFWindow):
    def __init__(self, prefs, sheets, tbs):
        forms.WPFWindow.__init__(self, XAML_PRANCHAS, literal_string=True)
        self.confirmado = False
        self.resultado  = {}
        self._sheets = sheets
        self._tbs    = tbs

        self._tabs       = self.FindName("tabs_modo")
        self._cb_orig    = self.FindName("cb_orig_sheet")
        self._tb_dnum    = self.FindName("tb_dup_num")
        self._tb_dnome   = self.FindName("tb_dup_nome")
        self._tb_dqtd    = self.FindName("tb_dup_qtd")
        self._cb_vmodo   = self.FindName("cb_dup_vmodo")
        self._ck_sched   = self.FindName("ck_cp_sched")
        self._ck_leg     = self.FindName("ck_cp_leg")
        self._ck_anot    = self.FindName("ck_cp_anot")

        self._cb_tb      = self.FindName("cb_tb")
        self._tb_am_num  = self.FindName("tb_am_num")
        self._tb_am_nome = self.FindName("tb_am_nome")
        self._tb_am_car  = self.FindName("tb_am_car")
        self._tb_am_gap  = self.FindName("tb_am_gap")
        self._cb_filtro  = self.FindName("cb_am_filtro")
        self._lb_vistas  = self.FindName("lb_vistas")

        for sh in sheets:
            self._cb_orig.Items.Add(u"{} - {}".format(sh.SheetNumber, sh.Name))
        if sheets:
            self._cb_orig.SelectedIndex = 0
            self._ao_mudar_sheet(None, None)

        for lbl, _ in _DUP_MODOS:
            self._cb_vmodo.Items.Add(lbl)
        self._cb_vmodo.SelectedIndex = max(0, min(len(_DUP_MODOS) - 1, int(prefs.get("pr_vmodo", 0) or 0)))

        for tb in tbs:
            fn = getattr(tb, "FamilyName", u"") or u""
            tn = _elem_name(tb)
            self._cb_tb.Items.Add(u"{} : {}".format(fn, tn) if fn else tn)
        if tbs:
            self._cb_tb.SelectedIndex = 0

        for f_lbl in (
            u"Todas as vistas elegíveis",
            u"Apenas vistas ainda NÃO inseridas em pranchas",
            u"Somente Plantas Baixas",
            u"Somente Cortes e Elevações",
            u"Somente Vistas 3D",
            u"Somente Vistas de Desenho / Legendas",
        ):
            self._cb_filtro.Items.Add(f_lbl)
        self._cb_filtro.SelectedIndex = max(0, min(5, int(prefs.get("pr_filt", 1) or 1)))

        self._tb_dqtd.Text    = str(prefs.get("pr_dqtd", "1"))
        self._tb_am_num.Text  = prefs.get("pr_am_num", u"PR-01")
        self._tb_am_nome.Text = prefs.get("pr_am_nome", u"Detalhamento Automático")
        self._tb_am_car.Text  = str(prefs.get("pr_am_car", "18"))
        self._tb_am_gap.Text  = str(prefs.get("pr_am_gap", "2"))

        self._cb_orig.SelectionChanged   += self._ao_mudar_sheet
        self._cb_filtro.SelectionChanged += self._popular_vistas
        self.FindName("btn_all").Click   += self._marcar_todas
        self.FindName("btn_none").Click  += self._desmarcar_todas
        self.FindName("btn_ok").Click    += self.on_ok
        self.FindName("btn_cancel").Click += self.on_cancel

        self._popular_vistas(None, None)

    def _ao_mudar_sheet(self, sender, args):
        idx = self._cb_orig.SelectedIndex
        if 0 <= idx < len(self._sheets):
            sh = self._sheets[idx]
            self._tb_dnum.Text  = _incrementar_codigo(sh.SheetNumber, 1)
            self._tb_dnome.Text = u"{} - Cópia".format(sh.Name)

    def _popular_vistas(self, sender, args):
        self._lb_vistas.Items.Clear()
        fidx = max(0, self._cb_filtro.SelectedIndex)
        for v in _listar_vistas_disponiveis(fidx):
            chk = WpfCheckBox()
            chk.Content = u"[{}] {}".format(str(v.ViewType), v.Name)
            chk.Tag = v
            chk.IsChecked = True
            self._lb_vistas.Items.Add(chk)

    def _marcar_todas(self, sender, args):
        for item in self._lb_vistas.Items:
            item.IsChecked = True

    def _desmarcar_todas(self, sender, args):
        for item in self._lb_vistas.Items:
            item.IsChecked = False

    def on_cancel(self, sender, args):
        self.confirmado = False
        self.Close()

    def on_ok(self, sender, args):
        modo_aba = self._tabs.SelectedIndex
        if modo_aba == 0:
            if not self._sheets:
                forms.alert("Nenhuma prancha encontrada para duplicar.", title="Aviso")
                return
            try:
                qtd = max(1, min(50, int(self._tb_dqtd.Text)))
            except Exception:
                forms.alert("Informe um número inteiro válido em Quantidade de cópias.", title="Aviso")
                return
            self.resultado = {
                "modo":      0,
                "orig":      self._sheets[max(0, self._cb_orig.SelectedIndex)],
                "num_ini":   (self._tb_dnum.Text or u"A02").strip(),
                "nome_base": (self._tb_dnome.Text or u"Prancha").strip(),
                "qtd":       qtd,
                "vmodo_idx": max(0, self._cb_vmodo.SelectedIndex),
                "cp_sched":  bool(self._ck_sched.IsChecked),
                "cp_leg":    bool(self._ck_leg.IsChecked),
                "cp_anot":   bool(self._ck_anot.IsChecked),
            }
        else:
            if not self._tbs:
                forms.alert("Nenhum Carimbo (TitleBlock) carregado no projeto.", title="Aviso")
                return
            vistas_sel = []
            for item in self._lb_vistas.Items:
                if item.IsChecked:
                    vistas_sel.append(item.Tag)
            if not vistas_sel:
                forms.alert("Marque pelo menos uma vista na lista para distribuir nas pranchas.", title="Aviso")
                return
            try:
                car_cm = max(0.0, float((self._tb_am_car.Text or "18").replace(",", ".")))
                gap_cm = max(0.5, float((self._tb_am_gap.Text or "2").replace(",", ".")))
            except Exception:
                forms.alert("Informe valores numéricos para reserva de carimbo e folga.", title="Aviso")
                return
            self.resultado = {
                "modo":      1,
                "tb":        self._tbs[max(0, self._cb_tb.SelectedIndex)],
                "num_ini":   (self._tb_am_num.Text or u"PR-01").strip(),
                "nome_base": (self._tb_am_nome.Text or u"Detalhamento").strip(),
                "car_cm":    car_cm,
                "gap_cm":    gap_cm,
                "vistas":    vistas_sel,
                "filtro_i":  max(0, self._cb_filtro.SelectedIndex),
            }
        self.confirmado = True
        self.Close()


def _duplicar_prancha_completa(op):
    src_sh = op["orig"]
    numeros_usados = set(sh.SheetNumber for sh in _listar_sheets())
    nomes_vistas = _coletar_nomes_vistas()
    dup_opt = _DUP_MODOS[op["vmodo_idx"]][1]

    # TitleBlock original da prancha fonte
    src_tb = None
    for fi in FilteredElementCollector(doc, src_sh.Id).OfCategory(BuiltInCategory.OST_TitleBlocks).WhereElementIsNotElementType().ToElements():
        src_tb = fi
        break
    tb_type_id = src_tb.GetTypeId() if src_tb is not None else ElementId.InvalidElementId

    # Tabelas, Viewports e anotações 2D da prancha origem
    src_scheds = list(FilteredElementCollector(doc, src_sh.Id).OfClass(ScheduleSheetInstance).ToElements())
    src_vps = [doc.GetElement(vp_id) for vp_id in src_sh.GetAllViewports()]
    src_vps = [vp for vp in src_vps if isinstance(vp, Viewport)]

    anot_ids = List[ElementId]()
    for el in FilteredElementCollector(doc, src_sh.Id).WhereElementIsNotElementType().ToElements():
        if isinstance(el, (ScheduleSheetInstance, Viewport)):
            continue
        if el.Category is not None and el.Category.Id == ElementId(BuiltInCategory.OST_TitleBlocks):
            continue
        if isinstance(el, (CurveElement, TextElement, AnnotationSymbol)) or (
            el.Category is not None and el.Category.CategoryType == CategoryType.Annotation
        ):
            anot_ids.Add(el.Id)

    criadas = []
    with Transaction(doc, u"Duplicar Prancha Completa") as t:
        t.Start()
        for k in range(op["qtd"]):
            num_cand = _incrementar_codigo(op["num_ini"], k)
            while num_cand in numeros_usados:
                num_cand = _incrementar_codigo(num_cand, 1)
            numeros_usados.add(num_cand)

            nome_cand = op["nome_base"] if op["qtd"] == 1 else u"{} ({})".format(op["nome_base"], k + 1)
            nova_sh = ViewSheet.Create(doc, tb_type_id)
            try:
                nova_sh.SheetNumber = num_cand
            except Exception:
                pass
            try:
                nova_sh.Name = nome_cand
            except Exception:
                pass

            # Copia posição do carimbo se diferente de (0,0)
            if src_tb is not None and isinstance(src_tb.Location, LocationPoint):
                for n_tb in FilteredElementCollector(doc, nova_sh.Id).OfCategory(BuiltInCategory.OST_TitleBlocks).WhereElementIsNotElementType().ToElements():
                    if isinstance(n_tb.Location, LocationPoint):
                        d = src_tb.Location.Point - n_tb.Location.Point
                        if d.GetLength() > 1e-4:
                            n_tb.Location.Move(d)
                    break

            # Anotações 2D na prancha
            if op["cp_anot"] and anot_ids.Count > 0:
                try:
                    ElementTransformUtils.CopyElements(src_sh, anot_ids, nova_sh, Transform.Identity, CopyPasteOptions())
                except Exception:
                    pass

            # Tabelas (Schedules)
            if op["cp_sched"]:
                for ss in src_scheds:
                    try:
                        ScheduleSheetInstance.Create(doc, nova_sh.Id, ss.ScheduleId, ss.Point)
                    except Exception:
                        pass

            # Vistas & Legendas
            for vp in src_vps:
                v_orig = doc.GetElement(vp.ViewId)
                if v_orig is None:
                    continue
                ctr_vp = vp.GetBoxCenter()
                if v_orig.ViewType == ViewType.Legend:
                    if op["cp_leg"]:
                        try:
                            nv = Viewport.Create(doc, nova_sh.Id, v_orig.Id, ctr_vp)
                            nv.ChangeTypeId(vp.GetTypeId())
                        except Exception:
                            pass
                elif dup_opt is not None:
                    try:
                        new_vid = v_orig.Duplicate(dup_opt)
                        new_v = doc.GetElement(new_vid)
                        if new_v is not None:
                            new_v.Name = _nome_vista_unico(nomes_vistas, u"{} - {}".format(v_orig.Name, num_cand))
                            if Viewport.CanAddViewToSheet(doc, nova_sh.Id, new_v.Id):
                                nv = Viewport.Create(doc, nova_sh.Id, new_v.Id, ctr_vp)
                                try:
                                    nv.ChangeTypeId(vp.GetTypeId())
                                except Exception:
                                    pass
                    except Exception:
                        pass

            criadas.append(u"{} - {}".format(nova_sh.SheetNumber, nova_sh.Name))
        t.Commit()

    forms.alert(
        u"Duplicação de prancha concluída!\n\n✔ Pranchas geradas ({}):\n- {}".format(
            len(criadas), u"\n- ".join(criadas)
        ),
        title="Duplicar Prancha Completa"
    )


def _auto_montar_pranchas(op):
    tb_sym = op["tb"]
    numeros_usados = set(sh.SheetNumber for sh in _listar_sheets())
    car_ft = (op["car_cm"] * 10.0) * MM
    gap_ft = (op["gap_cm"] * 10.0) * MM
    mar_ft = 20.0 * MM

    pranchas_criadas = []
    vistas_nao_colocadas = 0
    vistas_colocadas = 0

    def _nova_sheet(idx_sh):
        num = _incrementar_codigo(op["num_ini"], idx_sh)
        while num in numeros_usados:
            num = _incrementar_codigo(num, 1)
        numeros_usados.add(num)
        sh = ViewSheet.Create(doc, tb_sym.Id)
        try:
            sh.SheetNumber = num
        except Exception:
            pass
        try:
            sh.Name = u"{} {}".format(op["nome_base"], num)
        except Exception:
            pass
        doc.Regenerate()

        # Mede tamanho útil do carimbo
        bb_tb = None
        for fi in FilteredElementCollector(doc, sh.Id).OfCategory(BuiltInCategory.OST_TitleBlocks).WhereElementIsNotElementType().ToElements():
            bb_tb = fi.get_BoundingBox(sh)
            break
        if bb_tb is not None:
            x0 = bb_tb.Min.X + mar_ft
            y0 = bb_tb.Min.Y + mar_ft
            x1 = max(x0 + 100.0 * MM, bb_tb.Max.X - car_ft)
            y1 = bb_tb.Max.Y - mar_ft
        else:
            x0, y0, x1, y1 = mar_ft, mar_ft, 600.0 * MM, 550.0 * MM
        return sh, (x0, y0, x1, y1)

    with Transaction(doc, u"Auto-Montagem de Pranchas") as t:
        t.Start()
        idx_sh = 0
        cur_sh, area = _nova_sheet(idx_sh)
        pranchas_criadas.append(cur_sh)
        x0, y0, x1, y1 = area
        cx = x0
        cy_top = y1
        row_h = 0.0

        for v in op["vistas"]:
            # Nota: View.ViewType.Legend pode estar em várias pranchas; outras vistas só 1x
            if v.ViewType != ViewType.Legend and not Viewport.CanAddViewToSheet(doc, cur_sh.Id, v.Id):
                vistas_nao_colocadas += 1
                continue

            try:
                vp = Viewport.Create(doc, cur_sh.Id, v.Id, XYZ(0.5 * (x0 + x1), 0.5 * (y0 + y1), 0.0))
            except Exception:
                vistas_nao_colocadas += 1
                continue

            doc.Regenerate()
            ol = vp.GetBoxOutline()
            vw = max(10.0 * MM, ol.MaximumPoint.X - ol.MinimumPoint.X)
            vh = max(10.0 * MM, ol.MaximumPoint.Y - ol.MinimumPoint.Y)
            b_ctr = vp.GetBoxCenter()
            ol_ctr = XYZ(0.5 * (ol.MinimumPoint.X + ol.MaximumPoint.X), 0.5 * (ol.MinimumPoint.Y + ol.MaximumPoint.Y), 0.0)
            desv = b_ctr - ol_ctr

            # Quebra de linha se não couber horizontalmente
            if cx > x0 + 1e-4 and (cx + vw > x1):
                cx = x0
                cy_top -= (row_h + gap_ft)
                row_h = 0.0

            # Nova folha se não couber verticalmente (e já tiver algo na folha atual)
            if (cy_top - vh < y0) and (cy_top < y1 - 1e-4 or cx > x0 + 1e-4):
                doc.Delete(vp.Id)
                idx_sh += 1
                cur_sh, area = _nova_sheet(idx_sh)
                pranchas_criadas.append(cur_sh)
                x0, y0, x1, y1 = area
                cx = x0
                cy_top = y1
                row_h = 0.0
                vp = Viewport.Create(doc, cur_sh.Id, v.Id, XYZ(0.5 * (x0 + x1), 0.5 * (y0 + y1), 0.0))
                doc.Regenerate()
                ol = vp.GetBoxOutline()
                vw = max(10.0 * MM, ol.MaximumPoint.X - ol.MinimumPoint.X)
                vh = max(10.0 * MM, ol.MaximumPoint.Y - ol.MinimumPoint.Y)
                b_ctr = vp.GetBoxCenter()
                ol_ctr = XYZ(0.5 * (ol.MinimumPoint.X + ol.MaximumPoint.X), 0.5 * (ol.MinimumPoint.Y + ol.MaximumPoint.Y), 0.0)
                desv = b_ctr - ol_ctr

            alvo_ctr = XYZ(cx + 0.5 * vw, cy_top - 0.5 * vh, 0.0) + desv
            vp.SetBoxCenter(alvo_ctr)
            cx += vw + gap_ft
            row_h = max(row_h, vh)
            vistas_colocadas += 1

        t.Commit()

    nomes_pr = [u"{} - {}".format(s.SheetNumber, s.Name) for s in pranchas_criadas]
    msg = (
        u"Auto-Montagem concluída!\n\n"
        u"✔ Pranchas criadas: {}\n"
        u"✔ Vistas inseridas: {}\n"
        u"- {}"
    ).format(len(pranchas_criadas), vistas_colocadas, u"\n- ".join(nomes_pr))
    if vistas_nao_colocadas:
        msg += u"\n\n⚠ Vistas ignoradas (já estavam em outra prancha): {}".format(vistas_nao_colocadas)
    forms.alert(msg, title="Auto-Montagem de Pranchas")


def run_pranchas_auto():
    global doc, uidoc, view
    uidoc = __revit__.ActiveUIDocument
    doc   = uidoc.Document
    view  = doc.ActiveView

    prefs  = _carregar_prefs()
    sheets = _listar_sheets()
    tbs    = _listar_titleblocks()

    win = PranchasAutoWindow(prefs, sheets, tbs)
    win.ShowDialog()
    if not win.confirmado:
        script.exit()

    op = win.resultado
    if op["modo"] == 0:
        _salvar_prefs({"pr_vmodo": op["vmodo_idx"], "pr_dqtd": op["qtd"]})
        _duplicar_prancha_completa(op)
    else:
        _salvar_prefs({
            "pr_am_num":  op["num_ini"],
            "pr_am_nome": op["nome_base"],
            "pr_am_car":  op["car_cm"],
            "pr_am_gap":  op["gap_cm"],
            "pr_filt":    op["filtro_i"],
        })
        _auto_montar_pranchas(op)
