# -*- coding: utf-8 -*-
"""Ferramentas de Pranchas e Plotagem do Painel Documentacao (AutoBIM):
1. Alinhar Vistas e Titulos nas Pranchas (Viewports)
2. Carimbo e Revisoes em Lote
3. Exportar Pranchas (DWG Unico via AutoCAD accoreconsole + Lote PDF/DWG)
"""

import codecs
import datetime
import glob
import json
import os
import re
import shutil
import subprocess
import tempfile
import clr

clr.AddReference("System")
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("System.Windows.Forms")

from System.Collections.Generic import List
from System.Windows import Thickness, HorizontalAlignment, VerticalAlignment
from System.Windows.Controls import CheckBox as WpfCheckBox
from System.Windows.Forms import FolderBrowserDialog, DialogResult as WinFormsDialogResult
from Autodesk.Revit.DB import *
from pyrevit import forms, script

import Snippets._ui_search as uis
try:
    reload(uis)
except Exception:
    pass


doc = None
uidoc = None
view = None

MM = 1.0 / 304.8
SETTINGS_PATH = os.path.join(os.path.dirname(__file__), "_doc_sheets_settings.json")


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

def _sanitizar_nome_arquivo(nome):
    """Remove caracteres invalidos do Windows preservando legibilidade."""
    s = unicode(nome or u"").strip()
    s = re.sub(r'[\\/:*?"<>|]+', u"-", s)
    s = re.sub(r"\s+", u" ", s).strip(u" .")
    return s or u"Prancha"


def _formatar_nome_prancha(template, numero, nome, prefixo=u"", revisao=u""):
    """Aplica template de nomenclatura ({Prefixo}, {Numero}, {Nome}, {Rev})."""
    tpl = unicode(template or u"{Numero} - {Nome}")
    res = tpl.replace(u"{Prefixo}", unicode(prefixo or u""))
    res = res.replace(u"{Numero}", unicode(numero or u""))
    res = res.replace(u"{Nome}", unicode(nome or u""))
    res = res.replace(u"{Rev}", unicode(revisao or u""))
    # Limpar separadores duplicados caso Prefixo ou Rev estejam vazios
    res = re.sub(r"^[\s_\-]+", u"", res)
    res = re.sub(r"[\s_\-]+$", u"", res)
    res = re.sub(r"__+", u"_", res)
    return _sanitizar_nome_arquivo(res)


def _formatar_folha_x_de_y(idx, total, estilo=0):
    """Formata numeracao de folha: 0='01/08', 1='1 de 8', 2='01 de 08'."""
    if estilo == 1:
        return u"{} de {}".format(idx, total)
    if estilo == 2:
        return u"{:02d} de {:02d}".format(idx, total)
    return u"{:02d}/{:02d}".format(idx, total)


def _calcular_delta_alinhamento(modo, ref_center, dst_center, ref_proj=None, dst_proj=None, ref_tl=None, dst_tl=None):
    """Retorna tupla (dx, dy) em pes para mover dst_center ate alinhar com ref.
    modo: 0=Origem do Modelo, 1=Centro do Viewport, 2=Canto Superior Esquerdo.
    """
    if modo == 0 and ref_proj is not None and dst_proj is not None:
        return (float(ref_proj[0]) - float(dst_proj[0]), float(ref_proj[1]) - float(dst_proj[1]))
    if modo == 2 and ref_tl is not None and dst_tl is not None:
        return (float(ref_tl[0]) - float(dst_tl[0]), float(ref_tl[1]) - float(dst_tl[1]))
    return (float(ref_center[0]) - float(dst_center[0]), float(ref_center[1]) - float(dst_center[1]))


def _gerar_script_accoreconsole(dwg_files, out_dwg, offset_x=500.0):
    """Gera script .scr do AutoCAD accoreconsole para unir multiplos DWGs:
    mantem ModelSpace deslocado lado a lado e importa abas de Layout individuais.
    """
    linhas = [
        u"CMDECHO 0",
        u"FILEDIA 0",
        u"ATTREQ 0",
    ]
    for idx, item in enumerate(dwg_files):
        dwg_path = unicode(item["path"]).replace(u"\\", u"/")
        layout_name = _sanitizar_nome_arquivo(item.get("layout", u"PR{:02d}".format(idx + 1)))
        if idx == 0:
            # Renomeia primeiro layout para o numero da prancha
            linhas.append(u"_TILEMODE 0")
            linhas.append(u"_.-LAYOUT _R \"\" \"{}\"".format(layout_name))
            linhas.append(u"_ZOOM _E")
        else:
            dx = float(idx) * float(offset_x)
            # Insere ModelSpace deslocado lado a lado
            linhas.append(u"_TILEMODE 1")
            linhas.append(u"_.-INSERT \"*{}\" {},0 1 0".format(dwg_path, u"{:.2f}".format(dx)))
            # Importa Layout do DWG secundario
            linhas.append(u"_TILEMODE 0")
            linhas.append(u"_.-LAYOUT _T \"{}\" *".format(dwg_path))
    linhas.append(u"_TILEMODE 1")
    linhas.append(u"_ZOOM _E")
    linhas.append(u"_.-SAVEAS 2018 \"{}\"".format(unicode(out_dwg).replace(u"\\", u"/")))
    linhas.append(u"_QUIT _Y")
    return u"\r\n".join(linhas) + u"\r\n"


def _localizar_accoreconsole():
    """Procura accoreconsole.exe do AutoCAD nas pastas padrao do Windows."""
    padroes = [
        r"C:\Program Files\Autodesk\AutoCAD *\accoreconsole.exe",
        r"C:\Program Files\Autodesk\AutoCAD LT *\accoreconsole.exe",
        r"D:\Program Files\Autodesk\AutoCAD *\accoreconsole.exe",
        r"G:\Arquivos de Programas\Autodesk\AutoCAD *\accoreconsole.exe",
    ]
    encontrados = []
    for pat in padroes:
        try:
            encontrados.extend(glob.glob(pat))
        except Exception:
            pass
    if not encontrados:
        return u""
    encontrados.sort(key=_natural_key, reverse=True)
    return unicode(encontrados[0])


# ==============================================================================
# HELPERS REVIT DE PRANCHAS
# ==============================================================================

def _listar_pranchas(documento):
    sheets = []
    for sh in FilteredElementCollector(documento).OfClass(ViewSheet).WhereElementIsNotElementType().ToElements():
        try:
            if getattr(sh, "IsPlaceholder", False):
                continue
        except Exception:
            pass
        sheets.append(sh)
    sheets.sort(key=lambda s: _natural_key(getattr(s, "SheetNumber", u"")))
    return sheets


def _label_prancha(sh):
    return u"{} - {}".format(unicode(sh.SheetNumber or u""), unicode(sh.Name or u""))


def _viewports_da_prancha(documento, sheet):
    vps = []
    try:
        ids = sheet.GetAllViewports()
    except Exception:
        ids = []
    for vpid in ids:
        vp = documento.GetElement(vpid)
        if vp is not None:
            vps.append(vp)
    return vps


def _nome_vista_viewport(documento, vp):
    try:
        vw = documento.GetElement(vp.ViewId)
        if vw is not None:
            return unicode(vw.Name or u"Vista")
    except Exception:
        pass
    return u"Viewport {}".format(_eid_int(vp.Id))


def _tipo_vista_viewport(documento, vp):
    try:
        vw = documento.GetElement(vp.ViewId)
        if vw is not None:
            return unicode(vw.ViewType)
    except Exception:
        pass
    return u""


def _proj_origem_no_sheet(vp):
    """Retorna (x, y) da projecao da origem do modelo (0,0,0) na prancha (Revit 2022+)."""
    try:
        if hasattr(vp, "GetProjectionToSheetTransform"):
            tr = vp.GetProjectionToSheetTransform()
            if tr is not None:
                pt = tr.OfPoint(XYZ.Zero)
                return (pt.X, pt.Y)
    except Exception:
        pass
    return None


