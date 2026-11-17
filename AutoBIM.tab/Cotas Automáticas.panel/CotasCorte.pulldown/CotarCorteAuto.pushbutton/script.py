# -*- coding: utf-8 -*-
__title__ = "Cota Automática\nno Corte"
__author__ = "AutoBIM"
__version__ = "Versão 1.0"
__doc__ = """Cota automaticamente todo o Corte na vertical em 1 clique:
- Cadeia vertical externa na lateral livre (pisos/lajes/forro/topo parede/beiral/cumeeira + altura total);
- Pé-direitos internos (por Ambiente ou vão entre paredes, inclusive sem forro);
- Portas e Janelas visíveis."""

import traceback
from pyrevit import forms
import Snippets._section_dims as sd
try:
    reload(sd)
except Exception:
    pass

try:
    sd.run_cotar_corte_auto(abrir_config=False)
except SystemExit:
    raise
except Exception:
    forms.alert(traceback.format_exc(), title="Erro em Cota Automática no Corte")
