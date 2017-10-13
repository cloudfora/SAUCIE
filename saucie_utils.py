# -*- coding: utf-8 -*-
# File: saucie_utils.py
# Author: Krishnan Srinivasan <krishnan1994 at gmail>
# Date: 21.09.2017
# Last Modified Date: 02.10.2017

"""
Utils for SAUCIE
"""
import numpy as np
import pandas as pd
import glob
import tensorflow as tf
import os

from sklearn.preprocessing import LabelEncoder, MinMaxScaler
from sklearn.model_selection import train_test_split
from sklearn.metrics.pairwise import pairwise_distances
from sklearn.metrics import mutual_info_score
from sklearn.cluster import k_means
from sklearn.decomposition import PCA
from scipy.io import loadmat
from typing import NamedTuple


########### GLOBAL VARIABLES

RAND_SEED = 42
CYTOF_DATASETS = {'flu', 'zika', 'emt_cytof'}
RNASEQ_DATASETS = {'emt_rnaseq', 'mouse'}
FLOAT_DTYPE = np.float32
TF_FLOAT_DTYPE = tf.as_dtype(FLOAT_DTYPE)
EPS = FLOAT_DTYPE(1e-8)
RNASEQ_PCA_DIMS = 15
N_CLUSTS = [3, 7, 10]


########### MODEL/TENSORFLOW UTILS

class SparseLayerConfig(NamedTuple): 
    num_layers: int = 4
    id_lam:   np.array = np.zeros(num_layers, dtype=FLOAT_DTYPE)
    l1_lam:   np.array = np.zeros(num_layers, dtype=FLOAT_DTYPE)
    l1_w_lam: np.array = np.zeros(num_layers, dtype=FLOAT_DTYPE)
    l2_w_lam: np.array = np.zeros(num_layers, dtype=FLOAT_DTYPE)
    l1_b_lam: np.array = np.zeros(num_layers, dtype=FLOAT_DTYPE)
    l2_b_lam: np.array = np.zeros(num_layers, dtype=FLOAT_DTYPE)
    
    def __repr__(self):
        return ('SparseLayerConfig(id_lam={},l1_lam={},l1_w_lam={},l2_w_lam={},'
                'l1_b_lam={},l2_b_lam={})'.format(
                    self.id_lam.tolist(), self.l1_lam.tolist(), self.l1_w_lam.tolist(),
                    self.l2_w_lam.tolist(), self.l1_b_lam.tolist(), self.l2_b_lam.tolist(), 
                ))

    def __eq__(self, other):
        if isinstance(other, self.__class__):
            return (other.num_layers == self.num_layers and
                    np.all(other.id_lam == self.id_lam) and 
                    np.all(other.l1_lam == self.l1_lam) and
                    np.all(other.l1_w_lam == self.l1_w_lam) and 
                    np.all(other.l2_w_lam == self.l2_w_lam) and
                    np.all(other.l1_b_lam == self.l1_b_lam) and 
                    np.all(other.l2_b_lam == self.l2_b_lam))
        return False

    def __ne__(self, other):
        return not self.__eq__(other)


def mean_squared_error(predicted, actual, name='mean_squared_error'):
    return tf.reduce_mean(tf.square(predicted - actual), name=name)


def binary_crossentropy(predicted, actual, name='binary_crossentropy_error'):
    return -tf.reduce_mean(actual * tf.log(predicted + EPS)
                           + (1 - actual) * tf.log(1 - predicted + EPS), name=name)


def binarize(acts, thresh=.5, print_pct=0.01, bin_min=0.01):
    if type(bin_min) == float and bin_min > 0 and bin_min < 1:
        bin_min = len(acts) * bin_min

    binarized = np.where(acts>thresh, 1, 0)
    unique_rows = np.vstack({tuple(row) for row in binarized})
    num_clusters = unique_rows.shape[0]
    new_labels = np.zeros(acts.shape[0])
    excluded_pts = 0

    if num_clusters > 1:
        print('Unique binary clusters: {}'.format(num_clusters))
        for i, row in enumerate(unique_rows[1:]):
            subs = np.where(np.all(binarized == row, axis=1))[0]
            if bin_min and len(subs) < bin_min:
                new_labels[subs] = -1
                excluded_pts += len(subs)
            else:
                new_labels[subs] = i + 1
        uc, counts = np.unique(new_labels, return_counts=True)
        print('Cluster counts (greater than {}% of points), excluding {} points'.format(int(print_pct * 100), excluded_pts))
        print('\n'.join(['{},{}'.format(x,y) for x,y in zip(uc, counts) if y / len(acts) > print_pct]))
    return new_labels


