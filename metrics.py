import torch
import numpy as np
from sklearn.neighbors import KNeighborsClassifier
from scipy.stats import spearmanr
from pyannote.audio.embedding.extraction import SequenceEmbedding
from pyannote.database import get_protocol, get_unique_identifier
from pyannote.metrics.binary_classification import det_curve
from pyannote.core.utils.distance import cdist
from pyannote.core import Timeline
from distances import Distance
from losses.base import TrainingListener


class Metric:

    def fit(self, embeddings, y):
        raise NotImplementedError("A metric must implement the method 'fit'")

    def calculate_batch(self, embeddings, logits, y):
        raise NotImplementedError("A metric must implement the method 'calculate_batch'")

    def get(self):
        raise NotImplementedError("A metric must implement the method 'get'")


class KNNAccuracyMetric(Metric):
    """ TODO update docs
    Abstracts the accuracy calculation strategy. It uses a K Nearest Neighbors
        classifier fit with the embeddings produced for the training set,
        to determine to which class a given test embedding is assigned to.
    :param train_embeddings: a tensor of shape (N, d), where
            N = training set size
            d = embedding dimension
    :param train_y: a non one-hot encoded tensor of labels for the train embeddings
    :param distance: a Distance object for the KNN classifier
    """

    def __init__(self, distance):
        self.knn = KNeighborsClassifier(n_neighbors=1, metric=distance.to_sklearn_metric())
        self.correct, self.total = 0, 0

    def fit(self, embeddings, y):
        self.knn.fit(embeddings, y)

    def calculate_batch(self, embeddings, logits, y):
        predicted = self.knn.predict(embeddings)
        self.correct += (predicted == y).sum()
        self.total += y.shape[0]

    def get(self):
        metric = self.correct / self.total
        self.correct, self.total = 0, 0
        return metric


class LogitsSpearmanMetric(Metric):

    def __init__(self):
        self.predictions, self.targets = [], []

    def fit(self, embeddings, y):
        pass

    def calculate_batch(self, embeddings, logits, y):
        output = np.exp(logits)
        predicted = []
        for i in range(output.shape[0]):
            predicted.append(0 * output[i, 0] +
                             1 * output[i, 1] +
                             2 * output[i, 2] +
                             3 * output[i, 3] +
                             4 * output[i, 4] +
                             5 * output[i, 5])
        self.predictions.extend(predicted)
        self.targets.extend(list(y))

    def get(self):
        metric = spearmanr(self.predictions, self.targets)[0]
        self.predictions, self.targets = [], []
        return metric


class DistanceSpearmanMetric(Metric):

    def __init__(self, distance: Distance):
        self.distance = distance
        self.similarity, self.targets = [], []

    def fit(self, embeddings, y):
        pass

    def calculate_batch(self, embeddings, logits, y):
        embeddings1, embeddings2 = embeddings
        self.similarity.extend([-self.distance.dist(embeddings1[i,:].unsqueeze(0), embeddings2[i,:].unsqueeze(0))
                                for i in range(embeddings1.size(0))])
        self.targets.extend(list(y))

    def get(self):
        metric = spearmanr(self.similarity, self.targets)[0]
        self.similarity, self.targets = [], []
        return metric


class SpeakerValidationConfig:

    def __init__(self, protocol_name, feature_extraction, preprocessors, duration):
        self.protocol_name = protocol_name
        self.feature_extraction = feature_extraction
        self.preprocessors = preprocessors
        self.duration = duration


class SpeakerVerificationEvaluator(TrainingListener):

    @staticmethod
    def get_hash(trial_file):
        uri = get_unique_identifier(trial_file)
        try_with = trial_file['try_with']
        if isinstance(try_with, Timeline):
            segments = tuple(try_with)
        else:
            segments = (try_with,)
        return hash((uri, segments))

    def __init__(self, device: str, batch_size: int, distance: Distance, config: SpeakerValidationConfig, callbacks=[]):
        super(SpeakerVerificationEvaluator, self).__init__()
        self.device = device
        self.batch_size = batch_size
        self.distance = distance
        self.config = config
        self.callbacks = callbacks
        self.best_metric, self.best_epoch = 0, -1

    def _eval(self, model):
        model.eval()
        sequence_embedding = SequenceEmbedding(model=model,
                                               feature_extraction=self.config.feature_extraction,
                                               duration=self.config.duration,
                                               step=.5 * self.config.duration,
                                               batch_size=self.batch_size,
                                               device=self.device)
        protocol = get_protocol(self.config.protocol_name, progress=False, preprocessors=self.config.preprocessors)

        y_true, y_pred, cache = [], [], {}

        for trial in protocol.development_trial():

            # compute embedding for file1
            file1 = trial['file1']
            hash1 = self.get_hash(file1)
            if hash1 in cache:
                emb1 = cache[hash1]
            else:
                emb1 = sequence_embedding.crop(file1, file1['try_with'])
                emb1 = np.mean(np.stack(emb1), axis=0, keepdims=True)
                cache[hash1] = emb1

            # compute embedding for file2
            file2 = trial['file2']
            hash2 = self.get_hash(file2)
            if hash2 in cache:
                emb2 = cache[hash2]
            else:
                emb2 = sequence_embedding.crop(file2, file2['try_with'])
                emb2 = np.mean(np.stack(emb2), axis=0, keepdims=True)
                cache[hash2] = emb2

            # compare embeddings
            dist = cdist(emb1, emb2, metric=self.distance.to_sklearn_metric())[0, 0]
            y_pred.append(dist)
            y_true.append(trial['reference'])

        _, _, _, eer = det_curve(np.array(y_true), np.array(y_pred), distances=True)
        # Returning 1-eer because the evaluator keeps track of the highest metric value
        return 1 - eer

    def on_before_train(self, checkpoint):
        if checkpoint is not None:
            self.best_metric = checkpoint['accuracy']

    def on_after_epoch(self, epoch, model, loss_fn, optim, mean_loss):
        metric_value = self._eval(model)
        print(f"--------------- Epoch {epoch:02d} Results ---------------")
        print(f"Dev EER: {1 - metric_value:.6f}")
        if self.best_epoch != -1:
            print(f"Best until now: {1 - self.best_metric:.6f}, at epoch {self.best_epoch}")
        print("------------------------------------------------")
        if metric_value > self.best_metric:
            self.best_metric = metric_value
            self.best_epoch = epoch
            print('New Best Dev EER!')
            for cb in self.callbacks:
                cb.on_best_accuracy(epoch, model, loss_fn, optim, metric_value, None, None)


