# -*- coding: utf-8 -*-
"""Ferramentas do Painel Auditoria & Visualizacao (AutoBIM):
1. Colorir Elementos por Parametro / Tipo (Filtro Visual) + Resetar Cores
2. Section Box 3D Rapido (por Selecao, Ambiente ou Nivel)
3. Modo Fantasma / Isolar Inteligente (Ghost Mode)
4. Detector Rapido de Conflitos na Vista (Arquitetura x Estrutura / Interiores)
"""

import codecs
import json
import os
import re
import clr

clr.AddReference("System")
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")

from System.Collections.Generic import List
from System.Windows import Thickness, HorizontalAlignment, VerticalAlignment, CornerRadius
from System.Windows.Controls import Border, StackPanel, TextBlock, Orientation, CheckBox as WpfCheckBox
from System.Windows.Media import Color as WpfColor, SolidColorBrush
from Autodesk.Revit.DB import *
from Autodesk.Revit.UI.Selection import ObjectType
from pyrevit import forms, script


doc = None
uidoc = None
view = None

CM = 1.0 / 30.48
MM = 1.0 / 304.8
SETTINGS_PATH = os.path.join(os.path.dirname(__file__), "_audit_viz_settings.json")

PALETA_RGB = [
    (31, 119, 180),   # Azul
    (255, 127, 14),   # Laranja
    (44, 160, 44),    # Verde
    (214, 39, 40),    # Vermelho
    (148, 103, 189),  # Roxo
    (23, 190, 207),   # Ciano
    (227, 119, 194),  # Rosa
    (188, 189, 34),   # Oliva
    (140, 86, 75),    # Marrom
    (0, 153, 136),    # Verde-agua
    (238, 119, 51),   # Coral
    (51, 34, 136),    # Indigo
    (204, 51, 17),    # Vermelho telha
    (17, 119, 51),    # Floresta
    (136, 204, 238),  # Azul claro
    (221, 204, 119),  # Areia
]


def _init_ctx():
    global doc, uidoc, view
    uidoc = __revit__.ActiveUIDocument
    doc = uidoc.Document
    view = doc.ActiveView


def _eid_int(eid):
    if eid is None:
        return -1
    if hasattr(eid, "Value"):
        return int(eid.Value)
    return int(eid.IntegerValue)


def _natural_key(texto):
    partes = re.split(r"(\d+)", unicode(texto or u""))
    out = []
    for p in partes:
        if p.isdigit():
            out.append((0, int(p)))
        else:
            out.append((1, p.lower()))
    return out


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
# HELPERS PUROS (TESTAVEIS SEM REVIT)
# ==============================================================================

def _paleta_cor(idx):
    """Retorna tupla (r, g, b) de alto contraste para o indice informado."""
    return PALETA_RGB[int(idx) % len(PALETA_RGB)]


def _expandir_bbox_coords(min_xyz, max_xyz, margem_ft):
    """Expande coordenadas (x, y, z) de uma BoundingBox pela margem em pes."""
    m = max(0.0, float(margem_ft or 0.0))
    return (
        (float(min_xyz[0]) - m, float(min_xyz[1]) - m, float(min_xyz[2]) - m),
        (float(max_xyz[0]) + m, float(max_xyz[1]) + m, float(max_xyz[2]) + m),
    )


def _bboxes_colidem(bb1, bb2, tol_ft=0.0656):
    """Verifica intersecao 3D entre duas caixas ((x0,y0,z0),(x1,y1,z1)) descontando tol_ft (~2cm)."""
    t = max(0.0, float(tol_ft or 0.0))
    (ax0, ay0, az0), (ax1, ay1, az1) = bb1
    (bx0, by0, bz0), (bx1, by1, bz1) = bb2
    if (min(ax1, bx1) - max(ax0, bx0)) <= t:
        return False
    if (min(ay1, by1) - max(ay0, by0)) <= t:
        return False
    if (min(az1, bz1) - max(az0, bz0)) <= t:
        return False
    return True


def _calcular_intersecao_bbox(bb1, bb2):
    """Retorna a caixa de intersecao ((x0,y0,z0),(x1,y1,z1)) entre duas caixas."""
    (ax0, ay0, az0), (ax1, ay1, az1) = bb1
    (bx0, by0, bz0), (bx1, by1, bz1) = bb2
    return (
        (max(ax0, bx0), max(ay0, by0), max(az0, bz0)),
        (min(ax1, bx1), min(ay1, by1), min(az1, bz1)),
    )


def _parse_float_br(texto, default=0.0):
    s = unicode(texto or u"").strip().replace(u",", u".")
    try:
        return float(s)
    except Exception:
        return default


# ==============================================================================
# HELPERS REVIT DE VISUALIZACAO & GEOMETRIA
# ==============================================================================

def _elem_name(el):
    if el is None:
        return u""
    try:
        n = Element.Name.GetValue(el)
        if n:
            return unicode(n)
    except Exception:
        pass
    try:
        return unicode(el.Name or u"")
    except Exception:
        return u""


def _solid_fill_pattern_id(documento):
    for fp in FilteredElementCollector(documento).OfClass(FillPatternElement).ToElements():
        try:
            pat = fp.GetFillPattern()
            if pat is not None and pat.IsSolidFill:
                return fp.Id
        except Exception:
            pass
    return ElementId.InvalidElementId


def _criar_ogs_cor(rgb, solid_id, transparencia=0, halftone=False):
    ogs = OverrideGraphicSettings()
    col = Color(int(rgb[0]), int(rgb[1]), int(rgb[2]))
    if solid_id is not None and _eid_int(solid_id) != -1:
        try:
            ogs.SetSurfaceForegroundPatternId(solid_id)
            ogs.SetSurfaceForegroundPatternColor(col)
            ogs.SetCutForegroundPatternId(solid_id)
            ogs.SetCutForegroundPatternColor(col)
        except Exception:
            pass
    try:
        ogs.SetProjectionLineColor(col)
        ogs.SetCutLineColor(col)
    except Exception:
        pass
    if transparencia > 0:
        try:
            ogs.SetSurfaceTransparency(max(0, min(100, int(transparencia))))
        except Exception:
            pass
    if halftone:
        try:
            ogs.SetHalftone(True)
        except Exception:
            pass
    return ogs


