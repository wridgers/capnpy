import py
import sys
import types
from collections import defaultdict
import subprocess
import keyword
from pypytools.codegen import Code
from capnpy.convert_case import from_camel_case
from capnpy import schema
from capnpy.message import loads
from capnpy.blob import PYX

# the following imports have side-effects, and augment the schema.* classes
# with emit() methods
import capnpy.compiler.request
import capnpy.compiler.node
import capnpy.compiler.struct_
import capnpy.compiler.field
import capnpy.compiler.misc


## # pycapnp will be supported only until the boostrap is completed
## USE_PYCAPNP = False

## if USE_PYCAPNP:
##     import capnp
##     import schema_capnp
##     def loads(buf, payload_type):
##         return payload_type.from_bytes(buf)
## else:


class ModuleGenerator(object):

    def __init__(self, request, convert_case, pyx):
        self.code = Code(pyx=pyx)
        self.request = request
        self.convert_case = convert_case
        self.pyx = pyx
        self.allnodes = {} # id -> node
        self.children = defaultdict(list) # nodeId -> nested nodes
        self.importnames = {} # filename -> import name
 
    def w(self, *args, **kwargs):
        self.code.w(*args, **kwargs)

    def block(self, *args, **kwargs):
        return self.code.block(*args, **kwargs)

    def register_import(self, fname):
        name = py.path.local(fname).purebasename
        name = name.replace('+', 'PLUS')
        name = '_%s_capnp' % name
        if name in self.importnames.values():
            # avoid name clashes
            name = '%s_%s' % (name, len(self.filenames))
        self.importnames[fname] = name
        return name

    def generate(self):
        self.request.emit(self)
        return self.code.build()

    def _dump_node(self, node):
        def visit(node, deep=0):
            print '%s%s: %s' % (' ' * deep, node.which(), node.displayName)
            for child in self.children[node.id]:
                visit(child, deep+2)
        visit(node)

    def _convert_name(self, name):
        if self.convert_case:
            return from_camel_case(name)
        else:
            return name

    def _field_name(self, field):
        name = self._convert_name(field.name)
        name = self._mangle_name(name)
        return name

    def _mangle_name(self, name):
        if name in keyword.kwlist:
            return name + '_'
        return name

    def declare_enum(self, var_name, enum_name, items):
        # this method cannot go on Node__Enum because it's also called by
        # Node__Struct (for __tag__)
        items = map(repr, items)
        decl = "%s = _enum(%r, [%s])" % (var_name, enum_name, ', '.join(items))
        self.w(decl)

    def def_property(self, ns, name, src):
        if self.pyx:
            with ns.block('property {name}:', name=name):
                with ns.block('def __get__(self):'):
                    ns.ww(src)
        else:
            ns.w('@property')
            with ns.block('def {name}(self):', name=name):
                ns.ww(src)
        ns.w()

    def robust_arglist(self, argnames):
        # in pyx mode, we cannot use e.g. 'void' as argname: we need to use
        # 'object void', else cython complains.
        if self.pyx:
            def addtype(name):
                if name.startswith('*'):
                    return name
                return 'object %s' % name
            argnames = [addtype(name) for name in argnames]
        return argnames

