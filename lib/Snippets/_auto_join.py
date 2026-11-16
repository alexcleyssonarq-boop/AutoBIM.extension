# -*- coding: utf-8 -*-
"""Lógica compartilhada do comando Unir Geometria Automático (Auto-Join)."""

import os
import json
import codecs
import clr
clr.AddReference("System")
clr.AddReference("PresentationFramework")

from System.Collections.Generic import List
from Autodesk.Revit.DB import *
from pyrevit import forms, revit, script

doc   = None
uidoc = None
view  = None

MODO_VISTA        = "vista"
MODO_SELECIONADOS = "selecionados"

TEXTO_MODO = {
    MODO_VISTA:        u"Escopo: Todos os elementos visíveis na vista ativa (Planta, Corte ou 3D)",
    MODO_SELECIONADOS: u"Escopo: Apenas os elementos selecionados pelo usuário",
}

SETTINGS_PATH = os.path.join(os.path.dirname(__file__), "_auto_join_settings.json")


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


# =============================================================================
# SUPRESSOR DE AVISOS DO REVIT (Evita popups de elementos que apenas se tocam)
# =============================================================================
class AutoJoinFailuresPreprocessor(IFailuresPreprocessor):
    def PreprocessFailures(self, failuresAccessor):
        try:
            failures = failuresAccessor.GetFailureMessages()
            for f in failures:
                severity = f.GetSeverity()
                if severity == FailureSeverity.Warning:
                    failuresAccessor.DeleteWarning(f)
                elif severity == FailureSeverity.Error:
                    failuresAccessor.ResolveFailure(f)
                    return FailureProcessingResult.ProceedWithCommit
        except Exception:
            pass
        return FailureProcessingResult.Continue


# =============================================================================
# JANELA DE OPÇÕES
# =============================================================================
XAML = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Unir Geometria Automático (Auto-Join)" Width="450" SizeToContent="Height"
        WindowStartupLocation="CenterScreen" ResizeMode="NoResize"
        Background="#F3F3F3" FontFamily="Segoe UI" FontSize="12">
    <StackPanel Margin="18">
        <Border Background="#E3EEF9" BorderBrush="#0078D4" BorderThickness="0,0,0,2"
                Padding="10,8" Margin="0,0,0,14">
            <TextBlock x:Name="tx_modo" TextWrapping="Wrap" FontWeight="SemiBold"
                       Foreground="#1F3A57"/>
        </Border>

        <TextBlock Text="Categorias a unir / ajustar corte" FontWeight="SemiBold" Margin="0,0,0,6"/>
        <CheckBox x:Name="ck_pilares"  Margin="0,4,0,0" IsChecked="True" Content="Pilares (Estruturais e Arquitetônicos)"/>
        <CheckBox x:Name="ck_vigas"    Margin="0,6,0,0" IsChecked="True" Content="Vigas e Fundações Estruturais"/>
        <CheckBox x:Name="ck_pisos"    Margin="0,6,0,0" IsChecked="True" Content="Pisos e Lajes"/>
        <CheckBox x:Name="ck_paredes"  Margin="0,6,0,0" IsChecked="True" Content="Paredes"/>
        <CheckBox x:Name="ck_forros"   Margin="0,6,0,14" IsChecked="True" Content="Forros e Telhados"/>

        <TextBlock Text="Prioridade de corte entre Pisos/Lajes e Paredes" FontWeight="SemiBold"/>
        <ComboBox x:Name="cb_prioridade" Margin="0,4,0,12" Height="26"/>

        <CheckBox x:Name="ck_inverter" Margin="0,4,0,0" IsChecked="True"
                  Content="Corrigir também a ordem de elementos que já estavam unidos invertidos"/>

        <StackPanel Orientation="Horizontal" HorizontalAlignment="Right" Margin="0,20,0,0">
            <Button x:Name="btn_cancel" Content="Cancelar" Width="90" Height="30" Margin="0,0,8,0"/>
            <Button x:Name="btn_ok" Content="Executar Auto-Join" Width="150" Height="30"
                    IsDefault="True" Background="#0078D4" Foreground="White"/>
        </StackPanel>
    </StackPanel>
