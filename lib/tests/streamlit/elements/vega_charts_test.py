# Copyright (c) Streamlit Inc. (2018-2022) Snowflake Inc. (2022-2024)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import unittest
from typing import Any, Callable
from unittest import mock
from unittest.mock import patch

import altair as alt
import pandas as pd
import pyarrow as pa
import pytest
from packaging import version
from parameterized import parameterized

import streamlit as st
from streamlit.elements.vega_charts import (
    _reset_counter_pattern,
    _stabilize_vega_json_spec,
)
from streamlit.errors import StreamlitAPIException
from streamlit.type_util import (
    bytes_to_data_frame,
    is_altair_version_less_than,
    pyarrow_table_to_bytes,
)
from tests.delta_generator_test_case import DeltaGeneratorTestCase

df1 = pd.DataFrame([["A", "B", "C", "D"], [28, 55, 43, 91]], index=["a", "b"]).T
df2 = pd.DataFrame([["E", "F", "G", "H"], [11, 12, 13, 14]], index=["a", "b"]).T
autosize_spec = {"autosize": {"type": "fit", "contains": "padding"}}


def merge_dicts(x, y):
    z = x.copy()
    z.update(y)
    return z


def create_advanced_altair_chart() -> alt.Chart:
    """Create an advanced Altair chart based on concatenation and with parameters."""
    iris = alt.UrlData(
        "https://cdn.jsdelivr.net/npm/vega-datasets@v1.29.0/data/iris.json"
    )

    point = alt.selection_point()
    interval = alt.selection_interval()

    base = (
        alt.Chart()
        .mark_point()
        .encode(
            color="species:N",
            tooltip=alt.value(None),
        )
        .properties(width=200, height=200)
    )

    chart = alt.vconcat(data=iris)
    for y_encoding in ["petalLength:Q", "petalWidth:Q"]:
        row = alt.hconcat()
        for x_encoding in ["sepalLength:Q", "sepalWidth:Q"]:
            row |= base.encode(x=x_encoding, y=y_encoding)
        chart &= row
    chart = chart.add_params(point)
    chart = chart.add_params(interval)
    return chart


class AltairChartTest(DeltaGeneratorTestCase):
    """Test the `st.altair_chart` command."""

    def test_altair_chart(self):
        """Test that it can be called with args."""
        df = pd.DataFrame([["A", "B", "C", "D"], [28, 55, 43, 91]], index=["a", "b"]).T
        chart = alt.Chart(df).mark_bar().encode(x="a", y="b")
        EXPECTED_DATAFRAME = pd.DataFrame(
            {
                "a": ["A", "B", "C", "D"],
                "b": [28, 55, 43, 91],
            }
        )

        st.altair_chart(chart)

        proto = self.get_delta_from_queue().new_element.arrow_vega_lite_chart

        self.assertEqual(proto.HasField("data"), False)
        self.assertEqual(len(proto.datasets), 1)
        pd.testing.assert_frame_equal(
            bytes_to_data_frame(proto.datasets[0].data.data), EXPECTED_DATAFRAME
        )

        spec_dict = json.loads(proto.spec)
        self.assertEqual(
            spec_dict["encoding"],
            {
                "y": {"field": "b", "type": "quantitative"},
                "x": {"field": "a", "type": "nominal"},
            },
        )
        self.assertEqual(spec_dict["data"], {"name": proto.datasets[0].name})
        self.assertIn(spec_dict["mark"], ["bar", {"type": "bar"}])
        self.assertTrue("encoding" in spec_dict)

    def test_altair_chart_uses_convert_anything_to_df(self):
        """Test that st.altair_chart uses convert_anything_to_df to convert input data."""
        df = pd.DataFrame([["A", "B", "C", "D"], [28, 55, 43, 91]], index=["a", "b"]).T
        chart = alt.Chart(df).mark_bar().encode(x="a", y="b")

        with mock.patch(
            "streamlit.type_util.convert_anything_to_df"
        ) as convert_anything_to_df:
            convert_anything_to_df.return_value = df

            st.altair_chart(chart)
            convert_anything_to_df.assert_called_once()

    @parameterized.expand(
        [
            ("streamlit", "streamlit"),
            (None, ""),
        ]
    )
    def test_theme(self, theme_value, proto_value):
        df = pd.DataFrame([["A", "B", "C", "D"], [28, 55, 43, 91]], index=["a", "b"]).T
        chart = alt.Chart(df).mark_bar().encode(x="a", y="b")

        st.altair_chart(chart, theme=theme_value)

        el = self.get_delta_from_queue().new_element
        self.assertEqual(el.arrow_vega_lite_chart.theme, proto_value)

    def test_bad_theme(self):
        df = pd.DataFrame([["A", "B", "C", "D"], [28, 55, 43, 91]], index=["a", "b"]).T
        chart = alt.Chart(df).mark_bar().encode(x="a", y="b")

        with self.assertRaises(StreamlitAPIException) as exc:
            st.altair_chart(chart, theme="bad_theme")

        self.assertEqual(
            f'You set theme="bad_theme" while Streamlit charts only support theme=”streamlit” or theme=None to fallback to the default library theme.',
            str(exc.exception),
        )

    def test_empty_altair_chart_throws_error(self):
        with self.assertRaises(TypeError):
            st.altair_chart(use_container_width=True)

    def test_dataset_names_stay_stable(self):
        """Test that dataset names stay stable across multiple calls
        with new Pandas objects containing the same data.
        """
        # Execution 1:
        df = pd.DataFrame([["A", "B", "C", "D"], [28, 55, 43, 91]], index=["a", "b"]).T
        chart = alt.Chart(df).mark_bar().encode(x="a", y="b")
        st.altair_chart(chart)
        chart_el_1 = self.get_delta_from_queue().new_element

        # Execution 2 (recreate the same chart with new objects)
        df = pd.DataFrame([["A", "B", "C", "D"], [28, 55, 43, 91]], index=["a", "b"]).T
        chart = alt.Chart(df).mark_bar().encode(x="a", y="b")
        st.altair_chart(chart)

        chart_el_2 = self.get_delta_from_queue().new_element

        # Make sure that there is one named dataset:
        self.assertEqual(len(chart_el_1.arrow_vega_lite_chart.datasets), 1)
        # The names should not have changes
        self.assertEqual(
            [dataset.name for dataset in chart_el_1.arrow_vega_lite_chart.datasets],
            [dataset.name for dataset in chart_el_2.arrow_vega_lite_chart.datasets],
        )
        # The specs should also be the same:
        self.assertEqual(
            chart_el_1.arrow_vega_lite_chart.spec,
            chart_el_2.arrow_vega_lite_chart.spec,
        )

    @unittest.skipIf(
        is_altair_version_less_than("5.0.0") is True,
        "This test only runs if altair is >= 5.0.0",
    )
    def test_that_altair_chart_spec_stays_stable(self):
        """Test that st.altair_chart stays stable across multiple calls."""
        # Execution 1:
        chart = create_advanced_altair_chart()
        st.altair_chart(chart)

        initial_spec = (
            self.get_delta_from_queue().new_element.arrow_vega_lite_chart.spec
        )

        # Create the same chart 100 times and check that the spec is the same:
        for _ in range(100):
            chart = create_advanced_altair_chart()
            st.altair_chart(chart)

            el = self.get_delta_from_queue().new_element
            self.assertEqual(el.arrow_vega_lite_chart.spec, initial_spec)