def _canto_sup_esq_vp(vp):
    try:
        ol = vp.GetBoxOutline()
        if ol is not None:
            return (ol.MinimumPoint.X, ol.MaximumPoint.Y)
    except Exception:
        pass
    return None


def _ultima_revisao_prancha(documento, sheet):
    try:
        rev_ids = list(sheet.GetAllRevisionIds())
        if not rev_ids:
            return u""
        revs = [documento.GetElement(rid) for rid in rev_ids]
        revs = [r for r in revs if r is not None]
        if not revs:
            return u""
        revs.sort(key=lambda r: int(getattr(r, "SequenceNumber", 0)))
        last = revs[-1]
        num = getattr(last, "RevisionNumber", u"")
        return unicode(num or getattr(last, "SequenceNumber", u""))
    except Exception:
        return u""


def _set_str_param(elem, bip_or_name, valor):
    """Define parametro de texto por BuiltInParameter ou nome. Retorna True se alterado."""
    if elem is None:
        return False
    p = None
    try:
        if isinstance(bip_or_name, basestring):
            p = elem.LookupParameter(bip_or_name)
        else:
            p = elem.get_Parameter(bip_or_name)
    except Exception:
        p = None
    if p is None or p.IsReadOnly:
        return False
    try:
        if p.StorageType == StorageType.String:
            p.Set(unicode(valor))
            return True
    except Exception:
        pass
    return False


def _listar_params_texto_prancha(sheets):
    nomes = set()
    for sh in sheets[:10]:
        try:
            for p in sh.Parameters:
                if p is not None and not p.IsReadOnly and p.StorageType == StorageType.String:
                    n = unicode(p.Definition.Name)
                    if n:
                        nomes.add(n)
        except Exception:
            pass
    return sorted(nomes, key=lambda s: s.lower())


def _listar_params_texto_carimbo(documento, sheets):
    nomes = set()
    for sh in sheets[:10]:
        try:
            tbs = FilteredElementCollector(documento, sh.Id).OfCategory(
                BuiltInCategory.OST_TitleBlocks
            ).WhereElementIsNotElementType().ToElements()
            for tb in tbs:
                for p in tb.Parameters:
                    if p is not None and not p.IsReadOnly and p.StorageType == StorageType.String:
                        n = unicode(p.Definition.Name)
                        if n:
                            nomes.add(n)
        except Exception:
            pass
    return sorted(nomes, key=lambda s: s.lower())


# ==============================================================================
# COMANDO 1 — ALINHAR VISTAS E TITULOS NAS PRANCHAS (VIEWPORTS)
# ==============================================================================

XAML_ALINHAR_VP = """<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="AutoBIM - Alinhar Vistas e Títulos nas Pranchas"
        Width="560" Height="620" ResizeMode="CanResize"
        WindowStartupLocation="CenterScreen" Background="#F5F6F8">
    <Grid Margin="14">
        <Grid.RowDefinitions>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="*"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
        </Grid.RowDefinitions>

        <StackPanel Grid.Row="0" Margin="0,0,0,10">
            <TextBlock Text="Alinhar Vistas e Títulos (Viewports)" FontSize="15" FontWeight="Bold" Foreground="#1E2D41"/>
            <TextBlock Text="Alinha plantas e vistas entre pranchas diferentes na mesma posição exata." FontSize="11.5" Foreground="#5A6878"/>
        </StackPanel>

        <GroupBox Grid.Row="1" Header="1. Viewport de Referência (Modelo)" Padding="8" Margin="0,0,0,8" Background="White">
            <Grid>
                <Grid.RowDefinitions>
                    <RowDefinition Height="Auto"/>
                    <RowDefinition Height="Auto"/>
                    <RowDefinition Height="Auto"/>
                </Grid.RowDefinitions>
                <Grid.ColumnDefinitions>
                    <ColumnDefinition Width="125"/>
                    <ColumnDefinition Width="*"/>
                </Grid.ColumnDefinitions>
                <TextBlock Grid.Row="0" Grid.Column="0" Text="Prancha origem:" VerticalAlignment="Center" Margin="0,0,6,6"/>
                <ComboBox x:Name="cb_ref_sheet" Grid.Row="0" Grid.Column="1" Margin="0,0,0,6" Height="26"/>

                <TextBlock Grid.Row="1" Grid.Column="0" Text="Viewport origem:" VerticalAlignment="Center" Margin="0,0,6,6"/>
                <ComboBox x:Name="cb_ref_vp" Grid.Row="1" Grid.Column="1" Margin="0,0,0,6" Height="26"/>

                <TextBlock Grid.Row="2" Grid.Column="0" Text="Critério de alinhamento:" VerticalAlignment="Center" Margin="0,0,6,0"/>
                <ComboBox x:Name="cb_modo" Grid.Row="2" Grid.Column="1" Height="26">
                    <ComboBoxItem Content="Origem do Modelo (precisão exata entre plantas)"/>
                    <ComboBoxItem Content="Centro do Viewport (CropBox)"/>
                    <ComboBoxItem Content="Canto Superior Esquerdo do Viewport"/>
                </ComboBox>
            </Grid>
        </GroupBox>

        <Grid Grid.Row="2" Margin="0,0,0,6">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="Auto"/>
                <ColumnDefinition Width="Auto"/>
                <ColumnDefinition Width="Auto"/>
            </Grid.ColumnDefinitions>
            <TextBox x:Name="tb_busca" Grid.Column="0" Height="26" VerticalContentAlignment="Center" Padding="6,0" ToolTip="Filtrar pranchas ou vistas..."/>
            <Button x:Name="btn_all" Grid.Column="1" Content="Todas" Margin="6,0,0,0" Padding="10,3"/>
            <Button x:Name="btn_none" Grid.Column="2" Content="Nenhuma" Margin="4,0,0,0" Padding="10,3"/>
            <Button x:Name="btn_plans" Grid.Column="3" Content="Só Plantas" Margin="4,0,0,0" Padding="10,3"/>
        </Grid>

        <GroupBox Grid.Row="3" Header="2. Viewports de Destino a Alinhar" Padding="6" Margin="0,0,0,8" Background="White">
            <ScrollViewer VerticalScrollBarVisibility="Auto">
                <StackPanel x:Name="sp_destinos"/>
            </ScrollViewer>
        </GroupBox>

        <GroupBox Grid.Row="4" Header="3. Título da Vista (Viewport Label)" Padding="8" Margin="0,0,0,10" Background="White">
            <StackPanel>
                <CheckBox x:Name="ck_titulo" Content="Padronizar posição do Título da Vista (copiar deslocamento do viewport origem)" IsChecked="True" Margin="0,0,0,6"/>
                <StackPanel Orientation="Horizontal">
                    <CheckBox x:Name="ck_len_fixo" Content="Aplicar comprimento fixo da linha do título (mm):" VerticalAlignment="Center" Margin="0,0,8,0"/>
                    <TextBox x:Name="tb_len_mm" Text="80" Width="60" Height="24" VerticalContentAlignment="Center" Padding="4,0"/>
                </StackPanel>
            </StackPanel>
        </GroupBox>

        <StackPanel Grid.Row="5" Orientation="Horizontal" HorizontalAlignment="Right">
            <Button x:Name="btn_cancel" Content="Cancelar" Width="95" Height="30" Margin="0,0,8,0"/>
            <Button x:Name="btn_ok" Content="Alinhar Selecionados" Width="155" Height="30" Background="#0072C6" Foreground="White" FontWeight="SemiBold"/>
        </StackPanel>
    </Grid>
</Window>
"""


