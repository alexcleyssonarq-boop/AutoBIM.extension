# -*- coding: utf-8 -*-
__title__ = "Lista de Corte e\nQuantitativo Cobertura"
__author__ = "AutoBIM"
__version__ = "Versão 1.0"
__doc__ = """Gera Tabela nativa no Revit (AutoBIM - Lista de Corte) e planilha CSV/Excel com quantitativo completo de Madeiramento, Estrutura Metálica 3D, Treliças, Pontaletes, Montantes de Vidraça Inclinada e Calhas/Rufos, incluindo otimização 1D de barras comerciais (FFD)."""

import traceback
from pyrevit import forms
import Snippets._roof_extras as rx
try:
    reload(rx)
except Exception:
    pass

try:
    rx.run_quantitativo_cobertura()
except SystemExit:
    raise
except Exception:
    forms.alert(traceback.format_exc(), title="Erro em Lista de Corte de Cobertura")
