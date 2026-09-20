import numpy as np
from pipeline.prepare_geolife import label_points,samples,vectors

def points(times, speed=1.):
    times=np.asarray(times,dtype=float)
    # At equator one northward meter is 180/(pi*R) degrees.
    return np.column_stack([times*speed*180/(np.pi*6371008.8),np.zeros(len(times)),25569+times/86400])

def test_real_walk_labels_and_overlap_required():
    labels=[(0,100,'walk'),(50,60,'bus'),(120,200,'bike')]
    assert label_points(np.array([-1,0,40,50,60,61,120,201]),labels).tolist()==[-1,0,0,-1,-1,0,-1,-1]

def test_regular_sampling_units_speed_and_straight_turn():
    rows=np.concatenate([r for r,_ in samples(points(range(0,121,5)),[(0,120,'walk')])])
    assert len(rows)==3
    assert np.allclose(rows[:,3:5],1.)
    assert np.all(rows[:,1]==2) and np.all(rows[:,2]==18)
    assert np.allclose(rows[:,[6,8]],30)

def test_gap_mode_change_jump_cannot_be_bridged():
    assert list(samples(points([0,5,10,100,105,110]),[(0,120,'walk')]))==[]
    assert list(samples(points(range(0,91,5)),[(0,40,'walk'),(41,100,'bus')]))==[]
    assert list(samples(points(range(0,91,5),speed=10),[(0,100,'walk')]))==[]

def test_dateline_vector_is_local():
    assert np.linalg.norm(vectors(np.array([0,0]),np.array([179.999,-179.999])))<300
