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
"""Utilities for manipulating the schema."""

from __future__ import absolute_import
from __future__ import division

from __future__ import print_function

import logging
import six
from tensorflow_data_validation import types
from tensorflow_data_validation.types_compat import Iterable, List, Optional, Set, Tuple, Union
from google.protobuf import text_format
from tensorflow.python.lib.io import file_io
from tensorflow_metadata.proto.v0 import schema_pb2


def get_feature(schema,
                feature_path
               ):
  """Get a feature from the schema.

  Args:
    schema: A Schema protocol buffer.
    feature_path: The path of the feature to obtain from the schema. If a
      FeatureName is passed, a one-step FeaturePath will be constructed and
      used. For example, "my_feature" -> types.FeaturePath(["my_feature"])

  Returns:
    A Feature protocol buffer.

  Raises:
    TypeError: If the input schema is not of the expected type.
    ValueError: If the input feature is not found in the schema.
  """
  if not isinstance(schema, schema_pb2.Schema):
    raise TypeError(
        f'schema is of type {type(schema).__name__}, should be a Schema proto.'
    )

  if not isinstance(feature_path, types.FeaturePath):
    feature_path = types.FeaturePath([feature_path])

  feature_container = schema.feature
  if parent := feature_path.parent():
    for step in parent.steps():
      f = _look_up_feature(step, feature_container)
      if f is None:
        raise ValueError(f'Feature {feature_path} not found in the schema.')
      if f.type != schema_pb2.STRUCT:
        raise ValueError(
            f'Step {step} in feature {feature_path} does not refer to a valid STRUCT feature'
        )
      feature_container = f.struct_domain.feature

  feature = _look_up_feature(feature_path.steps()[-1], feature_container)
  if feature is None:
    raise ValueError(f'Feature {feature_path} not found in the schema.')
  return feature


FEATURE_DOMAIN = Union[schema_pb2.IntDomain, schema_pb2.FloatDomain,
                       schema_pb2.StringDomain, schema_pb2.BoolDomain]


def get_domain(schema,
               feature_path
              ):
  """Get the domain associated with the input feature from the schema.

  Args:
    schema: A Schema protocol buffer.
    feature_path: The path of the feature whose domain needs to be found. If a
      FeatureName is passed, a one-step FeaturePath will be constructed and
      used. For example, "my_feature" -> types.FeaturePath(["my_feature"])

  Returns:
    The domain protocol buffer (one of IntDomain, FloatDomain, StringDomain or
        BoolDomain) associated with the input feature.

  Raises:
    TypeError: If the input schema is not of the expected type.
    ValueError: If the input feature is not found in the schema or there is
        no domain associated with the feature.
  """
  if not isinstance(schema, schema_pb2.Schema):
    raise TypeError(
        f'schema is of type {type(schema).__name__}, should be a Schema proto.'
    )

  feature = get_feature(schema, feature_path)
  domain_info = feature.WhichOneof('domain_info')

  if domain_info is None:
    raise ValueError(f'Feature {feature_path} has no domain associated with it.')

  if domain_info == 'bool_domain':
    return feature.bool_domain

  elif domain_info == 'domain':
    for domain in schema.string_domain:
      if domain.name == feature.domain:
        return domain
  elif domain_info == 'float_domain':
    return feature.float_domain
  elif domain_info == 'int_domain':
    return feature.int_domain
  elif domain_info == 'string_domain':
    return feature.string_domain
  raise ValueError(
      f'Feature {feature_path} has an unsupported domain {domain_info}.')


