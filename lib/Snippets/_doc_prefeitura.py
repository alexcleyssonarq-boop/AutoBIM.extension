# -*- coding: utf-8 -*-
"""Quadro de Areas de Prefeitura (AutoBIM - Painel Documentacao):
Calcula areas construidas por pavimento, area do terreno, area permeavel e indices
urbanisticos (TO, CA, TP e Taxa de Impermeabilidade), gerando:
1. Vista de Desenho (ViewDrafting) com tabela grafica pronta para prancha
2. Arquivo CSV formatado no padrao brasileiro (Excel BR)
"""

import codecs
import json
import os
import re
import clr

clr.AddReference("System")
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("System.Windows.Forms")

from System.Windows.Forms import SaveFileDialog, DialogResult as WinFormsDialogResult
from Autodesk.Revit.DB import *
from Autodesk.Revit.DB.Architecture import Room
from pyrevit import forms, script


doc = None
uidoc = None
view = None

SQFT_TO_M2 = 0.09290304
MM = 1.0 / 304.8
SETTINGS_PATH = os.path.join(os.path.dirname(__file__), "_doc_prefeitura_settings.json")

KW_EXTERNOS = (u"descobert", u"jardim", u"quintal", u"terreno", u"recuo", u"grama", u"lote", u"calçada", u"calcada")
KW_PERMEAVEIS = (u"grama", u"perme", u"jardim", u"brita", u"solo", u"terra", u"canteiro", u"vegeta")
KW_TERRENO = (u"terreno", u"lote")


def _init_ctx():
    global doc, uidoc, view
    uidoc = __revit__.ActiveUIDocument
    doc = uidoc.Document
    view = doc.ActiveView


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

def _fmt_br(valor, casas=2):
    """Formata numero float no padrao brasileiro (ex.: 1234.5 -> '1.234,50')."""
    fmt = u"{:,.%df}" % int(casas)
    s = fmt.format(float(valor or 0.0))
    return s.replace(u",", u"_").replace(u".", u",").replace(u"_", u".")


def _parse_float_br(texto, default=0.0):
    s = unicode(texto or u"").strip()
    if not s:
        return default
    if u"," in s and u"." in s:
        s = s.replace(u".", u"").replace(u",", u".")
    elif u"," in s:
        s = s.replace(u",", u".")
    try:
        return float(s)
    except Exception:
        return default


def _calcular_indices_prefeitura(
    area_terreno_m2,
    areas_pavimentos,
    area_projecao_m2=None,
    area_permeavel_m2=0.0,
    to_max_pct=None,
    ca_max=None,
    tp_min_pct=None,
):
    """Calcula TO, CA, TP e Taxa de Impermeabilidade.

    areas_pavimentos: lista de tuplas [(nome_nivel, area_m2), ...]
    Retorna dicionario estruturado com totais, indices e status de atendimento.
    """
    terreno = max(0.0, float(area_terreno_m2 or 0.0))
    pavs = [(unicode(n), max(0.0, float(a or 0.0))) for n, a in (areas_pavimentos or [])]
    total_construida = sum(a for _, a in pavs)

    if area_projecao_m2 is None:
        projecao = max([a for _, a in pavs]) if pavs else 0.0
    else:
        projecao = max(0.0, float(area_projecao_m2))

    permeavel = max(0.0, float(area_permeavel_m2 or 0.0))

    if terreno > 1e-6:
        to_pct = (projecao / terreno) * 100.0
        ca_val = total_construida / terreno
        tp_pct = (permeavel / terreno) * 100.0
        ti_pct = max(0.0, 100.0 - tp_pct)
    else:
        to_pct = 0.0
        ca_val = 0.0
        tp_pct = 0.0
        ti_pct = 0.0

    status_to = u"-"
    if to_max_pct is not None and to_max_pct > 0:
        status_to = u"ATENDE (máx. {}%)".format(_fmt_br(to_max_pct, 1)) if to_pct <= to_max_pct + 1e-6 else u"EXCEDE (máx. {}%)".format(_fmt_br(to_max_pct, 1))

    status_ca = u"-"
    if ca_max is not None and ca_max > 0:
        status_ca = u"ATENDE (máx. {})".format(_fmt_br(ca_max, 2)) if ca_val <= ca_max + 1e-6 else u"EXCEDE (máx. {})".format(_fmt_br(ca_max, 2))

    status_tp = u"-"
    if tp_min_pct is not None and tp_min_pct > 0:
        status_tp = u"ATENDE (mín. {}%)".format(_fmt_br(tp_min_pct, 1)) if tp_pct + 1e-6 >= tp_min_pct else u"ABAIXO (mín. {}%)".format(_fmt_br(tp_min_pct, 1))

    return {
        "area_terreno": terreno,
        "pavimentos": pavs,
        "area_total_construida": total_construida,
        "area_projecao": projecao,
        "area_permeavel": permeavel,
        "to_pct": to_pct,
        "ca_val": ca_val,
        "tp_pct": tp_pct,
        "ti_pct": ti_pct,
        "status_to": status_to,
        "status_ca": status_ca,
        "status_tp": status_tp,
    }


