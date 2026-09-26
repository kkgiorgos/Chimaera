from setuptools import setup
from glob import glob
setup(name='wall_follow_benchmark', version='0.1.0', packages=['wall_follow_benchmark'],
      data_files=[('share/ament_index/resource_index/packages', ['resource/wall_follow_benchmark']),
                  ('share/wall_follow_benchmark', ['package.xml']),
                  ('share/wall_follow_benchmark/launch', glob('launch/*.py')),
                  ('share/wall_follow_benchmark/config', glob('config/*'))],
      install_requires=['setuptools'], zip_safe=True,
      maintainer='Chimaera', maintainer_email='maintainer@example.com',
      description='Configurable lidar wall following and reproducible benchmarks', license='Apache-2.0',
      entry_points={'console_scripts': ['controller = wall_follow_benchmark.node:main',
                                       'plot = wall_follow_benchmark.plot:main']})
