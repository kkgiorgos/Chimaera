"""Read bounded startup evidence without treating recoverable I/O errors as fatal."""
from pathlib import Path
import re
import struct


def log_tail(path, limit=65536):
    try:
        with path.open('rb') as source:
            source.seek(0, 2)
            source.seek(max(0, source.tell() - limit))
            return source.read(limit).decode('utf-8', errors='replace')
    except OSError:
        return ''


def startup_status(directory):
    directory = Path(directory)
    serial = directory / 'gem5/board.pc.com_1.device'
    if not serial.is_file():
        serial = directory / 'gem5/system.pc.com_1.device'
    console = log_tail(serial)
    gem5 = log_tail(directory / 'gem5.log')
    phase, description = 'gem5_boot', 'Guest has not reached readfile startup'
    for marker, next_phase, detail in [
        ('CHIMAERA_STARTUP readfile', 'guest_setup', 'Guest is executing readfile setup'),
        ('CHIMAERA_STARTUP launch', 'guest_launch', 'Guest is launching its ROS session'),
        ('Waiting for guest application ROS endpoints before workbegin',
         'guest_discovery', 'Guest bridge is waiting for local ROS endpoints'),
        ('Guest bridge ready; entering workbegin barrier', 'workbegin', 'Guest bridge reached workbegin'),
    ]:
        if marker in console:
            phase, description = next_phase, detail
    if 'Timing socket ready at ' in gem5:
        phase, description = 'timing_ready', 'gem5 timing server is available'
    driver = log_tail(directory / 'driver.log')
    if ('Latency startup: restarting guest session' in driver
            and 'Latency startup: next guest ready' not in driver):
        phase, description = 'guest_restart', 'Restarting the guest benchmark session'
    try:
        magic, value = struct.unpack('<QQ', (directory / 'logical-clock.bin').read_bytes())
        if magic == 0x434c42434c4f434b and value != 2**64 - 1:
            phase, description = 'cosimulation', 'Host initialized the simulation clock'
    except (OSError, struct.error):
        pass
    evidence = re.findall(r'[^\r\n]*(?:lost interrupt|failed command: READ DMA|'
                          r'I/O error, dev sd|CHIMAERA_STARTUP failed|Guest bridge:)[^\r\n]*', console)
    return dict(phase=phase, description=description, serial_evidence=evidence[-6:],
                serial_log=str(serial), serial_tail=console[-8192:], gem5_tail=gem5[-4096:])
