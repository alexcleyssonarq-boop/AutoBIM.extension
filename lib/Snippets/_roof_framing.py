# -*- coding: utf-8 -*-
"""Motor 3D para Madeiramento de Telhado (Ripas, Caibros, Terças, Vidraça Inclinada, Tesouras SINAPI e Pontaletes)."""

import os
import json
import math
import codecs
import tempfile
import clr
clr.AddReference("System")
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")

from System.Collections.Generic import List
from Autodesk.Revit.DB import *
from System.Windows import Visibility as WpfVisibility
from Autodesk.Revit.DB.Structure import StructuralType
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
from pyrevit import forms, script

doc   = None
uidoc = None
view  = None

FT = 1.0 / 0.3048
SETTINGS_PATH = os.path.join(os.path.dirname(__file__), "_roof_framing_settings.json")


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


class RoofSelectionFilter(ISelectionFilter):
    def AllowElement(self, elem):
        return isinstance(elem, RoofBase)

    def AllowReference(self, ref, pt):
        return True


def selecionar_telhados():
    roofs = []
    for eid in uidoc.Selection.GetElementIds():
        el = doc.GetElement(eid)
        if isinstance(el, RoofBase):
            roofs.append(el)
    if not roofs:
        try:
            with forms.WarningBar(title="Selecione o(s) Telhado(s) e clique em Finish"):
                refs = uidoc.Selection.PickObjects(
                    ObjectType.Element,
                    RoofSelectionFilter(),
                    "Selecione os Telhados"
                )
            roofs = [doc.GetElement(r) for r in refs if isinstance(doc.GetElement(r), RoofBase)]
        except Exception:
            script.exit()
    if not roofs:
        forms.alert("Nenhum telhado selecionado.", exitscript=True)
    return roofs


# =============================================================================
# AGRUPAMENTO AUTOMÁTICO (MODEL GROUP) E SUPRESSÃO DE AVISOS DE GRUPO
# =============================================================================
class GrupoFailurePreprocessor(IFailuresPreprocessor):
    """Suprime avisos automáticos de grupo (ex.: AtomViolationWhenOnePlaceInstance) ao agrupar elementos."""
    def PreprocessFailures(self, failuresAccessor):
        try:
            msgs = failuresAccessor.GetFailureMessages()
            if not msgs or len(msgs) == 0:
                return FailureProcessingResult.Continue
            for fma in list(msgs):
                try:
                    if fma.GetSeverity() == FailureSeverity.Warning:
                        failuresAccessor.DeleteWarning(fma)
                except Exception:
                    pass
        except Exception:
            pass
        return FailureProcessingResult.Continue


def _aplicar_preproc_grupo(t):
    try:
        opts = t.GetFailureHandlingOptions()
        opts.SetFailuresPreprocessor(GrupoFailurePreprocessor())
        opts.SetClearAfterRollback(True)
        t.SetFailureHandlingOptions(opts)
    except Exception:
        pass


def criar_grupo_elementos(ids_lista, nome_base):
    """Cria um Model Group com os ElementIds fornecidos e renomeia com sufixo único."""
    if not ids_lista or len(ids_lista) == 0:
        return None
    try:
        col_ids = List[ElementId](ids_lista)
        grp = doc.Create.NewGroup(col_ids)
        try:
            gt = grp.GroupType
            for i in range(1, 200):
                cand = nome_base if i == 1 else u"{} ({})".format(nome_base, i)
                try:
                    gt.Name = cand
                    break
                except Exception:
                    pass
        except Exception:
            pass
        return grp
    except Exception:
        return None


def agrupar_lote_em_transacao(grupos_pendentes, nome_tx="Agrupar Elementos"):
    """Cria os grupos em uma transação separada após o commit da geometria, eliminando avisos de edição de grupo."""
    validos = [(list(ids), nome) for ids, nome in (grupos_pendentes or []) if ids and len(ids) > 0]
    if not validos:
        return 0
    grupos_criados = 0
    with Transaction(doc, nome_tx) as t_grp:
        t_grp.Start()
        _aplicar_preproc_grupo(t_grp)
        for ids_grp, nome_grp in validos:
            if criar_grupo_elementos(ids_grp, nome_grp):
                grupos_criados += 1
        t_grp.Commit()
    return grupos_criados


# =============================================================================
# MATERIAL E CRIAÇÃO DE SÓLIDOS 3D (QUADRO ESTRUTURAL / MADEIRA)
# =============================================================================
def obter_ou_criar_material_madeira():
    nome_mat = u"Madeira - Telhado (AutoBIM)"
    for m in FilteredElementCollector(doc).OfClass(Material).ToElements():
        try:
            if m.Name == nome_mat:
                return m.Id
        except Exception:
            pass
    fallback_id = ElementId.InvalidElementId
    for m in FilteredElementCollector(doc).OfClass(Material).ToElements():
        try:
            if "madeira" in m.Name.lower() or "wood" in m.Name.lower():
                fallback_id = m.Id
                break
        except Exception:
            pass
    try:
        new_id = Material.Create(doc, nome_mat)
        mat = doc.GetElement(new_id)
        mat.Color = Color(186, 132, 78)
        return new_id
    except Exception:
        return fallback_id


def gerar_solido_prisma(p1, p2, dir_largura, dir_altura, largura_ft, altura_ft,
                        topo_em_p1_p2=True, mat_id=None):
    """
    Constrói e retorna apenas o objeto Solid 3D de uma peça prismática entre p1 e p2
    (permite realizar operações booleanas de encaixe/sambladura antes de criar o DirectShape).
    """
    vetor = p2 - p1
    comp = vetor.GetLength()
    if comp < 0.02 * FT:
        return None

    eixo = vetor.Normalize()
    w_vec = (dir_largura - eixo * dir_largura.DotProduct(eixo))
    if w_vec.GetLength() < 1e-6:
        aux = XYZ.BasisX if abs(eixo.X) < 0.9 else XYZ.BasisY
        w_vec = eixo.CrossProduct(aux)
    w_vec = w_vec.Normalize()
    h_vec = eixo.CrossProduct(w_vec).Normalize()
    if h_vec.DotProduct(dir_altura) < 0:
        h_vec = -h_vec

    meia_b = largura_ft * 0.5
    if topo_em_p1_p2:
        c0 = p1 - w_vec * meia_b
        c1 = p1 + w_vec * meia_b
        c2 = p1 + w_vec * meia_b - h_vec * altura_ft
        c3 = p1 - w_vec * meia_b - h_vec * altura_ft
    else:
        meia_h = altura_ft * 0.5
        c0 = p1 - w_vec * meia_b + h_vec * meia_h
        c1 = p1 + w_vec * meia_b + h_vec * meia_h
        c2 = p1 + w_vec * meia_b - h_vec * meia_h
        c3 = p1 - w_vec * meia_b - h_vec * meia_h

    pts = [c0, c1, c2, c3]
    normal_loop = (pts[1] - pts[0]).CrossProduct(pts[2] - pts[1])
    if normal_loop.DotProduct(eixo) < 0:
        pts.reverse()

    curves = List[Curve]()
    for i in range(4):
        curves.Add(Line.CreateBound(pts[i], pts[(i + 1) % 4]))
    loop = CurveLoop.Create(curves)
    loops = List[CurveLoop]()
    loops.Add(loop)

    solid_opt = SolidOptions(
        mat_id if mat_id else ElementId.InvalidElementId,
        ElementId.InvalidElementId
    )
    return GeometryCreationUtilities.CreateExtrusionGeometry(loops, eixo, comp, solid_opt)


def instanciar_solido_madeira(solido, comentario=u"Madeiramento"):
    """Cria um elemento DirectShape na categoria Quadro Estrutural a partir de um Solid."""
    if solido is None or solido.Volume <= 1e-9:
        return None
    ds = DirectShape.CreateElement(doc, ElementId(BuiltInCategory.OST_StructuralFraming))
    ds.SetShape(List[GeometryObject]([solido]))
    try:
        p_comm = ds.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
        if p_comm and not p_comm.IsReadOnly:
            p_comm.Set(comentario)
    except Exception:
        pass
    return ds


def criar_peca_madeira_3d(p1, p2, dir_largura, dir_altura, largura_ft, altura_ft,
                          topo_em_p1_p2=True, mat_id=None, comentario=u"Madeiramento"):
    comp = (p2 - p1).GetLength()
    solido = gerar_solido_prisma(
        p1, p2, dir_largura, dir_altura, largura_ft, altura_ft,
        topo_em_p1_p2=topo_em_p1_p2, mat_id=mat_id
    )
    if solido is None:
        return None
    return instanciar_solido_madeira(
        solido, u"{} (C={:.2f}m)".format(comentario, comp * 0.3048)
    )


def subtrair_solido_seguro(solido_alvo, solido_corte):
    """Recorta solido_corte de solido_alvo mantendo o original caso não haja interseção."""
    if solido_alvo is None or solido_corte is None:
        return solido_alvo
    try:
        res = BooleanOperationsUtils.ExecuteBooleanOperation(
            solido_alvo, solido_corte, BooleanOperationsType.Difference
        )
        if res is not None and res.Volume > 1e-7:
            return res
    except Exception:
        pass
    return solido_alvo


# =============================================================================
# EXTRAÇÃO DAS ÁGUAS DO TELHADO E RECORTES 2D (SCANLINE CLIPPING)
# =============================================================================
def extrair_faces_aguas(roof, usar_face_inferior=True):
    opt = Options()
    opt.DetailLevel = ViewDetailLevel.Fine
    geom = roof.get_Geometry(opt)
    if geom is None:
        return []

    aguas = []
    for obj in geom:
        if not isinstance(obj, Solid) or obj.Volume <= 0:
            continue
        for face in obj.Faces:
            if not isinstance(face, PlanarFace):
                continue
            fn = face.FaceNormal
            if usar_face_inferior:
                if fn.Z > -0.15:
                    continue
                n_up = XYZ(-fn.X, -fn.Y, -fn.Z).Normalize()
            else:
                if fn.Z < 0.15:
                    continue
                n_up = fn.Normalize()

            horiz_len = math.sqrt(n_up.X * n_up.X + n_up.Y * n_up.Y)
            if horiz_len > 1e-4:
                u_horiz = XYZ(-n_up.Y / horiz_len, n_up.X / horiz_len, 0.0).Normalize()
                v_slope = n_up.CrossProduct(u_horiz).Normalize()
                if v_slope.Z < 0:
                    u_horiz = -u_horiz
                    v_slope = -v_slope
            else:
                u_horiz = XYZ.BasisX
                v_slope = XYZ.BasisY

            origin = face.Origin
            loops_uv = []
            all_u = []
            all_v = []

            for cl in face.GetEdgesAsCurveLoops():
                poly_uv = []
                for curve in cl:
                    tess = curve.Tessellate()
                    pts_c = list(tess)[:-1] if (tess and len(tess) > 1) else [curve.GetEndPoint(0)]
                    for p in pts_c:
                        dp = p - origin
                        u = dp.DotProduct(u_horiz)
                        v = dp.DotProduct(v_slope)
                        poly_uv.append((u, v))
                        all_u.append(u)
                        all_v.append(v)
                if len(poly_uv) >= 3:
                    loops_uv.append(poly_uv)

            if not loops_uv or not all_u:
                continue

            aguas.append({
                "face":     face,
                "origin":   origin,
                "n_up":     n_up,
                "u_horiz":  u_horiz,
                "v_slope":  v_slope,
                "loops_uv": loops_uv,
                "u_min":    min(all_u),
                "u_max":    max(all_u),
                "v_min":    min(all_v),
                "v_max":    max(all_v),
            })

    return aguas


def recortar_linha_constante_v(loops_uv, v_val):
    xs = []
    for poly in loops_uv:
        n = len(poly)
        for i in range(n):
            u1, v1 = poly[i]
            u2, v2 = poly[(i + 1) % n]
            if (v1 <= v_val < v2) or (v2 <= v_val < v1):
                if abs(v2 - v1) > 1e-9:
                    u_int = u1 + (v_val - v1) * (u2 - u1) / (v2 - v1)
                    xs.append(u_int)
    xs.sort()
    intervalos = []
    for i in range(0, len(xs) - 1, 2):
        if xs[i + 1] - xs[i] > 0.05 * FT:
            intervalos.append((xs[i], xs[i + 1]))
    return intervalos


def recortar_linha_constante_u(loops_uv, u_val):
    ys = []
    for poly in loops_uv:
        n = len(poly)
        for i in range(n):
            u1, v1 = poly[i]
            u2, v2 = poly[(i + 1) % n]
            if (u1 <= u_val < u2) or (u2 <= u_val < u1):
                if abs(u2 - u1) > 1e-9:
                    v_int = v1 + (u_val - u1) * (v2 - v1) / (u2 - u1)
                    ys.append(v_int)
    ys.sort()
    intervalos = []
    for i in range(0, len(ys) - 1, 2):
        if ys[i + 1] - ys[i] > 0.05 * FT:
            intervalos.append((ys[i], ys[i + 1]))
    return intervalos


def ponto_3d_agua(agua, u, v, offset_abaixo_ft):
    return (
        agua["origin"]
        + agua["u_horiz"] * u
        + agua["v_slope"] * v
        - agua["n_up"] * offset_abaixo_ft
    )


def gerar_posicoes_uniformes(v_ini, v_fim, passo_max, margem_borda=0.0):
    a = v_ini + margem_borda
    b = v_fim - margem_borda
    if b <= a + 0.05 * FT:
        return [(v_ini + v_fim) * 0.5]
    comp = b - a
    n_div = max(1, int(math.ceil(comp / passo_max)))
    passo_real = comp / float(n_div)
    return [a + i * passo_real for i in range(n_div + 1)]


def gerar_posicoes_passo_fixo(val_ini, val_fim, passo, margem_borda=0.0):
    a = val_ini + margem_borda
    b = val_fim - margem_borda
    if b <= a + 0.02 * FT:
        return [(val_ini + val_fim) * 0.5]
    posicoes = []
    atual = a
    while atual <= b - 0.04 * FT:
        posicoes.append(atual)
        atual += passo
    posicoes.append(b)
    return posicoes


# =============================================================================
# COMANDO 1: TRAMA DO TELHADO (RIPAS, CAIBROS E TERÇAS)
# =============================================================================
XAML_TRAMA = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Madeiramento: Ripas, Caibros e Terças" Width="530" SizeToContent="Height"
        WindowStartupLocation="CenterScreen" ResizeMode="NoResize"
        Background="#F3F3F3" FontFamily="Segoe UI" FontSize="12">
    <StackPanel Margin="18">
        <Border Background="#E3EEF9" BorderBrush="#0078D4" BorderThickness="0,0,0,2"
                Padding="10,8" Margin="0,0,0,12">
            <TextBlock TextWrapping="Wrap" FontWeight="SemiBold" Foreground="#1F3A57"
                       Text="Modela automaticamente Ripas, Caibros e Terças acompanhando a inclinação e os recortes de cada água do telhado."/>
        </Border>

        <TextBlock Text="Referência no elemento de Telhado:" FontWeight="SemiBold" Margin="0,0,0,3"/>
        <ComboBox x:Name="cb_face_ref" Height="26" Margin="0,0,0,10"/>

        <Border Background="White" BorderBrush="#D0D7DE" BorderThickness="1" CornerRadius="4" Padding="10" Margin="0,0,0,8">
            <StackPanel>
                <CheckBox x:Name="ck_ripas" Content="1ª Camada — Gerar Ripas (paralelas ao beiral)" FontWeight="SemiBold" IsChecked="True" Margin="0,0,0,6"/>
                <Grid>
                    <Grid.ColumnDefinitions>
                        <ColumnDefinition Width="*"/>
                        <ColumnDefinition Width="10"/>
                        <ColumnDefinition Width="*"/>
                        <ColumnDefinition Width="10"/>
                        <ColumnDefinition Width="*"/>
                    </Grid.ColumnDefinitions>
                    <StackPanel Grid.Column="0">
                        <TextBlock Text="Largura b (cm)" FontSize="11"/>
                        <TextBox x:Name="tb_ripa_b" Height="24" Text="5" VerticalContentAlignment="Center"/>
                    </StackPanel>
                    <StackPanel Grid.Column="2">
                        <TextBlock Text="Altura h (cm)" FontSize="11"/>
                        <TextBox x:Name="tb_ripa_h" Height="24" Text="2" VerticalContentAlignment="Center"/>
                    </StackPanel>
                    <StackPanel Grid.Column="4">
                        <TextBlock Text="Espaçamento (cm)" FontSize="11"/>
                        <TextBox x:Name="tb_ripa_esp" Height="24" Text="35" VerticalContentAlignment="Center"/>
                    </StackPanel>
                </Grid>
            </StackPanel>
        </Border>

        <Border Background="White" BorderBrush="#D0D7DE" BorderThickness="1" CornerRadius="4" Padding="10" Margin="0,0,0,8">
            <StackPanel>
                <CheckBox x:Name="ck_caibros" Content="2ª Camada — Gerar Caibros (no sentido da caída)" FontWeight="SemiBold" IsChecked="True" Margin="0,0,0,6"/>
                <Grid>
                    <Grid.ColumnDefinitions>
                        <ColumnDefinition Width="*"/>
                        <ColumnDefinition Width="10"/>
                        <ColumnDefinition Width="*"/>
                        <ColumnDefinition Width="10"/>
                        <ColumnDefinition Width="*"/>
                    </Grid.ColumnDefinitions>
                    <StackPanel Grid.Column="0">
                        <TextBlock Text="Largura b (cm)" FontSize="11"/>
                        <TextBox x:Name="tb_caibro_b" Height="24" Text="5" VerticalContentAlignment="Center"/>
                    </StackPanel>
                    <StackPanel Grid.Column="2">
                        <TextBlock Text="Altura h (cm)" FontSize="11"/>
                        <TextBox x:Name="tb_caibro_h" Height="24" Text="6" VerticalContentAlignment="Center"/>
                    </StackPanel>
                    <StackPanel Grid.Column="4">
                        <TextBlock Text="Espaçamento (cm)" FontSize="11"/>
                        <TextBox x:Name="tb_caibro_esp" Height="24" Text="50" VerticalContentAlignment="Center"/>
                    </StackPanel>
                </Grid>
            </StackPanel>
        </Border>

        <Border Background="White" BorderBrush="#D0D7DE" BorderThickness="1" CornerRadius="4" Padding="10" Margin="0,0,0,10">
            <StackPanel>
                <CheckBox x:Name="ck_tercas" Content="3ª Camada — Gerar Terças (apoio dos caibros)" FontWeight="SemiBold" IsChecked="True" Margin="0,0,0,6"/>
                <Grid>
                    <Grid.ColumnDefinitions>
                        <ColumnDefinition Width="*"/>
                        <ColumnDefinition Width="8"/>
                        <ColumnDefinition Width="*"/>
                        <ColumnDefinition Width="8"/>
                        <ColumnDefinition Width="*"/>
                        <ColumnDefinition Width="8"/>
                        <ColumnDefinition Width="*"/>
                    </Grid.ColumnDefinitions>
                    <StackPanel Grid.Column="0">
                        <TextBlock Text="Largura b (cm)" FontSize="11"/>
                        <TextBox x:Name="tb_terca_b" Height="24" Text="6" VerticalContentAlignment="Center"/>
                    </StackPanel>
                    <StackPanel Grid.Column="2">
                        <TextBlock Text="Altura h (cm)" FontSize="11"/>
                        <TextBox x:Name="tb_terca_h" Height="24" Text="12" VerticalContentAlignment="Center"/>
                    </StackPanel>
                    <StackPanel Grid.Column="4">
                        <TextBlock Text="Vão máx. (cm)" FontSize="11"/>
                        <TextBox x:Name="tb_terca_esp" Height="24" Text="150" VerticalContentAlignment="Center"/>
                    </StackPanel>
                    <StackPanel Grid.Column="6">
                        <TextBlock Text="Recuo Frechal (cm)" FontSize="11"/>
                        <TextBox x:Name="tb_terca_recuo" Height="24" Text="0" VerticalContentAlignment="Center"/>
                    </StackPanel>
                </Grid>
            </StackPanel>
        </Border>

        <TextBlock Text="Agrupamento ao concluir:" FontWeight="SemiBold" Margin="0,0,0,3"/>
        <ComboBox x:Name="cb_agrupar" Height="26" Margin="0,0,0,12"/>

        <StackPanel Orientation="Horizontal" HorizontalAlignment="Right" Margin="0,4,0,0">
            <Button x:Name="btn_cancel" Content="Cancelar" Width="90" Height="30" Margin="0,0,8,0"/>
            <Button x:Name="btn_ok" Content="Gerar Madeiramento" Width="165" Height="30"
                    IsDefault="True" Background="#0078D4" Foreground="White"/>
        </StackPanel>
    </StackPanel>
