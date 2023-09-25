import types
import typing
from collections import deque
from collections.abc import MutableMapping
from collections.abc import Sequence
from functools import wraps
from inspect import Signature
from itertools import chain
from itertools import repeat

import numpy as np
import typing_extensions as tp

# from static_frame.core.container import ContainerBase
# from static_frame.core.frame import Frame
from static_frame.core.index import Index
# from static_frame.core.index_hierarchy import IndexHierarchy
from static_frame.core.series import Series
from static_frame.core.util import TLabel

# _UnionGenericAlias comes from tp.Union, UnionType from | expressions
# tp.Optional returns a _UnionGenericAlias with later Python, but a _GenericAlias with 3.8

def _iter_generic_classes() -> tp.Iterable[tp.Type[tp.Any]]:
    if t := getattr(types, 'GenericAlias', None):
        yield t
    if t := getattr(typing, '_GenericAlias', None):
        yield t # pyright: ignore

GENERIC_TYPES = tuple(_iter_generic_classes())

def iter_union_classes() -> tp.Iterable[tp.Type[tp.Any]]:
    if t := getattr(types, 'UnionType', None):
        yield t
    if t := getattr(typing, '_UnionGenericAlias', None):
        yield t # pyright: ignore

UNION_TYPES = tuple(iter_union_classes())

def is_union(hint: tp.Any) -> bool:
    if UNION_TYPES:
        return isinstance(hint, UNION_TYPES)
    elif isinstance(hint, GENERIC_TYPES):
        return tp.get_origin(hint) is tp.Union
    return False

TParent = tp.Tuple[tp.Any, ...]
TValidation = tp.Tuple[tp.Any, tp.Any, TParent]

#-------------------------------------------------------------------------------

class Constraint:
    __slots__: tp.Tuple[str, ...] = ()

    def get_log(self,
            value: tp.Any,
            hint: tp.Any,
            parent: TParent,
            ) -> tp.Iterator[TValidation]:
        raise NotImplementedError()

    def __repr__(self) -> str:
        args = ', '.join(repr(getattr(self, v)) for v in self.__slots__)
        return f'{self.__class__.__name__}({args})'

class Name(Constraint):
    __slots__ = ('_name',)

    def __init__(self, name: TLabel):
        self._name = name

    def get_log(self,
            value: tp.Any,
            hint: tp.Any,
            parent: TParent,
            ) -> tp.Iterator[TValidation]:
        # returning anything is an error
        if (n := value.name) != self._name:
            # provide expected first
            yield value, f'name {self._name!r}, provided name {n!r}', parent


class Len(Constraint):
    __slots__ = ('_len',)

    def __init__(self, len: int):
        self._len = len

    def get_log(self,
            value: tp.Any,
            hint: tp.Any,
            parent: TParent,
            ) -> tp.Iterator[TValidation]:
        if (vl := len(value)) != self._len:
            # provide expected first
            yield value, f'length {self._len}, provided length {vl}', parent

# TVLabels = tp.TypeVar('TVLabel', bound=tp.Sequence[TLabel])
# might acccept regular expression objects as label entries?
class Labels(Constraint):
    pass

# TVValidator = tp.TypeVar('TVLabel', bound=tp.Callable[..., bool])
class Validator(Constraint):
    pass



#-------------------------------------------------------------------------------

def to_name(v: tp.Any) -> str:
    if isinstance(v, GENERIC_TYPES):
        # for older Python, not all generics have __name__
        if not (name := getattr(v, '__name__', '')):
            name = str(v)
        return f'{name}[{", ".join(to_name(q) for q in tp.get_args(v))}]'
    if hasattr(v, '__name__'):
        return v.__name__ # type: ignore[no-any-return]
    if v is ...:
        return '...'
    return str(v)

