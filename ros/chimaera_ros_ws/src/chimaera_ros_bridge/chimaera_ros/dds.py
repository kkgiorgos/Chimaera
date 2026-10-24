"""Loopback discovery and sized shared memory for large local ROS messages.

This is the same transport configuration used by the ball-catching runner.
Humble's localhost mode appends its own 512 KiB SHM transport, so callers must
set ROS_LOCALHOST_ONLY=0 and use this profile's explicit loopback whitelist.
"""
import ctypes
import hashlib
from pathlib import Path
import re
import shutil
import subprocess


def stage_libraries(destination, binaries):
    """Freeze the selected RMW and linked libraries without relying on host paths."""
    from ament_index_python.packages import get_package_prefix
    plugin = Path(get_package_prefix('rmw_fastrtps_cpp')) / 'lib/librmw_fastrtps_cpp.so'
    destination.mkdir(parents=True, exist_ok=True)
    sources = [plugin]
    for binary in [*binaries, plugin]:
        linked = subprocess.run(['ldd', str(binary)], check=True, capture_output=True,
                                text=True).stdout
        if 'not found' in linked:
            raise ValueError('Missing runtime library for ' + str(binary) + ': ' + linked)
        for line in linked.splitlines():
            match = re.search(r'=> (/.+?) \(', line)
            if match:
                sources.append(Path(match[1]))
    copied = {}
    for source in sources:
        if re.fullmatch(r'lib(c|m|pthread|dl|rt)\.so.*', source.name):
            continue  # Keep the guest's glibc and matching ELF loader together.
        target = destination / source.name
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise ValueError('Conflicting staged runtime library: ' + source.name)
        else:
            shutil.copy2(source, target)
        copied[source.name] = dict(source=str(source), sha256=digest)
    return copied


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


def require_trace_overlay():
    """Old routing overlays do not contain the opt-in publication/take hooks."""
    library = require_middleware_overlay()
    try:
        plugin = ctypes.CDLL(str(library))
        if plugin.chimaera_rmw_trace_abi() != 1 or plugin.chimaera_dds_trace_transport_abi() != 1:
            raise AttributeError('unsupported trace ABI')
    except (OSError, AttributeError) as error:
        raise ValueError('Transport tracing requires the current DDS runtime and RMW overlay; '
                         'rebuild chimaera_dds_transport and the middleware overlay') from error
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


def udp_loopback_profile():
    """Host-only UDP reference, without transport SHM or DDS data-sharing."""
    return '''<?xml version="1.0" encoding="UTF-8"?>
<profiles xmlns="http://www.eprosima.com/XMLSchemas/fastRTPS_Profiles">
  <transport_descriptors><transport_descriptor>
    <transport_id>udp_loopback_only</transport_id><type>UDPv4</type>
    <maxMessageSize>65500</maxMessageSize>
    <interfaceWhiteList><address>127.0.0.1</address></interfaceWhiteList>
  </transport_descriptor></transport_descriptors>
  <participant profile_name="udp_loopback_participant" is_default_profile="true">
    <rtps><userTransports><transport_id>udp_loopback_only</transport_id></userTransports>
      <useBuiltinTransports>false</useBuiltinTransports>
    </rtps>
  </participant>
  <data_writer profile_name="udp_loopback_writer" is_default_profile="true">
    <qos><data_sharing><kind>OFF</kind></data_sharing></qos>
  </data_writer>
  <data_reader profile_name="udp_loopback_reader" is_default_profile="true">
    <qos><data_sharing><kind>OFF</kind></data_sharing></qos>
  </data_reader>
</profiles>
'''
