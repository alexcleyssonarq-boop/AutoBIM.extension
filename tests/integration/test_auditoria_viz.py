# -*- coding: utf-8 -*-
"""Testes de integracao para o painel Auditoria & Visualizacao (_audit_viz)."""

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

    sys.modules["System"] = types.ModuleType("System")
    sys.modules["System.Collections"] = types.ModuleType("System.Collections")
    gen_mod = types.ModuleType("System.Collections.Generic")
    gen_mod.List = list
    sys.modules["System.Collections.Generic"] = gen_mod

    win_mod = types.ModuleType("System.Windows")
    win_mod.Thickness = lambda *a: a
    win_mod.HorizontalAlignment = types.SimpleNamespace(Right=2)
    win_mod.VerticalAlignment = types.SimpleNamespace(Center=1)
    win_mod.CornerRadius = lambda *a: a
    sys.modules["System.Windows"] = win_mod

    ctrl_mod = types.ModuleType("System.Windows.Controls")
    ctrl_mod.Border = type("Border", (object,), {})
    ctrl_mod.StackPanel = type("StackPanel", (object,), {})
    ctrl_mod.TextBlock = type("TextBlock", (object,), {})
    ctrl_mod.Orientation = types.SimpleNamespace(Horizontal=0)
    ctrl_mod.CheckBox = type("CheckBox", (object,), {})
    sys.modules["System.Windows.Controls"] = ctrl_mod

    media_mod = types.ModuleType("System.Windows.Media")
    media_mod.Color = types.SimpleNamespace(FromRgb=lambda r, g, b: (r, g, b))
    media_mod.SolidColorBrush = lambda c: c
    sys.modules["System.Windows.Media"] = media_mod

    db_mod = types.ModuleType("Autodesk.Revit.DB")
    db_mod.XYZ = lambda x=0.0, y=0.0, z=0.0: types.SimpleNamespace(X=x, Y=y, Z=z)
    db_mod.Options = type("Options", (object,), {})
    db_mod.ViewDetailLevel = types.SimpleNamespace(Coarse=1)
    db_mod.Solid = type("Solid", (object,), {})
    db_mod.GeometryInstance = type("GeometryInstance", (object,), {})
    db_mod.__all__ = ["XYZ", "Options", "ViewDetailLevel", "Solid", "GeometryInstance"]

    sel_mod = types.ModuleType("Autodesk.Revit.UI.Selection")
    sel_mod.ObjectType = types.SimpleNamespace(Element=1)

    sys.modules["Autodesk"] = types.ModuleType("Autodesk")
    sys.modules["Autodesk.Revit"] = types.ModuleType("Autodesk.Revit")
    sys.modules["Autodesk.Revit.DB"] = db_mod
    sys.modules["Autodesk.Revit.UI"] = types.ModuleType("Autodesk.Revit.UI")
    sys.modules["Autodesk.Revit.UI.Selection"] = sel_mod

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

    builtins.unicode = str
    builtins.basestring = str


class TestAuditoriaVisualizacao(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _install_stubs()
        import Snippets._audit_viz as av

        cls.av = av

    def test_estrutura_painel_e_ironpython_compat(self):
        panel = os.path.join(REPO_ROOT, "AutoBIM.tab", "Auditoria e Visualização.panel")
        esperados = [
            os.path.join(panel, "bundle.yaml"),
            os.path.join(panel, "ColorirElementos.pushbutton", "script.py"),
            os.path.join(panel, "ColorirElementos.pushbutton", "icon.png"),
            os.path.join(panel, "ColorirElementos.pushbutton", "icon.dark.png"),
            os.path.join(panel, "SectionBox3D.pushbutton", "script.py"),
            os.path.join(panel, "ModoFantasma.pushbutton", "script.py"),
            os.path.join(panel, "DetectorConflitos.pushbutton", "script.py"),
            os.path.join(LIB_DIR, "Snippets", "_audit_viz.py"),
        ]
        for p in esperados:
            self.assertTrue(os.path.exists(p), "Arquivo ausente: " + p)
            if p.endswith(".py"):
                raw = open(p, "rb").read()
                self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), "BOM em: " + p)
                tree = ast.parse(raw.decode("utf-8"), filename=p)
                for node in ast.walk(tree):
                    self.assertFalse(isinstance(node, ast.JoinedStr), "f-string em: " + p)

    def test_paleta_e_bbox_helpers(self):
        self.assertEqual(len(self.av._paleta_cor(0)), 3)
        self.assertEqual(self.av._paleta_cor(0), self.av._paleta_cor(len(self.av.PALETA_RGB)))

        mn, mx = self.av._expandir_bbox_coords((0.0, 0.0, 0.0), (10.0, 5.0, 3.0), 1.0)
        self.assertEqual(mn, (-1.0, -1.0, -1.0))
        self.assertEqual(mx, (11.0, 6.0, 4.0))

        # Apenas encostadas na face X=10 -> nao colide
        bb1 = ((0.0, 0.0, 0.0), (10.0, 5.0, 3.0))
        bb_touch = ((10.0, 0.0, 0.0), (15.0, 5.0, 3.0))
        self.assertFalse(self.av._bboxes_colidem(bb1, bb_touch, tol_ft=0.05))

        # Sobreposicao real de 1 pe -> colide
        bb_hit = ((9.0, 1.0, 0.5), (12.0, 4.0, 2.5))
        self.assertTrue(self.av._bboxes_colidem(bb1, bb_hit, tol_ft=0.05))
        inter = self.av._calcular_intersecao_bbox(bb1, bb_hit)
        self.assertEqual(inter, ((9.0, 1.0, 0.5), (10.0, 4.0, 2.5)))

    def test_checar_pares_conflito_ignora_host_e_detecta_real(self):
        def _fake_el(eid, cat, bmin, bmax, host=None):
            bb = types.SimpleNamespace(
                Min=types.SimpleNamespace(X=bmin[0], Y=bmin[1], Z=bmin[2]),
                Max=types.SimpleNamespace(X=bmax[0], Y=bmax[1], Z=bmax[2]),
            )
            return types.SimpleNamespace(
                Id=types.SimpleNamespace(IntegerValue=eid, Value=eid),
                Category=types.SimpleNamespace(Name=cat),
                Host=host,
                get_BoundingBox=lambda v: bb,
                get_Geometry=lambda o: None,
            )

        parede = _fake_el(1, "Paredes", (0, 0, 0), (10, 0.5, 9))
        porta_hosp = _fake_el(2, "Portas", (2, 0, 0), (5, 0.5, 7), host=parede)
        pilar_clash = _fake_el(3, "Pilares", (3, -0.5, 0), (4, 1.0, 9))

        # Porta hospedada na parede nao deve gerar conflito com a propria parede
        self.assertEqual(self.av._checar_pares_conflito([porta_hosp], [parede], 0.05), [])
        # Porta colidindo com pilar deve gerar exatamente 1 conflito
        achados = self.av._checar_pares_conflito([porta_hosp], [pilar_clash], 0.05)
        self.assertEqual(len(achados), 1)
        self.assertIn("Portas [2]", achados[0]["label"])
        self.assertIn("Pilares [3]", achados[0]["label"])


if __name__ == "__main__":
    unittest.main()
