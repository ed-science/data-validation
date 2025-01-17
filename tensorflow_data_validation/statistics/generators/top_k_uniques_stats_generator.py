# Copyright 2019 Google LLC
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

"""Computes top-k most frequent values and number of unique values.

This generator computes these values for string and categorical features.
"""

from __future__ import absolute_import
from __future__ import division

from __future__ import print_function

import collections
import logging
import apache_beam as beam
import numpy as np
import pandas as pd
import six
from tensorflow_data_validation import types
from tensorflow_data_validation.arrow import arrow_util
from tensorflow_data_validation.pyarrow_tf import pyarrow as pa
from tensorflow_data_validation.statistics.generators import stats_generator
from tensorflow_data_validation.utils import schema_util
from tensorflow_data_validation.utils.stats_util import maybe_get_utf8
from tensorflow_data_validation.types_compat import Any, Iterable, Iterator, FrozenSet, List, Optional, Set, Text, Tuple, Union
from tensorflow_metadata.proto.v0 import schema_pb2
from tensorflow_metadata.proto.v0 import statistics_pb2

FeatureValueCount = collections.namedtuple('FeatureValueCount',
                                           ['feature_value', 'count'])

# Pickling types.FeaturePath is slow, so we use tuples directly where pickling
# happens frequently.
FeaturePathTuple = Tuple[types.FeatureName]

_INVALID_STRING = '__BYTES_VALUE__'


def _make_feature_stats_proto_with_uniques_stats(
    feature_path, count,
    is_categorical):
  """Makes a FeatureNameStatistics proto containing the uniques stats."""
  result = statistics_pb2.FeatureNameStatistics()
  result.path.CopyFrom(feature_path.to_proto())
  # If we have a categorical feature, we preserve the type to be the original
  # INT type.
  result.type = (
      statistics_pb2.FeatureNameStatistics.INT
      if is_categorical else statistics_pb2.FeatureNameStatistics.STRING)
  result.string_stats.unique = count
  return result


def _make_dataset_feature_stats_proto_with_uniques_for_single_feature(
    feature_path_to_value_count,
    categorical_features
):
  """Makes a DatasetFeatureStatistics proto with uniques stats for a feature."""
  (slice_key, feature_path_tuple), count = feature_path_to_value_count
  feature_path = types.FeaturePath(feature_path_tuple)
  result = statistics_pb2.DatasetFeatureStatistics()
  result.features.add().CopyFrom(
      _make_feature_stats_proto_with_uniques_stats(
          feature_path, count, feature_path in categorical_features))
  return slice_key, result.SerializeToString()


def make_feature_stats_proto_with_topk_stats(
    feature_path,
    top_k_value_count_list, is_categorical,
    is_weighted_stats, num_top_values,
    frequency_threshold,
    num_rank_histogram_buckets):
  """Makes a FeatureNameStatistics proto containing the top-k stats.

  Args:
    feature_path: The path of the feature.
    top_k_value_count_list: A list of FeatureValueCount tuples.
    is_categorical: Whether the feature is categorical.
    is_weighted_stats: Whether top_k_value_count_list incorporates weights.
    num_top_values: The number of most frequent feature values to keep for
      string features.
    frequency_threshold: The minimum number of examples (possibly weighted) the
      most frequent values must be present in.
    num_rank_histogram_buckets: The number of buckets in the rank histogram for
      string features.

  Returns:
    A FeatureNameStatistics proto containing the top-k stats.
  """
  # Sort the top_k_value_count_list in descending order by count. Where
  # multiple feature values have the same count, consider the feature with the
  # 'larger' feature value to be larger for purposes of breaking the tie.
  top_k_value_count_list.sort(
      key=lambda counts: (counts[1], counts[0]),
      reverse=True)

  result = statistics_pb2.FeatureNameStatistics()
  result.path.CopyFrom(feature_path.to_proto())
  # If we have a categorical feature, we preserve the type to be the original
  # INT type.
  result.type = (statistics_pb2.FeatureNameStatistics.INT if is_categorical
                 else statistics_pb2.FeatureNameStatistics.STRING)

  if is_weighted_stats:
    string_stats = result.string_stats.weighted_string_stats
  else:
    string_stats = result.string_stats

  for i in range(len(top_k_value_count_list)):
    value, count = top_k_value_count_list[i]
    if count < frequency_threshold:
      break
    # Check if we have a valid utf-8 string. If not, assign a default invalid
    # string value.
    if isinstance(value, six.binary_type):
      value = maybe_get_utf8(value)
      if value is None:
        logging.warning('Feature "%s" has bytes value "%s" which cannot be '
                        'decoded as a UTF-8 string.', feature_path, value)
        value = _INVALID_STRING
    elif not isinstance(value, six.text_type):
      value = str(value)

    if i < num_top_values:
      freq_and_value = string_stats.top_values.add()
      freq_and_value.value = value
      freq_and_value.frequency = count
    if i < num_rank_histogram_buckets:
      bucket = string_stats.rank_histogram.buckets.add()
      bucket.low_rank = i
      bucket.high_rank = i
      bucket.sample_count = count
      bucket.label = value
  return result


