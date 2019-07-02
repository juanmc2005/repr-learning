#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np
from distances import Distance
from scipy.spatial.distance import squareform
from pyannote.core.utils.distance import to_condensed


class TripletSamplingStrategy:
    
    def triplets(self, y, distances):
        """
        Sample triplets from batch x according to labels y
        :param y: non one-hot labels for the current batch
        :param distances: a condensed distance matrix for the current batch
        :return: a tuple (anchors, positives, negatives) corresponding to the triplets built
        """
        raise NotImplementedError("a TripletSamplingStrategy should implement 'triplets'")

    def filter(self, dist_pos, dist_neg):
        return dist_pos, dist_neg


class BatchAll(TripletSamplingStrategy):
    """
    Batch all strategy. Create every possible triplet for a given batch
    """

    def triplets(self, y, distances):
        anchors, positives, negatives = [], [], []
        for anchor, y_anchor in enumerate(y):
            for positive, y_positive in enumerate(y):
                # if same embedding or different labels, skip
                if (anchor == positive) or (y_anchor != y_positive):
                    continue
                for negative, y_negative in enumerate(y):
                    if y_negative == y_anchor:
                        continue
                    anchors.append(anchor)
                    positives.append(positive)
                    negatives.append(negative)
        return anchors, positives, negatives


class SemiHardNegative(TripletSamplingStrategy):

    def __init__(self, m: float, deviation: float):
        self.m = m
        self.deviation = deviation

    def filter(self, dist_pos, dist_neg):
        keep_inds = []
        for i in range(dist_neg.size(0)):
            keep_inds.append(self.m >= dist_neg[i] or self.deviation >= dist_neg[i] - self.m)
        keep_inds = torch.Tensor(keep_inds).float()
        return keep_inds


class HardestNegative(TripletSamplingStrategy):
    """
    Hardest negative strategy.
    Create every possible triplet with the hardest negative for each anchor
    """

    def triplets(self, y, distances):
        anchors, positives, negatives = [], [], []
        distances = squareform(distances.detach().cpu().numpy())
        y = y.cpu().numpy()
        for anchor, y_anchor in enumerate(y):
            # hardest negative
            d = distances[anchor]
            neg = np.where(y != y_anchor)[0]
            negative = int(neg[np.argmin(d[neg])])
            for positive in np.where(y == y_anchor)[0]:
                if positive == anchor:
                    continue
                anchors.append(anchor)
                positives.append(positive)
                negatives.append(negative)
        return anchors, positives, negatives
    
    
class HardestPositiveNegative(TripletSamplingStrategy):
    """
    Hardest positive and hardest negative strategy.
    Create a single triplet for anchor, with the hardest positive and the
        hardest negative. This can only be done if there are positives and
        negatives for every anchor.
    """

    def triplets(self, y, distances):
        anchors, positives, negatives = [], [], []
        distances = squareform(distances.detach().cpu().numpy())
        y = y.cpu().numpy()
        for anchor, y_anchor in enumerate(y):
            d = distances[anchor]
            # hardest positive
            pos = np.where(y == y_anchor)[0]
            pos = [p for p in pos if p != anchor]
            # hardest negative
            neg = np.where(y != y_anchor)[0]
            if d[pos].size > 0 and d[neg].size > 0:
                positive = int(pos[np.argmax(d[pos])])
                negative = int(neg[np.argmin(d[neg])])
                anchors.append(anchor)
                positives.append(positive)
                negatives.append(negative)
        return anchors, positives, negatives
    

class TripletLoss(nn.Module):
    """
    Triplet loss module
    Reference: https://arxiv.org/pdf/1503.03832.pdf
    :param device: a device in which to run the computation
    :param margin: a margin value to separe classes
    :param distance: a distance object to measure between the samples
    :param sampling: a TripletSamplingStrategy
    """

    def __init__(self, device: str, margin: float, distance: Distance,
                 size_average: bool, online: bool = True, sampling=BatchAll()):
        super(TripletLoss, self).__init__()
        self.device = device
        self.margin = margin
        self.distance = distance
        self.size_average = size_average
        self.online = online
        self.sampling = sampling
    
    def _calculate_distances(self, x, y):
        """
        Calculate the distances to positives and negatives for each anchor in the batch
        :param x: a tensor corresponding to a batch of size (N, d), where
            N = batch size
            d = dimension of the feature vectors
        :param y: a non one-hot label tensor corresponding to the batch
        :return: a pair (distances to positives, distances to negatives)
        """
        # Batch size
        n = x.size(0)
        # Calculate distances between every sample in the batch
        dist = self.distance.pdist(x).to(self.device)
        # Sample triplets according to the chosen strategy
        anchors, positives, negatives = self.sampling.triplets(y, dist)
        # Condense indices so we can fetch distances using the condensed triangular matrix (less memory footprint)
        pos = to_condensed(n, anchors, positives)
        neg = to_condensed(n, anchors, negatives)
        # Fetch the distances to positives and negatives
        return dist[pos], dist[neg]
    
    def forward(self, feat, logits, y):
        """
        Calculate the triplet loss
        :param feat: a tensor corresponding to a batch of size (N, d), where
            N = batch size
            d = dimension of the feature vectors
        :param logits: unused, kept for compatibility purposes
        :param y: a non one-hot label tensor corresponding to the batch
        :return: the triplet loss value
        """
        if self.online:
            # Calculate the distances to positives and negatives for each anchor
            dpos, dneg = self._calculate_distances(feat, y)
        else:
            # 'feat' is already separated into triplets
            anchors, positives, negatives = feat
            # Calculate the distances to positives and negatives for each anchor
            dpos = self.distance.dist(anchors, positives)
            dneg = self.distance.dist(anchors, negatives)
            # keep_mask = self.sampling.filter(dpos, dneg).to(self.device)
            # dpos = keep_mask * dpos
            # dneg = keep_mask * dneg

        # Calculate the loss using the margin
        loss = F.relu(torch.pow(dpos, 2) - torch.pow(dneg, 2) + self.margin)
        return loss.mean() if self.size_average else loss.sum()
