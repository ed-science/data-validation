/* Copyright 2019 Google LLC

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    https://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
==============================================================================*/
#include "tensorflow_data_validation/anomalies/dataset_constraints_util.h"

#include "tensorflow_data_validation/anomalies/internal_types.h"
#include "tensorflow_data_validation/anomalies/statistics_view.h"
#include "tensorflow_data_validation/anomalies/test_util.h"
#include "tensorflow_metadata/proto/v0/schema.pb.h"

namespace tensorflow {
namespace data_validation {
namespace {

using ::tensorflow::metadata::v0::DatasetConstraints;
using ::tensorflow::metadata::v0::DatasetFeatureStatistics;
using ::tensorflow::metadata::v0::NumericValueComparator;
using testing::EqualsProto;
using testing::ParseTextProtoOrDie;

TEST(DatasetConstraintsUtilTest, IdentifyComparatorTypeInDataset) {
  DatasetConstraints dataset_constraints =
      ParseTextProtoOrDie<DatasetConstraints>(R"(num_examples_drift_comparator {
                                                   min_fraction_threshold: 1.0,
                                                   max_fraction_threshold: 1.0
                                                 })");
  EXPECT_TRUE(DatasetConstraintsHasComparator(dataset_constraints,
                                              DatasetComparatorType::DRIFT));
  EXPECT_FALSE(DatasetConstraintsHasComparator(dataset_constraints,
                                               DatasetComparatorType::VERSION));
}

TEST(DatasetConstraintsUtilTest,
     GetNumExamplesComparatorReturnsExistingComparator) {
  DatasetConstraints dataset_constraints =
      ParseTextProtoOrDie<DatasetConstraints>(R"(num_examples_drift_comparator {
                                                   min_fraction_threshold: 1.0,
                                                   max_fraction_threshold: 1.0
                                                 })");
  NumericValueComparator* actual_comparator = GetNumExamplesComparator(
      &dataset_constraints, DatasetComparatorType::DRIFT);
  EXPECT_THAT(*actual_comparator,
              EqualsProto(dataset_constraints.num_examples_drift_comparator()));
}

TEST(DatasetConstraintsUtilTest,
     GetNumExamplesComparatorCreatesComparatorIfDoesNotExist) {
  DatasetConstraints dataset_constraints =
      ParseTextProtoOrDie<DatasetConstraints>(R"(num_examples_drift_comparator {
                                                   min_fraction_threshold: 1.0,
                                                   max_fraction_threshold: 1.0
                                                 })");
  NumericValueComparator* actual_comparator = GetNumExamplesComparator(
      &dataset_constraints, DatasetComparatorType::VERSION);
  NumericValueComparator empty_version_comparator =
      ParseTextProtoOrDie<NumericValueComparator>("");
  EXPECT_THAT(*actual_comparator, EqualsProto(empty_version_comparator));
}

TEST(DatasetConstraintsUtilTest,
     UpdateWithBetweenThresholdNumExamplesDoesNotChangeDriftComparator) {
  DatasetFeatureStatistics previous_span_statistics =
      ParseTextProtoOrDie<DatasetFeatureStatistics>(R"(num_examples: 4)");
  DatasetStatsView previous_span_stats_view =
      DatasetStatsView(previous_span_statistics, /*by_weight=*/false);
  DatasetFeatureStatistics current_statistics =
      ParseTextProtoOrDie<DatasetFeatureStatistics>(R"(num_examples: 2)");
  DatasetStatsView current_stats_view = DatasetStatsView(
      current_statistics, /*by_weight=*/false, /*environment=*/absl::nullopt,
      std::make_shared<DatasetStatsView>(previous_span_stats_view),
      /*serving=*/std::shared_ptr<DatasetStatsView>(),
      /*previous_version=*/std::shared_ptr<DatasetStatsView>());
  NumericValueComparator original_comparator =
      ParseTextProtoOrDie<NumericValueComparator>(
          R"(min_fraction_threshold: 0.5, max_fraction_threshold: 1.0)");
  NumericValueComparator comparator;
  comparator.CopyFrom(original_comparator);

  // num_examples in the current stats (i.e., 2) is not outside the threshold
  // bounds specified in the comparator (i.e., 0.5 * 4, which is
  // min_fraction_threshold * num_examples in previous span).
  UpdateNumExamplesComparatorDirect(current_stats_view,
                                    DatasetComparatorType::DRIFT, &comparator);

  EXPECT_THAT(comparator, EqualsProto(original_comparator));
}

