# -*- coding: utf-8 -*-
__title__ = "Section Box\n3D Rápido"
__author__ = "AutoBIM"
__version__ = "Versão 1.0"
__doc__ = """Recorta instantaneamente a vista 3D ao redor dos elementos selecionados, de um Ambiente (Room) inteiro ou de um Pavimento (Level), com margem configurável e reset em 1 clique."""

import traceback
from pyrevit import forms
import Snippets._audit_viz as av
try:
    reload(av)
except Exception:
    pass

try:
    av.run_section_box_3d()
except SystemExit:
    raise
except Exception:
    forms.alert(traceback.format_exc(), title="Erro em Section Box 3D Rápido")