def _resetar_overrides_vista(documento, vista):
    """Limpa todos os overrides graficos por elemento na vista ativa."""
    els = FilteredElementCollector(documento, vista.Id).WhereElementIsNotElementType().ToElements()
    vazio = OverrideGraphicSettings()
    cnt = 0
    with Transaction(documento, u"AutoBIM - Resetar Cores e Overrides da Vista") as t:
        t.Start()
        for el in els:
            try:
                vista.SetElementOverrides(el.Id, vazio)
                cnt += 1
            except Exception:
                pass
        t.Commit()
    return cnt


def _elementos_modelo_na_vista(documento, vista):
    res = []
    for el in FilteredElementCollector(documento, vista.Id).WhereElementIsNotElementType().ToElements():
        cat = getattr(el, "Category", None)
        if cat is None:
            continue
        try:
            if cat.CategoryType != CategoryType.Model:
                continue
        except Exception:
            pass
        res.append(el)
    return res


def _valor_criterio_elemento(documento, el, criterio):
    """Extrai string do criterio escolhido para agrupar/colorir o elemento."""
    if criterio == u"Tipo / Família":
        try:
            et = documento.GetElement(el.GetTypeId())
            if et is not None:
                fam = getattr(et, "FamilyName", u"") or u""
                tn = _elem_name(et)
                return u"{}: {}".format(unicode(fam), tn) if fam else tn
        except Exception:
            pass
        return _elem_name(el) or u"(Sem Tipo)"

    if criterio == u"Nível":
        try:
            lid = getattr(el, "LevelId", None)
            if lid is not None and _eid_int(lid) != -1:
                lv = documento.GetElement(lid)
                if lv is not None:
                    return _elem_name(lv)
        except Exception:
            pass
        for bip in (BuiltInParameter.WALL_BASE_CONSTRAINT, BuiltInParameter.LEVEL_PARAM, BuiltInParameter.FAMILY_LEVEL_PARAM):
            try:
                p = el.get_Parameter(bip)
                if p is not None and p.HasValue:
                    lv = documento.GetElement(p.AsElementId())
                    if lv is not None:
                        return _elem_name(lv)
            except Exception:
                pass
        return u"(Sem Nível)"

    if criterio == u"Material Principal":
        try:
            mids = list(el.GetMaterialIds(False))
            if mids:
                mat = documento.GetElement(mids[0])
                if mat is not None:
                    return _elem_name(mat)
        except Exception:
            pass
        return u"(Sem Material)"

    if criterio == u"Espessura / Largura":
        try:
            if isinstance(el, Wall):
                return u"{:.1f} cm".format(float(el.Width) * 30.48)
        except Exception:
            pass
        for bip in (BuiltInParameter.FLOOR_ATTR_THICKNESS_PARAM, BuiltInParameter.CEILING_THICKNESS, BuiltInParameter.DOOR_WIDTH, BuiltInParameter.WINDOW_WIDTH):
            try:
                p = el.get_Parameter(bip)
                if p is not None and p.HasValue:
                    return u"{:.1f} cm".format(float(p.AsDouble()) * 30.48)
            except Exception:
                pass
        return u"(N/A)"

    if criterio == u"Marca (Mark)":
        p = el.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
        if p is not None and p.AsString():
            return unicode(p.AsString()).strip()
        return u"(Vazio)"

    if criterio == u"Comentários":
        p = el.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
        if p is not None and p.AsString():
            return unicode(p.AsString()).strip()
        return u"(Vazio)"

    if criterio == u"Fase Criada":
        try:
            pid = getattr(el, "CreatedPhaseId", None)
            if pid is not None and _eid_int(pid) != -1:
                ph = documento.GetElement(pid)
                if ph is not None:
                    return _elem_name(ph)
        except Exception:
            pass
        return u"(Sem Fase)"

    # Parametro customizado por nome (instancia ou tipo)
    for alvo in (el, documento.GetElement(el.GetTypeId())):
        if alvo is None:
            continue
        try:
            p = alvo.LookupParameter(criterio)
            if p is not None and p.HasValue:
                val = p.AsValueString() or p.AsString()
                if val:
                    return unicode(val).strip()
        except Exception:
            pass
    return u"(Vazio)"


def _obter_ou_criar_vista_3d(documento, vista_ativa):
    if isinstance(vista_ativa, View3D) and not getattr(vista_ativa, "IsTemplate", False):
        return vista_ativa

    v3ds = [
        v for v in FilteredElementCollector(documento).OfClass(View3D).ToElements()
        if not getattr(v, "IsTemplate", False)
    ]
    for v in v3ds:
        if _elem_name(v) in (u"{3D}", u"3D"):
            return v
    if v3ds:
        return v3ds[0]

    vft3d = None
    for vt in FilteredElementCollector(documento).OfClass(ViewFamilyType).ToElements():
        if vt.ViewFamily == ViewFamily.ThreeDimensional:
            vft3d = vt
            break
    if vft3d is None:
        return None
    with Transaction(documento, u"AutoBIM - Criar Vista 3D") as t:
        t.Start()
        v_nova = View3D.CreateIsometric(documento, vft3d.Id)
        t.Commit()
    return v_nova


def _abrir_bbox_no_3d(documento, ui_doc, vista_ativa, min_pt, max_pt, margem_ft):
    v3d = _obter_ou_criar_vista_3d(documento, vista_ativa)
    if v3d is None:
        forms.alert(u"Não foi possível localizar ou criar uma Vista 3D.", title=u"Section Box 3D")
        return False

    (x0, y0, z0), (x1, y1, z1) = _expandir_bbox_coords(min_pt, max_pt, margem_ft)
    bb = BoundingBoxXYZ()
    bb.Min = XYZ(x0, y0, z0)
    bb.Max = XYZ(x1, y1, z1)

    with Transaction(documento, u"AutoBIM - Aplicar Section Box 3D") as t:
        t.Start()
        v3d.SetSectionBox(bb)
        v3d.IsSectionBoxActive = True
        t.Commit()

    try:
        ui_doc.ActiveView = v3d
        for uiv in ui_doc.GetOpenUIViews():
            if _eid_int(uiv.ViewId) == _eid_int(v3d.Id):
                uiv.ZoomToFit()
                break
    except Exception:
        pass
    return True


def _bbox_tuple_elemento(el):
    try:
        bb = el.get_BoundingBox(None)
        if bb is not None:
            return ((bb.Min.X, bb.Min.Y, bb.Min.Z), (bb.Max.X, bb.Max.Y, bb.Max.Z))
    except Exception:
        pass
    return None