def _montar_linhas_tabela(res):
    """Retorna lista de linhas (Item, Valor, Observacao/Status) para o Quadro."""
    linhas = [
        (u"Área do Terreno", u"{} m²".format(_fmt_br(res["area_terreno"])), u"Lote / Gleba"),
    ]
    for nome_pav, area_m2 in res["pavimentos"]:
        linhas.append((u"Área Construída - {}".format(nome_pav), u"{} m²".format(_fmt_br(area_m2)), u"Pavimento"))

    linhas.append((u"Área Total Construída", u"{} m²".format(_fmt_br(res["area_total_construida"])), u"Somatório"))
    linhas.append((u"Área de Projeção (Ocupada)", u"{} m²".format(_fmt_br(res["area_projecao"])), u"Base para TO"))
    linhas.append((u"Área Permeável", u"{} m²".format(_fmt_br(res["area_permeavel"])), u"Solo / Vegetação"))
    linhas.append((u"Taxa de Ocupação (TO)", u"{}%".format(_fmt_br(res["to_pct"])), res["status_to"]))
    linhas.append((u"Coeficiente de Aproveitamento (CA)", _fmt_br(res["ca_val"], 2), res["status_ca"]))
    linhas.append((u"Taxa de Permeabilidade (TP)", u"{}%".format(_fmt_br(res["tp_pct"])), res["status_tp"]))
    linhas.append((u"Taxa de Impermeabilidade", u"{}%".format(_fmt_br(res["ti_pct"])), u"100% - TP"))
    return linhas


def _gerar_csv_prefeitura(res):
    linhas = [u"Item;Valor;Observação / Legislação"]
    for item, val, obs in _montar_linhas_tabela(res):
        linhas.append(u"{};{};{}".format(item, val, obs))
    return u"\r\n".join(linhas) + u"\r\n"


# ==============================================================================
# LEITURA DO MODELO REVIT
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


def _area_m2_param(el):
    try:
        p = el.get_Parameter(BuiltInParameter.HOST_AREA_COMPUTED)
        if p is not None and p.HasValue:
            return float(p.AsDouble()) * SQFT_TO_M2
    except Exception:
        pass
    try:
        a = getattr(el, "Area", 0.0)
        if a:
            return float(a) * SQFT_TO_M2
    except Exception:
        pass
    return 0.0


