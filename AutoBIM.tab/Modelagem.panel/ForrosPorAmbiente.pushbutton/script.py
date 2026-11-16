# -*- coding: utf-8 -*-
__title__ = "Forros por\nAmbiente"
__author__ = "AutoBIM"
__version__ = "Versão 1.0"
__doc__ = """Cria forros automaticamente em lote nos ambientes da vista ativa ou selecionados, com opção de recuo perimetral para tabica/sanca.

Diferenciais:
- Permite criar em todos os ambientes da vista de uma só vez (com checklist para desmarcar áreas descobertas ou shafts).
- Opção de Recuo de Tabica (cm): afasta o contorno do forro em relação às paredes (ex.: 2 cm ou 3 cm).
- Configura Tipo de Forro, Nível e Pé-direito (Altura em metros) em lote."""

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

doc   = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument
view  = doc.ActiveView

FT = 1.0 / 0.3048
SETTINGS_PATH = os.path.join(os.path.dirname(__file__), "_ceilings_settings.json")


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
        Title="Forros por Ambiente (Em Lote + Tabica)" Width="450" SizeToContent="Height"
        WindowStartupLocation="CenterScreen" ResizeMode="NoResize"
        Background="#F3F3F3" FontFamily="Segoe UI" FontSize="12">
    <StackPanel Margin="18">
        <TextBlock Text="Modo de seleção dos ambientes" FontWeight="SemiBold"/>
        <ComboBox x:Name="cb_escopo" Margin="0,4,0,12" Height="26"/>

        <TextBlock Text="Tipo de Forro" FontWeight="SemiBold"/>
        <ComboBox x:Name="cb_ceiling" Margin="0,4,0,12" Height="26"/>

        <Grid Margin="0,0,0,12">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="12"/>
                <ColumnDefinition Width="140"/>
            </Grid.ColumnDefinitions>
            <StackPanel Grid.Column="0">
                <TextBlock Text="Nível de referência" FontWeight="SemiBold"/>
                <ComboBox x:Name="cb_level" Margin="0,4,0,0" Height="26"/>
            </StackPanel>
            <StackPanel Grid.Column="2">
                <TextBlock Text="Pé-direito / Altura (m)" FontWeight="SemiBold"/>
                <TextBox x:Name="tb_altura" Margin="0,4,0,0" Height="26"
                         VerticalContentAlignment="Center" Text="2,60"/>
            </StackPanel>
        </Grid>

        <Grid Margin="0,0,0,12">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="12"/>
                <ColumnDefinition Width="140"/>
            </Grid.ColumnDefinitions>
            <StackPanel Grid.Column="0" VerticalAlignment="Center">
                <TextBlock Text="Recuo perimetral para Tabica / Sanca (cm)" FontWeight="SemiBold"/>
                <TextBlock Text="Use 0 para encostar na parede, ou ex.: 2 ou 3 para tabica"
                           FontSize="10.5" Foreground="#555555"/>
            </StackPanel>
            <StackPanel Grid.Column="2">
                <TextBox x:Name="tb_tabica" Margin="0,4,0,0" Height="26"
                         VerticalContentAlignment="Center" Text="0"/>
            </StackPanel>
        </Grid>

        <StackPanel Orientation="Horizontal" HorizontalAlignment="Right" Margin="0,14,0,0">
            <Button x:Name="btn_cancel" Content="Cancelar" Width="90" Height="30" Margin="0,0,8,0"/>
            <Button x:Name="btn_ok" Content="Continuar" Width="150" Height="30"
                    IsDefault="True" Background="#0078D4" Foreground="White"/>
        </StackPanel>
    </StackPanel>