TEST(DatasetConstraintsUtilTest,
     UpdateWithOutsideThresholdNumExamplesChangesDriftComparator) {
  DatasetFeatureStatistics previous_span_statistics =
      ParseTextProtoOrDie<DatasetFeatureStatistics>(R"(num_examples: 4)");
  DatasetStatsView previous_span_stats_view =
      DatasetStatsView(previous_span_statistics, /*by_weight=*/false);
  DatasetFeatureStatistics current_statistics =
      ParseTextProtoOrDie<DatasetFeatureStatistics>(R"(num_examples: 2)");
  DatasetStatsView current_stats_view = DatasetStatsView(
      current_statistics, /*by_weight=*/false, /*environment=*/absl::nullopt,
      std::make_shared<DatasetStatsView>(previous_span_stats_view),
      /*serving=*/std::shared_ptr<DatasetStatsView>(),
      /*previous_version=*/std::shared_ptr<DatasetStatsView>());
  NumericValueComparator comparator =
      ParseTextProtoOrDie<NumericValueComparator>(
          R"(min_fraction_threshold: 1.0, max_fraction_threshold: 1.0)");

  // num_examples in the current stats (i.e., 2) is outside the threshold
  // bounds specified in the comparator (i.e., 1.0 * 4, which is
  // min_fraction_threshold * num_examples in previous span).
  UpdateNumExamplesComparatorDirect(current_stats_view,
                                    DatasetComparatorType::DRIFT, &comparator);

  // The comparator should be updated so that num_examples in the current stats
  // is within the threshold bounds.
  NumericValueComparator expected_comparator =
      ParseTextProtoOrDie<NumericValueComparator>(
          R"(min_fraction_threshold: 0.5, max_fraction_threshold: 1.0)");
  EXPECT_THAT(comparator, EqualsProto(expected_comparator));
}

TEST(DatasetConstraintsUtilTest,
     UpdateWithBetweenThresholdNumExamplesDoesNotChangeVersionComparator) {
  DatasetFeatureStatistics previous_version_statistics =
      ParseTextProtoOrDie<DatasetFeatureStatistics>(R"(num_examples: 4)");
  DatasetStatsView previous_version_stats_view =
      DatasetStatsView(previous_version_statistics, /*by_weight=*/false);
  DatasetFeatureStatistics current_statistics =
      ParseTextProtoOrDie<DatasetFeatureStatistics>(R"(num_examples: 2)");
  DatasetStatsView current_stats_view = DatasetStatsView(
      current_statistics, /*by_weight=*/false, /*environment=*/absl::nullopt,
      /*previous_span=*/std::shared_ptr<DatasetStatsView>(),
      /*serving=*/std::shared_ptr<DatasetStatsView>(),
      std::make_shared<DatasetStatsView>(previous_version_stats_view));
  NumericValueComparator original_comparator =
      ParseTextProtoOrDie<NumericValueComparator>(
          R"(min_fraction_threshold: 0.5, max_fraction_threshold: 1.0)");
  NumericValueComparator comparator;
  comparator.CopyFrom(original_comparator);

  // num_examples in the current stats (i.e., 2) is not outside the threshold
  // bounds specified in the comparator (i.e., 0.5 * 4, which is
  // min_fraction_threshold * num_examples in previous version).
  UpdateNumExamplesComparatorDirect(
      current_stats_view, DatasetComparatorType::VERSION, &comparator);

  EXPECT_THAT(comparator, EqualsProto(original_comparator));
}

TEST(DatasetConstraintsUtilTest,
     UpdateWithOutsideThresholdNumExamplesChangesVersionComparator) {
  DatasetFeatureStatistics previous_version_statistics =
      ParseTextProtoOrDie<DatasetFeatureStatistics>(R"(num_examples: 1)");
  DatasetStatsView previous_version_stats_view =
      DatasetStatsView(previous_version_statistics, /*by_weight=*/false);
  DatasetFeatureStatistics current_statistics =
      ParseTextProtoOrDie<DatasetFeatureStatistics>(R"(num_examples: 2)");
  DatasetStatsView current_stats_view = DatasetStatsView(
      current_statistics, /*by_weight=*/false, /*environment=*/absl::nullopt,
      /*previous_span=*/std::shared_ptr<DatasetStatsView>(),
      /*serving=*/std::shared_ptr<DatasetStatsView>(),
      std::make_shared<DatasetStatsView>(previous_version_stats_view));
  NumericValueComparator comparator =
      ParseTextProtoOrDie<NumericValueComparator>(
          R"(min_fraction_threshold: 1.0, max_fraction_threshold: 1.0)");

  // num_examples in the current stats (i.e., 1) is outside the threshold
  // bounds specified in the comparator (i.e., 1.0 * 2, which is
  // max_fraction_threshold * num_examples in previous span).
  UpdateNumExamplesComparatorDirect(
      current_stats_view, DatasetComparatorType::VERSION, &comparator);

  // The comparator should be updated so that num_examples in the current stats
  // is within the threshold bounds.
  NumericValueComparator expected_comparator =
      ParseTextProtoOrDie<NumericValueComparator>(
          R"(min_fraction_threshold: 1.0, max_fraction_threshold: 2.0)");
  EXPECT_THAT(comparator, EqualsProto(expected_comparator));
}