def _coletar_areas_por_nivel(documento, fonte=0, ignorar_externos=True):
    """Retorna lista ordenada por elevacao: [(nome_nivel, area_m2), ...].
    fonte: 0=Ambientes (Rooms), 1=Planos de Area (Areas), 2=Pisos (Floors).
    """
    niveis_elev = {}
    somas = {}

    def _add(level_el, area_m2):
        if area_m2 <= 1e-6:
            return
        if level_el is not None:
            nome = _elem_name(level_el)
            elev = float(getattr(level_el, "Elevation", 0.0))
        else:
            nome = u"Sem Nível"
            elev = 0.0
        niveis_elev[nome] = elev
        somas[nome] = somas.get(nome, 0.0) + area_m2

    if fonte == 0:
        rooms = FilteredElementCollector(documento).OfCategory(
            BuiltInCategory.OST_Rooms
        ).WhereElementIsNotElementType().ToElements()
        for r in rooms:
            a = _area_m2_param(r)
            if a <= 1e-6:
                continue
            rname = _elem_name(r).lower()
            if ignorar_externos and any(kw in rname for kw in KW_EXTERNOS):
                continue
            lvl = documento.GetElement(r.LevelId) if getattr(r, "LevelId", None) else None
            _add(lvl, a)
    elif fonte == 1:
        areas = FilteredElementCollector(documento).OfCategory(
            BuiltInCategory.OST_Areas
        ).WhereElementIsNotElementType().ToElements()
        for ar in areas:
            a = _area_m2_param(ar)
            if a <= 1e-6:
                continue
            aname = _elem_name(ar).lower()
            if any(kw in aname for kw in KW_TERRENO) or any(kw in aname for kw in KW_PERMEAVEIS):
                continue
            lvl = documento.GetElement(ar.LevelId) if getattr(ar, "LevelId", None) else None
            _add(lvl, a)
    else:
        floors = FilteredElementCollector(documento).OfClass(Floor).WhereElementIsNotElementType().ToElements()
        for fl in floors:
            a = _area_m2_param(fl)
            if a <= 1e-6:
                continue
            ftype = documento.GetElement(fl.GetTypeId())
            fname = (_elem_name(ftype) + u" " + _elem_name(fl)).lower()
            if ignorar_externos and (any(kw in fname for kw in KW_PERMEAVEIS) or any(kw in fname for kw in KW_TERRENO)):
                continue
            lvl = documento.GetElement(fl.LevelId) if getattr(fl, "LevelId", None) else None
            _add(lvl, a)

    nomes_ord = sorted(somas.keys(), key=lambda n: niveis_elev.get(n, 0.0))
    return [(n, somas[n]) for n in nomes_ord]


def _detectar_terreno_e_permeavel(documento):
    """Tenta detectar automaticamente area do terreno e area permeavel (m2)."""
    terreno = 0.0
    permeavel = 0.0

    # 1. PropertyLine (Linhas de Divisa)
    try:
        props = FilteredElementCollector(documento).OfCategory(
            BuiltInCategory.OST_SiteProperty
        ).WhereElementIsNotElementType().ToElements()
        for pr in props:
            a = _area_m2_param(pr)
            if a > terreno:
                terreno = a
    except Exception:
        pass

    # 2. Pisos e Ambientes com palavras-chave de terreno / area permeavel
    try:
        floors = FilteredElementCollector(documento).OfClass(Floor).WhereElementIsNotElementType().ToElements()
        for fl in floors:
            a = _area_m2_param(fl)
            if a <= 1e-6:
                continue
            ftype = documento.GetElement(fl.GetTypeId())
            fname = (_elem_name(ftype) + u" " + _elem_name(fl)).lower()
            if terreno <= 1e-6 and any(kw in fname for kw in KW_TERRENO):
                terreno += a
            if any(kw in fname for kw in KW_PERMEAVEIS):
                permeavel += a
    except Exception:
        pass

    if permeavel <= 1e-6:
        try:
            rooms = FilteredElementCollector(documento).OfCategory(
                BuiltInCategory.OST_Rooms
            ).WhereElementIsNotElementType().ToElements()
            for r in rooms:
                a = _area_m2_param(r)
                if a > 1e-6 and any(kw in _elem_name(r).lower() for kw in KW_PERMEAVEIS):
                    permeavel += a
        except Exception:
            pass

    return (terreno, permeavel)


