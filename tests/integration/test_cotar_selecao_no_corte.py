# -*- coding: utf-8 -*-
"""Teste de integracao para Cotar Selecao no Corte (Snippets._section_dims).

Pode ser executado:
1. Offline (via unittest / pytest / CI), usando mocks da API do Revit para validar
   o fluxo completo de `run_cotar_selecao_corte(abrir_config=False)` e os helpers
   de agrupamento/deduplicacao de referencias.
2. Em Revit ao vivo (via `pyrevit run`), usando `run_live_pyrevit_check(doc, uidoc)`
   para executar o comando e verificar via `FilteredElementCollector` se ao menos
   uma `Dimension` foi criada na vista ativa.
"""

import builtins
import os
import sys
import types
import unittest


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
LIB_DIR = os.path.join(REPO_ROOT, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)


def _install_revit_mocks():
    """Instala stubs minimos de clr, Autodesk.Revit.* e pyrevit para testes offline."""

    class _XYZ(object):
        def __init__(self, x=0.0, y=0.0, z=0.0):
            self.X = float(x)
            self.Y = float(y)
            self.Z = float(z)

        def __add__(self, other):
            return _XYZ(self.X + other.X, self.Y + other.Y, self.Z + other.Z)

        def __sub__(self, other):
            return _XYZ(self.X - other.X, self.Y - other.Y, self.Z - other.Z)

        def __mul__(self, scalar):
            return _XYZ(self.X * scalar, self.Y * scalar, self.Z * scalar)

        __rmul__ = __mul__

        def DotProduct(self, other):
            return self.X * other.X + self.Y * other.Y + self.Z * other.Z

        def CrossProduct(self, other):
            return _XYZ(
                self.Y * other.Z - self.Z * other.Y,
                self.Z * other.X - self.X * other.Z,
                self.X * other.Y - self.Y * other.X,
            )

        def GetLength(self):
            return (self.X ** 2 + self.Y ** 2 + self.Z ** 2) ** 0.5

        def DistanceTo(self, other):
            return (self - other).GetLength()

        def Normalize(self):
            ln = self.GetLength()
            if ln < 1e-12:
                return _XYZ(0.0, 0.0, 0.0)
            return _XYZ(self.X / ln, self.Y / ln, self.Z / ln)

    _XYZ.Zero = _XYZ(0.0, 0.0, 0.0)
    _XYZ.BasisX = _XYZ(1.0, 0.0, 0.0)
    _XYZ.BasisY = _XYZ(0.0, 1.0, 0.0)
    _XYZ.BasisZ = _XYZ(0.0, 0.0, 1.0)

    class _Line(object):
        def __init__(self, p0, p1):
            self._p0 = p0
            self._p1 = p1
            self.Origin = p0
            self.Direction = (p1 - p0).Normalize()
            self.Length = p0.DistanceTo(p1)

        @classmethod
        def CreateBound(cls, p0, p1):
            return cls(p0, p1)

        def GetEndPoint(self, idx):
            return self._p0 if idx == 0 else self._p1

    class _ElementId(object):
        def __init__(self, val):
            self.IntegerValue = int(val)
            self.Value = int(val)

    class _Reference(object):
        def __init__(self, name):
            self.name = name

    class _ReferenceArray(object):
        def __init__(self):
            self._items = []

        def Append(self, ref):
            self._items.append(ref)

        @property
        def Size(self):
            return len(self._items)

    class _PlanarFace(object):
        def __init__(self, origin, normal, ref_name):
            self.Origin = origin
            self.FaceNormal = normal
            self.Reference = _Reference(ref_name)

        def GetBoundingBox(self):
            class _UVBox(object):
                Min = types.SimpleNamespace(U=0.0, V=0.0)
                Max = types.SimpleNamespace(U=1.0, V=1.0)

            return _UVBox()

        def Evaluate(self, uv):
            return self.Origin

    class _Solid(object):
        def __init__(self, faces):
            self.Faces = faces
            self.Volume = 1.0

    class _BoundingBoxXYZ(object):
        def __init__(self, pmin, pmax):
            self.Min = pmin
            self.Max = pmax

    class _Category(object):
        def __init__(self, cat_id):
            self.Id = _ElementId(cat_id)

    class _Element(object):
        def __init__(self, eid, cat_id, faces, bbox):
            self.Id = _ElementId(eid)
            self.Category = _Category(cat_id)
            self._faces = faces
            self._bbox = bbox

        def get_BoundingBox(self, view):
            return self._bbox

        def get_Geometry(self, opts):
            return [_Solid(self._faces)]

    class _Wall(_Element):
        def __init__(self, eid, faces, bbox, curve=None):
            super(_Wall, self).__init__(eid, -2000011, faces, bbox)
            self.Location = types.SimpleNamespace(Curve=curve) if curve else None

    class _Floor(_Element):
        def __init__(self, eid, faces, bbox):
            super(_Floor, self).__init__(eid, -2000032, faces, bbox)

    class _Ceiling(_Element):
        def __init__(self, eid, faces, bbox):
            super(_Ceiling, self).__init__(eid, -2000038, faces, bbox)

    class _RoofBase(_Element):
        pass

    class _FamilyInstance(_Element):
        pass

    class _ViewSection(object):
        def __init__(self):
            self.Id = _ElementId(100)
            self.Origin = _XYZ(0.0, 0.0, 0.0)
            self.RightDirection = _XYZ.BasisX
            self.UpDirection = _XYZ.BasisZ
            self.ViewDirection = _XYZ.BasisY
            self.Scale = 50
            self.CropBoxActive = False
            self.CropBox = None

    class _Dimension(object):
        pass

    class _DimensionType(object):
        def __init__(self, eid=900, style=0, name="Linear 2.5mm"):
            self.Id = _ElementId(eid)
            self.StyleType = style
            self.Name = name

    class _Transaction(object):
        committed = 0

        def __init__(self, doc, name=""):
            self.doc = doc
            self.name = name

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            return False

        def Start(self):
            return True

        def Commit(self):
            _Transaction.committed += 1
            return True

    class _SubTransaction(object):
        def __init__(self, doc):
            self.doc = doc

        def Start(self):
            return True

        def Commit(self):
            return True

        def RollBack(self):
            return True

    class _FilteredElementCollector(object):
        def __init__(self, doc, view_id=None):
            self._doc = doc
            self._view_id = view_id
            self._cls = None
            self._cat = None
            self._only_instances = False

        def OfClass(self, cls):
            self._cls = cls
            return self

        def OfCategory(self, cat):
            self._cat = int(cat)
            return self

        def WhereElementIsElementType(self):
            return self

        def WhereElementIsNotElementType(self):
            self._only_instances = True
            return self

        def ToElements(self):
            if self._cls is _DimensionType:
                return list(self._doc._dim_types)
            if self._cls is _Dimension:
                return list(self._doc._dims)
            els = list(self._doc._elements.values())
            if self._cat is not None:
                els = [e for e in els if e.Category.Id.IntegerValue == self._cat]
            return els

    class _BuiltInCategory(object):
        OST_Walls = -2000011
        OST_Floors = -2000032
        OST_Ceilings = -2000038
        OST_Roofs = -2000035
        OST_StructuralFraming = -2001320
        OST_StructuralColumns = -2001330
        OST_Columns = -2000100
        OST_StructuralFoundation = -2001300
        OST_Stairs = -2000120
        OST_Ramps = -2000180
        OST_Doors = -2000023
        OST_Windows = -2000014
        OST_CurtainWallMullions = -2000171
        OST_CurtainWallPanels = -2000170
        OST_GenericModel = -2000151
        OST_Parts = -2000269

    class _BuiltInParameter(object):
        ALL_MODEL_INSTANCE_COMMENTS = 1
        SYMBOL_NAME_PARAM = 2

    class _Options(object):
        def __init__(self):
            self.ComputeReferences = False
            self.View = None
            self.DetailLevel = None

    class _GeometryInstance(object):
        pass

    class _GeometryElement(object):
        pass

    class _UV(object):
        def __init__(self, u=0.0, v=0.0):
            self.U = u
            self.V = v

    clr_mod = types.ModuleType("clr")
    clr_mod.AddReference = lambda *a, **kw: None

    db_mod = types.ModuleType("Autodesk.Revit.DB")
    db_mod.XYZ = _XYZ
    db_mod.Line = _Line
    db_mod.UV = _UV
    db_mod.ElementId = _ElementId
    db_mod.Reference = _Reference
    db_mod.ReferenceArray = _ReferenceArray
    db_mod.PlanarFace = _PlanarFace
    db_mod.Solid = _Solid
    db_mod.BoundingBoxXYZ = _BoundingBoxXYZ
    db_mod.Wall = _Wall
    db_mod.Floor = _Floor
    db_mod.Ceiling = _Ceiling
    db_mod.RoofBase = _RoofBase
    db_mod.FamilyInstance = _FamilyInstance
    db_mod.ViewSection = _ViewSection
    db_mod.Dimension = _Dimension
    db_mod.DimensionType = _DimensionType
    db_mod.DimensionStyleType = types.SimpleNamespace(Linear=0)
    db_mod.BuiltInCategory = _BuiltInCategory
    db_mod.BuiltInParameter = _BuiltInParameter
    db_mod.Transaction = _Transaction
    db_mod.SubTransaction = _SubTransaction
    db_mod.FilteredElementCollector = _FilteredElementCollector
    db_mod.Options = _Options
    db_mod.ViewDetailLevel = types.SimpleNamespace(Fine=3)
    db_mod.GeometryInstance = _GeometryInstance
    db_mod.GeometryElement = _GeometryElement
    db_mod.__all__ = [k for k in dir(db_mod) if not k.startswith("_")]

    arch_mod = types.ModuleType("Autodesk.Revit.DB.Architecture")
    arch_mod.Room = type("Room", (), {})
    arch_mod.RoomTag = type("RoomTag", (), {})

    sel_mod = types.ModuleType("Autodesk.Revit.UI.Selection")
    sel_mod.ObjectType = types.SimpleNamespace(Element=1)
    sel_mod.ObjectSnapTypes = types.SimpleNamespace(**{"None": 0})
    sel_mod.ISelectionFilter = object

    autodesk_mod = types.ModuleType("Autodesk")
    revit_mod = types.ModuleType("Autodesk.Revit")
    ui_mod = types.ModuleType("Autodesk.Revit.UI")

    alerts = []

    class _AlertExit(SystemExit):
        pass

    forms_mod = types.ModuleType("pyrevit.forms")
    forms_mod.WPFWindow = type("WPFWindow", (object,), {})
    forms_mod.alert = lambda msg, title=None, exitscript=False: alerts.append((title, msg))
    forms_mod.WarningBar = lambda *a, **kw: types.SimpleNamespace(
        __enter__=lambda s: s, __exit__=lambda s, *x: False
    )

    script_mod = types.ModuleType("pyrevit.script")
    script_mod.exit = lambda: (_ for _ in ()).throw(_AlertExit(0))

    pyrevit_mod = types.ModuleType("pyrevit")
    pyrevit_mod.forms = forms_mod
    pyrevit_mod.script = script_mod

    doc_extra_mod = types.ModuleType("Snippets._doc_extra")
    doc_extra_mod._eh_vidraca_inclinada = lambda el: False
    doc_extra_mod._elem_name = lambda el: getattr(el, "Name", "")
    doc_extra_mod._natural_key = lambda s: (str(s or "").lower(),)
    doc_extra_mod._iter_faces_solidas = lambda geom: [fc for s in (geom or []) for fc in getattr(s, "Faces", [])]
    ui_search_mod = types.ModuleType("Snippets._ui_search")

    sys.modules["clr"] = clr_mod
    sys.modules["Autodesk"] = autodesk_mod
    sys.modules["Autodesk.Revit"] = revit_mod
    sys.modules["Autodesk.Revit.DB"] = db_mod
    sys.modules["Autodesk.Revit.DB.Architecture"] = arch_mod
    sys.modules["Autodesk.Revit.UI"] = ui_mod
    sys.modules["Autodesk.Revit.UI.Selection"] = sel_mod
    sys.modules["pyrevit"] = pyrevit_mod
    sys.modules["pyrevit.forms"] = forms_mod
    sys.modules["pyrevit.script"] = script_mod
    sys.modules["Snippets._doc_extra"] = doc_extra_mod
    sys.modules["Snippets._ui_search"] = ui_search_mod

    return {
        "XYZ": _XYZ,
        "Floor": _Floor,
        "Ceiling": _Ceiling,
        "Wall": _Wall,
        "PlanarFace": _PlanarFace,
        "BoundingBoxXYZ": _BoundingBoxXYZ,
        "ViewSection": _ViewSection,
        "DimensionType": _DimensionType,
        "Transaction": _Transaction,
        "alerts": alerts,
    }