class CheckError(TypeError):

    def __init__(self, log: tp.Iterable[TValidation]) -> None:
        msg = []
        for v, h, p in log:
            if p:
                path = ', '.join(to_name(n) for n in p)
                prefix = f'In {path}, provided'
            else:
                prefix = 'Provided'
            msg.append(f'{prefix} {to_name(type(v))} invalid: expected {to_name(h)}.')
        TypeError.__init__(self, ' '.join(msg))


#-------------------------------------------------------------------------------
# handlers for getting components out of generics

def get_sequence_checks(value: tp.Any,
        hint: tp.Any,
        parent: TParent,
        ) -> tp.Iterable[TValidation]:
    [h_component] = tp.get_args(hint)
    for v in value:
        yield v, h_component, parent

def get_tuple_checks(value: tp.Any,
        hint: tp.Any,
        parent: TParent,
        ) -> tp.Iterable[TValidation]:
    h_components = tp.get_args(hint)
    if h_components[-1] is ...:
        if (h_len := len(h_components)) != 2:
            yield h_len, tp.Literal['invalid ellipses usage'], parent
        else:
            h = h_components[0]
            for v in value:
                yield v, h, parent
    else:
        if (h_len := len(h_components)) != len(value):
            yield h_len, tp.Literal['tuple length invalid'], parent
        for v, h in zip(value, h_components):
            yield v, h, parent

def get_mapping_checks(value: tp.Any,
        hint: tp.Any,
        parent: TParent,
        ) -> tp.Iterable[TValidation]:
    [h_keys, h_values] = tp.get_args(hint)
    for v, h in chain(
            zip(value.keys(), repeat(h_keys)),
            zip(value.values(), repeat(h_values)),
            ):
        yield v, h, parent


# NOTE: we create an instance of dtype.type() so as to not modify h_generic, as it might be Union or other generic that cannot be wrapped in a tp.Type

def get_series_checks(value: tp.Any,
        hint: tp.Any,
        parent: TParent,
        ) -> tp.Iterable[TValidation]:
    h_index, h_generic = tp.get_args(hint) # there must be two
    yield value.index, h_index, parent
    yield value.dtype.type(), h_generic, parent

def get_index_checks(value: tp.Any,
        hint: tp.Any,
        parent: TParent,
        ) -> tp.Iterable[TValidation]:
    [h_generic] = tp.get_args(hint)
    yield value.dtype.type(), h_generic, parent

def get_ndarray_checks(value: tp.Any,
        hint: tp.Any,
        parent: TParent,
        ) -> tp.Iterable[TValidation]:
    h_shape, h_dtype = tp.get_args(hint)
    yield value.dtype, h_dtype, parent

def get_dtype_checks(value: tp.Any,
        hint: tp.Any,
        parent: TParent,
        ) -> tp.Iterable[TValidation]:
    [h_generic] = tp.get_args(hint)
    yield value.type(), h_generic, parent

#-------------------------------------------------------------------------------