def _make_dataset_feature_stats_proto_with_topk_for_single_feature(
    feature_path_to_value_count_list,
    categorical_features, is_weighted_stats,
    num_top_values, frequency_threshold,
    num_rank_histogram_buckets):
  """Makes a DatasetFeatureStatistics proto with top-k stats for a feature."""
  (slice_key, feature_path_tuple), value_count_list = (
      feature_path_to_value_count_list)
  feature_path = types.FeaturePath(feature_path_tuple)
  result = statistics_pb2.DatasetFeatureStatistics()
  result.features.add().CopyFrom(
      make_feature_stats_proto_with_topk_stats(
          feature_path, value_count_list, feature_path in categorical_features,
          is_weighted_stats, num_top_values, frequency_threshold,
          num_rank_histogram_buckets))
  return slice_key, result.SerializeToString()


def _weighted_unique(values, weights
                    ):
  """Computes weighted uniques.

  Args:
    values: 1-D array.
    weights: 1-D numeric array. Should have the same size as `values`.
  Returns:
    An iterator of tuples (unique_value, count, sum_weight).

  Implementation note: we use Pandas and pay the cost of copying the
  input numpy arrays into a DataFrame because Pandas can perform group-by
  without sorting. A numpy-only implementation with sorting is possible but
  slower because of the calls to the string comparator.
  """
  df = pd.DataFrame({
      'value': values,
      'count': np.ones_like(values, dtype=np.int32),
      'weight': weights,
  })
  gb = df.groupby(
      'value', as_index=False, sort=False)['count', 'weight'].sum()
  return six.moves.zip(
      gb['value'].tolist(), gb['count'].tolist(), gb['weight'].tolist())


def _to_topk_tuples(
    sliced_table,
    categorical_features,
    weight_feature = None
):
  """Generates tuples for computing top-k and uniques from input tables."""
  slice_key, table = sliced_table
  weight_column = table.column(weight_feature) if weight_feature else None
  weight_array = weight_column.data.chunk(0) if weight_column else []
  if weight_array:
    flattened_weights = arrow_util.FlattenListArray(weight_array).to_numpy()

  for feature_column in table.columns:
    feature_name = feature_column.name
    # Skip the weight feature.
    if feature_name == weight_feature:
      continue
    feature_path = types.FeaturePath([feature_name])
    # if it's not a categorical feature nor a string feature, we don't bother
    # with topk stats.
    if not (feature_path in categorical_features or
            feature_column.type.equals(pa.list_(pa.binary())) or
            feature_column.type.equals(pa.list_(pa.string()))):
      continue
    value_array = feature_column.data.chunk(0)
    flattened_values = arrow_util.FlattenListArray(value_array)

    if weight_array and flattened_values:
      if (pa.types.is_binary(flattened_values.type) or
          pa.types.is_string(flattened_values.type)):
        # no free conversion.
        flattened_values_np = flattened_values.to_pandas()
      else:
        flattened_values_np = flattened_values.to_numpy()
      indices = arrow_util.GetFlattenedArrayParentIndices(value_array)
      weights_ndarray = flattened_weights[indices.to_numpy()]
      for value, count, weight in _weighted_unique(
          flattened_values_np, weights_ndarray):
        yield (slice_key, feature_path.steps(), value), (count, weight)
    else:
      value_counts = arrow_util.ValueCounts(flattened_values)
      values = value_counts.field('values').to_pylist()
      counts = value_counts.field('counts').to_pylist()
      for value, count in six.moves.zip(values, counts):
        yield ((slice_key, feature_path.steps(), value), count)


