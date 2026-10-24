from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from chimaera_ros.dds import require_trace_overlay


def test_old_trace_runtime_cannot_silently_produce_delivery_only_measurements():
    library = Path('/overlay/librmw_fastrtps_cpp.so')
    for transport_abi in (None, 0, 1):
        plugin = SimpleNamespace(chimaera_rmw_trace_abi=lambda: 1)
        if transport_abi is not None:
            plugin.chimaera_dds_trace_transport_abi = lambda: transport_abi
        with patch('chimaera_ros.dds.require_middleware_overlay', return_value=library), \
                patch('chimaera_ros.dds.ctypes.CDLL', return_value=plugin):
            if transport_abi == 1:
                assert require_trace_overlay() == library
            else:
                with pytest.raises(ValueError, match='current DDS runtime'):
                    require_trace_overlay()
