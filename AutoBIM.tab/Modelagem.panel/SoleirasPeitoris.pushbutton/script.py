# -*- coding: utf-8 -*-
__title__ = "Soleiras\ne Peitoris"
__author__ = "AutoBIM"
__version__ = "Versão 1.0"
__doc__ = """Modela automaticamente Peitoris em Janelas e/ou Soleiras em Portas (selecionados ou todos na vista ativa) para quantificação precisa de pedras e acabamentos.

Parâmetros configuráveis:
- Tipo de piso/pedra (ex.: Granito, Mármore).
- Orelha / Avanço lateral em cada lado do vão (cm).
- Pingadeira / Avanço para fora e para dentro da parede (cm).
- Em janelas, lê automaticamente a Altura do Peitoril (Sill Height) de cada instância."""

import os
import json
import codecs
import clr
clr.AddReference("System")
clr.AddReference("PresentationFramework")

from System.Collections.Generic import List
from Autodesk.Revit.DB import *
from pyrevit import forms, script
import Snippets._ui_search as uis
try:
    reload(uis)
except Exception:
    pass

doc   = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument
view  = doc.ActiveView

FT = 1.0 / 0.3048
MIN_LARGURA = 0.05 * FT
SETTINGS_PATH = os.path.join(os.path.dirname(__file__), "_sills_settings.json")


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
        Title="Modelar Soleiras e Peitoris" Width="450" SizeToContent="Height"
        WindowStartupLocation="CenterScreen" ResizeMode="NoResize"
        Background="#F3F3F3" FontFamily="Segoe UI" FontSize="12">
    <StackPanel Margin="18">
        <TextBlock Text="Escopo" FontWeight="SemiBold"/>
        <ComboBox x:Name="cb_escopo" Margin="0,4,0,12" Height="26"/>

        <TextBlock Text="O que deseja modelar?" FontWeight="SemiBold"/>
        <CheckBox x:Name="ck_janelas" Margin="0,6,0,0" IsChecked="True" Content="Peitoris em Janelas (usa a Altura de Peitoril de cada janela)"/>
        <CheckBox x:Name="ck_portas"  Margin="0,6,0,12" IsChecked="False" Content="Soleiras em Portas"/>

        <TextBlock Text="Tipo de Piso / Pedra (Peitoril / Soleira)" FontWeight="SemiBold"/>
        <ComboBox x:Name="cb_floor" Margin="0,4,0,12" Height="26"/>

        <Grid Margin="0,0,0,12">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="10"/>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="10"/>
                <ColumnDefinition Width="*"/>
            </Grid.ColumnDefinitions>
            <StackPanel Grid.Column="0">
                <TextBlock Text="Orelha lateral (cm)" FontWeight="SemiBold"/>
                <TextBox x:Name="tb_orelha" Margin="0,4,0,0" Height="26"
                         VerticalContentAlignment="Center" Text="2"/>
            </StackPanel>
            <StackPanel Grid.Column="2">
                <TextBlock Text="Pingadeira ext. (cm)" FontWeight="SemiBold"/>
                <TextBox x:Name="tb_ext" Margin="0,4,0,0" Height="26"
                         VerticalContentAlignment="Center" Text="2"/>
            </StackPanel>
            <StackPanel Grid.Column="4">
                <TextBlock Text="Avanço int. (cm)" FontWeight="SemiBold"/>
                <TextBox x:Name="tb_int" Margin="0,4,0,0" Height="26"
                         VerticalContentAlignment="Center" Text="0"/>
            </StackPanel>
        </Grid>

        <StackPanel Orientation="Horizontal" HorizontalAlignment="Right" Margin="0,12,0,0">
            <Button x:Name="btn_cancel" Content="Cancelar" Width="90" Height="30" Margin="0,0,8,0"/>
            <Button x:Name="btn_ok" Content="Criar Elementos" Width="150" Height="30"
                    IsDefault="True" Background="#0078D4" Foreground="White"/>
        </StackPanel>
    </StackPanel>