# information dimension regularization penalty

def id_penalty(act, lam, name='id_loss'):
    return tf.multiply(lam, -tf.reduce_mean(act * tf.log(act + EPS)), name=name)


def l1_penalty(t, lam, name='l1_loss'):
    return tf.multiply(lam, tf.reduce_sum(tf.abs(t)), name=name)


def l2_penalty(t, lam, name='l2_loss'):
    return tf.multiply(lam, tf.reduce_sum(tf.sqrt(tf.square(t))), name=name)


def make_dict_str(d={}, custom_keys=[], subset=[], kv_sep=': ', item_sep=', ',
                  float_format='{:6.5E}'):
    if not custom_keys:
        if subset:
            d = d.copy()
            for k in d.keys():
                if k not in subset:
                    del d[key]
        custom_keys = [(k,k) for k in d.keys()]

    item_list = []
    for c_key, key in custom_keys:
        item = d[key]
        if isinstance(item, (float, np.floating)) and item < 1e-4:
            item = float_format.format(item)
        elif isinstance(item, (list, np.ndarray)):
            for i,j in enumerate(item):
                if isinstance(j, (float, np.floating)) and j < 1e-4:
                    item[i] = float_format.format(j)
                else:
                    item[i] = str(item[i])
            item = ','.join(item)
        else:
            item = str(item)
        kv_str = kv_sep.join([c_key, item])
        item_list.append(kv_str)
    dict_str = item_sep.join(item_list)
    return dict_str


# Code from TensorFlow Github issue 4079
# https://github.com/tensorflow/tensorflow/issues/4079

def lrelu(x, leak=0.2, name="lrelu"):
     with tf.variable_scope(name):
         f1 = 0.5 * (1 + leak)
         f2 = 0.5 * (1 - leak)
         return f1 * x + f2 * abs(x)


############ SCOPE DECORATORS

# Code from Danijar Hafner gist:
# https://gist.github.com/danijar/8663d3bbfd586bffecf6a0094cd116f2

import functools

def doublewrap(function):
    """
    A decorator decorator, allowing to use the decorator to be used without
    parentheses if no arguments are provided. All arguments must be optional.
    """
    @functools.wraps(function)
    def decorator(*args, **kwargs):
        if len(args) == 1 and len(kwargs) == 0 and callable(args[0]):
            return function(args[0])
        else:
            return lambda wrapee: function(wrapee, *args, **kwargs)
    return decorator


@doublewrap
def define_var_scope(function, scope=None, *args, **kwargs):
    """
    A decorator for functions that define TensorFlow operations. The wrapped
    function will only be executed once. Subsequent calls to it will directly
    return the result so that operations are added to the graph only once.
    The operations added by the function live within a tf.variable_scope(). If
    this decorator is used with arguments, they will be forwarded to the
    variable scope. The scope name defaults to the name of the wrapped
    function.
    """
    attribute = '_cache_' + function.__name__
    name = scope or function.__name__
    @property
    @functools.wraps(function)
    def decorator(self):
        if not hasattr(self, attribute):
            with tf.variable_scope(name, *args, **kwargs):
                setattr(self, attribute, function(self))
        return getattr(self, attribute)
    return decorator


@doublewrap
def define_name_scope(function, scope=None, *args, **kwargs):
    """
    A decorator for functions that define TensorFlow operations. The wrapped
    function will only be executed once. Subsequent calls to it will directly
    return the result so that operations are added to the graph only once.
    The operations added by the function live within a tf.variable_scope(). If
    this decorator is used with arguments, they will be forwarded to the
    variable scope. The scope name defaults to the name of the wrapped
    function.
    """
    attribute = '_cache_' + function.__name__
    name = scope or function.__name__
    @property
    @functools.wraps(function)
    def decorator(self):
        if not hasattr(self, attribute):
            with tf.name_scope(name, *args, **kwargs):
                setattr(self, attribute, function(self))
        return getattr(self, attribute)
    return decorator


############ DATASET UTILS

# Code from TensorFlow source example:
# https://github.com/tensorflow/tensorflow/blob/master/tensorflow/contrib/learn/python/learn/datasets/mnist.py

