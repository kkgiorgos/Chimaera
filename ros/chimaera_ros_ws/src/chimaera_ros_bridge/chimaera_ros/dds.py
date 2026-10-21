"""Loopback discovery and sized shared memory for large local ROS messages.

This is the same transport configuration used by the ball-catching runner.
Humble's localhost mode appends its own 512 KiB SHM transport, so callers must
set ROS_LOCALHOST_ONLY=0 and use this profile's explicit loopback whitelist.
"""
import ctypes
from pathlib import Path


def require_middleware_overlay():
    """Reject a standard RMW plugin before starting an opted-in simulation."""
    try:
        from ament_index_python.packages import get_package_prefix
        library = Path(get_package_prefix('rmw_fastrtps_cpp')) / 'lib/librmw_fastrtps_cpp.so'
        plugin = ctypes.CDLL(str(library))
        version = plugin.chimaera_rmw_transport_abi()
    except (ImportError, LookupError, OSError, AttributeError) as error:
        raise ValueError('Fast DDS routing requires the pinned middleware RMW overlay; '
                         'build middleware/Dockerfile or follow middleware/README.md') from error
    if version != 1:
        raise ValueError('Incompatible Chimaera middleware RMW ABI')
    return library


def shared_memory_profile(segment_bytes):
    if type(segment_bytes) is not int or not 1024 * 1024 <= segment_bytes <= 128 * 1024 * 1024:
        raise ValueError('DDS shared memory must be 1..128 MiB')
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<profiles xmlns="http://www.eprosima.com/XMLSchemas/fastRTPS_Profiles">
  <transport_descriptors>
    <transport_descriptor>
      <transport_id>local_udp</transport_id><type>UDPv4</type>
      <interfaceWhiteList><address>127.0.0.1</address></interfaceWhiteList>
    </transport_descriptor>
    <transport_descriptor>
      <transport_id>image_shm</transport_id><type>SHM</type>
      <segment_size>{segment_bytes}</segment_size>
    </transport_descriptor>
  </transport_descriptors>
  <participant profile_name="image_participant" is_default_profile="true">
    <rtps>
      <userTransports>
        <transport_id>local_udp</transport_id><transport_id>image_shm</transport_id>
      </userTransports>
      <useBuiltinTransports>false</useBuiltinTransports>
    </rtps>
  </participant>
</profiles>
'''