def _extrair_solido_principal(el):
    try:
        opts = Options()
        opts.DetailLevel = ViewDetailLevel.Coarse
        geom = el.get_Geometry(opts)
        if geom is None:
            return None
        melhor = None
        for g in geom:
            if isinstance(g, Solid) and g.Volume > 1e-6:
                if melhor is None or g.Volume > melhor.Volume:
                    melhor = g
            elif isinstance(g, GeometryInstance):
                for sg in g.GetInstanceGeometry():
                    if isinstance(sg, Solid) and sg.Volume > 1e-6:
                        if melhor is None or sg.Volume > melhor.Volume:
                            melhor = sg
        return melhor
    except Exception:
        return None


# ==============================================================================
# COMANDO 1 — COLORIR ELEMENTOS POR PARAMETRO / TIPO + RESET
# ==============================================================================

XAML_COLORIR = """<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="AutoBIM - Colorir Elementos (Filtro Visual)"
        Width="540" Height="590" ResizeMode="CanResize"
        WindowStartupLocation="CenterScreen" Background="#F5F6F8">
    <Grid Margin="14">
        <Grid.RowDefinitions>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="*"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
        </Grid.RowDefinitions>

        <StackPanel Grid.Row="0" Margin="0,0,0,10">
            <TextBlock Text="Colorir Elementos por Parâmetro / Tipo" FontSize="15" FontWeight="Bold" Foreground="#1E2D41"/>
            <TextBlock Text="Pinta temporariamente os elementos na vista ativa para conferência visual rápida." FontSize="11.5" Foreground="#5A6878"/>
        </StackPanel>

        <GroupBox Grid.Row="1" Header="1. Categoria e Critério" Padding="8" Margin="0,0,0,8" Background="White">
            <Grid>
                <Grid.ColumnDefinitions>
                    <ColumnDefinition Width="120"/>
                    <ColumnDefinition Width="*"/>
                </Grid.ColumnDefinitions>
                <Grid.RowDefinitions>
                    <RowDefinition Height="Auto"/>
                    <RowDefinition Height="Auto"/>
                    <RowDefinition Height="Auto"/>
                </Grid.RowDefinitions>

                <TextBlock Grid.Row="0" Grid.Column="0" Text="Categoria:" VerticalAlignment="Center" Margin="0,0,6,6"/>
                <ComboBox x:Name="cb_cat" Grid.Row="0" Grid.Column="1" Height="25" Margin="0,0,0,6"/>

                <TextBlock Grid.Row="1" Grid.Column="0" Text="Agrupar por:" VerticalAlignment="Center" Margin="0,0,6,6"/>
                <ComboBox x:Name="cb_crit" Grid.Row="1" Grid.Column="1" Height="25" Margin="0,0,0,6"/>

                <TextBlock Grid.Row="2" Grid.Column="0" Text="Transparência (%):" VerticalAlignment="Center" Margin="0,0,6,0"/>
                <TextBox x:Name="tb_transp" Grid.Row="2" Grid.Column="1" Text="0" Width="70" Height="24" HorizontalAlignment="Left" VerticalContentAlignment="Center" Padding="4,0"/>
            </Grid>
        </GroupBox>

        <GroupBox Grid.Row="2" Header="2. Legenda de Cores (Prévia na Vista Ativa)" Padding="6" Margin="0,0,0,10" Background="White">
            <ScrollViewer VerticalScrollBarVisibility="Auto">
                <StackPanel x:Name="sp_legenda"/>
            </ScrollViewer>
        </GroupBox>

        <Grid Grid.Row="4">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="Auto"/>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="Auto"/>
            </Grid.ColumnDefinitions>
            <Button x:Name="btn_reset" Grid.Column="0" Content="Resetar Cores da Vista" Padding="12,5" Height="30" Background="#FFF1F0" Foreground="#B42318" BorderBrush="#F5C2C7"/>
            <StackPanel Grid.Column="2" Orientation="Horizontal">
                <Button x:Name="btn_cancel" Content="Fechar" Width="85" Height="30" Margin="0,0,8,0"/>
                <Button x:Name="btn_ok" Content="Aplicar Cores" Width="130" Height="30" Background="#0072C6" Foreground="White" FontWeight="SemiBold"/>
            </StackPanel>
        </Grid>
    </Grid>
</Window>
"""


class ColorirElementosWindow(forms.WPFWindow):
    def __init__(self, documento, vista):
        forms.WPFWindow.__init__(self, XAML_COLORIR, literal_string=True)
        self._doc = documento
        self._view = vista
        self._todos = _elementos_modelo_na_vista(documento, vista)
        self._grupos = []
        self.acao = None

        cats_map = {}
        for el in self._todos:
            cn = unicode(el.Category.Name)
            cats_map[cn] = cats_map.get(cn, 0) + 1
        self._cats = [u"(Todas as Categorias na Vista)"] + sorted(cats_map.keys(), key=lambda s: s.lower())
        for cn in self._cats:
            lbl = u"{} ({})".format(cn, cats_map[cn]) if cn in cats_map else cn
            self.cb_cat.Items.Add(lbl)
        self.cb_cat.SelectedIndex = 1 if len(self._cats) > 1 else 0

        self._criterios = [
            u"Tipo / Família",
            u"Nível",
            u"Material Principal",
            u"Espessura / Largura",
            u"Marca (Mark)",
            u"Comentários",
            u"Fase Criada",
        ]
        for c in self._criterios:
            self.cb_crit.Items.Add(c)
        self.cb_crit.SelectedIndex = 0

        self._atualizar_legenda()

        self.cb_cat.SelectionChanged += self._on_changed
        self.cb_crit.SelectionChanged += self._on_changed
        self.btn_reset.Click += self._on_reset
        self.btn_cancel.Click += self._on_cancel
        self.btn_ok.Click += self._on_ok

    def _elementos_filtrados(self):
        idx = self.cb_cat.SelectedIndex
        if idx <= 0 or idx >= len(self._cats):
            return list(self._todos)
        alvo_cat = self._cats[idx]
        return [el for el in self._todos if unicode(el.Category.Name) == alvo_cat]

    def _atualizar_legenda(self):
        self.sp_legenda.Children.Clear()
        els = self._elementos_filtrados()
        crit = self._criterios[max(0, self.cb_crit.SelectedIndex)]
        grupos_map = {}
        for el in els:
            chave = _valor_criterio_elemento(self._doc, el, crit)
            grupos_map.setdefault(chave, []).append(el)

        chaves_ord = sorted(grupos_map.keys(), key=lambda k: (-len(grupos_map[k]), k.lower()))
        self._grupos = []
        for idx, ch in enumerate(chaves_ord):
            rgb = _paleta_cor(idx)
            itens = grupos_map[ch]
            self._grupos.append((ch, rgb, itens))

            row = StackPanel()
            row.Orientation = Orientation.Horizontal
            row.Margin = Thickness(2, 3, 2, 3)

            swatch = Border()
            swatch.Width = 18
            swatch.Height = 14
            swatch.CornerRadius = CornerRadius(2)
            swatch.Margin = Thickness(0, 0, 8, 0)
            swatch.Background = SolidColorBrush(WpfColor.FromRgb(rgb[0], rgb[1], rgb[2]))
            row.Children.Add(swatch)

            tb = TextBlock()
            tb.Text = u"{}  ({} elem.)".format(ch, len(itens))
            tb.VerticalAlignment = VerticalAlignment.Center
            row.Children.Add(tb)
            self.sp_legenda.Children.Add(row)

    def _on_changed(self, s, e):
        self._atualizar_legenda()

    def _on_reset(self, s, e):
        self.acao = "reset"
        self.Close()

    def _on_cancel(self, s, e):
        self.Close()

    def _on_ok(self, s, e):
        self.transp = max(0, min(95, int(_parse_float_br(self.tb_transp.Text, 0))))
        self.acao = "apply"
        self.Close()


