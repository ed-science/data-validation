# Copyright 2018 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Module that computes the top-k and uniques statistics for string features."""

from __future__ import absolute_import
from __future__ import division

from __future__ import print_function

import collections
import six
from tensorflow_data_validation import types
from tensorflow_data_validation.arrow import arrow_util
from tensorflow_data_validation.pyarrow_tf import pyarrow as pa
from tensorflow_data_validation.statistics.generators import stats_generator
from tensorflow_data_validation.statistics.generators import top_k_uniques_stats_generator
from tensorflow_data_validation.utils import schema_util
from tensorflow_data_validation.utils import stats_util
from tensorflow_data_validation.types_compat import Any, Dict, Iterable, List, Optional, Set, Text
from tensorflow_metadata.proto.v0 import schema_pb2
from tensorflow_metadata.proto.v0 import statistics_pb2

# _ValueCounts holds two collections.Counter objects: one that holds the
# unweighted counts of each unique value for a given feature, and one that holds
# the weighted counts of each unique value for a given feature.
_ValueCounts = collections.namedtuple('_ValueCounts',
                                      ['unweighted_counts', 'weighted_counts'])


def _make_feature_stats_proto(
    feature_path,
    value_count_list,
    weighted_value_count_list, is_categorical,
    num_top_values, frequency_threshold,
    weighted_frequency_threshold,
    num_rank_histogram_buckets):
  """Makes a FeatureNameStatistics proto containing top-k and uniques stats."""
  # Create a FeatureNameStatistics proto that includes the unweighted top-k
  # stats.
  result = (
      top_k_uniques_stats_generator.make_feature_stats_proto_with_topk_stats(
          feature_path, value_count_list, is_categorical, False, num_top_values,
          frequency_threshold, num_rank_histogram_buckets))

  # If weights were provided, create another FeatureNameStatistics proto that
  # includes the weighted top-k stats, and then copy those weighted top-k stats
  # into the result proto.
  if weighted_value_count_list:
    weighted_result = (
        top_k_uniques_stats_generator.make_feature_stats_proto_with_topk_stats(
            feature_path, weighted_value_count_list, is_categorical, True,
            num_top_values, weighted_frequency_threshold,
            num_rank_histogram_buckets))

    result.string_stats.weighted_string_stats.CopyFrom(
        weighted_result.string_stats.weighted_string_stats)

  # Add the number of uniques to the FeatureNameStatistics proto.
  result.string_stats.unique = len(value_count_list)

  return result


def _make_dataset_feature_stats_proto_with_multiple_features(
    feature_names_to_value_counts,
    weighted_feature_names_to_value_counts,
    categorical_features, num_top_values,
    frequency_threshold, weighted_frequency_threshold,
    num_rank_histogram_buckets):
  """Makes a DatasetFeatureStatistics proto containing multiple features."""
  result = statistics_pb2.DatasetFeatureStatistics()
  for feature_path, value_count in six.iteritems(feature_names_to_value_counts):
    if weighted_feature_names_to_value_counts:
      weighted_value_count = weighted_feature_names_to_value_counts[
          feature_path]
    else:
      weighted_value_count = None
    result.features.add().CopyFrom(
        _make_feature_stats_proto(feature_path, value_count,
                                  weighted_value_count,
                                  feature_path in categorical_features,
                                  num_top_values, frequency_threshold,
                                  weighted_frequency_threshold,
                                  num_rank_histogram_buckets))
  return result


class _WeightedCounter(collections.defaultdict):
  """A counter that support weighted counts."""

  def __init__(self):
    super(_WeightedCounter, self).__init__(int)

  def weighted_update(self, values,
                      weights):
    """Updates the count of elements in `values` with weights."""

    for v, w in six.moves.zip(values, weights):
      self[v] += w

  def update(self, other):
    for k, v in six.iteritems(other):
      self[k] += v