</Window>
"""


class TramaWindow(forms.WPFWindow):
    def __init__(self, prefs):
        forms.WPFWindow.__init__(self, XAML_TRAMA, literal_string=True)
        self.confirmado = False
        self.resultado = {}

        self._cb_face = self.FindName("cb_face_ref")
        self._cb_face.Items.Add(u"Abaixo da face inferior do Telhado (Telhado modela a telha)")
        self._cb_face.Items.Add(u"Abaixo da face superior do Telhado (Embutido no volume)")
        self._cb_face.SelectedIndex = int(prefs.get("face_ref_idx", 0))

        self._cb_grp = self.FindName("cb_agrupar")
        self._cb_grp.Items.Add(u"Agrupar tudo em 1 único Grupo (Madeiramento Completo)")
        self._cb_grp.Items.Add(u"Agrupar por camada (Grupos separados: Ripas, Caibros e Terças)")
        self._cb_grp.Items.Add(u"Não agrupar (peças independentes)")
        self._cb_grp.SelectedIndex = int(prefs.get("trama_grp_idx", 0))

        self._ck_ripas   = self.FindName("ck_ripas")
        self._tb_ripa_b  = self.FindName("tb_ripa_b")
        self._tb_ripa_h  = self.FindName("tb_ripa_h")
        self._tb_ripa_e  = self.FindName("tb_ripa_esp")

        self._ck_caibros  = self.FindName("ck_caibros")
        self._tb_caibro_b = self.FindName("tb_caibro_b")
        self._tb_caibro_h = self.FindName("tb_caibro_h")
        self._tb_caibro_e = self.FindName("tb_caibro_esp")

        self._ck_tercas      = self.FindName("ck_tercas")
        self._tb_terca_b     = self.FindName("tb_terca_b")
        self._tb_terca_h     = self.FindName("tb_terca_h")
        self._tb_terca_e     = self.FindName("tb_terca_esp")
        self._tb_terca_recuo = self.FindName("tb_terca_recuo")

        self._ck_ripas.IsChecked   = bool(prefs.get("ripas", True))
        self._tb_ripa_b.Text       = str(prefs.get("ripa_b", "5"))
        self._tb_ripa_h.Text       = str(prefs.get("ripa_h", "2"))
        self._tb_ripa_e.Text       = str(prefs.get("ripa_esp", "35"))

        self._ck_caibros.IsChecked = bool(prefs.get("caibros", True))
        self._tb_caibro_b.Text     = str(prefs.get("caibro_b", "5"))
        self._tb_caibro_h.Text     = str(prefs.get("caibro_h", "6"))
        self._tb_caibro_e.Text     = str(prefs.get("caibro_esp", "50"))

        self._ck_tercas.IsChecked    = bool(prefs.get("tercas", True))
        self._tb_terca_b.Text        = str(prefs.get("terca_b", "6"))
        self._tb_terca_h.Text        = str(prefs.get("terca_h", "12"))
        self._tb_terca_e.Text        = str(prefs.get("terca_esp", "150"))
        self._tb_terca_recuo.Text    = str(prefs.get("terca_recuo", "0"))

        self.FindName("btn_ok").Click     += self.on_ok
        self.FindName("btn_cancel").Click += self.on_cancel

    def on_cancel(self, sender, args):
        self.confirmado = False
        self.Close()

    def on_ok(self, sender, args):
        try:
            res = {
                "face_ref_idx":  self._cb_face.SelectedIndex,
                "trama_grp_idx": self._cb_grp.SelectedIndex,
                "ripas":         bool(self._ck_ripas.IsChecked),
                "ripa_b":        float(self._tb_ripa_b.Text.replace(",", ".")),
                "ripa_h":        float(self._tb_ripa_h.Text.replace(",", ".")),
                "ripa_esp":      float(self._tb_ripa_e.Text.replace(",", ".")),
                "caibros":       bool(self._ck_caibros.IsChecked),
                "caibro_b":      float(self._tb_caibro_b.Text.replace(",", ".")),
                "caibro_h":      float(self._tb_caibro_h.Text.replace(",", ".")),
                "caibro_esp":    float(self._tb_caibro_e.Text.replace(",", ".")),
                "tercas":        bool(self._ck_tercas.IsChecked),
                "terca_b":       float(self._tb_terca_b.Text.replace(",", ".")),
                "terca_h":       float(self._tb_terca_h.Text.replace(",", ".")),
                "terca_esp":     float(self._tb_terca_e.Text.replace(",", ".")),
                "terca_recuo":   max(0.0, float(self._tb_terca_recuo.Text.replace(",", "."))),
            }
        except Exception:
            forms.alert("Informe apenas valores numéricos válidos.")
            return

        if not (res["ripas"] or res["caibros"] or res["tercas"]):
            forms.alert("Marque pelo menos uma camada (Ripas, Caibros ou Terças).")
            return

        self.resultado = res
        self.confirmado = True
        self.Close()


def run_trama():
    global doc, uidoc, view
    uidoc = __revit__.ActiveUIDocument
    doc   = uidoc.Document
    view  = doc.ActiveView

    prefs = carregar_preferencias()
    win = TramaWindow(prefs)
    win.ShowDialog()
    if not win.confirmado:
        script.exit()

    op = win.resultado
    salvar_preferencias(op)

    roofs = selecionar_telhados()

    ripa_b_ft   = op["ripa_b"] / 30.48
    ripa_h_ft   = op["ripa_h"] / 30.48
    ripa_esp_ft = op["ripa_esp"] / 30.48

    caibro_b_ft   = op["caibro_b"] / 30.48
    caibro_h_ft   = op["caibro_h"] / 30.48
    caibro_esp_ft = op["caibro_esp"] / 30.48

    terca_b_ft     = op["terca_b"] / 30.48
    terca_h_ft     = op["terca_h"] / 30.48
    terca_esp_ft   = op["terca_esp"] / 30.48
    terca_recuo_ft = op.get("terca_recuo", 0.0) / 30.48

    off_topo_ripa   = 0.0
    off_topo_caibro = ripa_h_ft if op["ripas"] else 0.0
    off_topo_terca  = off_topo_caibro + (caibro_h_ft if op["caibros"] else 0.0)

    usar_inferior = (op["face_ref_idx"] == 0)
    ids_ripas   = []
    ids_caibros = []
    ids_tercas  = []
    grupos_criados = 0

    with TransactionGroup(doc, "Madeiramento: Ripas, Caibros e Terças") as tg:
        tg.Start()
        with Transaction(doc, "Madeiramento: Ripas, Caibros e Terças") as t:
            t.Start()
            mat_id = obter_ou_criar_material_madeira()

            for roof in roofs:
                aguas = extrair_faces_aguas(roof, usar_face_inferior=usar_inferior)
                for agua in aguas:
                    u_horiz  = agua["u_horiz"]
                    v_slope  = agua["v_slope"]
                    n_up     = agua["n_up"]
                    loops_uv = agua["loops_uv"]
                    u_min, u_max = agua["u_min"], agua["u_max"]
                    v_min, v_max = agua["v_min"], agua["v_max"]

                    if op["ripas"]:
                        vs_ripa = gerar_posicoes_passo_fixo(v_min, v_max, ripa_esp_ft, ripa_b_ft * 0.5)
                        for vk in vs_ripa:
                            for u_ini, u_fim in recortar_linha_constante_v(loops_uv, vk):
                                p1 = ponto_3d_agua(agua, u_ini, vk, off_topo_ripa)
                                p2 = ponto_3d_agua(agua, u_fim, vk, off_topo_ripa)
                                peca = criar_peca_madeira_3d(
                                    p1, p2, v_slope, n_up, ripa_b_ft, ripa_h_ft,
                                    topo_em_p1_p2=True, mat_id=mat_id,
                                    comentario=u"Ripa {:.0f}x{:.0f}cm".format(op["ripa_b"], op["ripa_h"])
                                )
                                if peca:
                                    ids_ripas.append(peca.Id)

                    if op["caibros"]:
                        us_caibro = gerar_posicoes_passo_fixo(u_min, u_max, caibro_esp_ft, caibro_b_ft * 0.5)
                        for uk in us_caibro:
                            for v_ini, v_fim in recortar_linha_constante_u(loops_uv, uk):
                                p1 = ponto_3d_agua(agua, uk, v_ini, off_topo_caibro)
                                p2 = ponto_3d_agua(agua, uk, v_fim, off_topo_caibro)
                                peca = criar_peca_madeira_3d(
                                    p1, p2, u_horiz, n_up, caibro_b_ft, caibro_h_ft,
                                    topo_em_p1_p2=True, mat_id=mat_id,
                                    comentario=u"Caibro {:.0f}x{:.0f}cm".format(op["caibro_b"], op["caibro_h"])
                                )
                                if peca:
                                    ids_caibros.append(peca.Id)

                    if op["tercas"]:
                        cos_agua = max(0.2, abs(n_up.Z))
                        recuo_v = (terca_recuo_ft / cos_agua) if terca_recuo_ft > 1e-5 else 0.0
                        v_ini_terca = min(v_max, v_min + recuo_v)
                        margem_terca = 0.0 if terca_recuo_ft > 1e-5 else (terca_b_ft * 0.6)
                        vs_terca = gerar_posicoes_uniformes(v_ini_terca, v_max, terca_esp_ft, margem_terca)
                        for vk in vs_terca:
                            for u_ini, u_fim in recortar_linha_constante_v(loops_uv, vk):
                                p1 = ponto_3d_agua(agua, u_ini, vk, off_topo_terca)
                                p2 = ponto_3d_agua(agua, u_fim, vk, off_topo_terca)
                                peca = criar_peca_madeira_3d(
                                    p1, p2, v_slope, n_up, terca_b_ft, terca_h_ft,
                                    topo_em_p1_p2=True, mat_id=mat_id,
                                    comentario=u"Terça {:.0f}x{:.0f}cm".format(op["terca_b"], op["terca_h"])
                                )
                                if peca:
                                    ids_tercas.append(peca.Id)

            t.Commit()

        grupos_pendentes = []
        if op["trama_grp_idx"] == 0:
            grupos_pendentes.append((ids_ripas + ids_caibros + ids_tercas, u"Madeiramento - Trama Completa"))
        elif op["trama_grp_idx"] == 1:
            grupos_pendentes.append((ids_ripas, u"Madeiramento - Ripas"))
            grupos_pendentes.append((ids_caibros, u"Madeiramento - Caibros"))
            grupos_pendentes.append((ids_tercas, u"Madeiramento - Terças"))
        grupos_criados = agrupar_lote_em_transacao(grupos_pendentes, "Agrupar Madeiramento")
        tg.Assimilate()

    msg = u"Madeiramento concluído!\n\n"
    if op["ripas"]:
        msg += u"✔ Ripas criadas: {}\n".format(len(ids_ripas))
    if op["caibros"]:
        msg += u"✔ Caibros criados: {}\n".format(len(ids_caibros))
    if op["tercas"]:
        msg += u"✔ Terças criadas: {}\n".format(len(ids_tercas))
    if grupos_criados > 0:
        msg += u"✔ Grupos criados: {}\n".format(grupos_criados)
    forms.alert(msg, title="Madeiramento de Telhado")


# =============================================================================
# COMANDO 2: MADEIRAMENTO VIA VIDRAÇA INCLINADA (SLOPED GLAZING)
# =============================================================================
def obter_tipo_base_vidraca():
    """Localiza um RoofType da família de sistema Vidraça Inclinada (Sloped Glazing)."""
    fallback_rt = None
    for rt in FilteredElementCollector(doc).OfClass(RoofType).ToElements():
        try:
            # Apenas tipos de Vidraça Inclinada possuem o parâmetro SPACING_LAYOUT_1 ativo e sem CompoundStructure
            p_lay = rt.get_Parameter(BuiltInParameter.SPACING_LAYOUT_1)
            if p_lay is not None and rt.GetCompoundStructure() is None:
                nome = _get_type_name_str(rt)
                if not nome.startswith(u"AutoBIM -"):
                    return rt
                if fallback_rt is None:
                    fallback_rt = rt
        except Exception:
            pass
    return fallback_rt


def _get_param_bip_name(param):
    """Retorna o nome do BuiltInParameter de um parâmetro, quando disponível."""
    try:
        defn = param.Definition
        if defn is not None and hasattr(defn, "BuiltInParameter"):
            return str(defn.BuiltInParameter)
    except Exception:
        pass
    return u""


def _set_param_value(param, value):
    """Define o valor de um parâmetro editável respeitando seu StorageType."""
    if param is None or param.IsReadOnly:
        return False
    try:
        st = param.StorageType
        if st == StorageType.Integer:
            return bool(param.Set(int(value)))
        if st == StorageType.Double:
            return bool(param.Set(float(value)))
        if st == StorageType.ElementId:
            return bool(param.Set(value if value is not None else ElementId.InvalidElementId))
        if st == StorageType.String:
            try:
                return bool(param.Set(unicode(value)))
            except NameError:
                return bool(param.Set(str(value)))
    except Exception:
        pass
    return False


def _apply_element_param(elem, bip_names, fallback_names, value):
    """Aplica um valor procurando por BuiltInParameter e depois por nome/BuiltInParameter em elem.Parameters."""
    if elem is None:
        return False
    applied = False
    bip_set = set(bip_names or ())
    name_set = set(n.lower() for n in (fallback_names or ()))

    for bip_name in (bip_names or ()):
        try:
            if hasattr(BuiltInParameter, bip_name):
                p = elem.get_Parameter(getattr(BuiltInParameter, bip_name))
                if _set_param_value(p, value):
                    applied = True
                    break
        except Exception:
            pass

    if applied:
        return True

    try:
        for p in elem.Parameters:
            bip_str = _get_param_bip_name(p)
            p_name = u""
            try:
                if p.Definition is not None and p.Definition.Name:
                    p_name = p.Definition.Name.strip().lower()
            except Exception:
                pass
            if (bip_str in bip_set) or (p_name in name_set):
                if _set_param_value(p, value):
                    applied = True
                    break
    except Exception:
        pass
    return applied


def _has_editable_bip(elem, bip_names):
    """Verifica se o elemento possui algum BuiltInParameter editável na lista fornecida."""
    bip_set = set(bip_names)
    for bip_name in bip_names:
        try:
            if hasattr(BuiltInParameter, bip_name):
                p = elem.get_Parameter(getattr(BuiltInParameter, bip_name))
                if p is not None and not p.IsReadOnly:
                    return True
        except Exception:
            pass
    try:
        for p in elem.Parameters:
            if not p.IsReadOnly and _get_param_bip_name(p) in bip_set:
                return True
    except Exception:
        pass
    return False


def _get_type_name_str(elem_type):
    """Obtém o nome do tipo de forma segura em qualquer idioma."""
    try:
        p_n = elem_type.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME)
        if p_n and p_n.AsString():
            return p_n.AsString()
    except Exception:
        pass
    try:
        return elem_type.Name
    except Exception:
        return u""


def obter_ou_criar_mullion_madeira(nome_tipo, largura_ft, espessura_ft, mat_id):
    """Obtém ou duplica um MullionType retangular e sempre atualiza suas dimensões b x h."""
    mullions = list(FilteredElementCollector(doc).OfClass(MullionType).ToElements())
    if not mullions:
        return None

    alvo_mt = None
    for mt in mullions:
        if _get_type_name_str(mt) == nome_tipo:
            alvo_mt = mt
            break

    if alvo_mt is None:
        base_mt = None
        rect_bips = ("RECT_MULLION_THICK", "RECT_MULLION_WIDTH1", "RECT_MULLION_WIDTH2")
        for mt in mullions:
            if _has_editable_bip(mt, rect_bips):
                base_mt = mt
                break

        if base_mt is None:
            for mt in mullions:
                try:
                    fam_name = u""
                    p_fam = mt.get_Parameter(BuiltInParameter.ALL_MODEL_FAMILY_NAME)
                    if p_fam and p_fam.AsString():
                        fam_name = p_fam.AsString()
                    elif hasattr(mt, "FamilyName") and mt.FamilyName:
                        fam_name = mt.FamilyName
                    full_name = (fam_name + u" " + _get_type_name_str(mt)).lower()
                    if ("retangular" in full_name) or ("rectangular" in full_name):
                        base_mt = mt
                        break
                except Exception:
                    pass

        if base_mt is None:
            base_mt = mullions[0]

        try:
            alvo_mt = base_mt.Duplicate(nome_tipo)
        except Exception:
            alvo_mt = base_mt

    _apply_element_param(
        alvo_mt,
        ("RECT_MULLION_THICK",),
        (u"espessura", u"thickness"),
        espessura_ft
    )
    _apply_element_param(
        alvo_mt,
        ("RECT_MULLION_WIDTH1",),
        (u"largura no lado 1", u"width on side 1"),
        largura_ft * 0.5
    )
    _apply_element_param(
        alvo_mt,
        ("RECT_MULLION_WIDTH2",),
        (u"largura no lado 2", u"width on side 2"),
        largura_ft * 0.5
    )
    if mat_id and mat_id != ElementId.InvalidElementId:
        _apply_element_param(
            alvo_mt,
            ("MULLION_MATERIAL", "MATERIAL_ID_PARAM"),
            (u"material",),
            mat_id
        )

    return alvo_mt


class VidracaFailurePreprocessor(IFailuresPreprocessor):
    """Suprime avisos da Vidraça Inclinada preservando os montantes/terças criados."""
    def PreprocessFailures(self, failuresAccessor):
        resolved_any = False
        try:
            msgs = failuresAccessor.GetFailureMessages()
            if not msgs or len(msgs) == 0:
                return FailureProcessingResult.Continue
            for fma in list(msgs):
                try:
                    sev = fma.GetSeverity()
                    if sev == FailureSeverity.Warning:
                        failuresAccessor.DeleteWarning(fma)
                    elif sev == FailureSeverity.Error and fma.HasResolutionOfType(FailureResolutionType.DeleteElements):
                        fma.SetCurrentResolutionType(FailureResolutionType.DeleteElements)
                        failuresAccessor.ResolveFailure(fma)
                        resolved_any = True
                except Exception:
                    pass
        except Exception:
            pass
        if resolved_any:
            return FailureProcessingResult.ProceedWithCommit
        return FailureProcessingResult.Continue


def obter_ou_criar_tipo_vidraca(base_rt, nome_tipo, usar_eixo_1, layout_code, espacamento_ft, mullion_id, incluir_bordas=True):
    """Obtém ou duplica um RoofType de Vidraça Inclinada e aplica todas as regras de grid e montantes."""
    novo_rt = None
    for rt in FilteredElementCollector(doc).OfClass(RoofType).ToElements():
        if _get_type_name_str(rt) == nome_tipo:
            novo_rt = rt
            break

    if novo_rt is None:
        try:
            novo_rt = base_rt.Duplicate(nome_tipo)
        except Exception:
            novo_rt = base_rt

    m_id = mullion_id if mullion_id else ElementId.InvalidElementId
    inv  = ElementId.InvalidElementId

    layout_eixo1 = ("SPACING_LAYOUT_1", "SPACING_LAYOUT_VERT")
    length_eixo1 = ("SPACING_LENGTH_1", "SPACING_LENGTH_VERT")
    layout_eixo2 = ("SPACING_LAYOUT_2", "SPACING_LAYOUT_HORIZ")
    length_eixo2 = ("SPACING_LENGTH_2", "SPACING_LENGTH_HORIZ")

    mullion_eixo1 = (
        ("AUTO_MULLION_INTERIOR_GRID1", "AUTO_MULLION_INTERIOR_VERT"),
        ("AUTO_MULLION_BORDER1_GRID1", "AUTO_MULLION_BORDER1_VERT"),
        ("AUTO_MULLION_BORDER2_GRID1", "AUTO_MULLION_BORDER2_VERT"),
    )
    mullion_eixo2 = (
        ("AUTO_MULLION_INTERIOR_GRID2", "AUTO_MULLION_INTERIOR_HORIZ"),
        ("AUTO_MULLION_BORDER1_GRID2", "AUTO_MULLION_BORDER1_HORIZ"),
        ("AUTO_MULLION_BORDER2_GRID2", "AUTO_MULLION_BORDER2_HORIZ"),
    )

    if usar_eixo_1:
        lay_ativo, len_ativo, mul_ativo = layout_eixo1, length_eixo1, mullion_eixo1
        lay_inativo, mul_inativo = layout_eixo2, mullion_eixo2
    else:
        lay_ativo, len_ativo, mul_ativo = layout_eixo2, length_eixo2, mullion_eixo2
        lay_inativo, mul_inativo = layout_eixo1, mullion_eixo1

    # 1) Definir Layouts primeiro para destravar o parâmetro de Espaçamento
    _apply_element_param(novo_rt, lay_ativo, (), layout_code)
    _apply_element_param(novo_rt, lay_inativo, (), 0)
    try:
        doc.Regenerate()
    except Exception:
        pass

    # 2) Definir Espaçamento do eixo ativo (quando houver layout automático)
    if layout_code != 0:
        _apply_element_param(novo_rt, len_ativo, (), espacamento_ft)

    # 3) Definir Montantes (Interior, Borda 1, Borda 2)
    if incluir_bordas:
        for slot_bips in mul_ativo:
            _apply_element_param(novo_rt, slot_bips, (), m_id)
        for slot_bips in mul_inativo:
            _apply_element_param(novo_rt, slot_bips, (), inv)
    else:
        # Apenas montantes de interior habilitados (sem montantes nas bordas do beiral/cumeeira)
        _apply_element_param(novo_rt, mul_ativo[0], (), m_id)
        _apply_element_param(novo_rt, mul_ativo[1], (), inv)
        _apply_element_param(novo_rt, mul_ativo[2], (), inv)
        _apply_element_param(novo_rt, mul_inativo[0], (), m_id)
        _apply_element_param(novo_rt, mul_inativo[1], (), inv)
        _apply_element_param(novo_rt, mul_inativo[2], (), inv)

    return novo_rt


def _obter_centroide_curtain_grid(cg):
    """Obtém o centroide 3D aproximado de um CurtainGrid a partir de suas células ou painéis."""
    pts = []
    try:
        cells = cg.GetCurtainCells()
        if cells:
            for cell in cells:
                try:
                    loops = cell.CurveLoops
                    if loops:
                        for ca in loops:
                            for c in ca:
                                pts.append(c.GetEndPoint(0))
                except Exception:
                    pass
    except Exception:
        pass

    if not pts:
        try:
            for pid in cg.GetPanelIds():
                pan = doc.GetElement(pid)
                if pan is not None:
                    bb = pan.get_BoundingBox(None)
                    if bb is not None:
                        pts.append((bb.Min + bb.Max) * 0.5)
        except Exception:
            pass

    if not pts:
        return None
    n = float(len(pts))
    return XYZ(
        sum(p.X for p in pts) / n,
        sum(p.Y for p in pts) / n,
        sum(p.Z for p in pts) / n
    )


def _plano_curtain_grid(cg, n_hint=None):
    """Extrai (ponto_origem, normal) do plano real das células de um CurtainGrid."""
    pts = []
    try:
        cells = cg.GetCurtainCells()
        if cells:
            for cell in cells:
                loops = None
                try:
                    loops = cell.CurveLoops
                except Exception:
                    loops = None
                if not loops:
                    try:
                        loops = cell.PlanarizedCurveLoops
                    except Exception:
                        loops = None
                if not loops:
                    continue
                for ca in loops:
                    for c in ca:
                        pts.append(c.GetEndPoint(0))
                        try:
                            pts.append(c.Evaluate(0.5, True))
                        except Exception:
                            pass
    except Exception:
        pass

    if len(pts) < 3:
        return None

    n_pts = float(len(pts))
    p0 = XYZ(
        sum(p.X for p in pts) / n_pts,
        sum(p.Y for p in pts) / n_pts,
        sum(p.Z for p in pts) / n_pts
    )
    p_far1 = max(pts, key=lambda p: p.DistanceTo(p0))
    v1 = p_far1 - p0
    if v1.GetLength() < 1e-6:
        return None

    best_cross = None
    best_len = 0.0
    for p in pts:
        cr = v1.CrossProduct(p - p0)
        clen = cr.GetLength()
        if clen > best_len:
            best_len = clen
            best_cross = cr
    if best_cross is None or best_len < 1e-6:
        return None

    n = best_cross.Normalize()
    if n_hint is not None and n.DotProduct(n_hint) < 0:
        n = -n
    return (p0, n)


def _projetar_no_plano_z(pt, p0, n):
    """Projeta pt sobre o plano (p0, n) preservando XY quando possível."""
    if abs(n.Z) > 0.05:
        z_proj = p0.Z - (n.X * (pt.X - p0.X) + n.Y * (pt.Y - p0.Y)) / n.Z
        return XYZ(pt.X, pt.Y, z_proj)
    dist = (pt - p0).DotProduct(n)
    return pt - n * dist


def _extrair_loops_3d_cg(cg):
    """Extrai os polígonos 3D das células reais de um CurtainGrid."""
    loops_3d = []
    try:
        cells = cg.GetCurtainCells()
        if not cells:
            return loops_3d
        for cell in cells:
            loops = None
            try:
                loops = cell.CurveLoops
            except Exception:
                loops = None
            if not loops:
                try:
                    loops = cell.PlanarizedCurveLoops
                except Exception:
                    loops = None
            if not loops:
                continue
            for ca in loops:
                poly = []
                for c in ca:
                    try:
                        tess = c.Tessellate()
                        pts_c = list(tess)[:-1] if (tess and len(tess) > 1) else [c.GetEndPoint(0)]
                    except Exception:
                        pts_c = [c.GetEndPoint(0)]
                    poly.extend(pts_c)
                if len(poly) >= 3:
                    loops_3d.append(poly)
    except Exception:
        pass
    return loops_3d


def _curva_gridline_eh_horizontal(gl, u_horiz, v_slope_xy):
    """Verifica pelas curvas 3D da CurtainGridLine (FullCurve/AllSegmentCurves) se ela é horizontal."""
    if gl is None:
        return None
    curvas = []
    try:
        fc = gl.FullCurve
        if fc is not None:
            curvas.append(fc)
    except Exception:
        pass
    try:
        segs = gl.AllSegmentCurves
        if segs:
            for s in segs:
                if s is not None:
                    curvas.append(s)
    except Exception:
        pass

    melhor_c = None
    melhor_len = 0.0
    for c in curvas:
        try:
            ln = c.Length
            if ln > melhor_len:
                melhor_len = ln
                melhor_c = c
        except Exception:
            pass
    if melhor_c is None or melhor_len < 1e-4:
        return None

    try:
        dv = melhor_c.GetEndPoint(1) - melhor_c.GetEndPoint(0)
        dlen = dv.GetLength()
        if dlen < 1e-4:
            return None
        du = dv.Normalize()
        alinh_u = abs(du.DotProduct(u_horiz))
        alinh_v = abs(du.DotProduct(v_slope_xy))
        return (abs(du.Z) < 0.05) and (alinh_u >= alinh_v)
    except Exception:
        return None


def _detectar_eixo_horizontal_cg(cg, agua_alvo, plano_cg, cg_loops_uv, dz_base):
    """Testa no centro geométrico real do CurtainGrid qual eixo (is_u=False/True) gera linhas horizontais."""
    u_horiz = agua_alvo["u_horiz"]
    v_slope_xy = XYZ(agua_alvo["v_slope"].X, agua_alvo["v_slope"].Y, 0.0)
    if v_slope_xy.GetLength() > 1e-6:
        v_slope_xy = v_slope_xy.Normalize()
    else:
        v_slope_xy = XYZ(-u_horiz.Y, u_horiz.X, 0.0).Normalize()

    all_v = [uv[1] for poly in cg_loops_uv for uv in poly] if cg_loops_uv else []
    if not all_v:
        return False
    v_mid = 0.5 * (min(all_v) + max(all_v))
    ivs = recortar_linha_constante_v(cg_loops_uv, v_mid)
    if ivs:
        iv = max(ivs, key=lambda x: x[1] - x[0])
        u_mid = 0.5 * (iv[0] + iv[1])
    else:
        all_u = [uv[0] for poly in cg_loops_uv for uv in poly]
        u_mid = 0.5 * (min(all_u) + max(all_u))

    pt_probe = ponto_3d_agua(agua_alvo, u_mid, v_mid, 0.0) + XYZ(0, 0, dz_base)
    if plano_cg is not None:
        pt_probe = _projetar_no_plano_z(pt_probe, plano_cg[0], plano_cg[1])

    for cand_u in (False, True):
        gl_p = None
        eh_horiz = None
        try:
            gl_p = cg.AddGridLine(cand_u, pt_probe, False)
            if gl_p is not None:
                doc.Regenerate()
                eh_horiz = _curva_gridline_eh_horizontal(gl_p, u_horiz, v_slope_xy)
        except Exception:
            gl_p = None
        finally:
            if gl_p is not None:
                try:
                    doc.Delete(gl_p.Id)
                    doc.Regenerate()
                except Exception:
                    pass
        if eh_horiz is True:
            return cand_u
        if eh_horiz is False:
            return (not cand_u)
    return False


def _posicionar_tercas_vidraca(
    novo_el, aguas, dz_base, off_cam, esp_base,
    terca_b_ft, terca_h_ft, terca_esp_ft, terca_recuo_ft, mt_terca
):
    """
    Posiciona as linhas de eixo (CurtainGridLines) das Terças em cada água da Vidraça Inclinada:
    - Usa o contorno real das células do CurtainGrid para medir o Recuo do Beiral (terças inferiores)
      e garantir pontos 100% internos à água.
    - Detecta de forma determinística o eixo horizontal (is_u) no centro da água antes de lançar as terças.
    """
    try:
        cgrids = list(novo_el.CurtainGrids) if novo_el.CurtainGrids else []
    except Exception:
        cgrids = []
    if not cgrids or not aguas:
        return

    # Calcular centroides 3D das águas deslocadas por dz_base
    centroides_aguas = []
    for idx_a, agua in enumerate(aguas):
        c_agua = ponto_3d_agua(
            agua,
            0.5 * (agua["u_min"] + agua["u_max"]),
            0.5 * (agua["v_min"] + agua["v_max"]),
            0.0
        ) + XYZ(0, 0, dz_base)
        centroides_aguas.append((idx_a, c_agua, agua))

    usadas = set()
    for cg in cgrids:
        # Remove eventuais montantes residuais pré-existentes nas bordas antes de lançar as terças
        try:
            for mid in list(cg.GetMullionIds()):
                try:
                    m_el = doc.GetElement(mid)
                    if m_el is not None and m_el.Pinned:
                        m_el.Pinned = False
                    doc.Delete(mid)
                except Exception:
                    pass
        except Exception:
            pass

        loops_3d = _extrair_loops_3d_cg(cg)
        plano_cg = _plano_curtain_grid(cg, None)
        c_cg = _obter_centroide_curtain_grid(cg)
        if c_cg is None and loops_3d:
            pts_flat = [p for poly in loops_3d for p in poly]
            n_f = float(len(pts_flat))
            c_cg = XYZ(sum(p.X for p in pts_flat) / n_f, sum(p.Y for p in pts_flat) / n_f, sum(p.Z for p in pts_flat) / n_f)

        agua_alvo = None
        if c_cg is not None:
            melhor_score = 1e12
            melhor_idx = -1
            for usar_apenas_livres in (True, False):
                for idx_a, c_agua, agua in centroides_aguas:
                    if usar_apenas_livres and (idx_a in usadas):
                        continue
                    pen_normal = 0.0
                    if plano_cg is not None:
                        n_cg = plano_cg[1]
                        if n_cg.Z < 0:
                            n_cg = -n_cg
                        dot_n = n_cg.DotProduct(agua["n_up"])
                        if dot_n < 0.65:
                            pen_normal = 1e6 * (1.0 - dot_n)
                    score = c_cg.DistanceTo(c_agua) + pen_normal
                    if score < melhor_score:
                        melhor_score = score
                        melhor_idx = idx_a
                        agua_alvo = agua
                if agua_alvo is not None:
                    break
            if melhor_idx >= 0:
                usadas.add(melhor_idx)
        if agua_alvo is None:
            agua_alvo = centroides_aguas[0][2]

        # Reprojetar as células reais do CurtainGrid no sistema UV da água alvo
        cg_loops_uv = []
        all_v_cg = []
        if loops_3d:
            orig_a = agua_alvo["origin"]
            u_h = agua_alvo["u_horiz"]
            v_s = agua_alvo["v_slope"]
            for poly3d in loops_3d:
                p_uv = []
                for p in poly3d:
                    dp = p - orig_a
                    uu = dp.DotProduct(u_h)
                    vv = dp.DotProduct(v_s)
                    p_uv.append((uu, vv))
                    all_v_cg.append(vv)
                if len(p_uv) >= 3:
                    cg_loops_uv.append(p_uv)

        if not cg_loops_uv or not all_v_cg:
            cg_loops_uv = agua_alvo["loops_uv"]
            v_cg_min = agua_alvo["v_min"]
            v_cg_max = agua_alvo["v_max"]
        else:
            v_cg_min = min(all_v_cg)
            v_cg_max = max(all_v_cg)

        if plano_cg is None:
            plano_cg = _plano_curtain_grid(cg, agua_alvo["n_up"])

        cos_a = max(0.2, min(1.0, abs(agua_alvo["n_up"].Z)))
        sin_a = math.sqrt(max(0.0, 1.0 - cos_a * cos_a))
        tan_a = sin_a / cos_a

        # Recuo do beiral inferior (v_cg_min) + meia largura da terça para recuo líquido fiel
        v_ini = (
            v_cg_min
            + (terca_recuo_ft / cos_a)
            + max(0.0, off_cam - esp_base) * tan_a
            + 0.5 * terca_b_ft
        )
        v_fim = (
            v_cg_max
            - 0.5 * terca_b_ft
            - 0.5 * terca_h_ft * tan_a
        )
        if v_ini >= v_fim - 0.05 * FT:
            v_ini = max(v_cg_min + 0.5 * terca_b_ft, v_fim - 0.1 * FT)

        vs_terca = gerar_posicoes_uniformes(v_ini, v_fim, terca_esp_ft, 0.0)
        tem_folga_v = (v_cg_max - v_cg_min) > 0.10 * FT

        is_u = _detectar_eixo_horizontal_cg(cg, agua_alvo, plano_cg, cg_loops_uv, dz_base)
        gls_criadas = []

        for vk in vs_terca:
            vk_alvo = vk
            if tem_folga_v:
                vk_alvo = max(v_cg_min + 0.04 * FT, min(v_cg_max - 0.04 * FT, vk))

            intervalos_u = recortar_linha_constante_v(cg_loops_uv, vk_alvo)
            if not intervalos_u and tem_folga_v:
                # Se vk_alvo raspou exatamente uma quina, recua levemente para dentro
                vk_try = vk_alvo + (0.03 * FT if vk_alvo < 0.5 * (v_cg_min + v_cg_max) else -0.03 * FT)
                intervalos_u = recortar_linha_constante_v(cg_loops_uv, vk_try)
                if intervalos_u:
                    vk_alvo = vk_try

            if intervalos_u:
                melhor_u = max(intervalos_u, key=lambda iv: iv[1] - iv[0])
                u_cands = [
                    melhor_u[0] + fr * (melhor_u[1] - melhor_u[0])
                    for fr in (0.5, 0.35, 0.65, 0.2, 0.8)
                ]
            else:
                u_cands = [0.5 * (agua_alvo["u_min"] + agua_alvo["u_max"])]

            gl = None
            for u_try in u_cands:
                pt_3d = ponto_3d_agua(agua_alvo, u_try, vk_alvo, 0.0) + XYZ(0, 0, dz_base)
                if plano_cg is not None:
                    pt_3d = _projetar_no_plano_z(pt_3d, plano_cg[0], plano_cg[1])
                try:
                    gl = cg.AddGridLine(is_u, pt_3d, False)
                except Exception:
                    gl = None
                if gl is not None:
                    break

            if gl is not None:
                gls_criadas.append(gl)

        if gls_criadas and mt_terca is not None:
            try:
                doc.Regenerate()
            except Exception:
                pass
            for gl in gls_criadas:
                try:
                    tem_mullion = (gl.ExistingMullionIds is not None and gl.ExistingMullionIds.Count > 0)
                except Exception:
                    tem_mullion = False
                if not tem_mullion:
                    try:
                        for seg in gl.AllSegmentCurves:
                            gl.AddMullions(seg, mt_terca, False)
                    except Exception:
                        pass


def run_vidraca_inclinada():
    global doc, uidoc, view
    uidoc = __revit__.ActiveUIDocument
    doc   = uidoc.Document
    view  = doc.ActiveView

    base_rt = obter_tipo_base_vidraca()
    if base_rt is None:
        forms.alert(
            "Não foi encontrado nenhum tipo base da família 'Vidraça Inclinada' (Sloped Glazing) no projeto.",
            title="Madeiramento: Vidraça Inclinada",
            exitscript=True
        )

    prefs = carregar_preferencias()
    win = TramaWindow(prefs)
    win.Title = u"Madeiramento: Vidraça Inclinada (Sloped Glazing)"
    win.ShowDialog()
    if not win.confirmado:
        script.exit()

    op = win.resultado
    salvar_preferencias(op)

    roofs = selecionar_telhados()

    ripa_b_ft   = op["ripa_b"] / 30.48
    ripa_h_ft   = op["ripa_h"] / 30.48
    ripa_esp_ft = op["ripa_esp"] / 30.48

    caibro_b_ft   = op["caibro_b"] / 30.48
    caibro_h_ft   = op["caibro_h"] / 30.48
    caibro_esp_ft = op["caibro_esp"] / 30.48

    terca_b_ft     = op["terca_b"] / 30.48
    terca_h_ft     = op["terca_h"] / 30.48
    terca_esp_ft   = op["terca_esp"] / 30.48
    terca_recuo_ft = op.get("terca_recuo", 0.0) / 30.48

    vidracas_criadas = []

    with TransactionGroup(doc, "Madeiramento: Vidraça Inclinada") as tg:
        tg.Start()
        with Transaction(doc, "Madeiramento: Vidraça Inclinada") as t:
            t.Start()
            opts = t.GetFailureHandlingOptions()
            opts.SetFailuresPreprocessor(VidracaFailurePreprocessor())
            t.SetFailureHandlingOptions(opts)
            mat_id = obter_ou_criar_material_madeira()

            # Criar/obter tipos de montante e tipos de Vidraça Inclinada para cada camada marcada
            camadas_config = []
            off_acumulado = 0.0
            mt_terca = None

            if op["ripas"]:
                mt_ripa = obter_ou_criar_mullion_madeira(
                    u"AutoBIM - Ripa {:.0f}x{:.0f}cm".format(op["ripa_b"], op["ripa_h"]),
                    ripa_b_ft, ripa_h_ft, mat_id
                )
                rt_ripa = obter_ou_criar_tipo_vidraca(
                    base_rt,
                    u"AutoBIM - Ripas ({:.0f}x{:.0f}cm c/{:.0f}cm)".format(op["ripa_b"], op["ripa_h"], op["ripa_esp"]),
                    usar_eixo_1=False, layout_code=1, espacamento_ft=ripa_esp_ft,
                    mullion_id=(mt_ripa.Id if mt_ripa else None), incluir_bordas=True
                )
                camadas_config.append(("Ripas", rt_ripa, off_acumulado + ripa_h_ft * 0.5))
                off_acumulado += ripa_h_ft

            if op["caibros"]:
                mt_caibro = obter_ou_criar_mullion_madeira(
                    u"AutoBIM - Caibro {:.0f}x{:.0f}cm".format(op["caibro_b"], op["caibro_h"]),
                    caibro_b_ft, caibro_h_ft, mat_id
                )
                rt_caibro = obter_ou_criar_tipo_vidraca(
                    base_rt,
                    u"AutoBIM - Caibros ({:.0f}x{:.0f}cm c/{:.0f}cm)".format(op["caibro_b"], op["caibro_h"], op["caibro_esp"]),
                    usar_eixo_1=True, layout_code=1, espacamento_ft=caibro_esp_ft,
                    mullion_id=(mt_caibro.Id if mt_caibro else None), incluir_bordas=True
                )
                camadas_config.append(("Caibros", rt_caibro, off_acumulado + caibro_h_ft * 0.5))
                off_acumulado += caibro_h_ft

            if op["tercas"]:
                mt_terca = obter_ou_criar_mullion_madeira(
                    u"AutoBIM - Terça {:.0f}x{:.0f}cm".format(op["terca_b"], op["terca_h"]),
                    terca_b_ft, terca_h_ft, mat_id
                )
                rt_terca = obter_ou_criar_tipo_vidraca(
                    base_rt,
                    u"AutoBIM - Terças ({:.0f}x{:.0f}cm máx {:.0f}cm)".format(op["terca_b"], op["terca_h"], op["terca_esp"]),
                    usar_eixo_1=False, layout_code=0, espacamento_ft=terca_esp_ft,
                    mullion_id=(mt_terca.Id if mt_terca else None), incluir_bordas=False
                )
                camadas_config.append(("Terças", rt_terca, off_acumulado + terca_h_ft * 0.5))
                off_acumulado += terca_h_ft

            for roof in roofs:
                # No Revit, Telhado Básico cresce para cima a partir da base (0.0 = face inferior, +esp = face superior)
                esp_base = 0.0
                if op["face_ref_idx"] == 1:
                    try:
                        rt_orig = doc.GetElement(roof.GetTypeId())
                        cs = rt_orig.GetCompoundStructure()
                        if cs is not None:
                            esp_base = cs.GetWidth()
                    except Exception:
                        pass

                aguas = extrair_faces_aguas(roof, usar_face_inferior=True)
                if not aguas:
                    aguas = extrair_faces_aguas(roof, usar_face_inferior=False)
                if aguas:
                    cos_theta = max(0.2, min(1.0, abs(aguas[0]["n_up"].Z)))
                else:
                    cos_theta = 0.95

                for nome_cam, rt_alvo, off_cam in camadas_config:
                    dz_base = (esp_base - off_cam) / cos_theta
                    try:
                        copia_ids = ElementTransformUtils.CopyElement(doc, roof.Id, XYZ(0, 0, dz_base))
                        if copia_ids and copia_ids.Count > 0:
                            novo_el = doc.GetElement(copia_ids[0])
                            novo_el.ChangeTypeId(rt_alvo.Id)
                            if nome_cam == "Terças":
                                doc.Regenerate()
                                _posicionar_tercas_vidraca(
                                    novo_el, aguas, dz_base, off_cam, esp_base,
                                    terca_b_ft, terca_h_ft, terca_esp_ft, terca_recuo_ft, mt_terca
                                )
                            vidracas_criadas.append(novo_el.Id)
                    except Exception:
                        pass

            if len(vidracas_criadas) > 0:
                doc.Regenerate()

            t.Commit()

        if op["trama_grp_idx"] in (0, 1) and len(vidracas_criadas) > 0:
            agrupar_lote_em_transacao([(vidracas_criadas, u"Madeiramento - Vidraças Inclinadas")], "Agrupar Vidraça Inclinada")
        tg.Assimilate()

    forms.alert(
        u"Vidraça Inclinada concluída!\n\n✔ Telhados de Vidraça Inclinada gerados: {}".format(len(vidracas_criadas)),
        title="Madeiramento: Vidraça Inclinada"
    )


# =============================================================================
# COMANDO 3: TESOURAS DE TELHADO (1 OU 2 ÁGUAS - FAMÍLIA ÚNICA .RFA)
# =============================================================================
XAML_TESOURAS = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Madeiramento: Tesouras" Width="490" SizeToContent="Height"
        WindowStartupLocation="CenterScreen" ResizeMode="NoResize"
        Background="#F3F3F3" FontFamily="Segoe UI" FontSize="12">
    <StackPanel Margin="18">
        <Border Background="#E3EEF9" BorderBrush="#0078D4" BorderThickness="0,0,0,2"
                Padding="10,8" Margin="0,0,0,12">
            <TextBlock TextWrapping="Wrap" FontWeight="SemiBold" Foreground="#1F3A57"
                       Text="Gera Tesouras (2 águas) ou Meias-Tesouras (1 água) como família estrutural (.rfa) com entalhes e recortes nos encaixes."/>
        </Border>

        <TextBlock Text="Tipo de Tesoura (Treliça):" FontWeight="SemiBold" FontSize="11" Margin="0,0,0,3"/>
        <ComboBox x:Name="cb_tipo_tes" Height="26" Margin="0,0,0,10"/>

        <Grid Margin="0,0,0,10">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="12"/>
                <ColumnDefinition Width="*"/>
            </Grid.ColumnDefinitions>
            <StackPanel Grid.Column="0">
                <TextBlock Text="Espaçamento entre Tesouras (cm)" FontWeight="SemiBold" FontSize="11"/>
                <TextBox x:Name="tb_esp_linhas" Height="26" Margin="0,3,0,0" Text="250" VerticalContentAlignment="Center"/>
            </StackPanel>
            <StackPanel Grid.Column="2">
                <TextBlock Text="Direção das Tesouras" FontWeight="SemiBold" FontSize="11"/>
                <ComboBox x:Name="cb_direcao" Height="26" Margin="0,3,0,0"/>
            </StackPanel>
        </Grid>

        <Grid Margin="0,0,0,10">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="12"/>
                <ColumnDefinition Width="*"/>
            </Grid.ColumnDefinitions>
            <StackPanel Grid.Column="0">
                <TextBlock Text="Desconto da Trama Acima (cm)" FontWeight="SemiBold" FontSize="11"/>
                <TextBox x:Name="tb_desconto_trama" Height="26" Margin="0,3,0,0" Text="19" VerticalContentAlignment="Center"/>
                <CheckBox x:Name="ck_auto_trama" Content="Auto pela Trama (logo abaixo das Terças)" IsChecked="True" Margin="0,4,0,0" FontSize="10"/>
                <TextBlock x:Name="tb_trama_info" Text="(Ripa 2 + Caibro 5 + Terça 12 = 19 cm)" FontSize="10" Foreground="#555555" Margin="0,2,0,0"/>
            </StackPanel>
            <StackPanel Grid.Column="2">
                <TextBlock Text="Avanço da Linha além do apoio (cm)" FontWeight="SemiBold" FontSize="11"/>
                <TextBox x:Name="tb_avanco_linha" Height="26" Margin="0,3,0,0" Text="10" VerticalContentAlignment="Center"/>
            </StackPanel>
        </Grid>

        <StackPanel Margin="0,0,0,10">
            <TextBlock Text="Recuo do apoio (frechal) a partir da borda (cm)" FontWeight="SemiBold" FontSize="11"/>
            <TextBox x:Name="tb_recuo_apoio" Height="26" Margin="0,3,0,0" Text="60" VerticalContentAlignment="Center"/>
            <TextBlock Text="(Ex.: beiral de 60 cm → a Linha apoia na parede)" FontSize="10" Foreground="#555555" Margin="0,2,0,0"/>
        </StackPanel>

        <Border Background="White" BorderBrush="#D0D7DE" BorderThickness="1" CornerRadius="4" Padding="10" Margin="0,0,0,10">
            <StackPanel>
                <TextBlock Text="Bitola das Peças (Empena, Linha, Pendural, Montantes e Escoras):" FontWeight="SemiBold" Margin="0,0,0,6"/>
                <Grid>
                    <Grid.ColumnDefinitions>
                        <ColumnDefinition Width="*"/>
                        <ColumnDefinition Width="12"/>
                        <ColumnDefinition Width="*"/>
                    </Grid.ColumnDefinitions>
                    <StackPanel Grid.Column="0">
                        <TextBlock Text="Espessura b (cm)" FontSize="11"/>
                        <TextBox x:Name="tb_peca_b" Height="24" Text="6" VerticalContentAlignment="Center"/>
                    </StackPanel>
                    <StackPanel Grid.Column="2">
                        <TextBlock Text="Altura da Seção h (cm)" FontSize="11"/>
                        <TextBox x:Name="tb_peca_h" Height="24" Text="12" VerticalContentAlignment="Center"/>
                    </StackPanel>
                </Grid>
            </StackPanel>
        </Border>

        <Grid Margin="0,0,0,8">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="12"/>
                <ColumnDefinition Width="*"/>
            </Grid.ColumnDefinitions>
            <StackPanel Grid.Column="0">
                <CheckBox x:Name="ck_frechais" Content="Incluir Frechais sob a Linha" IsChecked="True" Margin="0,0,0,4"/>
                <CheckBox x:Name="ck_estribo" Content="Incluir Ferragem / Estribo" IsChecked="True"/>
            </StackPanel>
            <StackPanel Grid.Column="2">
                <CheckBox x:Name="ck_entalhes" Content="Entalhes de carpintaria (encaixes)" IsChecked="True"/>
            </StackPanel>
        </Grid>

        <TextBlock Text="Agrupamento ao concluir:" FontWeight="SemiBold" Margin="0,0,0,3"/>
        <ComboBox x:Name="cb_agrupar_tes" Height="26" Margin="0,0,0,12"/>

        <StackPanel Orientation="Horizontal" HorizontalAlignment="Right" Margin="0,4,0,0">
            <Button x:Name="btn_cancel" Content="Cancelar" Width="90" Height="30" Margin="0,0,8,0"/>
            <Button x:Name="btn_ok" Content="Gerar Estrutura" Width="165" Height="30"
                    IsDefault="True" Background="#0078D4" Foreground="White"/>
        </StackPanel>
    </StackPanel>
</Window>
"""