def run_colorir_elementos():
    _init_ctx()
    win = ColorirElementosWindow(doc, view)
    win.ShowDialog()

    if win.acao == "reset":
        cnt = _resetar_overrides_vista(doc, view)
        forms.alert(u"Cores e overrides da vista resetados ({} elementos restaurados).".format(cnt), title=u"AutoBIM: Colorir Elementos")
        return

    if win.acao != "apply" or not win._grupos:
        return

    solid_id = _solid_fill_pattern_id(doc)
    total_el = 0
    with Transaction(doc, u"AutoBIM - Colorir Elementos") as t:
        t.Start()
        for _, rgb, itens in win._grupos:
            ogs = _criar_ogs_cor(rgb, solid_id, transparencia=win.transp)
            for el in itens:
                try:
                    view.SetElementOverrides(el.Id, ogs)
                    total_el += 1
                except Exception:
                    pass
        t.Commit()

    forms.alert(
        u"Coloração aplicada na vista!\n\n✔ Grupos distintos: {}\n✔ Elementos coloridos: {}".format(len(win._grupos), total_el),
        title=u"AutoBIM: Colorir Elementos",
    )


# ==============================================================================
# COMANDO 2 — SECTION BOX 3D RAPIDO (SELECAO / AMBIENTE / NIVEL)
# ==============================================================================

XAML_SECTION_BOX = """<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="AutoBIM - Section Box 3D Rápido"
        Width="490" Height="370" ResizeMode="NoResize"
        WindowStartupLocation="CenterScreen" Background="#F5F6F8">
    <Grid Margin="14">
        <Grid.RowDefinitions>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="*"/>
            <RowDefinition Height="Auto"/>
        </Grid.RowDefinitions>

        <StackPanel Grid.Row="0" Margin="0,0,0,10">
            <TextBlock Text="Section Box 3D Rápido" FontSize="15" FontWeight="Bold" Foreground="#1E2D41"/>
            <TextBlock Text="Recorta instantaneamente o 3D por seleção, ambiente ou pavimento." FontSize="11.5" Foreground="#5A6878"/>
        </StackPanel>

        <GroupBox Grid.Row="1" Header="Origem do Recorte 3D" Padding="10" Background="White">
            <StackPanel>
                <RadioButton x:Name="rb_sel" Content="Elementos selecionados (ou selecionar agora)" IsChecked="True" Margin="0,0,0,8"/>

                <RadioButton x:Name="rb_room" Content="Por Ambiente (Room):" Margin="0,0,0,4"/>
                <ComboBox x:Name="cb_room" Height="25" Margin="20,0,0,8"/>

                <RadioButton x:Name="rb_level" Content="Por Pavimento (Nível):" Margin="0,0,0,4"/>
                <ComboBox x:Name="cb_level" Height="25" Margin="20,0,0,10"/>

                <StackPanel Orientation="Horizontal">
                    <TextBlock Text="Margem / Folga ao redor (cm):" VerticalAlignment="Center" Margin="0,0,8,0"/>
                    <TextBox x:Name="tb_margem" Text="50" Width="65" Height="24" VerticalContentAlignment="Center" Padding="4,0"/>
                </StackPanel>
            </StackPanel>
        </GroupBox>

        <Grid Grid.Row="2" Margin="0,12,0,0">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="Auto"/>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="Auto"/>
            </Grid.ColumnDefinitions>
            <Button x:Name="btn_reset" Grid.Column="0" Content="Resetar Section Box" Padding="10,5" Height="30" Background="#FFF1F0" Foreground="#B42318" BorderBrush="#F5C2C7"/>
            <StackPanel Grid.Column="2" Orientation="Horizontal">
                <Button x:Name="btn_cancel" Content="Cancelar" Width="85" Height="30" Margin="0,0,8,0"/>
                <Button x:Name="btn_ok" Content="Recortar no 3D" Width="130" Height="30" Background="#0072C6" Foreground="White" FontWeight="SemiBold"/>
            </StackPanel>
        </Grid>
    </Grid>
</Window>
"""