class TopKUniquesCombinerStatsGenerator(
    stats_generator.CombinerStatsGenerator):
  """Combiner statistics generator that computes top-k and uniques stats.

  This generator is for in-memory data only. The TopKStatsGenerator and
  UniquesStatsGenerator are for distributed data.
  """

  def __init__(
      self,  # pylint: disable=useless-super-delegation
      name = 'TopKUniquesCombinerStatsGenerator',
      schema = None,
      weight_feature = None,
      num_top_values = 2,
      frequency_threshold = 1,
      weighted_frequency_threshold = 1.0,
      num_rank_histogram_buckets = 1000):
    """Initializes a top-k and uniques combiner statistics generator.

    Args:
      name: An optional unique name associated with the statistics generator.
      schema: An optional schema for the dataset.
      weight_feature: Feature name whose numeric value represents the weight of
        an example. None if there is no weight feature.
      num_top_values: The number of most frequent feature values to keep for
        string features.
      frequency_threshold: An optional minimum number of examples
        the most frequent values must be present in (defaults to 1).
      weighted_frequency_threshold: An optional minimum weighted
        number of examples the most frequent weighted values must be
        present in (defaults to 1.0).
      num_rank_histogram_buckets: The number of buckets in the rank histogram
        for string features.
    """
    super(TopKUniquesCombinerStatsGenerator, self).__init__(name, schema)
    self._categorical_features = set(
        schema_util.get_categorical_numeric_features(schema) if schema else [])
    self._weight_feature = weight_feature
    self._num_top_values = num_top_values
    self._frequency_threshold = frequency_threshold
    self._weighted_frequency_threshold = weighted_frequency_threshold
    self._num_rank_histogram_buckets = num_rank_histogram_buckets

  def create_accumulator(self):
    return {}

  # Implementation note:
  # The current implementation loops over the flattened value ndarrays and
  # weight ndarrays which is slow. We could have used np.unique() (or similar
  # vectorized approach) but np.unique() involves a sort(), which is slow if
  # dtype==np.object (for strings, which is our most common use case).
  # However pandas allows us to group-by-and-count-and-sum without sorting.
  # So one way to improve the performance would be, for each feature, to
  # construct an Arrow Table that consists of flattend values and weights
  # (zero copy here), then table.to_pandas() (one copy here, but unavoidable
  # anyways), then use something like DataFrame.groupby().agg(['sum', 'count']).
  # The accumulator could simply be a DataFrame for each feature, and make use
  # of DataFrame merging provided by pandas.
  def add_input(self, accumulator,
                input_table):
    weight_column = (input_table.column(self._weight_feature)
                     if self._weight_feature else None)
    weight_array = weight_column.data.chunk(0) if weight_column else []
    if weight_array:
      flattened_weights = arrow_util.FlattenListArray(weight_array).to_numpy()

    for column in input_table.columns:
      feature_name = column.name
      # Skip the weight feature.
      if feature_name == self._weight_feature:
        continue
      feature_path = types.FeaturePath([feature_name])
      feature_type = stats_util.get_feature_type_from_arrow_type(
          feature_path, column.type)
      # if it's not a categorical feature nor a string feature, we don't bother
      # with topk stats.
      if not (feature_path in self._categorical_features or
              feature_type == statistics_pb2.FeatureNameStatistics.STRING):
        continue
      value_array = column.data.chunk(0)
      flattened_values = arrow_util.FlattenListArray(value_array)
      unweighted_counts = collections.Counter()
      # Compute unweighted counts.
      value_counts = arrow_util.ValueCounts(flattened_values)
      for value_count in value_counts:
        value_count = value_count.as_py()
        unweighted_counts[value_count['values']] = value_count['counts']

      # Compute weighted counts if a weight feature is specified.
      weighted_counts = _WeightedCounter()
      if weight_array:
        if (pa.types.is_binary(flattened_values.type) or
            pa.types.is_string(flattened_values.type)):
          # no free conversion.
          flattened_values_np = flattened_values.to_pandas()
        else:
          flattened_values_np = flattened_values.to_numpy()
        indices = arrow_util.GetFlattenedArrayParentIndices(value_array)
        weighted_counts.weighted_update(flattened_values_np,
                                        flattened_weights[indices.to_numpy()])

      if feature_path not in accumulator:
        accumulator[feature_path] = _ValueCounts(
            unweighted_counts=unweighted_counts,
            weighted_counts=weighted_counts)
      else:
        accumulator[feature_path].unweighted_counts.update(unweighted_counts)
        accumulator[feature_path].weighted_counts.update(weighted_counts)
    return accumulator

  def merge_accumulators(
      self, accumulators
  ):
    result = {}
    for accumulator in accumulators:
      for feature_path, value_counts in six.iteritems(accumulator):
        if feature_path not in result:
          result[feature_path] = value_counts
        else:
          result[feature_path].unweighted_counts.update(
              value_counts.unweighted_counts)
          if result[feature_path].weighted_counts:
            result[feature_path].weighted_counts.update(
                value_counts.weighted_counts)
    return result

  def extract_output(self, accumulator
                    ):
    feature_paths_to_value_counts = dict()
    feature_paths_to_weighted_value_counts = dict()

    for feature_path, value_counts in accumulator.items():
      if value_counts.unweighted_counts:
        feature_value_counts = [
            top_k_uniques_stats_generator.FeatureValueCount(key, value)
            for key, value in value_counts.unweighted_counts.items()
        ]
        feature_paths_to_value_counts[feature_path] = feature_value_counts
      if value_counts.weighted_counts:
        weighted_feature_value_counts = [
            top_k_uniques_stats_generator.FeatureValueCount(key, value)
            for key, value in value_counts.weighted_counts.items()
        ]
        feature_paths_to_weighted_value_counts[
            feature_path] = weighted_feature_value_counts

    return _make_dataset_feature_stats_proto_with_multiple_features(
        feature_paths_to_value_counts, feature_paths_to_weighted_value_counts,
        self._categorical_features, self._num_top_values,
        self._frequency_threshold, self._weighted_frequency_threshold,
        self._num_rank_histogram_buckets)