class TesourasWindow(forms.WPFWindow):
    def __init__(self, prefs):
        forms.WPFWindow.__init__(self, XAML_TESOURAS, literal_string=True)
        self.confirmado = False
        self.resultado = {}

        self._cb_tipo  = self.FindName("cb_tipo_tes")
        self._tb_lin   = self.FindName("tb_esp_linhas")
        self._tb_desc  = self.FindName("tb_desconto_trama")
        self._ck_auto_t = self.FindName("ck_auto_trama")
        self._tb_t_info = self.FindName("tb_trama_info")
        self._cb_dir   = self.FindName("cb_direcao")
        self._tb_rec   = self.FindName("tb_recuo_apoio")
        self._tb_avc   = self.FindName("tb_avanco_linha")
        self._tb_b     = self.FindName("tb_peca_b")
        self._tb_h     = self.FindName("tb_peca_h")
        self._ck_frech = self.FindName("ck_frechais")
        self._ck_estr  = self.FindName("ck_estribo")
        self._ck_ent   = self.FindName("ck_entalhes")
        self._cb_grp   = self.FindName("cb_agrupar_tes")

        self._cb_tipo.Items.Add(u"Estribada rainha (Tradicional: Pendural + Escoras)")
        self._cb_tipo.Items.Add(u"Estribada simples (Pendural central)")
        self._cb_tipo.Items.Add(u"Howe dupla (Pendural + Montantes + Escoras)")
        self._cb_tipo.Items.Add(u"Fink (Treliça em W)")
        self._cb_tipo.SelectedIndex = min(3, max(0, int(prefs.get("tes_tipo_idx", 0))))

        self._cb_dir.Items.Add(u"Automática (Transversal à maior cumeeira/água)")
        self._cb_dir.Items.Add(u"Vão ao longo do Eixo X (Tesouras paralelas a X)")
        self._cb_dir.Items.Add(u"Vão ao longo do Eixo Y (Tesouras paralelas a Y)")
        self._cb_dir.SelectedIndex = int(prefs.get("tes_dir_idx", 0))

        self._cb_grp.Items.Add(u"Não agrupar (1 elemento por Tesoura)")
        self._cb_grp.Items.Add(u"Agrupar todas as Tesouras em 1 Grupo Geral")
        self._cb_grp.SelectedIndex = min(1, int(prefs.get("tes_grp_idx", 0)))

        self._tb_lin.Text   = str(prefs.get("tes_esp_linhas", "250"))

        partes_trama = []
        soma_trama = 0.0
        if prefs.get("ripas", True):
            rh = float(prefs.get("ripa_h", 2.0))
            soma_trama += rh
            partes_trama.append(u"Ripa {:g}".format(rh))
        if prefs.get("caibros", True):
            ch = float(prefs.get("caibro_h", 5.0))
            soma_trama += ch
            partes_trama.append(u"Caibro {:g}".format(ch))
        if prefs.get("tercas", True):
            th = float(prefs.get("terca_h", 12.0))
            soma_trama += th
            partes_trama.append(u"Terça {:g}".format(th))
        self._soma_trama = round(soma_trama, 2)
        if partes_trama:
            self._tb_t_info.Text = u"({} = {:g} cm)".format(u" + ".join(partes_trama), self._soma_trama)
        else:
            self._tb_t_info.Text = u"(Nenhuma camada ativa na Trama)"

        self._ck_auto_t.IsChecked = bool(prefs.get("tes_auto_trama", True))
        if self._ck_auto_t.IsChecked:
            self._tb_desc.Text = u"{:g}".format(self._soma_trama)
            self._tb_desc.IsEnabled = False
        else:
            self._tb_desc.Text = str(prefs.get("tes_desconto", "19"))
            self._tb_desc.IsEnabled = True
        self._ck_auto_t.Checked   += self._atualizar_auto_trama
        self._ck_auto_t.Unchecked += self._atualizar_auto_trama

        self._tb_rec.Text   = str(prefs.get("tes_recuo", "60"))
        self._tb_avc.Text   = str(prefs.get("tes_avanco", "10"))
        self._tb_b.Text     = str(prefs.get("tes_b", "6"))
        self._tb_h.Text     = str(prefs.get("tes_h", "12"))
        self._ck_frech.IsChecked = bool(prefs.get("tes_frechais", True))
        self._ck_estr.IsChecked  = bool(prefs.get("tes_estribo", True))
        self._ck_ent.IsChecked   = bool(prefs.get("tes_entalhes", True))

        self.FindName("btn_ok").Click     += self.on_ok
        self.FindName("btn_cancel").Click += self.on_cancel

    def _atualizar_auto_trama(self, sender, args):
        if self._ck_auto_t.IsChecked:
            self._tb_desc.Text = u"{:g}".format(self._soma_trama)
            self._tb_desc.IsEnabled = False
        else:
            self._tb_desc.IsEnabled = True

    def on_cancel(self, sender, args):
        self.confirmado = False
        self.Close()

    def on_ok(self, sender, args):
        try:
            res = {
                "tes_tipo_idx":    max(0, self._cb_tipo.SelectedIndex),
                "tes_esp_linhas":  float(self._tb_lin.Text.replace(",", ".")),
                "tes_auto_trama":  bool(self._ck_auto_t.IsChecked),
                "tes_desconto":    float(self._tb_desc.Text.replace(",", ".")),
                "tes_recuo":       max(0.0, float(self._tb_rec.Text.replace(",", "."))),
                "tes_avanco":      max(0.0, float(self._tb_avc.Text.replace(",", "."))),
                "tes_dir_idx":     self._cb_dir.SelectedIndex,
                "tes_b":           float(self._tb_b.Text.replace(",", ".")),
                "tes_h":           float(self._tb_h.Text.replace(",", ".")),
                "tes_frechais":    bool(self._ck_frech.IsChecked),
                "tes_estribo":     bool(self._ck_estr.IsChecked),
                "tes_entalhes":    bool(self._ck_ent.IsChecked),
                "tes_grp_idx":     self._cb_grp.SelectedIndex,
            }
        except Exception:
            forms.alert("Informe apenas valores numéricos válidos.")
            return

        self.resultado = res
        self.confirmado = True
        self.Close()


def obter_z_agua_em_xy(agua, x, y, desconto_perp_ft):
    origin  = agua["origin"]
    u_horiz = agua["u_horiz"]
    v_slope = agua["v_slope"]

    dx = x - origin.X
    dy = y - origin.Y
    det = u_horiz.X * v_slope.Y - u_horiz.Y * v_slope.X
    if abs(det) < 1e-7:
        return None

    u = (dx * v_slope.Y - dy * v_slope.X) / det
    v = (u_horiz.X * dy - u_horiz.Y * dx) / det

    dentro = False
    for poly in agua["loops_uv"]:
        n = len(poly)
        j = n - 1
        for i in range(n):
            ui, vi = poly[i]
            uj, vj = poly[j]
            if ((vi > v) != (vj > v)) and (u < (uj - ui) * (v - vi) / ((vj - vi) + 1e-12) + ui):
                dentro = not dentro
            j = i
    if not dentro:
        return None

    pt_plano = origin + u_horiz * u + v_slope * v
    cos_incl = max(0.15, abs(agua["n_up"].Z))
    return pt_plano.Z - (desconto_perp_ft / cos_incl)


def _z_no_perfil(amostras, s_alvo, padrao):
    for i in range(len(amostras) - 1):
        s1, z1 = amostras[i]
        s2, z2 = amostras[i + 1]
        if s1 - 1e-5 <= s_alvo <= s2 + 1e-5:
            if abs(s2 - s1) < 1e-7:
                return z1
            return z1 + (s_alvo - s1) * (z2 - z1) / (s2 - s1)
    return padrao


# -----------------------------------------------------------------------------
# GERAÇÃO DA FAMÍLIA DA TESOURA (.rfa)
# -----------------------------------------------------------------------------
class _FamLoadOpts(IFamilyLoadOptions):
    def OnFamilyFound(self, familyInUse, overwriteParameterValues):
        overwriteParameterValues = True
        return True

    def OnSharedFamilyFound(self, sharedFamily, familyInUse, source, overwriteParameterValues):
        source = FamilySource.Family
        overwriteParameterValues = True
        return True


def _achar_template_generico(app, prefs=None):
    if prefs:
        salvo = prefs.get("tes_template_rft")
        if salvo and os.path.isfile(salvo):
            return salvo

    caminhos_diretos = [
        u"G:\\ProgramData\\Autodesk\\RVT 2026\\Family Templates\\Portuguese\\Modelo genérico métrico.rft",
        u"C:\\ProgramData\\Autodesk\\RVT 2026\\Family Templates\\Portuguese\\Modelo genérico métrico.rft",
    ]
    for direto in caminhos_diretos:
        if os.path.isfile(direto):
            try:
                salvar_preferencias({"tes_template_rft": direto})
            except Exception:
                pass
            return direto

    raizes = []
    try:
        fp = app.FamilyTemplatePath
        if fp and os.path.isdir(fp):
            raizes.append(fp)
    except Exception:
        pass

    # Fallback para instalações e backups em ProgramData (G: e C:)
    for autodesk_dir in (u"G:\\ProgramData\\Autodesk", u"C:\\ProgramData\\Autodesk"):
        if os.path.isdir(autodesk_dir):
            rvt_pastas = sorted(
                [d for d in os.listdir(autodesk_dir) if d.upper().startswith("RVT")],
                reverse=True
            )
            for d in rvt_pastas:
                ft_dir = os.path.join(autodesk_dir, d, "Family Templates")
                if os.path.isdir(ft_dir) and ft_dir not in raizes:
                    raizes.append(ft_dir)

    preferidos = (
        u"modelo genérico métrico",
        u"modelo generico metrico",
        u"metric generic model",
        u"generic model",
        u"modello generico metrico",
    )
    excluir = (
        u"baseado", u"based", u"line", u"linha",
        u"adaptive", u"adaptativ", u"pattern", u"padr",
        u"two level", u"dois", u"annotation", u"anotac"
    )

    candidatos = []
    for idx_raiz, base in enumerate(raizes):
        for raiz, _, arquivos in os.walk(base):
            for f in arquivos:
                nome = f.lower()
                if not nome.endswith(".rft"):
                    continue
                stem = nome[:-4]
                if stem in preferidos:
                    prio = preferidos.index(stem)
                    candidatos.append((idx_raiz, prio, len(stem), os.path.join(raiz, f)))
                elif (
                    (stem.startswith(u"modelo gen") or stem.startswith(u"metric generic model") or stem.startswith(u"generic model"))
                    and not any(ex in stem for ex in excluir)
                ):
                    candidatos.append((idx_raiz, 99, len(stem), os.path.join(raiz, f)))

    escolhido = None
    if candidatos:
        candidatos.sort(key=lambda item: (item[0], item[1], item[2]))
        escolhido = candidatos[0][3]
    else:
        init_dir = raizes[0] if raizes else None
        try:
            escolhido = forms.pick_file(
                file_ext="rft",
                init_dir=init_dir,
                title="Selecione o modelo de família 'Modelo genérico métrico' (.rft)"
            )
        except TypeError:
            escolhido = forms.pick_file(
                file_ext="rft",
                title="Selecione o modelo de família 'Modelo genérico métrico' (.rft)"
            )

    if escolhido and os.path.isfile(escolhido):
        try:
            salvar_preferencias({"tes_template_rft": escolhido})
        except Exception:
            pass
        return escolhido
    return None


def _mat_familia(fdoc, nome, rgb):
    for m in FilteredElementCollector(fdoc).OfClass(Material).ToElements():
        try:
            if m.Name == nome:
                return m.Id
        except Exception:
            pass
    try:
        mid = Material.Create(fdoc, nome)
        mat = fdoc.GetElement(mid)
        mat.Color = Color(rgb[0], rgb[1], rgb[2])
        return mid
    except Exception:
        return ElementId.InvalidElementId


def _poligono_limpo(pts, tol=0.0035):
    out = []
    for p in pts:
        if not out or math.hypot(p[0] - out[-1][0], p[1] - out[-1][1]) > tol:
            out.append(p)
    while len(out) > 1 and math.hypot(out[0][0] - out[-1][0], out[0][1] - out[-1][1]) <= tol:
        out.pop()
    return out


def _area_poligono(pts):
    if len(pts) < 3:
        return 0.0
    s = 0.0
    n = len(pts)
    for i in range(n):
        x1, z1 = pts[i]
        x2, z2 = pts[(i + 1) % n]
        s += x1 * z2 - x2 * z1
    return abs(s) * 0.5


def _extrusao_xz(fdoc, pts_xz, y0, y1, mat_id):
    """Extruda um polígono desenhado no plano XZ entre y0 e y1 (pés). Retorna o Extrusion ou None."""
    pts = _poligono_limpo(pts_xz)
    if len(pts) < 3 or abs(y1 - y0) < 0.004 or _area_poligono(pts) < 1e-5:
        return None
    plano = Plane.CreateByNormalAndOrigin(XYZ.BasisY, XYZ(0, y0, 0))
    sp = SketchPlane.Create(fdoc, plano)
    ca = CurveArray()
    n = len(pts)
    for i in range(n):
        a = pts[i]
        b = pts[(i + 1) % n]
        ca.Append(Line.CreateBound(XYZ(a[0], y0, a[1]), XYZ(b[0], y0, b[1])))
    caa = CurveArrArray()
    caa.Append(ca)
    ex = fdoc.FamilyCreate.NewExtrusion(True, caa, sp, y1 - y0)
    try:
        if mat_id is not None and mat_id != ElementId.InvalidElementId:
            p = ex.get_Parameter(BuiltInParameter.MATERIAL_ID_PARAM)
            if p is not None and not p.IsReadOnly:
                p.Set(mat_id)
    except Exception:
        pass
    return ex


def _retangulo(x0, z0, x1, z1):
    return [(x0, z0), (x1, z0), (x1, z1), (x0, z1)]


def _faixa(p, q, meia_larg):
    """Retângulo de espessura 2*meia_larg ao longo do segmento p→q."""
    dx = q[0] - p[0]
    dz = q[1] - p[1]
    c = math.hypot(dx, dz)
    if c < 1e-6:
        return None
    nx, nz = -dz / c, dx / c
    return [
        (p[0] + nx * meia_larg, p[1] + nz * meia_larg),
        (q[0] + nx * meia_larg, q[1] + nz * meia_larg),
        (q[0] - nx * meia_larg, q[1] - nz * meia_larg),
        (p[0] - nx * meia_larg, p[1] - nz * meia_larg),
    ]


def _clip_poly_halfplane(pts, ax, az, c):
    """Recorta polígono 2D mantendo ax*x + az*z <= c."""
    if not pts:
        return []
    out = []
    n = len(pts)
    tol = 1e-9
    for i in range(n):
        p1 = pts[i]
        p2 = pts[(i + 1) % n]
        d1 = ax * p1[0] + az * p1[1] - c
        d2 = ax * p2[0] + az * p2[1] - c
        in1 = (d1 <= tol)
        in2 = (d2 <= tol)
        if in1 and in2:
            out.append(p2)
        elif in1 and not in2:
            denom = d1 - d2
            if abs(denom) > 1e-12:
                t = d1 / denom
                out.append((p1[0] + t * (p2[0] - p1[0]), p1[1] + t * (p2[1] - p1[1])))
        elif not in1 and in2:
            denom = d1 - d2
            if abs(denom) > 1e-12:
                t = d1 / denom
                out.append((p1[0] + t * (p2[0] - p1[0]), p1[1] + t * (p2[1] - p1[1])))
            out.append(p2)
    return out


def _recortar_barra(p, q, meia_larg, planos, extra=2.0):
    """Gera barra estendida ao longo de p→q e recorta pelos semiplanos limites [(ax, az, c)]."""
    dx = q[0] - p[0]
    dz = q[1] - p[1]
    comp = math.hypot(dx, dz)
    if comp < 1e-5:
        return None
    ux, uz = dx / comp, dz / comp
    ext = max(extra, comp * 0.5)
    p_ext = (p[0] - ux * ext, p[1] - uz * ext)
    q_ext = (q[0] + ux * ext, q[1] + uz * ext)
    pts = _faixa(p_ext, q_ext, meia_larg)
    if not pts:
        return None
    for ax, az, c in planos:
        pts = _clip_poly_halfplane(pts, ax, az, c)
        if len(pts) < 3:
            return None
    pts = _poligono_limpo(pts)
    if len(pts) < 3 or _area_poligono(pts) < 1e-5:
        return None
    return pts


_NOMES_TIPO_TES = (u"Rainha", u"Simples", u"Howe", u"Fink")


def _chave_geometria(g):
    return (
        g.get("tipo", 0), g.get("agua1_dir", 0), g["b"], g["h"], g["xa"], g["xb"],
        g["xel"], g["xer"], g["zel"], g["zer"], g["xp"], g["zp"],
        g["avanco"], g["frechais"], g["estribo"],
        g.get("entalhes", 1),
    )


def _nome_familia(g):
    import hashlib
    vao = int(round(g["xb"] - g["xa"]))
    alt = int(round(g["zp"] - g["h"]))
    t_idx = min(3, max(0, int(g.get("tipo", 0))))
    t_nome = _NOMES_TIPO_TES[t_idx]
    suf_agua = u" 1A" if g.get("agua1_dir", 0) != 0 else u""
    tag = hashlib.md5(repr(_chave_geometria(g)).encode("utf-8")).hexdigest()[:6]
    return u"Tesoura {}{} AutoBIM - Vao {}cm - Alt {}cm - {}x{} - {}".format(
        t_nome, suf_agua, vao, alt, int(round(g["b"])), int(round(g["h"])), tag
    )


