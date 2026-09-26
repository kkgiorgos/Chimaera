import math
import numpy as np
import pytest
from wall_follow_benchmark.core import DEFAULTS, command, validate
from wall_follow_benchmark.world import make_world
import xml.etree.ElementTree as ET


def scan_wall(distance, heading=0.):
    a = np.linspace(-math.pi, math.pi,720)
    normal = np.array([-math.sin(heading),math.cos(heading)])
    den = normal[0]*np.cos(a)+normal[1]*np.sin(a)
    r = np.full_like(a,np.inf)
    mask=den < -1e-6
    r[mask]=-distance/den[mask]
    return r,a[0],a[1]-a[0]


def test_parallel_wall_and_feedback_sign():
    for distance in [.5,.8,1.1]:
        r,a,da=scan_wall(distance)
        v,w,d,h,state,_=command(r,a,da,.05,20,DEFAULTS)
        assert state=='tracking'
        assert d==pytest.approx(distance)
        assert h==pytest.approx(0,abs=1e-10)
        assert w==pytest.approx(-DEFAULTS['kp']*(distance-.8))
        assert v>0


def test_heading_feedback():
    r,a,da=scan_wall(.8,.1)
    assert command(r,a,da,.05,20,DEFAULTS)[1]>0


def test_front_obstacle_and_invalid_scan():
    r,a,da=scan_wall(.8)
    r[350:370]=.3
    result=command(r,a,da,.05,20,DEFAULTS)
    assert result[0]==0 and result[1]>0 and result[4]=='corner'
    assert command([float('nan')]*720,a,da,.05,20,DEFAULTS)[4]=='invalid_front'
    assert command([float('inf')]*720,a,da,.05,20,DEFAULTS)[4]=='lost_wall'


@pytest.mark.parametrize('key,value',[('control_hz',0.),('beam_stride',0),('speed',float('nan')),('sector_end',5.)])
def test_invalid_parameters(key,value):
    with pytest.raises(ValueError):
        validate(dict(DEFAULTS,**{key:value}))


def test_world():
    tree=ET.fromstring(make_world())
    assert len(tree.findall('.//sensor'))==1
    assert len(tree.findall('.//joint'))==4
    with pytest.raises(ValueError):
        make_world(lidar_hz=0)


def test_corner_returns_do_not_bias_wall_fit():
    r,a,da=scan_wall(.8)
    angles=a+np.arange(len(r))*da
    ahead=np.cos(angles)>0
    r[ahead]=np.minimum(r[ahead],.7/np.cos(angles[ahead]))
    result=command(r,a,da,.05,20,DEFAULTS)
    assert result[4]=='tracking'
    assert result[2]==pytest.approx(.8,abs=.01)
    assert abs(result[3])<.02


def test_noisy_wall_with_outliers():
    r,a,da=scan_wall(.8)
    rng=np.random.default_rng(42)
    mask=np.isfinite(r)
    r[mask]+=rng.normal(0,.005,mask.sum())
    ids=np.arange(160,260,7)
    r[ids]=3.
    result=command(r,a,da,.05,20,DEFAULTS)
    assert result[4]=='tracking'
    assert result[2]==pytest.approx(.8,abs=.015)


@pytest.mark.parametrize('width,height', [(12.,8.), (8.,6.), (20.,12.), (4.,4.)])
def test_arena_dimensions_spawn_and_ground_truth(width,height):
    from wall_follow_benchmark.world import wall_distance
    world = ET.fromstring(make_world(arena_width=width, arena_height=height)).find('world')
    east = world.find("model[@name='east']")
    north = world.find("model[@name='north']")
    assert float(east.findtext('pose').split()[0])-.1 == pytest.approx(width/2)
    assert float(north.findtext('pose').split()[1])-.1 == pytest.approx(height/2)
    x,y,*_ = map(float,world.find("model[@name='robot']/pose").text.split())
    assert -width/2 < x < width/2
    assert y == pytest.approx(-height/2+1)
    assert wall_distance(x,y,width,height)==pytest.approx(1)
    assert wall_distance(width/2+.1,0,width,height)==pytest.approx(-.1)


@pytest.mark.parametrize('width,height', [(0.,8.), (3.9,8.), (12.,float('inf')), (float('nan'),8.)])
def test_invalid_arena_dimensions(width,height):
    with pytest.raises(ValueError):
        make_world(arena_width=width, arena_height=height)
