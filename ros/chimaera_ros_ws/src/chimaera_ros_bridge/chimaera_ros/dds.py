"""Loopback discovery and sized shared memory for large local ROS messages.

This is the same transport configuration used by the ball-catching runner.
Humble's localhost mode appends its own 512 KiB SHM transport, so callers must
set ROS_LOCALHOST_ONLY=0 and use this profile's explicit loopback whitelist.
"""


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