def _criar_vista_desenho_quadro(documento, res, titulo_vista=u"AutoBIM - Quadro de Áreas (Prefeitura)"):
    """Cria ViewDrafting e desenha tabela grafica limpa com DetailCurves e TextNotes."""
    vft = None
    for vt in FilteredElementCollector(documento).OfClass(ViewFamilyType).ToElements():
        if vt.ViewFamily == ViewFamily.Drafting:
            vft = vt
            break
    if vft is None:
        raise Exception(u"Nenhum ViewFamilyType de Vista de Desenho (Drafting) encontrado.")

    txt_types = list(FilteredElementCollector(documento).OfClass(TextNoteType).ToElements())
    if not txt_types:
        raise Exception(u"Nenhum TextNoteType encontrado no projeto.")
    txt_types.sort(key=lambda t: _elem_name(t).lower())
    txt_type = txt_types[0]

    # Nome unico
    existentes = set(_elem_name(v) for v in FilteredElementCollector(documento).OfClass(ViewDrafting).ToElements())
    nome_final = titulo_vista
    idx_n = 2
    while nome_final in existentes:
        nome_final = u"{} ({})".format(titulo_vista, idx_n)
        idx_n += 1

    vd = ViewDrafting.Create(documento, vft.Id)
    vd.Name = nome_final
    vd.Scale = 1

    linhas_dados = _montar_linhas_tabela(res)
    # Dimensoes em mm (escala 1:1 -> tamanho real na folha)
    w_col1 = 78.0 * MM
    w_col2 = 34.0 * MM
    w_col3 = 48.0 * MM
    w_tot = w_col1 + w_col2 + w_col3
    h_tit = 9.0 * MM
    h_cab = 7.0 * MM
    h_row = 6.5 * MM
    pad_x = 2.0 * MM
    pad_y = 1.5 * MM

    n_rows = len(linhas_dados)
    h_tot = h_tit + h_cab + n_rows * h_row

    def _ln(x0, y0, x1, y1):
        documento.Create.NewDetailCurve(vd, Line.CreateBound(XYZ(x0, y0, 0.0), XYZ(x1, y1, 0.0)))

    def _txt(x, y, texto):
        TextNote.Create(documento, vd.Id, XYZ(x, y, 0.0), unicode(texto), txt_type.Id)

    # Moldura externa
    _ln(0.0, 0.0, w_tot, 0.0)
    _ln(0.0, -h_tot, w_tot, -h_tot)
    _ln(0.0, 0.0, 0.0, -h_tot)
    _ln(w_tot, 0.0, w_tot, -h_tot)

    # Titulo
    _ln(0.0, -h_tit, w_tot, -h_tit)
    _txt(pad_x, -pad_y, u"QUADRO DE ÁREAS — PREFEITURA (AutoBIM)")

    # Cabecalho
    y_cab = -h_tit
    _ln(0.0, y_cab - h_cab, w_tot, y_cab - h_cab)
    _ln(w_col1, y_cab, w_col1, -h_tot)
    _ln(w_col1 + w_col2, y_cab, w_col1 + w_col2, -h_tot)

    _txt(pad_x, y_cab - pad_y, u"DESCRIÇÃO")
    _txt(w_col1 + pad_x, y_cab - pad_y, u"VALOR")
    _txt(w_col1 + w_col2 + pad_x, y_cab - pad_y, u"OBSERVAÇÃO / ÍNDICE")

    # Linhas
    for i, (desc, val, obs) in enumerate(linhas_dados):
        y_top = y_cab - h_cab - i * h_row
        if i > 0:
            _ln(0.0, y_top, w_tot, y_top)
        _txt(pad_x, y_top - pad_y, desc)
        _txt(w_col1 + pad_x, y_top - pad_y, val)
        _txt(w_col1 + w_col2 + pad_x, y_top - pad_y, obs)

    return vd


# ==============================================================================
# INTERFACE WPF
# ==============================================================================

