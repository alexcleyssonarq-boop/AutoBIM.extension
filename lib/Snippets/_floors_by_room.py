# -*- coding: utf-8 -*-
"""Lógica compartilhada dos botões de Piso por Ambiente (sem tratamento, soleira separada, contínuo)."""

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

# Preenchidos a cada execução em run()
doc   = None
uidoc = None
view  = None

FT = 1.0 / 0.3048             # metros -> pés
OVERLAP_INTERNO = 0.03 * FT   # 3 cm de sobreposição dentro do ambiente (para a união)
MIN_LARGURA = 0.05 * FT       # vãos menores que 5 cm são ignorados

MODO_SEM      = "sem"
MODO_SOLEIRA  = "soleira"
MODO_CONTINUO = "continuo"

OPCAO_MESMO_PISO = u"<Mesmo tipo do piso do ambiente>"

TEXTO_MODO = {
    MODO_SEM:      u"Portas: sem tratamento (o piso para na face da parede)",
    MODO_SOLEIRA:  u"Portas: soleira separada (permite escolher outro tipo/material para a soleira)",
    MODO_CONTINUO: u"Portas: contínuo (o piso avança pelo vão, em uma peça só)",
}

# Arquivo JSON local para lembrar com 100% de confiabilidade as últimas escolhas (inclusive com acentos)
SETTINGS_PATH = os.path.join(os.path.dirname(__file__), "_floors_settings.json")


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
# JANELA DE OPÇÕES
# =============================================================================
XAML = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Piso por Ambiente" Width="440" SizeToContent="Height"
        WindowStartupLocation="CenterScreen" ResizeMode="NoResize"
        Background="#F3F3F3" FontFamily="Segoe UI" FontSize="12">
    <StackPanel Margin="18">
        <Border Background="#E3EEF9" BorderBrush="#0078D4" BorderThickness="0,0,0,2"
                Padding="10,8" Margin="0,0,0,14">
            <TextBlock x:Name="tx_modo" TextWrapping="Wrap" FontWeight="SemiBold"
                       Foreground="#1F3A57"/>
        </Border>

        <TextBlock Text="Tipo de piso do ambiente" FontWeight="SemiBold"/>
        <ComboBox x:Name="cb_floor" Margin="0,3,0,12" Height="26"/>

        <StackPanel x:Name="sp_soleira" Margin="0,0,0,12">
            <TextBlock Text="Tipo de piso da soleira (material da soleira)" FontWeight="SemiBold"/>
            <ComboBox x:Name="cb_sill_floor" Margin="0,3,0,0" Height="26"/>
        </StackPanel>

        <Grid Margin="0,0,0,12">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="12"/>
                <ColumnDefinition Width="140"/>
            </Grid.ColumnDefinitions>
            <StackPanel Grid.Column="0">
                <TextBlock Text="Nível" FontWeight="SemiBold"/>
                <ComboBox x:Name="cb_level" Margin="0,3,0,0" Height="26"/>
            </StackPanel>
            <StackPanel Grid.Column="2">
                <TextBlock Text="Deslocamento (cm)" FontWeight="SemiBold"/>
                <TextBox x:Name="tb_offset" Margin="0,3,0,0" Height="26"
                         VerticalContentAlignment="Center" Text="0"/>
            </StackPanel>
        </Grid>

        <TextBlock Text="Contorno do ambiente" FontWeight="SemiBold"/>
        <ComboBox x:Name="cb_boundary" Margin="0,3,0,0" Height="26"/>

        <CheckBox x:Name="ck_aberturas" Margin="0,14,0,0" IsChecked="True"
                  Content="Considerar também aberturas na parede (sem porta)"/>

        <StackPanel Orientation="Horizontal" HorizontalAlignment="Right" Margin="0,20,0,0">
            <Button x:Name="btn_cancel" Content="Cancelar" Width="90" Height="30" Margin="0,0,8,0"/>
            <Button x:Name="btn_ok" Content="Selecionar ambientes" Width="150" Height="30"
                    IsDefault="True" Background="#0078D4" Foreground="White"/>
        </StackPanel>
    </StackPanel>