class AlinharViewportsWindow(forms.WPFWindow):
    def __init__(self, documento, sheets, active_sheet_id, prefs):
        forms.WPFWindow.__init__(self, XAML_ALINHAR_VP, literal_string=True)
        self._doc = documento
        self._sheets = sheets
        self._vps_by_sheet = {}
        self._dest_items = []
        self.confirmado = False
        self.resultado = None

        ref_idx = 0
        for idx, sh in enumerate(sheets):
            vps = _viewports_da_prancha(documento, sh)
            self._vps_by_sheet[_eid_int(sh.Id)] = vps
            self.cb_ref_sheet.Items.Add(_label_prancha(sh))
            if active_sheet_id is not None and _eid_int(sh.Id) == active_sheet_id:
                ref_idx = idx

        self.cb_ref_sheet.SelectedIndex = ref_idx if sheets else -1
        self.cb_modo.SelectedIndex = max(0, min(2, int(prefs.get("av_modo", 0) or 0)))
        self.ck_titulo.IsChecked = bool(prefs.get("av_titulo", True))
        self.ck_len_fixo.IsChecked = bool(prefs.get("av_len_fixo", False))
        self.tb_len_mm.Text = unicode(prefs.get("av_len_mm", u"80"))

        self._popular_ref_vps()
        self._popular_destinos()

        self.cb_ref_sheet.SelectionChanged += self._on_ref_sheet_changed
        self.cb_ref_vp.SelectionChanged += self._on_ref_vp_changed
        self.tb_busca.TextChanged += self._on_busca_changed
        self.btn_all.Click += self._on_all
        self.btn_none.Click += self._on_none
        self.btn_plans.Click += self._on_plans
        self.btn_cancel.Click += self._on_cancel
        self.btn_ok.Click += self._on_ok

    def _ref_sheet_atual(self):
        idx = self.cb_ref_sheet.SelectedIndex
        if 0 <= idx < len(self._sheets):
            return self._sheets[idx]
        return None

    def _ref_vp_atual(self):
        sh = self._ref_sheet_atual()
        if sh is None:
            return None
        vps = self._vps_by_sheet.get(_eid_int(sh.Id), [])
        idx = self.cb_ref_vp.SelectedIndex
        if 0 <= idx < len(vps):
            return vps[idx]
        return None

    def _popular_ref_vps(self):
        self.cb_ref_vp.Items.Clear()
        sh = self._ref_sheet_atual()
        if sh is None:
            return
        vps = self._vps_by_sheet.get(_eid_int(sh.Id), [])
        for vp in vps:
            self.cb_ref_vp.Items.Add(u"{} [{}]".format(_nome_vista_viewport(self._doc, vp), _tipo_vista_viewport(self._doc, vp)))
        if vps:
            self.cb_ref_vp.SelectedIndex = 0

    def _popular_destinos(self):
        self.sp_destinos.Children.Clear()
        self._dest_items = []
        ref_vp = self._ref_vp_atual()
        ref_vp_id = _eid_int(ref_vp.Id) if ref_vp is not None else -1
        for sh in self._sheets:
            sh_lbl = _label_prancha(sh)
            for vp in self._vps_by_sheet.get(_eid_int(sh.Id), []):
                if _eid_int(vp.Id) == ref_vp_id:
                    continue
                vname = _nome_vista_viewport(self._doc, vp)
                vtype = _tipo_vista_viewport(self._doc, vp)
                lbl = u"{}  →  {} [{}]".format(sh_lbl, vname, vtype)
                ck = WpfCheckBox()
                ck.Content = lbl
                ck.Margin = Thickness(2, 3, 2, 3)
                ck.IsChecked = False
                self.sp_destinos.Children.Add(ck)
                self._dest_items.append((ck, vp, lbl.lower(), vtype.lower()))
        self._filtrar_destinos()

    def _filtrar_destinos(self):
        q = unicode(self.tb_busca.Text or u"").strip().lower()
        from System.Windows import Visibility
        for ck, _, lbl_low, _ in self._dest_items:
            ck.Visibility = Visibility.Visible if (not q or q in lbl_low) else Visibility.Collapsed

    def _on_ref_sheet_changed(self, s, e):
        self._popular_ref_vps()
        self._popular_destinos()

    def _on_ref_vp_changed(self, s, e):
        self._popular_destinos()

    def _on_busca_changed(self, s, e):
        self._filtrar_destinos()

    def _on_all(self, s, e):
        from System.Windows import Visibility
        for ck, _, _, _ in self._dest_items:
            if ck.Visibility == Visibility.Visible:
                ck.IsChecked = True

    def _on_none(self, s, e):
        for ck, _, _, _ in self._dest_items:
            ck.IsChecked = False

    def _on_plans(self, s, e):
        from System.Windows import Visibility
        for ck, _, _, vtype_low in self._dest_items:
            if ck.Visibility == Visibility.Visible:
                ck.IsChecked = (u"plan" in vtype_low)

    def _on_cancel(self, s, e):
        self.Close()

    def _on_ok(self, s, e):
        ref_vp = self._ref_vp_atual()
        if ref_vp is None:
            forms.alert(u"Selecione uma prancha que contenha ao menos um Viewport de referência.", title=u"Aviso")
            return
        destinos = [vp for ck, vp, _, _ in self._dest_items if bool(ck.IsChecked)]
        if not destinos:
            forms.alert(u"Marque pelo menos um Viewport de destino para alinhar.", title=u"Aviso")
            return
        len_mm = 80.0
        if bool(self.ck_len_fixo.IsChecked):
            try:
                len_mm = float(unicode(self.tb_len_mm.Text or u"80").replace(u",", u"."))
                if len_mm <= 0:
                    raise ValueError()
            except Exception:
                forms.alert(u"Informe um comprimento de linha válido em mm (ex.: 80).", title=u"Aviso")
                return
        self.resultado = {
            "ref_vp": ref_vp,
            "destinos": destinos,
            "modo": max(0, self.cb_modo.SelectedIndex),
            "alinhar_titulo": bool(self.ck_titulo.IsChecked),
            "usar_len_fixo": bool(self.ck_len_fixo.IsChecked),
            "len_mm": len_mm,
        }
        self.confirmado = True
        self.Close()


def run_alinhar_viewports():
    _init_ctx()
    sheets = _listar_pranchas(doc)
    if not sheets:
        forms.alert(u"Nenhuma prancha encontrada neste projeto.", title=u"Alinhar Vistas e Títulos")
        return

    active_sh_id = _eid_int(view.Id) if isinstance(view, ViewSheet) else None
    prefs = _carregar_prefs()
    win = AlinharViewportsWindow(doc, sheets, active_sh_id, prefs)
    win.ShowDialog()
    if not win.confirmado:
        return

    op = win.resultado
    _salvar_prefs({
        "av_modo": op["modo"],
        "av_titulo": op["alinhar_titulo"],
        "av_len_fixo": op["usar_len_fixo"],
        "av_len_mm": u"{:.1f}".format(op["len_mm"]).rstrip(u"0").rstrip(u"."),
    })

    ref_vp = op["ref_vp"]
    ref_c = ref_vp.GetBoxCenter()
    ref_proj = _proj_origem_no_sheet(ref_vp)
    ref_tl = _canto_sup_esq_vp(ref_vp)
    ref_lbl_off = getattr(ref_vp, "LabelOffset", None)
    ref_lbl_len = getattr(ref_vp, "LabelLineLength", None)

    alinhados = 0
    titulos_ok = 0
    erros = 0

    with Transaction(doc, u"AutoBIM - Alinhar Vistas e Títulos") as t:
        t.Start()
        for vp in op["destinos"]:
            try:
                dst_c = vp.GetBoxCenter()
                dst_proj = _proj_origem_no_sheet(vp)
                dst_tl = _canto_sup_esq_vp(vp)
                dx, dy = _calcular_delta_alinhamento(
                    op["modo"],
                    (ref_c.X, ref_c.Y),
                    (dst_c.X, dst_c.Y),
                    ref_proj=ref_proj,
                    dst_proj=dst_proj,
                    ref_tl=ref_tl,
                    dst_tl=dst_tl,
                )
                vp.SetBoxCenter(XYZ(dst_c.X + dx, dst_c.Y + dy, dst_c.Z))
                alinhados += 1

                if op["alinhar_titulo"] and ref_lbl_off is not None and hasattr(vp, "LabelOffset"):
                    try:
                        vp.LabelOffset = ref_lbl_off
                        if not op["usar_len_fixo"] and ref_lbl_len is not None and hasattr(vp, "LabelLineLength"):
                            vp.LabelLineLength = ref_lbl_len
                        titulos_ok += 1
                    except Exception:
                        pass

                if op["usar_len_fixo"] and hasattr(vp, "LabelLineLength"):
                    try:
                        vp.LabelLineLength = float(op["len_mm"]) * MM
                        if not op["alinhar_titulo"]:
                            titulos_ok += 1
                    except Exception:
                        pass
            except Exception:
                erros += 1
        t.Commit()

    msg = u"Alinhamento concluído!\n\n"
    msg += u"✔ Viewports alinhados: {}\n".format(alinhados)
    if titulos_ok:
        msg += u"✔ Títulos de vista padronizados: {}\n".format(titulos_ok)
    if erros:
        msg += u"⚠ Ignorados / com erro (ex.: bloqueados): {}".format(erros)
    forms.alert(msg, title=u"AutoBIM: Alinhar Vistas e Títulos")