XAML_PREFEITURA = """<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="AutoBIM - Quadro de Áreas de Prefeitura"
        Width="560" Height="610" ResizeMode="CanResize"
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
            <TextBlock Text="Quadro de Áreas de Prefeitura (TO / CA / TP)" FontSize="15" FontWeight="Bold" Foreground="#1E2D41"/>
            <TextBlock Text="Calcula áreas por pavimento e índices urbanísticos, gerando quadro pronto para prancha." FontSize="11.5" Foreground="#5A6878"/>
        </StackPanel>

        <GroupBox Grid.Row="1" Header="1. Fonte de Leitura e Dados do Terreno" Padding="8" Margin="0,0,0,8" Background="White">
            <Grid>
                <Grid.ColumnDefinitions>
                    <ColumnDefinition Width="165"/>
                    <ColumnDefinition Width="*"/>
                    <ColumnDefinition Width="Auto"/>
                </Grid.ColumnDefinitions>
                <Grid.RowDefinitions>
                    <RowDefinition Height="Auto"/>
                    <RowDefinition Height="Auto"/>
                    <RowDefinition Height="Auto"/>
                    <RowDefinition Height="Auto"/>
                    <RowDefinition Height="Auto"/>
                </Grid.RowDefinitions>

                <TextBlock Grid.Row="0" Grid.Column="0" Text="Origem das áreas:" VerticalAlignment="Center" Margin="0,0,6,6"/>
                <ComboBox x:Name="cb_fonte" Grid.Row="0" Grid.Column="1" Height="25" Margin="0,0,6,6">
                    <ComboBoxItem Content="Ambientes (Rooms) por Nível"/>
                    <ComboBoxItem Content="Planos de Área (Areas) por Nível"/>
                    <ComboBoxItem Content="Pisos (Floors) por Nível"/>
                </ComboBox>
                <Button x:Name="btn_recalc" Grid.Row="0" Grid.Column="2" Content="Recalcular" Padding="10,2" Height="25" Margin="0,0,0,6"/>

                <CheckBox x:Name="ck_ign_ext" Grid.Row="1" Grid.Column="1" Grid.ColumnSpan="2" Content="Ignorar ambientes/pisos externos (jardim, recuo, descoberto)" IsChecked="True" Margin="0,0,0,8"/>

                <TextBlock Grid.Row="2" Grid.Column="0" Text="Área do Terreno (m²):" VerticalAlignment="Center" Margin="0,0,6,6"/>
                <TextBox x:Name="tb_terreno" Grid.Row="2" Grid.Column="1" Grid.ColumnSpan="2" Height="24" VerticalContentAlignment="Center" Padding="4,0" Margin="0,0,0,6"/>

                <TextBlock Grid.Row="3" Grid.Column="0" Text="Área de Projeção (m² / auto):" VerticalAlignment="Center" Margin="0,0,6,6"/>
                <TextBox x:Name="tb_proj" Grid.Row="3" Grid.Column="1" Grid.ColumnSpan="2" Height="24" VerticalContentAlignment="Center" Padding="4,0" Margin="0,0,0,6" ToolTip="Deixe vazio para usar automaticamente a maior área de pavimento"/>

                <TextBlock Grid.Row="4" Grid.Column="0" Text="Área Permeável (m²):" VerticalAlignment="Center" Margin="0,0,6,0"/>
                <TextBox x:Name="tb_perm" Grid.Row="4" Grid.Column="1" Grid.ColumnSpan="2" Height="24" VerticalContentAlignment="Center" Padding="4,0"/>
            </Grid>
        </GroupBox>

        <GroupBox Grid.Row="2" Header="2. Prévia dos Pavimentos e Índices Calculados" Padding="8" Margin="0,0,0,8" Background="White">
            <TextBox x:Name="tb_preview" IsReadOnly="True" VerticalScrollBarVisibility="Auto" FontFamily="Consolas" FontSize="11.5" Background="#FAFBFD" BorderThickness="1" Padding="6" TextWrapping="Wrap"/>
        </GroupBox>

        <GroupBox Grid.Row="3" Header="3. Limites da Legislação (opcional) e Saída" Padding="8" Margin="0,0,0,10" Background="White">
            <StackPanel>
                <Grid Margin="0,0,0,8">
                    <Grid.ColumnDefinitions>
                        <ColumnDefinition Width="*"/>
                        <ColumnDefinition Width="8"/>
                        <ColumnDefinition Width="*"/>
                        <ColumnDefinition Width="8"/>
                        <ColumnDefinition Width="*"/>
                    </Grid.ColumnDefinitions>
                    <StackPanel Grid.Column="0">
                        <TextBlock Text="TO máx. (%):" FontSize="11" Foreground="#5A6878"/>
                        <TextBox x:Name="tb_to_max" Height="24" VerticalContentAlignment="Center" Padding="4,0"/>
                    </StackPanel>
                    <StackPanel Grid.Column="2">
                        <TextBlock Text="CA máx.:" FontSize="11" Foreground="#5A6878"/>
                        <TextBox x:Name="tb_ca_max" Height="24" VerticalContentAlignment="Center" Padding="4,0"/>
                    </StackPanel>
                    <StackPanel Grid.Column="4">
                        <TextBlock Text="TP mín. (%):" FontSize="11" Foreground="#5A6878"/>
                        <TextBox x:Name="tb_tp_min" Height="24" VerticalContentAlignment="Center" Padding="4,0"/>
                    </StackPanel>
                </Grid>

                <CheckBox x:Name="ck_drafting" Content="Gerar Vista de Desenho (Quadro Gráfico pronto para prancha)" IsChecked="True" Margin="0,0,0,4"/>
                <CheckBox x:Name="ck_csv" Content="Exportar também em CSV (padrão Excel Brasil)" IsChecked="False"/>
            </StackPanel>
        </GroupBox>

        <StackPanel Grid.Row="4" Orientation="Horizontal" HorizontalAlignment="Right">
            <Button x:Name="btn_cancel" Content="Cancelar" Width="95" Height="30" Margin="0,0,8,0"/>
            <Button x:Name="btn_ok" Content="Gerar Quadro" Width="135" Height="30" Background="#0072C6" Foreground="White" FontWeight="SemiBold"/>
        </StackPanel>
    </Grid>
</Window>
"""


