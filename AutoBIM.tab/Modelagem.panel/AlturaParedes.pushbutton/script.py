# -*- coding: utf-8 -*-
__title__ = "Altura de\nParedes"
__author__ = "AutoBIM"
__version__ = "Versão 1.0"
__doc__ = """Ajusta em lote a restrição superior, deslocamento de topo/base ou altura desconectada de paredes, com opção de descontar automaticamente a espessura de lajes e vigas acima.

Três modos disponíveis:
1. Descontar Laje/Viga Acima (Automático): detecta o piso/laje ou viga estrutural imediatamente acima de cada parede e ajusta o topo para parar na face inferior da estrutura.
2. Vincular ao Nível Superior + Deslocamento: conecta as paredes ao nível imediatamente superior aplicando o Top Offset informado (ex.: -12 cm).
3. Altura Desconectada Fixa: define as paredes como desconectadas com a altura informada em metros."""

import os
import json
import codecs
import clr
clr.AddReference("System")
clr.AddReference("PresentationFramework")

from System.Collections.Generic import List
from Autodesk.Revit.DB import *
from pyrevit import forms, revit, script

doc   = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument
view  = doc.ActiveView

FT = 1.0 / 0.3048
SETTINGS_PATH = os.path.join(os.path.dirname(__file__), "_wall_height_settings.json")


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
        Title="Anexar / Ajustar Altura de Paredes em Lote" Width="450" SizeToContent="Height"
        WindowStartupLocation="CenterScreen" ResizeMode="NoResize"
        Background="#F3F3F3" FontFamily="Segoe UI" FontSize="12">
    <StackPanel Margin="18">
        <TextBlock Text="Escopo das paredes" FontWeight="SemiBold"/>
        <ComboBox x:Name="cb_escopo" Margin="0,4,0,14" Height="26"/>

        <TextBlock Text="Modo de ajuste do topo das paredes" FontWeight="SemiBold"/>
        <RadioButton x:Name="rb_auto" GroupName="modo" Margin="0,6,0,0"
                     Content="1. Descontar Laje/Viga acima automaticamente (face inferior)"/>
        <RadioButton x:Name="rb_nivel" GroupName="modo" Margin="0,6,0,0"
                     Content="2. Vincular ao Nível Superior com deslocamento fixo (Top Offset)"/>
        <RadioButton x:Name="rb_fixa" GroupName="modo" Margin="0,6,0,12"
                     Content="3. Altura Desconectada fixa"/>

        <Grid Margin="0,0,0,12">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="12"/>
                <ColumnDefinition Width="*"/>
            </Grid.ColumnDefinitions>
            <StackPanel Grid.Column="0">
                <TextBlock Text="Deslocamento do Topo (cm)" FontWeight="SemiBold"/>
                <TextBox x:Name="tb_top_offset" Margin="0,4,0,0" Height="26"
                         VerticalContentAlignment="Center" Text="-12"/>
            </StackPanel>
            <StackPanel Grid.Column="2">
                <TextBlock Text="Altura Desconectada (m)" FontWeight="SemiBold"/>
                <TextBox x:Name="tb_altura" Margin="0,4,0,0" Height="26"
                         VerticalContentAlignment="Center" Text="2,70"/>
            </StackPanel>
        </Grid>

        <Grid Margin="0,0,0,12">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="12"/>
                <ColumnDefinition Width="*"/>
            </Grid.ColumnDefinitions>
            <StackPanel Grid.Column="0">
                <TextBlock Text="Deslocamento da Base (cm)" FontWeight="SemiBold"/>
                <TextBox x:Name="tb_base_offset" Margin="0,4,0,0" Height="26"
                         VerticalContentAlignment="Center" Text="0"/>
            </StackPanel>
            <StackPanel Grid.Column="2" VerticalAlignment="Bottom">
                <CheckBox x:Name="ck_join" Margin="0,0,0,5" IsChecked="True"
                          Content="Unir geometria com laje/viga"/>
            </StackPanel>
        </Grid>

        <StackPanel Orientation="Horizontal" HorizontalAlignment="Right" Margin="0,14,0,0">
            <Button x:Name="btn_cancel" Content="Cancelar" Width="90" Height="30" Margin="0,0,8,0"/>
            <Button x:Name="btn_ok" Content="Aplicar nas Paredes" Width="150" Height="30"
                    IsDefault="True" Background="#0078D4" Foreground="White"/>
        </StackPanel>
    </StackPanel>
