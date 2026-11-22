# -*- coding: utf-8 -*-
__title__ = "Detector de\nConflitos"
__author__ = "AutoBIM"
__version__ = "Versão 1.0"
__doc__ = """Detecta colisões e sobreposições comuns na vista ativa (Paredes × Estrutura, Portas/Janelas × Pilares/Vigas, Forros × Esquadrias e elementos duplicados), destacando em vermelho ou abrindo o conflito no 3D."""

import traceback
from pyrevit import forms
import Snippets._audit_viz as av
try:
    reload(av)
except Exception:
    pass

try:
    av.run_detector_conflitos()
except SystemExit:
    raise
except Exception:
    forms.alert(traceback.format_exc(), title="Erro em Detector de Conflitos")