class SectionBox3DWindow(forms.WPFWindow):
    def __init__(self, rooms, levels, has_pre_sel, prefs):
        forms.WPFWindow.__init__(self, XAML_SECTION_BOX, literal_string=True)
        self._rooms = rooms
        self._levels = levels
        self.acao = None

        for r in rooms:
            pnum = r.get_Parameter(BuiltInParameter.ROOM_NUMBER)
            num = unicode(pnum.AsString()) if (pnum and pnum.AsString()) else u""
            self.cb_room.Items.Add(u"{} - {}".format(num, _elem_name(r)) if num else _elem_name(r))
        if rooms:
            self.cb_room.SelectedIndex = 0

        for lv in levels:
            self.cb_level.Items.Add(u"{} ({:.2f} m)".format(_elem_name(lv), float(lv.Elevation) * 0.3048))
        if levels:
            self.cb_level.SelectedIndex = 0

        self.tb_margem.Text = unicode(prefs.get("sb_margem", u"50"))
        if has_pre_sel:
            self.rb_sel.IsChecked = True
        elif rooms:
            self.rb_room.IsChecked = True
        elif levels:
            self.rb_level.IsChecked = True

        self.btn_reset.Click += self._on_reset
        self.btn_cancel.Click += self._on_cancel
        self.btn_ok.Click += self._on_ok

    def _on_reset(self, s, e):
        self.acao = "reset"
        self.Close()

    def _on_cancel(self, s, e):
        self.Close()

    def _on_ok(self, s, e):
        self.margem_cm = max(0.0, _parse_float_br(self.tb_margem.Text, 50.0))
        if bool(self.rb_sel.IsChecked):
            self.modo = "sel"
        elif bool(self.rb_room.IsChecked):
            self.modo = "room"
            self.room_sel = self._rooms[self.cb_room.SelectedIndex] if 0 <= self.cb_room.SelectedIndex < len(self._rooms) else None
        else:
            self.modo = "level"
            self.level_idx = self.cb_level.SelectedIndex
        self.acao = "apply"
        self.Close()


def run_section_box_3d():
    _init_ctx()
    rooms = [
        r for r in FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Rooms).WhereElementIsNotElementType().ToElements()
        if getattr(r, "Area", 0.0) > 1e-6
    ]
    rooms.sort(key=lambda r: _natural_key(_elem_name(r)))
    levels = list(FilteredElementCollector(doc).OfClass(Level).ToElements())
    levels.sort(key=lambda lv: float(lv.Elevation))

    pre_ids = list(uidoc.Selection.GetElementIds())
    prefs = _carregar_prefs()
    win = SectionBox3DWindow(rooms, levels, bool(pre_ids), prefs)
    win.ShowDialog()

    if win.acao == "reset":
        v3d = _obter_ou_criar_vista_3d(doc, view)
        if v3d is not None:
            with Transaction(doc, u"AutoBIM - Resetar Section Box 3D") as t:
                t.Start()
                v3d.IsSectionBoxActive = False
                t.Commit()
            uidoc.ActiveView = v3d
        return

    if win.acao != "apply":
        return

    _salvar_prefs({"sb_margem": u"{:.0f}".format(win.margem_cm)})
    margem_ft = win.margem_cm * CM

    if win.modo == "sel":
        els = [doc.GetElement(eid) for eid in pre_ids]
        if not els:
            try:
                refs = uidoc.Selection.PickObjects(ObjectType.Element, u"Selecione os elementos para recortar no 3D e clique em Concluir")
                els = [doc.GetElement(rf.ElementId) for rf in (refs or [])]
            except Exception:
                return
        bbs = [_bbox_tuple_elemento(el) for el in els if el is not None]
        bbs = [b for b in bbs if b is not None]
        if not bbs:
            forms.alert(u"Nenhum elemento com geometria 3D válida selecionado.", title=u"Section Box 3D")
            return
        min_pt = (min(b[0][0] for b in bbs), min(b[0][1] for b in bbs), min(b[0][2] for b in bbs))
        max_pt = (max(b[1][0] for b in bbs), max(b[1][1] for b in bbs), max(b[1][2] for b in bbs))
        _abrir_bbox_no_3d(doc, uidoc, view, min_pt, max_pt, margem_ft)

    elif win.modo == "room":
        if win.room_sel is None:
            forms.alert(u"Nenhum Ambiente (Room) disponível.", title=u"Section Box 3D")
            return
        bb = _bbox_tuple_elemento(win.room_sel)
        if bb is None:
            forms.alert(u"O Ambiente selecionado não possui volume 3D válido.", title=u"Section Box 3D")
            return
        _abrir_bbox_no_3d(doc, uidoc, view, bb[0], bb[1], margem_ft)

    elif win.modo == "level":
        idx = win.level_idx
        if not (0 <= idx < len(levels)):
            return
        z0 = float(levels[idx].Elevation)
        z1 = float(levels[idx + 1].Elevation) if (idx + 1) < len(levels) else (z0 + 3.20 / 0.3048)
        # Extensao X/Y a partir dos elementos do modelo
        bbs = []
        for el in FilteredElementCollector(doc).OfClass(Wall).WhereElementIsNotElementType().ToElements():
            b = _bbox_tuple_elemento(el)
            if b is not None:
                bbs.append(b)
        if not bbs:
            min_pt = (-50.0, -50.0, z0)
            max_pt = (50.0, 50.0, z1)
        else:
            min_pt = (min(b[0][0] for b in bbs), min(b[0][1] for b in bbs), z0)
            max_pt = (max(b[1][0] for b in bbs), max(b[1][1] for b in bbs), z1)
        _abrir_bbox_no_3d(doc, uidoc, view, min_pt, max_pt, margem_ft)


# ==============================================================================
# COMANDO 3 — MODO FANTASMA / ISOLAR INTELIGENTE (GHOST MODE)
# ==============================================================================

