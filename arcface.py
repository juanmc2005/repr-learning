#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import torch
import torch.nn as nn
import torch.nn.functional as F


class ArcLoss(nn.Module):
    """
    Linear layer with Additive Angular Margin (ArcFace)
    Reference: https://arxiv.org/pdf/1801.07698.pdf
    
    :param nfeat: the number of features in the embedding
    :param nclass: the number of classes
    :param margin: the margin to separate classes in angular space
    :param s: the scaling factor for the feature vector. This will constrain
              the model to a hypersphere of radius s
    """
    
    def __init__(self, nfeat, nclass, margin=0.35, s=4.0):
        super(ArcLoss, self).__init__()
        self.nclass = nclass
        self.margin = margin
        self.s = s
        self.W = nn.Parameter(torch.Tensor(nfeat, nclass))
        self.loss_fn = nn.CrossEntropyLoss()
        nn.init.xavier_uniform_(self.W)
    
    def forward(self, x, y):
        """
        Apply the angular margin transformation
        :param x: a feature vector batch
        :param y: a non one-hot label batch
        :return: the value for the Additive Angular Margin loss
        """
        # Normalize the feature vectors and W
        xnorm = F.normalize(x)
        Wnorm = F.normalize(self.W)
        y = y.type(torch.LongTensor).view(-1, 1)
        # Calculate the logits, which will be our cosθj
        cos_theta_j = torch.mm(xnorm, Wnorm)
        # Get the cosθ corresponding to our classes
        cos_theta_yi = cos_theta_j.gather(1, y)
        # Get the angle separating x and W
        theta_yi = torch.acos(cos_theta_yi)
        # Apply the margin
        cos_theta_yi_margin = torch.cos(theta_yi + self.margin)
        # One hot encoding for y
        one_hot = torch.zeros_like(cos_theta_j)
        one_hot.scatter_(1, y, 1.0)
        # Project margin differences into cosθj
        cos_theta_j += one_hot * (cos_theta_yi_margin - cos_theta_yi)
        # Apply the scaling
        cos_theta_j = self.s * cos_theta_j
        # Apply softmax + cross entropy loss
        return self.loss_fn(cos_theta_j, y.view(-1))


def main(test_cuda=False):
    print('-'*80)
    device = torch.device("cuda" if test_cuda else "cpu")
    loss = ArcLoss(2,10).to(device)
    y = torch.Tensor([0,0,2,1]).to(device)
    feat = torch.rand(4,2).to(device).requires_grad_()
    print(list(loss.parameters()))
    print(loss.W.grad)
    out = loss(feat, y)
    print(out.item())
    out.backward()
    print(loss.W.grad)
    print(feat.grad)

if __name__ == '__main__':
    torch.manual_seed(999)
    main(test_cuda=False)
    if torch.cuda.is_available():
        main(test_cuda=True)
        