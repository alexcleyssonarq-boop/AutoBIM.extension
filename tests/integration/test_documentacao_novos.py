# -*- coding: utf-8 -*-
"""Testes de integracao para os 4 novos comandos do Painel Documentacao:
- Alinhar Vistas e Titulos (_doc_sheets)
- Carimbo e Revisoes em Lote (_doc_sheets)
- Exportar Pranchas DWG Unico / Lote PDF/DWG (_doc_sheets)
- Quadro de Areas de Prefeitura (_doc_prefeitura)
"""

import ast
import builtins
import os
import sys
import types
import unittest


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
LIB_DIR = os.path.join(REPO_ROOT, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)


def _install_stubs():
    clr_mod = types.ModuleType("clr")
    clr_mod.AddReference = lambda *a, **kw: None
    sys.modules["clr"] = clr_mod

    sys_mod = types.ModuleType("System")
    col_mod = types.ModuleType("System.Collections")
    gen_mod = types.ModuleType("System.Collections.Generic")
    gen_mod.List = list
    win_mod = types.ModuleType("System.Windows")
    win_mod.Thickness = lambda *a: a
    win_mod.HorizontalAlignment = types.SimpleNamespace(Right=2)
    win_mod.VerticalAlignment = types.SimpleNamespace(Center=1)
    win_mod.Visibility = types.SimpleNamespace(Visible=0, Collapsed=2)
    ctrl_mod = types.ModuleType("System.Windows.Controls")
    ctrl_mod.CheckBox = type("CheckBox", (object,), {})
    wf_mod = types.ModuleType("System.Windows.Forms")
    wf_mod.FolderBrowserDialog = type("FolderBrowserDialog", (object,), {})
    wf_mod.SaveFileDialog = type("SaveFileDialog", (object,), {})
    wf_mod.DialogResult = types.SimpleNamespace(OK=1)

    sys.modules["System"] = sys_mod
    sys.modules["System.Collections"] = col_mod
    sys.modules["System.Collections.Generic"] = gen_mod
    sys.modules["System.Windows"] = win_mod
    sys.modules["System.Windows.Controls"] = ctrl_mod
    sys.modules["System.Windows.Forms"] = wf_mod

    db_mod = types.ModuleType("Autodesk.Revit.DB")
    db_mod.XYZ = types.SimpleNamespace(Zero=(0.0, 0.0, 0.0))
    db_mod.__all__ = ["XYZ"]
    arch_mod = types.ModuleType("Autodesk.Revit.DB.Architecture")
    arch_mod.Room = type("Room", (object,), {})

    sys.modules["Autodesk"] = types.ModuleType("Autodesk")
    sys.modules["Autodesk.Revit"] = types.ModuleType("Autodesk.Revit")
    sys.modules["Autodesk.Revit.DB"] = db_mod
    sys.modules["Autodesk.Revit.DB.Architecture"] = arch_mod

    forms_mod = types.ModuleType("pyrevit.forms")
    forms_mod.WPFWindow = type("WPFWindow", (object,), {})
    forms_mod.alert = lambda *a, **kw: None
    script_mod = types.ModuleType("pyrevit.script")
    pyrevit_mod = types.ModuleType("pyrevit")
    pyrevit_mod.forms = forms_mod
    pyrevit_mod.script = script_mod

    sys.modules["pyrevit"] = pyrevit_mod
    sys.modules["pyrevit.forms"] = forms_mod
    sys.modules["pyrevit.script"] = script_mod
    sys.modules["Snippets._ui_search"] = types.ModuleType("Snippets._ui_search")

    builtins.unicode = str
    builtins.basestring = str