class QuadroPrefeituraWindow(forms.WPFWindow):
    def __init__(self, documento, prefs):
        forms.WPFWindow.__init__(self, XAML_PREFEITURA, literal_string=True)
        self._doc = documento
        self._pavs = []
        self._res = None
        self.confirmado = False
        self.resultado = None

        terreno_det, perm_det = _detectar_terreno_e_permeavel(documento)
        self.cb_fonte.SelectedIndex = max(0, min(2, int(prefs.get("qp_fonte", 0) or 0)))
        self.ck_ign_ext.IsChecked = bool(prefs.get("qp_ign_ext", True))

        if terreno_det > 1e-3:
            self.tb_terreno.Text = _fmt_br(terreno_det)
        else:
            self.tb_terreno.Text = unicode(prefs.get("qp_terreno", u"250,00"))

        if perm_det > 1e-3:
            self.tb_perm.Text = _fmt_br(perm_det)
        else:
            self.tb_perm.Text = unicode(prefs.get("qp_perm", u"0,00"))

        self.tb_to_max.Text = unicode(prefs.get("qp_to_max", u""))
        self.tb_ca_max.Text = unicode(prefs.get("qp_ca_max", u""))
        self.tb_tp_min.Text = unicode(prefs.get("qp_tp_min", u""))
        self.ck_drafting.IsChecked = bool(prefs.get("qp_drafting", True))
        self.ck_csv.IsChecked = bool(prefs.get("qp_csv", False))

        self._recalcular()

        self.btn_recalc.Click += self._on_recalc
        self.cb_fonte.SelectionChanged += self._on_recalc
        self.ck_ign_ext.Click += self._on_recalc
        self.tb_terreno.TextChanged += self._on_vals_changed
        self.tb_proj.TextChanged += self._on_vals_changed
        self.tb_perm.TextChanged += self._on_vals_changed
        self.tb_to_max.TextChanged += self._on_vals_changed
        self.tb_ca_max.TextChanged += self._on_vals_changed
        self.tb_tp_min.TextChanged += self._on_vals_changed
        self.btn_cancel.Click += self._on_cancel
        self.btn_ok.Click += self._on_ok

    def _recalcular(self):
        fonte = max(0, self.cb_fonte.SelectedIndex)
        ign = bool(self.ck_ign_ext.IsChecked)
        self._pavs = _coletar_areas_por_nivel(self._doc, fonte=fonte, ignorar_externos=ign)
        self._atualizar_preview()

    def _atualizar_preview(self):
        terreno = _parse_float_br(self.tb_terreno.Text, 0.0)
        proj_txt = unicode(self.tb_proj.Text or u"").strip()
        proj = _parse_float_br(proj_txt, None) if proj_txt else None
        perm = _parse_float_br(self.tb_perm.Text, 0.0)
        to_max = _parse_float_br(self.tb_to_max.Text, None) if unicode(self.tb_to_max.Text or u"").strip() else None
        ca_max = _parse_float_br(self.tb_ca_max.Text, None) if unicode(self.tb_ca_max.Text or u"").strip() else None
        tp_min = _parse_float_br(self.tb_tp_min.Text, None) if unicode(self.tb_tp_min.Text or u"").strip() else None

        self._res = _calcular_indices_prefeitura(
            terreno, self._pavs, area_projecao_m2=proj, area_permeavel_m2=perm,
            to_max_pct=to_max, ca_max=ca_max, tp_min_pct=tp_min,
        )
        linhas = []
        for desc, val, obs in _montar_linhas_tabela(self._res):
            linhas.append(u"{:<38} {:>12}   {}".format(desc, val, obs))
        self.tb_preview.Text = u"\n".join(linhas)

    def _on_recalc(self, s, e):
        self._recalcular()

    def _on_vals_changed(self, s, e):
        self._atualizar_preview()

    def _on_cancel(self, s, e):
        self.Close()

    def _on_ok(self, s, e):
        if not bool(self.ck_drafting.IsChecked) and not bool(self.ck_csv.IsChecked):
            forms.alert(u"Marque ao menos Vista de Desenho ou CSV.", title=u"Aviso")
            return
        if self._res is None or self._res["area_terreno"] <= 0:
            forms.alert(u"Informe uma Área do Terreno válida (> 0 m²).", title=u"Aviso")
            return
        self.resultado = {
            "res": self._res,
            "fonte": max(0, self.cb_fonte.SelectedIndex),
            "ign_ext": bool(self.ck_ign_ext.IsChecked),
            "terreno_txt": unicode(self.tb_terreno.Text or u""),
            "perm_txt": unicode(self.tb_perm.Text or u""),
            "to_max_txt": unicode(self.tb_to_max.Text or u""),
            "ca_max_txt": unicode(self.tb_ca_max.Text or u""),
            "tp_min_txt": unicode(self.tb_tp_min.Text or u""),
            "drafting": bool(self.ck_drafting.IsChecked),
            "csv": bool(self.ck_csv.IsChecked),
        }
        self.confirmado = True
        self.Close()


