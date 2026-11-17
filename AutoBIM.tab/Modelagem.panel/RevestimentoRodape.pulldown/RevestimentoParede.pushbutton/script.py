# -*- coding: utf-8 -*-
__title__ = "Revestimento e\nRodapé (Parede)"
__author__ = "AutoBIM"
__version__ = "Versão 1.0"
__doc__ = """Modela automaticamente paredes finas de revestimento (cerâmica, porcelanato, pintura) ou rodapés contornando internamente os ambientes selecionados.

Diferenciais:
- Altura configurável: Revestimento alto (ex.: 2,60 m), Meia-parede/Barrado (ex.: 1,20 m) ou Rodapé (ex.: 0,10 m).
- Executa JoinGeometry automático com a parede hospedeira para que portas e janelas cortem o revestimento/rodapé automaticamente.
- Opção para desativar 'Delimitante de Ambiente' (Room Bounding) nas camadas criadas para não alterar a área dos ambientes."""

import os
import json
import codecs
import clr
clr.AddReference("System")
clr.AddReference("PresentationFramework")

from System.Collections.Generic import List
from Autodesk.Revit.DB import *
from Autodesk.Revit.DB.Architecture import Room
from pyrevit import forms, revit, script
import Snippets._ui_search as uis
try:
    reload(uis)
except Exception:
    pass

doc   = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument
view  = doc.ActiveView

FT = 1.0 / 0.3048
SETTINGS_PATH = os.path.join(os.path.dirname(__file__), "_wall_finish_settings.json")


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


XAML = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Revestimento de Parede e Rodapé por Ambiente" Width="450" SizeToContent="Height"
        WindowStartupLocation="CenterScreen" ResizeMode="NoResize"
        Background="#F3F3F3" FontFamily="Segoe UI" FontSize="12">
    <StackPanel Margin="18">
        <TextBlock Text="Tipo de Parede (Revestimento ou Rodapé)" FontWeight="SemiBold"/>
        <ComboBox x:Name="cb_wall_type" Margin="0,4,0,12" Height="26"/>

        <Grid Margin="0,0,0,12">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="12"/>
                <ColumnDefinition Width="120"/>
                <ColumnDefinition Width="12"/>
                <ColumnDefinition Width="120"/>
            </Grid.ColumnDefinitions>
            <StackPanel Grid.Column="0">
                <TextBlock Text="Nível base" FontWeight="SemiBold"/>
                <ComboBox x:Name="cb_level" Margin="0,4,0,0" Height="26"/>
            </StackPanel>
            <StackPanel Grid.Column="2">
                <TextBlock Text="Altura (m)" FontWeight="SemiBold"/>
                <TextBox x:Name="tb_altura" Margin="0,4,0,0" Height="26"
                         VerticalContentAlignment="Center" Text="2,60"/>
            </StackPanel>
            <StackPanel Grid.Column="4">
                <TextBlock Text="Offset Base (cm)" FontWeight="SemiBold"/>
                <TextBox x:Name="tb_base" Margin="0,4,0,0" Height="26"
                         VerticalContentAlignment="Center" Text="0"/>
            </StackPanel>
        </Grid>

        <TextBlock Text="Dica: Para Rodapé, escolha um tipo fino e use ex.: Altura = 0,10 m"
                   FontSize="11" Foreground="#555555" Margin="0,0,0,10"/>

        <CheckBox x:Name="ck_join" Margin="0,4,0,0" IsChecked="True"
                  Content="Unir à parede original (corta portas e janelas automaticamente)"/>
        <CheckBox x:Name="ck_no_room_bound" Margin="0,8,0,0" IsChecked="True"
                  Content="Desativar 'Delimitante de Ambiente' (não reduz a área da Room)"/>

        <StackPanel Orientation="Horizontal" HorizontalAlignment="Right" Margin="0,20,0,0">
            <Button x:Name="btn_cancel" Content="Cancelar" Width="90" Height="30" Margin="0,0,8,0"/>
            <Button x:Name="btn_ok" Content="Selecionar Ambientes" Width="155" Height="30"
                    IsDefault="True" Background="#0078D4" Foreground="White"/>
        </StackPanel>
    </StackPanel>
