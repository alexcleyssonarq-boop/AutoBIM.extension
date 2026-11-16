# -*- coding: utf-8 -*-
"""Detecao automatica de Pe-Direito por Ambiente (Teto Reto, Forro Inclinado, Madeiramento, Telhado/Laje)."""

import os
import json
import math
import codecs
import tempfile

try:
    unicode
except NameError:
    unicode = str

try:
    import clr
    clr.AddReference("System")
    clr.AddReference("PresentationFramework")
    clr.AddReference("PresentationCore")
    from System.Collections.Generic import List
    from System.Windows.Markup import XamlReader
    from Autodesk.Revit.DB import (
        BuiltInCategory, BuiltInParameter, CategorySet, Element, ElementId,
        ElementMulticategoryFilter, ExternalDefinitionCreationOptions,
        FilteredElementCollector, FindReferenceTarget, Options, PlanarFace,
        ReferenceIntersector, Solid, SpatialElementBoundaryOptions,
        StorageType, Transaction, TransactionGroup, View3D, ViewDetailLevel,
        ViewFamily, ViewFamilyType, XYZ
    )
    from Autodesk.Revit.DB.Architecture import Room
    from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
    from pyrevit import forms, script
    HAS_REVIT = True
except Exception:
    HAS_REVIT = False


doc = None
uidoc = None
view = None

FT = 1.0 / 0.3048
SETTINGS_PATH = os.path.join(os.path.dirname(__file__), "_ceiling_height_settings.json")


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
# FUNCOES PURAS DE CALCULO E FORMATACAO DE PE-DIREITO (TESTAVEIS SEM REVIT)
# =============================================================================
def formatar_numero_br(valor_m, casas=2):
    fmt = u"{:." + unicode(int(casas)) + u"f}"
    return fmt.format(float(valor_m)).replace(".", ",")


def formatar_pd_texto(h_min_m, h_max_m, tol_plano_m=0.03, prefixo=u"PD = ", usar_virgula=True):
    """
    Formata o texto final para Tag de Ambiente:
      - Teto reto (|max - min| <= tol): 'PD = 2,80m'
      - Teto inclinado / variavel:       'PD = 2,60 / 3,40m'
    """
    if h_min_m is None or h_max_m is None:
        return u""
    lo = min(float(h_min_m), float(h_max_m))
    hi = max(float(h_min_m), float(h_max_m))
    if hi <= 0.1:
        return u""

    lo_r = round(lo, 2)
    hi_r = round(hi, 2)
    pref = prefixo if prefixo is not None else u""

    def _fmt(v):
        s = u"{:.2f}".format(v)
        return s.replace(".", ",") if usar_virgula else s

    if (hi_r - lo_r) <= max(0.0, float(tol_plano_m)) + 1e-6:
        med_r = round(0.5 * (lo + hi), 2)
        return u"{}{}m".format(pref, _fmt(med_r))

    return u"{}{} / {}m".format(pref, _fmt(lo_r), _fmt(hi_r))


def _dist_ponto_segmento_2d(px, py, ax, ay, bx, by):
    vx, vy = bx - ax, by - ay
    l2 = vx * vx + vy * vy
    if l2 < 1e-12:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / l2))
    return math.hypot(px - (ax + t * vx), py - (ay + t * vy))


def gerar_candidatos_malha_2d(xmin, xmax, ymin, ymax, segs_2d=None, passo_ft=1.0, margem_ft=0.5, max_pts=140):
    """
    Gera pontos candidatos (x, y) dentro do BoundingBox de um ambiente,
    aplicando recuo minimo das paredes se `segs_2d` [((x1,y1),(x2,y2)), ...] for informado.
    """
    w = max(0.05, xmax - xmin)
    h = max(0.05, ymax - ymin)

    # Adapta passo para nao passar de max_pts nem ficar ralo em salas pequenas
    area_bb = w * h
    passo_use = max(0.35, float(passo_ft))
    if (area_bb / (passo_use * passo_use)) > float(max_pts):
        passo_use = math.sqrt(area_bb / float(max_pts))
    elif min(w, h) < passo_use * 2.5:
        passo_use = max(0.25, min(w, h) / 4.0)

    # Reduz margem se o ambiente for estreito (ex.: corredor / lavabo)
    margem_ef = min(float(margem_ft), min(w, h) * 0.22)

    nx = max(3, int(math.ceil(w / passo_use)))
    ny = max(3, int(math.ceil(h / passo_use)))
    dx = w / float(nx)
    dy = h / float(ny)

    pts = []
    pts_fallback = []
    for ix in range(nx):
        px = xmin + (ix + 0.5) * dx
        for iy in range(ny):
            py = ymin + (iy + 0.5) * dy
            pts_fallback.append((px, py))
            if segs_2d and margem_ef > 1e-4:
                d_min = 9999.0
                for (a, b) in segs_2d:
                    d = _dist_ponto_segmento_2d(px, py, a[0], a[1], b[0], b[1])
                    if d < d_min:
                        d_min = d
                        if d_min < margem_ef:
                            break
                if d_min >= margem_ef:
                    pts.append((px, py))
            else:
                pts.append((px, py))

    return pts if pts else pts_fallback


