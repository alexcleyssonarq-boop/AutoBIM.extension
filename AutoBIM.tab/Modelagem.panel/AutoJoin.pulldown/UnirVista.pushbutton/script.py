# -*- coding: utf-8 -*-
__title__ = "Unir: na vista ativa"
__author__ = "AutoBIM"
__version__ = "Versão 1.0"
__doc__ = """Une e corrige a ordem de corte automaticamente de todos os elementos visíveis na vista ativa (ideal para Cortes, Plantas e 3D).

Prioridade Inteligente:
Une Pilares, Vigas, Pisos/Lajes, Paredes e Forros que se interceptam na vista ativa e garante que a estrutura corte a alvenaria e os acabamentos."""

import Snippets._auto_join as aj
try:
    reload(aj)
except Exception:
    pass

aj.run(aj.MODO_VISTA)