# ==============================================================================
# COMANDO 2 — CARIMBO E REVISOES EM LOTE
# ==============================================================================

XAML_CARIMBO_REV = """<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="AutoBIM - Carimbo e Revisões em Lote"
        Width="720" Height="640" ResizeMode="CanResize"
        WindowStartupLocation="CenterScreen" Background="#F5F6F8">
    <Grid Margin="14">
        <Grid.RowDefinitions>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="*"/>
            <RowDefinition Height="Auto"/>
        </Grid.RowDefinitions>

        <StackPanel Grid.Row="0" Margin="0,0,0,10">
            <TextBlock Text="Carimbo e Revisões em Lote" FontSize="15" FontWeight="Bold" Foreground="#1E2D41"/>
            <TextBlock Text="Selecione as pranchas à esquerda e marque apenas os campos/revisões que deseja atualizar." FontSize="11.5" Foreground="#5A6878"/>
        </StackPanel>

        <Grid Grid.Row="1">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="265"/>
                <ColumnDefinition Width="12"/>
                <ColumnDefinition Width="*"/>
            </Grid.ColumnDefinitions>

            <GroupBox Grid.Column="0" Header="1. Pranchas" Padding="6" Background="White">
                <Grid>
                    <Grid.RowDefinitions>
                        <RowDefinition Height="Auto"/>
                        <RowDefinition Height="Auto"/>
                        <RowDefinition Height="*"/>
                    </Grid.RowDefinitions>
                    <TextBox x:Name="tb_busca" Grid.Row="0" Height="25" VerticalContentAlignment="Center" Padding="5,0" Margin="0,0,0,6" ToolTip="Filtrar pranchas..."/>
                    <StackPanel Grid.Row="1" Orientation="Horizontal" Margin="0,0,0,6">
                        <Button x:Name="btn_all" Content="Marcar Todas" Padding="8,2" Margin="0,0,6,0"/>
                        <Button x:Name="btn_none" Content="Desmarcar" Padding="8,2"/>
                    </StackPanel>
                    <ScrollViewer Grid.Row="2" VerticalScrollBarVisibility="Auto">
                        <StackPanel x:Name="sp_sheets"/>
                    </ScrollViewer>
                </Grid>
            </GroupBox>

            <ScrollViewer Grid.Column="2" VerticalScrollBarVisibility="Auto">
                <StackPanel>
                    <GroupBox Header="2. Campos do Carimbo (marque o que alterar)" Padding="8" Margin="0,0,0,8" Background="White">
                        <Grid>
                            <Grid.ColumnDefinitions>
                                <ColumnDefinition Width="135"/>
                                <ColumnDefinition Width="*"/>
                                <ColumnDefinition Width="Auto"/>
                            </Grid.ColumnDefinitions>
                            <Grid.RowDefinitions>
                                <RowDefinition Height="Auto"/>
                                <RowDefinition Height="Auto"/>
                                <RowDefinition Height="Auto"/>
                                <RowDefinition Height="Auto"/>
                                <RowDefinition Height="Auto"/>
                                <RowDefinition Height="Auto"/>
                                <RowDefinition Height="Auto"/>
                            </Grid.RowDefinitions>

                            <CheckBox x:Name="ck_drawn" Grid.Row="0" Grid.Column="0" Content="Projetista/Autor:" VerticalAlignment="Center" Margin="0,0,4,6"/>
                            <TextBox x:Name="tb_drawn" Grid.Row="0" Grid.Column="1" Grid.ColumnSpan="2" Height="24" VerticalContentAlignment="Center" Padding="4,0" Margin="0,0,0,6"/>

                            <CheckBox x:Name="ck_checked" Grid.Row="1" Grid.Column="0" Content="Verificado por:" VerticalAlignment="Center" Margin="0,0,4,6"/>
                            <TextBox x:Name="tb_checked" Grid.Row="1" Grid.Column="1" Grid.ColumnSpan="2" Height="24" VerticalContentAlignment="Center" Padding="4,0" Margin="0,0,0,6"/>

                            <CheckBox x:Name="ck_approved" Grid.Row="2" Grid.Column="0" Content="Aprovado por:" VerticalAlignment="Center" Margin="0,0,4,6"/>
                            <TextBox x:Name="tb_approved" Grid.Row="2" Grid.Column="1" Grid.ColumnSpan="2" Height="24" VerticalContentAlignment="Center" Padding="4,0" Margin="0,0,0,6"/>

                            <CheckBox x:Name="ck_date" Grid.Row="3" Grid.Column="0" Content="Data de Emissão:" VerticalAlignment="Center" Margin="0,0,4,6"/>
                            <TextBox x:Name="tb_date" Grid.Row="3" Grid.Column="1" Height="24" VerticalContentAlignment="Center" Padding="4,0" Margin="0,0,6,6"/>
                            <Button x:Name="btn_hoje" Grid.Row="3" Grid.Column="2" Content="Hoje" Padding="8,2" Height="24" Margin="0,0,0,6"/>

                            <CheckBox x:Name="ck_folha" Grid.Row="4" Grid.Column="0" Content="Folha X de Y:" VerticalAlignment="Center" Margin="0,0,4,6"/>
                            <ComboBox x:Name="cb_folha_param" Grid.Row="4" Grid.Column="1" Height="24" Margin="0,0,6,6"/>
                            <ComboBox x:Name="cb_folha_fmt" Grid.Row="4" Grid.Column="2" Width="82" Height="24" Margin="0,0,0,6">
                                <ComboBoxItem Content="01/08"/>
                                <ComboBoxItem Content="1 de 8"/>
                                <ComboBoxItem Content="01 de 08"/>
                            </ComboBox>

                            <CheckBox x:Name="ck_extra" Grid.Row="5" Grid.Column="0" Content="Parâmetro extra:" VerticalAlignment="Center" Margin="0,0,4,6"/>
                            <ComboBox x:Name="cb_extra_param" Grid.Row="5" Grid.Column="1" Grid.ColumnSpan="2" Height="24" Margin="0,0,0,6"/>

                            <TextBlock Grid.Row="6" Grid.Column="0" Text="Valor do extra:" VerticalAlignment="Center" Foreground="#5A6878" Margin="20,0,4,0"/>
                            <TextBox x:Name="tb_extra_val" Grid.Row="6" Grid.Column="1" Grid.ColumnSpan="2" Height="24" VerticalContentAlignment="Center" Padding="4,0"/>
                        </Grid>
                    </GroupBox>

                    <GroupBox Header="3. Revisões em Lote" Padding="8" Background="White">
                        <StackPanel>
                            <Grid Margin="0,0,0,6">
                                <Grid.ColumnDefinitions>
                                    <ColumnDefinition Width="125"/>
                                    <ColumnDefinition Width="*"/>
                                </Grid.ColumnDefinitions>
                                <TextBlock Grid.Column="0" Text="Ação de Revisão:" VerticalAlignment="Center"/>
                                <ComboBox x:Name="cb_rev_acao" Grid.Column="1" Height="25">
                                    <ComboBoxItem Content="Nenhuma alteração nas revisões"/>
                                    <ComboBoxItem Content="Adicionar revisão existente às pranchas"/>
                                    <ComboBoxItem Content="Remover revisão existente das pranchas"/>
                                    <ComboBoxItem Content="Criar nova revisão e aplicar às pranchas"/>
                                </ComboBox>
                            </Grid>

                            <Grid Margin="0,0,0,6">
                                <Grid.ColumnDefinitions>
                                    <ColumnDefinition Width="125"/>
                                    <ColumnDefinition Width="*"/>
                                </Grid.ColumnDefinitions>
                                <TextBlock Grid.Column="0" Text="Revisão existente:" VerticalAlignment="Center"/>
                                <ComboBox x:Name="cb_rev_exist" Grid.Column="1" Height="25"/>
                            </Grid>

                            <Grid>
                                <Grid.ColumnDefinitions>
                                    <ColumnDefinition Width="125"/>
                                    <ColumnDefinition Width="*"/>
                                </Grid.ColumnDefinitions>
                                <Grid.RowDefinitions>
                                    <RowDefinition Height="Auto"/>
                                    <RowDefinition Height="Auto"/>
                                </Grid.RowDefinitions>
                                <TextBlock Grid.Row="0" Grid.Column="0" Text="Nova descrição:" VerticalAlignment="Center" Margin="0,0,0,6"/>
                                <TextBox x:Name="tb_rev_desc" Grid.Row="0" Grid.Column="1" Height="24" VerticalContentAlignment="Center" Padding="4,0" Margin="0,0,0,6" Text="Emissão para aprovação"/>

                                <TextBlock Grid.Row="1" Grid.Column="0" Text="Nova data:" VerticalAlignment="Center"/>
                                <TextBox x:Name="tb_rev_data" Grid.Row="1" Grid.Column="1" Height="24" VerticalContentAlignment="Center" Padding="4,0"/>
                            </Grid>
                        </StackPanel>
                    </GroupBox>
                </StackPanel>
            </ScrollViewer>
        </Grid>

        <StackPanel Grid.Row="2" Orientation="Horizontal" HorizontalAlignment="Right" Margin="0,10,0,0">
            <Button x:Name="btn_cancel" Content="Cancelar" Width="95" Height="30" Margin="0,0,8,0"/>
            <Button x:Name="btn_ok" Content="Aplicar em Lote" Width="140" Height="30" Background="#0072C6" Foreground="White" FontWeight="SemiBold"/>
        </StackPanel>
    </Grid>
</Window>
"""


