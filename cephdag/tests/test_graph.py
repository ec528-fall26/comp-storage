import json
import unittest

from cephdag.functions import REGISTRY, function, lookup
from cephdag.graph import Dataset, Graph, ObjectRef, OutputRef, ref_from_dict


def float_dataset(sizes, pools=("node0", "node1", "node2")):
    return Dataset(
        "test",
        tuple(
            ObjectRef(pools[index % len(pools)], f"d/{index}", size, partition=index)
            for index, size in enumerate(sizes)
        ),
    )


class GraphTests(unittest.TestCase):
    def test_multi_output_fan_in_and_serialization_round_trip(self):
        graph = Graph()
        source = ObjectRef("node0", "d/0", 4096)
        left, right = graph.add("split@1", [source], ratios=[0.5, 0.25], iterations=2)
        (joined,) = graph.add("join@1", [left, right], ratios=[1])
        self.assertEqual(graph.tasks["t000000"].output_sizes, (2048, 1024))
        self.assertEqual(graph.tasks["t000001"].output_sizes, (3072,))
        self.assertEqual(graph.dependencies("t000001"), {"t000000"})
        copy = Graph.from_dict(json.loads(json.dumps(graph.to_dict())))
        self.assertEqual(copy.to_dict(), graph.freeze().to_dict())
        self.assertEqual(ref_from_dict(joined.to_dict()), joined)

    def test_foreign_references_unknown_functions_and_type_mismatches_fail_early(self):
        graph = Graph()
        with self.assertRaisesRegex(ValueError, "foreign"):
            graph.add("map@1", [OutputRef("other", "t000000", 0, "float32", 0)])
        with self.assertRaisesRegex(ValueError, "unregistered"):
            graph.add("map@9", [ObjectRef("node0", "d/0", 4)])
        with self.assertRaisesRegex(ValueError, "signature"):
            graph.add("join@1", [ObjectRef("node0", "d/0", 4)])
        with self.assertRaisesRegex(ValueError, "signature"):
            graph.add("map@1", [ObjectRef("node0", "d/0", 4, dtype="lines")])
        with self.assertRaisesRegex(ValueError, "reserved"):
            graph.add("map@1", [ObjectRef("node0", "d/0", 4)], params={"ratios": [2]})

    def test_aligned_map_and_balanced_tree_reduce(self):
        graph = Graph()
        data = float_dataset([400] * 5)
        mapped = graph.map("map@1", data, ratios=[0.5])
        self.assertEqual([ref.partition for ref in mapped.shards], [0, 1, 2, 3, 4])
        root = graph.reduce("join@1", mapped)
        self.assertEqual(len(graph.tasks), 5 + 4)
        self.assertEqual(graph.size(root), 1000)
        with self.assertRaisesRegex(ValueError, "alignment"):
            graph.map("join@1", data, float_dataset([400] * 4))
        with self.assertRaisesRegex(ValueError, "one-output"):
            graph.map("split@1", data)

    def test_memory_bound_is_checked_when_tasks_are_added(self):
        graph = Graph(memory_limit=16 * 2**20)
        with self.assertRaisesRegex(ValueError, "memory reservation"):
            graph.add("map@1", [ObjectRef("node0", "d/0", 4 * 2**20)], ratios=[1])

    def test_loaded_graphs_cannot_have_cycles_bad_sizes_or_bad_outputs(self):
        graph = Graph()
        (first,) = graph.add("map@1", [ObjectRef("node0", "d/0", 400)])
        graph.add("map@1", [first])
        data = graph.to_dict()
        cyclic = json.loads(json.dumps(data))
        cyclic["tasks"][0]["inputs"] = [OutputRef(graph.id, "t000001", 0, "float32", 0).to_dict()]
        with self.assertRaisesRegex(ValueError, "cycle"):
            Graph.from_dict(cyclic)
        lying = json.loads(json.dumps(data))
        lying["tasks"][1]["output_sizes"] = [4]
        with self.assertRaisesRegex(ValueError, "sizes"):
            Graph.from_dict(lying)
        bad_index = json.loads(json.dumps(data))
        bad_index["tasks"][1]["inputs"][0]["output"] = 3
        with self.assertRaisesRegex(ValueError, "invalid output reference"):
            Graph.from_dict(bad_index)

    def test_frozen_graph_rejects_changes(self):
        graph = Graph()
        graph.add("map@1", [ObjectRef("node0", "d/0", 4)])
        graph.freeze()
        with self.assertRaises(RuntimeError):
            graph.add("map@1", [ObjectRef("node0", "d/0", 4)])

    def test_estimates_round_to_whole_items_of_each_output_type(self):
        @function("test-bytes", "1", inputs=["float32"], outputs=["float32", "bytes"])
        def unused(inputs, params, output_sizes):
            return (b"", b"")

        graph = Graph()
        graph.add("test-bytes@1", [ObjectRef("node0", "d/0", 400)], ratios=[0.33, 0.33])
        self.assertEqual(graph.tasks["t000000"].output_sizes, (132, 132))
        self.assertEqual(graph.tasks["t000000"].params, {"ratios": [0.33, 0.33], "iterations": 1})

    def test_object_refs_reject_path_escapes(self):
        for key in ("", "/abs", "a/../b"):
            with self.assertRaises(ValueError):
                ObjectRef("node0", key, 4)
        with self.assertRaises(ValueError):
            ObjectRef("node 0", "k", 4)


class RegistryTests(unittest.TestCase):
    def test_each_function_is_registered_once_with_its_implementation(self):
        self.assertIs(lookup("map@1").spec.output_types, REGISTRY["map@1"].spec.output_types)
        with self.assertRaisesRegex(ValueError, "already registered"):
            function("map", "1", inputs=["float32"], outputs=["float32"])(lambda *args: ())
        with self.assertRaisesRegex(ValueError, "invalid function name"):
            function("bad name", "1", inputs=[], outputs=[])


if __name__ == "__main__":
    unittest.main()
