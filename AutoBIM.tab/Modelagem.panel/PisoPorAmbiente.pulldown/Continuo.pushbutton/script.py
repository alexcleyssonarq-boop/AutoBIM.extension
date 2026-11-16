# -*- coding: utf-8 -*-
__title__ = "Piso: contínuo"
__author__ = "AutoBIM"
__version__ = "Versão 1.1"
__doc__ = """Cria um piso único e contínuo unindo todos os ambientes selecionados e passando pelos vãos das portas.

Contínuo:
Ao selecionar múltiplos ambientes, o comando funde os contornos dos ambientes e os vãos brutos das portas (incluindo forras/marcos) em um único elemento de piso contínuo, sem emendas nas soleiras."""

import Snippets._floors_by_room as fbr
try:
    reload(fbr)
except Exception:
    pass

fbr.run(fbr.MODO_CONTINUO)