class CarimboRevisoesWindow(forms.WPFWindow):
    def __init__(self, documento, sheets, revisoes, params_sheet, params_tb, prefs):
        forms.WPFWindow.__init__(self, XAML_CARIMBO_REV, literal_string=True)
        self._sheets = sheets
        self._revisoes = revisoes
        self._sheet_items = []
        self.confirmado = False
        self.resultado = None

        hoje = datetime.date.today().strftime("%d/%m/%Y")
        self.tb_drawn.Text = unicode(prefs.get("cr_drawn", u""))
        self.tb_checked.Text = unicode(prefs.get("cr_checked", u""))
        self.tb_approved.Text = unicode(prefs.get("cr_approved", u""))
        self.tb_date.Text = unicode(prefs.get("cr_date", hoje))
        self.tb_rev_data.Text = hoje

        for sh in sheets:
            lbl = _label_prancha(sh)
            ck = WpfCheckBox()
            ck.Content = lbl
            ck.Margin = Thickness(2, 3, 2, 3)
            ck.IsChecked = True
            self.sp_sheets.Children.Add(ck)
            self._sheet_items.append((ck, sh, lbl.lower()))

        all_params = []
        for p in params_sheet:
            all_params.append((u"Prancha: " + p, "sheet", p))
        for p in params_tb:
            all_params.append((u"Carimbo: " + p, "tb", p))
        self._folha_opts = all_params
        for lbl, _, _ in all_params:
            self.cb_folha_param.Items.Add(lbl)
        if all_params:
            self.cb_folha_param.SelectedIndex = 0
        self.cb_folha_fmt.SelectedIndex = 0

        for p in params_sheet:
            self.cb_extra_param.Items.Add(p)
        if params_sheet:
            self.cb_extra_param.SelectedIndex = 0

        self.cb_rev_acao.SelectedIndex = 0
        for rv in revisoes:
            r_lbl = u"Rev {} - {} ({})".format(
                getattr(rv, "SequenceNumber", u""),
                unicode(getattr(rv, "Description", u"") or u""),
                unicode(getattr(rv, "RevisionDate", u"") or u""),
            )
            self.cb_rev_exist.Items.Add(r_lbl)
        if revisoes:
            self.cb_rev_exist.SelectedIndex = len(revisoes) - 1

        self.tb_busca.TextChanged += self._on_busca
        self.btn_all.Click += self._on_all
        self.btn_none.Click += self._on_none
        self.btn_hoje.Click += self._on_hoje
        self.btn_cancel.Click += self._on_cancel
        self.btn_ok.Click += self._on_ok

    def _on_busca(self, s, e):
        q = unicode(self.tb_busca.Text or u"").strip().lower()
        from System.Windows import Visibility
        for ck, _, lbl_low in self._sheet_items:
            ck.Visibility = Visibility.Visible if (not q or q in lbl_low) else Visibility.Collapsed

    def _on_all(self, s, e):
        from System.Windows import Visibility
        for ck, _, _ in self._sheet_items:
            if ck.Visibility == Visibility.Visible:
                ck.IsChecked = True

    def _on_none(self, s, e):
        for ck, _, _ in self._sheet_items:
            ck.IsChecked = False

    def _on_hoje(self, s, e):
        self.tb_date.Text = datetime.date.today().strftime("%d/%m/%Y")
        self.ck_date.IsChecked = True

    def _on_cancel(self, s, e):
        self.Close()

    def _on_ok(self, s, e):
        sel = [sh for ck, sh, _ in self._sheet_items if bool(ck.IsChecked)]
        if not sel:
            forms.alert(u"Marque pelo menos uma prancha.", title=u"Aviso")
            return
        rev_acao = max(0, self.cb_rev_acao.SelectedIndex)
        any_field = (
            bool(self.ck_drawn.IsChecked)
            or bool(self.ck_checked.IsChecked)
            or bool(self.ck_approved.IsChecked)
            or bool(self.ck_date.IsChecked)
            or bool(self.ck_folha.IsChecked)
            or bool(self.ck_extra.IsChecked)
            or rev_acao > 0
        )
        if not any_field:
            forms.alert(u"Marque ao menos um campo do carimbo ou uma ação de revisão.", title=u"Aviso")
            return

        folha_target = None
        if bool(self.ck_folha.IsChecked) and 0 <= self.cb_folha_param.SelectedIndex < len(self._folha_opts):
            folha_target = self._folha_opts[self.cb_folha_param.SelectedIndex]

        rev_sel = None
        if rev_acao in (1, 2):
            idx_r = self.cb_rev_exist.SelectedIndex
            if not (0 <= idx_r < len(self._revisoes)):
                forms.alert(u"Nenhuma revisão existente selecionada.", title=u"Aviso")
                return
            rev_sel = self._revisoes[idx_r]

        self.resultado = {
            "sheets": sel,
            "ck_drawn": bool(self.ck_drawn.IsChecked),
            "drawn": unicode(self.tb_drawn.Text or u""),
            "ck_checked": bool(self.ck_checked.IsChecked),
            "checked": unicode(self.tb_checked.Text or u""),
            "ck_approved": bool(self.ck_approved.IsChecked),
            "approved": unicode(self.tb_approved.Text or u""),
            "ck_date": bool(self.ck_date.IsChecked),
            "date": unicode(self.tb_date.Text or u""),
            "ck_folha": bool(self.ck_folha.IsChecked),
            "folha_target": folha_target,
            "folha_fmt": max(0, self.cb_folha_fmt.SelectedIndex),
            "ck_extra": bool(self.ck_extra.IsChecked),
            "extra_param": unicode(self.cb_extra_param.SelectedItem or u""),
            "extra_val": unicode(self.tb_extra_val.Text or u""),
            "rev_acao": rev_acao,
            "rev_exist": rev_sel,
            "rev_desc": unicode(self.tb_rev_desc.Text or u"").strip() or u"Revisão",
            "rev_data": unicode(self.tb_rev_data.Text or u"").strip(),
        }
        self.confirmado = True
        self.Close()


