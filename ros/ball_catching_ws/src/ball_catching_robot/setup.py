from glob import glob
from setuptools import setup

setup(name='ball_catching_robot', version='0.1.0', packages=['ball_catching_robot'],
      data_files=[('share/ament_index/resource_index/packages', ['resource/ball_catching_robot']),
                  ('share/ball_catching_robot', ['package.xml']),
                  ('share/ball_catching_robot/launch', glob('launch/*.py'))],
      install_requires=['setuptools'], zip_safe=True,
      entry_points={'console_scripts': [
          'perception = ball_catching_robot.perception:main',
          'intercept = ball_catching_robot.intercept:main',
          'effort_control = ball_catching_robot.effort_control:main']})