</Window>
"""


class AutoJoinWindow(forms.WPFWindow):
    def __init__(self, modo, prefs):
        forms.WPFWindow.__init__(self, XAML, literal_string=True)
        self.confirmado = False
        self.resultado = {}

        self.FindName("tx_modo").Text = TEXTO_MODO[modo]
        self._ck_pilares = self.FindName("ck_pilares")
        self._ck_vigas   = self.FindName("ck_vigas")
        self._ck_pisos   = self.FindName("ck_pisos")
        self._ck_paredes = self.FindName("ck_paredes")
        self._ck_forros  = self.FindName("ck_forros")
        self._cb_prio    = self.FindName("cb_prioridade")
        self._ck_inv     = self.FindName("ck_inverter")

        opcoes_prio = (
            u"1. Estrutura > Pisos/Lajes > Paredes > Forros (Padrão para Cortes)",
            u"2. Estrutura > Paredes > Pisos/Lajes > Forros (Paredes cortam Pisos)",
        )
        for op in opcoes_prio:
            self._cb_prio.Items.Add(op)

        self._ck_pilares.IsChecked = bool(prefs.get("pilares", True))
        self._ck_vigas.IsChecked   = bool(prefs.get("vigas", True))
        self._ck_pisos.IsChecked   = bool(prefs.get("pisos", True))
        self._ck_paredes.IsChecked = bool(prefs.get("paredes", True))
        self._ck_forros.IsChecked  = bool(prefs.get("forros", True))
        self._cb_prio.SelectedIndex = int(prefs.get("prio_idx", 0))
        self._ck_inv.IsChecked     = bool(prefs.get("inverter", True))

        if modo == MODO_SELECIONADOS:
            self.FindName("btn_ok").Content = u"Selecionar e Unir"

        self.FindName("btn_ok").Click     += self.on_ok
        self.FindName("btn_cancel").Click += self.on_cancel

    def on_cancel(self, sender, args):
        self.confirmado = False
        self.Close()

    def on_ok(self, sender, args):
        res = {
            "pilares":  bool(self._ck_pilares.IsChecked),
            "vigas":    bool(self._ck_vigas.IsChecked),
            "pisos":    bool(self._ck_pisos.IsChecked),
            "paredes":  bool(self._ck_paredes.IsChecked),
            "forros":   bool(self._ck_forros.IsChecked),
            "prio_idx": self._cb_prio.SelectedIndex,
            "inverter": bool(self._ck_inv.IsChecked),
        }
        if not any((res["pilares"], res["vigas"], res["pisos"], res["paredes"], res["forros"])):
            forms.alert("Marque pelo menos uma categoria para unir.")
            return
        self.resultado = res
        self.confirmado = True
        self.Close()


# =============================================================================
# FUNÇÕES DE PRIORIDADE E INTERSEÇÃO DE BOUNDING BOX
# =============================================================================
CAT_PILARES_EST = ElementId(BuiltInCategory.OST_StructuralColumns)
CAT_PILARES_ARQ = ElementId(BuiltInCategory.OST_Columns)
CAT_VIGAS       = ElementId(BuiltInCategory.OST_StructuralFraming)
CAT_FUNDACOES   = ElementId(BuiltInCategory.OST_StructuralFoundation)
CAT_PISOS       = ElementId(BuiltInCategory.OST_Floors)
CAT_PAREDES     = ElementId(BuiltInCategory.OST_Walls)
CAT_TELHADOS    = ElementId(BuiltInCategory.OST_Roofs)
CAT_FORROS      = ElementId(BuiltInCategory.OST_Ceilings)


def obter_prioridade(el, prio_idx):
    """
    Retorna um número inteiro indicando a prioridade de corte (menor número = maior prioridade, corta os maiores).
    """
    cat = el.Category
    if cat is None:
        return 99
    cid = cat.Id

    # 1º: Pilares e Fundações
    if cid in (CAT_PILARES_EST, CAT_PILARES_ARQ, CAT_FUNDACOES):
        return 1
    # 2º: Vigas estruturais
    if cid == CAT_VIGAS:
        return 2

    # 3º e 4º: Pisos/Lajes vs Paredes
    if prio_idx == 0:
        # Pisos/Lajes cortam Paredes
        if cid == CAT_PISOS:
            # Se for piso fino de acabamento (< 4 cm), deixar a parede cortar o piso
            try:
                ft = doc.GetElement(el.GetTypeId())
                cs = ft.GetCompoundStructure()
                if cs is not None and cs.GetWidth() < (0.04 / 0.3048):
                    return 5
            except Exception:
                pass
            return 3
        if cid == CAT_PAREDES:
            return 4
    else:
        # Paredes cortam Pisos
        if cid == CAT_PAREDES:
            return 3
        if cid == CAT_PISOS:
            return 4

    # 5º: Telhados e Forros
    if cid in (CAT_TELHADOS, CAT_FORROS):
        return 6

    return 10


def bboxes_interceptam(b1, b2, tol=0.05):
    """Verifica interseção rápida 3D entre dois BoundingBoxXYZ com tolerância em pés."""
    if b1.Max.X + tol < b2.Min.X or b2.Max.X + tol < b1.Min.X:
        return False
    if b1.Max.Y + tol < b2.Min.Y or b2.Max.Y + tol < b1.Min.Y:
        return False
    if b1.Max.Z + tol < b2.Min.Z or b2.Max.Z + tol < b1.Min.Z:
        return False
    return True


# =============================================================================
# EXECUÇÃO PRINCIPAL
# =============================================================================
def run(modo):
    global doc, uidoc, view
    uidoc = __revit__.ActiveUIDocument
    doc   = uidoc.Document
    view  = doc.ActiveView

    prefs = carregar_preferencias()
    win = AutoJoinWindow(modo, prefs)
    win.ShowDialog()

    if not win.confirmado:
        script.exit()

    op = win.resultado
    salvar_preferencias(op)

    # Montar lista de BuiltInCategories permitidas
    bics = []
    if op["pilares"]:
        bics.extend([BuiltInCategory.OST_StructuralColumns, BuiltInCategory.OST_Columns])
    if op["vigas"]:
        bics.extend([BuiltInCategory.OST_StructuralFraming, BuiltInCategory.OST_StructuralFoundation])
    if op["pisos"]:
        bics.append(BuiltInCategory.OST_Floors)
    if op["paredes"]:
        bics.append(BuiltInCategory.OST_Walls)
    if op["forros"]:
        bics.extend([BuiltInCategory.OST_Ceilings, BuiltInCategory.OST_Roofs])

    cat_ids_permitidos = set(ElementId(b) for b in bics)

    # Coletar elementos conforme o modo escolhido
    elementos = []
    if modo == MODO_VISTA:
        cat_list = List[BuiltInCategory](bics)
        multicat = ElementMulticategoryFilter(cat_list)
        elementos = list(
            FilteredElementCollector(doc, view.Id)
            .WherePasses(multicat)
            .WhereElementIsNotElementType()
            .ToElements()
        )
    else:
        # Verificar se já há elementos pré-selecionados
        sel_ids = uidoc.Selection.GetElementIds()
        for eid in sel_ids:
            el = doc.GetElement(eid)
            if el is not None and el.Category is not None and el.Category.Id in cat_ids_permitidos:
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
                with forms.WarningBar(title="Selecione os elementos para unir (ex.: caixa de seleção) e clique em Finish"):
                    refs = uidoc.Selection.PickObjects(
                        ObjectType.Element,
                        CategoryFilter(cat_ids_permitidos),
                        "Selecione os elementos para unir"
                    )
                elementos = [doc.GetElement(r) for r in refs]
            except Exception:
                script.exit()

    if len(elementos) < 2:
        forms.alert("Pelo menos 2 elementos são necessários para unir a geometria.", exitscript=True)

    # Pré-calcular BoundingBox e prioridade de cada elemento
    dados_el = []
    for el in elementos:
        try:
            bb = el.get_BoundingBox(None)
            if bb is not None:
                prio = obter_prioridade(el, op["prio_idx"])
                dados_el.append((el, bb, prio))
        except Exception:
            pass

    n = len(dados_el)
    unioes_novas = 0
    ordens_ajustadas = 0

    with Transaction(doc, "Auto-Join Geometria") as t:
        t.Start()
        fail_opts = t.GetFailureHandlingOptions()
        fail_opts.SetFailuresPreprocessor(AutoJoinFailuresPreprocessor())
        t.SetFailureHandlingOptions(fail_opts)

        for i in range(n):
            el_a, bb_a, prio_a = dados_el[i]
            for j in range(i + 1, n):
                el_b, bb_b, prio_b = dados_el[j]

                if not bboxes_interceptam(bb_a, bb_b):
                    continue

                # Determinar quem deve cortar quem (menor valor de prio corta o maior)
                if prio_a <= prio_b:
                    el_corta, el_cortado = el_a, el_b
                else:
                    el_corta, el_cortado = el_b, el_a

                try:
                    ja_unidos = JoinGeometryUtils.AreElementsJoined(doc, el_corta, el_cortado)
                    if not ja_unidos:
                        sub = SubTransaction(doc)
                        sub.Start()
                        try:
                            JoinGeometryUtils.JoinGeometry(doc, el_corta, el_cortado)
                            sub.Commit()
                            ja_unidos = True
                            unioes_novas += 1
                        except Exception:
                            sub.RollBack()
                            ja_unidos = False

                    # Ajustar a ordem de corte se estiver invertida e as prioridades forem distintas
                    if ja_unidos and op["inverter"] and prio_a != prio_b:
                        if not JoinGeometryUtils.IsCuttingElementInJoin(doc, el_corta, el_cortado):
                            sub2 = SubTransaction(doc)
                            sub2.Start()
                            try:
                                JoinGeometryUtils.SwitchJoinOrder(doc, el_corta, el_cortado)
                                sub2.Commit()
                                ordens_ajustadas += 1
                            except Exception:
                                sub2.RollBack()
                except Exception:
                    pass

        t.Commit()

    forms.alert(
        u"Auto-Join concluído!\n\n"
        u"✔ Elementos analisados: {}\n"
        u"✔ Novas uniões aplicadas: {}\n"
        u"✔ Ordens de corte corrigidas: {}".format(n, unioes_novas, ordens_ajustadas),
        title="Unir Geometria Automático",
        warn_icon=False
    )