class DataSet:
    """Base data set class
    """

    def __init__(self, shuffle=True, labeled=True, **data_dict):
        assert '_data' in data_dict
        if labeled:
            assert '_labels' in data_dict
            assert data_dict['_data'].shape[0] == data_dict['_labels'].shape[0]
        self._labeled = labeled
        self._shuffle = shuffle
        self.__dict__.update(data_dict)
        self._num_samples = self._data.shape[0]
        self._index_in_epoch = 0
        self._epochs_trained = 0
        self._batch_number = 0
        if self._shuffle:
            self._shuffle_data()

    def __len__(self):
        return len(self._data) + len(self._test_data)

    @property
    def epochs_trained(self):
        return self._epochs_trained

    @epochs_trained.setter
    def epochs_trained(self, new_epochs_trained):
        self._epochs_trained = new_epochs_trained

    @property
    def batch_number(self):
        return self._batch_number

    @property
    def index_in_epoch(self):
        return self._index_in_epoch

    @property
    def num_samples(self):
        return self._num_samples

    @property
    def data(self):
        return self._data

    @property
    def labels(self):
        return self._labels

    @property
    def labeled(self):
        return self._labeled

    @property
    def test_data(self):
        return self._test_data

    @property
    def test_labels(self):
        return self._test_labels

    @classmethod
    def load(cls, filename):
        data_dict = np.load(filename)
        labeled = data_dict['_labeled']
        return cls(labeled=labeled, **data_dict)

    def save(self, filename):
        data_dict = self.__dict__
        np.savez_compressed(filename, **data_dict)

    def _shuffle_data(self):
        shuffled_idx = np.arange(self._num_samples)
        np.random.shuffle(shuffled_idx)
        self._data = self._data[shuffled_idx]
        if self._labeled:
            self._labels = self._labels[shuffled_idx]

    def next_batch(self, batch_size):
        assert batch_size <= self._num_samples
        start = self._index_in_epoch
        if start + batch_size > self._num_samples:
            self._epochs_trained += 1
            self._batch_number = 0
            data_batch = self._data[start:]
            if self._labeled:
                labels_batch = self._labels[start:]
            remaining = batch_size - (self._num_samples - start)
            if self._shuffle:
                self._shuffle_data()
            start = 0
            data_batch = np.concatenate([data_batch, self._data[:remaining]],
                                        axis=0)
            if self._labeled:
                labels_batch = np.concatenate([labels_batch,
                                               self._labels[:remaining]],
                                              axis=0)
            self._index_in_epoch = remaining
        else:
            data_batch = self._data[start:start + batch_size]
            if self._labeled:
                labels_batch = self._labels[start:start + batch_size]
            self._index_in_epoch = start + batch_size
        self._batch_number += 1
        batch = (data_batch, labels_batch) if self._labeled else data_batch
        return batch


def load_dataset_from_csv(csv_file, header=None, index_col=None, labels=None,
                          train_ratio=0.9, colnames=None, markers=None, keep_cols=True):
    if type(csv_file) == list:
        data = []
        for csv_fn in csv_file:
            data.append(pd.read_csv(csv_fn, header=header, index_col=index_col).values)
        data = np.concatenate(data)
    else:
        data = pd.read_csv(csv_file, header=header, index_col=index_col).values

    data_dict = {}

    if colnames:
        if type(colnames) == str:
            colnames = [x.strip().split('_')[-1] for x in open(colnames).readlines()]
            data_dict['_colnames'] = colnames
        if markers:
            data_dict['_markers'] = None
            if type(markers) == str:
                markers = [x.strip().split('_')[-1] for x in open(markers).readlines()]
            if not keep_cols:
                data = pd.DataFrame(data, columns=colnames)
                data = data[markers].values
                data_dict['_colnames'] = markers
            else:
                data_dict['_markers'] = markers

    if labels:
        if type(labels) == list:
            for labels_fn in labels_file:
                labels.append(pd.read_csv(labels_fn, header=None, index_col=None).values)
            labels = np.concatenate(labels)
        elif type(labels) == str:
            labels = pd.read_csv(labels_file, header=None, index_col=None).values
        elif type(labels) == int:
            labels = data[:,labels_col]
            data = np.delete(data, labels_col, 1)

        train_data, test_data, train_labels, test_labels = train_test_split(
            data, labels, train_size=train_ratio, random_state=RAND_SEED)
        data_dict['_data'], data_dict['_test_data'] = train_data, test_data
        data_dict['_labels'], data_dict['_test_labels'] = train_labels, test_labels
    else:
        train_data, test_data = train_test_split(data, train_size=train_ratio,
                                                 random_state=RAND_SEED)
        data_dict['_data'], data_dict['_test_data'] = train_data, test_data
        data_dict['labeled'] = False

    dataset = DataSet(**data_dict)
    return dataset


