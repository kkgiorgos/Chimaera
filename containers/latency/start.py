"""Start the container-owned workbench using persistent, container-local paths."""
import json
import os
from pathlib import Path

from chimaera_ros.latency_dashboard import serve


def main():
    data = Path('/data')
    data.mkdir(parents=True, exist_ok=True)
    runtime = data / 'runtime.json'
    if not runtime.exists():
        runtime.write_text(json.dumps(dict(
            prefix='/opt/chimaera/latency-runtime', gem5_root='/opt/chimaera/gem5',
            image=os.environ.get('CHIMAERA_GUEST_IMAGE', '/assets/guest.img'),
            kernel=os.environ.get('CHIMAERA_GUEST_KERNEL', '/assets/kernel'),
            bundle='/opt/chimaera/latency-guest.tar.gz'), indent=2) + '\n')
    serve('/repo', data=data, host='0.0.0.0', port=8787,
          public_address=os.environ.get('CHIMAERA_PUBLIC_ADDRESS'))


if __name__ == '__main__':
    main()
