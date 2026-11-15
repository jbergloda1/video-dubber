import numpy as np

from dubber import speakers


def _emb(base, k, noise=0.05, seed=0):
    rng = np.random.default_rng(seed)
    e = base + rng.normal(0, noise, (k, len(base)))
    return e / np.linalg.norm(e, axis=1, keepdims=True)


def test_unsure_pitch_lines_follow_the_closest_voice():
    m, f = np.zeros(16), np.zeros(16)
    m[0], f[1] = 1, 1
    E = np.vstack([_emb(m, 6, seed=1), _emb(f, 6, seed=2), _emb(m, 2, seed=3), _emb(f, 2, seed=4)])
    f0 = np.array([120] * 6 + [300] * 6 + [190, np.nan] + [175, np.nan], float)
    nv = np.array([20] * 12 + [3, 0, 3, 0])
    g = speakers.genders_from(E, f0, nv)
    assert list(g[:12]) == [0] * 6 + [1] * 6
    assert list(g[12:14]) == [0, 0] and list(g[14:]) == [1, 1]     # voice wins over an unsure pitch


def test_a_speaker_never_mixes_genders_and_tiny_clusters_merge():
    a, b = np.zeros(16), np.zeros(16)
    a[0], b[1] = 1, 1
    E = np.vstack([_emb(a, 10, seed=5), _emb(b, 10, seed=6)])
    g = np.array([0] * 5 + [1] * 5 + [0] * 5 + [1] * 5)       # same voices but split by gender
    lab = speakers.cluster_within(E, g)
    for k in set(lab):
        assert len(set(g[lab == k])) == 1
    assert len(set(lab)) <= 4