class TestDocumentacaoNovos(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _install_stubs()
        import Snippets._doc_sheets as ds
        import Snippets._doc_prefeitura as dp

        cls.ds = ds
        cls.dp = dp

    def test_estrutura_painel_documentacao_e_sem_bom_ou_fstring(self):
        panel = os.path.join(REPO_ROOT, "AutoBIM.tab", "Documentação.panel")
        esperados = [
            os.path.join(panel, "bundle.yaml"),
            os.path.join(panel, "AmbientesVistas.pulldown", "bundle.yaml"),
            os.path.join(panel, "Quadros.pulldown", "bundle.yaml"),
            os.path.join(panel, "Pranchas.pulldown", "bundle.yaml"),
            os.path.join(panel, "Pranchas.pulldown", "AlinharViewports.pushbutton", "script.py"),
            os.path.join(panel, "Pranchas.pulldown", "CarimboRevisoes.pushbutton", "script.py"),
            os.path.join(panel, "Pranchas.pulldown", "ExportarPranchas.pushbutton", "script.py"),
            os.path.join(panel, "Quadros.pulldown", "QuadroAreasPrefeitura.pushbutton", "script.py"),
            os.path.join(LIB_DIR, "Snippets", "_doc_sheets.py"),
            os.path.join(LIB_DIR, "Snippets", "_doc_prefeitura.py"),
        ]
        for caminho in esperados:
            self.assertTrue(os.path.exists(caminho), "Arquivo ausente: " + caminho)
            with open(caminho, "rb") as f:
                raw = f.read()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), "BOM encontrado em: " + caminho)
            if caminho.endswith(".py"):
                tree = ast.parse(raw.decode("utf-8"), filename=caminho)
                for node in ast.walk(tree):
                    self.assertFalse(
                        isinstance(node, ast.JoinedStr),
                        "f-string proibida em IronPython 2.7: " + caminho,
                    )

    def test_alinhar_viewports_calculo_delta(self):
        # Modo 0: Origem do Modelo
        dx, dy = self.ds._calcular_delta_alinhamento(
            0, (1.0, 1.0), (2.0, 2.0), ref_proj=(0.5, 0.8), dst_proj=(0.7, 0.3)
        )
        self.assertAlmostEqual(dx, -0.2)
        self.assertAlmostEqual(dy, 0.5)

        # Modo 1: Centro
        dx, dy = self.ds._calcular_delta_alinhamento(1, (3.0, 4.0), (1.0, 1.5))
        self.assertAlmostEqual(dx, 2.0)
        self.assertAlmostEqual(dy, 2.5)

        # Modo 2: Canto Superior Esquerdo
        dx, dy = self.ds._calcular_delta_alinhamento(
            2, (0.0, 0.0), (0.0, 0.0), ref_tl=(0.2, 2.0), dst_tl=(0.5, 1.6)
        )
        self.assertAlmostEqual(dx, -0.3)
        self.assertAlmostEqual(dy, 0.4)

    def test_carimbo_folha_x_de_y(self):
        self.assertEqual(self.ds._formatar_folha_x_de_y(1, 8, 0), "01/08")
        self.assertEqual(self.ds._formatar_folha_x_de_y(1, 8, 1), "1 de 8")
        self.assertEqual(self.ds._formatar_folha_x_de_y(1, 8, 2), "01 de 08")

    def test_exportar_nomes_e_script_accoreconsole(self):
        nome = self.ds._formatar_nome_prancha("{Prefixo}_{Numero} - {Nome}_R{Rev}", "A01", "Planta: Térreo/1", "ARQ", "02")
        self.assertEqual(nome, "ARQ_A01 - Planta- Térreo-1_R02")

        scr = self.ds._gerar_script_accoreconsole(
            [
                {"path": r"C:\tmp\A01.dwg", "layout": "A01"},
                {"path": r"C:\tmp\A02.dwg", "layout": "A02"},
            ],
            r"C:\out\Unico.dwg",
            offset_x=400.0,
        )
        self.assertIn('_.-LAYOUT _R "" "A01"', scr)
        self.assertIn('_.-INSERT "*C:/tmp/A02.dwg" 400.00,0 1 0', scr)
        self.assertIn('_.-LAYOUT _T "C:/tmp/A02.dwg" *', scr)
        self.assertIn('_.-SAVEAS 2018 "C:/out/Unico.dwg"', scr)

    def test_quadro_areas_prefeitura_indices_e_csv(self):
        res = self.dp._calcular_indices_prefeitura(
            area_terreno_m2=250.0,
            areas_pavimentos=[("Térreo", 125.0), ("Superior", 75.0)],
            area_projecao_m2=None,
            area_permeavel_m2=50.0,
            to_max_pct=60.0,
            ca_max=1.0,
            tp_min_pct=15.0,
        )
        self.assertAlmostEqual(res["area_total_construida"], 200.0)
        self.assertAlmostEqual(res["area_projecao"], 125.0)
        self.assertAlmostEqual(res["to_pct"], 50.0)
        self.assertAlmostEqual(res["ca_val"], 0.8)
        self.assertAlmostEqual(res["tp_pct"], 20.0)
        self.assertAlmostEqual(res["ti_pct"], 80.0)
        self.assertIn("ATENDE", res["status_to"])
        self.assertIn("ATENDE", res["status_ca"])
        self.assertIn("ATENDE", res["status_tp"])

        csv_out = self.dp._gerar_csv_prefeitura(res)
        self.assertIn("Área do Terreno;250,00 m²", csv_out)
        self.assertIn("Taxa de Ocupação (TO);50,00%", csv_out)
        self.assertIn("Coeficiente de Aproveitamento (CA);0,80", csv_out)


if __name__ == "__main__":
    unittest.main()