def run_quadro_areas_prefeitura():
    _init_ctx()
    prefs = _carregar_prefs()
    win = QuadroPrefeituraWindow(doc, prefs)
    win.ShowDialog()
    if not win.confirmado:
        return

    op = win.resultado
    _salvar_prefs({
        "qp_fonte": op["fonte"],
        "qp_ign_ext": op["ign_ext"],
        "qp_terreno": op["terreno_txt"],
        "qp_perm": op["perm_txt"],
        "qp_to_max": op["to_max_txt"],
        "qp_ca_max": op["ca_max_txt"],
        "qp_tp_min": op["tp_min_txt"],
        "qp_drafting": op["drafting"],
        "qp_csv": op["csv"],
    })

    saidas = []
    vd_criada = None

    if op["drafting"]:
        with Transaction(doc, u"AutoBIM - Quadro de Áreas de Prefeitura") as t:
            t.Start()
            vd_criada = _criar_vista_desenho_quadro(doc, op["res"])
            t.Commit()
        saidas.append(u"✔ Vista de Desenho criada: {}".format(vd_criada.Name))
        try:
            uidoc.ActiveView = vd_criada
        except Exception:
            pass

    if op["csv"]:
        dlg = SaveFileDialog()
        dlg.Filter = u"Arquivo CSV (*.csv)|*.csv"
        dlg.FileName = u"Quadro_Areas_Prefeitura.csv"
        if dlg.ShowDialog() == WinFormsDialogResult.OK:
            csv_txt = _gerar_csv_prefeitura(op["res"])
            with codecs.open(unicode(dlg.FileName), "w", encoding="utf-8-sig") as f:
                f.write(csv_txt)
            saidas.append(u"✔ CSV exportado: {}".format(dlg.FileName))

    res = op["res"]
    msg = u"Quadro de Áreas de Prefeitura gerado!\n\n"
    msg += u"• Área Total Construída: {} m²\n".format(_fmt_br(res["area_total_construida"]))
    msg += u"• Taxa de Ocupação (TO): {}%\n".format(_fmt_br(res["to_pct"]))
    msg += u"• Coeficiente (CA): {}\n".format(_fmt_br(res["ca_val"], 2))
    msg += u"• Taxa de Permeabilidade (TP): {}%\n\n".format(_fmt_br(res["tp_pct"]))
    msg += u"\n".join(saidas)
    forms.alert(msg, title=u"AutoBIM: Quadro de Áreas de Prefeitura")