def consolidar_amostras_altura(amostras_forro_m, amostras_mad_m, amostras_cob_m,
                                modo_sem_forro=0, tol_plano_m=0.03):
    """
    Consolida arrays de alturas medidas [m] por camada e seleciona a referencia correta:
      modo_sem_forro:
        0 = Prioriza Forro -> Madeiramento (caibros/tercas) -> Telhado/Laje
        1 = Prioriza Forro -> Telhado/Laje (ignora madeiramento)
        2 = Ponto a ponto (usa menor altura livre encontrada acima em qualquer camada)
    Retorna dict {'ok': bool, 'h_min_m': float, 'h_max_m': float, 'ref': unicode, 'is_inclinado': bool}.
    """
    valid_forro = sorted([float(x) for x in (amostras_forro_m or []) if 1.2 <= x <= 20.0])
    valid_mad = sorted([float(x) for x in (amostras_mad_m or []) if 1.2 <= x <= 20.0])
    valid_cob = sorted([float(x) for x in (amostras_cob_m or []) if 1.2 <= x <= 20.0])

    # Remove eventuais outliers extremos (< 3 pontos em relacao ao corpo principal apenas se amostra grande)
    def _limpar(vals):
        if len(vals) >= 12:
            # Descarta 1 extremo pontual inferior/superior apenas se isolado (> 40 cm do vizinho)
            ini = 1 if (vals[1] - vals[0] > 0.40) else 0
            fim = (len(vals) - 1) if (vals[-1] - vals[-2] > 0.40) else len(vals)
            sub = vals[ini:fim]
            if sub:
                return sub
        return vals

    def _resultado(vals, base_ref):
        vals = _limpar(vals)
        if not vals:
            return None
        h_min = min(vals)
        h_max = max(vals)
        incl = (round(h_max, 2) - round(h_min, 2)) > max(0.0, float(tol_plano_m)) + 1e-6
        ref_txt = (base_ref + u" (Inclinado)") if incl else base_ref
        return {
            "ok": True,
            "h_min_m": round(h_min, 3),
            "h_max_m": round(h_max, 3),
            "ref": ref_txt,
            "is_inclinado": incl,
            "n_amostras": len(vals),
        }

    total_pts = max(len(valid_forro), len(valid_mad), len(valid_cob), 1)

    if modo_sem_forro == 2:
        # Combina todas as alturas disponiveis ja filtradas por menor cota em cada ponto
        # (quando o chamador passar amostras combinadas ou escolhemos minimo)
        todos = sorted(valid_forro + valid_mad + valid_cob)
        if todos:
            # Se houver forro suficiente, prioriza forro; senao combina mad + cob
            if len(valid_forro) >= max(2, int(0.35 * len(todos))):
                return _resultado(valid_forro, u"Forro")
            return _resultado(todos, u"Estrutura / Cobertura")

    # 1. Se existe Forro cobrindo o ambiente (>= 2 pontos ou >= 25% das leituras validas)
    if valid_forro and (len(valid_forro) >= 3 or (not valid_mad and not valid_cob) or len(valid_forro) >= 0.25 * total_pts):
        return _resultado(valid_forro, u"Forro")

    # 2. Sem forro: verifica modo escolhido
    if modo_sem_forro == 0 and valid_mad:
        # Usa exclusivamente os hits no madeiramento para evitar que raios passem nas frestas entre caibros e peguem a telha (+5..15cm)
        return _resultado(valid_mad, u"Madeiramento")

    if valid_cob:
        return _resultado(valid_cob, u"Telhado / Laje")

    if valid_mad:
        return _resultado(valid_mad, u"Madeiramento")

    return {"ok": False, "h_min_m": 0.0, "h_max_m": 0.0, "ref": u"Não identificado", "is_inclinado": False, "n_amostras": 0}


# =============================================================================
# INTERFACE WPF
# =============================================================================
XAML_PEDIREITO = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="AutoBIM - Pé-Direito Automático para Tags de Ambiente"
        Width="525" SizeToContent="Height"
        WindowStartupLocation="CenterScreen" ResizeMode="NoResize"
        Background="#F3F3F3" FontFamily="Segoe UI" FontSize="12">
    <StackPanel Margin="16">
        <Border Background="#E3EEF9" BorderBrush="#0078D4" BorderThickness="0,0,0,2"
                Padding="10,8" Margin="0,0,0,10">
            <TextBlock TextWrapping="Wrap" FontWeight="SemiBold" Foreground="#1F3A57"
                       Text="Mede automaticamente o pé-direito livre dentro de cada Ambiente (teto reto, forro inclinado ou sem forro até o madeiramento/laje) e grava o resultado pronto para exibição nas Tags de Ambiente."/>
        </Border>

        <Border Background="White" BorderBrush="#D0D7DE" BorderThickness="1" CornerRadius="4" Padding="10" Margin="0,0,0,8">
            <StackPanel>
                <TextBlock Text="Escopo de Ambientes (Rooms)" FontWeight="SemiBold" Margin="0,0,0,4"/>
                <ComboBox x:Name="cb_pd_escopo" Height="26" Margin="0,0,0,8"/>

                <TextBlock Text="Referência quando NÃO houver Forro:" FontWeight="SemiBold" Margin="0,0,0,4"/>
                <ComboBox x:Name="cb_pd_sem_forro" Height="26"/>
            </StackPanel>
        </Border>

        <Border Background="White" BorderBrush="#D0D7DE" BorderThickness="1" CornerRadius="4" Padding="10" Margin="0,0,0,8">
            <StackPanel>
                <TextBlock Text="Formato do Texto na Tag e Precisão" FontWeight="SemiBold" Margin="0,0,0,6"/>
                <Grid>
                    <Grid.ColumnDefinitions>
                        <ColumnDefinition Width="*"/>
                        <ColumnDefinition Width="10"/>
                        <ColumnDefinition Width="115"/>
                        <ColumnDefinition Width="10"/>
                        <ColumnDefinition Width="125"/>
                    </Grid.ColumnDefinitions>
                    <StackPanel Grid.Column="0">
                        <TextBlock Text="Prefixo" FontSize="11" Foreground="#555"/>
                        <TextBox x:Name="tb_pd_prefixo" Height="24" VerticalContentAlignment="Center" Text="PD = "/>
                    </StackPanel>
                    <StackPanel Grid.Column="2">
                        <TextBlock Text="Tol. Teto Reto (cm)" FontSize="11" Foreground="#555"/>
                        <TextBox x:Name="tb_pd_tol_cm" Height="24" VerticalContentAlignment="Center" Text="3"/>
                    </StackPanel>
                    <StackPanel Grid.Column="4">
                        <TextBlock Text="Recuo Borda (cm)" FontSize="11" Foreground="#555"/>
                        <TextBox x:Name="tb_pd_margem_cm" Height="24" VerticalContentAlignment="Center" Text="20"/>
                    </StackPanel>
                </Grid>

                <TextBlock x:Name="lbl_pd_exemplo" Margin="0,8,0,0" FontSize="11" Foreground="#005A9E"
                           Text="Exemplo:  Teto reto → 'PD = 2,80m'   |   Inclinado → 'PD = 2,60 / 3,40m'"/>
            </StackPanel>
        </Border>

        <Border Background="White" BorderBrush="#D0D7DE" BorderThickness="1" CornerRadius="4" Padding="10" Margin="0,0,0,8">
            <StackPanel>
                <TextBlock Text="Gravação nos Parâmetros do Ambiente (Tag)" FontWeight="SemiBold" Margin="0,0,0,4"/>
                <TextBlock TextWrapping="Wrap" FontSize="11" Foreground="#555" Margin="0,0,0,6"
                           Text="Sempre grava nos parâmetros compartilhados 'AutoBIM_PD_Texto', 'AutoBIM_PD_Min', 'AutoBIM_PD_Max' e 'AutoBIM_PD_Ref'."/>
                <TextBlock Text="Copiar também para parâmetro existente na Tag (opcional):" FontSize="11" Foreground="#555" Margin="0,0,0,3"/>
                <ComboBox x:Name="cb_pd_extra_param" Height="25" Margin="0,0,0,6"/>
                <CheckBox x:Name="ck_pd_so_sem_forro_inc" Content="Incluir sufixo apenas informativo na lista final (não altera texto da Tag)" IsChecked="False" Visibility="Collapsed"/>
                <CheckBox x:Name="ck_pd_tab" Content="Gerar / Atualizar Tabela nativa de conferência ('AutoBIM - Pé-Direito por Ambiente')" IsChecked="False" Margin="0,2,0,0"/>
            </StackPanel>
        </Border>

        <StackPanel Orientation="Horizontal" HorizontalAlignment="Right" Margin="0,6,0,0">
            <Button x:Name="btn_cancel" Content="Cancelar" Width="90" Height="30" Margin="0,0,8,0"/>
            <Button x:Name="btn_ok" Content="Calcular e Preencher" Width="175" Height="30"
                    IsDefault="True" Background="#0078D4" Foreground="White" FontWeight="SemiBold"/>
        </StackPanel>
    </StackPanel>