XAML_FANTASMA = """<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="AutoBIM - Modo Fantasma (Ghost Mode)"
        Width="540" SizeToContent="Height" MinHeight="500" ResizeMode="CanResize"
        WindowStartupLocation="CenterScreen" Background="#F5F6F8">
    <Grid Margin="14">
        <Grid.RowDefinitions>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
        </Grid.RowDefinitions>

        <StackPanel Grid.Row="0" Margin="0,0,0,10">
            <TextBlock Text="Modo Fantasma / Isolar Inteligente" FontSize="15" FontWeight="Bold" Foreground="#1E2D41"/>
            <TextBlock Text="Mantém em destaque apenas o alvo escolhido e deixa o restante em transparência/meio-tom." FontSize="11.5" Foreground="#5A6878"/>
        </StackPanel>

        <GroupBox Grid.Row="1" Header="1. O que manter em destaque (opaco)" Padding="8" Margin="0,0,0,8" Background="White">
            <StackPanel>
                <RadioButton x:Name="rb_sel" Content="Elementos selecionados atualmente na vista" Margin="0,0,0,8"/>

                <RadioButton x:Name="rb_cat" Content="Por Categoria:" IsChecked="True" Margin="0,0,0,4"/>
                <ComboBox x:Name="cb_cat" Height="25" Margin="20,0,0,8"/>

                <RadioButton x:Name="rb_lvl" Content="Por Nível:" Margin="0,0,0,4"/>
                <ComboBox x:Name="cb_lvl" Height="25" Margin="20,0,0,8"/>

                <RadioButton x:Name="rb_tipo" Content="Por Tipo / Família:" Margin="0,0,0,4"/>
                <ComboBox x:Name="cb_tipo" Height="25" Margin="20,0,0,0"/>
            </StackPanel>
        </GroupBox>

        <GroupBox Grid.Row="2" Header="2. Estilo do Restante da Vista (Fantasma)" Padding="8" Margin="0,0,0,12" Background="White">
            <StackPanel>
                <StackPanel Orientation="Horizontal" Margin="0,0,0,6">
                    <TextBlock Text="Transparência do restante (%):" VerticalAlignment="Center" Margin="0,0,8,0"/>
                    <TextBox x:Name="tb_transp" Text="80" Width="60" Height="24" VerticalContentAlignment="Center" Padding="4,0"/>
                </StackPanel>
                <CheckBox x:Name="ck_halftone" Content="Aplicar Meio-Tom (Halftone) no restante da vista" IsChecked="True" Margin="0,0,0,4"/>
                <CheckBox x:Name="ck_destacar" Content="Realçar os elementos em destaque em Azul AutoBIM" IsChecked="False"/>
            </StackPanel>
        </GroupBox>

        <Grid Grid.Row="3">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="Auto"/>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="Auto"/>
            </Grid.ColumnDefinitions>
            <Button x:Name="btn_reset" Grid.Column="0" Content="Restaurar Vista (Reset)" Padding="12,5" Height="30" Background="#FFF1F0" Foreground="#B42318" BorderBrush="#F5C2C7"/>
            <StackPanel Grid.Column="2" Orientation="Horizontal">
                <Button x:Name="btn_cancel" Content="Fechar" Width="85" Height="30" Margin="0,0,8,0"/>
                <Button x:Name="btn_ok" Content="Aplicar Fantasma" Width="135" Height="30" Background="#0072C6" Foreground="White" FontWeight="SemiBold"/>
            </StackPanel>
        </Grid>
    </Grid>
</Window>
"""


class ModoFantasmaWindow(forms.WPFWindow):
    def __init__(self, documento, vista, pre_ids):
        forms.WPFWindow.__init__(self, XAML_FANTASMA, literal_string=True)
        self._doc = documento
        self._todos = _elementos_modelo_na_vista(documento, vista)
        self.acao = None

        self._cats = sorted(set(unicode(el.Category.Name) for el in self._todos), key=lambda s: s.lower())
        self._lvls = sorted(set(_valor_criterio_elemento(documento, el, u"Nível") for el in self._todos), key=lambda s: s.lower())
        self._tipos = sorted(set(_valor_criterio_elemento(documento, el, u"Tipo / Família") for el in self._todos), key=lambda s: s.lower())

        for c in self._cats:
            self.cb_cat.Items.Add(c)
        if self._cats:
            self.cb_cat.SelectedIndex = 0

        for l in self._lvls:
            self.cb_lvl.Items.Add(l)
        if self._lvls:
            self.cb_lvl.SelectedIndex = 0

        for t in self._tipos:
            self.cb_tipo.Items.Add(t)
        if self._tipos:
            self.cb_tipo.SelectedIndex = 0

        if pre_ids:
            self.rb_sel.IsChecked = True

        self.btn_reset.Click += self._on_reset
        self.btn_cancel.Click += self._on_cancel
        self.btn_ok.Click += self._on_ok

    def _on_reset(self, s, e):
        self.acao = "reset"
        self.Close()

    def _on_cancel(self, s, e):
        self.Close()

    def _on_ok(self, s, e):
        self.transp = max(10, min(95, int(_parse_float_br(self.tb_transp.Text, 80))))
        self.halftone = bool(self.ck_halftone.IsChecked)
        self.destacar = bool(self.ck_destacar.IsChecked)
        if bool(self.rb_sel.IsChecked):
            self.modo = "sel"
        elif bool(self.rb_cat.IsChecked):
            self.modo = "cat"
            self.alvo = self._cats[self.cb_cat.SelectedIndex] if 0 <= self.cb_cat.SelectedIndex < len(self._cats) else u""
        elif bool(self.rb_lvl.IsChecked):
            self.modo = "lvl"
            self.alvo = self._lvls[self.cb_lvl.SelectedIndex] if 0 <= self.cb_lvl.SelectedIndex < len(self._lvls) else u""
        else:
            self.modo = "tipo"
            self.alvo = self._tipos[self.cb_tipo.SelectedIndex] if 0 <= self.cb_tipo.SelectedIndex < len(self._tipos) else u""
        self.acao = "apply"
        self.Close()


def run_modo_fantasma():
    _init_ctx()
    pre_ids = set(_eid_int(eid) for eid in uidoc.Selection.GetElementIds())
    win = ModoFantasmaWindow(doc, view, pre_ids)
    win.ShowDialog()

    if win.acao == "reset":
        cnt = _resetar_overrides_vista(doc, view)
        forms.alert(u"Vista restaurada ({} elementos sem transparência/meio-tom).".format(cnt), title=u"AutoBIM: Modo Fantasma")
        return

    if win.acao != "apply":
        return

    if win.modo == "sel" and not pre_ids:
        forms.alert(u"Selecione ao menos um elemento na vista antes de usar o modo Seleção.", title=u"Modo Fantasma")
        return

    ogs_ghost = OverrideGraphicSettings()
    ogs_ghost.SetSurfaceTransparency(win.transp)
    ogs_ghost.SetHalftone(win.halftone)

    solid_id = _solid_fill_pattern_id(doc)
    ogs_hi = _criar_ogs_cor((0, 114, 198), solid_id, transparencia=0) if win.destacar else OverrideGraphicSettings()

    destacados = 0
    fantasmas = 0
    with Transaction(doc, u"AutoBIM - Modo Fantasma") as t:
        t.Start()
        for el in win._todos:
            eh_alvo = False
            if win.modo == "sel":
                eh_alvo = (_eid_int(el.Id) in pre_ids)
            elif win.modo == "cat":
                eh_alvo = (unicode(el.Category.Name) == win.alvo)
            elif win.modo == "lvl":
                eh_alvo = (_valor_criterio_elemento(doc, el, u"Nível") == win.alvo)
            elif win.modo == "tipo":
                eh_alvo = (_valor_criterio_elemento(doc, el, u"Tipo / Família") == win.alvo)

            try:
                if eh_alvo:
                    view.SetElementOverrides(el.Id, ogs_hi)
                    destacados += 1
                else:
                    view.SetElementOverrides(el.Id, ogs_ghost)
                    fantasmas += 1
            except Exception:
                pass
        t.Commit()

    forms.alert(
        u"Modo Fantasma aplicado!\n\n✔ Em destaque: {} elemento(s)\n✔ Em modo fantasma: {} elemento(s)".format(destacados, fantasmas),
        title=u"AutoBIM: Modo Fantasma",
    )