</Window>
"""


class OpcoesWindow(forms.WPFWindow):
    def __init__(self, modo, nomes_pisos, nomes_soleira, nomes_niveis, padroes):
        forms.WPFWindow.__init__(self, XAML, literal_string=True)
        self.confirmado = False
        self.resultado = {}

        self._cb_floor      = self.FindName("cb_floor")
        self._sp_soleira    = self.FindName("sp_soleira")
        self._cb_sill_floor = self.FindName("cb_sill_floor")
        self._cb_level      = self.FindName("cb_level")
        self._tb_offset     = self.FindName("tb_offset")
        self._cb_boundary   = self.FindName("cb_boundary")
        self._ck_ab         = self.FindName("ck_aberturas")
        self.FindName("tx_modo").Text = TEXTO_MODO[modo]

        for n in nomes_pisos:
            self._cb_floor.Items.Add(n)
        for n in nomes_soleira:
            self._cb_sill_floor.Items.Add(n)
        for n in nomes_niveis:
            self._cb_level.Items.Add(n)
        for n in (u"Face de acabamento", u"Eixo da parede"):
            self._cb_boundary.Items.Add(n)

        self._cb_floor.SelectedIndex      = padroes["floor_idx"]
        self._cb_sill_floor.SelectedIndex = padroes["sill_idx"]
        self._cb_level.SelectedIndex      = padroes["level_idx"]
        self._cb_boundary.SelectedIndex   = padroes["boundary_idx"]
        self._tb_offset.Text              = padroes["offset"]
        self._ck_ab.IsChecked             = padroes["aberturas"]

        from System.Windows import Visibility
        if modo != MODO_SOLEIRA:
            self._sp_soleira.Visibility = Visibility.Collapsed
        if modo == MODO_SEM:
            self._ck_ab.Visibility = Visibility.Collapsed

        self.FindName("btn_ok").Click     += self.on_ok
        self.FindName("btn_cancel").Click += self.on_cancel

    def on_cancel(self, sender, args):
        self.confirmado = False
        self.Close()

    def on_ok(self, sender, args):
        try:
            offset_cm = float(self._tb_offset.Text.replace(",", "."))
        except Exception:
            forms.alert("Informe o deslocamento em centímetros (ex.: 0 ou -5).")
            return

        if self._cb_floor.SelectedIndex < 0 or self._cb_level.SelectedIndex < 0:
            forms.alert("Selecione o tipo de piso e o nível.")
            return

        self.resultado = {
            "floor_idx":    self._cb_floor.SelectedIndex,
            "sill_idx":     self._cb_sill_floor.SelectedIndex,
            "level_idx":    self._cb_level.SelectedIndex,
            "boundary_idx": self._cb_boundary.SelectedIndex,
            "offset_cm":    offset_cm,
            "aberturas":    bool(self._ck_ab.IsChecked),
        }
        self.confirmado = True
        self.Close()


# =============================================================================
# FUNÇÕES DE GEOMETRIA
# =============================================================================
def flatten(curve, z):
    """Move a curva para a elevação z, mantendo arcos como arcos."""
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


def make_ccw(items):
    """items = [(curva, elemento)]. Devolve a lista no sentido anti-horário."""
    pts = [c.GetEndPoint(0) for c, _ in items]
    if signed_area_pts(pts) >= 0:
        return items
    return [(c.CreateReversed(), el) for c, el in reversed(items)]


def loop_from_items(items):
    curves = List[Curve]()
    for c, _ in items:
        curves.Add(c)
    return CurveLoop.Create(curves)


def rect_loop(pts):
    """CurveLoop retangular, sempre anti-horário."""
    if signed_area_pts(pts) < 0:
        pts = list(reversed(pts))
    curves = List[Curve]()
    for i in range(4):
        curves.Add(Line.CreateBound(pts[i], pts[(i + 1) % 4]))
    return CurveLoop.Create(curves)


def extrude(loops, altura=1.0):
    lst = List[CurveLoop]()
    for lp in loops:
        lst.Add(lp)
    return GeometryCreationUtilities.CreateExtrusionGeometry(lst, XYZ.BasisZ, altura)


def bottom_loops(solid):
    """Laços de todas as faces inferiores (normal para baixo) do sólido."""
    loops = []
    for face in solid.Faces:
        if isinstance(face, PlanarFace) and face.FaceNormal.Z < -0.99:
            for lp in face.GetEdgesAsCurveLoops():
                loops.append(lp)
    return loops if loops else None


def point_in_polygon_2d(pt, poly_pts):
    """Retorna True se o ponto (X, Y) estiver dentro do polígono 2D poly_pts."""
    x, y = pt.X, pt.Y
    inside = False
    n = len(poly_pts)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = poly_pts[i].X, poly_pts[i].Y
        xj, yj = poly_pts[j].X, poly_pts[j].Y
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / ((yj - yi) + 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def polygon_pts_from_items(items):
    """Extrai pontos 2D do contorno (incluindo tesselação de arcos)."""
    pts = []
    for c, _ in items:
        try:
            tess = c.Tessellate()
            if tess and len(tess) > 1:
                for p in list(tess)[:-1]:
                    pts.append(p)
            else:
                pts.append(c.GetEndPoint(0))
        except Exception:
            pts.append(c.GetEndPoint(0))
    return pts


# =============================================================================
# PORTAS E ABERTURAS
# =============================================================================
ID_PORTAS = ElementId(BuiltInCategory.OST_Doors)


def get_inserts(wall):
    result = []
    try:
        for eid in wall.FindInserts(True, False, False, False):
            el = doc.GetElement(eid)
            if el is not None:
                result.append(el)
    except Exception:
        pass
    return result


def door_width(inst, d):
    tipos = (inst, doc.GetElement(inst.GetTypeId()))
    # 1) Priorizar Largura Bruta (Rough Width) se for maior que a folha da porta
    larguras_encontradas = []
    for src in tipos:
        if src is None:
            continue
        for nome in ("FAMILY_ROUGH_WIDTH_PARAM", "DOOR_WIDTH", "FAMILY_WIDTH_PARAM"):
            try:
                p = src.get_Parameter(getattr(BuiltInParameter, nome))
                if p is not None and p.HasValue and p.AsDouble() > MIN_LARGURA:
                    larguras_encontradas.append(p.AsDouble())
            except Exception:
                pass
    if larguras_encontradas:
        return max(larguras_encontradas)

    try:
        bb = inst.get_BoundingBox(None)
        if bb is not None:
            dx = abs(bb.Max.X - bb.Min.X)
            dy = abs(bb.Max.Y - bb.Min.Y)
            return abs(dx * d.X) + abs(dy * d.Y)
    except Exception:
        pass
    return None


def get_wall_rough_span(wall, ins, p0, d, t_centro, w_nominal):
    """
    Obtém o intervalo exato (t_ini, t_fim) do vão bruto cortado na parede pela porta
    (incluindo forras/marcos/batentes), analisando as faces verticais de recorte da parede.
    """
    try:
        opt = Options()
        opt.DetailLevel = ViewDetailLevel.Coarse
        geom = wall.get_Geometry(opt)
        if geom is None:
            return None

        t_geradas = []
        t_todas_perp = []

        for obj in geom:
            if not isinstance(obj, Solid) or obj.Volume <= 0:
                continue
            for face in obj.Faces:
                if not isinstance(face, PlanarFace):
                    continue
                normal = face.FaceNormal
                # Face vertical perpendicular ao eixo da parede (faces das bonecas do vão)
                if abs(normal.Z) < 1e-4 and abs(abs(normal.DotProduct(d)) - 1.0) < 1e-2:
                    pt_f = face.Origin
                    t_f = (pt_f - p0).DotProduct(d)
                    t_todas_perp.append(t_f)
                    try:
                        gen_ids = wall.GetGeneratingElementIds(face)
                        if gen_ids and ins.Id in gen_ids:
                            t_geradas.append(t_f)
                    except Exception:
                        pass

        # 1) Se o Revit identificou diretamente as faces da parede geradas pelo corte da porta
        if len(t_geradas) >= 2:
            t_ini = min(t_geradas)
            t_fim = max(t_geradas)
            if (t_fim - t_ini) >= w_nominal - 0.005 * FT:
                return (t_ini, t_fim)

        # 2) Caso contrário, buscar o par de faces verticais da parede imediatamente ao redor da porta
        margem_max = (w_nominal * 0.5) + (0.20 * FT)  # até +20 cm de cada lado para marcos/forras
        esq = [t for t in t_todas_perp if (t_centro - margem_max) <= t <= (t_centro - w_nominal * 0.35)]
        dir_ = [t for t in t_todas_perp if (t_centro + w_nominal * 0.35) <= t <= (t_centro + margem_max)]
        if esq and dir_:
            # Pegar as faces do vão bruto na parede (mais próximas do centro da porta, porém >= w_nominal)
            t_ini = max(esq)
            t_fim = min(dir_)
            if (t_fim - t_ini) >= w_nominal - 0.005 * FT:
                return (t_ini, t_fim)
    except Exception:
        pass

    return None


def insert_info(ins, d, base_elev, incluir_aberturas):
    """Devolve (centro, largura) se o elemento for porta ou abertura no nível do piso."""
    try:
        if isinstance(ins, FamilyInstance):
            cat = ins.Category
            if cat is None or cat.Id != ID_PORTAS:
                return None
            loc = ins.Location
            if loc is None or not hasattr(loc, "Point"):
                return None
            w = door_width(ins, d)
            if w is None or w < MIN_LARGURA:
                return None
            return (loc.Point, w)

        if incluir_aberturas and isinstance(ins, Opening):
            rect = ins.BoundaryRect
            if rect is None or rect.Count < 2:
                return None
            p0, p1 = rect[0], rect[1]
            if min(p0.Z, p1.Z) > base_elev + 0.15 * FT:
                return None    # abertura elevada, não toca o piso
            centro = (p0 + p1) * 0.5
            w = abs((p1 - p0).DotProduct(d))
            if w < MIN_LARGURA:
                return None
            return (centro, w)
    except Exception:
        return None
    return None


def coletar_vaos(outer_items, z0, base_elev, ext_modo, incluir_aberturas, claimed, poligonos_selecionados=None):
    """
    Retorna lista de dicts com os cantos do retângulo de cada vão que pertence
    a este ambiente (sem os vãos já reivindicados por ambientes maiores).
    Se o vão desembocar dentro de outro ambiente selecionado (poligonos_selecionados),
    estende o retângulo de união em +OVERLAP_INTERNO para fundir os dois pisos em um só.
    """
    vaos = []
    for c, wall in outer_items:
        if not isinstance(wall, Wall) or not isinstance(c, Line):
            continue
        p0 = c.GetEndPoint(0)
        d = c.Direction
        d = XYZ(d.X, d.Y, 0).Normalize()
        n = XYZ(d.Y, -d.X, 0)                  # para fora do ambiente (laço anti-horário)
        comp = c.Length
        esp = wall.Width
        ext = esp if ext_modo == "finish" else esp * 0.5

        for ins in get_inserts(wall):
            chave = str(ins.Id)
            if chave in claimed:
                continue
            info = insert_info(ins, d, base_elev, incluir_aberturas)
            if info is None:
                continue
            centro, largura = info
            v = XYZ(centro.X - p0.X, centro.Y - p0.Y, 0)
            t = v.DotProduct(d)
            perp = v.DotProduct(n)
            if t <= 0 or t >= comp:
                continue
            if perp < -0.3 * FT or perp > esp + 0.3 * FT:
                continue

            claimed.add(chave)

            # Obter o vão bruto real na parede (incluindo forras/marcos)
            span_bruto = get_wall_rough_span(wall, ins, p0, d, t, largura)
            if span_bruto is not None:
                t_ini, t_fim = span_bruto
            else:
                hw = largura * 0.5
                t_ini = t - hw
                t_fim = t + hw

            a = XYZ(p0.X + d.X * t_ini, p0.Y + d.Y * t_ini, z0)
            b = XYZ(p0.X + d.X * t_fim, p0.Y + d.Y * t_fim, z0)

            def deslocar(pt, k, n=n):
                return XYZ(pt.X + n.X * k, pt.Y + n.Y * k, z0)

            # Verificar se o outro lado da porta pertence a outro ambiente selecionado
            meio_ab = XYZ((a.X + b.X) * 0.5, (a.Y + b.Y) * 0.5, z0)
            pt_alem = deslocar(meio_ab, ext + OVERLAP_INTERNO)
            conecta_outro = False
            if poligonos_selecionados:
                for poly in poligonos_selecionados:
                    if point_in_polygon_2d(pt_alem, poly):
                        conecta_outro = True
                        break

            ext_uniao = (ext + OVERLAP_INTERNO) if conecta_outro else ext

            vaos.append({
                "soleira": [a, b, deslocar(b, ext), deslocar(a, ext)],
                "uniao":   [deslocar(a, -OVERLAP_INTERNO), deslocar(b, -OVERLAP_INTERNO),
                            deslocar(b, ext_uniao), deslocar(a, ext_uniao)],
            })
    return vaos


# =============================================================================
# DADOS DO PROJETO
# =============================================================================
def nome_tipo(ft):
    try:
        p = ft.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME)
        if p is not None and p.AsString():
            return p.AsString()
    except Exception:
        pass
    return ft.Name


def nome_ambiente(room):
    try:
        return room.get_Parameter(BuiltInParameter.ROOM_NAME).AsString()
    except Exception:
        return str(room.Id)


def idx_por_nome(lista, nome, padrao=0):
    try:
        return lista.index(nome)
    except Exception:
        return padrao


# =============================================================================
# EXECUÇÃO
# =============================================================================
def run(modo):
    global doc, uidoc, view
    uidoc = __revit__.ActiveUIDocument
    doc   = uidoc.Document
    view  = doc.ActiveView

    # --- Tipos de piso e níveis ---------------------------------------------
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
    nomes_pisos = []
    vistos = {}
    for ft in floor_types:
        nome = nome_tipo(ft)
        if nome in vistos:
            vistos[nome] += 1
            nome = u"{} ({})".format(nome, vistos[nome])
        else:
            vistos[nome] = 1
        nomes_pisos.append(nome)

    nomes_soleira = [OPCAO_MESMO_PISO] + nomes_pisos

    niveis = sorted(
        FilteredElementCollector(doc).OfClass(Level).ToElements(),
        key=lambda l: l.Elevation
    )
    if not niveis:
        forms.alert("Nenhum nível encontrado no projeto.", exitscript=True)
    nomes_niveis = [u"{}  ({:.2f} m)".format(l.Name, l.Elevation * 0.3048) for l in niveis]

    # --- Preferências salvas em JSON ----------------------------------------
    prefs = carregar_preferencias()

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

    padroes = {
        "floor_idx":    idx_por_nome(nomes_pisos, prefs.get("floor", ""), 0),
        "sill_idx":     idx_por_nome(nomes_soleira, prefs.get("sill_floor", OPCAO_MESMO_PISO), 0),
        "level_idx":    nivel_vista,
        "boundary_idx": int(prefs.get("boundary", 0)),
        "offset":       str(prefs.get("offset", "0")),
        "aberturas":    bool(prefs.get("aberturas", True)),
    }

    # --- Janela de opções ---------------------------------------------------
    win = OpcoesWindow(modo, nomes_pisos, nomes_soleira, nomes_niveis, padroes)
    win.ShowDialog()

    if not win.confirmado:
        script.exit()

    op = win.resultado
    floor_type = floor_types[op["floor_idx"]]
    if op["sill_idx"] <= 0:
        sill_floor_type = floor_type
    else:
        sill_floor_type = floor_types[op["sill_idx"] - 1]

    nivel        = niveis[op["level_idx"]]
    offset_ft    = op["offset_cm"] / 30.48
    ext_modo     = "finish" if op["boundary_idx"] == 0 else "center"
    loc_contorno = (SpatialElementBoundaryLocation.Finish if ext_modo == "finish"
                    else SpatialElementBoundaryLocation.Center)

    # Salvar últimas escolhas em JSON
    novas_prefs = {
        "floor":      nomes_pisos[op["floor_idx"]],
        "boundary":   op["boundary_idx"],
        "offset":     op["offset_cm"],
        "aberturas":  op["aberturas"],
    }
    if modo == MODO_SOLEIRA:
        novas_prefs["sill_floor"] = nomes_soleira[op["sill_idx"]]
    salvar_preferencias(novas_prefs)

    # --- Seleção de ambientes (usa a seleção atual, se houver) ---------------
    rooms = []
    for eid in uidoc.Selection.GetElementIds():
        el = doc.GetElement(eid)
        if isinstance(el, Room):
            rooms.append(el)

    if not rooms:
        with forms.WarningBar(title="Selecione os Ambientes e pressione Finish"):
            rooms = revit.pick_elements_by_category(BuiltInCategory.OST_Rooms)

    rooms = [r for r in (rooms or []) if r.Area > 0]
    if not rooms:
        forms.alert("Nenhum ambiente válido selecionado.", exitscript=True)

    # Ambientes maiores primeiro: ficam com a soleira de vãos compartilhados
    rooms.sort(key=lambda r: r.Area, reverse=True)

    # --- Criação dos pisos --------------------------------------------------
    boundary_opts = SpatialElementBoundaryOptions()
    boundary_opts.SpatialElementBoundaryLocation = loc_contorno

    z0 = nivel.Elevation
    claimed = set()
    contadores = {"pisos": 0, "soleiras": 0}
    avisos = []

    def aplicar_offset(floor):
        if abs(offset_ft) < 1e-9:
            return
        try:
            p = floor.get_Parameter(BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM)
            if p is not None and not p.IsReadOnly:
                p.Set(offset_ft)
        except Exception:
            pass

    def criar_piso(lista_loops, tipo_piso=floor_type):
        lst = List[CurveLoop]()
        for lp in lista_loops:
            lst.Add(lp)
        fl = Floor.Create(doc, lst, tipo_piso.Id, nivel.Id)
        aplicar_offset(fl)
        return fl

    with Transaction(doc, "Piso por Ambiente") as t:
        t.Start()

        # Pré-extrair contornos de todos os ambientes selecionados
        dados_ambientes = []
        poligonos_selecionados = []

        for room in rooms:
            nome = nome_ambiente(room)
            try:
                seg_lists = room.GetBoundarySegments(boundary_opts)
                if not seg_lists or seg_lists.Count == 0:
                    avisos.append(u"'{}': sem contorno definido.".format(nome))
                    continue

                try:
                    base_elev = room.Level.Elevation
                except Exception:
                    base_elev = z0

                lacos = []
                for sl in seg_lists:
                    itens = []
                    for seg in sl:
                        curva = flatten(seg.GetCurve(), z0)
                        el = None
                        try:
                            if seg.ElementId != ElementId.InvalidElementId:
                                el = doc.GetElement(seg.ElementId)
                        except Exception:
                            el = None
                        itens.append((curva, el))
                    if len(itens) >= 3:
                        lacos.append(itens)

                if not lacos:
                    avisos.append(u"'{}': contorno inválido.".format(nome))
                    continue

                lacos.sort(key=lambda it: abs(signed_area_pts([c.GetEndPoint(0) for c, _ in it])),
                           reverse=True)
                outer = make_ccw(lacos[0])
                ilhas_items = lacos[1:]
                ilhas = [loop_from_items(it) for it in ilhas_items]
                poly_pts = polygon_pts_from_items(outer)

                poligonos_selecionados.append(poly_pts)
                dados_ambientes.append({
                    "room":        room,
                    "nome":        nome,
                    "base_elev":   base_elev,
                    "outer":       outer,
                    "ilhas_items": ilhas_items,
                    "ilhas":       ilhas,
                })
            except Exception as e_prep:
                avisos.append(u"'{}': {}".format(nome, str(e_prep)))

        # =====================================================================
        # MODO CONTÍNUO: Unir todos os ambientes selecionados + vãos em 1 único Piso
        # =====================================================================
        if modo == MODO_CONTINUO and dados_ambientes:
            sub_cont = SubTransaction(doc)
            sub_cont.Start()
            uniao_global_ok = False
            try:
                solidos_amb = []
                for d_amb in dados_ambientes:
                    outer       = d_amb["outer"]
                    ilhas_items = d_amb["ilhas_items"]
                    base_elev   = d_amb["base_elev"]

                    vaos = coletar_vaos(
                        outer, z0, base_elev, ext_modo, op["aberturas"],
                        claimed, poligonos_selecionados
                    )
                    d_amb["vaos"] = vaos

                    # Criar sólido base do ambiente (descontando eventuais ilhas internas)
                    sol_r = extrude([loop_from_items(outer)])
                    for it_ilha in ilhas_items:
                        try:
                            sol_ilha = extrude([loop_from_items(make_ccw(it_ilha))])
                            sol_r = BooleanOperationsUtils.ExecuteBooleanOperation(
                                sol_r, sol_ilha, BooleanOperationsType.Difference
                            )
                        except Exception:
                            pass

                    # Unir os vãos de porta deste ambiente ao sólido do ambiente
                    for v in vaos:
                        sol_r = BooleanOperationsUtils.ExecuteBooleanOperation(
                            sol_r, extrude([rect_loop(v["uniao"])]),
                            BooleanOperationsType.Union
                        )
                    solidos_amb.append(sol_r)

                # Fundir os sólidos dos ambientes conectados (suportando cadeias ex.: Sala -> Corredor -> Quarto)
                grupos_solidos = []
                pendentes = list(solidos_amb)
                while pendentes:
                    atual = pendentes.pop(0)
                    fundiu_algum = True
                    while fundiu_algum and pendentes:
                        fundiu_algum = False
                        restantes = []
                        for s_cand in pendentes:
                            try:
                                atual = BooleanOperationsUtils.ExecuteBooleanOperation(
                                    atual, s_cand, BooleanOperationsType.Union
                                )
                                fundiu_algum = True
                            except Exception:
                                restantes.append(s_cand)
                        pendentes = restantes
                    grupos_solidos.append(atual)

                loops_unificados = []
                for s_grp in grupos_solidos:
                    bl_grp = bottom_loops(s_grp)
                    if bl_grp:
                        loops_unificados.extend(bl_grp)

                if not loops_unificados:
                    raise Exception("face inferior unificada não encontrada")

                criar_piso(loops_unificados, floor_type)
                contadores["pisos"] += 1
                sub_cont.Commit()
                uniao_global_ok = True
            except Exception as e_global:
                try:
                    sub_cont.RollBack()
                except Exception:
                    pass
                # Caso a união global falhe, fazer fallback por ambiente
                uniao_global_ok = False

            if not uniao_global_ok:
                claimed.clear()

        # =====================================================================
        # MODO SEM TRATAMENTO / MODO SOLEIRA SEPARADA (ou Fallback individual)
        # =====================================================================
        if modo != MODO_CONTINUO or not uniao_global_ok:
            for d_amb in dados_ambientes:
                nome      = d_amb["nome"]
                outer     = d_amb["outer"]
                ilhas     = d_amb["ilhas"]
                base_elev = d_amb["base_elev"]

                sub = SubTransaction(doc)
                sub.Start()
                try:
                    vaos = []
                    if modo != MODO_SEM:
                        vaos = coletar_vaos(
                            outer, z0, base_elev, ext_modo, op["aberturas"],
                            claimed, poligonos_selecionados
                        )

                    soleiras_a_criar = []
                    loops_piso = None

                    if modo == MODO_CONTINUO and vaos:
                        try:
                            solido = extrude([loop_from_items(outer)])
                            for v in vaos:
                                solido = BooleanOperationsUtils.ExecuteBooleanOperation(
                                    solido, extrude([rect_loop(v["uniao"])]),
                                    BooleanOperationsType.Union)
                            bl = bottom_loops(solido)
                            if not bl:
                                raise Exception("face inferior não encontrada")
                            loops_piso = bl + ilhas
                        except Exception as e_un:
                            avisos.append(
                                u"'{}': não foi possível unir os vãos ao piso ({}); soleiras criadas separadas."
                                .format(nome, str(e_un)))
                            loops_piso = None
                            soleiras_a_criar = vaos
                    elif modo == MODO_SOLEIRA:
                        soleiras_a_criar = vaos

                    if loops_piso is None:
                        loops_piso = [loop_from_items(outer)] + ilhas

                    criar_piso(loops_piso, floor_type)
                    contadores["pisos"] += 1

                    for v in soleiras_a_criar:
                        try:
                            criar_piso([rect_loop(v["soleira"])], sill_floor_type)
                            contadores["soleiras"] += 1
                        except Exception as e_sol:
                            avisos.append(u"'{}': erro ao criar soleira ({}).".format(nome, str(e_sol)))

                    sub.Commit()

                except Exception as e_room:
                    try:
                        sub.RollBack()
                    except Exception:
                        pass
                    avisos.append(u"'{}': {}".format(nome, str(e_room)))

        t.Commit()

    # --- Resumo -------------------------------------------------------------
    msg = u"Concluído!\n\n✔ Pisos criados: {}\n".format(contadores["pisos"])
    if modo == MODO_SOLEIRA or contadores["soleiras"]:
        msg += u"✔ Soleiras criadas: {}\n".format(contadores["soleiras"])
    if avisos:
        msg += u"\n⚠ Avisos ({}):\n".format(len(avisos)) + u"\n".join(avisos[:8])
        if len(avisos) > 8:
            msg += u"\n... e mais {}.".format(len(avisos) - 8)

    forms.alert(msg, title="Piso por Ambiente", warn_icon=bool(avisos))