def _construir_familia_tesoura(app, template, g, caminho_rfa):
    """
    Constrói a família da tesoura (plano XZ, espessura em Y, origem na face inferior da Linha, no
    centro do vão entre apoios) com suporte a 1 ou 2 águas, Rainha/Simples/Howe/Fink e entalhes recortados.
    Retorna None se OK ou a mensagem de erro.
    """
    cm = 1.0 / 30.48
    b = g["b"] * cm
    h = g["h"] * cm
    xa, xb = g["xa"] * cm, g["xb"] * cm
    xel, xer = g["xel"] * cm, g["xer"] * cm
    zel, zer = g["zel"] * cm, g["zer"] * cm
    xp, zp = g["xp"] * cm, g["zp"] * cm
    avanco = g["avanco"] * cm
    tipo = min(3, max(0, int(g.get("tipo", 0))))
    agua1_dir = int(g.get("agua1_dir", 0))   # 0 = 2 águas; 1 = 1 água sobe L->R; -1 = 1 água sobe R->L
    entalhes = bool(g.get("entalhes", 1))
    folga = 0.0
    ent = min(0.20 * h, 2.5 * cm) if entalhes else 0.0

    fdoc = None
    try:
        fdoc = app.NewFamilyDocument(template)
        if fdoc is None:
            return u"Não foi possível abrir o modelo de família."

        t = Transaction(fdoc, "Gerar Tesoura")
        t.Start()
        try:
            try:
                cat = fdoc.Settings.Categories.get_Item(BuiltInCategory.OST_StructuralFraming)
                fdoc.OwnerFamily.FamilyCategory = cat
            except Exception:
                pass

            mad = _mat_familia(fdoc, u"Madeira - Telhado (AutoBIM)", (186, 132, 78))
            aco = _mat_familia(fdoc, u"Aço - Ferragem (AutoBIM)", (60, 60, 64))
            meia_b = b * 0.5

            # Inclinações e espessuras verticais das empenas (espessura perpendicular = h)
            if agua1_dir == 1:
                sl_l = (zp - zel) / max(1e-6, xp - xel)
                hv_l = h * math.sqrt(1.0 + sl_l * sl_l)
                sl_r = sl_l
                hv_r = hv_l
            elif agua1_dir == -1:
                sl_r = (zer - zp) / max(1e-6, xer - xp)
                hv_r = h * math.sqrt(1.0 + sl_r * sl_r)
                sl_l = sl_r
                hv_l = hv_r
            else:
                sl_l = (zp - zel) / max(1e-6, xp - xel)
                sl_r = (zer - zp) / max(1e-6, xer - xp)        # negativa
                hv_l = h * math.sqrt(1.0 + sl_l * sl_l)
                hv_r = h * math.sqrt(1.0 + sl_r * sl_r)

            def zt_l(x):
                return zel + sl_l * (x - xel)

            def zt_r(x):
                return zer + sl_r * (x - xer)

            def zb_l(x):
                return zt_l(x) - hv_l

            def zb_r(x):
                return zt_r(x) - hv_r

            # Face superior da Linha: encaixe/dente no calcanhar sobre o apoio baixo
            if agua1_dir == 1:
                z_lt = zb_l(xa) + ent
            elif agua1_dir == -1:
                z_lt = zb_r(xb) + ent
            else:
                z_lt = min(zb_l(xa), zb_r(xb)) + ent
            z_lb = z_lt - h
            dz = -z_lb                              # desloca tudo para a origem ficar na base da Linha

            def P(x, z):
                return (x, z + dz)

            # Semiplanos globais para recorte exato das barras internas (coordenadas pré-dz)
            pl_acima_linha = (0.0, -1.0, -(z_lt - folga))
            pl_sob_emp_l = (-sl_l, 1.0, (zel - hv_l - sl_l * xel) + folga * math.sqrt(1.0 + sl_l * sl_l))
            pl_sob_emp_r = (-sl_r, 1.0, (zer - hv_r - sl_r * xer) + folga * math.sqrt(1.0 + sl_r * sl_r))

            madeira = []
            tem_pendural = (tipo in (0, 1, 2))
            meia_p = h * 0.5

            # ------------------------------------------------------ 1. LINHA (COM DENTE/ENTALHE NO CALCANHAR)
            xa_out = xa if agua1_dir == -1 else (xa - avanco)
            xb_out = xb if agua1_dir == 1 else (xb + avanco)
            if ent > 1e-4:
                if agua1_dir == 1:
                    z_ent_l = max(z_lb + 0.35 * h, min(z_lt, zb_l(xa)))
                    x_top_l = xel + (z_lt + hv_l - zel) / max(1e-6, sl_l)
                    x_top_l = max(xa + 0.01 * h, min(xb - 0.5 * h, x_top_l))
                    z_tip_l = max(z_lb + 0.35 * h, min(z_ent_l, zb_l(xa_out)))
                    pts_linha = [
                        P(xa_out, z_lb), P(xb_out, z_lb), P(xb_out, z_lt),
                        P(x_top_l, z_lt), P(xa, z_ent_l), P(xa_out, z_tip_l),
                    ]
                elif agua1_dir == -1:
                    z_ent_r = max(z_lb + 0.35 * h, min(z_lt, zb_r(xb)))
                    x_top_r = xer + (z_lt + hv_r - zer) / min(-1e-6, sl_r)
                    x_top_r = min(xb - 0.01 * h, max(xa + 0.5 * h, x_top_r))
                    z_tip_r = max(z_lb + 0.35 * h, min(z_ent_r, zb_r(xb_out)))
                    pts_linha = [
                        P(xa_out, z_lb), P(xb_out, z_lb),
                        P(xb_out, z_tip_r), P(xb, z_ent_r), P(x_top_r, z_lt),
                        P(xa_out, z_lt),
                    ]
                else:
                    z_ent_l = max(z_lb + 0.35 * h, min(z_lt, zb_l(xa)))
                    z_ent_r = max(z_lb + 0.35 * h, min(z_lt, zb_r(xb)))
                    x_top_l = xel + (z_lt + hv_l - zel) / max(1e-6, sl_l)
                    x_top_r = xer + (z_lt + hv_r - zer) / min(-1e-6, sl_r)
                    x_top_l = max(xa + 0.01 * h, min(xp - 0.15 * h, x_top_l))
                    x_top_r = min(xb - 0.01 * h, max(xp + 0.15 * h, x_top_r))
                    if x_top_l >= x_top_r:
                        x_top_l = min(x_top_l, xp - 0.05 * h)
                        x_top_r = max(x_top_r, xp + 0.05 * h)
                    z_tip_l = max(z_lb + 0.35 * h, min(z_ent_l, zb_l(xa_out)))
                    z_tip_r = max(z_lb + 0.35 * h, min(z_ent_r, zb_r(xb_out)))
                    pts_linha = [
                        P(xa_out, z_lb), P(xb_out, z_lb),
                        P(xb_out, z_tip_r), P(xb, z_ent_r), P(x_top_r, z_lt),
                        P(x_top_l, z_lt), P(xa, z_ent_l), P(xa_out, z_tip_l),
                    ]
            else:
                pts_linha = [P(xa_out, z_lb), P(xb_out, z_lb), P(xb_out, z_lt), P(xa_out, z_lt)]

            ex = _extrusao_xz(fdoc, pts_linha, -meia_b, meia_b, mad)
            if ex is not None:
                madeira.append(ex)

            # ------------------------------------------------------ 2. EMPENAS (BEIRAL AO CUME/PENDURAL)
            if agua1_dir == 1:
                ex = _extrusao_xz(fdoc, [
                    P(xel, zel), P(xer, zt_l(xer)),
                    P(xer, zb_l(xer)), P(xel, zb_l(xel))
                ], -meia_b, meia_b, mad)
                if ex is not None:
                    madeira.append(ex)
            elif agua1_dir == -1:
                ex = _extrusao_xz(fdoc, [
                    P(xel, zt_r(xel)), P(xer, zer),
                    P(xer, zb_r(xer)), P(xel, zb_r(xel))
                ], -meia_b, meia_b, mad)
                if ex is not None:
                    madeira.append(ex)
            elif tem_pendural:
                x_cume_l = xp - meia_p + folga
                x_cume_r = xp + meia_p - folga
                if x_cume_l > xel + 0.05 * h:
                    ex = _extrusao_xz(fdoc, [
                        P(xel, zel), P(x_cume_l, zt_l(x_cume_l)),
                        P(x_cume_l, zb_l(x_cume_l)), P(xel, zb_l(xel))
                    ], -meia_b, meia_b, mad)
                    if ex is not None:
                        madeira.append(ex)
                if xer > x_cume_r + 0.05 * h:
                    ex = _extrusao_xz(fdoc, [
                        P(x_cume_r, zt_r(x_cume_r)), P(xer, zer),
                        P(xer, zb_r(xer)), P(x_cume_r, zb_r(x_cume_r))
                    ], -meia_b, meia_b, mad)
                    if ex is not None:
                        madeira.append(ex)
            else:
                # Fink: duas empenas encontram diretamente no prumo do cume
                x_cume_l = xp + folga
                x_cume_r = xp - folga
                ex = _extrusao_xz(fdoc, [
                    P(xel, zel), P(x_cume_l, zt_l(x_cume_l)),
                    P(x_cume_l, zb_l(x_cume_l)), P(xel, zb_l(xel))
                ], -meia_b, meia_b, mad)
                if ex is not None:
                    madeira.append(ex)
                ex = _extrusao_xz(fdoc, [
                    P(x_cume_r, zt_r(x_cume_r)), P(xer, zer),
                    P(xer, zb_r(xer)), P(x_cume_r, zb_r(x_cume_r))
                ], -meia_b, meia_b, mad)
                if ex is not None:
                    madeira.append(ex)

            # ------------------------------------------------------ 3. PENDURAL CENTRAL + PÉ DE APOIO
            tem_pe = False
            meia_pe = meia_p
            x_est = xp
            z_topo_est = zp
            if agua1_dir == 1:
                x_m0, x_m1 = xb - 2.0 * meia_p, xb
                x_est = xb - meia_p
                z_m0, z_m1 = zb_l(x_m0), zb_l(x_m1)
                z_topo_est = z_m0
                if z_m0 > z_lt + 0.10 * h:
                    ex = _extrusao_xz(fdoc, [
                        P(x_m0, z_lt), P(x_m1, z_lt), P(x_m1, z_m1), P(x_m0, z_m0)
                    ], -meia_b, meia_b, mad)
                    if ex is not None:
                        madeira.append(ex)
            elif agua1_dir == -1:
                x_m0, x_m1 = xa, xa + 2.0 * meia_p
                x_est = xa + meia_p
                z_m0, z_m1 = zb_r(x_m0), zb_r(x_m1)
                z_topo_est = z_m1
                if z_m1 > z_lt + 0.10 * h:
                    ex = _extrusao_xz(fdoc, [
                        P(x_m0, z_lt), P(x_m1, z_lt), P(x_m1, z_m1), P(x_m0, z_m0)
                    ], -meia_b, meia_b, mad)
                    if ex is not None:
                        madeira.append(ex)
            elif tem_pendural:
                z_base_p = z_lt - folga
                usa_pe = (entalhes and tipo in (0, 2) and (zp - z_lt) > 2.8 * h)
                if tipo == 2:
                    x2_l_chk = xa + (xp - xa) * (2.0 / 3.0)
                    x2_r_chk = xp + (xb - xp) * (1.0 / 3.0)
                    if min(xp - x2_l_chk, x2_r_chk - xp) <= (meia_p + 0.35 * h) + 0.375 * h + 0.2 * h:
                        usa_pe = False
                if usa_pe:
                    tem_pe = True
                    meia_pe = meia_p + 0.32 * h
                    h_pe = min(1.20 * h, 0.35 * (zp - z_lt))
                    z_pe_ret = z_lt + 0.70 * h_pe
                    z_pe_fim = z_lt + h_pe
                    pts_pend = [
                        P(xp - meia_pe, z_base_p), P(xp + meia_pe, z_base_p),
                        P(xp + meia_pe, z_pe_ret), P(xp + meia_p, z_pe_fim),
                        P(xp + meia_p, zt_r(xp + meia_p)), P(xp, zp),
                        P(xp - meia_p, zt_l(xp - meia_p)),
                        P(xp - meia_p, z_pe_fim), P(xp - meia_pe, z_pe_ret),
                    ]
                else:
                    pts_pend = [
                        P(xp - meia_p, z_base_p), P(xp + meia_p, z_base_p),
                        P(xp + meia_p, zt_r(xp + meia_p)), P(xp, zp),
                        P(xp - meia_p, zt_l(xp - meia_p)),
                    ]
                ex = _extrusao_xz(fdoc, pts_pend, -meia_b, meia_b, mad)
                if ex is not None:
                    madeira.append(ex)

            def _add_barra_recortada(p1, p2, meia_w, planos_extra):
                if agua1_dir == 1:
                    planos = [pl_acima_linha, pl_sob_emp_l, (1.0, 0.0, xb - 2.0 * meia_p)] + list(planos_extra)
                elif agua1_dir == -1:
                    planos = [pl_acima_linha, pl_sob_emp_r, (-1.0, 0.0, -(xa + 2.0 * meia_p))] + list(planos_extra)
                else:
                    planos = [pl_acima_linha, pl_sob_emp_l, pl_sob_emp_r] + list(planos_extra)
                pts = _recortar_barra(p1, p2, meia_w, planos, extra=max(h * 4.0, 2.0))
                if pts:
                    pts_dz = [P(pt[0], pt[1]) for pt in pts]
                    ex_b = _extrusao_xz(fdoc, pts_dz, -meia_b, meia_b, mad)
                    if ex_b is not None:
                        madeira.append(ex_b)

            # ------------------------------------------------------ 4. BARRAS INTERNAS POR TIPO
            meia_esc = 0.38 * h

            if agua1_dir != 0:
                x_low = xa if agua1_dir == 1 else xb
                x_hi_in = (xb - 2.0 * meia_p) if agua1_dir == 1 else (xa + 2.0 * meia_p)
                zb_fn_1a = zb_l if agua1_dir == 1 else zb_r
                vao_1a = abs(x_hi_in - x_low)
                if vao_1a > 0.6 * h:
                    if tipo == 0:
                        x_t = 0.5 * (x_low + x_hi_in)
                        _add_barra_recortada((x_hi_in, z_lt + 0.40 * h), (x_t, zb_fn_1a(x_t)), meia_esc, [])
                    elif tipo == 1:
                        pass
                    elif tipo == 2:
                        meia_m = 0.36 * h
                        if vao_1a > 3.2 * h:
                            xs_m = [x_low + (x_hi_in - x_low) / 3.0, x_low + 2.0 * (x_hi_in - x_low) / 3.0]
                        elif vao_1a > 1.6 * h:
                            xs_m = [0.5 * (x_low + x_hi_in)]
                        else:
                            xs_m = []
                        for xm in xs_m:
                            z_top_m = zb_fn_1a(xm)
                            if z_top_m > z_lt + 0.25 * h:
                                _add_barra_recortada((xm, z_lt), (xm, z_top_m), meia_m, [])
                        if len(xs_m) == 2:
                            xm_out, xm_in = xs_m[0], xs_m[1]
                            if agua1_dir == 1:
                                x_f1 = xm_in + meia_m
                                _add_barra_recortada((x_hi_in, z_lt + 0.35 * h), (x_f1, zb_fn_1a(x_f1)), meia_esc, [(-1.0, 0.0, -(x_f1 - folga))])
                                x_s1, x_s2 = xm_in - meia_m, xm_out + meia_m
                                if x_s1 > x_s2 + 0.2 * h and zb_fn_1a(x_s2) > z_lt + 0.25 * h:
                                    _add_barra_recortada((x_s1, z_lt + 0.25 * h), (x_s2, zb_fn_1a(x_s2)), meia_esc, [(1.0, 0.0, x_s1 + folga), (-1.0, 0.0, -(x_s2 - folga))])
                            else:
                                x_f1 = xm_in - meia_m
                                _add_barra_recortada((x_hi_in, z_lt + 0.35 * h), (x_f1, zb_fn_1a(x_f1)), meia_esc, [(1.0, 0.0, x_f1 + folga)])
                                x_s1, x_s2 = xm_in + meia_m, xm_out - meia_m
                                if x_s2 > x_s1 + 0.2 * h and zb_fn_1a(x_s2) > z_lt + 0.25 * h:
                                    _add_barra_recortada((x_s1, z_lt + 0.25 * h), (x_s2, zb_fn_1a(x_s2)), meia_esc, [(-1.0, 0.0, -(x_s1 - folga)), (1.0, 0.0, x_s2 + folga)])
                        elif len(xs_m) == 1:
                            xm_in = xs_m[0]
                            if agua1_dir == 1:
                                x_f1 = xm_in + meia_m
                                _add_barra_recortada((x_hi_in, z_lt + 0.35 * h), (x_f1, zb_fn_1a(x_f1)), meia_esc, [(-1.0, 0.0, -(x_f1 - folga))])
                            else:
                                x_f1 = xm_in - meia_m
                                _add_barra_recortada((x_hi_in, z_lt + 0.35 * h), (x_f1, zb_fn_1a(x_f1)), meia_esc, [(1.0, 0.0, x_f1 + folga)])
                    elif tipo == 3:
                        x_b = x_low + (2.0 / 3.0) * (x_hi_in - x_low)
                        x_t = 0.5 * (x_low + x_hi_in)
                        _add_barra_recortada((x_t, zb_fn_1a(x_t)), (x_b, z_lt), meia_esc, [])
                        _add_barra_recortada((x_b, z_lt), (x_hi_in, zb_fn_1a(x_hi_in)), meia_esc, [])

            elif tipo == 0:
                # ESTRIBADA RAINHA (Pendural + 2 Escoras)
                x_pe_l = xp - meia_pe
                x_pe_r = xp + meia_pe
                z_start = z_lt + 0.45 * h
                x_tl = 0.5 * (xa + xp)
                x_tr = 0.5 * (xp + xb)
                if (x_pe_l - xa) > 0.3 * h:
                    _add_barra_recortada(
                        (x_pe_l, z_start), (x_tl, zb_l(x_tl)), meia_esc,
                        [(1.0, 0.0, x_pe_l + folga)]
                    )
                if (xb - x_pe_r) > 0.3 * h:
                    _add_barra_recortada(
                        (x_pe_r, z_start), (x_tr, zb_r(x_tr)), meia_esc,
                        [(-1.0, 0.0, -(x_pe_r - folga))]
                    )

            elif tipo == 1:
                # ESTRIBADA SIMPLES (somente Pendural central)
                pass

            elif tipo == 2:
                # HOWE DUPLA (Pendural + Montantes + Escoras em leque)
                meia_m = 0.36 * h
                x_pe_l = xp - meia_pe
                x_pe_r = xp + meia_pe
                for lado, x_lim, zb_fn, x_pe_borda in ((-1, xa, zb_l, x_pe_l), (1, xb, zb_r, x_pe_r)):
                    vao_lado = abs(xp - x_lim)
                    if vao_lado > 3.2 * h:
                        xs_m = [x_lim + (xp - x_lim) / 3.0, x_lim + 2.0 * (xp - x_lim) / 3.0]
                    elif vao_lado > 1.6 * h:
                        xs_m = [0.5 * (x_lim + xp)]
                    else:
                        xs_m = []

                    # Montantes verticais
                    for xm in xs_m:
                        z_top_m = zb_fn(xm)
                        if z_top_m > z_lt + 0.25 * h:
                            _add_barra_recortada((xm, z_lt), (xm, z_top_m), meia_m, [])

                    # Escoras (do pé do montante/pendural interno ao topo do montante externo)
                    nos_base = list(reversed(xs_m))
                    if len(xs_m) == 2:
                        # Painel interno: pé do pendural -> topo de xm[1]
                        xm_in = xs_m[1]
                        p_ini = (x_pe_borda, z_lt + 0.35 * h)
                        if lado < 0:
                            x_fim_m = xm_in + meia_m
                            _add_barra_recortada(p_ini, (x_fim_m, zb_fn(x_fim_m)), meia_esc, [
                                (1.0, 0.0, x_pe_borda + folga),
                                (-1.0, 0.0, -(x_fim_m - folga)),
                            ])
                            # Painel intermediário: pé de xm[1] -> topo de xm[0]
                            xm_out = xs_m[0]
                            x_s1 = xm_in - meia_m
                            x_s2 = xm_out + meia_m
                            if x_s1 > x_s2 + 0.2 * h and zb_fn(x_s2) > z_lt + 0.25 * h:
                                _add_barra_recortada((x_s1, z_lt + 0.25 * h), (x_s2, zb_fn(x_s2)), meia_esc, [
                                    (1.0, 0.0, x_s1 + folga),
                                    (-1.0, 0.0, -(x_s2 - folga)),
                                ])
                        else:
                            x_fim_m = xm_in - meia_m
                            _add_barra_recortada(p_ini, (x_fim_m, zb_fn(x_fim_m)), meia_esc, [
                                (-1.0, 0.0, -(x_pe_borda - folga)),
                                (1.0, 0.0, x_fim_m + folga),
                            ])
                            xm_out = xs_m[0]
                            x_s1 = xm_in + meia_m
                            x_s2 = xm_out - meia_m
                            if x_s2 > x_s1 + 0.2 * h and zb_fn(x_s2) > z_lt + 0.25 * h:
                                _add_barra_recortada((x_s1, z_lt + 0.25 * h), (x_s2, zb_fn(x_s2)), meia_esc, [
                                    (-1.0, 0.0, -(x_s1 - folga)),
                                    (1.0, 0.0, x_s2 + folga),
                                ])
                    elif len(xs_m) == 1:
                        xm_in = xs_m[0]
                        p_ini = (x_pe_borda, z_lt + 0.35 * h)
                        if lado < 0:
                            x_fim_m = xm_in + meia_m
                            _add_barra_recortada(p_ini, (x_fim_m, zb_fn(x_fim_m)), meia_esc, [
                                (1.0, 0.0, x_pe_borda + folga),
                                (-1.0, 0.0, -(x_fim_m - folga)),
                            ])
                        else:
                            x_fim_m = xm_in - meia_m
                            _add_barra_recortada(p_ini, (x_fim_m, zb_fn(x_fim_m)), meia_esc, [
                                (-1.0, 0.0, -(x_pe_borda - folga)),
                                (1.0, 0.0, x_fim_m + folga),
                            ])

            elif tipo == 3:
                # FINK (Treliça em W)
                x_bL = xa + (2.0 / 3.0) * (xp - xa)
                x_bR = xp + (1.0 / 3.0) * (xb - xp)
                x_tL = 0.5 * (xa + xp)
                x_tR = 0.5 * (xp + xb)
                z_cume_inf = zp - 0.5 * (hv_l + hv_r)

                _add_barra_recortada((x_tL, zb_l(x_tL)), (x_bL, z_lt), meia_esc, [])
                _add_barra_recortada((x_bL, z_lt), (xp, z_cume_inf), meia_esc, [(1.0, 0.0, xp + folga)])
                _add_barra_recortada((xp, z_cume_inf), (x_bR, z_lt), meia_esc, [(-1.0, 0.0, -(xp - folga))])
                _add_barra_recortada((x_bR, z_lt), (x_tR, zb_r(x_tR)), meia_esc, [])

            # ------------------------------------------------------ 5. FRECHAIS
            if g["frechais"]:
                meio_comp = 0.20 / 0.3048
                for xf in (xa, xb):
                    ex = _extrusao_xz(fdoc, _retangulo(xf - h * 0.5, -b, xf + h * 0.5, folga),
                                      -meio_comp, meio_comp, mad)
                    if ex is not None:
                        madeira.append(ex)

            # ------------------------------------------------------ 6. REGENERAR ANTES DA FERRAGEM
            fdoc.Regenerate()

            # ------------------------------------------------------ 7. FERRAGEM / ESTRIBO METÁLICO
            if g["estribo"]:
                esp = 0.005 / 0.3048
                larg = 0.025 / 0.3048
                if tem_pendural or agua1_dir != 0:
                    topo_est = min((z_lt + dz) + h * 1.6, max((z_lt + dz) + h * 0.5, (z_topo_est + dz) - 0.1 * h))
                    for y0, y1 in ((meia_b, meia_b + esp), (-meia_b - esp, -meia_b)):
                        _extrusao_xz(fdoc, _retangulo(x_est - larg, -0.04 / 0.3048, x_est + larg, topo_est),
                                     y0, y1, aco)
                else:
                    # Chapa metálica no cume para Fink (não possui pendural central)
                    z_inf_ch = max(z_lt + dz + 0.1 * h, (zp - max(hv_l, hv_r) - 0.6 * h) + dz)
                    z_sup_ch = (zp - 0.25 * max(hv_l, hv_r)) + dz
                    for y0, y1 in ((meia_b, meia_b + esp), (-meia_b - esp, -meia_b)):
                        _extrusao_xz(fdoc, _retangulo(xp - larg * 1.4, z_inf_ch, xp + larg * 1.4, z_sup_ch),
                                     y0, y1, aco)

            t.Commit()
        except Exception as e_in:
            try:
                t.RollBack()
            except Exception:
                pass
            raise e_in

        pasta = os.path.dirname(caminho_rfa)
        if not os.path.isdir(pasta):
            os.makedirs(pasta)
        so = SaveAsOptions()
        so.OverwriteExistingFile = True
        fdoc.SaveAs(caminho_rfa, so)
        fdoc.Close(False)
        fdoc = None
        return None
    except Exception as e:
        try:
            if fdoc is not None:
                fdoc.Close(False)
        except Exception:
            pass
        return u"{}".format(e)


def _nivel_para_z(z_abs):
    niveis = sorted(FilteredElementCollector(doc).OfClass(Level).ToElements(), key=lambda l: l.Elevation)
    if not niveis:
        return None
    escolhido = niveis[0]
    for lv in niveis:
        if lv.Elevation <= z_abs + 1e-6:
            escolhido = lv
    return escolhido


def _achar_simbolo_existente(nome_fam):
    for fam in FilteredElementCollector(doc).OfClass(Family).ToElements():
        try:
            if fam.Name == nome_fam:
                for sid in fam.GetFamilySymbolIds():
                    return doc.GetElement(sid)
        except Exception:
            pass
    return None


def run_tesouras():
    global doc, uidoc, view
    uidoc = __revit__.ActiveUIDocument
    doc   = uidoc.Document
    view  = doc.ActiveView
    import tempfile

    prefs = carregar_preferencias()
    win = TesourasWindow(prefs)
    win.ShowDialog()
    if not win.confirmado:
        script.exit()

    op = win.resultado
    salvar_preferencias(op)

    roofs = selecionar_telhados()

    esp_linhas_ft = op["tes_esp_linhas"] / 30.48
    desconto_ft   = op["tes_desconto"] / 30.48
    recuo_ft      = op["tes_recuo"] / 30.48
    peca_b_ft     = op["tes_b"] / 30.48
    peca_h_ft     = op["tes_h"] / 30.48

    tesouras = []
    ignoradas = 0

    for roof in roofs:
        aguas = extrair_faces_aguas(roof, usar_face_inferior=True)
        if not aguas:
            aguas = extrair_faces_aguas(roof, usar_face_inferior=False)
        if not aguas:
            continue

        if op["tes_dir_idx"] == 1:
            dir_vao = XYZ.BasisX
            dir_rep = XYZ.BasisY
        elif op["tes_dir_idx"] == 2:
            dir_vao = XYZ.BasisY
            dir_rep = XYZ.BasisX
        else:
            maior_agua = max(aguas, key=lambda a: a["face"].Area)
            u_h = maior_agua["u_horiz"]
            dir_rep = XYZ(u_h.X, u_h.Y, 0).Normalize()
            dir_vao = XYZ(-dir_rep.Y, dir_rep.X, 0).Normalize()

        todos_pts_3d = []
        for a in aguas:
            for poly in a["loops_uv"]:
                for u_val, v_val in poly:
                    todos_pts_3d.append(ponto_3d_agua(a, u_val, v_val, desconto_ft))
        if not todos_pts_3d:
            continue

        z_base_global = min(p.Z for p in todos_pts_3d)
        reps = [p.DotProduct(dir_rep) for p in todos_pts_3d]
        vaos = [p.DotProduct(dir_vao) for p in todos_pts_3d]
        rep_min, rep_max = min(reps), max(reps)
        vao_min, vao_max = min(vaos), max(vaos)

        posicoes = gerar_posicoes_uniformes(rep_min, rep_max, esp_linhas_ft, 0.35 * FT)

        for idx_tes, r_pos in enumerate(posicoes, 1):
            n_amostras = 200
            passo_s = (vao_max - vao_min) / float(n_amostras)
            am = []
            for k in range(n_amostras + 1):
                s_val = vao_min + k * passo_s
                pt_xy = dir_rep * r_pos + dir_vao * s_val
                z_max_s = None
                for a in aguas:
                    z_c = obter_z_agua_em_xy(a, pt_xy.X, pt_xy.Y, desconto_ft)
                    if z_c is not None and (z_max_s is None or z_c > z_max_s):
                        z_max_s = z_c
                if z_max_s is not None:
                    am.append((s_val, z_max_s))

            if len(am) < 3:
                ignoradas += 1
                continue
            s_ini, s_fim = am[0][0], am[-1][0]
            if (s_fim - s_ini) < 0.80 * FT:
                ignoradas += 1
                continue
            s_pico, z_pico = max(am, key=lambda it: it[1])

            # --- Tesoura: perfil entre apoios (frechais) ---
            xa_s = s_ini + recuo_ft
            xb_s = s_fim - recuo_ft
            if (xb_s - xa_s) < 0.80 * FT:
                ignoradas += 1
                continue
            z_el = _z_no_perfil(am, s_ini, am[0][1])
            z_er = _z_no_perfil(am, s_fim, am[-1][1])

            # Detecção automática: 2 águas vs 1 água (Meia-Tesoura)
            agua1_dir = 0
            s_pico_eff = s_pico
            z_pico_eff = z_pico
            if (s_fim - s_pico) < 0.35 * FT and (z_pico - z_el) >= 0.25 * FT:
                agua1_dir = 1
                s_pico_eff = s_fim
                z_pico_eff = z_er
            elif (s_pico - s_ini) < 0.35 * FT and (z_pico - z_er) >= 0.25 * FT:
                agua1_dir = -1
                s_pico_eff = s_ini
                z_pico_eff = z_el
            elif (s_pico - s_ini) < 0.35 * FT or (s_fim - s_pico) < 0.35 * FT:
                ignoradas += 1
                continue

            s_mid = 0.5 * (xa_s + xb_s)

            # Cota absoluta da base da Linha (calculada igual à do construtor da família)
            ent_ft = min(0.20 * peca_h_ft, 2.5 / 30.48) if op.get("tes_entalhes", True) else 0.0
            if agua1_dir == 1:
                sl_l = (z_pico_eff - z_el) / max(1e-6, s_pico_eff - s_ini)
                hv_l = peca_h_ft * math.sqrt(1.0 + sl_l * sl_l)
                z_lt_abs = (z_el + sl_l * (xa_s - s_ini) - hv_l) + ent_ft
            elif agua1_dir == -1:
                sl_r = (z_er - z_pico_eff) / max(1e-6, s_fim - s_pico_eff)
                hv_r = peca_h_ft * math.sqrt(1.0 + sl_r * sl_r)
                z_lt_abs = (z_er + sl_r * (xb_s - s_fim) - hv_r) + ent_ft
            else:
                sl_l = (z_pico_eff - z_el) / max(1e-6, s_pico_eff - s_ini)
                sl_r = (z_er - z_pico_eff) / max(1e-6, s_fim - s_pico_eff)
                hv_l = peca_h_ft * math.sqrt(1.0 + sl_l * sl_l)
                hv_r = peca_h_ft * math.sqrt(1.0 + sl_r * sl_r)
                z_lt_abs = min(z_el + sl_l * (xa_s - s_ini) - hv_l,
                               z_er + sl_r * (xb_s - s_fim) - hv_r) + ent_ft
            if (z_pico_eff - z_lt_abs) < 0.25 * FT:
                ignoradas += 1
                continue
            z_lb_abs = z_lt_abs - peca_h_ft

            def c(v_ft):
                return round(v_ft * 30.48, 1)

            tesouras.append({
                "idx": idx_tes, "r_pos": r_pos, "dir_rep": dir_rep, "dir_vao": dir_vao,
                "s_mid": s_mid, "z_lb": z_lb_abs,
                "geo": {
                    "tipo": int(op.get("tes_tipo_idx", 0)),
                    "agua1_dir": agua1_dir,
                    "b": round(op["tes_b"], 1), "h": round(op["tes_h"], 1),
                    "xa": c(xa_s - s_mid), "xb": c(xb_s - s_mid),
                    "xel": c(s_ini - s_mid), "xer": c(s_fim - s_mid),
                    "zel": c(z_el - z_lb_abs), "zer": c(z_er - z_lb_abs),
                    "xp": c(s_pico_eff - s_mid), "zp": c(z_pico_eff - z_lb_abs),
                    "avanco": round(op["tes_avanco"], 1),
                    "frechais": 1 if op["tes_frechais"] else 0,
                    "estribo": 1 if op["tes_estribo"] else 0,
                    "entalhes": 1 if op.get("tes_entalhes", True) else 0,
                }
            })

    ids_criados = []
    familias_novas = 0
    erros_familia = []

    # ------------------------------------------------------------------ TESOURAS
    if tesouras:
        app = doc.Application
        template = _achar_template_generico(app, prefs)
        if not template:
            forms.alert("Modelo de família 'Modelo genérico' (.rft) não encontrado.", exitscript=True)

        pasta_tmp = os.path.join(tempfile.gettempdir(), "AutoBIM_Tesouras")
        arquivos = {}   # nome -> caminho
        for tes in tesouras:
            nome = _nome_familia(tes["geo"])
            tes["nome"] = nome
            if nome in arquivos:
                continue
            if _achar_simbolo_existente(nome) is not None:
                arquivos[nome] = None
                continue
            caminho = os.path.join(pasta_tmp, nome + u".rfa")
            erro = _construir_familia_tesoura(app, template, tes["geo"], caminho)
            if erro:
                erros_familia.append(erro)
                arquivos[nome] = False
            else:
                arquivos[nome] = caminho
                familias_novas += 1

        with TransactionGroup(doc, "Madeiramento: Tesouras") as tg:
            tg.Start()
            with Transaction(doc, "Madeiramento: Tesouras") as t:
                t.Start()
                simbolos = {}
                opts_load = _FamLoadOpts()
                for nome, caminho in arquivos.items():
                    if caminho is False:
                        continue
                    sym = None
                    if caminho:
                        try:
                            doc.LoadFamily(caminho, opts_load)
                        except Exception as e_ld:
                            erros_familia.append(u"Carregar família: {}".format(e_ld))
                    sym = _achar_simbolo_existente(nome)
                    if sym is not None:
                        if not sym.IsActive:
                            sym.Activate()
                        simbolos[nome] = sym
                doc.Regenerate()

                criadas_z = []
                for tes in tesouras:
                    sym = simbolos.get(tes["nome"])
                    if sym is None:
                        continue
                    p_xy = tes["dir_rep"] * tes["r_pos"] + tes["dir_vao"] * tes["s_mid"]
                    try:
                        nivel = _nivel_para_z(tes["z_lb"])
                        if nivel is not None:
                            # No Revit API, location.Z com Level entra como deslocamento relativo ao nível
                            pt_inst = XYZ(p_xy.X, p_xy.Y, tes["z_lb"] - nivel.Elevation)
                            inst = doc.Create.NewFamilyInstance(pt_inst, sym, nivel, StructuralType.NonStructural)
                        else:
                            pt_inst = XYZ(p_xy.X, p_xy.Y, tes["z_lb"])
                            inst = doc.Create.NewFamilyInstance(pt_inst, sym, StructuralType.NonStructural)
                        ang = math.atan2(tes["dir_vao"].Y, tes["dir_vao"].X)
                        if abs(ang) > 1e-6:
                            eixo = Line.CreateBound(XYZ(p_xy.X, p_xy.Y, 0.0), XYZ(p_xy.X, p_xy.Y, 1.0))
                            ElementTransformUtils.RotateElement(doc, inst.Id, eixo, ang)
                        criadas_z.append((inst.Id, tes["z_lb"]))
                        ids_criados.append(inst.Id)
                    except Exception as e_inst:
                        erros_familia.append(u"Inserir tesoura {:02d}: {}".format(tes["idx"], e_inst))

                # Garante cota Z exata caso o Revit aplique offset/nível de forma diferente
                doc.Regenerate()
                for eid, z_alvo in criadas_z:
                    inst = doc.GetElement(eid)
                    loc_pt = getattr(getattr(inst, "Location", None), "Point", None)
                    if loc_pt is None:
                        continue
                    dz_corr = z_alvo - loc_pt.Z
                    if abs(dz_corr) > 0.002:
                        try:
                            ElementTransformUtils.MoveElement(doc, eid, XYZ(0, 0, dz_corr))
                        except Exception:
                            pass
                        doc.Regenerate()
                        loc_pt2 = getattr(getattr(inst, "Location", None), "Point", None)
                        if loc_pt2 is not None and abs(z_alvo - loc_pt2.Z) > 0.002:
                            dz_corr2 = z_alvo - loc_pt2.Z
                            for bip in (
                                BuiltInParameter.INSTANCE_FREE_HOST_OFFSET_PARAM,
                                BuiltInParameter.INSTANCE_ELEVATION_PARAM,
                            ):
                                p_off = inst.get_Parameter(bip)
                                if p_off is not None and not p_off.IsReadOnly and p_off.StorageType == StorageType.Double:
                                    p_off.Set(p_off.AsDouble() + dz_corr2)
                                    break

                t.Commit()

            if op["tes_grp_idx"] == 1 and len(ids_criados) > 1:
                agrupar_lote_em_transacao([(ids_criados, u"Madeiramento - Tesouras")], "Agrupar Tesouras")
            tg.Assimilate()

    # ------------------------------------------------------------------- RESUMO
    msg = u"Tesouras concluídas!\n\n✔ Tesouras inseridas: {}\n✔ Famílias novas geradas: {}\n".format(
        len(ids_criados), familias_novas)
    if ignoradas:
        msg += u"\n⚠ Posições ignoradas (vão insuficiente ou plano horizontal): {}\n".format(ignoradas)
    if erros_familia:
        msg += u"\n✘ Erros:\n" + u"\n".join(erros_familia[:5])
    forms.alert(msg, title="Madeiramento: Tesouras")