# ==============================================================================
# COMANDO 4 — DETECTOR RAPIDO DE CONFLITOS NA VISTA
# ==============================================================================

XAML_CONFLITOS = """<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="AutoBIM - Detector Rápido de Conflitos na Vista"
        Width="640" Height="580" ResizeMode="CanResize"
        WindowStartupLocation="CenterScreen" Background="#F5F6F8">
    <Grid Margin="14">
        <Grid.RowDefinitions>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="*"/>
            <RowDefinition Height="Auto"/>
        </Grid.RowDefinitions>

        <StackPanel Grid.Row="0" Margin="0,0,0,10">
            <TextBlock Text="Detector Rápido de Conflitos na Vista" FontSize="15" FontWeight="Bold" Foreground="#1E2D41"/>
            <TextBlock Text="Verifica colisões sólidas entre arquitetura, esquadrias, forros e estrutura na vista ativa." FontSize="11.5" Foreground="#5A6878"/>
        </StackPanel>

        <GroupBox Grid.Row="1" Header="1. Par de Verificação" Padding="8" Margin="0,0,0,8" Background="White">
            <Grid>
                <Grid.ColumnDefinitions>
                    <ColumnDefinition Width="*"/>
                    <ColumnDefinition Width="Auto"/>
                    <ColumnDefinition Width="Auto"/>
                </Grid.ColumnDefinitions>
                <ComboBox x:Name="cb_par" Grid.Column="0" Height="26" Margin="0,0,8,0">
                    <ComboBoxItem Content="Verificação Completa (Todos os pares abaixo)"/>
                    <ComboBoxItem Content="Portas e Janelas × Pilares e Vigas"/>
                    <ComboBoxItem Content="Paredes × Pilares e Vigas"/>
                    <ComboBoxItem Content="Forros × Portas e Janelas"/>
                    <ComboBoxItem Content="Elementos Sobrepostos na Mesma Categoria (Paredes / Pisos)"/>
                </ComboBox>
                <StackPanel Grid.Column="1" Orientation="Horizontal" Margin="0,0,8,0">
                    <TextBlock Text="Tol. (cm):" VerticalAlignment="Center" Margin="0,0,4,0"/>
                    <TextBox x:Name="tb_tol" Text="2" Width="44" Height="26" VerticalContentAlignment="Center" Padding="4,0"/>
                </StackPanel>
                <Button x:Name="btn_scan" Grid.Column="2" Content="Verificar Agora" Padding="12,3" Height="26" Background="#0072C6" Foreground="White" FontWeight="SemiBold"/>
            </Grid>
        </GroupBox>

        <GroupBox Grid.Row="2" Header="2. Conflitos Encontrados" Padding="6" Margin="0,0,0,10" Background="White">
            <Grid>
                <Grid.RowDefinitions>
                    <RowDefinition Height="Auto"/>
                    <RowDefinition Height="*"/>
                </Grid.RowDefinitions>
                <TextBlock x:Name="lbl_resumo" Grid.Row="0" Text="Clique em 'Verificar Agora' para iniciar a auditoria na vista ativa." Margin="4,0,4,6" Foreground="#5A6878"/>
                <ListBox x:Name="lb_conflitos" Grid.Row="1" BorderThickness="1"/>
            </Grid>
        </GroupBox>

        <Grid Grid.Row="3">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="Auto"/>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="Auto"/>
            </Grid.ColumnDefinitions>
            <Button x:Name="btn_reset" Grid.Column="0" Content="Resetar Destaques" Padding="10,5" Height="30" Background="#FFF1F0" Foreground="#B42318" BorderBrush="#F5C2C7"/>
            <StackPanel Grid.Column="2" Orientation="Horizontal">
                <Button x:Name="btn_destacar" Content="Destacar em Vermelho" Padding="10,5" Height="30" Margin="0,0,6,0"/>
                <Button x:Name="btn_ver3d" Content="Ver Conflito no 3D" Padding="10,5" Height="30" Margin="0,0,6,0" Background="#0072C6" Foreground="White" FontWeight="SemiBold"/>
                <Button x:Name="btn_fechar" Content="Fechar" Width="75" Height="30"/>
            </StackPanel>
        </Grid>
    </Grid>
</Window>
"""