</Window>
"""


class ForrosWindow(forms.WPFWindow):
    def __init__(self, nomes_forros, nomes_niveis, nivel_vista_idx, prefs):
        forms.WPFWindow.__init__(self, XAML, literal_string=True)
        self.confirmado = False
        self.resultado = {}

        self._cb_escopo  = self.FindName("cb_escopo")
        self._cb_ceiling = self.FindName("cb_ceiling")
        self._cb_level   = self.FindName("cb_level")
        self._tb_altura  = self.FindName("tb_altura")
        self._tb_tabica  = self.FindName("tb_tabica")

        for item in (u"Listar todos os ambientes da vista ativa (Checklist)",
                     u"Selecionar ambientes manualmente na planta"):
            self._cb_escopo.Items.Add(item)

        for n in nomes_forros:
            self._cb_ceiling.Items.Add(n)
        for n in nomes_niveis:
            self._cb_level.Items.Add(n)

        self._cb_escopo.SelectedIndex = int(prefs.get("escopo_idx", 0))
        ultimo_forro = prefs.get("ceiling", "")
        self._cb_ceiling.SelectedIndex = nomes_forros.index(ultimo_forro) if ultimo_forro in nomes_forros else 0
        self._cb_level.SelectedIndex   = nivel_vista_idx
        self._tb_altura.Text           = str(prefs.get("altura_m", "2,60"))
        self._tb_tabica.Text           = str(prefs.get("tabica_cm", "0"))

        self.FindName("btn_ok").Click     += self.on_ok
        self.FindName("btn_cancel").Click += self.on_cancel

    def on_cancel(self, sender, args):
        self.confirmado = False
        self.Close()

    def on_ok(self, sender, args):
        try:
            altura_m  = float(self._tb_altura.Text.replace(",", "."))
            tabica_cm = float(self._tb_tabica.Text.replace(",", "."))
        except Exception:
            forms.alert("Informe valores numéricos válidos para altura e tabica.")
            return

        if tabica_cm < 0 or tabica_cm > 50:
            forms.alert("O recuo de tabica deve estar entre 0 e 50 cm.")
            return

        self.resultado = {
            "escopo_idx":  self._cb_escopo.SelectedIndex,
            "ceiling_idx": self._cb_ceiling.SelectedIndex,
            "level_idx":   self._cb_level.SelectedIndex,
            "altura_m":    altura_m,
            "tabica_cm":   tabica_cm,
        }
        self.confirmado = True
        self.Close()


# =============================================================================
# COLETAR DADOS DO PROJETO
# =============================================================================
def nome_tipo(el):
    try:
        p = el.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME)
        if p is not None and p.AsString():
            return p.AsString()
    except Exception:
        pass
    return el.Name


ceiling_types = list(FilteredElementCollector(doc).OfClass(CeilingType).ToElements())
if not ceiling_types:
    forms.alert("Nenhum tipo de forro encontrado no projeto.", exitscript=True)

ceiling_types.sort(key=lambda c: nome_tipo(c).lower())
nomes_forros = [nome_tipo(c) for c in ceiling_types]

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
win = ForrosWindow(nomes_forros, nomes_niveis, nivel_vista, prefs)
win.ShowDialog()

if not win.confirmado:
    script.exit()

op = win.resultado
ceiling_type = ceiling_types[op["ceiling_idx"]]
nivel        = niveis[op["level_idx"]]
altura_ft    = op["altura_m"] * FT
tabica_ft    = op["tabica_cm"] / 30.48

op["ceiling"] = nomes_forros[op["ceiling_idx"]]
salvar_preferencias(op)


def nome_ambiente(room):
    try:
        n = room.get_Parameter(BuiltInParameter.ROOM_NAME).AsString()
        num = room.Number
        return u"{} - {}".format(num, n) if num else n
    except Exception:
        return str(room.Id)


# =============================================================================
# SELECIONAR AMBIENTES (CHECKLIST DA VISTA OU SELEÇÃO MANUAL)
# =============================================================================
rooms = []
if op["escopo_idx"] == 0:
    todos_vista = [
        r for r in FilteredElementCollector(doc, view.Id)
        .OfCategory(BuiltInCategory.OST_Rooms)
        .WhereElementIsNotElementType()
        .ToElements()
        if isinstance(r, Room) and r.Area > 0
    ]
    if not todos_vista:
        forms.alert("Nenhum ambiente encontrado na vista ativa.", exitscript=True)

    mapa_rooms = {}
    for r in todos_vista:
        chave = u"{}  ({:.1f} m²)".format(nome_ambiente(r), r.Area * 0.092903)
        mapa_rooms[chave] = r

    selecionados_nomes = forms.SelectFromList.show(
        sorted(mapa_rooms.keys()),
        title="Selecione os Ambientes que receberão Forro",
        button_name="Criar Forros",
        multiselect=True
    )
    if not selecionados_nomes:
        script.exit()
    rooms = [mapa_rooms[k] for k in selecionados_nomes]
else:
    for eid in uidoc.Selection.GetElementIds():
        el = doc.GetElement(eid)
        if isinstance(el, Room) and el.Area > 0:
            rooms.append(el)
    if not rooms:
        with forms.WarningBar(title="Selecione os Ambientes para criar o forro e clique em Finish"):
            rooms = revit.pick_elements_by_category(BuiltInCategory.OST_Rooms)
    rooms = [r for r in (rooms or []) if r.Area > 0]

if not rooms:
    forms.alert("Nenhum ambiente válido selecionado.", exitscript=True)


# =============================================================================
# FUNÇÕES GEOMÉTRICAS E CRIAÇÃO DOS FORROS
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


def make_ccw_curves(curves):
    pts = [c.GetEndPoint(0) for c in curves]
    if signed_area_pts(pts) >= 0:
        return curves
    return [c.CreateReversed() for c in reversed(curves)]


boundary_opts = SpatialElementBoundaryOptions()
boundary_opts.SpatialElementBoundaryLocation = SpatialElementBoundaryLocation.Finish

z0 = nivel.Elevation
criados = 0
erros = 0

with Transaction(doc, "Criar Forros por Ambiente") as t:
    t.Start()

    for room in rooms:
        try:
            seg_lists = room.GetBoundarySegments(boundary_opts)
            if not seg_lists or seg_lists.Count == 0:
                continue

            lacos_curvas = []
            for sl in seg_lists:
                curvas = [flatten(seg.GetCurve(), z0) for seg in sl]
                if len(curvas) >= 3:
                    lacos_curvas.append(curvas)

            if not lacos_curvas:
                continue

            lacos_curvas.sort(
                key=lambda cs: abs(signed_area_pts([c.GetEndPoint(0) for c in cs])),
                reverse=True
            )

            outer_curves = make_ccw_curves(lacos_curvas[0])
            outer_list = List[Curve]()
            for c in outer_curves:
                outer_list.Add(c)
            outer_loop = CurveLoop.Create(outer_list)

            # Aplicar recuo perimetral (tabica/sanca) se > 0
            if tabica_ft > 1e-4:
                try:
                    outer_loop = CurveLoop.CreateViaOffset(outer_loop, -tabica_ft, XYZ.BasisZ)
                except Exception:
                    pass

            loops_finais = List[CurveLoop]()
            loops_finais.Add(outer_loop)

            for ilha_cs in lacos_curvas[1:]:
                ilha_list = List[Curve]()
                for c in ilha_cs:
                    ilha_list.Add(c)
                loops_finais.Add(CurveLoop.Create(ilha_list))

            forro = Ceiling.Create(doc, loops_finais, ceiling_type.Id, nivel.Id)
            if forro:
                p_off = forro.get_Parameter(BuiltInParameter.CEILING_HEIGHTABOVELEVEL_PARAM)
                if p_off and not p_off.IsReadOnly:
                    p_off.Set(altura_ft)
                criados += 1

        except Exception as e:
            erros += 1
            print("Erro no ambiente {}: {}".format(nome_ambiente(room), str(e)))

    t.Commit()

forms.alert(
    u"Concluído!\n\n"
    u"✔ Forros criados: {}\n"
    u"✘ Com erro: {}".format(criados, erros),
    title="Forros por Ambiente",
    warn_icon=False
)
