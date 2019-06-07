#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import torch
import torch.nn.functional as F
import numpy as np


# TODO remove this function and use pyannote function directly
# def to_condensed(n, i, j):
#     """
#     Borrowed from pyannote: https://github.com/pyannote/pyannote-core
#     Compute index in condensed pdist matrix
#                 V
#         0 | . 0 1 2 3
#      -> 1 | . . 4 5 6 <-   ==>   0 1 2 3 4 5 6 7 8 9
#         2 | . . . 7 8                    ^
#         3 | . . . . 9
#         4 | . . . . .
#            ----------
#             0 1 2 3 4
#     Parameters
#     ----------
#     n : int
#         Number of inputs in squared pdist matrix
#     i, j : `int` or `numpy.ndarray`
#         Indices in squared pdist matrix
#     Returns
#     -------
#     k : `int` or `numpy.ndarray`
#         Index in condensed pdist matrix
#     """
#     i, j = np.array(i), np.array(j)
#     if np.any(i == j):
#         raise ValueError('i and j should be different.')
#     i, j = np.minimum(i, j), np.maximum(i, j)
#     return np.int64(i * n - i * i / 2 - 3 * i / 2 + j - 1)


class Distance:
    """
    A distance function implementing pairwise distance
    """

    def dist(self, x, y):
        raise NotImplementedError("a Distance should implement 'dist'")
    
    def pdist(self, x):
        """
        Calculate the pairwise distance for a given batch
        :param x: a tensor of shape (N, d), where
            N = batch size
            d = feature dimension
        :return: a 1D tensor corresponding to the condensed triangular distance matrix
        """
        raise NotImplementedError("a Distance should implement 'pdist'")
    
    def sqdist_sum(self, x, y):
        """
        Calculate the squared distance between 2 batches and then return the sum
        :param x: a tensor of shape (N, d), where
            N = batch size
            d = feature dimension
        :param y: a tensor of shape (N, d), where
            N = batch size
            d = feature dimension
        :return: a tensor with a single value, the sum of the squared distances
        """
        raise NotImplementedError("a Distance should implement 'sqdist_sum'")
    
    def to_sklearn_metric(self):
        """
        Get the scikit-learn name for this function. This should return a string that
            can be used with sklearn functions
        :return: a string representing the metric that this Distance object implements
        """
        raise NotImplementedError("a Distance should implement 'to_sklearn_metric'")


class CosineDistance(Distance):
    """
    Cosine distance module using PyTorch's cosine_similarity to calculate pdist
    """
    
    def __init__(self):
        super(CosineDistance, self).__init__()
    
    def __str__(self):
        return 'Cosine Distance'
    
    def to_sklearn_metric(self):
        return 'cosine'

    def dist(self, x, y):
        return 1 - F.cosine_similarity(x, y, dim=1, eps=1e-8)
    
    def sqdist_sum(self, x, y):
        d = self.dist(x, y)
        return d.pow(2).sum()
    
    def pdist(self, x):
        nbatch, _ = x.size()
        distances = []
        for i in range(nbatch-1):
            d = 1. - F.cosine_similarity(
                x[i, :].expand(nbatch - 1 - i, -1),
                x[i+1:, :], dim=1, eps=1e-8)
            distances.append(d)
        return torch.cat(distances)


class EuclideanDistance(Distance):
    """
    Euclidean distance module using PyTorch's pdist,
        which already supports this distance
    """
    
    def __init__(self):
        super(EuclideanDistance, self).__init__()
    
    def __str__(self):
        return 'Euclidean Distance'
    
    def to_sklearn_metric(self):
        return 'euclidean'

    def dist(self, x, y):
        return torch.dist(x, y, p=2)
    
    def sqdist_sum(self, x, y):
        return (x - y).pow(2).sum()
    
    def pdist(self, x):
        return F.pdist(x)