class VegaLiteChartTest(DeltaGeneratorTestCase):
    """Test the `st.vega_lite_chart` command."""

    def test_no_args(self):
        """Test that an error is raised when called with no args."""
        with self.assertRaises(StreamlitAPIException):
            st.vega_lite_chart()

    def test_none_args(self):
        """Test that an error is raised when called with args set to None."""
        with self.assertRaises(StreamlitAPIException):
            st.vega_lite_chart(None, None)

    def test_spec_but_no_data(self):
        """Test that it can be called with only data set to None."""
        st.vega_lite_chart(None, {"mark": "rect"})

        proto = self.get_delta_from_queue().new_element.arrow_vega_lite_chart
        self.assertEqual(proto.HasField("data"), False)
        self.assertDictEqual(
            json.loads(proto.spec), merge_dicts(autosize_spec, {"mark": "rect"})
        )

    def test_spec_in_arg1(self):
        """Test that it can be called with spec as the 1st arg."""
        st.vega_lite_chart({"mark": "rect"})

        proto = self.get_delta_from_queue().new_element.arrow_vega_lite_chart
        self.assertEqual(proto.HasField("data"), False)
        self.assertDictEqual(
            json.loads(proto.spec), merge_dicts(autosize_spec, {"mark": "rect"})
        )

    def test_data_in_spec(self):
        """Test passing data=df inside the spec."""
        st.vega_lite_chart({"mark": "rect", "data": df1})

        proto = self.get_delta_from_queue().new_element.arrow_vega_lite_chart
        pd.testing.assert_frame_equal(
            bytes_to_data_frame(proto.data.data), df1, check_dtype=False
        )
        self.assertDictEqual(
            json.loads(proto.spec), merge_dicts(autosize_spec, {"mark": "rect"})
        )

    def test_vega_lite_chart_uses_convert_anything_to_df(self):
        """Test that st.vega_lite_chart uses convert_anything_to_df to convert input data."""

        with patch(
            "streamlit.type_util.convert_anything_to_df"
        ) as convert_anything_to_df:
            convert_anything_to_df.return_value = df1

            st.vega_lite_chart({"mark": "rect", "data": df1})
            convert_anything_to_df.assert_called_once()

    def test_data_values_in_spec(self):
        """Test passing data={values: df} inside the spec."""
        st.vega_lite_chart({"mark": "rect", "data": {"values": df1}})

        proto = self.get_delta_from_queue().new_element.arrow_vega_lite_chart
        pd.testing.assert_frame_equal(
            bytes_to_data_frame(proto.data.data), df1, check_dtype=False
        )
        self.assertDictEqual(
            json.loads(proto.spec),
            merge_dicts(autosize_spec, {"mark": "rect"}),
        )

    def test_datasets_in_spec(self):
        """Test passing datasets={foo: df} inside the spec."""
        st.vega_lite_chart({"mark": "rect", "datasets": {"foo": df1}})

        proto = self.get_delta_from_queue().new_element.arrow_vega_lite_chart
        self.assertEqual(proto.HasField("data"), False)
        self.assertDictEqual(
            json.loads(proto.spec), merge_dicts(autosize_spec, {"mark": "rect"})
        )

    def test_datasets_correctly_in_spec(self):
        """Test passing datasets={foo: df}, data={name: 'foo'} in the spec."""
        st.vega_lite_chart(
            {"mark": "rect", "datasets": {"foo": df1}, "data": {"name": "foo"}}
        )

        proto = self.get_delta_from_queue().new_element.arrow_vega_lite_chart
        self.assertEqual(proto.HasField("data"), False)
        self.assertDictEqual(
            json.loads(proto.spec),
            merge_dicts(autosize_spec, {"data": {"name": "foo"}, "mark": "rect"}),
        )

    def test_dict_unflatten(self):
        """Test passing a spec as keywords."""
        st.vega_lite_chart(df1, x="foo", boink_boop=100, baz={"boz": "booz"})

        proto = self.get_delta_from_queue().new_element.arrow_vega_lite_chart
        pd.testing.assert_frame_equal(
            bytes_to_data_frame(proto.data.data), df1, check_dtype=False
        )
        self.assertDictEqual(
            json.loads(proto.spec),
            merge_dicts(
                autosize_spec,
                {
                    "baz": {"boz": "booz"},
                    "boink": {"boop": 100},
                    "encoding": {"x": "foo"},
                },
            ),
        )

    def test_pyarrow_table_data(self):
        """Test that you can pass pyarrow.Table as data."""
        table = pa.Table.from_pandas(df1)
        st.vega_lite_chart(table, {"mark": "rect"})

        proto = self.get_delta_from_queue().new_element.arrow_vega_lite_chart

        self.assertEqual(proto.HasField("data"), True)
        self.assertEqual(proto.data.data, pyarrow_table_to_bytes(table))

    def test_add_rows(self):
        """Test that you can call add_rows on arrow_vega_lite_chart (with data)."""
        chart = st.vega_lite_chart(df1, {"mark": "rect"})

        proto = self.get_delta_from_queue().new_element.arrow_vega_lite_chart
        self.assertEqual(proto.HasField("data"), True)

        chart.add_rows(df2)

        proto = self.get_delta_from_queue().arrow_add_rows
        pd.testing.assert_frame_equal(
            bytes_to_data_frame(proto.data.data), df2, check_dtype=False
        )

    def test_no_args_add_rows(self):
        """Test that you can call add_rows on a arrow_vega_lite_chart (without data)."""
        chart = st.vega_lite_chart({"mark": "rect"})

        proto = self.get_delta_from_queue().new_element.arrow_vega_lite_chart
        self.assertEqual(proto.HasField("data"), False)

        chart.add_rows(df1)

        proto = self.get_delta_from_queue().arrow_add_rows
        pd.testing.assert_frame_equal(
            bytes_to_data_frame(proto.data.data), df1, check_dtype=False
        )

    def test_use_container_width(self):
        """Test that use_container_width=True autosets to full width."""
        st.vega_lite_chart(df1, {"mark": "rect"}, use_container_width=True)

        proto = self.get_delta_from_queue().new_element.arrow_vega_lite_chart
        self.assertDictEqual(
            json.loads(proto.spec), merge_dicts(autosize_spec, {"mark": "rect"})
        )

        self.assertEqual(proto.use_container_width, True)

    @parameterized.expand(
        [
            ("streamlit", "streamlit"),
            (None, ""),
        ]
    )
    def test_theme(self, theme_value, proto_value):
        st.vega_lite_chart(
            df1, {"mark": "rect"}, use_container_width=True, theme=theme_value
        )

        el = self.get_delta_from_queue().new_element
        self.assertEqual(el.arrow_vega_lite_chart.theme, proto_value)

    def test_bad_theme(self):
        with self.assertRaises(StreamlitAPIException) as exc:
            st.vega_lite_chart(df1, theme="bad_theme")

        self.assertEqual(
            f'You set theme="bad_theme" while Streamlit charts only support theme=”streamlit” or theme=None to fallback to the default library theme.',
            str(exc.exception),
        )

    def test_width_inside_spec(self):
        """Test that Vega-Lite sets the width."""
        st.vega_lite_chart(df1, {"mark": "rect", "width": 200})

        proto = self.get_delta_from_queue().new_element.arrow_vega_lite_chart
        self.assertDictEqual(
            json.loads(proto.spec),
            merge_dicts(autosize_spec, {"mark": "rect", "width": 200}),
        )

    @parameterized.expand(
        [
            (None, {}),
            (pd.DataFrame({"a": [1, 2, 3, 4], "b": [1, 3, 2, 4]}), {}),
            (pd.DataFrame({"a": [1, 2, 3, 4], "b": [1, 3, 2, 4]}), None),
            (None, None),
        ]
    )
    def test_empty_vega_lite_chart_throws_error(self, data, spec):
        with self.assertRaises(StreamlitAPIException):
            st.vega_lite_chart(data, spec, use_container_width=True)


