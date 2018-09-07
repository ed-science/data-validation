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
# ==============================================================================

"""Tests for Validation API."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from absl.testing import absltest
from tensorflow_data_validation.api import validation_api
from google.protobuf import text_format
from tensorflow_metadata.proto.v0 import anomalies_pb2
from tensorflow_metadata.proto.v0 import schema_pb2
from tensorflow_metadata.proto.v0 import statistics_pb2


class ValidationApiTest(absltest.TestCase):

  def test_infer_schema(self):
    statistics = text_format.Parse(
        """
        datasets {
          num_examples: 7
          features: {
            name: 'feature1'
            type: STRING
            string_stats: {
              common_stats: {
                num_non_missing: 7
                min_num_values: 1
                max_num_values: 1
              }
              unique: 3
            }
          }
        }
        """, statistics_pb2.DatasetFeatureStatisticsList())

    expected_schema = text_format.Parse(
        """
        feature {
          name: "feature1"
          value_count: {
            min: 1
            max: 1
          }
          presence: {
            min_fraction: 1.0
            min_count: 1
          }
          type: BYTES
        }
        """, schema_pb2.Schema())

    # Infer the schema from the stats.
    actual_schema = validation_api.infer_schema(statistics,
                                                infer_feature_shape=False)
    self.assertEqual(actual_schema, expected_schema)

  def test_infer_schema_with_string_domain(self):
    statistics = text_format.Parse(
        """
        datasets {
          num_examples: 7
          features: {
            name: 'feature1'
            type: STRING
            string_stats: {
              common_stats: {
                num_missing: 3
                num_non_missing: 4
                min_num_values: 1
                max_num_values: 1
              }
              unique: 3
              rank_histogram {
                buckets {
                  low_rank: 0
                  high_rank: 0
                  label: "a"
                  sample_count: 2.0
                }
                buckets {
                  low_rank: 1
                  high_rank: 1
                  label: "b"
                  sample_count: 1.0
                }
                buckets {
                  low_rank: 2
                  high_rank: 2
                  label: "c"
                  sample_count: 1.0
                }
              }
            }
          }
        }
        """, statistics_pb2.DatasetFeatureStatisticsList())

    expected_schema = text_format.Parse(
        """
        feature {
          name: "feature1"
          value_count: {
            min: 1
            max: 1
          }
          presence: {
            min_count: 1
          }
          type: BYTES
          domain: "feature1"
        }
        string_domain {
          name: "feature1"
          value: "a"
          value: "b"
          value: "c"
        }
        """, schema_pb2.Schema())

    # Infer the schema from the stats.
    actual_schema = validation_api.infer_schema(statistics)
    self.assertEqual(actual_schema, expected_schema)

  def test_infer_schema_without_string_domain(self):
    statistics = text_format.Parse(
        """
        datasets {
          num_examples: 7
          features: {
            name: 'feature1'
            type: STRING
            string_stats: {
              common_stats: {
                num_missing: 3
                num_non_missing: 4
                min_num_values: 1
                max_num_values: 1
              }
              unique: 3
              rank_histogram {
                buckets {
                  low_rank: 0
                  high_rank: 0
                  label: "a"
                  sample_count: 2.0
                }
                buckets {
                  low_rank: 1
                  high_rank: 1
                  label: "b"
                  sample_count: 1.0
                }
                buckets {
                  low_rank: 2
                  high_rank: 2
                  label: "c"
                  sample_count: 1.0
                }
              }
            }
          }
        }
        """, statistics_pb2.DatasetFeatureStatisticsList())

    expected_schema = text_format.Parse(
        """
        feature {
          name: "feature1"
          value_count: {
            min: 1
            max: 1
          }
          presence: {
            min_count: 1
          }
          type: BYTES
        }
        """, schema_pb2.Schema())

    # Infer the schema from the stats.
    actual_schema = validation_api.infer_schema(statistics,
                                                max_string_domain_size=2)
    self.assertEqual(actual_schema, expected_schema)

  def test_infer_schema_with_infer_shape(self):
    statistics = text_format.Parse(
        """
        datasets {
          num_examples: 7
          features: {
            name: 'feature1'
            type: STRING
            string_stats: {
              common_stats: {
                num_missing: 0
                num_non_missing: 7
                min_num_values: 1
                max_num_values: 1
              }
              unique: 3
            }
          }
          features: {
            name: 'feature2'
            type: STRING
            string_stats: {
              common_stats: {
                num_missing: 0
                num_non_missing: 7
                min_num_values: 3
                max_num_values: 3
              }
              unique: 5
            }
          }
        }
        """, statistics_pb2.DatasetFeatureStatisticsList())

    expected_schema = text_format.Parse(
        """
        feature {
          name: "feature1"
          shape { dim { size: 1 } }
          presence: {
            min_fraction: 1.0
            min_count: 1
          }
          type: BYTES
        }
        feature {
          name: "feature2"
          value_count: { min: 1 }
          presence: {
            min_fraction: 1.0
            min_count: 1
          }
          type: BYTES
        }
        """, schema_pb2.Schema())

    # Infer the schema from the stats.
    actual_schema = validation_api.infer_schema(statistics,
                                                infer_feature_shape=True)
    self.assertEqual(actual_schema, expected_schema)

  def test_infer_schema_invalid_statistics_input(self):
    with self.assertRaisesRegexp(
        TypeError, '.*should be a DatasetFeatureStatisticsList proto.*'):
      _ = validation_api.infer_schema({})

  def test_infer_schema_invalid_multiple_datasets(self):
    statistics = statistics_pb2.DatasetFeatureStatisticsList()
    statistics.datasets.extend([
        statistics_pb2.DatasetFeatureStatistics(),
        statistics_pb2.DatasetFeatureStatistics()
    ])
    with self.assertRaisesRegexp(ValueError,
                                 '.*statistics proto with one dataset.*'):
      _ = validation_api.infer_schema(statistics)

  def _assert_equal_anomalies(self, actual_anomalies, expected_anomalies):
    # Check if the actual anomalies matches with the expected anomalies.
    # Doesn't compare the diff_regions.
    for feature_name in expected_anomalies:
      self.assertIn(feature_name, actual_anomalies.anomaly_info)
      self.assertEqual(actual_anomalies.anomaly_info[feature_name],
                       expected_anomalies[feature_name])
    self.assertEqual(
        len(actual_anomalies.anomaly_info), len(expected_anomalies))

  def test_validate_stats(self):
    schema = text_format.Parse(
        """
        string_domain {
          name: "MyAloneEnum"
          value: "A"
          value: "B"
          value: "C"
        }
        feature {
          name: "annotated_enum"
          value_count {
            min:1
            max:1
          }
          presence {
            min_count: 1
          }
          type: BYTES
          domain: "MyAloneEnum"
        }
        feature {
          name: "ignore_this"
          lifecycle_stage: DEPRECATED
          value_count {
            min:1
          }
          presence {
            min_count: 1
          }
          type: BYTES
        }
        """, schema_pb2.Schema())
    statistics = text_format.Parse(
        """
        datasets{
          num_examples: 10
          features {
            name: 'annotated_enum'
            type: STRING
            string_stats {
              common_stats {
                num_missing: 3
                num_non_missing: 7
                min_num_values: 1
                max_num_values: 1
              }
              unique: 3
              rank_histogram {
                buckets {
                  label: "D"
                  sample_count: 1
                }
              }
            }
          }
        }
        """, statistics_pb2.DatasetFeatureStatisticsList())
    expected_anomalies = {
        'annotated_enum':
            text_format.Parse(
                """
      description: "Examples contain values missing from the schema: D (?). "
      severity: ERROR
      short_description: "Unexpected string values"
      reason {
        type: ENUM_TYPE_UNEXPECTED_STRING_VALUES
        short_description: "Unexpected string values"
        description: "Examples contain values missing from the schema: D (?). "
      }
            """, anomalies_pb2.AnomalyInfo())
    }

    # Validate the stats.
    anomalies = validation_api.validate_statistics(statistics, schema)
    self._assert_equal_anomalies(anomalies, expected_anomalies)

  # pylint: disable=line-too-long
  _annotated_enum_anomaly_info = """
            description: "Examples contain values missing from the schema: b (?).  The Linfty distance between current and previous is 0.25 (up to six significant digits), above the threshold 0.01. The feature value with maximum difference is: b"
            severity: ERROR
            short_description: "Multiple errors"
            reason {
              type: ENUM_TYPE_UNEXPECTED_STRING_VALUES
              short_description: "Unexpected string values"
              description: "Examples contain values missing from the schema: b (?). "
            }
            reason {
              type: COMPARATOR_L_INFTY_HIGH
              short_description: "High Linfty distance between current and previous"
              description: "The Linfty distance between current and previous is 0.25 (up to six significant digits), above the threshold 0.01. The feature value with maximum difference is: b"
            }"""

  _bar_anomaly_info = """
            short_description: "High Linfty distance between serving and training"
            description: "The Linfty distance between serving and training is 0.2 (up to six significant digits), above the threshold 0.1. The feature value with maximum difference is: a"
            severity: ERROR
            reason {
              type: COMPARATOR_L_INFTY_HIGH
              short_description: "High Linfty distance between serving and training"
              description: "The Linfty distance between serving and training is 0.2 (up to six significant digits), above the threshold 0.1. The feature value with maximum difference is: a"
            }"""

  def test_validate_stats_with_previous_stats(self):
    statistics = text_format.Parse(
        """
        datasets {
          num_examples: 2
          features {
            name: 'annotated_enum'
            type: STRING
            string_stats {
              common_stats { num_non_missing: 2 num_missing: 0 max_num_values: 1 }
              rank_histogram {
                buckets { label: "a" sample_count: 1 }
                buckets { label: "b" sample_count: 1 }
              }
            }
          }
        }""", statistics_pb2.DatasetFeatureStatisticsList())

    previous_statistics = text_format.Parse(
        """
        datasets {
          num_examples: 4
          features {
            name: 'annotated_enum'
            type: STRING
            string_stats {
              common_stats { num_non_missing: 4 num_missing: 0 max_num_values: 1 }
              rank_histogram {
                buckets { label: "a" sample_count: 3 }
                buckets { label: "b" sample_count: 1 }
              }
            }
          }
        }""", statistics_pb2.DatasetFeatureStatisticsList())

    schema = text_format.Parse(
        """
        feature {
          name: "annotated_enum"
          type: BYTES
          domain: "annotated_enum"
          drift_comparator { infinity_norm { threshold: 0.01 } }
        }
        string_domain { name: "annotated_enum" value: "a" }
        """, schema_pb2.Schema())

    expected_anomalies = {
        'annotated_enum': text_format.Parse(self._annotated_enum_anomaly_info,
                                            anomalies_pb2.AnomalyInfo())
    }
    # Validate the stats.
    anomalies = validation_api.validate_statistics(
        statistics, schema, previous_statistics=previous_statistics)
    self._assert_equal_anomalies(anomalies, expected_anomalies)

  def test_validate_stats_with_serving_stats(self):
    statistics = text_format.Parse(
        """
        datasets {
          num_examples: 10
          features {
            name: 'bar'
            type: STRING
            string_stats {
              common_stats {
                num_missing: 0
                num_non_missing: 10
                max_num_values: 1
              }
              rank_histogram {
                buckets { label: "a" sample_count: 1 }
                buckets { label: "b" sample_count: 2 }
                buckets { label: "c" sample_count: 7 }
              }
            }
          }
        }""", statistics_pb2.DatasetFeatureStatisticsList())

    serving_statistics = text_format.Parse(
        """
        datasets {
          num_examples: 10
          features {
            name: 'bar'
            type: STRING
            string_stats {
              common_stats {
                num_missing: 0
                num_non_missing: 10
                max_num_values: 1
              }
              rank_histogram {
                buckets { label: "a" sample_count: 3 }
                buckets { label: "b" sample_count: 1 }
                buckets { label: "c" sample_count: 6 }
              }
            }
          }
        }""", statistics_pb2.DatasetFeatureStatisticsList())

    schema = text_format.Parse(
        """
        feature {
          name: 'bar'
          type: BYTES
          skew_comparator {
            infinity_norm { threshold: 0.1}
          }
        }""", schema_pb2.Schema())

    expected_anomalies = {
        'bar': text_format.Parse(self._bar_anomaly_info,
                                 anomalies_pb2.AnomalyInfo())
    }
    # Validate the stats.
    anomalies = validation_api.validate_statistics(
        statistics, schema, serving_statistics=serving_statistics)
    self._assert_equal_anomalies(anomalies, expected_anomalies)

  def test_validate_stats_with_environment(self):
    statistics = text_format.Parse(
        """
        datasets {
          num_examples: 1000
          features {
            name: 'feature'
            type: STRING
            string_stats {
              common_stats {
                num_non_missing: 1000
                min_num_values: 1
                max_num_values: 1
              }
              unique: 3
            }
          }
        }""", statistics_pb2.DatasetFeatureStatisticsList())

    schema = text_format.Parse(
        """
        default_environment: "TRAINING"
        default_environment: "SERVING"
        feature {
          name: "label"
          not_in_environment: "SERVING"
          value_count { min: 1 max: 1 }
          presence { min_count: 1 }
          type: BYTES
        }
        feature {
          name: "feature"
          value_count { min: 1 max: 1 }
          presence { min_count: 1 }
          type: BYTES
        }
        """, schema_pb2.Schema())

    expected_anomalies_training = {
        'label':
            text_format.Parse(
                """
            description: "Column is completely missing"
            severity: ERROR
            short_description: "Column dropped"
            reason {
              type: SCHEMA_MISSING_COLUMN
              short_description: "Column dropped"
              description: "Column is completely missing"
            }
            """, anomalies_pb2.AnomalyInfo())
    }
    # Validate the stats in TRAINING environment.
    anomalies_training = validation_api.validate_statistics(
        statistics, schema, environment='TRAINING')
    self._assert_equal_anomalies(anomalies_training,
                                 expected_anomalies_training)

    # Validate the stats in SERVING environment.
    anomalies_serving = validation_api.validate_statistics(
        statistics, schema, environment='SERVING')
    self._assert_equal_anomalies(anomalies_serving, {})

  def test_validate_stats_with_previous_and_serving_stats(self):
    statistics = text_format.Parse(
        """
        datasets {
          num_examples: 10
          features {
            name: 'bar'
            type: STRING
            string_stats {
              common_stats {
                num_missing: 0
                num_non_missing: 10
                max_num_values: 1
              }
              rank_histogram {
                buckets { label: "a" sample_count: 1 }
                buckets { label: "b" sample_count: 2 }
                buckets { label: "c" sample_count: 7 }
              }
            }
          }
          features {
            name: 'annotated_enum'
            type: STRING
            string_stats {
              common_stats {
                num_missing: 0
                num_non_missing: 10
                max_num_values: 1
              }
              rank_histogram {
                buckets { label: "a" sample_count: 1 }
                buckets { label: "b" sample_count: 1 }
              }
            }
          }
        }""", statistics_pb2.DatasetFeatureStatisticsList())

    previous_statistics = text_format.Parse(
        """
        datasets {
          num_examples: 10
          features {
            name: 'annotated_enum'
            type: STRING
            string_stats {
              common_stats {
                num_non_missing: 10
                num_missing: 0
                max_num_values: 1
              }
              rank_histogram {
                buckets { label: "a" sample_count: 3 }
                buckets { label: "b" sample_count: 1 }
              }
            }
          }
          features {
            name: 'bar'
            type: STRING
            string_stats {
              common_stats {
                num_missing: 0
                num_non_missing: 10
                max_num_values: 1
              }
              rank_histogram {
                buckets { label: "a" sample_count: 3 }
                buckets { label: "b" sample_count: 1 }
                buckets { label: "c" sample_count: 6 }
              }
            }
          }
        }""", statistics_pb2.DatasetFeatureStatisticsList())

    serving_statistics = text_format.Parse(
        """
        datasets {
          num_examples: 10
          features {
            name: 'bar'
            type: STRING
            string_stats {
              common_stats {
                num_missing: 0
                num_non_missing: 10
                max_num_values: 1
              }
              rank_histogram {
                buckets { label: "a" sample_count: 3 }
                buckets { label: "b" sample_count: 1 }
                buckets { label: "c" sample_count: 6 }
              }
            }
          }
          features {
            name: 'annotated_enum'
            type: STRING
            string_stats {
              common_stats {
                num_non_missing: 10
                num_missing: 0
                max_num_values: 1
              }
              rank_histogram {
                buckets { label: "a" sample_count: 3 }
                buckets { label: "b" sample_count: 1 }
              }
            }
          }
        }""", statistics_pb2.DatasetFeatureStatisticsList())

    schema = text_format.Parse(
        """
        feature {
          name: 'bar'
          type: BYTES
          skew_comparator { infinity_norm { threshold: 0.1 } }
        }
        feature {
          name: "annotated_enum"
          type: BYTES
          domain: "annotated_enum"
          drift_comparator { infinity_norm { threshold: 0.01 } }
        }
        string_domain { name: "annotated_enum" value: "a" }
        """, schema_pb2.Schema())

    expected_anomalies = {
        'bar': text_format.Parse(self._bar_anomaly_info,
                                 anomalies_pb2.AnomalyInfo()),
        'annotated_enum': text_format.Parse(self._annotated_enum_anomaly_info,
                                            anomalies_pb2.AnomalyInfo())
    }

    # Validate the stats.
    anomalies = validation_api.validate_statistics(
        statistics,
        schema,
        previous_statistics=previous_statistics,
        serving_statistics=serving_statistics)
    self._assert_equal_anomalies(anomalies, expected_anomalies)
  # pylint: enable=line-too-long

  def test_validate_stats_invalid_statistics_input(self):
    schema = schema_pb2.Schema()
    with self.assertRaisesRegexp(
        TypeError, 'statistics is of type.*'):
      _ = validation_api.validate_statistics({}, schema)

  def test_validate_stats_invalid_previous_statistics_input(self):
    statistics = statistics_pb2.DatasetFeatureStatisticsList()
    statistics.datasets.extend([statistics_pb2.DatasetFeatureStatistics()])
    schema = schema_pb2.Schema()
    with self.assertRaisesRegexp(
        TypeError, 'previous_statistics is of type.*'):
      _ = validation_api.validate_statistics(statistics, schema,
                                             previous_statistics='test')

  def test_validate_stats_invalid_serving_statistics_input(self):
    statistics = statistics_pb2.DatasetFeatureStatisticsList()
    statistics.datasets.extend([statistics_pb2.DatasetFeatureStatistics()])
    schema = schema_pb2.Schema()
    with self.assertRaisesRegexp(
        TypeError, 'serving_statistics is of type.*'):
      _ = validation_api.validate_statistics(statistics, schema,
                                             serving_statistics='test')

  def test_validate_stats_invalid_schema_input(self):
    statistics = statistics_pb2.DatasetFeatureStatisticsList()
    statistics.datasets.extend([statistics_pb2.DatasetFeatureStatistics()])
    with self.assertRaisesRegexp(TypeError, '.*should be a Schema proto.*'):
      _ = validation_api.validate_statistics(statistics, {})

  def test_validate_stats_invalid_environment(self):
    statistics = statistics_pb2.DatasetFeatureStatisticsList()
    statistics.datasets.extend([statistics_pb2.DatasetFeatureStatistics()])
    schema = text_format.Parse(
        """
        default_environment: "TRAINING"
        default_environment: "SERVING"
        feature {
          name: "label"
          not_in_environment: "SERVING"
          value_count { min: 1 max: 1 }
          presence { min_count: 1 }
          type: BYTES
        }
        """, schema_pb2.Schema())
    with self.assertRaisesRegexp(
        ValueError, 'Environment.*not found in the schema.*'):
      _ = validation_api.validate_statistics(statistics, schema,
                                             environment='INVALID')

  def test_validate_stats_invalid_statistics_multiple_datasets(self):
    statistics = statistics_pb2.DatasetFeatureStatisticsList()
    statistics.datasets.extend([
        statistics_pb2.DatasetFeatureStatistics(),
        statistics_pb2.DatasetFeatureStatistics()
    ])
    schema = schema_pb2.Schema()
    with self.assertRaisesRegexp(
        ValueError, 'statistics proto contains multiple datasets.*'):
      _ = validation_api.validate_statistics(statistics, schema)

  def test_validate_stats_invalid_previous_statistics_multiple_datasets(self):
    current_stats = statistics_pb2.DatasetFeatureStatisticsList()
    current_stats.datasets.extend([
        statistics_pb2.DatasetFeatureStatistics()
    ])
    previous_stats = statistics_pb2.DatasetFeatureStatisticsList()
    previous_stats.datasets.extend([
        statistics_pb2.DatasetFeatureStatistics(),
        statistics_pb2.DatasetFeatureStatistics()
    ])
    schema = schema_pb2.Schema()
    with self.assertRaisesRegexp(
        ValueError, 'previous_statistics proto contains multiple datasets.*'):
      _ = validation_api.validate_statistics(current_stats, schema,
                                             previous_statistics=previous_stats)

  def test_validate_stats_invalid_serving_statistics_multiple_datasets(self):
    current_stats = statistics_pb2.DatasetFeatureStatisticsList()
    current_stats.datasets.extend([
        statistics_pb2.DatasetFeatureStatistics()
    ])
    serving_stats = statistics_pb2.DatasetFeatureStatisticsList()
    serving_stats.datasets.extend([
        statistics_pb2.DatasetFeatureStatistics(),
        statistics_pb2.DatasetFeatureStatistics()
    ])
    schema = schema_pb2.Schema()
    with self.assertRaisesRegexp(
        ValueError, 'serving_statistics proto contains multiple datasets.*'):
      _ = validation_api.validate_statistics(current_stats, schema,
                                             serving_statistics=serving_stats)

if __name__ == '__main__':
  absltest.main()