def run_tesouras_pontaletes():
    run_tesouras()


# =============================================================================
# COMANDO 4: PONTALETES DE MADEIRA OU METÁLICOS (BERÇO / SAPATA + TERÇAS)
# =============================================================================
def obter_ou_criar_material_aco_galv():
    """Obtém ou cria o material de aço galvanizado para pontaletes e terças metálicas."""
    nome_mat = u"Aço Galvanizado - Estrutura (AutoBIM)"
    for m in FilteredElementCollector(doc).OfClass(Material).ToElements():
        try:
            if m.Name == nome_mat:
                return m.Id
        except Exception:
            pass
    fallback_id = ElementId.InvalidElementId
    for m in FilteredElementCollector(doc).OfClass(Material).ToElements():
        try:
            nl = m.Name.lower()
            if u"aço" in nl or "aco" in nl or "steel" in nl or "galvaniz" in nl:
                fallback_id = m.Id
                break
        except Exception:
            pass
    try:
        new_id = Material.Create(doc, nome_mat)
        mat = doc.GetElement(new_id)
        mat.Color = Color(178, 184, 190)
        return new_id
    except Exception:
        return fallback_id


def _loops_perfil_2d(b_ft, h_ft, e_ft, tipo_perfil):
    """
    Retorna lista de polígonos 2D [(x, y), ...] para extrusão:
      - 'retangular'  : seção maciça cheia
      - 'metalon'     : tubo oco retangular/quadrado
      - 'u_simples'   : perfil U simples aberto em +X
      - 'u_enrijecido': perfil C (U enrijecido com dobras para dentro em +X)
      - 'perfil_i'    : perfil I / W laminado (mesas superior/inferior + alma central)
    """
    hb = b_ft * 0.5
    hh = h_ft * 0.5
    if tipo_perfil == "retangular":
        return [[(-hb, -hh), (hb, -hh), (hb, hh), (-hb, hh)]]

    e = max(0.002 * FT, min(e_ft, min(hb, hh) * 0.42))
    if tipo_perfil == "metalon":
        outer = [(-hb, -hh), (hb, -hh), (hb, hh), (-hb, hh)]
        inner = [(-hb + e, -hh + e), (-hb + e, hh - e), (hb - e, hh - e), (hb - e, -hh + e)]
        return [outer, inner]

    if tipo_perfil == "perfil_i":
        tf = min(hh * 0.30, max(e, hh * 0.08))
        hw = max(0.0015 * FT, min(hb * 0.35, e * 0.55))
        if hb > hw * 1.2 and hh > tf * 1.3:
            return [[
                (-hb, -hh), (hb, -hh), (hb, -hh + tf), (hw, -hh + tf),
                (hw, hh - tf), (hb, hh - tf), (hb, hh), (-hb, hh),
                (-hb, hh - tf), (-hw, hh - tf), (-hw, -hh + tf), (-hb, -hh + tf),
            ]]

    dobra = min(hh * 0.35, max(e * 1.8, min(b_ft, h_ft) * 0.22))
    if tipo_perfil == "u_enrijecido" and dobra > e * 1.25 and (hh - dobra) > e * 0.8:
        return [[
            (-hb, -hh), (hb, -hh), (hb, -hh + dobra), (hb - e, -hh + dobra),
            (hb - e, -hh + e), (-hb + e, -hh + e), (-hb + e, hh - e),
            (hb - e, hh - e), (hb - e, hh - dobra), (hb, hh - dobra),
            (hb, hh), (-hb, hh),
        ]]

    return [[
        (-hb, -hh), (hb, -hh), (hb, -hh + e), (-hb + e, -hh + e),
        (-hb + e, hh - e), (hb, hh - e), (hb, hh), (-hb, hh),
    ]]


def gerar_solido_perfil(p1, p2, dir_largura, dir_altura, largura_ft, altura_ft,
                        topo_em_p1_p2=True, tipo_perfil="retangular", e_ft=0.007, mat_id=None):
    """
    Constrói Solid 3D prismático ou metálico (U, C/Ue, Metalon, Perfil I) ao longo do eixo p1 -> p2.
    """
    vetor = p2 - p1
    comp = vetor.GetLength()
    if comp < 0.02 * FT or largura_ft < 1e-4 or altura_ft < 1e-4:
        return None

    eixo = vetor.Normalize()
    w_vec = dir_largura - eixo * dir_largura.DotProduct(eixo)
    if w_vec.GetLength() < 1e-6:
        aux = XYZ.BasisX if abs(eixo.X) < 0.9 else XYZ.BasisY
        w_vec = eixo.CrossProduct(aux)
    w_vec = w_vec.Normalize()
    h_vec = eixo.CrossProduct(w_vec).Normalize()
    if h_vec.DotProduct(dir_altura) < 0:
        h_vec = -h_vec
        w_vec = h_vec.CrossProduct(eixo).Normalize()

    centro = (p1 - h_vec * (altura_ft * 0.5)) if topo_em_p1_p2 else p1
    loops_2d = _loops_perfil_2d(largura_ft, altura_ft, e_ft, tipo_perfil)

    loops = List[CurveLoop]()
    for poly in loops_2d:
        pts_3d = [centro + w_vec * px + h_vec * py for (px, py) in poly]
        curves = List[Curve]()
        n = len(pts_3d)
        for i in range(n):
            curves.Add(Line.CreateBound(pts_3d[i], pts_3d[(i + 1) % n]))
        loops.Add(CurveLoop.Create(curves))

    solid_opt = SolidOptions(
        mat_id if mat_id else ElementId.InvalidElementId,
        ElementId.InvalidElementId
    )
    return GeometryCreationUtilities.CreateExtrusionGeometry(loops, eixo, comp, solid_opt)


XAML_PONTALETES = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Estrutura de Cobertura: Pontaletes (Madeira / Metálico)"
        Width="500" SizeToContent="Height"
        WindowStartupLocation="CenterScreen" ResizeMode="NoResize"
        Background="#F3F3F3" FontFamily="Segoe UI" FontSize="12">
    <StackPanel Margin="18">
        <Border Background="#E3EEF9" BorderBrush="#0078D4" BorderThickness="0,0,0,2"
                Padding="10,8" Margin="0,0,0,10">
            <TextBlock TextWrapping="Wrap" FontWeight="SemiBold" Foreground="#1F3A57"
                       Text="Gera pontaletes verticais chanfrados no ângulo da água até a altura do caibro/terça, subindo a partir da altura do menor pontalete."/>
        </Border>

        <Border Background="White" BorderBrush="#D0D7DE" BorderThickness="1" CornerRadius="4" Padding="10" Margin="0,0,0,8">
            <StackPanel>
                <TextBlock Text="Material e Perfil Estrutural" FontWeight="SemiBold" Margin="0,0,0,6"/>
                <StackPanel Orientation="Horizontal" Margin="0,0,0,6">
                    <RadioButton x:Name="rb_mad" Content="Madeira (Berço + Pontalete + Terça)" GroupName="MatPont" Margin="0,0,16,0" IsChecked="True"/>
                    <RadioButton x:Name="rb_met" Content="Metálico (Aço Galvanizado)" GroupName="MatPont"/>
                </StackPanel>
                <Grid x:Name="pn_metal" Margin="0,4,0,0">
                    <Grid.ColumnDefinitions>
                        <ColumnDefinition Width="155"/>
                        <ColumnDefinition Width="*"/>
                        <ColumnDefinition Width="80"/>
                        <ColumnDefinition Width="55"/>
                    </Grid.ColumnDefinitions>
                    <TextBlock Grid.Column="0" Text="Perfil metálico:" VerticalAlignment="Center"/>
                    <ComboBox Grid.Column="1" x:Name="cb_perfil" Height="24" Margin="0,0,8,0"/>
                    <TextBlock Grid.Column="2" Text="Chapa (mm):" VerticalAlignment="Center" HorizontalAlignment="Right" Margin="0,0,4,0"/>
                    <TextBox Grid.Column="3" x:Name="tb_chapa" Height="24" Text="2.0" VerticalContentAlignment="Center"/>
                </Grid>
            </StackPanel>
        </Border>

        <Border Background="White" BorderBrush="#D0D7DE" BorderThickness="1" CornerRadius="4" Padding="10" Margin="0,0,0,8">
            <Grid>
                <Grid.ColumnDefinitions>
                    <ColumnDefinition Width="265"/>
                    <ColumnDefinition Width="*"/>
                </Grid.ColumnDefinitions>
                <Grid.RowDefinitions>
                    <RowDefinition Height="28"/>
                    <RowDefinition Height="28"/>
                    <RowDefinition Height="28"/>
                    <RowDefinition Height="28"/>
                    <RowDefinition Height="28"/>
                    <RowDefinition Height="26"/>
                    <RowDefinition Height="28"/>
                    <RowDefinition Height="26"/>
                    <RowDefinition Height="28"/>
                </Grid.RowDefinitions>

                <TextBlock Grid.Row="0" Grid.Column="0" Text="Espaçamento entre Linhas / Terças (cm):" VerticalAlignment="Center"/>
                <TextBox   Grid.Row="0" Grid.Column="1" x:Name="tb_lin" Height="24" Text="150" VerticalContentAlignment="Center"/>

                <TextBlock Grid.Row="1" Grid.Column="0" Text="Espaçamento entre Pontaletes (cm):" VerticalAlignment="Center"/>
                <TextBox   Grid.Row="1" Grid.Column="1" x:Name="tb_esp" Height="24" Text="150" VerticalContentAlignment="Center"/>

                <TextBlock Grid.Row="2" Grid.Column="0" Text="Recuo das bordas / beiral (cm):" VerticalAlignment="Center"/>
                <TextBox   Grid.Row="2" Grid.Column="1" x:Name="tb_recuo" Height="24" Text="60" VerticalContentAlignment="Center"/>

                <TextBlock Grid.Row="3" Grid.Column="0" Text="Altura do menor pontalete (cm):" VerticalAlignment="Center" FontWeight="SemiBold"/>
                <TextBox   Grid.Row="3" Grid.Column="1" x:Name="tb_h_min" Height="24" Text="15" VerticalContentAlignment="Center"/>

                <TextBlock Grid.Row="4" Grid.Column="0" Text="Seção do Pontalete — b x h (cm):" VerticalAlignment="Center"/>
                <StackPanel Grid.Row="4" Grid.Column="1" Orientation="Horizontal" VerticalAlignment="Center">
                    <TextBox x:Name="tb_b" Width="56" Height="24" Text="6" VerticalContentAlignment="Center"/>
                    <TextBlock Text=" x " VerticalAlignment="Center" Margin="4,0"/>
                    <TextBox x:Name="tb_h" Width="56" Height="24" Text="12" VerticalContentAlignment="Center"/>
                </StackPanel>

                <CheckBox Grid.Row="5" Grid.Column="0" Grid.ColumnSpan="2" x:Name="ck_tercas"
                          Content="Criar Terças / Longarinas apoiadas nos Pontaletes" IsChecked="True" VerticalAlignment="Center"/>

                <TextBlock Grid.Row="6" Grid.Column="0" Text="Seção da Terça — b x h (cm):" VerticalAlignment="Center"/>
                <StackPanel Grid.Row="6" Grid.Column="1" Orientation="Horizontal" VerticalAlignment="Center">
                    <TextBox x:Name="tb_tb" Width="56" Height="24" Text="6" VerticalContentAlignment="Center"/>
                    <TextBlock Text=" x " VerticalAlignment="Center" Margin="4,0"/>
                    <TextBox x:Name="tb_th" Width="56" Height="24" Text="12" VerticalContentAlignment="Center"/>
                </StackPanel>

                <StackPanel Grid.Row="7" Grid.Column="0" Grid.ColumnSpan="2" Orientation="Horizontal" VerticalAlignment="Center">
                    <CheckBox x:Name="ck_auto_t" Content="Sincronizar desconto até o Caibro" IsChecked="True" VerticalAlignment="Center"/>
                    <TextBlock x:Name="tb_t_info" FontSize="11" Foreground="#555555" VerticalAlignment="Center" Margin="6,0,0,0"/>
                </StackPanel>

                <TextBlock Grid.Row="8" Grid.Column="0" Text="Desconto até o Caibro (Ripa + Caibro) (cm):" VerticalAlignment="Center"/>
                <TextBox   Grid.Row="8" Grid.Column="1" x:Name="tb_desc" Height="24" Text="7" VerticalContentAlignment="Center"/>
            </Grid>
        </Border>

        <CheckBox x:Name="ck_berco" Content="Criar Berço (madeira 4cm) / Sapata metálica (aço 8mm) na base"
                  IsChecked="True" Margin="0,0,0,8"/>

        <Grid Margin="0,0,0,12">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="165"/>
                <ColumnDefinition Width="*"/>
            </Grid.ColumnDefinitions>
            <TextBlock Grid.Column="0" Text="Agrupamento (Model Group):" VerticalAlignment="Center"/>
            <ComboBox  Grid.Column="1" x:Name="cb_grp" Height="24"/>
        </Grid>

        <StackPanel Orientation="Horizontal" HorizontalAlignment="Right">
            <Button x:Name="btn_cancel" Content="Cancelar" Width="100" Height="30" Margin="0,0,8,0"/>
            <Button x:Name="btn_ok" Content="Gerar Pontaletes" Width="140" Height="30"
                    Background="#0078D4" Foreground="White" FontWeight="SemiBold"/>
        </StackPanel>
    </StackPanel>
</Window>
"""


class PontaletesWindow(forms.WPFWindow):
    def __init__(self, prefs):
        forms.WPFWindow.__init__(self, XAML_PONTALETES, literal_string=True)
        self.confirmado = False
        self.resultado  = {}

        self._rb_mad    = self.FindName("rb_mad")
        self._rb_met    = self.FindName("rb_met")
        self._pn_metal  = self.FindName("pn_metal")
        self._cb_perfil = self.FindName("cb_perfil")
        self._tb_chapa  = self.FindName("tb_chapa")

        self._tb_lin    = self.FindName("tb_lin")
        self._tb_esp    = self.FindName("tb_esp")
        self._tb_recuo  = self.FindName("tb_recuo")
        self._tb_h_min  = self.FindName("tb_h_min")
        self._tb_b      = self.FindName("tb_b")
        self._tb_h      = self.FindName("tb_h")
        self._ck_tercas = self.FindName("ck_tercas")
        self._tb_tb     = self.FindName("tb_tb")
        self._tb_th     = self.FindName("tb_th")
        self._ck_auto_t = self.FindName("ck_auto_t")
        self._tb_t_info = self.FindName("tb_t_info")
        self._tb_desc   = self.FindName("tb_desc")
        self._ck_berco  = self.FindName("ck_berco")
        self._cb_grp    = self.FindName("cb_grp")

        self._cb_perfil.Items.Add(u"Perfil U Enrijecido (C)")
        self._cb_perfil.Items.Add(u"Perfil U Simples")
        self._cb_perfil.Items.Add(u"Tubo Metalon")
        self._cb_perfil.SelectedIndex = max(0, min(2, int(prefs.get("pont_perfil_idx", 0) or 0)))

        self._cb_grp.Items.Add(u"Grupo único para todo o telhado (Recomendado)")
        self._cb_grp.Items.Add(u"Por linha de Pontaletes")
        self._cb_grp.Items.Add(u"Sem agrupamento (peças soltas)")
        self._cb_grp.SelectedIndex = max(0, min(2, int(prefs.get("pont_grp_idx", 0) or 0)))

        is_met = (prefs.get("pont_mat", "madeira") == "metal")
        self._rb_met.IsChecked = is_met
        self._rb_mad.IsChecked = not is_met
        self._tb_chapa.Text = str(prefs.get("pont_chapa", "2.0"))

        self._tb_lin.Text   = str(prefs.get("pont_esp_linhas", prefs.get("terca_esp", "150")))
        self._tb_esp.Text   = str(prefs.get("pont_esp_pont", "150"))
        self._tb_recuo.Text = str(prefs.get("pont_recuo", prefs.get("terca_recuo", "60")))
        self._tb_h_min.Text = str(prefs.get("pont_h_min", "15"))
        self._tb_b.Text     = str(prefs.get("pont_b", "6"))
        self._tb_h.Text     = str(prefs.get("pont_h", "12"))
        self._ck_tercas.IsChecked = bool(prefs.get("pont_tercas", True))
        self._tb_tb.Text    = str(prefs.get("pont_tb", prefs.get("terca_b", "6")))
        self._tb_th.Text    = str(prefs.get("pont_th", prefs.get("terca_h", "12")))
        self._ck_berco.IsChecked = bool(prefs.get("pont_berco", True))

        self._soma_sem_terca = round(
            (float(prefs.get("ripa_h") or 2.0) if prefs.get("ripas", True) else 0.0) +
            (float(prefs.get("caibro_h") or 5.0) if prefs.get("caibros", True) else 0.0), 2
        )
        self._ck_auto_t.IsChecked = bool(prefs.get("pont_auto_trama", True))
        self._desc_manual = str(prefs.get("pont_desconto", self._soma_sem_terca))

        self._rb_mad.Checked    += self._atualizar_ui
        self._rb_met.Checked    += self._atualizar_ui
        self._ck_tercas.Checked   += self._atualizar_ui
        self._ck_tercas.Unchecked += self._atualizar_ui
        self._ck_auto_t.Checked   += self._atualizar_ui
        self._ck_auto_t.Unchecked += self._atualizar_ui
        self._atualizar_ui(None, None)

        self.FindName("btn_ok").Click     += self.on_ok
        self.FindName("btn_cancel").Click += self.on_cancel

    def _atualizar_ui(self, sender, args):
        is_met = bool(self._rb_met.IsChecked)
        self._pn_metal.Visibility = WpfVisibility.Visible if is_met else WpfVisibility.Collapsed

        criar_t = bool(self._ck_tercas.IsChecked)
        self._tb_tb.IsEnabled = criar_t
        self._tb_th.IsEnabled = criar_t

        alvo_trama = self._soma_sem_terca
        self._tb_t_info.Text = u"(Ripa + Caibro = {:g} cm)".format(alvo_trama)
        if self._ck_auto_t.IsChecked:
            self._tb_desc.Text = u"{:g}".format(alvo_trama)
            self._tb_desc.IsEnabled = False
        else:
            self._tb_desc.IsEnabled = True
            if not self._tb_desc.Text:
                self._tb_desc.Text = self._desc_manual

    def on_cancel(self, sender, args):
        self.confirmado = False
        self.Close()

    def on_ok(self, sender, args):
        try:
            res = {
                "pont_mat":        "metal" if self._rb_met.IsChecked else "madeira",
                "pont_perfil_idx": max(0, self._cb_perfil.SelectedIndex),
                "pont_chapa":      max(0.5, float(self._tb_chapa.Text.replace(",", "."))),
                "pont_esp_linhas": max(20.0, float(self._tb_lin.Text.replace(",", "."))),
                "pont_esp_pont":   max(20.0, float(self._tb_esp.Text.replace(",", "."))),
                "pont_recuo":      max(0.0, float(self._tb_recuo.Text.replace(",", "."))),
                "pont_h_min":      max(2.0, float(self._tb_h_min.Text.replace(",", "."))),
                "pont_b":          max(1.5, float(self._tb_b.Text.replace(",", "."))),
                "pont_h":          max(1.5, float(self._tb_h.Text.replace(",", "."))),
                "pont_tercas":     bool(self._ck_tercas.IsChecked),
                "pont_tb":         max(1.5, float(self._tb_tb.Text.replace(",", "."))),
                "pont_th":         max(1.5, float(self._tb_th.Text.replace(",", "."))),
                "pont_auto_trama": bool(self._ck_auto_t.IsChecked),
                "pont_desconto":   max(0.0, float(self._tb_desc.Text.replace(",", "."))),
                "pont_berco":      bool(self._ck_berco.IsChecked),
                "pont_grp_idx":    self._cb_grp.SelectedIndex,
            }
        except Exception:
            forms.alert("Informe apenas valores numéricos válidos.")
            return

        self.resultado = res
        self.confirmado = True
        self.Close()


def run_pontaletes():
    global doc, uidoc, view
    uidoc = __revit__.ActiveUIDocument
    doc   = uidoc.Document
    view  = doc.ActiveView

    prefs = carregar_preferencias()
    win = PontaletesWindow(prefs)
    win.ShowDialog()
    if not win.confirmado:
        script.exit()

    op = win.resultado
    salvar_preferencias(op)

    roofs = selecionar_telhados()

    is_metal      = (op["pont_mat"] == "metal")
    perfis_map    = ("u_enrijecido", "u_simples", "metalon")
    tipo_perfil   = perfis_map[min(2, op["pont_perfil_idx"])] if is_metal else "retangular"
    e_ft          = (op["pont_chapa"] * 0.1) / 30.48
    esp_linhas_ft = op["pont_esp_linhas"] / 30.48
    esp_pont_ft   = op["pont_esp_pont"] / 30.48
    recuo_ft      = op["pont_recuo"] / 30.48
    h_min_ft      = op.get("pont_h_min", 15.0) / 30.48
    pont_b_ft     = op["pont_b"] / 30.48
    pont_h_ft     = op["pont_h"] / 30.48
    terca_b_ft    = op["pont_tb"] / 30.48
    terca_h_ft    = op["pont_th"] / 30.48
    desconto_ft   = op["pont_desconto"] / 30.48
    criar_tercas  = bool(op["pont_tercas"])
    criar_berco   = bool(op["pont_berco"])
    off_sob_terca = desconto_ft + (terca_h_ft if criar_tercas else 0.0)
    h_pad         = (0.008 / 0.3048 if is_metal else 0.04 / 0.3048) if criar_berco else 0.0

    ids_criados = []
    grupos_linhas = []
    qtd_pont    = 0
    qtd_bercos  = 0
    qtd_tercas  = 0
    pontos_usados = []  # (X, Y, Z_topo) para evitar pontaletes duplicados na cumeeira
    prefix_mat = u"Metálico" if is_metal else u"Madeira"

    with TransactionGroup(doc, "Madeiramento: Pontaletes") as tg:
        tg.Start()
        with Transaction(doc, "Madeiramento: Pontaletes") as t:
            t.Start()
            mat_id = obter_ou_criar_material_aco_galv() if is_metal else obter_ou_criar_material_madeira()

            linha_idx = 0
            for roof in roofs:
                aguas = extrair_faces_aguas(roof, usar_face_inferior=True)
                if not aguas:
                    aguas = extrair_faces_aguas(roof, usar_face_inferior=False)
                if not aguas:
                    continue

                # 1ª passagem: coletar a menor cota Z real dos topos dos pontaletes neste telhado
                # para posicionar a base horizontal exatamente a h_min_ft abaixo do menor pontalete
                zs_pont_roof = []
                meio_chanfro_max = 0.02 * FT
                for a in aguas:
                    cos_a = max(0.2, min(1.0, abs(a["n_up"].Z)))
                    tan_a = math.sqrt(max(0.0, 1.0 - cos_a * cos_a)) / cos_a
                    meio_chanfro_max = max(meio_chanfro_max, 0.5 * pont_h_ft * tan_a)
                    rec_v = (recuo_ft / cos_a) if recuo_ft > 1e-5 else 0.15 * FT
                    v_i = min(a["v_max"] - 0.15 * FT, a["v_min"] + rec_v)
                    v_f = max(v_i, a["v_max"] - 0.15 * FT)
                    for vk_p in gerar_posicoes_uniformes(v_i, v_f, esp_linhas_ft, 0.0):
                        for u_i, u_f in recortar_linha_constante_v(a["loops_uv"], vk_p):
                            sp_u = u_f - u_i
                            mg_u = recuo_ft if sp_u > 2.0 * recuo_ft + 0.5 * FT else min(0.25 * sp_u, 0.25 * FT)
                            for uk_p in gerar_posicoes_uniformes(u_i, u_f, esp_pont_ft, mg_u):
                                zs_pont_roof.append(ponto_3d_agua(a, uk_p, vk_p, off_sob_terca).Z)

                if not zs_pont_roof:
                    continue
                z_min_pont    = min(zs_pont_roof)
                h_min_efetivo = max(h_min_ft, meio_chanfro_max + 0.05 * FT)
                z_pont_ini    = z_min_pont - h_min_efetivo
                z_base_pad    = z_pont_ini - h_pad

                for agua in aguas:
                    u_horiz  = agua["u_horiz"]
                    v_slope  = agua["v_slope"]
                    n_up     = agua["n_up"]
                    loops_uv = agua["loops_uv"]
                    cos_agua = max(0.2, min(1.0, abs(n_up.Z)))
                    tan_agua = math.sqrt(max(0.0, 1.0 - cos_agua * cos_agua)) / cos_agua

                    v_min, v_max = agua["v_min"], agua["v_max"]
                    recuo_v = (recuo_ft / cos_agua) if recuo_ft > 1e-5 else 0.15 * FT
                    v_ini = min(v_max - 0.15 * FT, v_min + recuo_v)
                    v_fim = max(v_ini, v_max - 0.15 * FT)
                    vs_linhas = gerar_posicoes_uniformes(v_ini, v_fim, esp_linhas_ft, 0.0)

                    v_slope_xy = XYZ(v_slope.X, v_slope.Y, 0.0)
                    if v_slope_xy.GetLength() > 1e-6:
                        v_slope_xy = v_slope_xy.Normalize()
                    else:
                        v_slope_xy = XYZ(-u_horiz.Y, u_horiz.X, 0.0).Normalize()

                    for vk in vs_linhas:
                        segs = recortar_linha_constante_v(loops_uv, vk)
                        if not segs:
                            continue
                        linha_idx += 1
                        ids_linha = []

                        for u_ini, u_fim in segs:
                            if criar_tercas:
                                pt_t1 = ponto_3d_agua(agua, u_ini, vk, desconto_ft)
                                pt_t2 = ponto_3d_agua(agua, u_fim, vk, desconto_ft)
                                sol_t = gerar_solido_perfil(
                                    pt_t1, pt_t2, v_slope, n_up, terca_b_ft, terca_h_ft,
                                    topo_em_p1_p2=True, tipo_perfil=tipo_perfil, e_ft=e_ft, mat_id=mat_id
                                )
                                if sol_t:
                                    ds_t = instanciar_solido_madeira(
                                        sol_t, u"Terça {} {:.0f}x{:.0f}cm".format(prefix_mat, op["pont_tb"], op["pont_th"])
                                    )
                                    if ds_t:
                                        ids_linha.append(ds_t.Id)
                                        qtd_tercas += 1

                            span_u = u_fim - u_ini
                            margem_u = recuo_ft if span_u > 2.0 * recuo_ft + 0.5 * FT else min(0.25 * span_u, 0.25 * FT)
                            us_pont = gerar_posicoes_uniformes(u_ini, u_fim, esp_pont_ft, margem_u)

                            cut_span = max(pont_b_ft, pont_h_ft, terca_b_ft) * 3.0 + 0.5 * FT

                            for uk in us_pont:
                                p_sob = ponto_3d_agua(agua, uk, vk, off_sob_terca)

                                duplicado = False
                                tol_xy = max(pont_b_ft, pont_h_ft) * 1.15
                                for (px0, py0, pz0) in pontos_usados:
                                    if math.hypot(p_sob.X - px0, p_sob.Y - py0) < tol_xy and abs(p_sob.Z - pz0) < 0.6 * FT:
                                        duplicado = True
                                        break
                                if duplicado:
                                    continue

                                pontos_usados.append((p_sob.X, p_sob.Y, p_sob.Z))

                                if criar_berco:
                                    if is_metal:
                                        b_pad = pont_b_ft + 0.05 / 0.3048
                                        l_pad = pont_h_ft + 0.05 / 0.3048
                                        c_pad = u"Sapata Metálica Pontalete"
                                    else:
                                        b_pad = max(pont_b_ft + 0.02 / 0.3048, 0.08 / 0.3048)
                                        l_pad = max(0.35 / 0.3048, pont_h_ft + 0.16 / 0.3048)
                                        c_pad = u"Berço Madeira Pontalete"
                                    sol_pad = gerar_solido_prisma(
                                        XYZ(p_sob.X, p_sob.Y, z_base_pad + h_pad) - v_slope_xy * (l_pad * 0.5),
                                        XYZ(p_sob.X, p_sob.Y, z_base_pad + h_pad) + v_slope_xy * (l_pad * 0.5),
                                        u_horiz, XYZ.BasisZ, b_pad, h_pad,
                                        topo_em_p1_p2=True, mat_id=mat_id
                                    )
                                    ds_pad = instanciar_solido_madeira(sol_pad, c_pad)
                                    if ds_pad:
                                        ids_linha.append(ds_pad.Id)
                                        qtd_bercos += 1

                                # Topo bruto apenas o suficiente para cobrir o chanfro inclinado em p_sob
                                z_pont_ext = p_sob.Z + 0.5 * pont_h_ft * tan_agua + 0.10 * FT
                                sol_p = gerar_solido_perfil(
                                    XYZ(p_sob.X, p_sob.Y, z_pont_ini),
                                    XYZ(p_sob.X, p_sob.Y, z_pont_ext),
                                    u_horiz, v_slope_xy, pont_b_ft, pont_h_ft,
                                    topo_em_p1_p2=False, tipo_perfil=tipo_perfil, e_ft=e_ft, mat_id=mat_id
                                )
                                if sol_p:
                                    # Bloco de corte partindo EXATAMENTE do plano (p_sob, n_up) para cima (+n_up)
                                    sol_teto = gerar_solido_prisma(
                                        p_sob - u_horiz * cut_span, p_sob + u_horiz * cut_span,
                                        v_slope, -n_up, cut_span * 2.0, 3.0 * FT,
                                        topo_em_p1_p2=True, mat_id=mat_id
                                    )
                                    sol_p = subtrair_solido_seguro(sol_p, sol_teto)
                                    ds_p = instanciar_solido_madeira(
                                        sol_p, u"Pontalete {} {:.0f}x{:.0f}cm".format(prefix_mat, op["pont_b"], op["pont_h"])
                                    )
                                    if ds_p:
                                        ids_linha.append(ds_p.Id)
                                        qtd_pont += 1

                        ids_criados.extend(ids_linha)
                        if op["pont_grp_idx"] == 1 and len(ids_linha) > 1:
                            grupos_linhas.append((ids_linha, u"Pontaletes Linha {:02d}".format(linha_idx)))

            t.Commit()

        grupos_pendentes = []
        if op["pont_grp_idx"] == 0 and len(ids_criados) > 1:
            grupos_pendentes.append((ids_criados, u"Estrutura - Pontaletes ({})".format(prefix_mat)))
        elif op["pont_grp_idx"] == 1:
            grupos_pendentes.extend(grupos_linhas)
        agrupar_lote_em_transacao(grupos_pendentes, "Agrupar Pontaletes")
        tg.Assimilate()

    msg = u"Pontaletes concluídos ({})!\n\n✔ Pontaletes: {}\n".format(prefix_mat, qtd_pont)
    if criar_berco:
        msg += u"✔ {}s: {}\n".format(u"Sapatas" if is_metal else u"Berços", qtd_bercos)
    if criar_tercas:
        msg += u"✔ Terças: {}\n".format(qtd_tercas)
    forms.alert(msg, title="Estrutura de Cobertura: Pontaletes")


# =============================================================================
# COMANDO 5 E 6: ESTRUTURA METÁLICA DE COBERTURA (3D E VIDRAÇA INCLINADA)
# =============================================================================
XAML_ESTRUTURA_METALICA = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Estrutura Metálica de Cobertura (Vigas I + Terças C)"
        Width="550" SizeToContent="Height"
        WindowStartupLocation="CenterScreen" ResizeMode="NoResize"
        Background="#F3F3F3" FontFamily="Segoe UI" FontSize="12">
    <StackPanel Margin="18">
        <Border Background="#E3EEF9" BorderBrush="#0078D4" BorderThickness="0,0,0,2"
                Padding="10,8" Margin="0,0,0,10">
            <TextBlock TextWrapping="Wrap" FontWeight="SemiBold" Foreground="#1F3A57"
                       Text="Gera a estrutura metálica do telhado com Terças horizontais (Perfil C / U / Metalon) apoiadas sobre Vigas Principais no sentido do caimento (Perfil I / W / C)."/>
        </Border>

        <Border Background="White" BorderBrush="#D0D7DE" BorderThickness="1" CornerRadius="4" Padding="10" Margin="0,0,0,8">
            <StackPanel>
                <CheckBox x:Name="ck_met_tercas" Content="1. Terças Metálicas (horizontais, paralelas ao beiral/cumeeira)"
                          FontWeight="SemiBold" IsChecked="True" Margin="0,0,0,6"/>
                <Grid x:Name="pn_met_tercas">
                    <Grid.ColumnDefinitions>
                        <ColumnDefinition Width="230"/>
                        <ColumnDefinition Width="*"/>
                    </Grid.ColumnDefinitions>
                    <Grid.RowDefinitions>
                        <RowDefinition Height="28"/>
                        <RowDefinition Height="28"/>
                        <RowDefinition Height="28"/>
                        <RowDefinition Height="28"/>
                    </Grid.RowDefinitions>

                    <TextBlock Grid.Row="0" Grid.Column="0" Text="Perfil Comercial da Terça:" VerticalAlignment="Center"/>
                    <ComboBox  Grid.Row="0" Grid.Column="1" x:Name="cb_met_t_perfil" Height="24"/>

                    <TextBlock Grid.Row="1" Grid.Column="0" Text="Seção b x h (cm) e Chapa (mm):" VerticalAlignment="Center"/>
                    <StackPanel Grid.Row="1" Grid.Column="1" Orientation="Horizontal" VerticalAlignment="Center">
                        <TextBox x:Name="tb_met_tb" Width="46" Height="24" Text="5" VerticalContentAlignment="Center"/>
                        <TextBlock Text=" x " VerticalAlignment="Center" Margin="2,0"/>
                        <TextBox x:Name="tb_met_th" Width="46" Height="24" Text="10" VerticalContentAlignment="Center"/>
                        <TextBlock Text=" cm | e=" VerticalAlignment="Center" Margin="4,0"/>
                        <TextBox x:Name="tb_met_te" Width="44" Height="24" Text="2.0" VerticalContentAlignment="Center"/>
                        <TextBlock Text=" mm" VerticalAlignment="Center" Margin="2,0,0,0"/>
                    </StackPanel>

                    <TextBlock Grid.Row="2" Grid.Column="0" Text="Espaçamento máx. entre Terças (cm):" VerticalAlignment="Center"/>
                    <TextBox   Grid.Row="2" Grid.Column="1" x:Name="tb_met_tesp" Height="24" Text="140" VerticalContentAlignment="Center"/>

                    <TextBlock Grid.Row="3" Grid.Column="0" Text="Recuo do Beiral / Frechal (cm):" VerticalAlignment="Center"/>
                    <TextBox   Grid.Row="3" Grid.Column="1" x:Name="tb_met_trec" Height="24" Text="30" VerticalContentAlignment="Center"/>
                </Grid>
            </StackPanel>
        </Border>

        <Border Background="White" BorderBrush="#D0D7DE" BorderThickness="1" CornerRadius="4" Padding="10" Margin="0,0,0,8">
            <StackPanel>
                <CheckBox x:Name="ck_met_vigas" Content="2. Vigas Principais (inclinadas no caimento, sob as Terças)"
                          FontWeight="SemiBold" IsChecked="True" Margin="0,0,0,6"/>
                <Grid x:Name="pn_met_vigas">
                    <Grid.ColumnDefinitions>
                        <ColumnDefinition Width="230"/>
                        <ColumnDefinition Width="*"/>
                    </Grid.ColumnDefinitions>
                    <Grid.RowDefinitions>
                        <RowDefinition Height="28"/>
                        <RowDefinition Height="28"/>
                        <RowDefinition Height="28"/>
                        <RowDefinition Height="28"/>
                    </Grid.RowDefinitions>

                    <TextBlock Grid.Row="0" Grid.Column="0" Text="Perfil Comercial da Viga:" VerticalAlignment="Center"/>
                    <ComboBox  Grid.Row="0" Grid.Column="1" x:Name="cb_met_v_perfil" Height="24"/>

                    <TextBlock Grid.Row="1" Grid.Column="0" Text="Seção b x h (cm) e Alma/Mesa (mm):" VerticalAlignment="Center"/>
                    <StackPanel Grid.Row="1" Grid.Column="1" Orientation="Horizontal" VerticalAlignment="Center">
                        <TextBox x:Name="tb_met_vb" Width="46" Height="24" Text="10" VerticalContentAlignment="Center"/>
                        <TextBlock Text=" x " VerticalAlignment="Center" Margin="2,0"/>
                        <TextBox x:Name="tb_met_vh" Width="46" Height="24" Text="15" VerticalContentAlignment="Center"/>
                        <TextBlock Text=" cm | e=" VerticalAlignment="Center" Margin="4,0"/>
                        <TextBox x:Name="tb_met_ve" Width="44" Height="24" Text="6.0" VerticalContentAlignment="Center"/>
                        <TextBlock Text=" mm" VerticalAlignment="Center" Margin="2,0,0,0"/>
                    </StackPanel>

                    <TextBlock Grid.Row="2" Grid.Column="0" Text="Espaçamento máx. entre Vigas (cm):" VerticalAlignment="Center"/>
                    <TextBox   Grid.Row="2" Grid.Column="1" x:Name="tb_met_vesp" Height="24" Text="250" VerticalContentAlignment="Center"/>

                    <TextBlock Grid.Row="3" Grid.Column="0" Text="Recuo das Bordas Laterais (cm):" VerticalAlignment="Center"/>
                    <TextBox   Grid.Row="3" Grid.Column="1" x:Name="tb_met_vrec" Height="24" Text="15" VerticalContentAlignment="Center"/>
                </Grid>
            </StackPanel>
        </Border>

        <Border Background="White" BorderBrush="#D0D7DE" BorderThickness="1" CornerRadius="4" Padding="10" Margin="0,0,0,12">
            <Grid>
                <Grid.ColumnDefinitions>
                    <ColumnDefinition Width="230"/>
                    <ColumnDefinition Width="*"/>
                </Grid.ColumnDefinitions>
                <Grid.RowDefinitions>
                    <RowDefinition Height="28"/>
                    <RowDefinition Height="28"/>
                    <RowDefinition Height="28"/>
                </Grid.RowDefinitions>

                <TextBlock Grid.Row="0" Grid.Column="0" Text="Referência no Telhado:" VerticalAlignment="Center"/>
                <ComboBox  Grid.Row="0" Grid.Column="1" x:Name="cb_met_face" Height="24"/>

                <TextBlock Grid.Row="1" Grid.Column="0" Text="Folga / Desconto acima das Terças (cm):" VerticalAlignment="Center"/>
                <TextBox   Grid.Row="1" Grid.Column="1" x:Name="tb_met_folga" Height="24" Text="0" VerticalContentAlignment="Center"/>

                <TextBlock Grid.Row="2" Grid.Column="0" Text="Agrupamento (Model Group):" VerticalAlignment="Center"/>
                <ComboBox  Grid.Row="2" Grid.Column="1" x:Name="cb_met_grp" Height="24"/>
            </Grid>
        </Border>

        <StackPanel Orientation="Horizontal" HorizontalAlignment="Right">
            <Button x:Name="btn_cancel" Content="Cancelar" Width="100" Height="30" Margin="0,0,8,0"/>
            <Button x:Name="btn_ok" Content="Gerar Estrutura Metálica" Width="175" Height="30"
                    Background="#0078D4" Foreground="White" FontWeight="SemiBold"/>
        </StackPanel>
    </StackPanel>
</Window>
"""