ST_CHART_ARGS = [
    (st.area_chart, "area"),
    (st.bar_chart, "bar"),
    (st.line_chart, "line"),
    (st.scatter_chart, "circle"),
]


class BuiltInChartTest(DeltaGeneratorTestCase):
    """Test our built-in chart commands."""

    @parameterized.expand(ST_CHART_ARGS)
    def test_empty_chart(self, chart_command: Callable, altair_type: str):
        """Test arrow chart with no arguments."""
        EXPECTED_DATAFRAME = pd.DataFrame()

        # Make some mutations that arrow_altair.prep_data() does.
        column_names = list(
            EXPECTED_DATAFRAME.columns
        )  # list() converts RangeIndex, etc, to regular list.
        str_column_names = [str(c) for c in column_names]
        EXPECTED_DATAFRAME.columns = pd.Index(str_column_names)

        chart_command()

        proto = self.get_delta_from_queue().new_element.arrow_vega_lite_chart

        chart_spec = json.loads(proto.spec)
        self.assertIn(chart_spec["mark"], [altair_type, {"type": altair_type}])

        pd.testing.assert_frame_equal(
            bytes_to_data_frame(proto.datasets[0].data.data),
            EXPECTED_DATAFRAME,
        )

    @parameterized.expand(ST_CHART_ARGS)
    def test_chart_with_implicit_x_and_y(
        self, chart_command: Callable, altair_type: str
    ):
        """Test st.line_chart with implicit x and y."""
        df = pd.DataFrame([[20, 30, 50]], columns=["a", "b", "c"])
        EXPECTED_DATAFRAME = pd.DataFrame(
            [[20, "b", 30], [20, "c", 50]],
            columns=["a", "color--p5bJXXpQgvPz6yvQMFiy", "value--p5bJXXpQgvPz6yvQMFiy"],
        )

        chart_command(df, x="a", y=["b", "c"])

        proto = self.get_delta_from_queue().new_element.arrow_vega_lite_chart
        chart_spec = json.loads(proto.spec)

        self.assertIn(chart_spec["mark"], [altair_type, {"type": altair_type}])
        self.assertEqual(chart_spec["encoding"]["x"]["field"], "a")
        self.assertEqual(
            chart_spec["encoding"]["y"]["field"], "value--p5bJXXpQgvPz6yvQMFiy"
        )
        self.assertEqual(
            chart_spec["encoding"]["color"]["field"], "color--p5bJXXpQgvPz6yvQMFiy"
        )

        self.assert_output_df_is_correct_and_input_is_untouched(
            orig_df=df, expected_df=EXPECTED_DATAFRAME, chart_proto=proto
        )

    @parameterized.expand(ST_CHART_ARGS)
    def test_chart_with_explicit_x_and_implicit_y(
        self, chart_command: Callable, altair_type: str
    ):
        """Test st.line_chart with explicit x and implicit y."""
        df = pd.DataFrame([[20, 30, 50]], columns=["a", "b", "c"])
        EXPECTED_DATAFRAME = pd.DataFrame(
            [[20, "b", 30], [20, "c", 50]],
            columns=["a", "color--p5bJXXpQgvPz6yvQMFiy", "value--p5bJXXpQgvPz6yvQMFiy"],
        )

        chart_command(df, x="a")

        proto = self.get_delta_from_queue().new_element.arrow_vega_lite_chart
        chart_spec = json.loads(proto.spec)
        self.assertIn(chart_spec["mark"], [altair_type, {"type": altair_type}])
        self.assertEqual(chart_spec["encoding"]["x"]["field"], "a")
        self.assertEqual(
            chart_spec["encoding"]["y"]["field"], "value--p5bJXXpQgvPz6yvQMFiy"
        )
        self.assertEqual(
            chart_spec["encoding"]["color"]["field"], "color--p5bJXXpQgvPz6yvQMFiy"
        )

        self.assert_output_df_is_correct_and_input_is_untouched(
            orig_df=df, expected_df=EXPECTED_DATAFRAME, chart_proto=proto
        )

    @parameterized.expand(ST_CHART_ARGS)
    def test_chart_with_implicit_x_and_explicit_y(
        self, chart_command: Callable, altair_type: str
    ):
        """Test st.line_chart with implicit x and explicit y."""
        df = pd.DataFrame([[20, 30, 50]], columns=["a", "b", "c"])
        EXPECTED_DATAFRAME = pd.DataFrame(
            [[0, 30]], columns=["index--p5bJXXpQgvPz6yvQMFiy", "b"]
        )

        chart_command(df, y="b")

        proto = self.get_delta_from_queue().new_element.arrow_vega_lite_chart
        chart_spec = json.loads(proto.spec)
        self.assertIn(chart_spec["mark"], [altair_type, {"type": altair_type}])
        self.assertEqual(
            chart_spec["encoding"]["x"]["field"], "index--p5bJXXpQgvPz6yvQMFiy"
        )
        self.assertEqual(chart_spec["encoding"]["y"]["field"], "b")
        self.assertFalse("color" in chart_spec["encoding"])

        self.assert_output_df_is_correct_and_input_is_untouched(
            orig_df=df, expected_df=EXPECTED_DATAFRAME, chart_proto=proto
        )

    @parameterized.expand(ST_CHART_ARGS)
    def test_chart_with_implicit_x_and_explicit_y_sequence(
        self, chart_command: Callable, altair_type: str
    ):
        """Test st.line_chart with implicit x and explicit y sequence."""
        df = pd.DataFrame([[20, 30, 50, 60]], columns=["a", "b", "c", "d"])
        EXPECTED_DATAFRAME = pd.DataFrame(
            [[0, "b", 30], [0, "c", 50]],
            columns=[
                "index--p5bJXXpQgvPz6yvQMFiy",
                "color--p5bJXXpQgvPz6yvQMFiy",
                "value--p5bJXXpQgvPz6yvQMFiy",
            ],
        )

        chart_command(df, y=["b", "c"])

        proto = self.get_delta_from_queue().new_element.arrow_vega_lite_chart
        chart_spec = json.loads(proto.spec)
        self.assertIn(chart_spec["mark"], [altair_type, {"type": altair_type}])
        self.assertEqual(
            chart_spec["encoding"]["x"]["field"], "index--p5bJXXpQgvPz6yvQMFiy"
        )
        self.assertEqual(
            chart_spec["encoding"]["y"]["field"], "value--p5bJXXpQgvPz6yvQMFiy"
        )
        self.assertEqual(
            chart_spec["encoding"]["color"]["field"], "color--p5bJXXpQgvPz6yvQMFiy"
        )

        self.assert_output_df_is_correct_and_input_is_untouched(
            orig_df=df, expected_df=EXPECTED_DATAFRAME, chart_proto=proto
        )

    @parameterized.expand(ST_CHART_ARGS)
    def test_chart_with_explicit_x_and_y(
        self, chart_command: Callable, altair_type: str
    ):
        """Test x/y-support for built-in charts."""
        df = pd.DataFrame([[20, 30, 50]], columns=["a", "b", "c"])
        EXPECTED_DATAFRAME = pd.DataFrame([[20, 30]], columns=["a", "b"])

        chart_command(df, x="a", y="b", width=640, height=480)

        proto = self.get_delta_from_queue().new_element.arrow_vega_lite_chart
        chart_spec = json.loads(proto.spec)

        self.assertIn(chart_spec["mark"], [altair_type, {"type": altair_type}])
        self.assertEqual(chart_spec["width"], 640)
        self.assertEqual(chart_spec["height"], 480)
        self.assertEqual(chart_spec["encoding"]["x"]["field"], "a")
        self.assertEqual(chart_spec["encoding"]["y"]["field"], "b")

        self.assert_output_df_is_correct_and_input_is_untouched(
            orig_df=df, expected_df=EXPECTED_DATAFRAME, chart_proto=proto
        )

    @parameterized.expand(ST_CHART_ARGS)
    def test_chart_with_explicit_x_and_y_sequence(
        self, chart_command: Callable, altair_type: str
    ):
        """Test support for explicit wide-format tables (i.e. y is a sequence)."""
        df = pd.DataFrame([[20, 30, 50, 60]], columns=["a", "b", "c", "d"])
        EXPECTED_DATAFRAME = pd.DataFrame(
            [[20, "b", 30], [20, "c", 50]],
            columns=["a", "color--p5bJXXpQgvPz6yvQMFiy", "value--p5bJXXpQgvPz6yvQMFiy"],
        )

        chart_command(df, x="a", y=["b", "c"])

        proto = self.get_delta_from_queue().new_element.arrow_vega_lite_chart
        chart_spec = json.loads(proto.spec)

        self.assertIn(chart_spec["mark"], [altair_type, {"type": altair_type}])
        self.assertEqual(chart_spec["encoding"]["x"]["field"], "a")
        self.assertEqual(
            chart_spec["encoding"]["y"]["field"], "value--p5bJXXpQgvPz6yvQMFiy"
        )
        self.assertEqual(
            chart_spec["encoding"]["color"]["field"], "color--p5bJXXpQgvPz6yvQMFiy"
        )

        self.assert_output_df_is_correct_and_input_is_untouched(
            orig_df=df, expected_df=EXPECTED_DATAFRAME, chart_proto=proto
        )

    @parameterized.expand(ST_CHART_ARGS)
    def test_chart_with_color_value(self, chart_command: Callable, altair_type: str):
        """Test color support for built-in charts."""
        df = pd.DataFrame([[20, 30]], columns=["a", "b"])
        EXPECTED_DATAFRAME = pd.DataFrame([[20, 30]], columns=["a", "b"])

        chart_command(df, x="a", y="b", color="#f00")

        proto = self.get_delta_from_queue().new_element.arrow_vega_lite_chart
        chart_spec = json.loads(proto.spec)

        self.assertEqual(chart_spec["encoding"]["color"]["value"], "#f00")

        self.assert_output_df_is_correct_and_input_is_untouched(
            orig_df=df, expected_df=EXPECTED_DATAFRAME, chart_proto=proto
        )

    @parameterized.expand(ST_CHART_ARGS)
    def test_chart_with_color_column(self, chart_command: Callable, altair_type: str):
        """Test color support for built-in charts."""
        df = pd.DataFrame(
            {
                "x": [0, 1, 2],
                "y": [22, 21, 20],
                "tuple3_int_color": [[255, 0, 0], [0, 255, 0], [0, 0, 255]],
                "tuple4_int_int_color": [
                    [255, 0, 0, 51],
                    [0, 255, 0, 51],
                    [0, 0, 255, 51],
                ],
                "tuple4_int_float_color": [
                    [255, 0, 0, 0.2],
                    [0, 255, 0, 0.2],
                    [0, 0, 255, 0.2],
                ],
                "tuple3_float_color": [
                    [1.0, 0.0, 0.0],
                    [0.0, 1.0, 0.0],
                    [0.0, 0.0, 1.0],
                ],
                "tuple4_float_float_color": [
                    [1.0, 0.0, 0.0, 0.2],
                    [0.0, 1.0, 0.0, 0.2],
                    [0.0, 0.0, 1.0, 0.2],
                ],
                "hex3_color": ["#f00", "#0f0", "#00f"],
                "hex4_color": ["#f008", "#0f08", "#00f8"],
                "hex6_color": ["#ff0000", "#00ff00", "#0000ff"],
                "hex8_color": ["#ff000088", "#00ff0088", "#0000ff88"],
            }
        )

        color_columns = sorted(set(df.columns))
        color_columns.remove("x")
        color_columns.remove("y")

        expected_values = pd.DataFrame(
            {
                "tuple3": ["rgb(255, 0, 0)", "rgb(0, 255, 0)", "rgb(0, 0, 255)"],
                "tuple4": [
                    "rgba(255, 0, 0, 0.2)",
                    "rgba(0, 255, 0, 0.2)",
                    "rgba(0, 0, 255, 0.2)",
                ],
                "hex3": ["#f00", "#0f0", "#00f"],
                "hex6": ["#ff0000", "#00ff00", "#0000ff"],
                "hex4": ["#f008", "#0f08", "#00f8"],
                "hex8": ["#ff000088", "#00ff0088", "#0000ff88"],
            }
        )

        def get_expected_color_values(col_name):
            for prefix, expected_color_values in expected_values.items():
                if col_name.startswith(prefix):
                    return expected_color_values

        for color_column in color_columns:
            expected_color_values = get_expected_color_values(color_column)

            chart_command(df, x="x", y="y", color=color_column)

            proto = self.get_delta_from_queue().new_element.arrow_vega_lite_chart
            chart_spec = json.loads(proto.spec)

            self.assertEqual(chart_spec["encoding"]["color"]["field"], color_column)

            # Manually-specified colors should not have a legend
            self.assertEqual(chart_spec["encoding"]["color"]["legend"], None)

            # Manually-specified colors are set via the color scale's range property.
            self.assertTrue(chart_spec["encoding"]["color"]["scale"]["range"])

            proto_df = bytes_to_data_frame(proto.datasets[0].data.data)

            pd.testing.assert_series_equal(
                proto_df[color_column],
                expected_color_values,
                check_names=False,
            )

    @parameterized.expand(ST_CHART_ARGS)
    def test_chart_with_explicit_x_plus_y_and_color_sequence(
        self, chart_command: Callable, altair_type: str
    ):
        """Test color support for built-in charts with wide-format table."""
        df = pd.DataFrame([[20, 30, 50]], columns=["a", "b", "c"])

        EXPECTED_DATAFRAME = pd.DataFrame(
            [[20, "b", 30], [20, "c", 50]],
            columns=["a", "color--p5bJXXpQgvPz6yvQMFiy", "value--p5bJXXpQgvPz6yvQMFiy"],
        )

        chart_command(df, x="a", y=["b", "c"], color=["#f00", "#0ff"])

        proto = self.get_delta_from_queue().new_element.arrow_vega_lite_chart
        chart_spec = json.loads(proto.spec)

        self.assertIn(chart_spec["mark"], [altair_type, {"type": altair_type}])

        # Color should be set to the melted column name.
        self.assertEqual(
            chart_spec["encoding"]["color"]["field"], "color--p5bJXXpQgvPz6yvQMFiy"
        )

        # Automatically-specified colors should have no legend title.
        self.assertEqual(chart_spec["encoding"]["color"]["title"], " ")

        # Automatically-specified colors should have a legend
        self.assertNotEqual(chart_spec["encoding"]["color"]["legend"], None)

        self.assert_output_df_is_correct_and_input_is_untouched(
            orig_df=df, expected_df=EXPECTED_DATAFRAME, chart_proto=proto
        )

    @parameterized.expand(
        [[None], [[]], [tuple()]],
    )
    def test_chart_with_empty_color(self, color_arg: Any):
        """Test color support for built-in charts with wide-format table."""
        df = pd.DataFrame([[20, 30, 50]], columns=["a", "b", "c"])

        EXPECTED_DATAFRAME = pd.DataFrame([[20, 30]], columns=["a", "b"])

        st.line_chart(df, x="a", y="b", color=color_arg)

        proto = self.get_delta_from_queue().new_element.arrow_vega_lite_chart
        chart_spec = json.loads(proto.spec)

        # Color should be set to the melted column name.
        self.assertEqual(getattr(chart_spec["encoding"], "color", None), None)

        self.assert_output_df_is_correct_and_input_is_untouched(
            orig_df=df, expected_df=EXPECTED_DATAFRAME, chart_proto=proto
        )

    @parameterized.expand(
        [
            (st.area_chart, "a", "foooo"),
            (st.bar_chart, "not-valid", "b"),
            (st.line_chart, "foo", "bar"),
            (st.line_chart, None, "bar"),
            (st.line_chart, "foo", None),
            (st.line_chart, "a", ["b", "foo"]),
            (st.line_chart, None, "variable"),
            (st.line_chart, "variable", ["a", "b"]),
        ]
    )
    def test_chart_with_x_y_invalid_input(
        self,
        chart_command: Callable,
        x: str,
        y: str,
    ):
        """Test x/y support for built-in charts with invalid input."""
        df = pd.DataFrame([[20, 30, 50]], columns=["a", "b", "c"])

        with pytest.raises(StreamlitAPIException):
            chart_command(df, x=x, y=y)

    def test_chart_with_x_y_on_sliced_data(
        self,
    ):
        """Test x/y-support for built-in charts on sliced data."""
        df = pd.DataFrame([[20, 30, 50], [60, 70, 80]], columns=["a", "b", "c"])
        EXPECTED_DATAFRAME = pd.DataFrame([[20, 30], [60, 70]], columns=["a", "b"])[1:]

        # Use all data after first item
        st.line_chart(df[1:], x="a", y="b")

        proto = self.get_delta_from_queue().new_element.arrow_vega_lite_chart
        chart_spec = json.loads(proto.spec)

        self.assertEqual(chart_spec["encoding"]["x"]["field"], "a")
        self.assertEqual(chart_spec["encoding"]["y"]["field"], "b")

        self.assert_output_df_is_correct_and_input_is_untouched(
            orig_df=df, expected_df=EXPECTED_DATAFRAME, chart_proto=proto
        )

    @parameterized.expand(ST_CHART_ARGS)
    @unittest.skipIf(
        version.parse(alt.__version__) < version.parse("5.0.0"),
        "This test only runs if Altair is >= 5.0.0",
    )
    def test_chart_with_ordered_categorical_data(
        self, chart_command: Callable, altair_type: str
    ):
        """Test that built-in charts support ordered categorical data."""
        df = df = pd.DataFrame(
            {
                "categorical": pd.Series(
                    pd.Categorical(
                        ["b", "c", "a", "a"], categories=["c", "b", "a"], ordered=True
                    )
                ),
                "numbers": pd.Series([1, 2, 3, 4]),
            }
        )

        chart_command(df, x="categorical", y="numbers")

        proto = self.get_delta_from_queue().new_element.arrow_vega_lite_chart
        chart_spec = json.loads(proto.spec)

        self.assertIn(chart_spec["mark"], [altair_type, {"type": altair_type}])
        self.assertEqual(chart_spec["encoding"]["x"]["type"], "ordinal")
        self.assertEqual(chart_spec["encoding"]["x"]["sort"], ["c", "b", "a"])
        self.assertEqual(chart_spec["encoding"]["y"]["type"], "quantitative")

    def test_line_chart_with_named_index(self):
        """Test st.line_chart with a named index."""
        df = pd.DataFrame([[20, 30, 50]], columns=["a", "b", "c"])
        df.set_index("a", inplace=True)

        EXPECTED_DATAFRAME = pd.DataFrame(
            [[20, "b", 30], [20, "c", 50]],
            index=[0, 1],
            columns=["a", "color--p5bJXXpQgvPz6yvQMFiy", "value--p5bJXXpQgvPz6yvQMFiy"],
        )

        st.line_chart(df)

        proto = self.get_delta_from_queue().new_element.arrow_vega_lite_chart
        chart_spec = json.loads(proto.spec)
        self.assertIn(chart_spec["mark"], ["line", {"type": "line"}])

        self.assert_output_df_is_correct_and_input_is_untouched(
            orig_df=df, expected_df=EXPECTED_DATAFRAME, chart_proto=proto
        )

    def test_line_chart_with_non_contiguous_index(self):
        """Test st.line_chart with a non-zero-based, non-contiguous, non-sorted index."""
        df = pd.DataFrame(
            {
                "a": [11, 2, 55],
                "b": [100, 101, 102],
                "c": [200, 201, 202],
                "d": [300, 301, 302],
            }
        )
        df.set_index("a", inplace=True)

        # There used to be a bug where this line would throw an exception.
        # (Because some color-handling code was dependent on the DF index starting at 0)
        # So if there's no exception, then the test passes.
        st.line_chart(df, x="b", y="c", color="d")

    @parameterized.expand(ST_CHART_ARGS)
    def test_unused_columns_are_dropped(
        self, chart_command: Callable, altair_type: str
    ):
        """Test built-in charts drop columns that are not used."""

        df = pd.DataFrame(
            [[5, 10, 20, 30, 35, 40, 50, 60]],
            columns=["z", "a", "b", "c", "x", "d", "e", "f"],
        )

        if chart_command == st.scatter_chart:
            chart_command(df, x="a", y="c", color="d", size="e")
            EXPECTED_DATAFRAME = pd.DataFrame(
                [[10, 40, 50, 30]], columns=["a", "d", "e", "c"]
            )
        else:
            chart_command(df, x="a", y="c", color="d")

            EXPECTED_DATAFRAME = pd.DataFrame([[10, 40, 30]], columns=["a", "d", "c"])

        proto = self.get_delta_from_queue().new_element.arrow_vega_lite_chart
        json.loads(proto.spec)

        self.assert_output_df_is_correct_and_input_is_untouched(
            orig_df=df, expected_df=EXPECTED_DATAFRAME, chart_proto=proto
        )

    @parameterized.expand(
        [
            (st.area_chart, "area"),
            (st.bar_chart, "bar"),
            (st.line_chart, "line"),
        ]
    )
    def test_chart_with_bad_color_arg(self, chart_command: Callable, altair_type: str):
        """Test that we throw a pretty exception when colors arg is wrong."""
        df = pd.DataFrame([[20, 30, 50]], columns=["a", "b", "c"])

        too_few_args = ["#f00", ["#f00"], (1, 0, 0, 0.5)]
        too_many_args = [["#f00", "#0ff"], [(1, 0, 0), (0, 0, 1)]]
        bad_args = ["foo", "blue"]

        for color_arg in too_few_args:
            with self.assertRaises(StreamlitAPIException) as exc:
                chart_command(df, y=["a", "b"], color=color_arg)

            self.assertTrue("The list of colors" in str(exc.exception))

        for color_arg in too_many_args:
            with self.assertRaises(StreamlitAPIException) as exc:
                chart_command(df, y="a", color=color_arg)

            self.assertTrue("The list of colors" in str(exc.exception))

        for color_arg in bad_args:
            with self.assertRaises(StreamlitAPIException) as exc:
                chart_command(df, y="a", color=color_arg)

            self.assertTrue(
                "This does not look like a valid color argument" in str(exc.exception)
            )

    def assert_output_df_is_correct_and_input_is_untouched(
        self, orig_df, expected_df, chart_proto
    ):
        """Test that when we modify the outgoing DF we don't mutate the input DF."""
        output_df = bytes_to_data_frame(chart_proto.datasets[0].data.data)

        self.assertNotEqual(id(orig_df), id(output_df))
        self.assertNotEqual(id(orig_df), id(expected_df))
        self.assertNotEqual(id(output_df), id(expected_df))

        pd.testing.assert_frame_equal(output_df, expected_df)