</Window>
"""


class SoleirasPeitorisWindow(forms.WPFWindow):
    def __init__(self, nomes_pisos, prefs):
        forms.WPFWindow.__init__(self, XAML, literal_string=True)
        self.confirmado = False
        self.resultado = {}

        self._cb_escopo  = self.FindName("cb_escopo")
        self._ck_janelas = self.FindName("ck_janelas")
        self._ck_portas  = self.FindName("ck_portas")
        self._cb_floor   = self.FindName("cb_floor")
        self._tb_orelha  = self.FindName("tb_orelha")
        self._tb_ext     = self.FindName("tb_ext")
        self._tb_int     = self.FindName("tb_int")

        for item in (u"Selecionar Janelas/Portas manualmente (ou usar seleção atual)",
                     u"Todas as Janelas/Portas visíveis na vista ativa"):
            self._cb_escopo.Items.Add(item)

        self._cb_escopo.SelectedIndex = int(prefs.get("escopo_idx", 0))
        self._ck_janelas.IsChecked = bool(prefs.get("janelas", True))
        self._ck_portas.IsChecked  = bool(prefs.get("portas", False))

        ultimo_piso = prefs.get("floor", "")
        idx_fl = nomes_pisos.index(ultimo_piso) if ultimo_piso in nomes_pisos else 0
        self._sc_floor = uis.SearchableCombo.attach_auto(
            self._cb_floor, nomes_pisos, placeholder=u"Pesquisar tipo de piso / pedra...", initial_index=idx_fl
        )

        self._tb_orelha.Text = str(prefs.get("orelha_cm", "2"))
        self._tb_ext.Text    = str(prefs.get("ext_cm", "2"))
        self._tb_int.Text    = str(prefs.get("int_cm", "0"))

        self.FindName("btn_ok").Click     += self.on_ok
        self.FindName("btn_cancel").Click += self.on_cancel

    def on_cancel(self, sender, args):
        self.confirmado = False
        self.Close()

    def on_ok(self, sender, args):
        if not self._ck_janelas.IsChecked and not self._ck_portas.IsChecked:
            forms.alert("Marque pelo menos Janelas ou Portas.")
            return
        try:
            orelha_cm = float(self._tb_orelha.Text.replace(",", "."))
            ext_cm    = float(self._tb_ext.Text.replace(",", "."))
            int_cm    = float(self._tb_int.Text.replace(",", "."))
        except Exception:
            forms.alert("Verifique os valores numéricos em centímetros.")
            return

        floor_idx = self._sc_floor.selected_index()
        if floor_idx < 0:
            forms.alert("Selecione o tipo de piso / pedra.")
            return

        self.resultado = {
            "escopo_idx": self._cb_escopo.SelectedIndex,
            "janelas":    bool(self._ck_janelas.IsChecked),
            "portas":     bool(self._ck_portas.IsChecked),
            "floor_idx":  floor_idx,
            "orelha_cm":  orelha_cm,
            "ext_cm":     ext_cm,
            "int_cm":     int_cm,
        }
        self.confirmado = True
        self.Close()


# =============================================================================
# COLETAR TIPOS DE PISO E ABRIR JANELA
# =============================================================================
def nome_tipo(ft):
    try:
        p = ft.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME)
        if p is not None and p.AsString():
            return p.AsString()
    except Exception:
        pass
    return ft.Name


floor_types = []
for ft in FilteredElementCollector(doc).OfClass(FloorType).ToElements():
    try:
        if ft.IsFoundationSlab:
            continue
    except Exception:
        pass
    floor_types.append(ft)

if not floor_types:
    forms.alert("Nenhum tipo de piso encontrado no projeto.", exitscript=True)

floor_types.sort(key=lambda f: nome_tipo(f).lower())
nomes_pisos = [nome_tipo(f) for f in floor_types]

prefs = carregar_preferencias()
win = SoleirasPeitorisWindow(nomes_pisos, prefs)
win.ShowDialog()

if not win.confirmado:
    script.exit()

op = win.resultado
floor_type = floor_types[op["floor_idx"]]
op["floor"] = nomes_pisos[op["floor_idx"]]
salvar_preferencias(op)

# Espessura do tipo de piso escolhido (para somar ao Sill Height da janela se necessário)
esp_pedra = 0.0
try:
    cs = floor_type.GetCompoundStructure()
    if cs is not None:
        esp_pedra = cs.GetWidth()
except Exception:
    pass

# Categorias alvo
bics = []
if op["janelas"]:
    bics.append(BuiltInCategory.OST_Windows)
if op["portas"]:
    bics.append(BuiltInCategory.OST_Doors)

cat_ids = set(ElementId(b) for b in bics)
elementos = []

if op["escopo_idx"] == 1:
    elementos = list(
        FilteredElementCollector(doc, view.Id)
        .WherePasses(ElementMulticategoryFilter(List[BuiltInCategory](bics)))
        .WhereElementIsNotElementType()
        .ToElements()
    )
else:
    for eid in uidoc.Selection.GetElementIds():
        el = doc.GetElement(eid)
        if el is not None and el.Category is not None and el.Category.Id in cat_ids:
            elementos.append(el)

    if not elementos:
        from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter

        class CategoryFilter(ISelectionFilter):
            def __init__(self, allowed_cat_ids):
                self.allowed = allowed_cat_ids

            def AllowElement(self, elem):
                try:
                    return elem.Category is not None and elem.Category.Id in self.allowed
                except Exception:
                    return False

            def AllowReference(self, ref, pt):
                return True

        try:
            with forms.WarningBar(title="Selecione as Janelas e/ou Portas e clique em Finish"):
                refs = uidoc.Selection.PickObjects(
                    ObjectType.Element,
                    CategoryFilter(cat_ids),
                    "Selecione Janelas/Portas"
                )
            elementos = [doc.GetElement(r) for r in refs]
        except Exception:
            script.exit()

if not elementos:
    forms.alert("Nenhuma janela ou porta encontrada.", exitscript=True)


def obter_largura_vao(inst, d):
    larguras = []
    for src in (inst, doc.GetElement(inst.GetTypeId())):
        if src is None:
            continue
        for p_nome in ("FAMILY_ROUGH_WIDTH_PARAM", "WINDOW_WIDTH", "DOOR_WIDTH", "FAMILY_WIDTH_PARAM"):
            try:
                p = src.get_Parameter(getattr(BuiltInParameter, p_nome))
                if p is not None and p.HasValue and p.AsDouble() > MIN_LARGURA:
                    larguras.append(p.AsDouble())
            except Exception:
                pass
    if larguras:
        return max(larguras)
    return 0.80 * FT


orelha_ft = op["orelha_cm"] / 30.48
ext_ft    = op["ext_cm"] / 30.48
int_ft    = op["int_cm"] / 30.48

criados = 0
erros = 0
ID_JANELAS = ElementId(BuiltInCategory.OST_Windows)

with Transaction(doc, "Modelar Soleiras e Peitoris") as t:
    t.Start()

    for inst in elementos:
        try:
            wall = inst.Host
            if not isinstance(wall, Wall):
                continue
            curve = wall.Location.Curve
            if not isinstance(curve, Line):
                continue
            loc = inst.Location
            if loc is None or not hasattr(loc, "Point"):
                continue

            pt_ins = loc.Point
            p0 = curve.GetEndPoint(0)
            d = XYZ(curve.Direction.X, curve.Direction.Y, 0).Normalize()

            # Sentido externo da parede ou da janela/porta (FacingOrientation)
            try:
                n_ext = XYZ(inst.FacingOrientation.X, inst.FacingOrientation.Y, 0).Normalize()
            except Exception:
                n_ext = XYZ(wall.Orientation.X, wall.Orientation.Y, 0).Normalize()

            largura = obter_largura_vao(inst, d) + (2.0 * orelha_ft)
            esp_parede = wall.Width

            # Projetar o centro da janela/porta no eixo central da parede
            t_proj = (XYZ(pt_ins.X, pt_ins.Y, p0.Z) - p0).DotProduct(d)
            centro_eixo = XYZ(p0.X + d.X * t_proj, p0.Y + d.Y * t_proj, 0.0)

            hw = largura * 0.5
            meia_esp = esp_parede * 0.5

            # Para portas (soleiras), caso o usuário não tenha pedido pingadeira, usar apenas a espessura da parede
            eh_janela = (inst.Category.Id == ID_JANELAS)
            avanco_frente = ext_ft if eh_janela else 0.0
            avanco_tras   = int_ft if eh_janela else 0.0

            p_a = centro_eixo - d * hw - n_ext * (meia_esp + avanco_tras)
            p_b = centro_eixo + d * hw - n_ext * (meia_esp + avanco_tras)
            p_c = centro_eixo + d * hw + n_ext * (meia_esp + avanco_frente)
            p_d = centro_eixo - d * hw + n_ext * (meia_esp + avanco_frente)

            lvl_id = inst.LevelId
            if lvl_id is None or lvl_id == ElementId.InvalidElementId:
                lvl_id = wall.LevelId
            lvl = doc.GetElement(lvl_id)
            z_lvl = lvl.Elevation if lvl else 0.0

            pts = [
                XYZ(p_a.X, p_a.Y, z_lvl),
                XYZ(p_b.X, p_b.Y, z_lvl),
                XYZ(p_c.X, p_c.Y, z_lvl),
                XYZ(p_d.X, p_d.Y, z_lvl),
            ]
            # Garantir sentido anti-horário
            area2 = sum(pts[i].X * pts[(i + 1) % 4].Y - pts[(i + 1) % 4].X * pts[i].Y for i in range(4))
            if area2 < 0:
                pts.reverse()

            curves = List[Curve]()
            for i in range(4):
                curves.Add(Line.CreateBound(pts[i], pts[(i + 1) % 4]))
            loop = CurveLoop.Create(curves)
            loops = List[CurveLoop]()
            loops.Add(loop)

            fl = Floor.Create(doc, loops, floor_type.Id, lvl_id)

            # Ajustar elevação (Sill Height + espessura da pedra para que o topo do peitoril fique na base da janela)
            sill_h = 0.0
            p_sill = inst.get_Parameter(BuiltInParameter.INSTANCE_SILL_HEIGHT_PARAM)
            if p_sill and p_sill.HasValue:
                sill_h = p_sill.AsDouble()

            offset_final = (sill_h + esp_pedra) if eh_janela else sill_h
            p_fl_off = fl.get_Parameter(BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM)
            if p_fl_off and not p_fl_off.IsReadOnly:
                p_fl_off.Set(offset_final)

            criados += 1
        except Exception as e:
            erros += 1
            print("Erro em {}: {}".format(inst.Id, str(e)))

    t.Commit()

forms.alert(
    u"Concluído!\n\n"
    u"✔ Elementos criados: {}\n"
    u"✘ Com erro: {}".format(criados, erros),
    title="Soleiras e Peitoris",
    warn_icon=False
)