TEST(DatasetConstraintsUtilTest,
     UpdateWithZeroExamplesInControlClearsMaxThreshold) {
  DatasetFeatureStatistics previous_version_statistics =
      ParseTextProtoOrDie<DatasetFeatureStatistics>(R"(num_examples: 0)");
  DatasetStatsView previous_version_stats_view =
      DatasetStatsView(previous_version_statistics, /*by_weight=*/false);
  DatasetFeatureStatistics current_statistics =
      ParseTextProtoOrDie<DatasetFeatureStatistics>(R"(num_examples: 2)");
  DatasetStatsView current_stats_view = DatasetStatsView(
      current_statistics, /*by_weight=*/false, /*environment=*/absl::nullopt,
      /*previous_span=*/std::shared_ptr<DatasetStatsView>(),
      /*serving=*/std::shared_ptr<DatasetStatsView>(),
      std::make_shared<DatasetStatsView>(previous_version_stats_view));
  NumericValueComparator comparator =
      ParseTextProtoOrDie<NumericValueComparator>(
          R"(min_fraction_threshold: 1.0, max_fraction_threshold: 1.0)");

  UpdateNumExamplesComparatorDirect(
      current_stats_view, DatasetComparatorType::VERSION, &comparator);

  NumericValueComparator expected_comparator =
      ParseTextProtoOrDie<NumericValueComparator>(
          R"(min_fraction_threshold: 1.0)");
  EXPECT_THAT(comparator, EqualsProto(expected_comparator));
}

TEST(DatasetConstraintsUtilTest,
     UpdateWithNoControlStatsDoesNotAlterThreshold) {
  DatasetFeatureStatistics current_statistics =
      ParseTextProtoOrDie<DatasetFeatureStatistics>(R"(num_examples: 2)");
  DatasetStatsView current_stats_view = DatasetStatsView(
      current_statistics, /*by_weight=*/false, /*environment=*/absl::nullopt,
      /*previous_span=*/std::shared_ptr<DatasetStatsView>(),
      /*serving=*/std::shared_ptr<DatasetStatsView>(),
      /*previous_version=*/std::shared_ptr<DatasetStatsView>());
  NumericValueComparator original_comparator =
      ParseTextProtoOrDie<NumericValueComparator>(
          R"(max_fraction_threshold: 1.0)");
  NumericValueComparator comparator;
  comparator.CopyFrom(original_comparator);

  std::vector<Description> actual_descriptions =
      UpdateNumExamplesComparatorDirect(
          current_stats_view, DatasetComparatorType::VERSION, &comparator);

  EXPECT_TRUE(actual_descriptions.empty());
  EXPECT_THAT(comparator, EqualsProto(original_comparator));
}

TEST(DatasetConstraintsUtilTest,
     UpdateWithNoControlStatsReturnsEmptyDescriptions) {
  DatasetFeatureStatistics current_statistics =
      ParseTextProtoOrDie<DatasetFeatureStatistics>(R"(num_examples: 2)");
  DatasetStatsView current_stats_view = DatasetStatsView(
      current_statistics, /*by_weight=*/false, /*environment=*/absl::nullopt,
      /*previous_span=*/std::shared_ptr<DatasetStatsView>(),
      /*serving=*/std::shared_ptr<DatasetStatsView>(),
      /*previous_version=*/std::shared_ptr<DatasetStatsView>());
  NumericValueComparator original_comparator =
      ParseTextProtoOrDie<NumericValueComparator>(
          R"(max_fraction_threshold: 1.0)");
  NumericValueComparator comparator;
  comparator.CopyFrom(original_comparator);

  std::vector<Description> actual_descriptions =
      UpdateNumExamplesComparatorDirect(
          current_stats_view, DatasetComparatorType::VERSION, &comparator);

  EXPECT_TRUE(actual_descriptions.empty());
}

}  // namespace
}  // namespace data_validation
}  // namespace tensorflow