</Window>
"""


class RevestimentoWindow(forms.WPFWindow):
    def __init__(self, nomes_paredes, nomes_niveis, nivel_vista_idx, prefs):
        forms.WPFWindow.__init__(self, XAML, literal_string=True)
        self.confirmado = False
        self.resultado = {}

        self._cb_wt    = self.FindName("cb_wall_type")
        self._cb_lvl   = self.FindName("cb_level")
        self._tb_alt   = self.FindName("tb_altura")
        self._tb_base  = self.FindName("tb_base")
        self._ck_join  = self.FindName("ck_join")
        self._ck_nrb   = self.FindName("ck_no_room_bound")

        for n in nomes_niveis:
            self._cb_lvl.Items.Add(n)

        ultimo_wt = prefs.get("wall_type", "")
        idx_wt = nomes_paredes.index(ultimo_wt) if ultimo_wt in nomes_paredes else 0
        self._sc_wt = uis.SearchableCombo.attach_auto(
            self._cb_wt, nomes_paredes, placeholder=u"Pesquisar tipo de parede...", initial_index=idx_wt
        )
        self._cb_lvl.SelectedIndex = nivel_vista_idx
        self._tb_alt.Text          = str(prefs.get("altura_m", "2,60"))
        self._tb_base.Text         = str(prefs.get("base_cm", "0"))
        self._ck_join.IsChecked    = bool(prefs.get("join", True))
        self._ck_nrb.IsChecked     = bool(prefs.get("no_room_bound", True))

        self.FindName("btn_ok").Click     += self.on_ok
        self.FindName("btn_cancel").Click += self.on_cancel

    def on_cancel(self, sender, args):
        self.confirmado = False
        self.Close()

    def on_ok(self, sender, args):
        try:
            altura_m = float(self._tb_alt.Text.replace(",", "."))
            base_cm  = float(self._tb_base.Text.replace(",", "."))
        except Exception:
            forms.alert("Informe valores numéricos válidos para Altura e Offset Base.")
            return

        if altura_m <= 0.01:
            forms.alert("A altura deve ser maior que zero.")
            return

        wt_idx = self._sc_wt.selected_index()
        if wt_idx < 0:
            forms.alert("Selecione o tipo de parede.")
            return

        self.resultado = {
            "wt_idx":        wt_idx,
            "lvl_idx":       self._cb_lvl.SelectedIndex,
            "altura_m":      altura_m,
            "base_cm":       base_cm,
            "join":          bool(self._ck_join.IsChecked),
            "no_room_bound": bool(self._ck_nrb.IsChecked),
        }
        self.confirmado = True
        self.Close()


# =============================================================================
# COLETAR TIPOS DE PAREDE BÁSICOS E NÍVEIS
# =============================================================================
def nome_tipo(el):
    try:
        p = el.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME)
        if p is not None and p.AsString():
            return p.AsString()
    except Exception:
        pass
    return el.Name


wall_types = [
    wt for wt in FilteredElementCollector(doc).OfClass(WallType).ToElements()
    if wt.Kind == WallKind.Basic
]
if not wall_types:
    forms.alert("Nenhum tipo de parede básica encontrado no projeto.", exitscript=True)

wall_types.sort(key=lambda w: nome_tipo(w).lower())
nomes_paredes = [u"{}  ({:.1f} cm)".format(nome_tipo(w), w.Width * 30.48) for w in wall_types]

niveis = sorted(
    FilteredElementCollector(doc).OfClass(Level).ToElements(),
    key=lambda l: l.Elevation
)
nomes_niveis = [u"{}  ({:.2f} m)".format(l.Name, l.Elevation * 0.3048) for l in niveis]

nivel_vista = 0
try:
    gl = view.GenLevel
    if gl is not None:
        for i, l in enumerate(niveis):
            if l.Id == gl.Id:
                nivel_vista = i
                break
except Exception:
    pass

prefs = carregar_preferencias()
win = RevestimentoWindow(nomes_paredes, nomes_niveis, nivel_vista, prefs)
win.ShowDialog()

if not win.confirmado:
    script.exit()

op = win.resultado
wall_type   = wall_types[op["wt_idx"]]
nivel       = niveis[op["lvl_idx"]]
altura_ft   = op["altura_m"] * FT
base_off_ft = op["base_cm"] / 30.48
meia_esp    = wall_type.Width * 0.5

op["wall_type"] = nomes_paredes[op["wt_idx"]]
salvar_preferencias(op)

# =============================================================================
# SELECIONAR AMBIENTES
# =============================================================================
rooms = []
for eid in uidoc.Selection.GetElementIds():
    el = doc.GetElement(eid)
    if isinstance(el, Room) and el.Area > 0:
        rooms.append(el)

if not rooms:
    with forms.WarningBar(title="Selecione os Ambientes que receberão Revestimento/Rodapé e clique em Finish"):
        rooms = revit.pick_elements_by_category(BuiltInCategory.OST_Rooms)

rooms = [r for r in (rooms or []) if r.Area > 0]
if not rooms:
    forms.alert("Nenhum ambiente válido selecionado.", exitscript=True)


# =============================================================================
# CRIAR PAREDES DE REVESTIMENTO / RODAPÉ E UNIR ÀS PAREDES HOSPEDEIRAS
# =============================================================================
def flatten(curve, z):
    dz = z - curve.GetEndPoint(0).Z
    if abs(dz) < 1e-9:
        return curve
    return curve.CreateTransformed(Transform.CreateTranslation(XYZ(0, 0, dz)))


def signed_area_pts(pts):
    a = 0.0
    n = len(pts)
    for i in range(n):
        p = pts[i]
        q = pts[(i + 1) % n]
        a += p.X * q.Y - q.X * p.Y
    return a * 0.5


def make_ccw_items(items):
    pts = [c.GetEndPoint(0) for c, _ in items]
    if signed_area_pts(pts) >= 0:
        return items
    return [(c.CreateReversed(), el) for c, el in reversed(items)]


boundary_opts = SpatialElementBoundaryOptions()
boundary_opts.SpatialElementBoundaryLocation = SpatialElementBoundaryLocation.Finish

z0 = nivel.Elevation
paredes_criadas = 0
ambientes_ok = 0
erros = 0

with Transaction(doc, "Modelar Revestimento e Rodapé por Ambiente") as t:
    t.Start()

    for room in rooms:
        try:
            seg_lists = room.GetBoundarySegments(boundary_opts)
            if not seg_lists or seg_lists.Count == 0:
                continue

            lacos = []
            for sl in seg_lists:
                itens = []
                for seg in sl:
                    c = flatten(seg.GetCurve(), z0)
                    el = None
                    if seg.ElementId and seg.ElementId != ElementId.InvalidElementId:
                        el = doc.GetElement(seg.ElementId)
                    itens.append((c, el))
                if len(itens) >= 3:
                    lacos.append(itens)

            if not lacos:
                continue

            lacos.sort(
                key=lambda it: abs(signed_area_pts([c.GetEndPoint(0) for c, _ in it])),
                reverse=True
            )
            outer_items = make_ccw_items(lacos[0])

            # Criar CurveLoop externo no sentido anti-horário e deslocar para dentro por meia_esp
            outer_curves_list = List[Curve]()
            for c, _ in outer_items:
                outer_curves_list.Add(c)
            outer_loop = CurveLoop.Create(outer_curves_list)

            try:
                offset_loop = CurveLoop.CreateViaOffset(outer_loop, -meia_esp, XYZ.BasisZ)
                curvas_eixo = list(offset_loop)
            except Exception:
                curvas_eixo = [c for c, _ in outer_items]

            criou_no_room = False

            for idx, c_eixo in enumerate(curvas_eixo):
                if c_eixo.Length < 0.03 * FT:
                    continue

                # Identificar a parede hospedeira mais próxima do ponto médio deste trecho
                mid_pt = c_eixo.Evaluate(0.5, True)
                host_wall = None
                menor_dist = float("inf")
                for c_orig, el_orig in outer_items:
                    if isinstance(el_orig, Wall):
                        d_proj = c_orig.Distance(mid_pt)
                        if d_proj < menor_dist:
                            menor_dist = d_proj
                            host_wall = el_orig

                nova_parede = Wall.Create(
                    doc,
                    c_eixo,
                    wall_type.Id,
                    nivel.Id,
                    altura_ft,
                    base_off_ft,
                    False,
                    False
                )

                if nova_parede:
                    if op["no_room_bound"]:
                        p_rb = nova_parede.get_Parameter(BuiltInParameter.WALL_ATTR_ROOM_BOUNDING)
                        if p_rb and not p_rb.IsReadOnly:
                            p_rb.Set(0)

                    if op["join"] and host_wall is not None:
                        try:
                            if not JoinGeometryUtils.AreElementsJoined(doc, host_wall, nova_parede):
                                JoinGeometryUtils.JoinGeometry(doc, host_wall, nova_parede)
                        except Exception:
                            pass

                    paredes_criadas += 1
                    criou_no_room = True

            if criou_no_room:
                ambientes_ok += 1

        except Exception as e:
            erros += 1
            print("Erro ao criar revestimento no ambiente {}: {}".format(room.Id, str(e)))

    t.Commit()

forms.alert(
    u"Concluído!\n\n"
    u"✔ Ambientes revestidos: {}\n"
    u"✔ Trechos de revestimento/rodapé criados: {}\n"
    u"✘ Erros: {}".format(ambientes_ok, paredes_criadas, erros),
    title="Revestimento e Rodapé",
    warn_icon=False
)