def _checar_pares_conflito(grupo_a, grupo_b, tol_ft, mesmo_grupo=False, limite=120):
    """Retorna lista de dicts {'a': el_a, 'b': el_b, 'bb': inter_bbox, 'label': texto}."""
    conflitos = []
    bbs_a = [(el, _bbox_tuple_elemento(el)) for el in grupo_a]
    bbs_a = [(el, b) for el, b in bbs_a if b is not None]

    if mesmo_grupo:
        bbs_b = bbs_a
    else:
        bbs_b = [(el, _bbox_tuple_elemento(el)) for el in grupo_b]
        bbs_b = [(el, b) for el, b in bbs_b if b is not None]

    sol_cache = {}

    def _get_sol(el):
        k = _eid_int(el.Id)
        if k not in sol_cache:
            sol_cache[k] = _extrair_solido_principal(el)
        return sol_cache[k]

    for i, (el_a, bb_a) in enumerate(bbs_a):
        start_j = i + 1 if mesmo_grupo else 0
        for j in range(start_j, len(bbs_b)):
            el_b, bb_b = bbs_b[j]
            if _eid_int(el_a.Id) == _eid_int(el_b.Id):
                continue
            if not _bboxes_colidem(bb_a, bb_b, tol_ft=tol_ft):
                continue

            # Ignora porta/janela hospedada na propria parede
            try:
                host_a = getattr(el_a, "Host", None)
                host_b = getattr(el_b, "Host", None)
                if host_a is not None and _eid_int(host_a.Id) == _eid_int(el_b.Id):
                    continue
                if host_b is not None and _eid_int(host_b.Id) == _eid_int(el_a.Id):
                    continue
            except Exception:
                pass

            # Refino booleano de solido (evita falso positivo em elementos em L / diagonais)
            s_a = _get_sol(el_a)
            s_b = _get_sol(el_b)
            if s_a is not None and s_b is not None:
                try:
                    inter = BooleanOperationsUtils.ExecuteBooleanOperation(
                        s_a, s_b, BooleanOperationsType.Intersect
                    )
                    if inter is None or inter.Volume <= (tol_ft ** 3):
                        continue
                except Exception:
                    pass

            inter_bb = _calcular_intersecao_bbox(bb_a, bb_b)
            lbl = u"{} [{}]  ×  {} [{}]".format(
                unicode(el_a.Category.Name), _eid_int(el_a.Id),
                unicode(el_b.Category.Name), _eid_int(el_b.Id),
            )
            conflitos.append({"a": el_a, "b": el_b, "bb": inter_bb, "label": lbl})
            if len(conflitos) >= limite:
                return conflitos
    return conflitos


class DetectorConflitosWindow(forms.WPFWindow):
    def __init__(self, documento, ui_doc, vista):
        forms.WPFWindow.__init__(self, XAML_CONFLITOS, literal_string=True)
        self._doc = documento
        self._uidoc = ui_doc
        self._view = vista
        self._conflitos = []
        self.cb_par.SelectedIndex = 0

        self.btn_scan.Click += self._on_scan
        self.btn_destacar.Click += self._on_destacar
        self.btn_ver3d.Click += self._on_ver3d
        self.btn_reset.Click += self._on_reset
        self.btn_fechar.Click += self._on_fechar

    def _coletar_por_cats(self, bics):
        res = []
        for bic in bics:
            try:
                res.extend(
                    FilteredElementCollector(self._doc, self._view.Id)
                    .OfCategory(bic)
                    .WhereElementIsNotElementType()
                    .ToElements()
                )
            except Exception:
                pass
        return res

    def _on_scan(self, s, e):
        tol_cm = max(0.5, _parse_float_br(self.tb_tol.Text, 2.0))
        tol_ft = tol_cm * CM
        modo = max(0, self.cb_par.SelectedIndex)

        esq = self._coletar_por_cats([BuiltInCategory.OST_Doors, BuiltInCategory.OST_Windows])
        estr = self._coletar_por_cats([BuiltInCategory.OST_StructuralColumns, BuiltInCategory.OST_Columns, BuiltInCategory.OST_StructuralFraming])
        paredes = self._coletar_por_cats([BuiltInCategory.OST_Walls])
        forros = self._coletar_por_cats([BuiltInCategory.OST_Ceilings])
        pisos = self._coletar_por_cats([BuiltInCategory.OST_Floors])

        achados = []
        if modo in (0, 1):
            achados.extend(_checar_pares_conflito(esq, estr, tol_ft))
        if modo in (0, 2):
            achados.extend(_checar_pares_conflito(paredes, estr, tol_ft))
        if modo in (0, 3):
            achados.extend(_checar_pares_conflito(forros, esq, tol_ft))
        if modo in (0, 4):
            achados.extend(_checar_pares_conflito(paredes, paredes, tol_ft, mesmo_grupo=True))
            achados.extend(_checar_pares_conflito(pisos, pisos, tol_ft, mesmo_grupo=True))

        self._conflitos = achados
        self.lb_conflitos.Items.Clear()
        for cf in achados:
            self.lb_conflitos.Items.Add(cf["label"])
        if achados:
            self.lb_conflitos.SelectedIndex = 0
            self.lbl_resumo.Text = u"⚠ {} conflito(s) detectado(s) na vista ativa.".format(len(achados))
        else:
            self.lbl_resumo.Text = u"✔ Nenhum conflito encontrado com tolerância de {:.1f} cm!".format(tol_cm)

    def _on_destacar(self, s, e):
        if not self._conflitos:
            forms.alert(u"Execute a verificação primeiro.", title=u"Detector de Conflitos")
            return
        clash_ids = set()
        for cf in self._conflitos:
            clash_ids.add(_eid_int(cf["a"].Id))
            clash_ids.add(_eid_int(cf["b"].Id))

        solid_id = _solid_fill_pattern_id(self._doc)
        ogs_red = _criar_ogs_cor((214, 39, 40), solid_id, transparencia=0)
        ogs_ghost = OverrideGraphicSettings()
        ogs_ghost.SetSurfaceTransparency(80)
        ogs_ghost.SetHalftone(True)

        todos = _elementos_modelo_na_vista(self._doc, self._view)
        with Transaction(self._doc, u"AutoBIM - Destacar Conflitos") as t:
            t.Start()
            for el in todos:
                try:
                    if _eid_int(el.Id) in clash_ids:
                        self._view.SetElementOverrides(el.Id, ogs_red)
                    else:
                        self._view.SetElementOverrides(el.Id, ogs_ghost)
                except Exception:
                    pass
            t.Commit()
        self.lbl_resumo.Text = u"✔ {} elementos conflitantes destacados em vermelho.".format(len(clash_ids))

    def _on_ver3d(self, s, e):
        idx = self.lb_conflitos.SelectedIndex
        if not (0 <= idx < len(self._conflitos)):
            forms.alert(u"Selecione um conflito na lista para abrir no 3D.", title=u"Detector de Conflitos")
            return
        cf = self._conflitos[idx]
        self.Close()
        _abrir_bbox_no_3d(self._doc, self._uidoc, self._view, cf["bb"][0], cf["bb"][1], 60.0 * CM)

    def _on_reset(self, s, e):
        _resetar_overrides_vista(self._doc, self._view)
        self.lbl_resumo.Text = u"✔ Destaques da vista resetados."

    def _on_fechar(self, s, e):
        self.Close()


def run_detector_conflitos():
    _init_ctx()
    win = DetectorConflitosWindow(doc, uidoc, view)
    win.ShowDialog()
