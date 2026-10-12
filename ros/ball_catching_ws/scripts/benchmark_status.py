"""Read explicit guest application failures from the saved serial console."""
import re


def guest_failure(path):
    try:
        serial = path.read_text(errors='replace')
    except FileNotFoundError:
        return None
    for pattern in (r'^Guest bridge: .+$', r'^\w+ exited \(-?\d+\); stopping session\r?$',
                    r'^CHIMAERA_GUEST_APPLICATION_EXIT=\d+\r?$'):
        match = re.search(pattern, serial, re.MULTILINE)
        if match:
            return match.group().strip()
    return None