def run_carimbo_revisoes():
    _init_ctx()
    sheets = _listar_pranchas(doc)
    if not sheets:
        forms.alert(u"Nenhuma prancha encontrada neste projeto.", title=u"Carimbo e Revisões em Lote")
        return

    revisoes = list(FilteredElementCollector(doc).OfClass(Revision).ToElements())
    revisoes.sort(key=lambda r: int(getattr(r, "SequenceNumber", 0)))
    params_sheet = _listar_params_texto_prancha(sheets)
    params_tb = _listar_params_texto_carimbo(doc, sheets)
    prefs = _carregar_prefs()

    win = CarimboRevisoesWindow(doc, sheets, revisoes, params_sheet, params_tb, prefs)
    win.ShowDialog()
    if not win.confirmado:
        return

    op = win.resultado
    _salvar_prefs({
        "cr_drawn": op["drawn"],
        "cr_checked": op["checked"],
        "cr_approved": op["approved"],
        "cr_date": op["date"],
    })

    total = len(op["sheets"])
    campos_ok = 0
    revs_ok = 0

    with Transaction(doc, u"AutoBIM - Carimbo e Revisões em Lote") as t:
        t.Start()

        rev_id_alvo = None
        if op["rev_acao"] in (1, 2) and op["rev_exist"] is not None:
            rev_id_alvo = op["rev_exist"].Id
        elif op["rev_acao"] == 3:
            nova_rev = Revision.Create(doc)
            nova_rev.Description = op["rev_desc"]
            if op["rev_data"]:
                nova_rev.RevisionDate = op["rev_data"]
            rev_id_alvo = nova_rev.Id

        for idx, sh in enumerate(op["sheets"], 1):
            if op["ck_drawn"] and _set_str_param(sh, BuiltInParameter.SHEET_DRAWN_BY, op["drawn"]):
                campos_ok += 1
            if op["ck_checked"] and _set_str_param(sh, BuiltInParameter.SHEET_CHECKED_BY, op["checked"]):
                campos_ok += 1
            if op["ck_approved"] and _set_str_param(sh, BuiltInParameter.SHEET_APPROVED_BY, op["approved"]):
                campos_ok += 1
            if op["ck_date"] and _set_str_param(sh, BuiltInParameter.SHEET_ISSUE_DATE, op["date"]):
                campos_ok += 1
            if op["ck_extra"] and op["extra_param"]:
                if _set_str_param(sh, op["extra_param"], op["extra_val"]):
                    campos_ok += 1

            if op["ck_folha"] and op["folha_target"] is not None:
                _, escopo, pname = op["folha_target"]
                val_folha = _formatar_folha_x_de_y(idx, total, op["folha_fmt"])
                if escopo == "sheet":
                    if _set_str_param(sh, pname, val_folha):
                        campos_ok += 1
                else:
                    tbs = FilteredElementCollector(doc, sh.Id).OfCategory(
                        BuiltInCategory.OST_TitleBlocks
                    ).WhereElementIsNotElementType().ToElements()
                    for tb in tbs:
                        if _set_str_param(tb, pname, val_folha):
                            campos_ok += 1

            if rev_id_alvo is not None:
                try:
                    atuais = list(sh.GetAdditionalRevisionIds())
                    atuais_ints = set(_eid_int(x) for x in atuais)
                    alvo_int = _eid_int(rev_id_alvo)
                    if op["rev_acao"] in (1, 3):
                        if alvo_int not in atuais_ints:
                            atuais.append(rev_id_alvo)
                            sh.SetAdditionalRevisionIds(List[ElementId](atuais))
                            revs_ok += 1
                    elif op["rev_acao"] == 2:
                        if alvo_int in atuais_ints:
                            novos = [x for x in atuais if _eid_int(x) != alvo_int]
                            sh.SetAdditionalRevisionIds(List[ElementId](novos))
                            revs_ok += 1
                except Exception:
                    pass

        t.Commit()

    msg = u"Atualização em lote concluída!\n\n"
    msg += u"✔ Pranchas processadas: {}\n".format(total)
    msg += u"✔ Campos preenchidos: {}\n".format(campos_ok)
    if op["rev_acao"] > 0:
        msg += u"✔ Pranchas com revisão atualizada: {}".format(revs_ok)
    forms.alert(msg, title=u"AutoBIM: Carimbo e Revisões em Lote")


# ==============================================================================
# COMANDO 3 — EXPORTAR PRANCHAS (DWG UNICO + LOTE PDF/DWG)
# ==============================================================================