def check(
        value: tp.Any,
        hint: tp.Any,
        fail_fast: bool = False,
        parent: TParent = (),
        ) -> tp.Iterable[TValidation]:

    q = deque(((value, hint, parent),))
    log: tp.List[TValidation] = []

    while q:
        if fail_fast and log:
            return log

        v, h, p = q.popleft()

        if h is tp.Any:
            continue

        p_next = p + (h,)

        if is_union(h):
            # NOTE: must check union types first as tp.Union matches as generic type
            u_log: tp.List[TValidation] = []
            for c_hint in tp.get_args(h): # get components
                # handing one pair at a time with a secondary call will allow nested types in the union to be evaluated on their own
                c_log = check(v, c_hint, fail_fast, p_next)
                if not c_log: # no error found, can exit
                    break
                u_log.extend(c_log)
            else: # no breaks, so no matches within union
                log.extend(u_log)
        elif isinstance(h, Constraint):
            log.extend(h.get_log(v, h, p_next))
        elif isinstance(h, GENERIC_TYPES):
            # have a generic container
            origin = tp.get_origin(h)

            if origin is type: # a tp.Type[x] generic
                [t] = tp.get_args(h)
                try: # the v should be a subclass of t
                    t_check = issubclass(t, v)
                except TypeError:
                    t_check = False
                if t_check:
                    continue
                log.append((v, h, p))
            elif origin is tp.Annotated:
                h_type, *h_annotations = tp.get_args(h)
                # perform the un-annoitated check
                q.append((v, h_type, p_next))
                for h_annotation in h_annotations:
                    if isinstance(h_annotation, Constraint):
                        q.append((v, h_annotation, p_next))
            else:
                if origin is typing.Literal:
                    l_log: tp.List[TValidation] = []
                    for l_hint in tp.get_args(h): # get components
                        c_log = check(v, l_hint, fail_fast, p_next)
                        if not c_log: # no error found, can exit
                            break
                        l_log.extend(c_log)
                    else: # no breaks, so no matches within union
                        log.extend(l_log)
                    continue

                if not isinstance(v, origin):
                    log.append((v, origin, p))
                    continue

                if isinstance(v, tuple):
                    q.extend(get_tuple_checks(v, h, p_next))
                elif isinstance(v, Sequence):
                    q.extend(get_sequence_checks(v, h, p_next))
                elif isinstance(v, MutableMapping):
                    q.extend(get_mapping_checks(v, h, p_next))
                elif isinstance(v, Index):
                    q.extend(get_index_checks(v, h, p_next))
                elif isinstance(v, Series):
                    q.extend(get_series_checks(v, h, p_next))
                elif isinstance(v, np.ndarray):
                    q.extend(get_ndarray_checks(v, h, p_next))
                elif isinstance(v, np.dtype):
                    q.extend(get_dtype_checks(v, h, p_next))
                else:
                    raise NotImplementedError(f'no handling for generic {origin}')
        elif not isinstance(h, type):
            # h is value from a literal
            # must check type: https://peps.python.org/pep-0586/#equivalence-of-two-literals
            if type(v) != type(h) or v != h: # pylint: disable=C0123
                log.append((v, h, p))
        else: # h is a non-generic type
            # special cases
            if v.__class__ is bool:
                if h is bool:
                    continue
            # general case
            elif isinstance(v, h):
                continue
            log.append((v, h, p))

    return log


def check_type(value: tp.Any,
        hint: tp.Any,
        fail_fast: bool = False,
        parent: TParent = (),
        ) -> None:
    log = check(value, hint, fail_fast, parent)
    if log:
        raise CheckError(log)


TVFunc = tp.TypeVar('TVFunc', bound=tp.Callable[..., tp.Any])

def check_interface(
        *args: tp.Any,
        fail_fast: bool = False,
        ) -> tp.Callable[[TVFunc], TVFunc]:

    def decorator(func: TVFunc) -> TVFunc:

        @wraps(func)
        def wrapper(*args: tp.Any, **kwargs: tp.Any) -> tp.Any:
            # dict keyed by parameter name, only for those defined; if return is hinted, will be under key 'return'
            # hints = tp.get_type_hints(func)
            sig = Signature.from_callable(func)
            sig_bound = sig.bind(*args, **kwargs)
            sig_bound.apply_defaults()
            parent = (f'args of {sig}',)
            for k, v in sig_bound.arguments.items():
                if (h_p := sig.parameters[k].annotation) != Signature.empty:
                    check_type(v, h_p, fail_fast, parent)

            post = func(*args, **kwargs)

            if (h_return := sig.return_annotation) != Signature.empty:
                check_type(post, h_return, fail_fast, (f'return of {sig}',))

            return post

        return tp.cast(TVFunc, wrapper)

    if args: # if used without calling with kwargs
        if len(args) != 1 or not callable(args[0]):
            raise NotImplementedError('Only provide keyword arguments.')
        return decorator(args[0]) # type: ignore

    return decorator