class EstruturaMetalicaWindow(forms.WPFWindow):
    def __init__(self, prefs):
        forms.WPFWindow.__init__(self, XAML_ESTRUTURA_METALICA, literal_string=True)
        self.confirmado = False
        self.resultado  = {}

        self._ck_tercas   = self.FindName("ck_met_tercas")
        self._pn_tercas   = self.FindName("pn_met_tercas")
        self._cb_t_perfil = self.FindName("cb_met_t_perfil")
        self._tb_tb       = self.FindName("tb_met_tb")
        self._tb_th       = self.FindName("tb_met_th")
        self._tb_te       = self.FindName("tb_met_te")
        self._tb_tesp     = self.FindName("tb_met_tesp")
        self._tb_trec     = self.FindName("tb_met_trec")

        self._ck_vigas    = self.FindName("ck_met_vigas")
        self._pn_vigas    = self.FindName("pn_met_vigas")
        self._cb_v_perfil = self.FindName("cb_met_v_perfil")
        self._tb_vb       = self.FindName("tb_met_vb")
        self._tb_vh       = self.FindName("tb_met_vh")
        self._tb_ve       = self.FindName("tb_met_ve")
        self._tb_vesp     = self.FindName("tb_met_vesp")
        self._tb_vrec     = self.FindName("tb_met_vrec")

        self._cb_face     = self.FindName("cb_met_face")
        self._tb_folga    = self.FindName("tb_met_folga")
        self._cb_grp      = self.FindName("cb_met_grp")

        perfis_terca = (
            u"Perfil U Enrijecido (C) — Comercial",
            u"Perfil U Simples — Comercial",
            u"Tubo Metalon Retangular",
            u"Perfil I / W Laminado",
        )
        for p in perfis_terca:
            self._cb_t_perfil.Items.Add(p)
        self._cb_t_perfil.SelectedIndex = max(0, min(3, int(prefs.get("met_t_perfil_idx", 0) or 0)))

        perfis_viga = (
            u"Perfil I / W Laminado (Aço)",
            u"Perfil U Enrijecido (C)",
            u"Tubo Metalon Retangular",
            u"Perfil U Simples",
        )
        for p in perfis_viga:
            self._cb_v_perfil.Items.Add(p)
        self._cb_v_perfil.SelectedIndex = max(0, min(3, int(prefs.get("met_v_perfil_idx", 0) or 0)))

        self._cb_face.Items.Add(u"Face Inferior do Telhado (Sob a telha)")
        self._cb_face.Items.Add(u"Face Superior do Telhado")
        self._cb_face.SelectedIndex = max(0, min(1, int(prefs.get("met_face_idx", 0) or 0)))

        self._cb_grp.Items.Add(u"Grupo único para toda a Estrutura Metálica (Recomendado)")
        self._cb_grp.Items.Add(u"Separar grupos por camada (Terças / Vigas)")
        self._cb_grp.Items.Add(u"Sem agrupamento (peças soltas)")
        self._cb_grp.SelectedIndex = max(0, min(2, int(prefs.get("met_grp_idx", 0) or 0)))

        self._ck_tercas.IsChecked = bool(prefs.get("met_tercas", True))
        self._tb_tb.Text   = str(prefs.get("met_tb", "5"))
        self._tb_th.Text   = str(prefs.get("met_th", "10"))
        self._tb_te.Text   = str(prefs.get("met_te", "2.0"))
        self._tb_tesp.Text = str(prefs.get("met_tesp", "140"))
        self._tb_trec.Text = str(prefs.get("met_trec", "30"))

        self._ck_vigas.IsChecked = bool(prefs.get("met_vigas", True))
        self._tb_vb.Text   = str(prefs.get("met_vb", "10"))
        self._tb_vh.Text   = str(prefs.get("met_vh", "15"))
        self._tb_ve.Text   = str(prefs.get("met_ve", "6.0"))
        self._tb_vesp.Text = str(prefs.get("met_vesp", "250"))
        self._tb_vrec.Text = str(prefs.get("met_vrec", "15"))

        self._tb_folga.Text = str(prefs.get("met_folga", "0"))

        self._ck_tercas.Checked   += self._atualizar_ui
        self._ck_tercas.Unchecked += self._atualizar_ui
        self._ck_vigas.Checked    += self._atualizar_ui
        self._ck_vigas.Unchecked  += self._atualizar_ui
        self._atualizar_ui(None, None)

        self.FindName("btn_ok").Click     += self.on_ok
        self.FindName("btn_cancel").Click += self.on_cancel

    def _atualizar_ui(self, sender, args):
        self._pn_tercas.IsEnabled = bool(self._ck_tercas.IsChecked)
        self._pn_vigas.IsEnabled  = bool(self._ck_vigas.IsChecked)

    def on_cancel(self, sender, args):
        self.confirmado = False
        self.Close()

    def on_ok(self, sender, args):
        if not self._ck_tercas.IsChecked and not self._ck_vigas.IsChecked:
            forms.alert("Marque pelo menos uma camada (Terças ou Vigas Principais).")
            return
        try:
            res = {
                "met_tercas":       bool(self._ck_tercas.IsChecked),
                "met_t_perfil_idx": max(0, self._cb_t_perfil.SelectedIndex),
                "met_tb":           max(1.5, float(self._tb_tb.Text.replace(",", "."))),
                "met_th":           max(1.5, float(self._tb_th.Text.replace(",", "."))),
                "met_te":           max(0.5, float(self._tb_te.Text.replace(",", "."))),
                "met_tesp":         max(20.0, float(self._tb_tesp.Text.replace(",", "."))),
                "met_trec":         max(0.0, float(self._tb_trec.Text.replace(",", "."))),
                "met_vigas":        bool(self._ck_vigas.IsChecked),
                "met_v_perfil_idx": max(0, self._cb_v_perfil.SelectedIndex),
                "met_vb":           max(1.5, float(self._tb_vb.Text.replace(",", "."))),
                "met_vh":           max(1.5, float(self._tb_vh.Text.replace(",", "."))),
                "met_ve":           max(0.5, float(self._tb_ve.Text.replace(",", "."))),
                "met_vesp":         max(20.0, float(self._tb_vesp.Text.replace(",", "."))),
                "met_vrec":         max(0.0, float(self._tb_vrec.Text.replace(",", "."))),
                "met_face_idx":     max(0, self._cb_face.SelectedIndex),
                "met_folga":        max(0.0, float(self._tb_folga.Text.replace(",", "."))),
                "met_grp_idx":      max(0, self._cb_grp.SelectedIndex),
            }
        except Exception:
            forms.alert("Informe apenas valores numéricos válidos.")
            return

        self.resultado = res
        self.confirmado = True
        self.Close()


def run_estrutura_metalica_3d():
    """Gera a Estrutura Metálica de Cobertura (Terças C/U/Metalon + Vigas I/W/C) em sólidos 3D agrupáveis."""
    global doc, uidoc, view
    uidoc = __revit__.ActiveUIDocument
    doc   = uidoc.Document
    view  = doc.ActiveView

    prefs = carregar_preferencias()
    win = EstruturaMetalicaWindow(prefs)
    win.Title = u"Estrutura Metálica: Vigas e Terças (Peças 3D)"
    win.ShowDialog()
    if not win.confirmado:
        script.exit()

    op = win.resultado
    salvar_preferencias(op)

    roofs = selecionar_telhados()

    map_terca = ("u_enrijecido", "u_simples", "metalon", "perfil_i")
    map_viga  = ("perfil_i", "u_enrijecido", "metalon", "u_simples")
    nomes_t   = (u"Perfil C (Ue)", u"Perfil U", u"Metalon", u"Perfil I")
    nomes_v   = (u"Perfil I/W", u"Perfil C (Ue)", u"Metalon", u"Perfil U")

    t_tipo = map_terca[min(3, op["met_t_perfil_idx"])]
    v_tipo = map_viga[min(3, op["met_v_perfil_idx"])]
    t_nome = nomes_t[min(3, op["met_t_perfil_idx"])]
    v_nome = nomes_v[min(3, op["met_v_perfil_idx"])]

    tb_ft   = op["met_tb"] / 30.48
    th_ft   = op["met_th"] / 30.48
    te_ft   = (op["met_te"] * 0.1) / 30.48
    tesp_ft = op["met_tesp"] / 30.48
    trec_ft = op["met_trec"] / 30.48

    vb_ft   = op["met_vb"] / 30.48
    vh_ft   = op["met_vh"] / 30.48
    ve_ft   = (op["met_ve"] * 0.1) / 30.48
    vesp_ft = op["met_vesp"] / 30.48
    vrec_ft = op["met_vrec"] / 30.48

    folga_ft = op["met_folga"] / 30.48
    off_topo_terca = folga_ft
    off_topo_viga  = folga_ft + (th_ft if op["met_tercas"] else 0.0)
    usar_inferior  = (op["met_face_idx"] == 0)

    ids_tercas = []
    ids_vigas  = []

    with TransactionGroup(doc, "Estrutura Metálica: Vigas e Terças (3D)") as tg:
        tg.Start()
        with Transaction(doc, "Estrutura Metálica: Vigas e Terças (3D)") as t:
            t.Start()
            mat_id = obter_ou_criar_material_aco_galv()

            for roof in roofs:
                aguas = extrair_faces_aguas(roof, usar_face_inferior=usar_inferior)
                if not aguas:
                    aguas = extrair_faces_aguas(roof, usar_face_inferior=not usar_inferior)
                for agua in aguas:
                    u_horiz  = agua["u_horiz"]
                    v_slope  = agua["v_slope"]
                    n_up     = agua["n_up"]
                    loops_uv = agua["loops_uv"]
                    u_min, u_max = agua["u_min"], agua["u_max"]
                    v_min, v_max = agua["v_min"], agua["v_max"]
                    cos_agua = max(0.2, min(1.0, abs(n_up.Z)))

                    if op["met_tercas"]:
                        recuo_v = (trec_ft / cos_agua) if trec_ft > 1e-5 else 0.0
                        v_ini_t = min(v_max, v_min + recuo_v)
                        margem_t = 0.0 if trec_ft > 1e-5 else (tb_ft * 0.6)
                        vs_terca = gerar_posicoes_uniformes(v_ini_t, v_max, tesp_ft, margem_t)
                        for vk in vs_terca:
                            for u_ini, u_fim in recortar_linha_constante_v(loops_uv, vk):
                                p1 = ponto_3d_agua(agua, u_ini, vk, off_topo_terca)
                                p2 = ponto_3d_agua(agua, u_fim, vk, off_topo_terca)
                                sol_t = gerar_solido_perfil(
                                    p1, p2, v_slope, n_up, tb_ft, th_ft,
                                    topo_em_p1_p2=True, tipo_perfil=t_tipo, e_ft=te_ft, mat_id=mat_id
                                )
                                if sol_t:
                                    ds_t = instanciar_solido_madeira(
                                        sol_t,
                                        u"Terça Metálica {} {:.0f}x{:.0f}cm (e={:g}mm)".format(
                                            t_nome, op["met_tb"], op["met_th"], op["met_te"]
                                        )
                                    )
                                    if ds_t:
                                        ids_tercas.append(ds_t.Id)

                    if op["met_vigas"]:
                        span_u = u_max - u_min
                        margem_u = vrec_ft if span_u > 2.0 * vrec_ft + 0.3 * FT else min(vb_ft * 0.6, span_u * 0.2)
                        us_viga = gerar_posicoes_uniformes(u_min, u_max, vesp_ft, margem_u)
                        for uk in us_viga:
                            for v_ini_v, v_fim_v in recortar_linha_constante_u(loops_uv, uk):
                                p1 = ponto_3d_agua(agua, uk, v_ini_v, off_topo_viga)
                                p2 = ponto_3d_agua(agua, uk, v_fim_v, off_topo_viga)
                                sol_v = gerar_solido_perfil(
                                    p1, p2, u_horiz, n_up, vb_ft, vh_ft,
                                    topo_em_p1_p2=True, tipo_perfil=v_tipo, e_ft=ve_ft, mat_id=mat_id
                                )
                                if sol_v:
                                    ds_v = instanciar_solido_madeira(
                                        sol_v,
                                        u"Viga Metálica {} {:.0f}x{:.0f}cm (e={:g}mm)".format(
                                            v_nome, op["met_vb"], op["met_vh"], op["met_ve"]
                                        )
                                    )
                                    if ds_v:
                                        ids_vigas.append(ds_v.Id)

            t.Commit()

        grp_idx = op["met_grp_idx"]
        grupos_pendentes = []
        if grp_idx == 0:
            todos = ids_tercas + ids_vigas
            if len(todos) > 1:
                grupos_pendentes.append((todos, u"Estrutura Metálica de Cobertura"))
        elif grp_idx == 1:
            if len(ids_tercas) > 1:
                grupos_pendentes.append((ids_tercas, u"Estrutura Metálica - Terças"))
            if len(ids_vigas) > 1:
                grupos_pendentes.append((ids_vigas, u"Estrutura Metálica - Vigas Principais"))
        agrupar_lote_em_transacao(grupos_pendentes, "Agrupar Estrutura Metálica")
        tg.Assimilate()

    forms.alert(
        u"Estrutura Metálica (3D) concluída!\n\n"
        u"✔ Terças Metálicas ({}): {}\n"
        u"✔ Vigas Principais ({}): {}".format(t_nome, len(ids_tercas), v_nome, len(ids_vigas)),
        title="Estrutura Metálica: Vigas e Terças (3D)"
    )


def _achar_template_perfil_montante(app):
    caminhos_diretos = [
        u"G:\\ProgramData\\Autodesk\\RVT 2026\\Family Templates\\Portuguese\\Perfil métrico-Montante.rft",
        u"C:\\ProgramData\\Autodesk\\RVT 2026\\Family Templates\\Portuguese\\Perfil métrico-Montante.rft",
    ]
    for d in caminhos_diretos:
        if os.path.isfile(d):
            return d
    try:
        fp = app.FamilyTemplatePath
        if fp and os.path.isdir(fp):
            cand = os.path.join(fp, u"Perfil métrico-Montante.rft")
            if os.path.isfile(cand):
                return cand
    except Exception:
        pass
    return None


def _achar_simbolo_perfil(nome_perfil):
    for fs in FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_ProfileFamilies).OfClass(FamilySymbol).ToElements():
        try:
            fam_n = fs.Family.Name if fs.Family else u""
            sym_n = _get_type_name_str(fs)
            if fam_n == nome_perfil or sym_n == nome_perfil:
                return fs
        except Exception:
            pass
    return None


def _garantir_arquivo_perfil_montante(app, rft_path, nome_perfil, b_ft, h_ft, e_ft, tipo_perfil):
    """Gera (fora de transação do projeto) um arquivo .rfa de Perfil de Montante 2D para perfis metálicos."""
    if not rft_path or tipo_perfil not in ("perfil_i", "u_enrijecido", "u_simples"):
        return None
    if _achar_simbolo_perfil(nome_perfil) is not None:
        return None

    loops_2d = _loops_perfil_2d(b_ft, h_ft, e_ft, tipo_perfil)
    if not loops_2d or len(loops_2d) != 1:
        return None
    poly = loops_2d[0]

    pasta_tmp = os.path.join(tempfile.gettempdir(), "AutoBIM_PerfisMontante")
    if not os.path.isdir(pasta_tmp):
        try:
            os.makedirs(pasta_tmp)
        except Exception:
            return None
    caminho_rfa = os.path.join(pasta_tmp, nome_perfil + u".rfa")

    doc_p = None
    try:
        doc_p = app.NewFamilyDocument(rft_path)
        vista = None
        for vp in FilteredElementCollector(doc_p).OfClass(ViewPlan).ToElements():
            if not vp.IsTemplate:
                vista = vp
                break
        if vista is None:
            doc_p.Close(False)
            return None

        with Transaction(doc_p, "Desenhar Perfil Montante") as tp:
            tp.Start()
            n = len(poly)
            for i in range(n):
                x1, y1 = poly[i]
                x2, y2 = poly[(i + 1) % n]
                p1 = XYZ(x1, y1, 0.0)
                p2 = XYZ(x2, y2, 0.0)
                if p1.DistanceTo(p2) > 1e-5:
                    doc_p.FamilyCreate.NewDetailCurve(vista, Line.CreateBound(p1, p2))
            tp.Commit()

        opt_save = SaveAsOptions()
        opt_save.OverwriteExistingFile = True
        doc_p.SaveAs(caminho_rfa, opt_save)
        doc_p.Close(False)
        return caminho_rfa
    except Exception:
        if doc_p is not None:
            try:
                doc_p.Close(False)
            except Exception:
                pass
        return None


def obter_ou_criar_mullion_metalico(nome_tipo, largura_ft, espessura_ft, mat_id, profile_symbol_id=None):
    """Obtém ou cria um MullionType metálico aplicando material Aço Galvanizado e perfil 2D (se disponível)."""
    mt = obter_ou_criar_mullion_madeira(nome_tipo, largura_ft, espessura_ft, mat_id)
    if mt is not None and profile_symbol_id is not None and profile_symbol_id != ElementId.InvalidElementId:
        try:
            _apply_element_param(
                mt,
                ("MULLION_PROFILE",),
                (u"perfil", u"profile"),
                profile_symbol_id
            )
        except Exception:
            pass
    return mt


def run_estrutura_metalica_vidraca():
    """Gera a Estrutura Metálica de Cobertura (Terças C + Vigas I) via Vidraça Inclinada (Sloped Glazing)."""
    global doc, uidoc, view
    uidoc = __revit__.ActiveUIDocument
    doc   = uidoc.Document
    view  = doc.ActiveView

    base_rt = obter_tipo_base_vidraca()
    if base_rt is None:
        forms.alert(
            "Não foi encontrado nenhum tipo base da família 'Vidraça Inclinada' (Sloped Glazing) no projeto.",
            title="Estrutura Metálica: Vidraça Inclinada",
            exitscript=True
        )

    prefs = carregar_preferencias()
    win = EstruturaMetalicaWindow(prefs)
    win.Title = u"Estrutura Metálica: Vidraça Inclinada (Sloped Glazing)"
    win.ShowDialog()
    if not win.confirmado:
        script.exit()

    op = win.resultado
    salvar_preferencias(op)

    roofs = selecionar_telhados()

    map_terca = ("u_enrijecido", "u_simples", "metalon", "perfil_i")
    map_viga  = ("perfil_i", "u_enrijecido", "metalon", "u_simples")
    nomes_t   = (u"Perfil C", u"Perfil U", u"Metalon", u"Perfil I")
    nomes_v   = (u"Perfil I", u"Perfil C", u"Metalon", u"Perfil U")

    t_tipo = map_terca[min(3, op["met_t_perfil_idx"])]
    v_tipo = map_viga[min(3, op["met_v_perfil_idx"])]
    t_nome = nomes_t[min(3, op["met_t_perfil_idx"])]
    v_nome = nomes_v[min(3, op["met_v_perfil_idx"])]

    tb_ft   = op["met_tb"] / 30.48
    th_ft   = op["met_th"] / 30.48
    te_ft   = (op["met_te"] * 0.1) / 30.48
    tesp_ft = op["met_tesp"] / 30.48
    trec_ft = op["met_trec"] / 30.48

    vb_ft   = op["met_vb"] / 30.48
    vh_ft   = op["met_vh"] / 30.48
    ve_ft   = (op["met_ve"] * 0.1) / 30.48
    vesp_ft = op["met_vesp"] / 30.48

    folga_ft = op["met_folga"] / 30.48

    # Pré-gerar famílias de perfil 2D de montante (fora da Transaction do projeto)
    app = doc.Application
    rft_montante = _achar_template_perfil_montante(app)
    nome_prof_t = u"AutoBIM_Perfil_{}_{:.0f}x{:.0f}cm_{:g}mm".format(
        t_tipo, op["met_tb"], op["met_th"], op["met_te"]
    )
    nome_prof_v = u"AutoBIM_Perfil_{}_{:.0f}x{:.0f}cm_{:g}mm".format(
        v_tipo, op["met_vb"], op["met_vh"], op["met_ve"]
    )
    rfa_prof_t = None
    rfa_prof_v = None
    if op["met_tercas"]:
        rfa_prof_t = _garantir_arquivo_perfil_montante(app, rft_montante, nome_prof_t, tb_ft, th_ft, te_ft, t_tipo)
    if op["met_vigas"]:
        rfa_prof_v = _garantir_arquivo_perfil_montante(app, rft_montante, nome_prof_v, vb_ft, vh_ft, ve_ft, v_tipo)

    vidracas_criadas = []

    with TransactionGroup(doc, "Estrutura Metálica: Vidraça Inclinada") as tg:
        tg.Start()
        with Transaction(doc, "Estrutura Metálica: Vidraça Inclinada") as t:
            t.Start()
            opts = t.GetFailureHandlingOptions()
            opts.SetFailuresPreprocessor(VidracaFailurePreprocessor())
            t.SetFailureHandlingOptions(opts)

            mat_id = obter_ou_criar_material_aco_galv()
            opts_load = _FamLoadOpts()

            sym_prof_t_id = None
            if op["met_tercas"]:
                if rfa_prof_t:
                    try:
                        doc.LoadFamily(rfa_prof_t, opts_load)
                    except Exception:
                        pass
                fs_t = _achar_simbolo_perfil(nome_prof_t)
                if fs_t is not None:
                    sym_prof_t_id = fs_t.Id

            sym_prof_v_id = None
            if op["met_vigas"]:
                if rfa_prof_v:
                    try:
                        doc.LoadFamily(rfa_prof_v, opts_load)
                    except Exception:
                        pass
                fs_v = _achar_simbolo_perfil(nome_prof_v)
                if fs_v is not None:
                    sym_prof_v_id = fs_v.Id

            camadas_config = []
            off_acumulado = folga_ft
            mt_terca = None

            if op["met_tercas"]:
                mt_terca = obter_ou_criar_mullion_metalico(
                    u"AutoBIM - Terça Metálica {} {:.0f}x{:.0f}cm".format(t_nome, op["met_tb"], op["met_th"]),
                    tb_ft, th_ft, mat_id, sym_prof_t_id
                )
                rt_terca = obter_ou_criar_tipo_vidraca(
                    base_rt,
                    u"AutoBIM - Terças Metálicas {} ({:.0f}x{:.0f}cm máx {:.0f}cm)".format(
                        t_nome, op["met_tb"], op["met_th"], op["met_tesp"]
                    ),
                    usar_eixo_1=False, layout_code=0, espacamento_ft=tesp_ft,
                    mullion_id=(mt_terca.Id if mt_terca else None), incluir_bordas=False
                )
                camadas_config.append(("Terças", rt_terca, off_acumulado + th_ft * 0.5))
                off_acumulado += th_ft

            if op["met_vigas"]:
                mt_viga = obter_ou_criar_mullion_metalico(
                    u"AutoBIM - Viga Metálica {} {:.0f}x{:.0f}cm".format(v_nome, op["met_vb"], op["met_vh"]),
                    vb_ft, vh_ft, mat_id, sym_prof_v_id
                )
                rt_viga = obter_ou_criar_tipo_vidraca(
                    base_rt,
                    u"AutoBIM - Vigas Metálicas {} ({:.0f}x{:.0f}cm c/{:.0f}cm)".format(
                        v_nome, op["met_vb"], op["met_vh"], op["met_vesp"]
                    ),
                    usar_eixo_1=True, layout_code=1, espacamento_ft=vesp_ft,
                    mullion_id=(mt_viga.Id if mt_viga else None), incluir_bordas=True
                )
                camadas_config.append(("Vigas", rt_viga, off_acumulado + vh_ft * 0.5))
                off_acumulado += vh_ft

            for roof in roofs:
                esp_base = 0.0
                if op["met_face_idx"] == 1:
                    try:
                        rt_orig = doc.GetElement(roof.GetTypeId())
                        cs = rt_orig.GetCompoundStructure()
                        if cs is not None:
                            esp_base = cs.GetWidth()
                    except Exception:
                        pass

                aguas = extrair_faces_aguas(roof, usar_face_inferior=True)
                if not aguas:
                    aguas = extrair_faces_aguas(roof, usar_face_inferior=False)
                if aguas:
                    cos_theta = max(0.2, min(1.0, abs(aguas[0]["n_up"].Z)))
                else:
                    cos_theta = 0.95

                for nome_cam, rt_alvo, off_cam in camadas_config:
                    dz_base = (esp_base - off_cam) / cos_theta
                    try:
                        copia_ids = ElementTransformUtils.CopyElement(doc, roof.Id, XYZ(0, 0, dz_base))
                        if copia_ids and copia_ids.Count > 0:
                            novo_el = doc.GetElement(copia_ids[0])
                            novo_el.ChangeTypeId(rt_alvo.Id)
                            if nome_cam == "Terças":
                                doc.Regenerate()
                                _posicionar_tercas_vidraca(
                                    novo_el, aguas, dz_base, off_cam, esp_base,
                                    tb_ft, th_ft, tesp_ft, trec_ft, mt_terca
                                )
                            vidracas_criadas.append(novo_el.Id)
                    except Exception:
                        pass

            if len(vidracas_criadas) > 0:
                doc.Regenerate()

            t.Commit()

        if op["met_grp_idx"] in (0, 1) and len(vidracas_criadas) > 0:
            agrupar_lote_em_transacao([(vidracas_criadas, u"Estrutura Metálica - Vidraças Inclinadas")], "Agrupar Vidraça Metálica")
        tg.Assimilate()

    forms.alert(
        u"Estrutura Metálica (Vidraça Inclinada) concluída!\n\n"
        u"✔ Telhados de Vidraça Inclinada gerados: {}".format(len(vidracas_criadas)),
        title="Estrutura Metálica: Vidraça Inclinada"
    )