class TestCotarSelecaoNoCorte(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mocks = _install_revit_mocks()
        builtins.unicode = str
        import Snippets._section_dims as sd

        cls.sd = sd

    def test_helpers_dedup_e_slot_externo(self):
        refs = [
            (0.0, "r0", "FlBot"),
            (0.002, "r0_dup", "FlBotDup"),
            (0.5, "r1", "FlTop"),
            (9.8, "r2", "CeilBot"),
        ]
        dedup = self.sd._dedup_refs_por_coord(refs, min_gap=0.05)
        self.assertEqual([r[1] for r in dedup], ["r0", "r1", "r2"])

        slot = self.sd._proximo_slot_externo(10.0, 1.0, [10.0, 11.0], sentido=1)
        self.assertAlmostEqual(slot, 12.0)

    def test_run_cotar_selecao_corte_cria_cadeia_e_total(self):
        m = self.mocks
        XYZ = m["XYZ"]
        PlanarFace = m["PlanarFace"]
        BoundingBoxXYZ = m["BoundingBoxXYZ"]

        # Piso (0.0 -> 0.5 ft), Forro (9.0 -> 9.2 ft) -> 4 refs verticais distintas
        piso = m["Floor"](
            101,
            [
                PlanarFace(XYZ(5.0, 0.0, 0.0), XYZ(0.0, 0.0, -1.0), "piso_bot"),
                PlanarFace(XYZ(5.0, 0.0, 0.5), XYZ(0.0, 0.0, 1.0), "piso_top"),
            ],
            BoundingBoxXYZ(XYZ(0.0, -1.0, 0.0), XYZ(10.0, 1.0, 0.5)),
        )
        forro = m["Ceiling"](
            102,
            [
                PlanarFace(XYZ(5.0, 0.0, 9.0), XYZ(0.0, 0.0, -1.0), "forro_bot"),
                PlanarFace(XYZ(5.0, 0.0, 9.2), XYZ(0.0, 0.0, 1.0), "forro_top"),
            ],
            BoundingBoxXYZ(XYZ(0.0, -1.0, 9.0), XYZ(10.0, 1.0, 9.2)),
        )

        created_dims = []

        class _DocCreate(object):
            def NewDimension(self, view, line, ref_array, dim_type=None):
                dim_obj = types.SimpleNamespace(
                    view=view,
                    line=line,
                    refs=[r.name for r in ref_array._items],
                    dim_type=dim_type,
                )
                created_dims.append(dim_obj)
                return dim_obj

        class _Doc(object):
            def __init__(self):
                self.ActiveView = m["ViewSection"]()
                self.Create = _DocCreate()
                self._elements = {101: piso, 102: forro}
                self._dim_types = [m["DimensionType"](900, 0, "Linear")]
                self._dims = []

            def GetElement(self, eid):
                return self._elements.get(eid.IntegerValue)

        doc = _Doc()
        uidoc = types.SimpleNamespace(
            Document=doc,
            Selection=types.SimpleNamespace(GetElementIds=lambda: [piso.Id, forro.Id]),
        )
        builtins.__revit__ = types.SimpleNamespace(ActiveUIDocument=uidoc)

        m["Transaction"].committed = 0
        m["alerts"][:] = []

        self.sd.run_cotar_selecao_corte(abrir_config=False)

        self.assertEqual(m["Transaction"].committed, 1)
        # Cadeia com 4 refs + cota total com 2 refs
        self.assertEqual(len(created_dims), 2)
        self.assertEqual(
            created_dims[0].refs,
            ["piso_bot", "piso_top", "forro_bot", "forro_top"],
        )
        self.assertEqual(created_dims[1].refs, ["piso_bot", "forro_top"])
        self.assertTrue(any("Linhas de cota verticais criadas: 2" in msg for _, msg in m["alerts"]))


def run_live_pyrevit_check(doc, uidoc):
    """Helper para execucao ao vivo dentro do Revit (`pyrevit run`)."""
    from Autodesk.Revit.DB import Dimension, FilteredElementCollector
    import Snippets._section_dims as sd

    view = doc.ActiveView
    before = len(list(FilteredElementCollector(doc, view.Id).OfClass(Dimension).ToElements()))
    sd.run_cotar_selecao_corte(abrir_config=False)
    after = len(list(FilteredElementCollector(doc, view.Id).OfClass(Dimension).ToElements()))
    assert after > before, "Nenhuma cota criada pelo Cotar Selecao no Corte."
    return after - before


if __name__ == "__main__":
    unittest.main()
