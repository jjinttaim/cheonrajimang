"""Small learned endpoint model. Not a temporal movement or detection model."""
import numpy as np
from pyproj import Geod
from scipy.optimize import minimize
from scipy.special import logsumexp, ndtr

GEOD = Geod(ellps='WGS84')
RADIAL_EDGES = np.arange(0., 20000. + 250, 250)
BEARINGS = np.arange(2.5, 360., 5.)
CELLS = (len(RADIAL_EDGES) - 1) * len(BEARINGS)
AREA = np.repeat(np.pi * np.diff(RADIAL_EDGES ** 2) / len(BEARINGS), len(BEARINGS))
PENALTY = .1


def centers(lat, lon):
    radius = np.repeat((RADIAL_EDGES[:-1] + RADIAL_EDGES[1:]) / 2, len(BEARINGS))
    bearing = np.tile(BEARINGS, len(RADIAL_EDGES) - 1)
    x, y, _ = GEOD.fwd(np.full(CELLS, lon), np.full(CELLS, lat), bearing, radius)
    return x, y


def target_cell(lat, lon, find_lat, find_lon):
    bearing, _, distance = GEOD.inv(lon, lat, find_lon, find_lat)
    if distance >= RADIAL_EDGES[-1]:
        return CELLS, float(distance)
    index = int(distance // 250) * len(BEARINGS) + int((bearing % 360) // 5)
    return index, float(distance)


def radial_fit(distances):
    d = np.asarray(distances, float)
    if len(d) < 2 or not np.isfinite(d).all() or (d <= 0).any():
        raise ValueError('Finite positive observed distances required')
    log = np.log(d)
    mu, sigma = float(log.mean()), float(log.std())
    if sigma <= 0:
        raise ValueError('Degenerate distance fit')
    return {'log_mu': mu, 'log_sigma': sigma}


def base_mass(radial):
    mu, sigma = radial['log_mu'], radial['log_sigma']
    z = np.full(len(RADIAL_EDGES), -np.inf)
    z[1:] = (np.log(RADIAL_EDGES[1:]) - mu) / sigma
    cdf = ndtr(z)
    mass = np.repeat(np.diff(cdf) / len(BEARINGS), len(BEARINGS))
    outside = float(ndtr(-z[-1]))
    if (mass <= 0).any() or outside <= 0 or not np.isclose(mass.sum()+outside, 1., atol=1e-12):
        raise ValueError('Invalid radial mass')
    return mass, outside


def distribution(features, radial, beta):
    x = np.asarray(features, float)
    b = np.asarray(beta, float)
    if x.shape[-2:] != (CELLS, 2) or b.shape != (2,) or not np.isfinite(x).all() or not np.isfinite(b).all():
        raise ValueError('Invalid features or coefficients')
    base, outside = base_mass(radial)
    score = np.log(base) + x @ b
    logs = score - logsumexp(score, axis=-1, keepdims=True) + np.log1p(-outside)
    mass = np.exp(logs)
    if not np.allclose(mass.sum(axis=-1) + outside, 1., atol=1e-12):
        raise ValueError('Probability mass not conserved')
    return mass, outside


def objective(beta, features, labels, radial):
    base, outside = base_mass(radial)
    x, y = np.asarray(features, float), np.asarray(labels, int)
    score = np.log(base) + x @ beta
    normalizer = logsumexp(score, axis=1)
    inside = y < CELLS
    rows = np.flatnonzero(inside)
    nll = np.full(len(y), -np.log(outside))
    nll[inside] = normalizer[inside] - score[rows, y[inside]] - np.log1p(-outside)
    conditional = np.exp(score - normalizer[:, None])
    gradient = np.einsum('ij,ijk->ik', conditional, x)
    gradient[inside] -= x[rows, y[inside]]
    gradient[~inside] = 0
    return (float(nll.mean() + PENALTY / 2 * np.dot(beta, beta)),
            gradient.mean(axis=0) + PENALTY * beta)


def fit(features, labels, distances):
    radial = radial_fit(distances)
    result = minimize(objective, np.zeros(2), args=(features, labels, radial), jac=True,
                      method='L-BFGS-B', bounds=[(-3., 3.), (-3., 3.)],
                      options={'maxiter': 200, 'gtol': 1e-7})
    if not result.success or not np.isfinite(result.fun):
        raise ValueError('Terrain fit did not converge: ' + str(result.message))
    return {'kind': 'cop90_endpoint_exponential_tilt_v1', 'radial': radial,
            'beta': result.x.tolist(), 'penalty': PENALTY,
            'iterations': int(result.nit), 'training_objective': float(result.fun),
            'scope': 'US_HIKER_ENDPOINT_NOT_CURRENT_POSITION_OR_POD'}


def evaluate_one(features, label, model):
    mass, outside = distribution(features, model['radial'], model.get('beta', [0., 0.]))
    nll = -np.log(outside if label == CELLS else mass[label])
    order = np.argsort(-(mass / AREA), kind='stable')
    area = np.cumsum(AREA[order])
    hits = [bool(label in order[area <= fraction * AREA.sum()]) for fraction in (.1, .2, .3)]
    return {'nll': float(nll), 'area_hits': hits, 'outside_mass': outside}


def split_indices(lat, lon, groups, held_out, buffered=True):
    lat, lon, groups = np.asarray(lat), np.asarray(lon), np.asarray(groups)
    test = np.flatnonzero(groups == held_out)
    train_mask = groups != held_out
    for i in test:
        _, _, distance = GEOD.inv(np.full(len(lat), lon[i]), np.full(len(lat), lat[i]), lon, lat)
        if buffered:
            train_mask &= np.asarray(distance) > 40000.
    return np.flatnonzero(train_mask), test