class _ComputeTopKUniquesStats(beam.PTransform):
  """A ptransform that computes top-k and uniques for string features."""

  def __init__(self, schema,
               weight_feature, num_top_values,
               frequency_threshold, weighted_frequency_threshold,
               num_rank_histogram_buckets):
    """Initializes _ComputeTopKUniquesStats.

    Args:
      schema: An schema for the dataset. None if no schema is available.
      weight_feature: Feature name whose numeric value represents the weight
          of an example. None if there is no weight feature.
      num_top_values: The number of most frequent feature values to keep for
          string features.
      frequency_threshold: The minimum number of examples the most frequent
          values must be present in.
      weighted_frequency_threshold: The minimum weighted number of examples the
          most frequent weighted values must be present in.
      num_rank_histogram_buckets: The number of buckets in the rank histogram
          for string features.
    """
    self._categorical_features = set(
        schema_util.get_categorical_numeric_features(schema) if schema else [])
    self._weight_feature = weight_feature
    self._num_top_values = num_top_values
    self._frequency_threshold = frequency_threshold
    self._weighted_frequency_threshold = weighted_frequency_threshold
    self._num_rank_histogram_buckets = num_rank_histogram_buckets

  def expand(self, pcoll):

    def _sum_pairwise(
        iter_of_pairs
    ):
      """Computes sum of counts and weights."""
      # We take advantage of the fact that constructing a np array from a list
      # is much faster as the length is known beforehand.
      if isinstance(iter_of_pairs, list):
        arr = np.array(
            iter_of_pairs, dtype=[('c', np.int64), ('w', np.float)])
      else:
        arr = np.fromiter(
            iter_of_pairs, dtype=[('c', np.int64), ('w', np.float)])
      return arr['c'].sum(), arr['w'].sum()

    if self._weight_feature is not None:
      sum_fn = _sum_pairwise
    else:
      # For non-weighted case, use sum combine fn over integers to allow Beam
      # to use Cython combiner.
      sum_fn = sum
    top_k_tuples_combined = (
        pcoll
        | 'ToTopKTuples' >> beam.FlatMap(
            _to_topk_tuples,
            categorical_features=self._categorical_features,
            weight_feature=self._weight_feature)
        | 'CombineCountsAndWeights' >> beam.CombinePerKey(sum_fn))

    top_k = top_k_tuples_combined
    if self._weight_feature is not None:
      top_k |= 'Unweighted_DropWeights' >> beam.Map(lambda x: (x[0], x[1][0]))
    # (slice_key, feature, v), c
    top_k |= (
        'Unweighted_Prepare' >>
        beam.Map(lambda x: ((x[0][0], x[0][1]), (x[0][2], x[1])))
        # (slice_key, feature), (v, c)
        | 'Unweighted_TopK' >> beam.combiners.Top().PerKey(
            max(self._num_top_values, self._num_rank_histogram_buckets),
            key=lambda x: (x[1], x[0]))
        | 'Unweighted_ToProto' >> beam.Map(
            _make_dataset_feature_stats_proto_with_topk_for_single_feature,
            categorical_features=self._categorical_features,
            is_weighted_stats=False,
            num_top_values=self._num_top_values,
            frequency_threshold=self._frequency_threshold,
            num_rank_histogram_buckets=self._num_rank_histogram_buckets))
    uniques = (
        top_k_tuples_combined
        | 'Uniques_DropValues' >> beam.Map(lambda x: (x[0][0], x[0][1]))
        | 'Uniques_CountPerFeatureName' >> beam.combiners.Count().PerElement()
        | 'Uniques_ConvertToSingleFeatureStats' >> beam.Map(
            _make_dataset_feature_stats_proto_with_uniques_for_single_feature,
            categorical_features=self._categorical_features))
    result_protos = [top_k, uniques]

    if self._weight_feature is not None:
      weighted_top_k = (
          top_k_tuples_combined
          | 'Weighted_DropCounts' >> beam.Map(lambda x: (x[0], x[1][1]))
          | 'Weighted_Prepare' >>
          # (slice_key, feature), (v, w)
          beam.Map(lambda x: ((x[0][0], x[0][1]), (x[0][2], x[1])))
          | 'Weighted_TopK' >> beam.combiners.Top().PerKey(
              max(self._num_top_values, self._num_rank_histogram_buckets),
              key=lambda x: (x[1], x[0]))
          | 'Weighted_ToProto' >> beam.Map(
              _make_dataset_feature_stats_proto_with_topk_for_single_feature,
              categorical_features=self._categorical_features,
              is_weighted_stats=True,
              num_top_values=self._num_top_values,
              frequency_threshold=self._weighted_frequency_threshold,
              num_rank_histogram_buckets=self._num_rank_histogram_buckets))
      result_protos.append(weighted_top_k)

    def _deserialize_sliced_feature_stats_proto(entry):
      feature_stats_proto = statistics_pb2.DatasetFeatureStatistics()
      feature_stats_proto.ParseFromString(entry[1])
      return entry[0], feature_stats_proto
    return (result_protos
            | 'FlattenTopKUniquesFeatureStatsProtos' >> beam.Flatten()
            # TODO(b/121152126): This deserialization stage is a workaround.
            # Remove this once it is no longer needed.
            | 'DeserializeTopKUniquesFeatureStatsProto' >>
            beam.Map(_deserialize_sliced_feature_stats_proto))