# ==============================================================================
# TRELIÇAS METÁLICAS (TRIANGULAR, PARALELA NO CAIMENTO E RETA HORIZONTAL)
# ==============================================================================

XAML_TRELICAS = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Estrutura Metálica: Treliças (1 e 2 Águas)" Width="560" SizeToContent="Height" MaxHeight="790"
        WindowStartupLocation="CenterScreen" ResizeMode="NoResize" Background="#F7F8FA">
    <ScrollViewer VerticalScrollBarVisibility="Auto">
        <StackPanel Margin="14">
            <Border Background="#EBF3FC" BorderBrush="#B8D4F5" BorderThickness="1" CornerRadius="4" Padding="9" Margin="0,0,0,10">
                <TextBlock TextWrapping="Wrap" FontWeight="SemiBold" Foreground="#1F3A57"
                           Text="Gera famílias paramétricas de Treliças Metálicas (Triangular, Paralela no Caimento ou Reta Horizontal) em Aço Galvanizado com perfis independentes para Banzos e Montantes/Diagonais."/>
            </Border>

            <Border Background="White" BorderBrush="#D0D7DE" BorderThickness="1" CornerRadius="4" Padding="10" Margin="0,0,0,8">
                <StackPanel>
                    <TextBlock Text="1. Geometria e Malha da Treliça" FontWeight="SemiBold" Margin="0,0,0,6"/>
                    <Grid>
                        <Grid.ColumnDefinitions>
                            <ColumnDefinition Width="210"/>
                            <ColumnDefinition Width="*"/>
                        </Grid.ColumnDefinitions>
                        <Grid.RowDefinitions>
                            <RowDefinition Height="28"/>
                            <RowDefinition Height="28"/>
                            <RowDefinition Height="28"/>
                            <RowDefinition Height="28"/>
                        </Grid.RowDefinitions>

                        <TextBlock Grid.Row="0" Grid.Column="0" Text="Forma da treliça:" VerticalAlignment="Center"/>
                        <ComboBox  Grid.Row="0" Grid.Column="1" x:Name="cb_forma" Height="24"/>

                        <TextBlock Grid.Row="1" Grid.Column="0" Text="Tipo de malha / diagonais:" VerticalAlignment="Center"/>
                        <ComboBox  Grid.Row="1" Grid.Column="1" x:Name="cb_malha" Height="24"/>

                        <TextBlock Grid.Row="2" Grid.Column="0" Text="Altura útil H (Paralela / Reta) (cm):" VerticalAlignment="Center"/>
                        <TextBox   Grid.Row="2" Grid.Column="1" x:Name="tb_h_tr" Height="24" Text="35" VerticalContentAlignment="Center"/>

                        <TextBlock Grid.Row="3" Grid.Column="0" Text="Comprimento máx. do painel (cm):" VerticalAlignment="Center"/>
                        <TextBox   Grid.Row="3" Grid.Column="1" x:Name="tb_esp_painel" Height="24" Text="75" VerticalContentAlignment="Center"/>
                    </Grid>
                </StackPanel>
            </Border>

            <Border Background="White" BorderBrush="#D0D7DE" BorderThickness="1" CornerRadius="4" Padding="10" Margin="0,0,0,8">
                <StackPanel>
                    <TextBlock Text="2. Distribuição e Apoios no Telhado" FontWeight="SemiBold" Margin="0,0,0,6"/>
                    <Grid>
                        <Grid.ColumnDefinitions>
                            <ColumnDefinition Width="265"/>
                            <ColumnDefinition Width="*"/>
                        </Grid.ColumnDefinitions>
                        <Grid.RowDefinitions>
                            <RowDefinition Height="28"/>
                            <RowDefinition Height="28"/>
                            <RowDefinition Height="28"/>
                            <RowDefinition Height="28"/>
                            <RowDefinition Height="24"/>
                            <RowDefinition Height="28"/>
                        </Grid.RowDefinitions>

                        <TextBlock Grid.Row="0" Grid.Column="0" Text="Espaçamento entre treliças (cm):" VerticalAlignment="Center"/>
                        <TextBox   Grid.Row="0" Grid.Column="1" x:Name="tb_esp_linhas" Height="24" Text="250" VerticalContentAlignment="Center"/>

                        <TextBlock Grid.Row="1" Grid.Column="0" Text="Direção do vão das treliças:" VerticalAlignment="Center"/>
                        <ComboBox  Grid.Row="1" Grid.Column="1" x:Name="cb_dir" Height="24"/>

                        <TextBlock Grid.Row="2" Grid.Column="0" Text="Recuo dos apoios / bordas (cm):" VerticalAlignment="Center"/>
                        <TextBox   Grid.Row="2" Grid.Column="1" x:Name="tb_recuo" Height="24" Text="20" VerticalContentAlignment="Center"/>

                        <TextBlock Grid.Row="3" Grid.Column="0" Text="Avanço banzo inf. além apoio (Triang.) (cm):" VerticalAlignment="Center"/>
                        <TextBox   Grid.Row="3" Grid.Column="1" x:Name="tb_avanco" Height="24" Text="0" VerticalContentAlignment="Center"/>

                        <StackPanel Grid.Row="4" Grid.Column="0" Grid.ColumnSpan="2" Orientation="Horizontal" VerticalAlignment="Center">
                            <CheckBox x:Name="ck_auto_trama" Content="Sincronizar desconto automático (Trama)" IsChecked="True" VerticalAlignment="Center"/>
                            <TextBlock x:Name="tb_trama_info" FontSize="11" Foreground="#555555" VerticalAlignment="Center" Margin="6,0,0,0"/>
                        </StackPanel>

                        <TextBlock Grid.Row="5" Grid.Column="0" Text="Desconto sob a telha (cm):" VerticalAlignment="Center"/>
                        <TextBox   Grid.Row="5" Grid.Column="1" x:Name="tb_desconto" Height="24" Text="10" VerticalContentAlignment="Center"/>
                    </Grid>
                </StackPanel>
            </Border>

            <Border Background="White" BorderBrush="#D0D7DE" BorderThickness="1" CornerRadius="4" Padding="10" Margin="0,0,0,8">
                <StackPanel>
                    <TextBlock Text="3. Perfis Metálicos — Largura b (Y) x Altura/Aba h (cm)" FontWeight="SemiBold" Margin="0,0,0,6"/>

                    <TextBlock Text="Banzos (Superior e Inferior):" FontWeight="SemiBold" Foreground="#1F3A57" FontSize="11.5" Margin="0,0,0,3"/>
                    <Grid Margin="0,0,0,8">
                        <Grid.ColumnDefinitions>
                            <ColumnDefinition Width="195"/>
                            <ColumnDefinition Width="6"/>
                            <ColumnDefinition Width="52"/>
                            <ColumnDefinition Width="18"/>
                            <ColumnDefinition Width="52"/>
                            <ColumnDefinition Width="26"/>
                            <ColumnDefinition Width="58"/>
                            <ColumnDefinition Width="48"/>
                            <ColumnDefinition Width="22"/>
                        </Grid.ColumnDefinitions>
                        <ComboBox Grid.Column="0" x:Name="cb_b_perf" Height="24"/>
                        <TextBox  Grid.Column="2" x:Name="tb_b_b" Height="24" Text="5" VerticalContentAlignment="Center"/>
                        <TextBlock Grid.Column="3" Text="x" VerticalAlignment="Center" HorizontalAlignment="Center"/>
                        <TextBox  Grid.Column="4" x:Name="tb_b_h" Height="24" Text="8" VerticalContentAlignment="Center"/>
                        <TextBlock Grid.Column="5" Text="cm" VerticalAlignment="Center" Margin="3,0,0,0"/>
                        <TextBlock Grid.Column="6" Text="Chapa:" VerticalAlignment="Center" HorizontalAlignment="Right" Margin="0,0,4,0"/>
                        <TextBox  Grid.Column="7" x:Name="tb_b_chapa" Height="24" Text="2.0" VerticalContentAlignment="Center"/>
                        <TextBlock Grid.Column="8" Text="mm" VerticalAlignment="Center" Margin="3,0,0,0"/>
                    </Grid>

                    <TextBlock Text="Montantes e Diagonais (Malha interna):" FontWeight="SemiBold" Foreground="#1F3A57" FontSize="11.5" Margin="0,0,0,3"/>
                    <Grid>
                        <Grid.ColumnDefinitions>
                            <ColumnDefinition Width="195"/>
                            <ColumnDefinition Width="6"/>
                            <ColumnDefinition Width="52"/>
                            <ColumnDefinition Width="18"/>
                            <ColumnDefinition Width="52"/>
                            <ColumnDefinition Width="26"/>
                            <ColumnDefinition Width="58"/>
                            <ColumnDefinition Width="48"/>
                            <ColumnDefinition Width="22"/>
                        </Grid.ColumnDefinitions>
                        <ComboBox Grid.Column="0" x:Name="cb_m_perf" Height="24"/>
                        <TextBox  Grid.Column="2" x:Name="tb_m_b" Height="24" Text="3" VerticalContentAlignment="Center"/>
                        <TextBlock Grid.Column="3" Text="x" VerticalAlignment="Center" HorizontalAlignment="Center"/>
                        <TextBox  Grid.Column="4" x:Name="tb_m_h" Height="24" Text="4" VerticalContentAlignment="Center"/>
                        <TextBlock Grid.Column="5" Text="cm" VerticalAlignment="Center" Margin="3,0,0,0"/>
                        <TextBlock Grid.Column="6" Text="Chapa:" VerticalAlignment="Center" HorizontalAlignment="Right" Margin="0,0,4,0"/>
                        <TextBox  Grid.Column="7" x:Name="tb_m_chapa" Height="24" Text="1.5" VerticalContentAlignment="Center"/>
                        <TextBlock Grid.Column="8" Text="mm" VerticalAlignment="Center" Margin="3,0,0,0"/>
                    </Grid>
                </StackPanel>
            </Border>

            <CheckBox x:Name="ck_chapas" Content="Incluir chapas metálicas de apoio nas extremidades / nó central (aço 8 mm)"
                      IsChecked="True" Margin="0,0,0,8"/>

            <Grid Margin="0,0,0,12">
                <Grid.ColumnDefinitions>
                    <ColumnDefinition Width="185"/>
                    <ColumnDefinition Width="*"/>
                </Grid.ColumnDefinitions>
                <TextBlock Grid.Column="0" Text="Agrupamento (Model Group):" VerticalAlignment="Center"/>
                <ComboBox  Grid.Column="1" x:Name="cb_grp" Height="24"/>
            </Grid>

            <StackPanel Orientation="Horizontal" HorizontalAlignment="Right">
                <Button x:Name="btn_cancel" Content="Cancelar" Width="95" Height="30" Margin="0,0,8,0"/>
                <Button x:Name="btn_ok" Content="Gerar Treliças Metálicas" Width="185" Height="30"
                        IsDefault="True" Background="#0078D4" Foreground="White" FontWeight="SemiBold"/>
            </StackPanel>
        </StackPanel>
    </ScrollViewer>