class ClassAccuracyEvaluator(TrainingListener):

    def __init__(self, device, loader, metric, batch_transforms=[], callbacks=[]):
        super(ClassAccuracyEvaluator, self).__init__()
        self.device = device
        self.loader = loader
        self.metric = metric
        self.batch_transforms = batch_transforms
        self.callbacks = callbacks
        self.feat_train, self.y_train = None, None
        self.best_metric, self.best_epoch = 0, -1

    def _eval(self, model):
        model.eval()
        feat_test, logits_test, y_test = [], [], []
        for cb in self.callbacks:
            cb.on_before_test()
        with torch.no_grad():
            for i in range(self.loader.nbatches()):
                x, y = next(self.loader)

                # Apply custom transformations to the batch before feeding the model
                for transform in self.batch_transforms:
                    x, y = transform(x, y)

                # Feed Forward
                feat, logits = model(x, y)
                feat = feat.detach().cpu().numpy()
                logits = logits.detach().cpu().numpy()
                y = y.detach().cpu().numpy()

                # Track accuracy
                feat_test.append(feat)
                logits_test.append(logits)
                y_test.append(y)
                self.metric.calculate_batch(feat, logits, y)

                for cb in self.callbacks:
                    cb.on_batch_tested(i, feat)

        for cb in self.callbacks:
            cb.on_after_test()
        return np.concatenate(feat_test), np.concatenate(y_test)

    def on_before_train(self, checkpoint):
        if checkpoint is not None:
            self.best_metric = checkpoint['accuracy']

    def on_before_epoch(self, epoch):
        self.feat_train, self.y_train = [], []

    def on_after_gradients(self, epoch, ibatch, feat, logits, y, loss):
        self.y_train.append(y.detach().cpu().numpy())

    def on_after_epoch(self, epoch, model, loss_fn, optim, mean_loss):
        feat_train = np.concatenate(self.feat_train)
        y_train = np.concatenate(self.y_train)
        self.metric.fit(feat_train, y_train)
        feat_test, y_test = self._eval(model)
        metric_value = self.metric.get()
        print(f"--------------- Epoch {epoch:02d} Results ---------------")
        print(f"Dev Accuracy: {metric_value:.6f}")
        if self.best_epoch != -1:
            print(f"Best until now: {self.best_metric:.6f}, at epoch {self.best_epoch}")
        print("------------------------------------------------")
        if metric_value > self.best_metric:
            self.best_metric = metric_value
            self.best_epoch = epoch
            print('New Best Dev Accuracy!')
            for cb in self.callbacks:
                cb.on_best_accuracy(epoch, model, loss_fn, optim, metric_value, feat_test, y_test)


class STSEvaluator(TrainingListener):

    def __init__(self, device, loader, metric, batch_transforms=[], callbacks=[]):
        super(STSEvaluator, self).__init__()
        self.device = device
        self.loader = loader
        self.metric = metric
        self.batch_transforms = batch_transforms
        self.callbacks = callbacks
        self.best_metric, self.best_epoch = 0, -1

    def _eval(self, model):
        model.eval()
        feat_test, y_test = [], []
        for cb in self.callbacks:
            cb.on_before_test()
        with torch.no_grad():
            for i in range(self.loader.nbatches()):
                x, y = next(self.loader)

                # Apply custom transformations to the batch before feeding the model
                for transform in self.batch_transforms:
                    x, y = transform(x, y)

                # Feed Forward
                feat = model(x)

                # In evaluation mode, we always receive 2 phrases and no logits
                feat1 = feat[0].detach().cpu().numpy()
                feat2 = feat[1].detach().cpu().numpy()
                y = y.detach().cpu().numpy()

                # Track accuracy
                feat_test.append((feat1, feat2))
                y_test.append(y)
                self.metric.calculate_batch(feat, None, y)

                for cb in self.callbacks:
                    cb.on_batch_tested(i, feat)

        for cb in self.callbacks:
            cb.on_after_test()
        return feat_test, y_test

    def on_before_train(self, checkpoint):
        if checkpoint is not None:
            self.best_metric = checkpoint['accuracy']

    def on_after_epoch(self, epoch, model, loss_fn, optim, mean_loss):
        feat_test, y_test = self._eval(model.to_prediction_model())
        metric_value = self.metric.get()
        print(f"--------------- Epoch {epoch:02d} Results ---------------")
        print(f"Dev Spearman: {metric_value:.6f}")
        if self.best_epoch != -1:
            print(f"Best until now: {self.best_metric:.6f}, at epoch {self.best_epoch}")
        print("------------------------------------------------")
        if metric_value > self.best_metric:
            self.best_metric = metric_value
            self.best_epoch = epoch
            print('New Best Dev Spearman!')
            for cb in self.callbacks:
                cb.on_best_accuracy(epoch, model, loss_fn, optim, metric_value, feat_test, y_test)