class TopKUniquesStatsGenerator(stats_generator.TransformStatsGenerator):
  """A transform statistics generator that computes top-k and uniques."""

  def __init__(self,
               name = 'TopKUniquesStatsGenerator',
               schema = None,
               weight_feature = None,
               num_top_values = 2,
               frequency_threshold = 1,
               weighted_frequency_threshold = 1.0,
               num_rank_histogram_buckets = 1000):
    """Initializes top-k and uniques stats generator.

    Args:
      name: An optional unique name associated with the statistics generator.
      schema: An optional schema for the dataset.
      weight_feature: An optional feature name whose numeric value
          (must be of type INT or FLOAT) represents the weight of an example.
      num_top_values: An optional number of most frequent feature values to keep
          for string features (defaults to 2).
      frequency_threshold: An optional minimum number of examples
        the most frequent values must be present in (defaults to 1).
      weighted_frequency_threshold: An optional minimum weighted
        number of examples the most frequent weighted values must be
        present in (defaults to 1.0).
      num_rank_histogram_buckets: An optional number of buckets in the rank
          histogram for string features (defaults to 1000).
    """
    super(TopKUniquesStatsGenerator, self).__init__(
        name,
        schema=schema,
        ptransform=_ComputeTopKUniquesStats(
            schema=schema,
            weight_feature=weight_feature,
            num_top_values=num_top_values,
            frequency_threshold=frequency_threshold,
            weighted_frequency_threshold=weighted_frequency_threshold,
            num_rank_histogram_buckets=num_rank_histogram_buckets))