</Window>
"""

_PERFIS_TREL = ("metalon", "u_enrijecido", "u_simples", "perfil_i")
_NOMES_FORMA_TREL = (u"Triang", u"Paralela", u"Reta")
_NOMES_MALHA_TREL = (u"Warren", u"WarrenM", u"Pratt", u"Howe", u"Fink")


class TrelicasMetalicasWindow(forms.WPFWindow):
    def __init__(self, prefs):
        forms.WPFWindow.__init__(self, XAML_TRELICAS, literal_string=True)
        self.confirmado = False
        self.resultado  = {}

        self._cb_forma    = self.FindName("cb_forma")
        self._cb_malha    = self.FindName("cb_malha")
        self._tb_h_tr     = self.FindName("tb_h_tr")
        self._tb_painel   = self.FindName("tb_esp_painel")
        self._tb_linhas   = self.FindName("tb_esp_linhas")
        self._cb_dir      = self.FindName("cb_dir")
        self._tb_recuo    = self.FindName("tb_recuo")
        self._tb_avanco   = self.FindName("tb_avanco")
        self._ck_auto     = self.FindName("ck_auto_trama")
        self._tb_t_info   = self.FindName("tb_trama_info")
        self._tb_desconto = self.FindName("tb_desconto")
        self._cb_b_perf   = self.FindName("cb_b_perf")
        self._tb_b_b      = self.FindName("tb_b_b")
        self._tb_b_h      = self.FindName("tb_b_h")
        self._tb_b_chapa  = self.FindName("tb_b_chapa")
        self._cb_m_perf   = self.FindName("cb_m_perf")
        self._tb_m_b      = self.FindName("tb_m_b")
        self._tb_m_h      = self.FindName("tb_m_h")
        self._tb_m_chapa  = self.FindName("tb_m_chapa")
        self._ck_chapas   = self.FindName("ck_chapas")
        self._cb_grp      = self.FindName("cb_grp")

        self._cb_forma.Items.Add(u"Triangular (Banzo inferior horizontal)")
        self._cb_forma.Items.Add(u"Paralela no Caimento (Viga Treliçada inclinada)")
        self._cb_forma.Items.Add(u"Reta Horizontal (Banzos retos horizontais)")
        self._cb_forma.SelectedIndex = max(0, min(2, int(prefs.get("trel_forma_idx", 0) or 0)))

        self._cb_malha.Items.Add(u"Warren (Triângulos alternados /\\/\\)")
        self._cb_malha.Items.Add(u"Warren com Montantes Verticais")
        self._cb_malha.Items.Add(u"Pratt (Montantes + Diagonais descendo p/ centro)")
        self._cb_malha.Items.Add(u"Howe (Montantes + Diagonais subindo p/ centro)")
        self._cb_malha.Items.Add(u"Fink (Treliça em W / Subdividida)")
        self._cb_malha.SelectedIndex = max(0, min(4, int(prefs.get("trel_malha_idx", 0) or 0)))

        self._cb_dir.Items.Add(u"Automática (Transversal à maior cumeeira/água)")
        self._cb_dir.Items.Add(u"Vão ao longo do Eixo X (Treliças paralelas a X)")
        self._cb_dir.Items.Add(u"Vão ao longo do Eixo Y (Treliças paralelas a Y)")
        self._cb_dir.SelectedIndex = max(0, min(2, int(prefs.get("trel_dir_idx", 0) or 0)))

        for cb in (self._cb_b_perf, self._cb_m_perf):
            cb.Items.Add(u"Tubo Metalon")
            cb.Items.Add(u"Perfil U Enrijecido (C)")
            cb.Items.Add(u"Perfil U Simples")
            cb.Items.Add(u"Perfil I / W Laminado")

        self._cb_b_perf.SelectedIndex = max(0, min(3, int(prefs.get("trel_b_perf_idx", 0) or 0)))
        self._cb_m_perf.SelectedIndex = max(0, min(3, int(prefs.get("trel_m_perf_idx", 0) or 0)))

        self._cb_grp.Items.Add(u"Não agrupar (1 elemento por Treliça)")
        self._cb_grp.Items.Add(u"Agrupar todas as Treliças em 1 Grupo Geral")
        self._cb_grp.SelectedIndex = max(0, min(1, int(prefs.get("trel_grp_idx", 0) or 0)))

        self._tb_h_tr.Text    = str(prefs.get("trel_h_paralela", "35"))
        self._tb_painel.Text  = str(prefs.get("trel_esp_painel", "75"))
        self._tb_linhas.Text  = str(prefs.get("trel_esp_linhas", "250"))
        self._tb_recuo.Text   = str(prefs.get("trel_recuo", "20"))
        self._tb_avanco.Text  = str(prefs.get("trel_avanco", "0"))
        self._tb_b_b.Text     = str(prefs.get("trel_b_b", "5"))
        self._tb_b_h.Text     = str(prefs.get("trel_b_h", "8"))
        self._tb_b_chapa.Text = str(prefs.get("trel_b_chapa", "2.0"))
        self._tb_m_b.Text     = str(prefs.get("trel_m_b", "3"))
        self._tb_m_h.Text     = str(prefs.get("trel_m_h", "4"))
        self._tb_m_chapa.Text = str(prefs.get("trel_m_chapa", "1.5"))
        self._ck_chapas.IsChecked = bool(prefs.get("trel_chapas", True))

        partes_trama = []
        soma_trama = 0.0
        if prefs.get("ripas", True):
            rh = float(prefs.get("ripa_h", 2.0))
            soma_trama += rh
            partes_trama.append(u"Ripa {:g}".format(rh))
        if prefs.get("caibros", True):
            ch = float(prefs.get("caibro_h", 5.0))
            soma_trama += ch
            partes_trama.append(u"Caibro {:g}".format(ch))
        if prefs.get("tercas", True):
            th = float(prefs.get("terca_h", 12.0))
            soma_trama += th
            partes_trama.append(u"Terça {:g}".format(th))
        self._soma_trama = round(soma_trama, 2)
        if partes_trama:
            self._tb_t_info.Text = u"({} = {:g} cm)".format(u" + ".join(partes_trama), self._soma_trama)
        else:
            self._tb_t_info.Text = u"(Nenhuma camada ativa na Trama)"

        self._ck_auto.IsChecked = bool(prefs.get("trel_auto_trama", True))
        self._desc_manual = str(prefs.get("trel_desconto", "10"))
        self._tb_desconto.Text = self._desc_manual

        self._cb_forma.SelectionChanged += self._atualizar_ui
        self._ck_auto.Checked   += self._atualizar_ui
        self._ck_auto.Unchecked += self._atualizar_ui
        self._atualizar_ui(None, None)

        self.FindName("btn_ok").Click     += self.on_ok
        self.FindName("btn_cancel").Click += self.on_cancel

    def _atualizar_ui(self, sender, args):
        forma = max(0, self._cb_forma.SelectedIndex)
        self._tb_h_tr.IsEnabled   = (forma in (1, 2))
        self._tb_avanco.IsEnabled = (forma == 0)

        if self._ck_auto.IsChecked:
            self._tb_desconto.Text = u"{:g}".format(self._soma_trama)
            self._tb_desconto.IsEnabled = False
        else:
            self._tb_desconto.IsEnabled = True
            if not self._tb_desconto.Text:
                self._tb_desconto.Text = self._desc_manual

    def on_cancel(self, sender, args):
        self.confirmado = False
        self.Close()

    def on_ok(self, sender, args):
        try:
            res = {
                "trel_forma_idx":   max(0, self._cb_forma.SelectedIndex),
                "trel_malha_idx":   max(0, self._cb_malha.SelectedIndex),
                "trel_h_paralela":  max(10.0, float(self._tb_h_tr.Text.replace(",", "."))),
                "trel_esp_painel":  max(20.0, float(self._tb_painel.Text.replace(",", "."))),
                "trel_esp_linhas":  max(20.0, float(self._tb_linhas.Text.replace(",", "."))),
                "trel_dir_idx":     max(0, self._cb_dir.SelectedIndex),
                "trel_recuo":       max(0.0, float(self._tb_recuo.Text.replace(",", "."))),
                "trel_avanco":      max(0.0, float(self._tb_avanco.Text.replace(",", "."))),
                "trel_auto_trama":  bool(self._ck_auto.IsChecked),
                "trel_desconto":    float(self._tb_desconto.Text.replace(",", ".")),
                "trel_b_perf_idx":  max(0, self._cb_b_perf.SelectedIndex),
                "trel_b_b":         max(1.0, float(self._tb_b_b.Text.replace(",", "."))),
                "trel_b_h":         max(1.0, float(self._tb_b_h.Text.replace(",", "."))),
                "trel_b_chapa":     max(0.9, float(self._tb_b_chapa.Text.replace(",", "."))),
                "trel_m_perf_idx":  max(0, self._cb_m_perf.SelectedIndex),
                "trel_m_b":         max(1.0, float(self._tb_m_b.Text.replace(",", "."))),
                "trel_m_h":         max(1.0, float(self._tb_m_h.Text.replace(",", "."))),
                "trel_m_chapa":     max(0.9, float(self._tb_m_chapa.Text.replace(",", "."))),
                "trel_chapas":      bool(self._ck_chapas.IsChecked),
                "trel_grp_idx":     max(0, self._cb_grp.SelectedIndex),
            }
        except Exception:
            forms.alert("Informe apenas valores numéricos válidos.")
            return

        self.resultado = res
        self.confirmado = True
        self.Close()


def _recortar_eixo_barra(p1, p2, meia_h, planos, extra=2.0):
    """Recorta segmento p1->p2 no plano XZ garantindo que nenhum canto (±meia_h) saia dos semiplanos [(ax, az, c)]."""
    dx = p2[0] - p1[0]
    dz = p2[1] - p1[1]
    comp = math.hypot(dx, dz)
    if comp < 1e-5:
        return None
    ux, uz = dx / comp, dz / comp
    nx, nz = -uz, ux
    s_min = -extra
    s_max = comp + extra
    for ax, az, c in planos:
        a_dot_u  = ax * ux + az * uz
        a_dot_p1 = ax * p1[0] + az * p1[1]
        a_dot_n  = abs(ax * nx + az * nz) * meia_h
        rhs = c - a_dot_p1 - a_dot_n
        if a_dot_u > 1e-9:
            s_max = min(s_max, rhs / a_dot_u)
        elif a_dot_u < -1e-9:
            s_min = max(s_min, rhs / a_dot_u)
        else:
            if rhs < -1e-6:
                return None
    if s_max - s_min < 0.025:
        return None
    return (
        (p1[0] + ux * s_min, p1[1] + uz * s_min),
        (p1[0] + ux * s_max, p1[1] + uz * s_max),
    )


def _loops_perfil_2d_orientado(b_ft, h_ft, e_ft, tipo_perfil, orient="side"):
    """Retorna polígonos 2D do perfil; para U/C nos banzos ('down'/'up'), gira a abertura para dentro da treliça."""
    if orient == "side" or tipo_perfil not in ("u_simples", "u_enrijecido"):
        return _loops_perfil_2d(b_ft, h_ft, e_ft, tipo_perfil)

    hb = b_ft * 0.5
    hh = h_ft * 0.5
    e = max(0.0011 * FT, min(e_ft, min(hb, hh) * 0.42))
    dobra = min(hb * 0.35, max(e * 1.8, min(b_ft, h_ft) * 0.22))

    if tipo_perfil == "u_enrijecido" and dobra > e * 1.25 and (hb - dobra) > e * 0.8:
        lp_down = [
            (-hb, -hh), (-hb + dobra, -hh), (-hb + dobra, -hh + e), (-hb + e, -hh + e),
            (-hb + e, hh - e), (hb - e, hh - e), (hb - e, -hh + e), (hb - dobra, -hh + e),
            (hb - dobra, -hh), (hb, -hh), (hb, hh), (-hb, hh),
        ]
    else:
        lp_down = [
            (-hb, -hh), (-hb + e, -hh), (-hb + e, hh - e),
            (hb - e, hh - e), (hb - e, -hh), (hb, -hh),
            (hb, hh), (-hb, hh),
        ]

    if orient == "up":
        return [list(reversed([(pt[0], -pt[1]) for pt in lp_down]))]
    return [lp_down]


def _extrusao_barra_perfil(fdoc, p1_xz, p2_xz, b_ft, h_ft, e_ft, tipo_perfil, mat_id, orient="side"):
    """Extruda uma barra metálica (Metalon/C/U/I) ao longo do eixo p1->p2 no plano XZ de fdoc."""
    dx = p2_xz[0] - p1_xz[0]
    dz = p2_xz[1] - p1_xz[1]
    comp = math.hypot(dx, dz)
    if comp < 0.020 or b_ft < 0.015 or h_ft < 0.015:
        return None

    dir_bar = XYZ(dx / comp, 0.0, dz / comp)
    n_xz    = XYZ(-dz / comp, 0.0, dx / comp)
    y_axis  = XYZ.BasisY
    origin  = XYZ(p1_xz[0], 0.0, p1_xz[1])

    e_safe = max(0.0011 * FT, e_ft)
    loops = _loops_perfil_2d_orientado(b_ft, h_ft, e_safe, tipo_perfil, orient=orient)

    def _loops_ok(lps):
        for lp in lps:
            n_pt = len(lp)
            if n_pt < 3:
                return False
            for idx in range(n_pt):
                a, b = lp[idx], lp[(idx + 1) % n_pt]
                if math.hypot(b[0] - a[0], b[1] - a[1]) <= 0.0028:
                    return False
        return True

    if not _loops_ok(loops):
        loops = _loops_perfil_2d(b_ft, h_ft, e_safe, "retangular")
        if not _loops_ok(loops):
            return None

    plano = Plane.CreateByOriginAndBasis(origin, y_axis, n_xz)
    sp    = SketchPlane.Create(fdoc, plano)
    caa   = CurveArrArray()
    for lp in loops:
        ca = CurveArray()
        n_pt = len(lp)
        for idx in range(n_pt):
            a = lp[idx]
            b = lp[(idx + 1) % n_pt]
            pA = origin + y_axis * a[0] + n_xz * a[1]
            pB = origin + y_axis * b[0] + n_xz * b[1]
            ca.Append(Line.CreateBound(pA, pB))
        caa.Append(ca)

    ex = fdoc.FamilyCreate.NewExtrusion(True, caa, sp, comp)
    try:
        if mat_id is not None and mat_id != ElementId.InvalidElementId:
            p = ex.get_Parameter(BuiltInParameter.MATERIAL_ID_PARAM)
            if p is not None and not p.IsReadOnly:
                p.Set(mat_id)
    except Exception:
        pass
    return ex


def _calc_z_lb_trelica(forma, agua1_dir, xa, xb, xel, xer, zel, zer, xp, zp, z_min_span, bh, h_tr):
    """Calcula (z_lb, sl_l, sl_r, zt_l_fn, zt_r_fn, h_tr_eff) de forma consistente no projeto e na família."""
    if agua1_dir == 1:
        sl_l = (zp - zel) / max(1e-6, xp - xel)
        sl_r = sl_l
    elif agua1_dir == -1:
        sl_r = (zer - zp) / max(1e-6, xer - xp)
        sl_l = sl_r
    else:
        sl_l = (zp - zel) / max(1e-6, xp - xel)
        sl_r = (zer - zp) / max(1e-6, xer - xp)

    if forma == 2:
        sl_l = 0.0
        sl_r = 0.0

    def zt_l(x):
        if forma == 2:
            return z_min_span
        return zel + sl_l * (x - xel)

    def zt_r(x):
        if forma == 2:
            return z_min_span
        return zer + sl_r * (x - xer)

    h_tr_eff = max(h_tr, bh * 2.3 + 0.05 * FT)
    if forma == 0:
        hbv_l = bh * math.sqrt(1.0 + sl_l * sl_l)
        hbv_r = bh * math.sqrt(1.0 + sl_r * sl_r)
        if agua1_dir == 1:
            z_lt = zt_l(xa) - hbv_l
        elif agua1_dir == -1:
            z_lt = zt_r(xb) - hbv_r
        else:
            z_lt = min(zt_l(xa) - hbv_l, zt_r(xb) - hbv_r)
        z_lb = z_lt - bh
    elif forma == 1:
        hv_tr_l = h_tr_eff * math.sqrt(1.0 + sl_l * sl_l)
        hv_tr_r = h_tr_eff * math.sqrt(1.0 + sl_r * sl_r)
        if agua1_dir == 1:
            z_lb = zt_l(xa) - hv_tr_l
        elif agua1_dir == -1:
            z_lb = zt_r(xb) - hv_tr_r
        else:
            z_lb = min(zt_l(xa) - hv_tr_l, zt_r(xb) - hv_tr_r)
    else:
        z_lb = z_min_span - h_tr_eff

    return z_lb, sl_l, sl_r, zt_l, zt_r, h_tr_eff


def _chave_geometria_trel(g):
    return (
        g["forma"], g["malha"], g["agua1_dir"],
        g["xa"], g["xb"], g["xel"], g["xer"], g["zel"], g["zer"], g["xp"], g["zp"],
        g["z_min_span"], g["h_tr"], g["esp_painel"], g["avanco"],
        g["b_perf"], g["b_b"], g["b_h"], g["b_chapa"],
        g["m_perf"], g["m_b"], g["m_h"], g["m_chapa"],
        g["chapas"], "v2_cwrap",
    )


def _nome_familia_trel(g):
    import hashlib
    vao = int(round(g["xb"] - g["xa"]))
    f_nome = _NOMES_FORMA_TREL[min(2, max(0, int(g["forma"])))]
    m_nome = _NOMES_MALHA_TREL[min(4, max(0, int(g["malha"])))]
    suf_agua = u" 1A" if g["agua1_dir"] != 0 else u""
    tag = hashlib.md5(repr(_chave_geometria_trel(g)).encode("utf-8")).hexdigest()[:6]
    return u"Trelica Met {}-{}{} AutoBIM - Vao {}cm - {}x{} - {}".format(
        f_nome, m_nome, suf_agua, vao, int(round(g["b_b"])), int(round(g["b_h"])), tag
    )


def _construir_familia_trelica_metalica(app, template, g, caminho_rfa):
    """Constrói família .rfa de Treliça Metálica (Triangular, Paralela no Caimento ou Reta Horizontal)."""
    cm = 1.0 / 30.48
    mm = 1.0 / 304.8

    forma     = int(g["forma"])
    malha     = int(g["malha"])
    agua1_dir = int(g["agua1_dir"])

    xa, xb     = g["xa"] * cm, g["xb"] * cm
    xel, xer   = g["xel"] * cm, g["xer"] * cm
    zel, zer   = g["zel"] * cm, g["zer"] * cm
    xp, zp     = g["xp"] * cm, g["zp"] * cm
    z_min_sp   = g["z_min_span"] * cm
    h_tr       = g["h_tr"] * cm
    esp_painel = max(0.30 * FT, g["esp_painel"] * cm)
    avanco     = g["avanco"] * cm

    b_perf  = _PERFIS_TREL[min(3, max(0, int(g["b_perf"])))]
    bb, bh  = g["b_b"] * cm, g["b_h"] * cm
    b_e     = g["b_chapa"] * mm
    m_perf  = _PERFIS_TREL[min(3, max(0, int(g["m_perf"])))]
    mb, mh  = g["m_b"] * cm, g["m_h"] * cm
    m_e     = g["m_chapa"] * mm

    fdoc = None
    try:
        fdoc = app.NewFamilyDocument(template)
        if fdoc is None:
            return u"Não foi possível abrir o modelo de família."

        t = Transaction(fdoc, "Gerar Treliça Metálica")
        t.Start()
        try:
            try:
                cat = fdoc.Settings.Categories.get_Item(BuiltInCategory.OST_StructuralFraming)
                fdoc.OwnerFamily.FamilyCategory = cat
            except Exception:
                pass

            aco = _mat_familia(fdoc, u"Aço Galvanizado (AutoBIM)", (176, 184, 192))

            z_lb_raw, sl_l, sl_r, zt_l_raw, zt_r_raw, h_tr_eff = _calc_z_lb_trelica(
                forma, agua1_dir, xa, xb, xel, xer, zel, zer, xp, zp, z_min_sp, bh, h_tr
            )
            dz = -z_lb_raw

            def zt_l(x):
                return zt_l_raw(x) + dz

            def zt_r(x):
                return zt_r_raw(x) + dz

            norm_l = math.sqrt(1.0 + sl_l * sl_l)
            norm_r = math.sqrt(1.0 + sl_r * sl_r)
            hbv_l  = bh * norm_l
            hbv_r  = bh * norm_r
            hv_tr_l = h_tr_eff * norm_l
            hv_tr_r = h_tr_eff * norm_r
            z_lt_tri = bh   # Em forma == 0, base inferior do banzo inf. está em Z = 0

            def zb_l(x):
                if forma == 0:
                    return 0.0
                if forma == 1:
                    return zt_l(x) - hv_tr_l
                return 0.0

            def zb_r(x):
                if forma == 0:
                    return 0.0
                if forma == 1:
                    return zt_r(x) - hv_tr_r
                return 0.0

            def add_bar_clipped(p1, p2, b_sec, h_sec, e_sec, perf, planos, orient="side"):
                seg = _recortar_eixo_barra(p1, p2, 0.5 * h_sec, planos, extra=max(h_tr_eff, 2.0))
                if seg is not None:
                    _extrusao_barra_perfil(fdoc, seg[0], seg[1], b_sec, h_sec, e_sec, perf, aco, orient=orient)

            # Planos de face externa dos banzos superiores (para manter barras sempre sob o telhado)
            pl_top_ext_l = (-sl_l / norm_l, 1.0 / norm_l, (zt_l(0.0) / norm_l))
            pl_top_ext_r = (-sl_r / norm_r, 1.0 / norm_r, (zt_r(0.0) / norm_r))

            # Planos de engaste interno para montantes e diagonais (alojam dentro das abas sem vazar pela alma)
            rec_top = min(0.40 * bh, max(b_e * 1.15, 0.20 * bh))
            pl_top_in_l = (-sl_l / norm_l, 1.0 / norm_l, (zt_l(0.0) / norm_l) - rec_top)
            pl_top_in_r = (-sl_r / norm_r, 1.0 / norm_r, (zt_r(0.0) / norm_r) - rec_top)

            def _pl_bot_in(sl_b, x0_b, zb0_b, rec_b):
                nb = math.sqrt(1.0 + sl_b * sl_b)
                return (sl_b / nb, -1.0 / nb, ((sl_b * x0_b - zb0_b) / nb) - rec_b)

            # -------------------------------------------------- 1. BANZOS SUPERIORES E INFERIORES
            if forma == 0:
                # TRIANGULAR
                if agua1_dir == 1:
                    p_t1 = (xel, zt_l(xel) - 0.5 * hbv_l)
                    p_t2 = (xer, zt_l(xer) - 0.5 * hbv_l)
                    add_bar_clipped(p_t1, p_t2, bb, bh, b_e, b_perf, [(-1.0, 0.0, -xel), (1.0, 0.0, xer)], orient="down")
                elif agua1_dir == -1:
                    p_t1 = (xel, zt_r(xel) - 0.5 * hbv_r)
                    p_t2 = (xer, zt_r(xer) - 0.5 * hbv_r)
                    add_bar_clipped(p_t1, p_t2, bb, bh, b_e, b_perf, [(-1.0, 0.0, -xel), (1.0, 0.0, xer)], orient="down")
                else:
                    p_tl1 = (xel, zt_l(xel) - 0.5 * hbv_l)
                    p_tl2 = (xp,  zt_l(xp)  - 0.5 * hbv_l)
                    add_bar_clipped(p_tl1, p_tl2, bb, bh, b_e, b_perf, [(-1.0, 0.0, -xel), (1.0, 0.0, xp)], orient="down")

                    p_tr1 = (xp,  zt_r(xp)  - 0.5 * hbv_r)
                    p_tr2 = (xer, zt_r(xer) - 0.5 * hbv_r)
                    add_bar_clipped(p_tr1, p_tr2, bb, bh, b_e, b_perf, [(-1.0, 0.0, -xp), (1.0, 0.0, xer)], orient="down")

                xa_out = max(xel, xa - avanco) if agua1_dir != -1 else xa
                xb_out = min(xer, xb + avanco) if agua1_dir != 1  else xb
                planos_bot = [(-1.0, 0.0, -xa_out), (1.0, 0.0, xb_out)]
                if agua1_dir == 1:
                    planos_bot.append(pl_top_ext_l)
                elif agua1_dir == -1:
                    planos_bot.append(pl_top_ext_r)
                else:
                    planos_bot.extend([pl_top_ext_l, pl_top_ext_r])
                add_bar_clipped((xa_out, 0.5 * bh), (xb_out, 0.5 * bh), bb, bh, b_e, b_perf, planos_bot, orient="up")

            elif forma == 1:
                # PARALELA NO CAIMENTO
                if agua1_dir != 0:
                    sl_1 = sl_l if agua1_dir == 1 else sl_r
                    hbv_1 = hbv_l if agua1_dir == 1 else hbv_r
                    zt_1 = zt_l if agua1_dir == 1 else zt_r
                    zb_1 = zb_l if agua1_dir == 1 else zb_r
                    pl_x = [(-1.0, 0.0, -xa), (1.0, 0.0, xb)]
                    add_bar_clipped((xa, zt_1(xa) - 0.5 * hbv_1), (xb, zt_1(xb) - 0.5 * hbv_1), bb, bh, b_e, b_perf, pl_x, orient="down")
                    add_bar_clipped((xa, zb_1(xa) + 0.5 * hbv_1), (xb, zb_1(xb) + 0.5 * hbv_1), bb, bh, b_e, b_perf, pl_x, orient="up")
                else:
                    pl_xl = [(-1.0, 0.0, -xa), (1.0, 0.0, xp)]
                    pl_xr = [(-1.0, 0.0, -xp), (1.0, 0.0, xb)]
                    add_bar_clipped((xa, zt_l(xa) - 0.5 * hbv_l), (xp, zt_l(xp) - 0.5 * hbv_l), bb, bh, b_e, b_perf, pl_xl, orient="down")
                    add_bar_clipped((xa, zb_l(xa) + 0.5 * hbv_l), (xp, zb_l(xp) + 0.5 * hbv_l), bb, bh, b_e, b_perf, pl_xl, orient="up")
                    add_bar_clipped((xp, zt_r(xp) - 0.5 * hbv_r), (xb, zt_r(xb) - 0.5 * hbv_r), bb, bh, b_e, b_perf, pl_xr, orient="down")
                    add_bar_clipped((xp, zb_r(xp) + 0.5 * hbv_r), (xb, zb_r(xb) + 0.5 * hbv_r), bb, bh, b_e, b_perf, pl_xr, orient="up")

            else:
                # RETA HORIZONTAL
                pl_x = [(-1.0, 0.0, -xa), (1.0, 0.0, xb)]
                add_bar_clipped((xa, h_tr_eff - 0.5 * bh), (xb, h_tr_eff - 0.5 * bh), bb, bh, b_e, b_perf, pl_x, orient="down")
                add_bar_clipped((xa, 0.5 * bh),            (xb, 0.5 * bh),            bb, bh, b_e, b_perf, pl_x, orient="up")

            # -------------------------------------------------- 2. MONTANTES E DIAGONAIS (MALHA INTERNA)
            def _gerar_malha_ala(x_out, x_in, zt_fn, zb_fn, sl_w, pl_t_in, planos_extra, is_centro_compartilhado):
                vao_ala = abs(x_in - x_out)
                if vao_ala < 0.35 * FT:
                    return
                norm_w = math.sqrt(1.0 + sl_w * sl_w)
                hbv_w = bh * norm_w
                sl_b = 0.0 if forma in (0, 2) else sl_w
                pl_b_in = _pl_bot_in(sl_b, x_out, zb_fn(x_out), rec_top)
                planos_web = [pl_t_in, pl_b_in] + list(planos_extra)

                def _pt_top(u):
                    x = x_out + (x_in - x_out) * u
                    return (x, zt_fn(x) - 0.5 * hbv_w)

                def _pt_bot(u):
                    x = x_out + (x_in - x_out) * u
                    return (x, zb_fn(x) + 0.5 * bh * math.sqrt(1.0 + sl_b * sl_b))

                def _h_clara(u):
                    x = x_out + (x_in - x_out) * u
                    return (zt_fn(x) - hbv_w) - (zb_fn(x) + bh * math.sqrt(1.0 + sl_b * sl_b))

                def _add_montante(x_m):
                    if _h_clara((x_m - x_out) / (x_in - x_out)) <= 0.30 * mh:
                        return
                    p_b = (x_m, zb_fn(x_m))
                    p_t = (x_m, zt_fn(x_m))
                    add_bar_clipped(p_b, p_t, mb, mh, m_e, m_perf, planos_web)

                n_pan = max(2, min(28, int(math.ceil(vao_ala / esp_painel))))
                du = 1.0 / float(n_pan)
                sgn_in = 1.0 if x_in > x_out else -1.0

                # Montante de extremidade externa (Paralela/Reta; ou no lado alto 1A)
                if forma in (1, 2):
                    _add_montante(x_out + sgn_in * 0.5 * mh)
                if not is_centro_compartilhado:
                    _add_montante(x_in - sgn_in * 0.5 * mh)

                if malha == 0 or malha == 1:
                    # 0: WARREN | 1: WARREN COM MONTANTES
                    if forma in (1, 2):
                        for i in range(n_pan):
                            u0, u1 = i * du, (i + 1) * du
                            if i % 2 == 0:
                                add_bar_clipped(_pt_bot(u0), _pt_top(u1), mb, mh, m_e, m_perf, planos_web)
                            else:
                                add_bar_clipped(_pt_top(u0), _pt_bot(u1), mb, mh, m_e, m_perf, planos_web)
                            if malha == 1 and i > 0:
                                _add_montante(x_out + (x_in - x_out) * u0)
                    else:
                        # Triangular Warren: b_1 -> t_1 -> b_2 -> ... -> b_N (evita ângulo fechado no bico u=0)
                        for k in range(1, n_pan):
                            ub0 = k * du
                            ut  = (k + 0.5) * du
                            ub1 = (k + 1) * du
                            if malha == 1:
                                _add_montante(x_out + (x_in - x_out) * ub0)
                            if _h_clara(ut) > 0.30 * mh:
                                add_bar_clipped(_pt_bot(ub0), _pt_top(ut), mb, mh, m_e, m_perf, planos_web)
                                add_bar_clipped(_pt_top(ut), _pt_bot(ub1), mb, mh, m_e, m_perf, planos_web)
                            else:
                                add_bar_clipped(_pt_bot(ub0), _pt_top(ub1), mb, mh, m_e, m_perf, planos_web)

                elif malha == 2:
                    # PRATT (Montantes verticais + diagonais caindo em direção ao centro)
                    for i in range(1, n_pan):
                        _add_montante(x_out + (x_in - x_out) * (i * du))
                    for i in range(n_pan):
                        if forma == 0 and i == 0 and _h_clara(0.0) <= 0.35 * mh:
                            continue
                        add_bar_clipped(_pt_top(i * du), _pt_bot((i + 1) * du), mb, mh, m_e, m_perf, planos_web)

                elif malha == 3:
                    # HOWE (Montantes verticais + diagonais subindo em direção ao centro)
                    for i in range(1, n_pan):
                        _add_montante(x_out + (x_in - x_out) * (i * du))
                    for i in range(n_pan):
                        if forma == 0 and i == 0 and _h_clara(0.0) <= 0.35 * mh:
                            continue
                        add_bar_clipped(_pt_bot(i * du), _pt_top((i + 1) * du), mb, mh, m_e, m_perf, planos_web)

                else:
                    # FINK (Treliça em W / Subdividida)
                    if forma == 0:
                        if vao_ala > 3.2 / 0.3048:
                            pts_w = [
                                (_pt_top(0.25), _pt_bot(1.0 / 3.0)),
                                (_pt_bot(1.0 / 3.0), _pt_top(0.5)),
                                (_pt_top(0.5), _pt_bot(2.0 / 3.0)),
                                (_pt_bot(2.0 / 3.0), _pt_top(0.75)),
                                (_pt_bot(2.0 / 3.0), _pt_top(1.0)),
                            ]
                        else:
                            pts_w = [
                                (_pt_top(0.5), _pt_bot(2.0 / 3.0)),
                                (_pt_bot(2.0 / 3.0), _pt_top(1.0)),
                            ]
                        for pa, pb in pts_w:
                            add_bar_clipped(pa, pb, mb, mh, m_e, m_perf, planos_web)
                    else:
                        # Em treliça paralela/reta, Fink subdividida usa padrão X/K simétrico + montantes
                        for i in range(1, n_pan):
                            _add_montante(x_out + (x_in - x_out) * (i * du))
                        for i in range(n_pan):
                            u0, u1 = i * du, (i + 1) * du
                            add_bar_clipped(_pt_bot(u0), _pt_top(u1), mb, mh, m_e, m_perf, planos_web)
                            add_bar_clipped(_pt_top(u0), _pt_bot(u1), mb, mh, m_e, m_perf, planos_web)

            # Montagem por 1 água / 2 águas / Reta Horizontal
            if forma == 2:
                # Reta horizontal: duas alas simétricas encontrando no centro x_mid (sem sobrepor nó central)
                x_mid = 0.5 * (xa + xb)
                pl_cl = [(-1.0, 0.0, -xa), (1.0, 0.0, x_mid)]
                pl_cr = [(-1.0, 0.0, -x_mid), (1.0, 0.0, xb)]
                if malha == 0:
                    # Warren contínuo de xa até xb
                    _gerar_malha_ala(xa, xb, zt_l, zb_l, 0.0, pl_top_in_l, [(-1.0, 0.0, -xa), (1.0, 0.0, xb)], False)
                else:
                    _gerar_malha_ala(xa, x_mid, zt_l, zb_l, 0.0, pl_top_in_l, pl_cl, True)
                    _gerar_malha_ala(xb, x_mid, zt_l, zb_l, 0.0, pl_top_in_l, pl_cr, True)
                    add_bar_clipped((x_mid, 0.5 * bh), (x_mid, h_tr_eff - 0.5 * bh), mb, mh, m_e, m_perf, [pl_top_in_l, _pl_bot_in(0.0, xa, 0.0, rec_top)])
            elif agua1_dir == 1:
                _gerar_malha_ala(xa, xb, zt_l, zb_l, sl_l, pl_top_in_l, [(-1.0, 0.0, -xa), (1.0, 0.0, xb)], False)
            elif agua1_dir == -1:
                _gerar_malha_ala(xb, xa, zt_r, zb_r, sl_r, pl_top_in_r, [(-1.0, 0.0, -xa), (1.0, 0.0, xb)], False)
            else:
                pl_cl = [(-1.0, 0.0, -xa), (1.0, 0.0, xp)]
                pl_cr = [(-1.0, 0.0, -xp), (1.0, 0.0, xb)]
                _gerar_malha_ala(xa, xp, zt_l, zb_l, sl_l, pl_top_in_l, pl_cl, True)
                _gerar_malha_ala(xb, xp, zt_r, zb_r, sl_r, pl_top_in_r, pl_cr, True)

                # Montante central na cumeeira (xp)
                precisa_mont_central = (malha != 4) or (forma == 1)
                if precisa_mont_central:
                    zb_c_min = min(zb_l(xp), zb_r(xp))
                    pl_c_top = pl_top_in_l if sl_l >= 0 else pl_top_in_r
                    add_bar_clipped(
                        (xp, zb_c_min), (xp, zt_l(xp)), mb, mh, m_e, m_perf,
                        [pl_top_in_l, pl_top_in_r, _pl_bot_in(0.0, xp, zb_c_min, rec_top)]
                    )

            # -------------------------------------------------- 3. CHAPAS METÁLICAS DE APOIO / NÓ CENTRAL
            if g["chapas"]:
                esp_ch = 0.008 / 0.3048
                meio_y_ch = 0.5 * bb + 0.02 / 0.3048
                meio_x_ch = max(0.06 / 0.3048, 0.75 * bh)
                pts_apoio = []
                if forma == 0:
                    pts_apoio = [(xa, 0.0), (xb, 0.0)]
                elif forma == 1:
                    if agua1_dir == 1:
                        pts_apoio = [(xa, zb_l(xa)), (xb, zb_l(xb))]
                    elif agua1_dir == -1:
                        pts_apoio = [(xa, zb_r(xa)), (xb, zb_r(xb))]
                    else:
                        pts_apoio = [(xa, zb_l(xa)), (xb, zb_r(xb))]
                else:
                    pts_apoio = [(xa, 0.0), (xb, 0.0)]

                for x_ap, z_ap in pts_apoio:
                    _extrusao_xz(fdoc, _retangulo(x_ap - meio_x_ch, z_ap - esp_ch, x_ap + meio_x_ch, z_ap),
                                 -meio_y_ch, meio_y_ch, aco)

                # Chapa de ligação na cumeeira para 2 águas
                if agua1_dir == 0 and forma in (0, 1):
                    esp_g = 0.005 / 0.3048
                    larg_g = max(0.04 / 0.3048, bh * 0.9)
                    z_cg_top = zt_l(xp) - 0.12 * bh
                    z_cg_bot = max(0.5 * bh, z_cg_top - 1.6 * bh)
                    for y0, y1 in ((0.5 * bb, 0.5 * bb + esp_g), (-0.5 * bb - esp_g, -0.5 * bb)):
                        _extrusao_xz(fdoc, _retangulo(xp - larg_g, z_cg_bot, xp + larg_g, z_cg_top),
                                     y0, y1, aco)

            fdoc.Regenerate()
            t.Commit()
        except Exception as e_in:
            try:
                t.RollBack()
            except Exception:
                pass
            raise e_in

        pasta = os.path.dirname(caminho_rfa)
        if not os.path.isdir(pasta):
            os.makedirs(pasta)
        so = SaveAsOptions()
        so.OverwriteExistingFile = True
        fdoc.SaveAs(caminho_rfa, so)
        fdoc.Close(False)
        fdoc = None
        return None
    except Exception as e:
        try:
            if fdoc is not None:
                fdoc.Close(False)
        except Exception:
            pass
        return u"{}".format(e)


def run_trelicas_metalicas():
    global doc, uidoc, view
    uidoc = __revit__.ActiveUIDocument
    doc   = uidoc.Document
    view  = doc.ActiveView

    prefs = carregar_preferencias()
    win = TrelicasMetalicasWindow(prefs)
    win.ShowDialog()
    if not win.confirmado:
        script.exit()

    op = win.resultado
    salvar_preferencias(op)

    roofs = selecionar_telhados()

    esp_linhas_ft = op["trel_esp_linhas"] / 30.48
    desconto_ft   = op["trel_desconto"] / 30.48
    recuo_ft      = op["trel_recuo"] / 30.48
    bh_ft         = op["trel_b_h"] / 30.48
    h_tr_ft       = op["trel_h_paralela"] / 30.48
    forma         = int(op["trel_forma_idx"])

    trelicas = []
    ignoradas = 0

    for roof in roofs:
        aguas = extrair_faces_aguas(roof, usar_face_inferior=True)
        if not aguas:
            aguas = extrair_faces_aguas(roof, usar_face_inferior=False)
        if not aguas:
            continue

        if op["trel_dir_idx"] == 1:
            dir_vao = XYZ.BasisX
            dir_rep = XYZ.BasisY
        elif op["trel_dir_idx"] == 2:
            dir_vao = XYZ.BasisY
            dir_rep = XYZ.BasisX
        else:
            maior_agua = max(aguas, key=lambda a: a["face"].Area)
            u_h = maior_agua["u_horiz"]
            dir_rep = XYZ(u_h.X, u_h.Y, 0).Normalize()
            dir_vao = XYZ(-dir_rep.Y, dir_rep.X, 0).Normalize()

        todos_pts_3d = []
        for a in aguas:
            for poly in a["loops_uv"]:
                for u_val, v_val in poly:
                    todos_pts_3d.append(ponto_3d_agua(a, u_val, v_val, desconto_ft))
        if not todos_pts_3d:
            continue

        reps = [p.DotProduct(dir_rep) for p in todos_pts_3d]
        vaos = [p.DotProduct(dir_vao) for p in todos_pts_3d]
        rep_min, rep_max = min(reps), max(reps)
        vao_min, vao_max = min(vaos), max(vaos)

        posicoes = gerar_posicoes_uniformes(rep_min, rep_max, esp_linhas_ft, 0.35 * FT)

        for idx_t, r_pos in enumerate(posicoes, 1):
            n_amostras = 200
            passo_s = (vao_max - vao_min) / float(n_amostras)
            am = []
            for k in range(n_amostras + 1):
                s_val = vao_min + k * passo_s
                pt_xy = dir_rep * r_pos + dir_vao * s_val
                z_max_s = None
                for a in aguas:
                    z_c = obter_z_agua_em_xy(a, pt_xy.X, pt_xy.Y, desconto_ft)
                    if z_c is not None and (z_max_s is None or z_c > z_max_s):
                        z_max_s = z_c
                if z_max_s is not None:
                    am.append((s_val, z_max_s))

            if len(am) < 3:
                ignoradas += 1
                continue

            s_ini, s_fim = am[0][0], am[-1][0]
            if (s_fim - s_ini) < 0.80 * FT:
                ignoradas += 1
                continue

            xa_s = s_ini + recuo_ft
            xb_s = s_fim - recuo_ft
            if (xb_s - xa_s) < 0.80 * FT:
                ignoradas += 1
                continue

            z_el = _z_no_perfil(am, s_ini, am[0][1])
            z_er = _z_no_perfil(am, s_fim, am[-1][1])
            s_pico, z_pico = max(am, key=lambda it: it[1])

            agua1_dir = 0
            s_pico_eff = s_pico
            z_pico_eff = z_pico

            if (s_fim - s_pico) < 0.35 * FT and (z_pico - z_el) >= 0.08 * FT:
                agua1_dir = 1
                s_pico_eff = s_fim
                z_pico_eff = z_er
            elif (s_pico - s_ini) < 0.35 * FT and (z_pico - z_er) >= 0.08 * FT:
                agua1_dir = -1
                s_pico_eff = s_ini
                z_pico_eff = z_el
            elif abs(z_er - z_el) < 0.08 * FT and (z_pico - max(z_el, z_er)) < 0.12 * FT:
                # Telhado plano / quase plano
                if forma == 0:
                    ignoradas += 1
                    continue
                agua1_dir = 1
                s_pico_eff = s_fim
                z_pico_eff = z_er
            elif (s_pico - s_ini) < 0.35 * FT or (s_fim - s_pico) < 0.35 * FT:
                if forma == 0:
                    ignoradas += 1
                    continue
                agua1_dir = 1
                s_pico_eff = s_fim
                z_pico_eff = z_er

            am_span = [z for s_val, z in am if xa_s - 1e-4 <= s_val <= xb_s + 1e-4]
            z_min_span = min(am_span) if am_span else min(z_el, z_er)

            z_lb_abs, _, _, _, _, _ = _calc_z_lb_trelica(
                forma, agua1_dir, xa_s, xb_s, s_ini, s_fim, z_el, z_er, s_pico_eff, z_pico_eff,
                z_min_span, bh_ft, h_tr_ft
            )

            if forma == 0 and (z_pico_eff - (z_lb_abs + bh_ft)) < 0.25 * FT:
                ignoradas += 1
                continue

            s_mid = 0.5 * (xa_s + xb_s)

            def c(v_ft):
                return round(v_ft * 30.48, 1)

            trelicas.append({
                "idx": idx_t, "r_pos": r_pos, "dir_rep": dir_rep, "dir_vao": dir_vao,
                "s_mid": s_mid, "z_lb": z_lb_abs,
                "geo": {
                    "forma":      forma,
                    "malha":      int(op["trel_malha_idx"]),
                    "agua1_dir":  agua1_dir,
                    "xa":         c(xa_s - s_mid),
                    "xb":         c(xb_s - s_mid),
                    "xel":        c(s_ini - s_mid),
                    "xer":        c(s_fim - s_mid),
                    "zel":        c(z_el - z_lb_abs),
                    "zer":        c(z_er - z_lb_abs),
                    "xp":         c(s_pico_eff - s_mid),
                    "zp":         c(z_pico_eff - z_lb_abs),
                    "z_min_span": c(z_min_span - z_lb_abs),
                    "h_tr":       round(op["trel_h_paralela"], 1),
                    "esp_painel": round(op["trel_esp_painel"], 1),
                    "avanco":     round(op["trel_avanco"], 1),
                    "b_perf":     int(op["trel_b_perf_idx"]),
                    "b_b":        round(op["trel_b_b"], 1),
                    "b_h":        round(op["trel_b_h"], 1),
                    "b_chapa":    round(op["trel_b_chapa"], 2),
                    "m_perf":     int(op["trel_m_perf_idx"]),
                    "m_b":        round(op["trel_m_b"], 1),
                    "m_h":        round(op["trel_m_h"], 1),
                    "m_chapa":    round(op["trel_m_chapa"], 2),
                    "chapas":     1 if op["trel_chapas"] else 0,
                }
            })

    ids_criados = []
    familias_novas = 0
    erros_familia = []

    if trelicas:
        app = doc.Application
        template = _achar_template_generico(app, prefs)
        if not template:
            forms.alert("Modelo de família 'Modelo genérico' (.rft) não encontrado.", exitscript=True)

        pasta_tmp = os.path.join(tempfile.gettempdir(), "AutoBIM_TrelicasMetalicas")
        arquivos = {}
        for trel in trelicas:
            nome = _nome_familia_trel(trel["geo"])
            trel["nome"] = nome
            if nome in arquivos:
                continue
            if _achar_simbolo_existente(nome) is not None:
                arquivos[nome] = None
                continue
            caminho = os.path.join(pasta_tmp, nome + u".rfa")
            erro = _construir_familia_trelica_metalica(app, template, trel["geo"], caminho)
            if erro:
                erros_familia.append(erro)
                arquivos[nome] = False
            else:
                arquivos[nome] = caminho
                familias_novas += 1

        with TransactionGroup(doc, "Estrutura Metálica: Treliças") as tg:
            tg.Start()
            with Transaction(doc, "Estrutura Metálica: Treliças") as t:
                t.Start()
                simbolos = {}
                opts_load = _FamLoadOpts()
                for nome, caminho in arquivos.items():
                    if caminho is False:
                        continue
                    if caminho:
                        try:
                            doc.LoadFamily(caminho, opts_load)
                        except Exception as e_ld:
                            erros_familia.append(u"Carregar família: {}".format(e_ld))
                    sym = _achar_simbolo_existente(nome)
                    if sym is not None:
                        if not sym.IsActive:
                            sym.Activate()
                        simbolos[nome] = sym
                doc.Regenerate()

                criadas_z = []
                for trel in trelicas:
                    sym = simbolos.get(trel["nome"])
                    if sym is None:
                        continue
                    p_xy = trel["dir_rep"] * trel["r_pos"] + trel["dir_vao"] * trel["s_mid"]
                    try:
                        nivel = _nivel_para_z(trel["z_lb"])
                        if nivel is not None:
                            pt_inst = XYZ(p_xy.X, p_xy.Y, trel["z_lb"] - nivel.Elevation)
                            inst = doc.Create.NewFamilyInstance(pt_inst, sym, nivel, StructuralType.NonStructural)
                        else:
                            pt_inst = XYZ(p_xy.X, p_xy.Y, trel["z_lb"])
                            inst = doc.Create.NewFamilyInstance(pt_inst, sym, StructuralType.NonStructural)
                        ang = math.atan2(trel["dir_vao"].Y, trel["dir_vao"].X)
                        if abs(ang) > 1e-6:
                            eixo = Line.CreateBound(XYZ(p_xy.X, p_xy.Y, 0.0), XYZ(p_xy.X, p_xy.Y, 1.0))
                            ElementTransformUtils.RotateElement(doc, inst.Id, eixo, ang)
                        criadas_z.append((inst.Id, trel["z_lb"]))
                        ids_criados.append(inst.Id)
                    except Exception as e_inst:
                        erros_familia.append(u"Inserir treliça {:02d}: {}".format(trel["idx"], e_inst))

                doc.Regenerate()
                for eid, z_alvo in criadas_z:
                    inst = doc.GetElement(eid)
                    loc_pt = getattr(getattr(inst, "Location", None), "Point", None)
                    if loc_pt is None:
                        continue
                    dz_corr = z_alvo - loc_pt.Z
                    if abs(dz_corr) > 0.002:
                        try:
                            ElementTransformUtils.MoveElement(doc, eid, XYZ(0, 0, dz_corr))
                        except Exception:
                            pass
                        doc.Regenerate()
                        loc_pt2 = getattr(getattr(inst, "Location", None), "Point", None)
                        if loc_pt2 is not None and abs(z_alvo - loc_pt2.Z) > 0.002:
                            dz_corr2 = z_alvo - loc_pt2.Z
                            for bip in (
                                BuiltInParameter.INSTANCE_FREE_HOST_OFFSET_PARAM,
                                BuiltInParameter.INSTANCE_ELEVATION_PARAM,
                            ):
                                p_off = inst.get_Parameter(bip)
                                if p_off is not None and not p_off.IsReadOnly and p_off.StorageType == StorageType.Double:
                                    p_off.Set(p_off.AsDouble() + dz_corr2)
                                    break

                t.Commit()

            if op["trel_grp_idx"] == 1 and len(ids_criados) > 1:
                agrupar_lote_em_transacao([(ids_criados, u"Estrutura Metálica - Treliças")], "Agrupar Treliças Metálicas")
            tg.Assimilate()

    msg = u"Treliças Metálicas concluídas!\n\n✔ Treliças inseridas: {}\n✔ Famílias novas geradas: {}\n".format(
        len(ids_criados), familias_novas)
    if ignoradas:
        msg += u"\n⚠ Posições ignoradas (vão insuficiente ou plano incompatível): {}\n".format(ignoradas)
    if erros_familia:
        msg += u"\n✘ Erros:\n" + u"\n".join(erros_familia[:5])
    forms.alert(msg, title="Estrutura Metálica: Treliças")