</Window>
"""


def _parse_float(txt, default_val):
    try:
        return float((txt or u"").replace(",", ".").strip())
    except Exception:
        return float(default_val)


if HAS_REVIT:
    class _RoomPickFilter(ISelectionFilter):
        def AllowElement(self, elem):
            return isinstance(elem, Room)

        def AllowReference(self, ref, pt):
            return True

    class PeDireitoWindow(object):
        def __init__(self, prefs, extra_param_names):
            self.window = XamlReader.Parse(XAML_PEDIREITO)
            self.confirmado = False
            self.resultado = None
            w = self.window

            self._cb_escopo = w.FindName("cb_pd_escopo")
            self._cb_sem_forro = w.FindName("cb_pd_sem_forro")
            self._tb_prefixo = w.FindName("tb_pd_prefixo")
            self._tb_tol_cm = w.FindName("tb_pd_tol_cm")
            self._tb_margem_cm = w.FindName("tb_pd_margem_cm")
            self._lbl_exemplo = w.FindName("lbl_pd_exemplo")
            self._cb_extra_param = w.FindName("cb_pd_extra_param")
            self._ck_tab = w.FindName("ck_pd_tab")

            for item in (
                u"Todos os ambientes do projeto",
                u"Ambientes visíveis na vista ativa",
                u"Selecionar ambientes manualmente",
            ):
                self._cb_escopo.Items.Add(item)
            self._cb_escopo.SelectedIndex = int(prefs.get("pd_escopo", 1))

            for item in (
                u"Face inferior do Madeiramento (Caibros / Terças / Vigas)",
                u"Face inferior do Telhado / Laje (ignora madeiramento)",
                u"Menor obstáculo acima ponto a ponto (Geral)",
            ):
                self._cb_sem_forro.Items.Add(item)
            self._cb_sem_forro.SelectedIndex = int(prefs.get("pd_sem_forro", 0))

            self._tb_prefixo.Text = unicode(prefs.get("pd_prefixo", u"PD = "))
            self._tb_tol_cm.Text = unicode(prefs.get("pd_tol_cm", "3"))
            self._tb_margem_cm.Text = unicode(prefs.get("pd_margem_cm", "20"))
            self._ck_tab.IsChecked = bool(prefs.get("pd_tabela", False))

            self._cb_extra_param.Items.Add(u"(Não copiar para outro parâmetro)")
            for pn in extra_param_names:
                self._cb_extra_param.Items.Add(pn)
            saved_extra = prefs.get("pd_extra_param", u"(Não copiar para outro parâmetro)")
            idx_sel = 0
            for i in range(self._cb_extra_param.Items.Count):
                if unicode(self._cb_extra_param.Items[i]) == unicode(saved_extra):
                    idx_sel = i
                    break
            self._cb_extra_param.SelectedIndex = idx_sel

            self._tb_prefixo.TextChanged += self._atualizar_exemplo
            w.FindName("btn_ok").Click += self._ao_ok
            w.FindName("btn_cancel").Click += self._ao_cancelar
            self._atualizar_exemplo(None, None)

        def _atualizar_exemplo(self, s, a):
            pref = self._tb_prefixo.Text if self._tb_prefixo.Text is not None else u""
            ex_reto = formatar_pd_texto(2.80, 2.80, prefixo=pref)
            ex_incl = formatar_pd_texto(2.60, 3.40, prefixo=pref)
            self._lbl_exemplo.Text = u"Exemplo:  Teto reto → '{}'   |   Inclinado → '{}'".format(ex_reto, ex_incl)

        def _ao_cancelar(self, s, a):
            self.window.Close()

        def _ao_ok(self, s, a):
            extra_txt = u""
            if self._cb_extra_param.SelectedIndex > 0:
                extra_txt = unicode(self._cb_extra_param.SelectedItem)
            self.resultado = {
                "pd_escopo": max(0, int(self._cb_escopo.SelectedIndex)),
                "pd_sem_forro": max(0, int(self._cb_sem_forro.SelectedIndex)),
                "pd_prefixo": self._tb_prefixo.Text if self._tb_prefixo.Text is not None else u"PD = ",
                "pd_tol_cm": max(0.0, _parse_float(self._tb_tol_cm.Text, 3.0)),
                "pd_margem_cm": max(0.0, _parse_float(self._tb_margem_cm.Text, 20.0)),
                "pd_extra_param": extra_txt,
                "pd_tabela": bool(self._ck_tab.IsChecked),
            }
            self.confirmado = True
            self.window.Close()

        def ShowDialog(self):
            return self.window.ShowDialog()


# =============================================================================
# MOTOR REVIT DE RAY-CAST + PROJECAO NO MADEIRAMENTO + SHARED PARAMETERS
# =============================================================================
_PD_SP_SPECS = (
    ("AutoBIM_PD_Texto", "text", "a7b1f101-4a9e-4c11-9d20-1100aabbcc01"),
    ("AutoBIM_PD_Ref", "text", "a7b1f102-4a9e-4c11-9d20-1100aabbcc02"),
    ("AutoBIM_PD_Min", "number", "a7b1f103-4a9e-4c11-9d20-1100aabbcc03"),
    ("AutoBIM_PD_Max", "number", "a7b1f104-4a9e-4c11-9d20-1100aabbcc04"),
)


def _descobrir_parametros_texto_room(rooms):
    """Lista parametros gravaveis do tipo String nos Rooms (ex.: Comentarios, Pe-Direito, etc.)."""
    achados = []
    seen = set()
    # Prioriza Comentarios sempre no topo da lista opcional
    achados.append(u"Comentários (Built-in)")
    seen.add(u"comentários")
    seen.add(u"comments")

    for r in rooms[:5]:
        try:
            for p in r.Parameters:
                try:
                    if p.IsReadOnly or p.StorageType != StorageType.String:
                        continue
                    nome = unicode(p.Definition.Name).strip()
                    nl = nome.lower()
                    if nome.startswith("AutoBIM_PD_"):
                        continue
                    if nl in seen:
                        continue
                    seen.add(nl)
                    achados.append(nome)
                except Exception:
                    pass
        except Exception:
            pass
    return achados


def _selecionar_ambientes_pd(escopo_idx):
    if escopo_idx == 2:
        try:
            with forms.WarningBar(title=u"Selecione os Ambientes (Rooms) e clique em Finish"):
                refs = uidoc.Selection.PickObjects(ObjectType.Element, _RoomPickFilter(), u"Selecione os Ambientes")
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
        forms.alert(u"Nenhum Ambiente válido (fechado e com área > 0) encontrado.", exitscript=True)
    return validos


def _criar_vista_3d_temporaria():
    """Cria View3D limpa sem SectionBox, sem Template e em Nivel Fino para ReferenceIntersector."""
    vft = None
    for vt in FilteredElementCollector(doc).OfClass(ViewFamilyType).ToElements():
        if vt.ViewFamily == ViewFamily.ThreeDimensional:
            vft = vt
            break
    if vft is None:
        return None
    v3d = View3D.CreateIsometric(doc, vft.Id)
    try:
        v3d.ViewTemplateId = ElementId.InvalidElementId
    except Exception:
        pass
    try:
        v3d.DetailLevel = ViewDetailLevel.Fine
    except Exception:
        pass
    try:
        v3d.IsSectionBoxActive = False
    except Exception:
        pass
    for bic in (
        BuiltInCategory.OST_Floors,
        BuiltInCategory.OST_Ceilings,
        BuiltInCategory.OST_StructuralFraming,
        BuiltInCategory.OST_CurtainWallMullions,
        BuiltInCategory.OST_Roofs,
        BuiltInCategory.OST_GenericModel,
    ):
        try:
            cid = ElementId(bic)
            if v3d.CanCategoryBeHidden(cid):
                v3d.SetCategoryHidden(cid, False)
        except Exception:
            pass
    return v3d


def _segmentos_borda_room_2d(room):
    segs = []
    try:
        opt = SpatialElementBoundaryOptions()
        loops = room.GetBoundarySegments(opt)
        if loops:
            for lp in loops:
                for bs in lp:
                    c = bs.GetCurve()
                    tess = c.Tessellate()
                    for i in range(len(tess) - 1):
                        p0, p1 = tess[i], tess[i + 1]
                        segs.append(((p0.X, p0.Y), (p1.X, p1.Y)))
    except Exception:
        pass
    return segs


def _pontos_internos_room(room, margem_ft):
    loc_pt = getattr(getattr(room, "Location", None), "Point", None)
    bb = room.get_BoundingBox(None)
    if bb is None:
        return ([loc_pt] if loc_pt is not None else []), 0.0

    z_ref = loc_pt.Z if loc_pt is not None else (bb.Min.Z + 0.50 * FT)
    segs_2d = _segmentos_borda_room_2d(room)
    cands = gerar_candidatos_malha_2d(
        bb.Min.X, bb.Max.X, bb.Min.Y, bb.Max.Y,
        segs_2d=segs_2d, passo_ft=0.35 * FT, margem_ft=margem_ft, max_pts=140
    )

    pts = []
    for (px, py) in cands:
        p = XYZ(px, py, z_ref + 0.30 * FT)
        try:
            if room.IsPointInRoom(p):
                pts.append(p)
        except Exception:
            pass

    if not pts and loc_pt is not None:
        pts.append(XYZ(loc_pt.X, loc_pt.Y, z_ref + 0.30 * FT))

    z_level = bb.Min.Z
    try:
        if room.Level is not None:
            z_elev = room.Level.Elevation
            p_off = room.get_Parameter(BuiltInParameter.ROOM_LOWER_OFFSET)
            off_val = p_off.AsDouble() if p_off is not None else 0.0
            z_level = z_elev + off_val
    except Exception:
        pass
    return pts, z_level


def selecionar_hit_mais_proximo(hits, origin_z, dir_z, z_min_limit, z_max_limit, min_prox=0.05 * FT):
    """
    Seleciona a intersecao de menor distancia (Proximity > min_prox) dentro da faixa de cota valida.
    Importante: ReferenceIntersector.Find() NAO garante ordenacao por Proximity,
    portanto NUNCA interromper no primeiro resultado valido.
    hits: iteravel de (proximity, is_skipped).
    """
    best_z = None
    best_d = 1e12
    for d, skip in hits:
        if skip or d < min_prox:
            continue
        z_hit = origin_z + dir_z * d
        if z_hit < z_min_limit or z_hit > z_max_limit:
            continue
        if d < best_d:
            best_d = d
            best_z = z_hit
    return best_z


def _elemento_eh_ripa(el):
    if el is None:
        return False
    try:
        pc = el.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
        if pc is not None and pc.AsString() and u"ripa" in unicode(pc.AsString()).lower():
            return True
    except Exception:
        pass
    try:
        tid = el.GetTypeId()
        if tid != ElementId.InvalidElementId:
            tp = doc.GetElement(tid)
            if tp is not None:
                nm = u""
                try:
                    nm = unicode(Element.Name.GetValue(tp) or u"").lower()
                except Exception:
                    nm = unicode(getattr(tp, "Name", u"") or u"").lower()
                if u"ripa" in nm:
                    return True
    except Exception:
        pass
    return False


def _coletar_caixas_madeiramento_room_fallback():
    """Coleta elementos e BoundingBoxes de madeiramento/estrutura/montantes (exclui Ripas)."""
    itens = []
    for bic in (
        BuiltInCategory.OST_StructuralFraming,
        BuiltInCategory.OST_CurtainWallMullions,
        BuiltInCategory.OST_GenericModel,
    ):
        for el in FilteredElementCollector(doc).OfCategory(bic).WhereElementIsNotElementType().ToElements():
            try:
                if _elemento_eh_ripa(el):
                    continue
                if bic == BuiltInCategory.OST_GenericModel:
                    pc = el.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
                    comm = unicode(pc.AsString() or u"").lower() if pc is not None else u""
                    if not any(k in comm for k in (u"caibro", u"terça", u"terca", u"tesoura", u"treliç", u"trelic", u"pontalete", u"viga")):
                        continue
                bb = el.get_BoundingBox(None)
                if bb is not None:
                    itens.append((el, bb))
            except Exception:
                pass
    return itens


def _pontos_contorno_e_eixos_room(room, z_test):
    """
    Gera pontos rentes ao perimetro interno (3 cm das paredes) e nos eixos centrais da sala.
    Essencial para capturar altura minima junto a parede e altura maxima na cumeeira
    em ambientes sem forro ou com teto inclinado.
    """
    pts = []
    segs_2d = _segmentos_borda_room_2d(room)
    for (a, b) in segs_2d:
        vx, vy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(vx, vy)
        if L < 0.05 * FT:
            continue
        nx, ny = -vy / L, vx / L
        n_div = max(2, min(14, int(math.ceil(L / (0.35 * FT)))))
        for i in range(n_div + 1):
            t = 0.02 + 0.96 * (float(i) / float(n_div))
            bx = a[0] + vx * t
            by = a[1] + vy * t
            for sign in (1.0, -1.0):
                px = bx + sign * nx * 0.03 * FT
                py = by + sign * ny * 0.03 * FT
                cand = XYZ(px, py, z_test)
                try:
                    if room.IsPointInRoom(cand):
                        pts.append(cand)
                        break
                except Exception:
                    pass

    bb = room.get_BoundingBox(None)
    if bb is not None:
        mx = 0.5 * (bb.Min.X + bb.Max.X)
        my = 0.5 * (bb.Min.Y + bb.Max.Y)
        for i in range(15):
            t = 0.02 + 0.96 * (float(i) / 14.0)
            for cx, cy in (
                (mx, bb.Min.Y + t * (bb.Max.Y - bb.Min.Y)),
                (bb.Min.X + t * (bb.Max.X - bb.Min.X), my),
            ):
                cand = XYZ(cx, cy, z_test)
                try:
                    if room.IsPointInRoom(cand):
                        pts.append(cand)
                except Exception:
                    pass
    return pts


def _medir_faces_inferiores_madeiramento(room, z_piso_ref, framing_items, z_min_lim, z_max_lim):
    """
    Complemento geometrico direto nos Solidos das pecas de madeiramento (caibros/tercas/mullions):
    projeta os pontos das faces inferiores dentro do ambiente e devolve lista de alturas [m].
    """
    alturas = []
    bb_r = room.get_BoundingBox(None)
    if bb_r is None or not framing_items:
        return alturas

    opt = Options()
    try:
        opt.DetailLevel = ViewDetailLevel.Fine
    except Exception:
        pass

    z_test = z_piso_ref + 0.30 * FT

    def _collect_solids(geom_elem):
        out = []
        if geom_elem is None:
            return out
        for g in geom_elem:
            if isinstance(g, Solid) and g.Volume > 1e-7:
                out.append(g)
            elif hasattr(g, "GetInstanceGeometry"):
                try:
                    out.extend(_collect_solids(g.GetInstanceGeometry()))
                except Exception:
                    pass
        return out

    for el, fbb in framing_items:
        if (fbb.Max.X < bb_r.Min.X or fbb.Min.X > bb_r.Max.X or
                fbb.Max.Y < bb_r.Min.Y or fbb.Min.Y > bb_r.Max.Y or
                fbb.Max.Z < z_min_lim or fbb.Min.Z > z_max_lim):
            continue
        try:
            geom = el.get_Geometry(opt)
        except Exception:
            geom = None
        for sol in _collect_solids(geom):
            for f in sol.Faces:
                if not isinstance(f, PlanarFace):
                    continue
                fn = f.FaceNormal
                if fn.Z >= -0.15:
                    continue
                for loop in f.GetEdgesAsCurveLoops():
                    for crv in loop:
                        p0 = crv.GetEndPoint(0)
                        p1 = crv.GetEndPoint(1)
                        L_crv = p0.DistanceTo(p1)
                        n_sub = max(2, min(20, int(math.ceil(L_crv / (0.15 * FT)))))
                        for si in range(n_sub + 1):
                            t = float(si) / float(n_sub)
                            p = p0 + (p1 - p0) * t
                            if p.Z < z_min_lim or p.Z > z_max_lim:
                                continue
                            if (p.X < bb_r.Min.X or p.X > bb_r.Max.X or
                                    p.Y < bb_r.Min.Y or p.Y > bb_r.Max.Y):
                                continue
                            try:
                                if room.IsPointInRoom(XYZ(p.X, p.Y, z_test)):
                                    alturas.append((p.Z - z_piso_ref) * 0.3048)
                            except Exception:
                                pass
    return alturas


def _medir_room_com_raycast(room, intersectors, framing_boxes, op):
    margem_ft = op["pd_margem_cm"] / 30.48
    pts_xy, z_level = _pontos_internos_room(room, margem_ft)
    if not pts_xy:
        return {"ok": False, "h_min_m": 0.0, "h_max_m": 0.0, "ref": u"Sem pontos", "is_inclinado": False, "n_amostras": 0}

    bb_r = room.get_BoundingBox(None)
    # Adiciona pontos amostrados exatamente embaixo dos caibros/tercas que cruzam a sala
    # para capturar com exatidao PD_Min e PD_Max inclinados mesmo entre frestas da grade regular!
    pts_extra_mad = []
    if bb_r is not None and framing_boxes:
        z_test = pts_xy[0].Z
        for _, fbb in framing_boxes:
            if (fbb.Max.X < bb_r.Min.X or fbb.Min.X > bb_r.Max.X or
                    fbb.Max.Y < bb_r.Min.Y or fbb.Min.Y > bb_r.Max.Y or
                    fbb.Max.Z < z_level + 1.2 * FT or fbb.Min.Z > z_level + 15.0 * FT):
                continue
            ox0 = max(bb_r.Min.X + 0.2 * FT, fbb.Min.X)
            ox1 = min(bb_r.Max.X - 0.2 * FT, fbb.Max.X)
            oy0 = max(bb_r.Min.Y + 0.2 * FT, fbb.Min.Y)
            oy1 = min(bb_r.Max.Y - 0.2 * FT, fbb.Max.Y)
            if ox1 < ox0 or oy1 < oy0:
                continue
            for t in (0.01, 0.08, 0.50, 0.92, 0.99):
                cands_box = (
                    (0.5 * (ox0 + ox1), oy0 + t * (oy1 - oy0)),
                    (ox0 + t * (ox1 - ox0), 0.5 * (oy0 + oy1)),
                    (ox0 + t * (ox1 - ox0), oy0 + t * (oy1 - oy0)),
                    (ox0 + t * (ox1 - ox0), oy1 - t * (oy1 - oy0)),
                )
                for cx_b, cy_b in cands_box:
                    cand = XYZ(cx_b, cy_b, z_test)
                    try:
                        if room.IsPointInRoom(cand):
                            pts_extra_mad.append(cand)
                    except Exception:
                        pass
                if len(pts_extra_mad) >= 220:
                    break
            if len(pts_extra_mad) >= 220:
                break

    def _hit_nearest_z(intersector, origin, direction, z_min_limit, z_max_limit, skip_ripa=False):
        try:
            refs = intersector.Find(origin, direction)
            hits = []
            for rw in refs:
                d = rw.Proximity
                skip = False
                if skip_ripa:
                    try:
                        el_hit = doc.GetElement(rw.GetReference().ElementId)
                        if _elemento_eh_ripa(el_hit):
                            skip = True
                    except Exception:
                        pass
                hits.append((d, skip))
            return selecionar_hit_mais_proximo(
                hits, origin.Z, direction.Z, z_min_limit, z_max_limit, min_prox=0.05 * FT
            )
        except Exception:
            return None

    # Determina z_piso mediano da sala (a partir dos pisos reais ou nivel base)
    z_pisos = []
    ri_floor = intersectors["floor"]
    for pt in pts_xy[:25]:
        zp = _hit_nearest_z(ri_floor, XYZ(pt.X, pt.Y, z_level + 1.0 * FT), -XYZ.BasisZ,
                            z_level - 0.35 * FT, z_level + 0.45 * FT)
        if zp is not None:
            z_pisos.append(zp)
    z_piso_ref = sorted(z_pisos)[len(z_pisos) // 2] if z_pisos else z_level

    amostras_forro = []
    amostras_mad = []
    amostras_cob = []

    z_start = z_piso_ref + 0.50 * FT
    z_min_lim = z_piso_ref + 1.40 * FT
    z_max_lim = z_piso_ref + 15.0 * FT

    ri_ceil = intersectors["ceil"]
    ri_mad = intersectors["mad"]
    ri_cob = intersectors["cob"]

    for pt in pts_xy:
        orig = XYZ(pt.X, pt.Y, z_start)
        zf = _hit_nearest_z(ri_ceil, orig, XYZ.BasisZ, z_min_lim, z_max_lim)
        if zf is not None:
            amostras_forro.append((zf - z_piso_ref) * 0.3048)

        zm = _hit_nearest_z(ri_mad, orig, XYZ.BasisZ, z_min_lim, z_max_lim, skip_ripa=True)
        if zm is not None:
            amostras_mad.append((zm - z_piso_ref) * 0.3048)

        zc = _hit_nearest_z(ri_cob, orig, XYZ.BasisZ, z_min_lim, z_max_lim)
        if zc is not None:
            amostras_cob.append((zc - z_piso_ref) * 0.3048)

    for pt in pts_extra_mad:
        orig = XYZ(pt.X, pt.Y, z_start)
        zm = _hit_nearest_z(ri_mad, orig, XYZ.BasisZ, z_min_lim, z_max_lim, skip_ripa=True)
        if zm is not None:
            amostras_mad.append((zm - z_piso_ref) * 0.3048)

    tol_m = op["pd_tol_cm"] / 100.0
    forro_plano = (
        len(amostras_forro) >= 3 and
        (round(max(amostras_forro), 2) - round(min(amostras_forro), 2)) <= tol_m + 1e-6
    )

    # Em ambientes SEM forro plano (forro inclinado, madeiramento ou telhado aparente),
    # faz varredura adicional rente as paredes (3 cm) + eixos centrais + faces inferiores dos solidos
    # para capturar com exatidao a cota minima no beiral/parede e maxima na cumeeira.
    if not forro_plano:
        pts_edge = _pontos_contorno_e_eixos_room(room, pts_xy[0].Z)
        for pt in pts_edge:
            orig = XYZ(pt.X, pt.Y, z_start)
            zf = _hit_nearest_z(ri_ceil, orig, XYZ.BasisZ, z_min_lim, z_max_lim)
            if zf is not None:
                amostras_forro.append((zf - z_piso_ref) * 0.3048)
            zm = _hit_nearest_z(ri_mad, orig, XYZ.BasisZ, z_min_lim, z_max_lim, skip_ripa=True)
            if zm is not None:
                amostras_mad.append((zm - z_piso_ref) * 0.3048)
            zc = _hit_nearest_z(ri_cob, orig, XYZ.BasisZ, z_min_lim, z_max_lim)
            if zc is not None:
                amostras_cob.append((zc - z_piso_ref) * 0.3048)

        if op["pd_sem_forro"] != 1 and framing_boxes:
            amostras_mad.extend(
                _medir_faces_inferiores_madeiramento(room, z_piso_ref, framing_boxes, z_min_lim, z_max_lim)
            )

    return consolidar_amostras_altura(
        amostras_forro, amostras_mad, amostras_cob,
        modo_sem_forro=op["pd_sem_forro"], tol_plano_m=tol_m
    )


def _garantir_parametros_pd_rooms():
    app = doc.Application
    orig_sp = app.SharedParametersFilename
    tmp_path = os.path.join(os.path.dirname(__file__), "_AutoBIM_PeDireito_SP.txt")
    if not os.path.exists(tmp_path):
        with codecs.open(tmp_path, "w", encoding="utf-8") as f:
            f.write("")

    cat_rooms = doc.Settings.Categories.get_Item(BuiltInCategory.OST_Rooms)
    if cat_rooms is None:
        return False
    cat_set = CategorySet()
    cat_set.Insert(cat_rooms)

    try:
        app.SharedParametersFilename = tmp_path
        sp_file = app.OpenSharedParameterFile()
        if sp_file is None:
            return False
        grp = sp_file.Groups.get_Item("AutoBIM_Ambientes")
        if grp is None:
            grp = sp_file.Groups.Create("AutoBIM_Ambientes")

        for pname, pkind, pguid in _PD_SP_SPECS:
            defn = grp.Definitions.get_Item(pname)
            if defn is None:
                try:
                    from Autodesk.Revit.DB import SpecTypeId
                    spec = SpecTypeId.String.Text if pkind == "text" else SpecTypeId.Number
                    opts = ExternalDefinitionCreationOptions(pname, spec)
                except Exception:
                    from Autodesk.Revit.DB import ParameterType
                    pt = ParameterType.Text if pkind == "text" else ParameterType.Number
                    opts = ExternalDefinitionCreationOptions(pname, pt)
                opts.Visible = True
                try:
                    import System
                    opts.GUID = System.Guid(pguid)
                except Exception:
                    pass
                defn = grp.Definitions.Create(opts)

            binding = app.Create.NewInstanceBinding(cat_set)
            try:
                from Autodesk.Revit.DB import GroupTypeId
                pg = GroupTypeId.Data
            except Exception:
                from Autodesk.Revit.DB import BuiltInParameterGroup
                pg = BuiltInParameterGroup.PG_DATA

            try:
                if doc.ParameterBindings.Contains(defn):
                    doc.ParameterBindings.ReInsert(defn, binding, pg)
                else:
                    doc.ParameterBindings.Insert(defn, binding, pg)
            except Exception:
                try:
                    doc.ParameterBindings.Insert(defn, binding, pg)
                except Exception:
                    pass
    finally:
        try:
            app.SharedParametersFilename = orig_sp or ""
        except Exception:
            pass
    return True


def _obter_ou_criar_tabela_pd():
    from Autodesk.Revit.DB import ViewSchedule, ScheduleFieldDisplayType, ScheduleSortGroupField, ScheduleSortOrder
    nome_tab = u"AutoBIM - Pé-Direito por Ambiente"
    for vs in FilteredElementCollector(doc).OfClass(ViewSchedule).ToElements():
        try:
            if not vs.IsTemplate and vs.Name == nome_tab:
                return vs
        except Exception:
            pass
    vs = ViewSchedule.CreateSchedule(doc, ElementId(BuiltInCategory.OST_Rooms))
    try:
        vs.Name = nome_tab
    except Exception:
        pass
    defn = vs.Definition
    defn.ClearFields()

    sfields = list(defn.GetSchedulableFields())
    by_name = {}
    by_bip = {}
    for sf in sfields:
        try:
            by_name[sf.GetName(doc).strip()] = sf
        except Exception:
            pass
        try:
            pid_int = int(sf.ParameterId.IntegerValue) if hasattr(sf.ParameterId, "IntegerValue") else int(sf.ParameterId.Value)
            by_bip[pid_int] = sf
        except Exception:
            pass

    col_specs = [
        (by_bip.get(int(BuiltInParameter.ROOM_NUMBER)), u"Número", True),
        (by_bip.get(int(BuiltInParameter.ROOM_NAME)), u"Ambiente", False),
        (by_name.get("AutoBIM_PD_Texto"), u"Pé-Direito (Tag)", False),
        (by_name.get("AutoBIM_PD_Min"), u"PD Mín. (m)", False),
        (by_name.get("AutoBIM_PD_Max"), u"PD Máx. (m)", False),
        (by_name.get("AutoBIM_PD_Ref"), u"Referência", False),
    ]
    f_num = None
    for sf, title, is_sort in col_specs:
        if sf is None:
            continue
        fld = defn.AddField(sf)
        fld.ColumnHeading = title
        if is_sort:
            f_num = fld
    if f_num is not None:
        defn.AddSortGroupField(ScheduleSortGroupField(f_num.FieldId, ScheduleSortOrder.Ascending))
    return vs


def run_pe_direito_auto():
    global doc, uidoc, view
    uidoc = __revit__.ActiveUIDocument
    doc = uidoc.Document
    view = doc.ActiveView

    prefs = carregar_preferencias()
    # Coleta preventiva de parametros de texto disponiveis no Room
    sample_rooms = [
        el for el in FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Rooms).WhereElementIsNotElementType().ToElements()
        if getattr(el, "Area", 0.0) > 1e-4
    ]
    if not sample_rooms:
        forms.alert(u"Nenhum Ambiente (Room) válido encontrado no projeto.", exitscript=True)

    extra_params = _descobrir_parametros_texto_room(sample_rooms)
    win = PeDireitoWindow(prefs, extra_params)
    win.ShowDialog()
    if not win.confirmado:
        script.exit()

    op = win.resultado
    salvar_preferencias(op)

    rooms = _selecionar_ambientes_pd(op["pd_escopo"])

    resultados = []
    temp_v3d = None

    with TransactionGroup(doc, "AutoBIM: Pé-Direito Automático") as tg:
        tg.Start()

        # Passo 1: cria vista 3D temporaria para ReferenceIntersector medir os ambientes com precisao
        with Transaction(doc, "Criar Vista 3D Auxiliar") as t_v:
            t_v.Start()
            temp_v3d = _criar_vista_3d_temporaria()
            t_v.Commit()

        if temp_v3d is None:
            tg.RollBack()
            forms.alert(u"Não foi possível inicializar a vista 3D para leitura geométrica.", exitscript=True)

        f_floor = ElementMulticategoryFilter(List[BuiltInCategory]([BuiltInCategory.OST_Floors]))
        f_ceil = ElementMulticategoryFilter(List[BuiltInCategory]([BuiltInCategory.OST_Ceilings]))
        f_mad = ElementMulticategoryFilter(List[BuiltInCategory]([
            BuiltInCategory.OST_StructuralFraming,
            BuiltInCategory.OST_CurtainWallMullions,
            BuiltInCategory.OST_GenericModel,
        ]))
        f_cob = ElementMulticategoryFilter(List[BuiltInCategory]([BuiltInCategory.OST_Roofs, BuiltInCategory.OST_Floors]))

        intersectors = {
            "floor": ReferenceIntersector(f_floor, FindReferenceTarget.Face, temp_v3d),
            "ceil": ReferenceIntersector(f_ceil, FindReferenceTarget.Face, temp_v3d),
            "mad": ReferenceIntersector(f_mad, FindReferenceTarget.Face, temp_v3d),
            "cob": ReferenceIntersector(f_cob, FindReferenceTarget.Face, temp_v3d),
        }
        for ri in intersectors.values():
            ri.FindReferencesInRevitLinks = True

        framing_boxes = _coletar_caixas_madeiramento_room_fallback()

        rooms_ord = sorted(
            rooms,
            key=lambda r: (
                unicode(r.get_Parameter(BuiltInParameter.ROOM_NUMBER).AsString() or u"") if r.get_Parameter(BuiltInParameter.ROOM_NUMBER) else u"",
                unicode(r.get_Parameter(BuiltInParameter.ROOM_NAME).AsString() or u"") if r.get_Parameter(BuiltInParameter.ROOM_NAME) else u"",
            ),
        )

        tol_m = op["pd_tol_cm"] / 100.0
        for r in rooms_ord:
            med = _medir_room_com_raycast(r, intersectors, framing_boxes, op)
            txt = formatar_pd_texto(med["h_min_m"], med["h_max_m"], tol_plano_m=tol_m, prefixo=op["pd_prefixo"]) if med["ok"] else u""
            r_num = u""
            r_nom = u""
            try:
                r_num = unicode(r.get_Parameter(BuiltInParameter.ROOM_NUMBER).AsString() or u"")
                r_nom = unicode(r.get_Parameter(BuiltInParameter.ROOM_NAME).AsString() or u"")
            except Exception:
                pass
            resultados.append({
                "room": r,
                "label": (r_num + u" " + r_nom).strip() or u"Ambiente",
                "med": med,
                "texto": txt,
            })

        # Passo 2: remove vista 3D temporaria, vincula Shared Parameters e grava resultados nas Rooms
        tab_pd = None
        qtd_ok = 0
        with Transaction(doc, "Gravar Pé-Direito nos Ambientes") as t_w:
            t_w.Start()
            try:
                doc.Delete(temp_v3d.Id)
            except Exception:
                pass

            _garantir_parametros_pd_rooms()
            try:
                doc.Regenerate()
            except Exception:
                pass

            extra_name = op.get("pd_extra_param", u"")
            copy_comment = (extra_name == u"Comentários (Built-in)")

            for res in resultados:
                if not res["med"]["ok"]:
                    continue
                rm = res["room"]
                med = res["med"]
                txt = res["texto"]
                for pname, pval in (
                    ("AutoBIM_PD_Texto", txt),
                    ("AutoBIM_PD_Ref", med["ref"]),
                    ("AutoBIM_PD_Min", float(round(med["h_min_m"], 2))),
                    ("AutoBIM_PD_Max", float(round(med["h_max_m"], 2))),
                ):
                    try:
                        p = rm.LookupParameter(pname)
                        if p is not None and not p.IsReadOnly:
                            p.Set(pval)
                    except Exception:
                        pass

                if copy_comment:
                    try:
                        pc = rm.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
                        if pc is not None and not pc.IsReadOnly:
                            pc.Set(txt)
                    except Exception:
                        pass
                elif extra_name:
                    try:
                        pe = rm.LookupParameter(extra_name)
                        if pe is not None and not pe.IsReadOnly and pe.StorageType == StorageType.String:
                            pe.Set(txt)
                    except Exception:
                        pass
                qtd_ok += 1

            if op["pd_tabela"]:
                tab_pd = _obter_ou_criar_tabela_pd()

            t_w.Commit()

        tg.Assimilate()

    linhas = [
        u"Pé-Direito Automático concluído!\n",
        u"✔ Ambientes processados: {}".format(len(resultados)),
        u"✔ Ambientes gravados com sucesso: {}".format(qtd_ok),
        u"✔ Parâmetro p/ Tag de Ambiente: AutoBIM_PD_Texto",
    ]
    if op.get("pd_extra_param"):
        linhas.append(u"✔ Copiado também para: {}".format(op["pd_extra_param"]))
    if tab_pd is not None:
        linhas.append(u"✔ Tabela Revit: {}".format(tab_pd.Name))

    linhas.append(u"\n--- Leituras por Ambiente ---")
    for res in resultados[:16]:
        if res["med"]["ok"]:
            linhas.append(u"• {}: {}  [{}]".format(res["label"], res["texto"], res["med"]["ref"]))
        else:
            linhas.append(u"• {}: Não detectado (sem forro/estrutura/laje acima)".format(res["label"]))
    if len(resultados) > 16:
        linhas.append(u"... e mais {} ambientes.".format(len(resultados) - 16))

    forms.alert(u"\n".join(linhas), title="AutoBIM: Pé-Direito Automático")