def load_dataset_from_mat(mat_file, data_key='data', label_key=None, colnames=None,
                          train_ratio=0.9):
    mat_dict = loadmat(mat_file) 
    data = mat_dict[data_key].astype(FLOAT_DTYPE)
    if label_key:
        labels = mat_dict[label_key] 
        train_data, test_data, train_labels, test_labels = train_test_split(data, labels, train_ratio=train_ratio, random_state=RAND_SEED)
        data_dict = dict(_data=train_data, _test_data=test_data, _labels=train_labels, _test_labels=test_labels, labeled=True)
    else:
        train_data, test_data = train_test_split(data, train_size=train_ratio, random_state=RAND_SEED)
        data_dict = dict(_data=train_data, _test_data=test_data, labeled=False)
    if colnames:
        colnames = np.array([x.strip() for x in open(colnames).readlines()])
        data_dict['_colnames'] = colnames
    return DataSet(**data_dict)


def load_dataset(dataset, data_path, labels=None, colnames=None, markers=None,
                 keep_cols=None):
    filetype = data_path.split('.')[-1]
    if filetype == 'npz':
        data = DataSet.load(data_path)
    elif filetype == 'csv':
        if len(glob.glob(data_path)) > 1:
            data_path_re = data_path
            data_path = glob.glob(data_path)
        if labels:
            if len(glob.glob(labels)) > 1:
                labels = glob.glob(labels)
            elif labels.isdigit():
                labels = int(labels)
        header = 0 if dataset == 'zika' else None
        data = load_dataset_from_csv(data_path, header=header,
                                     labels=labels,
                                     colnames=colnames,
                                     markers=markers,
                                     keep_cols=keep_cols)
    elif filetype == 'mat':
        data = load_dataset_from_mat(data_path, label_key=labels, colnames=colnames)
    if dataset in CYTOF_DATASETS:
        data._data = np.arcsinh(data._data / 5, dtype=FLOAT_DTYPE)
        data._test_data = np.arcsinh(data._test_data / 5, dtype=FLOAT_DTYPE)
        ms = MinMaxScaler()
        ms.fit(np.concatenate([data._data, data._test_data]))
        data._data = ms.transform(data._data)
        data._test_data = ms.transform(data._test_data)
        data.__dict__['_max'] = ms.data_max_
        data.__dict__['_min'] = ms.data_min_
    elif dataset in RNASEQ_DATASETS and filetype != 'npz':
        x = np.concatenate([data.data, data.test_data], axis=0)

        # libsize normalization
        x = np.clip(x, x.min(axis=0), np.percentile(x, 99.5, axis=0))

        # PCA
        pca = PCA(RNASEQ_PCA_DIMS)
        print('Fitting PCA on data with shape: {}, may take awhile'.format(data.data.shape))
        x = pca.fit_transform(x)
        print('Done fitting PCA')
        data._data = x[:len(data._data)]
        data._test_data = x[len(data._data):]
        data.__dict__['_components'] = pca.components_
        data.__dict__['_mean_offset'] = pca.mean_

        """
        # min-max scaling
        ms = MinMaxScaler()
        ms.fit(x)
        data._data = ms.transform(data._data)
        data._test_data = ms.transform(data._test_data)
        data.__dict__['_max'] = ms.data_max_
        data.__dict__['_min'] = ms.data_min_
        """
        del x
    if filetype != 'npz':
        if type(data_path) == str:
            data_path = data_path[:-4] + '.npz'
        else:
            data_path = data_path_re[:-5] + '.npz'
        data.save(data_path)
    return data


def activation_mutual_info(x, y, method='dist', k=None, bins=None):
    """
    Args:
        x: input data (n x d)
        y: data embedded by autoencoder (n x d_layer)
    Returns:
        mi: mutual info score from quantized distance grid 
    """
    n = len(x)
    if k is None:
        x_dist = pairwise_distances(x).flatten()[:n * n-1]
        y_dist = pairwise_distances(y).flatten()[:n * n-1]
        if bins == None:
            bins = np.sqrt(n / 5)
        hist = np.histogram2d(x_dist, y_dist, bins)[0]
        mi = mutual_info_score(np.zeros(n), np.zeros(n), contingency=hist)
    else:
        x_clust = k_means(x, k)[1]
        y_clust = k_means(x, k)[1]
        mi = mutual_info_score(x_clust, y_clust)
    return mi


def rnaseq_inverse_transform(data, x=None):
    if x == None:
        x = np.concatenate([data._data, data._test_data])
    if '_min' in data.__dict__ and '_max' in data.__dict__:
        x = x * (data._max - data._min) + data._min
    if '_components' in data.__dict__:
        x = np.dot(x, data._components) + data._mean_offset
    return data
