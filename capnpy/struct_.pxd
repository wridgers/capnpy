from capnpy.blob cimport Blob
from capnpy.ptr cimport Ptr

cpdef assert_undefined(object val, str name, str other_name)

cdef class Struct(Blob):
    cdef public long _data_offset
    cdef public long _ptrs_offset
    cdef public long _data_size
    cdef public long _ptrs_size

    cpdef _init_from_buffer(self, object buf, long offset,
                            long data_size, long ptrs_size)
    cpdef _read_data(self, long offset, char ifmt)
    cpdef _read_ptr(self, long offset)