class VegaUtilitiesTest(unittest.TestCase):
    """Test vega chart utility methods."""

    @parameterized.expand(
        [
            (
                "param_",
                '{"config": {"settings": ["param_1", "param_2"], "ignore": ["param_3"]}}',
                '{"config": {"settings": ["param_1", "param_2"], "ignore": ["param_3"]}}',
            ),  # Deep structure, but "ignore" should not be reset
            (
                "param_",
                '{"data": {"options": ["param_20"], "params": ["param_20", "param_5"]}}',
                '{"data": {"options": ["param_1"], "params": ["param_1", "param_2"]}}',
            ),  # Nested with duplicates across sub-structures
            (
                "view_",
                '{"views": {"list": ["view_10", "view_2"], "additional": "view_1"}}',
                '{"views": {"list": ["view_1", "view_2"], "additional": "view_3"}}',
            ),  # Deep structure, with single key being the same as others
            (
                "view_",
                '{"layers": [{"id": "view_5"}, {"id": "view_5"}, {"id": "view_7"}]}',
                '{"layers": [{"id": "view_1"}, {"id": "view_1"}, {"id": "view_2"}]}',
            ),  # Objects in an array with duplicate IDs
            (
                "plot_",
                '{"data": {"items": ["plot_3"], "descriptions": ["This plot_4 shows..."]}}',
                '{"data": {"items": ["plot_1"], "descriptions": ["This plot_4 shows..."]}}',
            ),  # Only replace actual IDs, not text content
        ]
    )
    def test_reset_counter_pattern(self, prefix: str, vega_spec: str, expected: str):
        """Test that _reset_counter_pattern correctly replaces IDs."""
        result = _reset_counter_pattern(prefix, vega_spec)
        self.assertEqual(result, expected)

    @parameterized.expand(
        [
            (
                '{"vconcat": [{"hconcat": [{"mark": {"type": "point"}, "encoding": {"color": {"field": "species", "type": "nominal"}, "tooltip": {"value": null}, "x": {"field": "sepalLength", "type": "quantitative"}, "y": {"field": "petalLength", "type": "quantitative"}}, "height": 200, "name": "view_33", "width": 200}, {"mark": {"type": "point"}, "encoding": {"color": {"field": "species", "type": "nominal"}, "tooltip": {"value": null}, "x": {"field": "sepalWidth", "type": "quantitative"}, "y": {"field": "petalLength", "type": "quantitative"}}, "height": 200, "name": "view_34", "width": 200}]}, {"hconcat": [{"mark": {"type": "point"}, "encoding": {"color": {"field": "species", "type": "nominal"}, "tooltip": {"value": null}, "x": {"field": "sepalLength", "type": "quantitative"}, "y": {"field": "petalWidth", "type": "quantitative"}}, "height": 200, "name": "view_35", "width": 200}, {"mark": {"type": "point"}, "encoding": {"color": {"field": "species", "type": "nominal"}, "tooltip": {"value": null}, "x": {"field": "sepalWidth", "type": "quantitative"}, "y": {"field": "petalWidth", "type": "quantitative"}}, "height": 200, "name": "view_36", "width": 200}]}], "data": {"url": "https://cdn.jsdelivr.net/npm/vega-datasets@v1.29.0/data/iris.json"}, "params": [{"name": "param_17", "select": {"type": "point"}, "views": ["view_33", "view_34", "view_35", "view_36"]}, {"name": "param_18", "select": {"type": "interval"}, "views": ["view_33", "view_34", "view_35", "view_36"]}], "$schema": "https://vega.github.io/schema/vega-lite/v5.17.0.json", "autosize": {"type": "fit", "contains": "padding"}}',
                '{"vconcat": [{"hconcat": [{"mark": {"type": "point"}, "encoding": {"color": {"field": "species", "type": "nominal"}, "tooltip": {"value": null}, "x": {"field": "sepalLength", "type": "quantitative"}, "y": {"field": "petalLength", "type": "quantitative"}}, "height": 200, "name": "view_1", "width": 200}, {"mark": {"type": "point"}, "encoding": {"color": {"field": "species", "type": "nominal"}, "tooltip": {"value": null}, "x": {"field": "sepalWidth", "type": "quantitative"}, "y": {"field": "petalLength", "type": "quantitative"}}, "height": 200, "name": "view_2", "width": 200}]}, {"hconcat": [{"mark": {"type": "point"}, "encoding": {"color": {"field": "species", "type": "nominal"}, "tooltip": {"value": null}, "x": {"field": "sepalLength", "type": "quantitative"}, "y": {"field": "petalWidth", "type": "quantitative"}}, "height": 200, "name": "view_3", "width": 200}, {"mark": {"type": "point"}, "encoding": {"color": {"field": "species", "type": "nominal"}, "tooltip": {"value": null}, "x": {"field": "sepalWidth", "type": "quantitative"}, "y": {"field": "petalWidth", "type": "quantitative"}}, "height": 200, "name": "view_4", "width": 200}]}], "data": {"url": "https://cdn.jsdelivr.net/npm/vega-datasets@v1.29.0/data/iris.json"}, "params": [{"name": "param_1", "select": {"type": "point"}, "views": ["view_1", "view_2", "view_3", "view_4"]}, {"name": "param_2", "select": {"type": "interval"}, "views": ["view_1", "view_2", "view_3", "view_4"]}], "$schema": "https://vega.github.io/schema/vega-lite/v5.17.0.json", "autosize": {"type": "fit", "contains": "padding"}}',
            ),  # Advanced concatenated Vega-Lite spec with parameters
            # Simpler cases:
            (
                "{ 'mark': 'point', 'encoding': { 'x': { 'field': 'a', 'type': 'quantitative' }, 'y': { 'field': 'b', 'type': 'quantitative' } } }",
                "{ 'mark': 'point', 'encoding': { 'x': { 'field': 'a', 'type': 'quantitative' }, 'y': { 'field': 'b', 'type': 'quantitative' } } }",
            ),  # Simple with nothing replaced
            (
                '{"mark": "bar", "encoding": {"x": {"field": "data", "type": "ordinal"}, "y": {"field": "value", "type": "quantitative"}, "color": {"field": "category", "type": "nominal"}}, "name": "view_112"}',
                '{"mark": "bar", "encoding": {"x": {"field": "data", "type": "ordinal"}, "y": {"field": "value", "type": "quantitative"}, "color": {"field": "category", "type": "nominal"}}, "name": "view_112"}',
            ),  # A simple bar chart will not have `view_` replaced, only composite charts
            (
                '{"description": "This is a view_123 visualization of param_45 data points.", "mark": "point"}',
                '{"description": "This is a view_123 visualization of param_45 data points.", "mark": "point"}',
            ),  # Ensure text containing prefix within descriptions or other properties is not changed
            (
                '{"elements": [{"type": "parameter", "name": "param_5"}]}',
                '{"elements": [{"type": "parameter", "name": "param_5"}]}',
            ),  # Do not replace params when there's no "params" key but similar naming exists
            (
                '{"layer": [{"mark": "line", "encoding": {"x": {"field": "year", "type": "temporal"}, "y": {"field": "growth", "type": "quantitative"}}, "name": "view_203"}]}',
                '{"layer": [{"mark": "line", "encoding": {"x": {"field": "year", "type": "temporal"}, "y": {"field": "growth", "type": "quantitative"}}, "name": "view_1"}]}',
            ),  # A layer spec with a single view needing reset
            (
                '{"repeat": {"layer": ["year_1", "year_2"]}, "spec": {"mark": "area", "encoding": {"y": {"field": {"repeat": "layer"}, "type": "quantitative"}}, "name": "view_15"}}',
                '{"repeat": {"layer": ["year_1", "year_2"]}, "spec": {"mark": "area", "encoding": {"y": {"field": {"repeat": "layer"}, "type": "quantitative"}}, "name": "view_1"}}',
            ),  # Nested structure using repeat and requiring name reset
            (
                '{"concat": [{"view": {"mark": "point", "name": "view_250"}}, {"view": {"mark": "point", "name": "view_251"}}]}',
                '{"concat": [{"view": {"mark": "point", "name": "view_1"}}, {"view": {"mark": "point", "name": "view_2"}}]}',
            ),  # Concatenated chart requiring name reset
            (
                '{"hconcat": [{"view": {"mark": "point", "name": "view_250"}}, {"view": {"mark": "point", "name": "view_251"}}]}',
                '{"hconcat": [{"view": {"mark": "point", "name": "view_1"}}, {"view": {"mark": "point", "name": "view_2"}}]}',
            ),  # hconcat chart requiring name reset
            (
                '{"vconcat": [{"view": {"mark": "point", "name": "view_250"}}, {"view": {"mark": "point", "name": "view_251"}}]}',
                '{"vconcat": [{"view": {"mark": "point", "name": "view_1"}}, {"view": {"mark": "point", "name": "view_2"}}]}',
            ),  # vconcat chart requiring name reset
            (
                '{"facet": {"field": "category", "type": "ordinal"}, "spec": {"mark": "tick", "encoding": {"x": {"field": "value", "type": "quantitative"}}, "name": "view_54"}}',
                '{"facet": {"field": "category", "type": "ordinal"}, "spec": {"mark": "tick", "encoding": {"x": {"field": "value", "type": "quantitative"}}, "name": "view_1"}}',
            ),  # Faceted chart requiring name reset
        ]
    )
    def test_stabilize_vega_json_spec(self, input_spec: str, expected: str):
        """Test that _stabilize_vega_json_spec correctly fixes the auto-generated names."""
        result = _stabilize_vega_json_spec(input_spec)
        self.assertEqual(result, expected)