XAML_EXPORTAR = """<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="AutoBIM - Exportar Pranchas (DWG Único / Lote PDF &amp; DWG)"
        Width="720" Height="640" ResizeMode="CanResize"
        WindowStartupLocation="CenterScreen" Background="#F5F6F8">
    <Grid Margin="14">
        <Grid.RowDefinitions>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="*"/>
            <RowDefinition Height="Auto"/>
        </Grid.RowDefinitions>

        <StackPanel Grid.Row="0" Margin="0,0,0,10">
            <TextBlock Text="Exportar Pranchas (DWG Único + Lote PDF/DWG)" FontSize="15" FontWeight="Bold" Foreground="#1E2D41"/>
            <TextBlock Text="Exporte pranchas em PDF (combinado ou individual) e DWG (lote ou DWG único via AutoCAD)." FontSize="11.5" Foreground="#5A6878"/>
        </StackPanel>

        <Grid Grid.Row="1">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="265"/>
                <ColumnDefinition Width="12"/>
                <ColumnDefinition Width="*"/>
            </Grid.ColumnDefinitions>

            <GroupBox Grid.Column="0" Header="1. Pranchas" Padding="6" Background="White">
                <Grid>
                    <Grid.RowDefinitions>
                        <RowDefinition Height="Auto"/>
                        <RowDefinition Height="Auto"/>
                        <RowDefinition Height="*"/>
                    </Grid.RowDefinitions>
                    <TextBox x:Name="tb_busca" Grid.Row="0" Height="25" VerticalContentAlignment="Center" Padding="5,0" Margin="0,0,0,6"/>
                    <StackPanel Grid.Row="1" Orientation="Horizontal" Margin="0,0,0,6">
                        <Button x:Name="btn_all" Content="Marcar Todas" Padding="8,2" Margin="0,0,6,0"/>
                        <Button x:Name="btn_none" Content="Desmarcar" Padding="8,2"/>
                    </StackPanel>
                    <ScrollViewer Grid.Row="2" VerticalScrollBarVisibility="Auto">
                        <StackPanel x:Name="sp_sheets"/>
                    </ScrollViewer>
                </Grid>
            </GroupBox>

            <ScrollViewer Grid.Column="2" VerticalScrollBarVisibility="Auto">
                <StackPanel>
                    <GroupBox Header="2. Destino e Nomenclatura" Padding="8" Margin="0,0,0,8" Background="White">
                        <Grid>
                            <Grid.ColumnDefinitions>
                                <ColumnDefinition Width="115"/>
                                <ColumnDefinition Width="*"/>
                                <ColumnDefinition Width="Auto"/>
                            </Grid.ColumnDefinitions>
                            <Grid.RowDefinitions>
                                <RowDefinition Height="Auto"/>
                                <RowDefinition Height="Auto"/>
                                <RowDefinition Height="Auto"/>
                            </Grid.RowDefinitions>

                            <TextBlock Grid.Row="0" Grid.Column="0" Text="Pasta de saída:" VerticalAlignment="Center" Margin="0,0,4,6"/>
                            <TextBox x:Name="tb_out_dir" Grid.Row="0" Grid.Column="1" Height="24" VerticalContentAlignment="Center" Padding="4,0" Margin="0,0,6,6"/>
                            <Button x:Name="btn_dir" Grid.Row="0" Grid.Column="2" Content="Procurar..." Padding="8,2" Height="24" Margin="0,0,0,6"/>

                            <TextBlock Grid.Row="1" Grid.Column="0" Text="Padrão do nome:" VerticalAlignment="Center" Margin="0,0,4,6"/>
                            <TextBox x:Name="tb_tpl" Grid.Row="1" Grid.Column="1" Grid.ColumnSpan="2" Height="24" VerticalContentAlignment="Center" Padding="4,0" Margin="0,0,0,6" ToolTip="Tokens: {Prefixo}, {Numero}, {Nome}, {Rev}"/>

                            <TextBlock Grid.Row="2" Grid.Column="0" Text="Prefixo opcional:" VerticalAlignment="Center" Margin="0,0,4,0"/>
                            <TextBox x:Name="tb_prefixo" Grid.Row="2" Grid.Column="1" Grid.ColumnSpan="2" Height="24" VerticalContentAlignment="Center" Padding="4,0"/>
                        </Grid>
                    </GroupBox>

                    <GroupBox Header="3. Exportação PDF (Nativo Revit)" Padding="8" Margin="0,0,0,8" Background="White">
                        <StackPanel>
                            <CheckBox x:Name="ck_pdf" Content="Exportar PDF das pranchas selecionadas" IsChecked="True" FontWeight="SemiBold" Margin="0,0,0,6"/>
                            <CheckBox x:Name="ck_pdf_comb" Content="Combinar tudo em um único arquivo PDF" IsChecked="False" Margin="18,0,0,4"/>
                            <CheckBox x:Name="ck_pdf_clean" Content="Ocultar caixas de recorte, planos de referência e scope boxes" IsChecked="True" Margin="18,0,0,0"/>
                        </StackPanel>
                    </GroupBox>

                    <GroupBox Header="4. Exportação DWG (Lote ou DWG Único)" Padding="8" Background="White">
                        <StackPanel>
                            <CheckBox x:Name="ck_dwg" Content="Exportar DWG das pranchas selecionadas" IsChecked="True" FontWeight="SemiBold" Margin="0,0,0,6"/>
                            <CheckBox x:Name="ck_dwg_unico" Content="Unir em 1 único DWG com abas de Layout (via AutoCAD accoreconsole)" IsChecked="True" Margin="18,0,0,6"/>
                            <Grid Margin="18,0,0,6">
                                <Grid.ColumnDefinitions>
                                    <ColumnDefinition Width="115"/>
                                    <ColumnDefinition Width="*"/>
                                </Grid.ColumnDefinitions>
                                <TextBlock Grid.Column="0" Text="Config. DWG:" VerticalAlignment="Center"/>
                                <ComboBox x:Name="cb_dwg_setup" Grid.Column="1" Height="24"/>
                            </Grid>
                            <TextBlock x:Name="lbl_acad_status" Margin="18,2,0,0" FontSize="11" Foreground="#4A5A6A" TextWrapping="Wrap"/>
                        </StackPanel>
                    </GroupBox>
                </StackPanel>
            </ScrollViewer>
        </Grid>

        <StackPanel Grid.Row="2" Orientation="Horizontal" HorizontalAlignment="Right" Margin="0,10,0,0">
            <Button x:Name="btn_cancel" Content="Cancelar" Width="95" Height="30" Margin="0,0,8,0"/>
            <Button x:Name="btn_ok" Content="Exportar Agora" Width="140" Height="30" Background="#0072C6" Foreground="White" FontWeight="SemiBold"/>
        </StackPanel>
    </Grid>
</Window>
"""


class ExportarPranchasWindow(forms.WPFWindow):
    def __init__(self, sheets, dwg_setups, accore_path, prefs):
        forms.WPFWindow.__init__(self, XAML_EXPORTAR, literal_string=True)
        self._sheets = sheets
        self._dwg_setups = dwg_setups
        self._accore = accore_path
        self._sheet_items = []
        self.confirmado = False
        self.resultado = None

        default_dir = prefs.get("exp_dir") or os.path.join(os.path.expanduser("~"), "Desktop", "Export_AutoBIM")
        self.tb_out_dir.Text = unicode(default_dir)
        self.tb_tpl.Text = unicode(prefs.get("exp_tpl", u"{Prefixo}_{Numero} - {Nome}"))
        self.tb_prefixo.Text = unicode(prefs.get("exp_prefixo", u""))
        self.ck_pdf.IsChecked = bool(prefs.get("exp_pdf", True))
        self.ck_pdf_comb.IsChecked = bool(prefs.get("exp_pdf_comb", False))
        self.ck_pdf_clean.IsChecked = bool(prefs.get("exp_pdf_clean", True))
        self.ck_dwg.IsChecked = bool(prefs.get("exp_dwg", True))
        self.ck_dwg_unico.IsChecked = bool(prefs.get("exp_dwg_unico", bool(accore_path)))

        for sh in sheets:
            lbl = _label_prancha(sh)
            ck = WpfCheckBox()
            ck.Content = lbl
            ck.Margin = Thickness(2, 3, 2, 3)
            ck.IsChecked = True
            self.sp_sheets.Children.Add(ck)
            self._sheet_items.append((ck, sh, lbl.lower()))

        self.cb_dwg_setup.Items.Add(u"(Configuração Padrão do Revit)")
        for st in dwg_setups:
            self.cb_dwg_setup.Items.Add(unicode(st.Name))
        self.cb_dwg_setup.SelectedIndex = 0

        if accore_path:
            self.lbl_acad_status.Text = u"✔ AutoCAD detectado: {}".format(accore_path)
        else:
            self.lbl_acad_status.Text = u"⚠ accoreconsole.exe não encontrado. Se marcado 'DWG Único', será usado fallback para lote DWG padronizado."

        self.tb_busca.TextChanged += self._on_busca
        self.btn_all.Click += self._on_all
        self.btn_none.Click += self._on_none
        self.btn_dir.Click += self._on_dir
        self.btn_cancel.Click += self._on_cancel
        self.btn_ok.Click += self._on_ok

    def _on_busca(self, s, e):
        q = unicode(self.tb_busca.Text or u"").strip().lower()
        from System.Windows import Visibility
        for ck, _, lbl_low in self._sheet_items:
            ck.Visibility = Visibility.Visible if (not q or q in lbl_low) else Visibility.Collapsed

    def _on_all(self, s, e):
        from System.Windows import Visibility
        for ck, _, _ in self._sheet_items:
            if ck.Visibility == Visibility.Visible:
                ck.IsChecked = True

    def _on_none(self, s, e):
        for ck, _, _ in self._sheet_items:
            ck.IsChecked = False

    def _on_dir(self, s, e):
        dlg = FolderBrowserDialog()
        dlg.Description = u"Selecione a pasta para salvar as pranchas exportadas"
        if os.path.isdir(unicode(self.tb_out_dir.Text or u"")):
            dlg.SelectedPath = unicode(self.tb_out_dir.Text)
        if dlg.ShowDialog() == WinFormsDialogResult.OK:
            self.tb_out_dir.Text = unicode(dlg.SelectedPath)

    def _on_cancel(self, s, e):
        self.Close()

    def _on_ok(self, s, e):
        sel = [sh for ck, sh, _ in self._sheet_items if bool(ck.IsChecked)]
        if not sel:
            forms.alert(u"Marque pelo menos uma prancha para exportar.", title=u"Aviso")
            return
        if not bool(self.ck_pdf.IsChecked) and not bool(self.ck_dwg.IsChecked):
            forms.alert(u"Marque PDF e/ou DWG para exportar.", title=u"Aviso")
            return
        out_dir = unicode(self.tb_out_dir.Text or u"").strip()
        if not out_dir:
            forms.alert(u"Informe a pasta de destino.", title=u"Aviso")
            return
        idx_st = self.cb_dwg_setup.SelectedIndex
        dwg_setup = self._dwg_setups[idx_st - 1] if idx_st > 0 else None
        self.resultado = {
            "sheets": sel,
            "out_dir": out_dir,
            "tpl": unicode(self.tb_tpl.Text or u"{Numero} - {Nome}"),
            "prefixo": unicode(self.tb_prefixo.Text or u"").strip(),
            "pdf": bool(self.ck_pdf.IsChecked),
            "pdf_comb": bool(self.ck_pdf_comb.IsChecked),
            "pdf_clean": bool(self.ck_pdf_clean.IsChecked),
            "dwg": bool(self.ck_dwg.IsChecked),
            "dwg_unico": bool(self.ck_dwg_unico.IsChecked),
            "dwg_setup": dwg_setup,
            "accore": self._accore,
        }
        self.confirmado = True
        self.Close()