class Compiler(object):

    def __init__(self, path, pyx):
        self.path = [py.path.local(dirname) for dirname in path]
        self.modules = {}
        #
        assert pyx in (True, False, 'auto')
        if pyx == 'auto':
            pyx = PYX
        self.pyx = pyx
        if self.pyx:
            assert PYX, 'Cython extensions are missing; please run setup.py install'
            self.tmpdir = py.path.local.mkdtemp()
        else:
            self.tmpdir = None

    def load_schema(self, modname=None, importname=None, filename=None, convert_case=True):
        """
        Compile and load a capnp schema, which can be specified by setting one
        (and only one) of the following params:

          - *modname*: in the form 'a.b.c', it will search the file
             a/b/c.capnp in the directories of the path. This is useful if you
             want to distribute the schema file together with your python
             package

          - *importname*: similar to *modname*, but using the same syntax as
             the ``import`` expression in capnp schemas; in the example above,
             it becomes "/a/b/c.capnp". The starting slash indicates that it
             is an non-relative import, i.e. that it will be looked in all the
             directories listed in path

          - *filename*: the (relative or absolute) file containing the schema;
             no search if performed
        """
        filename = self._get_filename(modname, importname, filename)
        try:
            return self.modules[filename]
        except KeyError:
            mod = self._compile_file(filename, convert_case)
            self.modules[filename] = mod
            return mod

    def _compile_file(self, filename, convert_case):
        m, src = self.generate_py_source(filename, convert_case)
        if self.pyx:
            return self._compile_pyx(filename, m, src)
        else:
            return self._compile_py(filename, m, src)

    def generate_py_source(self, filename, convert_case):
        data = self._capnp_compile(filename)
        request = loads(data, schema.CodeGeneratorRequest)
        m = ModuleGenerator(request, convert_case, self.pyx)
        src = m.generate()
        return m, py.code.Source(src)

    def _compile_py(self, filename, m, src):
        """
        Compile and load the schema as pure python
        """
        mod = types.ModuleType(m.modname)
        mod.__file__ = str(filename)
        mod.__schema__ = str(filename)
        mod.__source__ = str(src)
        mod.__dict__['__compiler'] = self
        exec src.compile() in mod.__dict__
        return mod

    def _compile_pyx(self, filename, m, src):
        """
        Use Cython to compile the schema
        """
        import capnpy.ext # the package which we will load the .so in
        import imp
        from pyximport.pyxbuild import pyx_to_dll
        pyxname = filename.new(ext='pyx')
        pyxfile = self.tmpdir.join(pyxname).ensure(file=True)
        pyxfile.write(src)
        dll = pyx_to_dll(str(pyxfile), pyxbuild_dir=str(self.tmpdir))
        #
        # the generated file needs a reference to __compiler to be able to
        # import other schemas. In pure-python mode, we simply inject
        # __compiler in the __dict__ before compiling the source; but in pyx
        # mode we cannot, hence we need a way to "pass" an argument from the
        # outside. I think the only way is to temporarily stick it in some
        # global state, for example sys.modules. Then, as we don't want to
        # clutter any global state, we cleanup sys.modules.
        #
        # So, when compiling foo.capnp, we create a dummy foo_tmp module which
        # contains __compiler. Then, in foo.pyx, we import it:
        #     from foo_tmp import __compiler
        #
        tmpmod = types.ModuleType(m.tmpname)
        tmpmod.__dict__['__compiler'] = self
        tmpmod.__dict__['__schema__'] = str(filename)
        sys.modules[m.tmpname] = tmpmod
        modname = 'capnpy.ext.%s' % m.modname
        mod = imp.load_dynamic(modname, str(dll))
        #
        # clean-up the cluttered sys.modules
        del sys.modules[mod.__name__]
        del sys.modules[tmpmod.__name__]
        return mod

    def _get_filename(self, modname, importname, filename):
        n = (modname, importname, filename).count(None)
        if n != 2:
            raise ValueError("You have to specify exactly 1 of modname, importname or filename")
        #
        if modname is not None:
            importname = '%s.capnp' % modname.replace('.', '/')
            return self._find_file(importname)
        elif importname is not None:
            if not importname.startswith('/'):
                raise ValueError("schema paths must be absolute: %s" % importname)
            return self._find_file(importname)
        else:
            return py.path.local(filename)

    def _find_file(self, importname):
        for dirpath in self.path:
            f = dirpath.join(importname)
            if f.check(file=True):
                return f
        raise ValueError("Cannot find %s in the given path" % importname)

    def _capnp_compile(self, filename):
        # this is a hack: we use cat as a plugin of capnp compile to get the
        # CodeGeneratorRequest bytes. There MUST be a more proper way to do that
        cmd = ['capnp', 'compile', '-o', '/bin/cat']
        for dirname in self.path:
            cmd.append('-I%s' % dirname)
        cmd.append(str(filename))
        #print ' '.join(cmd)
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = proc.communicate()
        ret = proc.wait()
        if ret != 0:
            raise ValueError(stderr)
        return stdout

_compiler = Compiler(sys.path, pyx='auto')
load_schema = _compiler.load_schema
