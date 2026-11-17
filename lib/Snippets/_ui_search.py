# -*- coding: utf-8 -*-
"""Helpers reutilizaveis de pesquisa em ComboBox para janelas WPF do AutoBIM."""

import unicodedata

try:
    unicode
except NameError:
    unicode = str


def normalizar_texto_busca(texto):
    """Converte para minusculas e remove acentos para comparacao tolerante."""
    s = unicode(texto or u"").strip().lower()
    if not s:
        return u""
    try:
        decomp = unicodedata.normalize("NFD", s)
        return u"".join(ch for ch in decomp if unicodedata.category(ch) != "Mn")
    except Exception:
        return s


def filtrar_indices_por_busca(itens, termo):
    """Retorna os indices originais de `itens` que casam com todos os tokens de `termo`."""
    norm_q = normalizar_texto_busca(termo)
    if not norm_q:
        return list(range(len(itens)))
    tokens = [t for t in norm_q.split() if t]
    if not tokens:
        return list(range(len(itens)))
    res = []
    for idx, item in enumerate(itens):
        norm_it = normalizar_texto_busca(item)
        ok = True
        for tok in tokens:
            if tok not in norm_it:
                ok = False
                break
        if ok:
            res.append(idx)
    return res


class SearchableCombo(object):
    """Conecta uma barra TextBox de pesquisa a um ComboBox WPF preservando os indices originais."""

    def __init__(self, combo, search_box, items, clear_btn=None, placeholder_el=None, count_el=None, initial_index=0):
        self.combo = combo
        self.search_box = search_box
        self.clear_btn = clear_btn
        self.placeholder_el = placeholder_el
        self.count_el = count_el

        self._all_items = [unicode(x) for x in (items or [])]
        self._filtered_indices = list(range(len(self._all_items)))
        self._selected_orig_idx = initial_index if (0 <= initial_index < len(self._all_items)) else (-1 if not self._all_items else 0)
        self._last_valid_orig_idx = self._selected_orig_idx
        self._updating = False

        self._popular_combo(self._filtered_indices, alvo_orig=self._selected_orig_idx)
        self._atualizar_adornos()

        self.combo.SelectionChanged += self._on_combo_changed
        self.search_box.TextChanged += self._on_search_changed
        self.search_box.PreviewKeyDown += self._on_search_keydown
        if self.clear_btn is not None:
            self.clear_btn.Click += self._on_clear_click

    def selected_index(self):
        """Retorna o indice na lista original (ou -1 se a busca atual nao tiver itens selecionados)."""
        idx_cb = self.combo.SelectedIndex
        if 0 <= idx_cb < len(self._filtered_indices):
            return self._filtered_indices[idx_cb]
        return -1

    def selected_text(self):
        orig = self.selected_index()
        if 0 <= orig < len(self._all_items):
            return self._all_items[orig]
        return u""

    def set_items(self, items, initial_index=0):
        self._all_items = [unicode(x) for x in (items or [])]
        self._selected_orig_idx = initial_index if (0 <= initial_index < len(self._all_items)) else (-1 if not self._all_items else 0)
        self._last_valid_orig_idx = self._selected_orig_idx
        self._aplicar_filtro()

    def set_index(self, orig_idx):
        """Seleciona item pelo indice original (respeitando filtro ativo ou limpando se necessario)."""
        if not (0 <= orig_idx < len(self._all_items)):
            return
        self._last_valid_orig_idx = orig_idx
        if orig_idx in self._filtered_indices:
            self._popular_combo(self._filtered_indices, alvo_orig=orig_idx)
        else:
            self.search_box.Text = u""
            self._popular_combo(self._filtered_indices, alvo_orig=orig_idx)
        self._atualizar_adornos()

    def _popular_combo(self, indices, alvo_orig=-1):
        self._updating = True
        try:
            self.combo.Items.Clear()
            for orig_i in indices:
                self.combo.Items.Add(self._all_items[orig_i])
            if indices:
                pos = 0
                if alvo_orig in indices:
                    pos = indices.index(alvo_orig)
                self.combo.SelectedIndex = pos
                self._selected_orig_idx = indices[pos]
                self._last_valid_orig_idx = self._selected_orig_idx
            else:
                self.combo.SelectedIndex = -1
                self._selected_orig_idx = -1
        finally:
            self._updating = False

    def _atualizar_adornos(self):
        from System.Windows import Visibility
        from System.Windows.Media import SolidColorBrush, Color

        termo = unicode(self.search_box.Text or u"")
        tem_texto = len(termo) > 0

        if self.placeholder_el is not None:
            self.placeholder_el.Visibility = Visibility.Collapsed if tem_texto else Visibility.Visible
        if self.clear_btn is not None:
            self.clear_btn.Visibility = Visibility.Visible if tem_texto else Visibility.Collapsed

        if self.count_el is not None:
            total = len(self._all_items)
            visiveis = len(self._filtered_indices)
            if tem_texto:
                if visiveis == 0:
                    self.count_el.Text = u"0/{} encontrado(s)".format(total)
                    self.count_el.Foreground = SolidColorBrush(Color.FromRgb(196, 43, 28))
                else:
                    self.count_el.Text = u"{}/{} filtrado(s)".format(visiveis, total)
                    self.count_el.Foreground = SolidColorBrush(Color.FromRgb(0, 120, 212))
            else:
                self.count_el.Text = u"{} itens".format(total) if total > 0 else u""
                self.count_el.Foreground = SolidColorBrush(Color.FromRgb(102, 112, 133))

    def _aplicar_filtro(self):
        termo = unicode(self.search_box.Text or u"")
        novos_indices = filtrar_indices_por_busca(self._all_items, termo)
        self._filtered_indices = novos_indices
        alvo = self._selected_orig_idx if self._selected_orig_idx >= 0 else self._last_valid_orig_idx
        self._popular_combo(novos_indices, alvo_orig=alvo)
        self._atualizar_adornos()

    def _on_combo_changed(self, sender, args):
        if self._updating:
            return
        idx_cb = self.combo.SelectedIndex
        if 0 <= idx_cb < len(self._filtered_indices):
            self._selected_orig_idx = self._filtered_indices[idx_cb]
            self._last_valid_orig_idx = self._selected_orig_idx

    def _on_search_changed(self, sender, args):
        self._aplicar_filtro()

    def _on_clear_click(self, sender, args):
        self.search_box.Text = u""
        self.search_box.Focus()

    def _on_search_keydown(self, sender, args):
        from System.Windows.Input import Key
        if args.Key == Key.Down:
            if len(self._filtered_indices) > 0:
                self.combo.Focus()
                self.combo.IsDropDownOpen = True
            args.Handled = True
        elif args.Key == Key.Escape:
            if unicode(self.search_box.Text or u""):
                self.search_box.Text = u""
                args.Handled = True

    @staticmethod
    def attach_auto(combo, items, placeholder=u"Pesquisar...", initial_index=0):
        """Envolve dinamicamente um ComboBox existente (em StackPanel, Grid ou Border) com barra de pesquisa."""
        import System
        from System.ComponentModel import DependencyPropertyDescriptor
        from System.Windows import (
            CornerRadius, GridLength, GridUnitType, HorizontalAlignment,
            TextTrimming, Thickness, UIElement, VerticalAlignment, Visibility,
        )
        from System.Windows.Controls import (
            Border, Button, ColumnDefinition, DockPanel, Grid,
            Orientation, Panel, StackPanel, TextBlock, TextBox,
        )
        from System.Windows.Input import Cursors
        from System.Windows.Media import Brushes, Color, Geometry, PenLineCap, SolidColorBrush
        from System.Windows.Shapes import Path

        parent = getattr(combo, "Parent", None)
        if parent is None:
            return None

        orig_margin = combo.Margin
        orig_halign = combo.HorizontalAlignment
        orig_valign = combo.VerticalAlignment
        orig_width  = combo.Width
        orig_vis    = combo.Visibility

        is_grid = isinstance(parent, Grid)
        is_dock = isinstance(parent, DockPanel)
        g_row   = Grid.GetRow(combo) if is_grid else 0
        g_col   = Grid.GetColumn(combo) if is_grid else 0
        g_rspan = Grid.GetRowSpan(combo) if is_grid else 1
        g_cspan = Grid.GetColumnSpan(combo) if is_grid else 1
        d_dock  = DockPanel.GetDock(combo) if is_dock else None

        if is_grid and 0 <= g_row < parent.RowDefinitions.Count:
            try:
                rd = parent.RowDefinitions[g_row]
                if not rd.Height.IsStar:
                    rd.Height = GridLength.Auto
            except Exception:
                pass

        slot_idx = -1
        if isinstance(parent, Panel):
            slot_idx = parent.Children.IndexOf(combo)
            if slot_idx >= 0:
                parent.Children.RemoveAt(slot_idx)
        elif hasattr(parent, "Child") and parent.Child == combo:
            parent.Child = None
        else:
            return None

        wrapper = StackPanel()
        wrapper.Orientation = Orientation.Vertical
        wrapper.Margin = orig_margin
        wrapper.HorizontalAlignment = orig_halign
        wrapper.VerticalAlignment = VerticalAlignment.Center if (is_grid and orig_valign == VerticalAlignment.Stretch) else orig_valign
        if not System.Double.IsNaN(orig_width):
            wrapper.Width = orig_width
        wrapper.Visibility = orig_vis

        if is_grid:
            Grid.SetRow(wrapper, g_row)
            Grid.SetColumn(wrapper, g_col)
            Grid.SetRowSpan(wrapper, g_rspan)
            Grid.SetColumnSpan(wrapper, g_cspan)
            combo.ClearValue(Grid.RowProperty)
            combo.ClearValue(Grid.ColumnProperty)
            combo.ClearValue(Grid.RowSpanProperty)
            combo.ClearValue(Grid.ColumnSpanProperty)
        if is_dock and d_dock is not None:
            DockPanel.SetDock(wrapper, d_dock)
            combo.ClearValue(DockPanel.DockProperty)

        b_border = Border()
        b_border.Background = Brushes.White
        b_border.BorderBrush = SolidColorBrush(Color.FromRgb(200, 209, 220))
        b_border.BorderThickness = Thickness(1)
        b_border.CornerRadius = CornerRadius(4, 4, 0, 0)
        b_border.Padding = Thickness(6, 2, 4, 2)

        g_bar = Grid()
        g_bar.Height = 20.0
        for w_spec in (
            GridLength(17.0, GridUnitType.Pixel),
            GridLength(1.0, GridUnitType.Star),
            GridLength.Auto,
            GridLength.Auto,
        ):
            cd = ColumnDefinition()
            cd.Width = w_spec
            g_bar.ColumnDefinitions.Add(cd)

        icon = Path()
        icon.Data = Geometry.Parse("M 10.5,6 A 4.5,4.5 0 1 1 1.5,6 A 4.5,4.5 0 1 1 10.5,6 M 9.2,9.2 L 13.5,13.5")
        icon.Stroke = SolidColorBrush(Color.FromRgb(102, 112, 133))
        icon.StrokeThickness = 1.4
        icon.StrokeStartLineCap = PenLineCap.Round
        icon.StrokeEndLineCap = PenLineCap.Round
        icon.Width = 14.0
        icon.Height = 14.0
        icon.VerticalAlignment = VerticalAlignment.Center
        icon.HorizontalAlignment = HorizontalAlignment.Center
        icon.IsHitTestVisible = False
        Grid.SetColumn(icon, 0)
        g_bar.Children.Add(icon)

        g_txt = Grid()
        g_txt.Margin = Thickness(4, 0, 2, 0)
        Grid.SetColumn(g_txt, 1)

        ph_tb = TextBlock()
        ph_tb.Text = unicode(placeholder or u"Pesquisar...")
        ph_tb.Foreground = SolidColorBrush(Color.FromRgb(138, 148, 166))
        ph_tb.VerticalAlignment = VerticalAlignment.Center
        ph_tb.TextTrimming = TextTrimming.CharacterEllipsis
        ph_tb.IsHitTestVisible = False
        g_txt.Children.Add(ph_tb)

        tb_search = TextBox()
        tb_search.Background = Brushes.Transparent
        tb_search.BorderThickness = Thickness(0)
        tb_search.VerticalContentAlignment = VerticalAlignment.Center
        tb_search.Padding = Thickness(0)
        g_txt.Children.Add(tb_search)
        g_bar.Children.Add(g_txt)

        cnt_tb = TextBlock()
        cnt_tb.FontSize = 10.5
        cnt_tb.Foreground = SolidColorBrush(Color.FromRgb(102, 112, 133))
        cnt_tb.VerticalAlignment = VerticalAlignment.Center
        cnt_tb.Margin = Thickness(4, 0, 2, 0)
        cnt_tb.IsHitTestVisible = False
        Grid.SetColumn(cnt_tb, 2)
        g_bar.Children.Add(cnt_tb)

        clear_btn = Button()
        clear_btn.Content = u"✕"
        clear_btn.Width = 18.0
        clear_btn.Height = 18.0
        clear_btn.Background = Brushes.Transparent
        clear_btn.BorderThickness = Thickness(0)
        clear_btn.Foreground = SolidColorBrush(Color.FromRgb(102, 112, 133))
        clear_btn.FontSize = 10.5
        clear_btn.Cursor = Cursors.Hand
        clear_btn.Visibility = Visibility.Collapsed
        clear_btn.ToolTip = u"Limpar pesquisa"
        Grid.SetColumn(clear_btn, 3)
        g_bar.Children.Add(clear_btn)

        b_border.Child = g_bar

        combo.Margin = Thickness(0, -1, 0, 0)
        combo.HorizontalAlignment = HorizontalAlignment.Stretch

        wrapper.Children.Add(b_border)
        wrapper.Children.Add(combo)

        if isinstance(parent, Panel):
            parent.Children.Insert(slot_idx, wrapper)
        else:
            parent.Child = wrapper

        try:
            dp_vis = DependencyPropertyDescriptor.FromProperty(UIElement.VisibilityProperty, combo.GetType())
            if dp_vis is not None:
                def _on_vis(s, e):
                    wrapper.Visibility = combo.Visibility
                dp_vis.AddValueChanged(combo, System.EventHandler(_on_vis))
            dp_en = DependencyPropertyDescriptor.FromProperty(UIElement.IsEnabledProperty, combo.GetType())
            if dp_en is not None:
                def _on_en(s, e):
                    b_border.IsEnabled = combo.IsEnabled
                    b_border.Opacity = 1.0 if combo.IsEnabled else 0.55
                dp_en.AddValueChanged(combo, System.EventHandler(_on_en))
                _on_en(None, None)
        except Exception:
            pass

        ctrl = SearchableCombo(
            combo,
            tb_search,
            items,
            clear_btn=clear_btn,
            placeholder_el=ph_tb,
            count_el=cnt_tb,
            initial_index=initial_index,
        )
        ctrl.container = wrapper
        return ctrl
