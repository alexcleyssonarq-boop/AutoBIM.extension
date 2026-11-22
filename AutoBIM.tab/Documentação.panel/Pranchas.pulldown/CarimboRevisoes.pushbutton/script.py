# -*- coding: utf-8 -*-
__title__ = "Carimbo &\nRevisões"
__author__ = "AutoBIM"
__version__ = "Versão 1.0"
__doc__ = """Edita em lote os campos do carimbo (Autor, Verificado, Aprovado, Data, Folha X de Y e parâmetros extras) e aplica, cria ou remove Revisões em múltiplas pranchas de uma só vez."""

import traceback
from pyrevit import forms
import Snippets._doc_sheets as ds
try:
    reload(ds)
except Exception:
    pass

try:
    ds.run_carimbo_revisoes()
except SystemExit:
    raise
except Exception:
    forms.alert(traceback.format_exc(), title="Erro em Carimbo e Revisões em Lote")
