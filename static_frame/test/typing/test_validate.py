import numpy as np
import typing_extensions as tp

from static_frame.core.validate import validate_pair_raises

def test_ndarray_a() -> None:
    v = np.array([False, True, False])
    # NOTE: must type this as a dytpe, not just a a generic
    h1 = np.ndarray[tp.Any, np.dtype[np.bool_]]

    # validate_pair_raises(v, h1)
