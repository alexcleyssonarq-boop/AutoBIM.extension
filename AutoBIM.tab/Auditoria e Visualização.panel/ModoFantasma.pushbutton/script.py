# -*- coding: utf-8 -*-
__title__ = "Modo\nFantasma"
__author__ = "AutoBIM"
__version__ = "Versão 1.0"
__doc__ = """Deixa o restante da vista em transparência e meio-tom (Ghost Mode), mantendo em destaque apenas os elementos selecionados ou filtrados por Categoria, Nível ou Tipo, com reset em 1 clique."""

import traceback
from pyrevit import forms
import Snippets._audit_viz as av
try:
    reload(av)
except Exception:
    pass

try:
    av.run_modo_fantasma()
except SystemExit:
    raise
except Exception:
    forms.alert(traceback.format_exc(), title="Erro em Modo Fantasma")