def run_exportar_pranchas():
    _init_ctx()
    sheets = _listar_pranchas(doc)
    if not sheets:
        forms.alert(u"Nenhuma prancha encontrada neste projeto.", title=u"Exportar Pranchas")
        return

    dwg_setups = list(FilteredElementCollector(doc).OfClass(ExportDWGSettings).ToElements())
    dwg_setups.sort(key=lambda s: unicode(s.Name).lower())
    accore_path = _localizar_accoreconsole()
    prefs = _carregar_prefs()

    win = ExportarPranchasWindow(sheets, dwg_setups, accore_path, prefs)
    win.ShowDialog()
    if not win.confirmado:
        return

    op = win.resultado
    out_dir = op["out_dir"]
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)

    _salvar_prefs({
        "exp_dir": out_dir,
        "exp_tpl": op["tpl"],
        "exp_prefixo": op["prefixo"],
        "exp_pdf": op["pdf"],
        "exp_pdf_comb": op["pdf_comb"],
        "exp_pdf_clean": op["pdf_clean"],
        "exp_dwg": op["dwg"],
        "exp_dwg_unico": op["dwg_unico"],
    })

    proj_nome = _sanitizar_nome_arquivo(getattr(doc, "Title", u"Projeto") or u"Projeto")
    if op["prefixo"]:
        proj_nome = _sanitizar_nome_arquivo(u"{}_{}".format(op["prefixo"], proj_nome))

    resumo = []

    # 1. Exportacao PDF
    if op["pdf"]:
        try:
            pdf_opts = PDFExportOptions()
            pdf_opts.HideCropBoundaries = op["pdf_clean"]
            pdf_opts.HideReferencePlane = op["pdf_clean"]
            pdf_opts.HideScopeBoxes = op["pdf_clean"]
            pdf_opts.HideUnreferencedViewTags = op["pdf_clean"]
            try:
                pdf_opts.ExportQuality = PDFExportQualityType.DPI300
            except Exception:
                pass

            if op["pdf_comb"]:
                pdf_opts.Combine = True
                pdf_opts.FileName = u"{}_Pranchas".format(proj_nome)
                ids = List[ElementId]([sh.Id for sh in op["sheets"]])
                doc.Export(out_dir, ids, pdf_opts)
                resumo.append(u"✔ PDF único gerado: {}.pdf".format(pdf_opts.FileName))
            else:
                pdf_opts.Combine = False
                cnt_pdf = 0
                for sh in op["sheets"]:
                    rev = _ultima_revisao_prancha(doc, sh)
                    fname = _formatar_nome_prancha(op["tpl"], sh.SheetNumber, sh.Name, op["prefixo"], rev)
                    pdf_opts.FileName = fname
                    ids = List[ElementId]([sh.Id])
                    if doc.Export(out_dir, ids, pdf_opts):
                        cnt_pdf += 1
                resumo.append(u"✔ Lote PDF exportado: {} arquivo(s)".format(cnt_pdf))
        except Exception as ex:
            resumo.append(u"⚠ Erro ao exportar PDF (requer Revit 2022+): {}".format(ex))

    # 2. Exportacao DWG
    if op["dwg"]:
        try:
            if op["dwg_setup"] is not None:
                dwg_opts = op["dwg_setup"].GetDWGExportOptions()
            else:
                dwg_opts = DWGExportOptions()
            dwg_opts.MergedViews = True

            usar_unico = op["dwg_unico"] and bool(op["accore"]) and os.path.exists(op["accore"])
            destino_dwg = tempfile.mkdtemp(prefix=u"autobim_dwg_") if usar_unico else out_dir
            exportados = []

            for sh in op["sheets"]:
                rev = _ultima_revisao_prancha(doc, sh)
                fname = _formatar_nome_prancha(op["tpl"], sh.SheetNumber, sh.Name, op["prefixo"], rev)
                ids = List[ElementId]([sh.Id])
                if doc.Export(destino_dwg, fname, ids, dwg_opts):
                    dwg_file = os.path.join(destino_dwg, fname + u".dwg")
                    if os.path.exists(dwg_file):
                        exportados.append({"path": dwg_file, "layout": unicode(sh.SheetNumber or fname)})

            if usar_unico and exportados:
                out_unico = os.path.join(out_dir, u"{}_Pranchas_Unico.dwg".format(proj_nome))
                scr_path = os.path.join(destino_dwg, u"merge_autobim.scr")
                scr_txt = _gerar_script_accoreconsole(exportados, out_unico)
                with codecs.open(scr_path, "w", encoding="utf-8") as f:
                    f.write(scr_txt)

                si = subprocess.STARTUPINFO()
                si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                cmd = [op["accore"], "/i", exportados[0]["path"], "/s", scr_path]
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, startupinfo=si)
                proc.communicate()

                if os.path.exists(out_unico):
                    resumo.append(u"✔ DWG Único gerado via AutoCAD: {}".format(os.path.basename(out_unico)))
                    try:
                        shutil.rmtree(destino_dwg, ignore_errors=True)
                    except Exception:
                        pass
                else:
                    # Fallback: copia os DWGs individuais para a pasta de saida
                    for item in exportados:
                        shutil.copy2(item["path"], os.path.join(out_dir, os.path.basename(item["path"])))
                    try:
                        shutil.rmtree(destino_dwg, ignore_errors=True)
                    except Exception:
                        pass
                    resumo.append(u"⚠ AutoCAD não gerou o DWG único; fallback salvo com {} DWG(s) individuais.".format(len(exportados)))
            else:
                if op["dwg_unico"] and not op["accore"]:
                    resumo.append(u"✔ Lote DWG padronizado (fallback sem AutoCAD): {} arquivo(s)".format(len(exportados)))
                else:
                    resumo.append(u"✔ Lote DWG exportado: {} arquivo(s)".format(len(exportados)))
        except Exception as ex:
            resumo.append(u"⚠ Erro ao exportar DWG: {}".format(ex))

    msg = u"Exportação concluída!\n\nPasta: {}\n\n{}".format(out_dir, u"\n".join(resumo))
    forms.alert(msg, title=u"AutoBIM: Exportar Pranchas")
