import py
import pytest
from capnpy.schema import Field, Type
from capnpy.compiler.structor import Structor
from capnpy.testing.compiler.support import CompilerTest

class TestComputeFormat(object):

    @pytest.fixture
    def m(self):
        class FakeModuleGenerator:
            def _field_name(self, f):
                return f.name
        return FakeModuleGenerator()

    def test_compute_format_simple(self, m):
        fields = [Field.new_slot('x', 0, Type.new_int64()),
                  Field.new_slot('y', 1, Type.new_int64())]
        s = Structor(m, 'fake', data_size=2, ptrs_size=0, fields=fields)
        assert s.fmt == 'qq'

    def test_compute_format_holes(self, m):
        fields = [Field.new_slot('x', 0, Type.new_int32()),
                  Field.new_slot('y', 1, Type.new_int64())]
        s = Structor(m, 'fake', data_size=2, ptrs_size=0, fields=fields)
        assert s.fmt == 'ixxxxq'


class TestConstructors(CompilerTest):

    def test_primitive(self):
        schema = """
        @0xbf5147cbbecf40c1;
        struct Point {
            x @0 :Int64;
            y @1 :Int64;
        }
        """
        mod = self.compile(schema)
        buf = ('\x01\x00\x00\x00\x00\x00\x00\x00'  # 1
               '\x02\x00\x00\x00\x00\x00\x00\x00') # 2
        #
        p = mod.Point(1, 2)
        assert p.x == 1
        assert p.y == 2
        assert p._buf.s == buf
        #
        p = mod.Point(y=2, x=1)
        assert p.x == 1
        assert p.y == 2
        assert p._buf.s == buf

    def test_void(self):
        schema = """
        @0xbf5147cbbecf40c1;
        struct Point {
            x @0 :Int64;
            y @1 :Int64;
            z @2 :Void;
        }
        """
        mod = self.compile(schema)
        buf = ('\x01\x00\x00\x00\x00\x00\x00\x00'  # 1
               '\x02\x00\x00\x00\x00\x00\x00\x00') # 2
        #
        p = mod.Point(1, 2)
        assert p.x == 1
        assert p.y == 2
        assert p._buf.s == buf

    def test_string(self):
        schema = """
        @0xbf5147cbbecf40c1;
        struct Foo {
            x @0 :Int64;
            y @1 :Text;
        }
        """
        mod = self.compile(schema)
        foo = mod.Foo(1, 'hello capnp')
        assert foo._buf.s == ('\x01\x00\x00\x00\x00\x00\x00\x00'
                              '\x01\x00\x00\x00\x62\x00\x00\x00'
                              'h' 'e' 'l' 'l' 'o' ' ' 'c' 'a'
                              'p' 'n' 'p' '\x00\x00\x00\x00\x00')

    def test_struct(self):
        schema = """
        @0xbf5147cbbecf40c1;
        struct Point {
            x @0 :Int64;
            y @1 :Int64;
        }
        struct Foo {
            x @0 :Point;
        }
        """
        mod = self.compile(schema)
        p = mod.Point(1, 2)
        foo = mod.Foo(p)
        assert foo._buf.s == ('\x00\x00\x00\x00\x02\x00\x00\x00'  # ptr to point
                              '\x01\x00\x00\x00\x00\x00\x00\x00'  # p.x == 1
                              '\x02\x00\x00\x00\x00\x00\x00\x00') # p.y == 2


    def test_list(self):
        schema = """
        @0xbf5147cbbecf40c1;
        struct Foo {
            x @0 :List(Int8);
        }
        """
        mod = self.compile(schema)
        foo = mod.Foo([1, 2, 3, 4])
        assert foo._buf.s == ('\x01\x00\x00\x00\x22\x00\x00\x00'   # ptrlist
                              '\x01\x02\x03\x04\x00\x00\x00\x00')  # 1,2,3,4 + padding



    def test_list_of_structs(self):
        schema = """
        @0xbf5147cbbecf40c1;
        struct Polygon {
            struct Point {
                x @0 :Int64;
                y @1 :Int64;
            }
            points @0 :List(Point);
        }
        """
        mod = self.compile(schema)
        p1 = mod.Polygon.Point(1, 2)
        p2 = mod.Polygon.Point(3, 4)
        poly = mod.Polygon([p1, p2])
        assert poly.points[0].x == 1
        assert poly.points[0].y == 2
        assert poly.points[1].x == 3
        assert poly.points[1].y == 4


class TestUnionConstructors(CompilerTest):

    @py.test.fixture
    def mod(self):
        schema = """
        @0xbf5147cbbecf40c1;
        struct Shape {
          area @0 :Int64;
          perimeter @1 :Int64;
          union {
            circle @2 :Int64;      # radius
            square @3 :Int64;      # width
          }
        }
        """
        return self.compile(schema)

    def test_specific_ctors(self, mod):
        s = mod.Shape.new_circle(area=1, circle=2, perimeter=3)
        assert s.which() == mod.Shape.__tag__.circle
        assert s.area == 1
        assert s.circle == 2
        assert s.perimeter == 3
        buf = ('\x01\x00\x00\x00\x00\x00\x00\x00'   # area == 1
               '\x03\x00\x00\x00\x00\x00\x00\x00'   # perimeter == 3
               '\x02\x00\x00\x00\x00\x00\x00\x00'   # circle == 2
               '\x00\x00\x00\x00\x00\x00\x00\x00')  # __tag__ == 0 (circle)
        assert s._buf.s == buf
        #
        s = mod.Shape.new_square(area=1, square=2, perimeter=3)
        assert s.which() == mod.Shape.__tag__.square
        assert s.area == 1
        assert s.square == 2
        assert s.perimeter == 3
        buf = ('\x01\x00\x00\x00\x00\x00\x00\x00'   # area == 1
               '\x03\x00\x00\x00\x00\x00\x00\x00'   # perimeter == 3
               '\x02\x00\x00\x00\x00\x00\x00\x00'   # squadre == 2
               '\x01\x00\x00\x00\x00\x00\x00\x00')  # __tag__ == 1 (square)
        assert s._buf.s == buf

    def test_generic_ctor(self, mod):
        # test the __init__
        s = mod.Shape(area=1, square=2, perimeter=3)
        assert s.which() == mod.Shape.__tag__.square
        assert s.area == 1
        assert s.square == 2
        assert s.perimeter == 3

    def test_multiple_tags(self, mod):
        einfo = py.test.raises(TypeError,
                              "mod.Shape(area=0, perimeter=0, circle=1, square=2)")
        assert str(einfo.value) == 'got multiple values for the union tag: square, circle'

    def test_no_tags(self, mod):
        einfo = py.test.raises(TypeError, "mod.Shape(area=0, perimeter=0)")
        assert str(einfo.value) == "one of the following args is required: circle, square"