</Window>
"""


class AlturaParedesWindow(forms.WPFWindow):
    def __init__(self, prefs):
        forms.WPFWindow.__init__(self, XAML, literal_string=True)
        self.confirmado = False
        self.resultado = {}

        self._cb_escopo = self.FindName("cb_escopo")
        self._rb_auto   = self.FindName("rb_auto")
        self._rb_nivel  = self.FindName("rb_nivel")
        self._rb_fixa   = self.FindName("rb_fixa")
        self._tb_top    = self.FindName("tb_top_offset")
        self._tb_alt    = self.FindName("tb_altura")
        self._tb_base   = self.FindName("tb_base_offset")
        self._ck_join   = self.FindName("ck_join")

        for item in (u"Selecionar paredes manualmente (ou usar seleção atual)",
                     u"Todas as paredes visíveis na vista ativa"):
            self._cb_escopo.Items.Add(item)

        self._cb_escopo.SelectedIndex = int(prefs.get("escopo_idx", 0))
        modo = prefs.get("modo", "auto")
        self._rb_auto.IsChecked  = (modo == "auto")
        self._rb_nivel.IsChecked = (modo == "nivel")
        self._rb_fixa.IsChecked  = (modo == "fixa")

        self._tb_top.Text  = str(prefs.get("top_cm", "-12"))
        self._tb_alt.Text  = str(prefs.get("altura_m", "2,70"))
        self._tb_base.Text = str(prefs.get("base_cm", "0"))
        self._ck_join.IsChecked = bool(prefs.get("join", True))

        self.FindName("btn_ok").Click     += self.on_ok
        self.FindName("btn_cancel").Click += self.on_cancel

    def on_cancel(self, sender, args):
        self.confirmado = False
        self.Close()

    def on_ok(self, sender, args):
        try:
            top_cm   = float(self._tb_top.Text.replace(",", "."))
            altura_m = float(self._tb_alt.Text.replace(",", "."))
            base_cm  = float(self._tb_base.Text.replace(",", "."))
        except Exception:
            forms.alert("Verifique os valores numéricos informados.")
            return

        if altura_m <= 0.05:
            forms.alert("A altura desconectada deve ser maior que zero.")
            return

        if self._rb_auto.IsChecked:
            modo = "auto"
        elif self._rb_nivel.IsChecked:
            modo = "nivel"
        else:
            modo = "fixa"

        self.resultado = {
            "escopo_idx": self._cb_escopo.SelectedIndex,
            "modo":       modo,
            "top_cm":     top_cm,
            "altura_m":   altura_m,
            "base_cm":    base_cm,
            "join":       bool(self._ck_join.IsChecked),
        }
        self.confirmado = True
        self.Close()


# =============================================================================
# EXECUÇÃO
# =============================================================================
prefs = carregar_preferencias()
win = AlturaParedesWindow(prefs)
win.ShowDialog()

if not win.confirmado:
    script.exit()

op = win.resultado
salvar_preferencias(op)

# Coletar paredes conforme o escopo
walls = []
if op["escopo_idx"] == 1:
    walls = list(
        FilteredElementCollector(doc, view.Id)
        .OfClass(Wall)
        .WhereElementIsNotElementType()
        .ToElements()
    )
else:
    for eid in uidoc.Selection.GetElementIds():
        el = doc.GetElement(eid)
        if isinstance(el, Wall):
            walls.append(el)
    if not walls:
        with forms.WarningBar(title="Selecione as Paredes para ajustar a altura e clique em Finish"):
            walls = revit.pick_elements_by_category(BuiltInCategory.OST_Walls)

if not walls:
    forms.alert("Nenhuma parede selecionada ou encontrada.", exitscript=True)

# Ordenar níveis do projeto por elevação para localizar o nível imediatamente superior
niveis = sorted(
    FilteredElementCollector(doc).OfClass(Level).ToElements(),
    key=lambda l: l.Elevation
)


def nivel_imediatamente_acima(base_level_id):
    base_lvl = doc.GetElement(base_level_id)
    if base_lvl is None:
        return None
    z_base = base_lvl.Elevation
    for lvl in niveis:
        if lvl.Elevation > z_base + 0.1 * FT:
            return lvl
    return None


# Se modo == "auto" ou ck_join ativo, coletar Pisos/Lajes e Vigas do projeto para detectar elementos acima da parede
elementos_topo = []
if op["modo"] == "auto" or op["join"]:
    bics_topo = List[BuiltInCategory]([
        BuiltInCategory.OST_Floors,
        BuiltInCategory.OST_StructuralFraming,
        BuiltInCategory.OST_Roofs,
    ])
    col_topo = (
        FilteredElementCollector(doc)
        .WherePasses(ElementMulticategoryFilter(bics_topo))
        .WhereElementIsNotElementType()
        .ToElements()
    )
    for el_t in col_topo:
        try:
            bb_t = el_t.get_BoundingBox(None)
            if bb_t is not None:
                # Ignorar pisos finos de acabamento (< 4 cm) para que a parede encoste na laje estrutural
                if isinstance(el_t, Floor):
                    ft = doc.GetElement(el_t.GetTypeId())
                    cs = ft.GetCompoundStructure()
                    if cs is not None and cs.GetWidth() < 0.04 * FT:
                        continue
                elementos_topo.append((el_t, bb_t))
        except Exception:
            pass


def encontrar_elemento_acima(wall, z_base, z_busca_max):
    """
    Localiza a laje/piso/viga que cobre horizontalmente a parede e retorna
    (menor_z_inferior, lista_elementos_que_interceptam).
    """
    bb_w = wall.get_BoundingBox(None)
    if bb_w is None:
        return (None, [])

    tol_xy = 0.02 * FT
    z_min_corte = None
    interceptados = []

    for el_t, bb_t in elementos_topo:
        # Verificar sobreposição em X e Y
        if bb_w.Max.X - tol_xy < bb_t.Min.X or bb_t.Max.X - tol_xy < bb_w.Min.X:
            continue
        if bb_w.Max.Y - tol_xy < bb_t.Min.Y or bb_t.Max.Y - tol_xy < bb_w.Min.Y:
            continue
        # Verificar se a base da laje/viga está acima da base da parede (ex.: > 1,50 m acima da base)
        if (bb_t.Min.Z > z_base + 1.20 * FT) and (bb_t.Min.Z <= z_busca_max + 0.80 * FT):
            interceptados.append(el_t)
            if z_min_corte is None or bb_t.Min.Z < z_min_corte:
                z_min_corte = bb_t.Min.Z

    return (z_min_corte, interceptados)


ajustadas = 0
erros = 0

top_offset_padrao  = op["top_cm"] / 30.48
base_offset_padrao = op["base_cm"] / 30.48
altura_fixa_ft     = op["altura_m"] * FT

with Transaction(doc, "Ajustar Altura de Paredes em Lote") as t:
    t.Start()

    for wall in walls:
        try:
            p_base_lvl = wall.get_Parameter(BuiltInParameter.WALL_BASE_CONSTRAINT)
            p_base_off = wall.get_Parameter(BuiltInParameter.WALL_BASE_OFFSET)
            p_top_lvl  = wall.get_Parameter(BuiltInParameter.WALL_HEIGHT_TYPE)
            p_top_off  = wall.get_Parameter(BuiltInParameter.WALL_TOP_OFFSET)
            p_unconn   = wall.get_Parameter(BuiltInParameter.WALL_USER_HEIGHT_PARAM)

            if p_base_off and not p_base_off.IsReadOnly:
                p_base_off.Set(base_offset_padrao)

            base_lvl = doc.GetElement(p_base_lvl.AsElementId()) if p_base_lvl else None
            z_base = base_lvl.Elevation if base_lvl else 0.0
            lvl_sup = nivel_imediatamente_acima(base_lvl.Id) if base_lvl else None
            z_sup = lvl_sup.Elevation if lvl_sup else (z_base + altura_fixa_ft)

            if op["modo"] == "fixa":
                if p_top_lvl and not p_top_lvl.IsReadOnly:
                    p_top_lvl.Set(ElementId.InvalidElementId)
                if p_unconn and not p_unconn.IsReadOnly:
                    p_unconn.Set(altura_fixa_ft)
                ajustadas += 1

            elif op["modo"] == "nivel":
                if lvl_sup and p_top_lvl and not p_top_lvl.IsReadOnly:
                    p_top_lvl.Set(lvl_sup.Id)
                    if p_top_off and not p_top_off.IsReadOnly:
                        p_top_off.Set(top_offset_padrao)
                else:
                    if p_top_lvl and not p_top_lvl.IsReadOnly:
                        p_top_lvl.Set(ElementId.InvalidElementId)
                    if p_unconn and not p_unconn.IsReadOnly:
                        p_unconn.Set((z_sup - z_base) + top_offset_padrao)
                ajustadas += 1

            elif op["modo"] == "auto":
                z_fundo_laje, el_interceptados = encontrar_elemento_acima(wall, z_base, z_sup)
                if lvl_sup and p_top_lvl and not p_top_lvl.IsReadOnly:
                    p_top_lvl.Set(lvl_sup.Id)
                    if z_fundo_laje is not None:
                        desconto = z_fundo_laje - z_sup
                    else:
                        desconto = top_offset_padrao
                    if p_top_off and not p_top_off.IsReadOnly:
                        p_top_off.Set(desconto)
                else:
                    if p_top_lvl and not p_top_lvl.IsReadOnly:
                        p_top_lvl.Set(ElementId.InvalidElementId)
                    nova_alt = (z_fundo_laje - z_base) if z_fundo_laje is not None else altura_fixa_ft
                    if p_unconn and not p_unconn.IsReadOnly and nova_alt > 0.2 * FT:
                        p_unconn.Set(nova_alt)

                if op["join"] and el_interceptados:
                    for el_t in el_interceptados:
                        try:
                            if not JoinGeometryUtils.AreElementsJoined(doc, el_t, wall):
                                JoinGeometryUtils.JoinGeometry(doc, el_t, wall)
                            elif not JoinGeometryUtils.IsCuttingElementInJoin(doc, el_t, wall):
                                JoinGeometryUtils.SwitchJoinOrder(doc, el_t, wall)
                        except Exception:
                            pass
                ajustadas += 1

        except Exception as e:
            erros += 1
            print("Erro na parede {}: {}".format(wall.Id, str(e)))

    t.Commit()

forms.alert(
    u"Concluído!\n\n"
    u"✔ Paredes ajustadas: {}\n"
    u"✘ Com erro: {}".format(ajustadas, erros),
    title="Altura de Paredes em Lote",
    warn_icon=False
)