def set_domain(schema, feature_path,
               domain):
  """Sets the domain for the input feature in the schema.

  If the input feature already has a domain, it is overwritten with the newly
  provided input domain. This method cannot be used to add a new global domain.

  Args:
    schema: A Schema protocol buffer.
    feature_path: The name of the feature whose domain needs to be set. If a
      FeatureName is passed, a one-step FeaturePath will be constructed and
      used. For example, "my_feature" -> types.FeaturePath(["my_feature"])
    domain: A domain protocol buffer (one of IntDomain, FloatDomain,
      StringDomain or BoolDomain) or the name of a global string domain present
      in the input schema.
  Example:  ```python >>> from tensorflow_metadata.proto.v0 import schema_pb2
    >>> import tensorflow_data_validation as tfdv >>> schema =
    schema_pb2.Schema() >>> schema.feature.add(name='feature') # Setting a int
    domain. >>> int_domain = schema_pb2.IntDomain(min=3, max=5) >>>
    tfdv.set_domain(schema, "feature", int_domain) # Setting a string domain.
    >>> str_domain = schema_pb2.StringDomain(value=['one', 'two', 'three']) >>>
    tfdv.set_domain(schema, "feature", str_domain) ```

  Raises:
    TypeError: If the input schema or the domain is not of the expected type.
    ValueError: If an invalid global string domain is provided as input.
  """
  if not isinstance(schema, schema_pb2.Schema):
    raise TypeError(
        f'schema is of type {type(schema).__name__}, should be a Schema proto.'
    )

  if not isinstance(domain, (schema_pb2.IntDomain, schema_pb2.FloatDomain,
                             schema_pb2.StringDomain, schema_pb2.BoolDomain,
                             six.string_types)):
    raise TypeError('domain is of type %s, should be one of IntDomain, '
                    'FloatDomain, StringDomain, BoolDomain proto or a string '
                    'denoting the name of a global domain in the schema.' %
                    type(domain).__name__)

  feature = get_feature(schema, feature_path)
  if feature.type == schema_pb2.STRUCT:
    raise TypeError(
        f'Could not set the domain of a STRUCT feature {feature_path}.')

  if feature.WhichOneof('domain_info') is not None:
    logging.warning('Replacing existing domain of feature "%s".', feature_path)

  if isinstance(domain, schema_pb2.IntDomain):
    feature.int_domain.CopyFrom(domain)
  elif isinstance(domain, schema_pb2.FloatDomain):
    feature.float_domain.CopyFrom(domain)
  elif isinstance(domain, schema_pb2.StringDomain):
    feature.string_domain.CopyFrom(domain)
  elif isinstance(domain, schema_pb2.BoolDomain):
    feature.bool_domain.CopyFrom(domain)
  else:
    found_domain = any(
        global_domain.name == domain for global_domain in schema.string_domain)
    if not found_domain:
      raise ValueError(f'Invalid global string domain "{domain}".')
    feature.domain = domain


def write_schema_text(schema, output_path):
  """Writes input schema to a file in text format.

  Args:
    schema: A Schema protocol buffer.
    output_path: File path to write the input schema.

  Raises:
    TypeError: If the input schema is not of the expected type.
  """
  if not isinstance(schema, schema_pb2.Schema):
    raise TypeError(
        f'schema is of type {type(schema).__name__}, should be a Schema proto.'
    )

  schema_text = text_format.MessageToString(schema)
  file_io.write_string_to_file(output_path, schema_text)


def load_schema_text(input_path):
  """Loads the schema stored in text format in the input path.

  Args:
    input_path: File path to load the schema from.

  Returns:
    A Schema protocol buffer.
  """
  schema = schema_pb2.Schema()
  schema_text = file_io.read_file_to_string(input_path)
  text_format.Parse(schema_text, schema)
  return schema


def is_categorical_feature(feature):
  """Checks if the input feature is categorical."""
  if feature.type == schema_pb2.BYTES:
    return True
  elif feature.type == schema_pb2.INT:
    return ((feature.HasField('int_domain') and
             feature.int_domain.is_categorical) or
            feature.HasField('bool_domain'))
  else:
    return False


def get_categorical_numeric_features(
    schema):
  """Get the list of numeric features that should be treated as categorical.

  Args:
    schema: The schema for the data.

  Returns:
    A list of int features that should be considered categorical.
  """
  return [
      feature_path for feature_path, feature in _get_all_leaf_features(schema)
      if feature.type == schema_pb2.INT and is_categorical_feature(feature)
  ]


def get_categorical_features(schema
                            ):
  """Gets the set containing the names of all categorical features.

  Args:
    schema: The schema for the data.

  Returns:
    A set containing the names of all categorical features.
  """
  return {
      feature_path for feature_path, feature in _get_all_leaf_features(schema)
      if is_categorical_feature(feature)
  }


def get_multivalent_features(schema
                            ):
  """Gets the set containing the names of all multivalent features.

  Args:
    schema: The schema for the data.

  Returns:
    A set containing the names of all multivalent features.
  """

  # Check if the feature is not univalent. A univalent feature will either
  # have the shape field set with one dimension of size 1 or the value_count
  # field set with a max value_count of 1.
  # pylint: disable=g-complex-comprehension
  return {
      feature_path for feature_path, feature in _get_all_leaf_features(schema)
      if not ((feature.shape and feature.shape.dim and
               len(feature.shape.dim) == feature.shape.dim[0].size == 1) or
              (feature.value_count and feature.value_count.max == 1))
  }


def _look_up_feature(feature_name,
                     container
                    ):
  return next((f for f in container if f.name == feature_name), None)


def _get_all_leaf_features(
    schema
):
  """Returns all leaf features in a schema."""
  def _recursion_helper(
      parent_path,
      feature_container,
      result):
    for f in feature_container:
      feature_path = parent_path.child(f.name)
      if f.type != schema_pb2.STRUCT:
        result.append((feature_path, f))
      else:
        _recursion_helper(feature_path, f.struct_domain.feature, result)

  result = []
  _recursion_helper(types.FeaturePath([]), schema.feature, result)
  return result
